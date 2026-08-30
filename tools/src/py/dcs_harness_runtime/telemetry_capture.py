"""Prototype one-shot, read-only world snapshot acquisition and normalization."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from .grpc_support import GrpcSupport
from .lua_support import LuaSupport
from .result import ErrorCode, HarnessError


SNAPSHOT_TIMEOUT_SECONDS = 10.0
MISSION_SERVICE = "dcs.mission.v0.MissionService"
MAX_CAPTURE_ERRORS = 10

COALITIONS = {0: "NEUTRAL", 1: "RED", 2: "BLUE"}
GROUP_CATEGORIES = {
    0: "AIRPLANE",
    1: "HELICOPTER",
    2: "GROUND",
    3: "SHIP",
    4: "TRAIN",
}

SNAPSHOT_LUA = r'''
local result = {
  source = "mission_lua_batch",
  mission_time = timer.getTime(),
  coalitions_enumerated = 0,
  groups_seen = 0,
  inactive_count = 0,
  error_count = 0,
  errors = {},
  units = {},
}

local function record_error(scope, name, reason)
  result.error_count = result.error_count + 1
  if #result.errors < 10 then
    table.insert(result.errors, {
      scope = scope,
      name = tostring(name or ""),
      reason = string.sub(tostring(reason), 1, 200),
    })
  end
end

for _, side in ipairs({coalition.side.NEUTRAL, coalition.side.RED, coalition.side.BLUE}) do
  local ok_groups, groups = pcall(coalition.getGroups, side)
  if not ok_groups or type(groups) ~= "table" then
    record_error("coalition", side, groups)
  else
    result.coalitions_enumerated = result.coalitions_enumerated + 1
    for _, group in ipairs(groups) do
      local ok_group, group_data = pcall(function()
        return {
          id = tonumber(group:getID()),
          name = group:getName(),
          category = group:getCategory(),
          coalition = group:getCoalition(),
          units = group:getUnits(),
        }
      end)
      if not ok_group or type(group_data.units) ~= "table" then
        record_error("group", "", group_data)
      else
        result.groups_seen = result.groups_seen + 1
        for _, unit in ipairs(group_data.units) do
          local ok_unit, sample = pcall(function()
            if not unit:isExist() or not unit:isActive() then
              return nil
            end
            local transform = unit:getPosition()
            local velocity = unit:getVelocity()
            local country_id = unit:getCountry()
            local country_name = type(country) == "table"
              and type(country.name) == "table"
              and country.name[country_id]
              or nil
            local fuel = nil
            if group_data.category == Group.Category.AIRPLANE
              or group_data.category == Group.Category.HELICOPTER then
              fuel = unit:getFuel()
            end
            return {
              unit_id = tonumber(unit:getID()),
              unit_name = unit:getName(),
              unit_type = unit:getTypeName(),
              unit_country = country_name,
              player_name = unit:getPlayerName(),
              group_id = group_data.id,
              group_name = group_data.name,
              group_category = group_data.category,
              coalition = unit:getCoalition(),
              position = transform.p,
              forward = transform.x,
              velocity = velocity,
              life = unit:getLife(),
              life_initial = unit:getLife0(),
              fuel_fraction = fuel,
              in_air = unit:inAir(),
            }
          end)
          if not ok_unit then
            record_error("unit", "", sample)
          elseif sample == nil then
            result.inactive_count = result.inactive_count + 1
          else
            table.insert(result.units, sample)
          end
        end
      end
    end
  end
end

result.unit_count = #result.units
result.partial = result.error_count > 0
return result
'''


class TelemetrySnapshotSource:
    """Capture one factual snapshot without retaining state or scheduling."""

    def __init__(self, context: Any) -> None:
        self.context = context

    def capture(self, *, snapshot_id: int = 1) -> dict[str, Any]:
        try:
            snapshot_id = _integer(snapshot_id, "snapshot_id")
        except ValueError as error:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "snapshot_id must be a positive integer.",
            ) from error
        if snapshot_id < 1:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "snapshot_id must be a positive integer.",
            )

        started = time.perf_counter()
        try:
            session_id = self._session_id()
            raw = LuaSupport(self.context).eval(
                SNAPSHOT_LUA, timeout=SNAPSHOT_TIMEOUT_SECONDS
            ).get("result")
            confirmed_session = self._session_id()
        except HarnessError as error:
            if error.code == ErrorCode.INVALID_ARGUMENT:
                raise
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "A live DCS telemetry snapshot is unavailable.",
                details={
                    "reason": "TELEMETRY_SOURCE_UNAVAILABLE",
                    "cause_code": error.code.value,
                },
            ) from error

        if confirmed_session != session_id:
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "The DCS session changed during telemetry capture.",
                details={
                    "reason": "SESSION_CHANGED_DURING_CAPTURE",
                    "session_before": session_id,
                    "session_after": confirmed_session,
                },
            )

        captured_at = datetime.now(timezone.utc).isoformat()
        duration_ms = (time.perf_counter() - started) * 1000.0
        return normalize_snapshot(
            raw,
            session_id=session_id,
            snapshot_id=snapshot_id,
            captured_at=captured_at,
            capture_duration_ms=duration_ms,
        )

    def _session_id(self) -> str:
        response = GrpcSupport(self.context).call(
            MISSION_SERVICE,
            "GetSessionId",
            {},
            timeout=3.0,
        )
        session_id = response.get("session_id") if isinstance(response, Mapping) else None
        if isinstance(session_id, bool) or not isinstance(session_id, (str, int)):
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC returned malformed session metadata.",
                details={"reason": "MALFORMED_SESSION_ID"},
            )
        return str(session_id)


def normalize_snapshot(
    raw: Any,
    *,
    session_id: str,
    snapshot_id: int,
    captured_at: str,
    capture_duration_ms: float,
) -> dict[str, Any]:
    try:
        return _normalize_snapshot(
            raw,
            session_id=session_id,
            snapshot_id=snapshot_id,
            captured_at=captured_at,
            capture_duration_ms=capture_duration_ms,
        )
    except HarnessError:
        raise
    except (TypeError, ValueError) as error:
        raise _malformed(str(error)) from error


def _normalize_snapshot(
    raw: Any,
    *,
    session_id: str,
    snapshot_id: int,
    captured_at: str,
    capture_duration_ms: float,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _malformed("snapshot result must be an object")
    if raw.get("source") != "mission_lua_batch":
        raise _malformed("snapshot source marker is missing")
    if _integer(raw.get("coalitions_enumerated"), "coalitions_enumerated") == 0:
        raise HarnessError(
            ErrorCode.GRPC_CALL_FAILED,
            "Mission Lua could not enumerate any coalition.",
            details={"reason": "TELEMETRY_CAPTURE_FAILED"},
        )

    mission_time = _finite(raw.get("mission_time"), "mission_time")
    groups_seen = _integer(raw.get("groups_seen"), "groups_seen")
    inactive_count = _integer(raw.get("inactive_count"), "inactive_count")
    source_error_count = _integer(raw.get("error_count"), "error_count")
    raw_units = _lua_array(raw.get("units"), "units")
    reported_unit_count = _integer(raw.get("unit_count"), "unit_count")
    if reported_unit_count != len(raw_units):
        raise _malformed("unit_count does not match the units array")
    raw_partial = raw.get("partial")
    if not isinstance(raw_partial, bool):
        raise _malformed("partial must be a boolean")

    errors = _capture_errors(raw.get("errors"))
    normalized: list[dict[str, Any]] = []
    normalization_error_count = 0
    for index, unit in enumerate(raw_units):
        try:
            normalized.append(
                normalize_unit(
                    unit,
                    session_id=session_id,
                    snapshot_id=snapshot_id,
                    mission_time=mission_time,
                    captured_at=captured_at,
                )
            )
        except (HarnessError, TypeError, ValueError) as error:
            normalization_error_count += 1
            if len(errors) < MAX_CAPTURE_ERRORS:
                errors.append(
                    {
                        "scope": "normalization",
                        "name": str(index),
                        "reason": str(error)[:200],
                    }
                )

    error_count = source_error_count + normalization_error_count
    return {
        "session_id": str(session_id),
        "snapshot_id": snapshot_id,
        "mission_time": mission_time,
        "captured_at": captured_at,
        "capture_duration_ms": _finite(
            capture_duration_ms, "capture_duration_ms"
        ),
        "unit_count": len(normalized),
        "observed_unit_count": len(raw_units),
        "groups_seen": groups_seen,
        "inactive_count": inactive_count,
        "source": "mission_lua_batch",
        "heading_reference": "dcs_local_x_north_z_east",
        "partial": raw_partial or error_count > 0,
        "error_count": error_count,
        "errors": errors,
        "units": normalized,
    }


def normalize_unit(
    raw: Any,
    *,
    session_id: str,
    snapshot_id: int,
    mission_time: float,
    captured_at: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("unit sample must be an object")
    category_id = _integer(raw.get("group_category"), "group_category")
    coalition_id = _integer(raw.get("coalition"), "coalition")
    category = GROUP_CATEGORIES.get(category_id)
    coalition_name = COALITIONS.get(coalition_id)
    if category is None or coalition_name is None:
        raise ValueError("unit sample contains an unknown category or coalition")

    position = _vector(raw.get("position"), "position")
    velocity = _vector(raw.get("velocity"), "velocity")
    forward = _vector(raw.get("forward"), "forward")
    horizontal_forward = math.hypot(forward[0], forward[2])
    heading = (
        math.degrees(math.atan2(forward[2], forward[0])) % 360.0
        if horizontal_forward > 1e-12
        else None
    )
    country = raw.get("unit_country")
    if country is not None and not isinstance(country, str):
        raise ValueError("unit_country must be a string or null")

    return {
        "session_id": str(session_id),
        "snapshot_id": snapshot_id,
        "mission_time": mission_time,
        "captured_at": captured_at,
        "instance_id": None,
        "unit": {
            "id": _integer(raw.get("unit_id"), "unit_id"),
            "name": _optional_name(raw.get("unit_name"), "unit_name"),
            "type": _string(raw.get("unit_type"), "unit_type"),
            "category": category,
            "coalition": coalition_name,
            "country": country,
        },
        "group": {
            "id": _integer(raw.get("group_id"), "group_id"),
            "name": _string(raw.get("group_name"), "group_name"),
        },
        "position": {
            "x_m": position[0],
            "y_m": position[1],
            "z_m": position[2],
            "latitude_deg": None,
            "longitude_deg": None,
        },
        "velocity": {
            "x_mps": velocity[0],
            "y_mps": velocity[1],
            "z_mps": velocity[2],
        },
        "heading_deg": heading,
        "ground_speed_mps": math.hypot(velocity[0], velocity[2]),
        "vertical_speed_mps": velocity[1],
        "life": _optional_finite(raw.get("life"), "life"),
        "life_initial": _optional_finite(
            raw.get("life_initial"), "life_initial"
        ),
        "fuel_fraction": _optional_finite(
            raw.get("fuel_fraction"), "fuel_fraction"
        ),
        "in_air": _optional_bool(raw.get("in_air"), "in_air"),
        "player_name": _optional_string(raw.get("player_name"), "player_name"),
    }


def _capture_errors(value: Any) -> list[dict[str, str]]:
    value = _lua_array(value, "errors")
    errors: list[dict[str, str]] = []
    for item in value[:MAX_CAPTURE_ERRORS]:
        if not isinstance(item, Mapping):
            raise _malformed("capture error must be an object")
        errors.append(
            {
                "scope": str(item.get("scope", ""))[:40],
                "name": str(item.get("name", ""))[:120],
                "reason": str(item.get("reason", ""))[:200],
            }
        )
    return errors


def _lua_array(value: Any, name: str) -> list[Any]:
    if isinstance(value, list):
        return value
    # The pinned DCS-gRPC JSON encoder represents an empty Lua table as `{}`
    # because Lua does not retain an empty table's intended array/object shape.
    if isinstance(value, Mapping) and not value:
        return []
    raise _malformed(f"{name} must be an array")


def _vector(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return (
        _finite(value.get("x"), f"{name}.x"),
        _finite(value.get("y"), f"{name}.y"),
        _finite(value.get("z"), f"{name}.z"),
    )


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _optional_finite(value: Any, name: str) -> float | None:
    return None if value is None else _finite(value, name)


def _integer(value: Any, name: str) -> int:
    number = _finite(value, name)
    if not number.is_integer() or number < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(number)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _optional_name(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    return _string(value, name)


def _optional_bool(value: Any, name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean or null")
    return value


def _malformed(reason: str) -> HarnessError:
    return HarnessError(
        ErrorCode.GRPC_CALL_FAILED,
        "Mission Lua returned a malformed telemetry snapshot.",
        details={
            "reason": "MALFORMED_TELEMETRY_SNAPSHOT",
            "validation": reason,
        },
    )
