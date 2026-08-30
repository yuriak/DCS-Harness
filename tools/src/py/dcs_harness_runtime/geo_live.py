"""Bounded live-DCS coordinate conversion for the Geo capability."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .geo_math import finite_number, geographic_point
from .grpc_support import GrpcSupport
from .lua_support import LuaSupport
from .result import ErrorCode, HarnessError


LIVE_TIMEOUT_SECONDS = 3.0
MISSION_SERVICE = "dcs.mission.v0.MissionService"
WORLD_SERVICE = "dcs.world.v0.WorldService"

PROBE_LUA = """\
return {
  available = type(coord) == "table"
    and type(coord.LLtoLO) == "function"
    and type(coord.LOtoLL) == "function"
}
"""


class GeoLiveBridge:
    """Use only fixed, narrowly parameterized mission-runtime Lua adapters."""

    def __init__(self, context: Any) -> None:
        self.context = context

    def status(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "implemented": True,
            "available": False,
            "source": "live_dcs",
        }
        try:
            self._require_eval_enabled()
            session_id, theatre = self._metadata()
            probe = LuaSupport(self.context).eval(
                PROBE_LUA, timeout=LIVE_TIMEOUT_SECONDS
            ).get("result")
            if not isinstance(probe, Mapping) or probe.get("available") is not True:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "The current DCS mission does not expose coordinate conversion.",
                    details={"reason": "LIVE_COORDINATE_CONVERSION_UNAVAILABLE"},
                )
            value.update(
                available=True,
                session_id=session_id,
                theatre=theatre,
            )
        except HarnessError as error:
            value["error"] = _error_value(_as_unavailable(error))
        return value

    def convert(self, args: Mapping[str, Any]) -> dict[str, Any]:
        direction = args.get("direction")
        if direction == "geographic_to_local":
            allowed = {"direction", "geographic"}
            _reject_unknown(args, allowed)
            source, code = _geographic_to_local(args.get("geographic"))
        elif direction == "local_to_geographic":
            allowed = {"direction", "local"}
            _reject_unknown(args, allowed)
            source, code = _local_to_geographic(args.get("local"))
        else:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "direction must be 'geographic_to_local' or 'local_to_geographic'.",
                details={"reason": "INVALID_CONVERSION_DIRECTION"},
            )

        try:
            self._require_eval_enabled()
            session_id, theatre = self._metadata()
            result = LuaSupport(self.context).eval(
                code, timeout=LIVE_TIMEOUT_SECONDS
            ).get("result")
        except HarnessError as error:
            if error.code == ErrorCode.INVALID_ARGUMENT:
                raise
            raise _as_unavailable(error) from error

        output = _conversion_result(direction, result)
        try:
            confirmed_session = self._session_id()
        except HarnessError as error:
            raise _as_unavailable(error) from error

        if confirmed_session != session_id:
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "The DCS session changed during coordinate conversion.",
                details={
                    "reason": "SESSION_CHANGED_DURING_CONVERSION",
                    "session_before": session_id,
                    "session_after": confirmed_session,
                },
            )
        return {
            "direction": direction,
            "source": "live_dcs",
            "session_id": session_id,
            "theatre": theatre,
            "input": source,
            "output": output,
            "units": {
                "latitude": "deg",
                "longitude": "deg",
                "local": "m",
                "elevation": "m",
            },
        }

    def _require_eval_enabled(self) -> None:
        endpoint = self.context.require_grpc_client_endpoint()
        if not endpoint.eval_enabled:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS-gRPC Eval is disabled; live coordinate conversion is unavailable.",
                details={
                    "reason": "EVAL_DISABLED",
                    "environment_path": str(self.context.environment_path),
                },
            )

    def _metadata(self) -> tuple[str, str]:
        session_id = self._session_id()
        response = GrpcSupport(self.context).call(
            WORLD_SERVICE,
            "GetTheatre",
            {},
            timeout=LIVE_TIMEOUT_SECONDS,
        )
        theatre = response.get("theatre") if isinstance(response, Mapping) else None
        if not isinstance(theatre, str) or not theatre.strip():
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC returned malformed theatre metadata.",
                details={"reason": "MALFORMED_THEATRE"},
            )
        return session_id, theatre

    def _session_id(self) -> str:
        response = GrpcSupport(self.context).call(
            MISSION_SERVICE,
            "GetSessionId",
            {},
            timeout=LIVE_TIMEOUT_SECONDS,
        )
        session_id = response.get("session_id") if isinstance(response, Mapping) else None
        if isinstance(session_id, bool) or not isinstance(session_id, (str, int)):
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC returned malformed session metadata.",
                details={"reason": "MALFORMED_SESSION_ID"},
            )
        return str(session_id)


def _geographic_to_local(value: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        geographic_point(value, "geographic")
        raise AssertionError("geographic_point must reject non-mappings")
    unknown = sorted(set(value) - {"latitude_deg", "longitude_deg", "elevation_m"})
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "geographic contains unsupported coordinate fields.",
            details={
                "reason": "INVALID_COORDINATE",
                "field": "geographic",
                "unknown": unknown,
                "allowed": ["elevation_m", "latitude_deg", "longitude_deg"],
            },
        )
    latitude, longitude = geographic_point(
        {
            "latitude_deg": value.get("latitude_deg"),
            "longitude_deg": value.get("longitude_deg"),
        },
        "geographic",
    )
    elevation = finite_number(value.get("elevation_m", 0.0), "geographic.elevation_m")
    source = {
        "geographic": {
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "elevation_m": elevation,
        }
    }
    code = f"""\
if type(coord) ~= "table" or type(coord.LLtoLO) ~= "function" then
  return {{ok=false, reason="coordinate_api_unavailable"}}
end
local ok, point = pcall(coord.LLtoLO, {json.dumps(latitude)}, {json.dumps(longitude)}, {json.dumps(elevation)})
if not ok or type(point) ~= "table" then
  return {{ok=false, reason="conversion_failed"}}
end
return {{ok=true, x_m=point.x, y_m=point.y, z_m=point.z}}
"""
    return source, code


def _local_to_geographic(value: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "local must be a DCS local coordinate object.",
            details={"reason": "INVALID_COORDINATE", "field": "local"},
        )
    unknown = sorted(set(value) - {"x_m", "y_m", "z_m"})
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "local contains unsupported coordinate fields.",
            details={
                "reason": "INVALID_COORDINATE",
                "field": "local",
                "unknown": unknown,
                "allowed": ["x_m", "y_m", "z_m"],
            },
        )
    x_m = finite_number(value.get("x_m"), "local.x_m")
    y_m = finite_number(value.get("y_m", 0.0), "local.y_m")
    z_m = finite_number(value.get("z_m"), "local.z_m")
    source = {"local": {"x_m": x_m, "y_m": y_m, "z_m": z_m}}
    code = f"""\
if type(coord) ~= "table" or type(coord.LOtoLL) ~= "function" then
  return {{ok=false, reason="coordinate_api_unavailable"}}
end
local ok, latitude, longitude, altitude = pcall(coord.LOtoLL, {{x={json.dumps(x_m)}, y={json.dumps(y_m)}, z={json.dumps(z_m)}}})
if not ok then
  return {{ok=false, reason="conversion_failed"}}
end
return {{ok=true, latitude_deg=latitude, longitude_deg=longitude, elevation_m=altitude}}
"""
    return source, code


def _conversion_result(direction: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and value.get("ok") is False:
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "The current DCS mission could not perform coordinate conversion.",
            details={
                "reason": "LIVE_COORDINATE_CONVERSION_UNAVAILABLE",
                "adapter_reason": str(value.get("reason", "unknown")),
            },
        )
    if not isinstance(value, Mapping) or value.get("ok") is not True:
        raise _malformed_result()
    try:
        if direction == "geographic_to_local":
            return {
                "local": {
                    "x_m": finite_number(value.get("x_m"), "result.x_m"),
                    "y_m": finite_number(value.get("y_m"), "result.y_m"),
                    "z_m": finite_number(value.get("z_m"), "result.z_m"),
                }
            }
        latitude, longitude = geographic_point(
            {
                "latitude_deg": value.get("latitude_deg"),
                "longitude_deg": value.get("longitude_deg"),
            },
            "result",
        )
        return {
            "geographic": {
                "latitude_deg": latitude,
                "longitude_deg": longitude,
                "elevation_m": finite_number(
                    value.get("elevation_m"), "result.elevation_m"
                ),
            }
        }
    except HarnessError as error:
        raise _malformed_result() from error


def _malformed_result() -> HarnessError:
    return HarnessError(
        ErrorCode.GRPC_CALL_FAILED,
        "Live DCS coordinate conversion returned malformed data.",
        details={"reason": "MALFORMED_LIVE_CONVERSION_RESULT"},
    )


def _as_unavailable(error: HarnessError) -> HarnessError:
    if error.code == ErrorCode.CAPABILITY_UNAVAILABLE:
        return error
    return HarnessError(
        ErrorCode.CAPABILITY_UNAVAILABLE,
        "Live DCS coordinate conversion is unavailable.",
        details={
            "reason": "LIVE_DCS_UNAVAILABLE",
            "cause_code": error.code.value,
        },
    )


def _error_value(error: HarnessError) -> dict[str, Any]:
    value: dict[str, Any] = {"code": error.code.value, "message": error.message}
    if error.details:
        value["details"] = dict(error.details)
    return value


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Geo convert contains unsupported arguments.",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )
