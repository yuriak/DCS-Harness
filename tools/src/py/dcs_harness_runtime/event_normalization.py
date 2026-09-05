"""Source-aware factual normalization for combat-relevant DCS events."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .result import ErrorCode, HarnessError


COMBAT_EVENT_TYPES = frozenset(
    {
        "shot",
        "hit",
        "kill",
        "dead",
        "unit_lost",
        "crash",
        "ejection",
        "shooting_start",
        "shooting_end",
    }
)
EVENT_SOURCE_NAMES = frozenset({"grpc", "native_combat"})


def normalize_combat_event(
    event_type: str,
    mission_time: float,
    payload: Mapping[str, Any],
    *,
    source: str,
) -> dict[str, Any] | None:
    if event_type not in COMBAT_EVENT_TYPES:
        return None
    if source not in EVENT_SOURCE_NAMES:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "Combat event has an unsupported source.",
            details={"source": source},
        )
    if not isinstance(mission_time, (int, float)) or isinstance(mission_time, bool):
        raise HarnessError(ErrorCode.INTERNAL_ERROR, "Combat event time is invalid.")
    mission_time = float(mission_time)
    if not math.isfinite(mission_time):
        raise HarnessError(ErrorCode.INTERNAL_ERROR, "Combat event time is invalid.")

    if source == "grpc":
        body = payload.get(event_type)
        body = body if isinstance(body, Mapping) else {}
        initiator = _grpc_entity(body.get("initiator"))
        target = _grpc_entity(body.get("target"))
        weapon = _grpc_weapon(body.get("weapon"), body.get("weapon_name"))
        source_sequence = None
    else:
        body = payload
        initiator = _native_entity(body.get("initiator"))
        target = _native_entity(body.get("target"))
        weapon = _native_weapon(body.get("weapon"))
        source_sequence = _optional_integer(body.get("native_sequence"))

    return {
        "schema_version": 1,
        "event_type": event_type,
        "mission_time": mission_time,
        "initiator": initiator,
        "target": target,
        "weapon": weapon,
        "source_sequence": source_sequence,
    }


def combat_fingerprint(value: Mapping[str, Any]) -> tuple[Any, ...] | None:
    initiator = _entity_fingerprint(value.get("initiator"))
    target = _entity_fingerprint(value.get("target"))
    if value.get("event_type") == "ejection":
        # DCS reports the parachute as static via protobuf and unknown via
        # native Lua (often with placeholder ID 0). Retain its name distinction.
        parachute = value.get("target")
        if isinstance(parachute, Mapping) and parachute.get("kind") in {"static", "unknown"}:
            name = _clean_text(parachute.get("object_name"))
            target = ("parachute", name) if name else None
    weapon_value = value.get("weapon")
    weapon = None
    if isinstance(weapon_value, Mapping):
        weapon = (
            _clean_text(weapon_value.get("type")),
            _clean_text(weapon_value.get("event_weapon_name")),
        )
        if weapon == (None, None):
            weapon = None
    # Weapon-only records are too weak to merge safely when rapid fire can
    # produce multiple same-type events inside the time tolerance.
    if initiator is None and target is None:
        return None
    return (value.get("event_type"), initiator, target, weapon)


def merge_normalized(
    existing: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    def merge(left: Any, right: Any) -> Any:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            keys = set(left) | set(right)
            return {key: merge(left.get(key), right.get(key)) for key in keys}
        return left if left is not None else right

    return merge(dict(existing), dict(incoming))


def entity_identity(value: Any) -> tuple[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("unit_id", "unit_name", "object_id", "object_name"):
        candidate = value.get(key)
        if candidate is not None and candidate != "":
            return key, candidate
    return None


def _grpc_entity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    kind = next(
        (name for name in ("unit", "static", "scenery", "weapon", "airbase", "unknown") if isinstance(value.get(name), Mapping)),
        None,
    )
    if kind is None:
        return None
    item = value[kind]
    group = item.get("group") if isinstance(item.get("group"), Mapping) else {}
    unit = kind == "unit"
    return {
        "kind": kind,
        "unit_id": _optional_integer(item.get("id")) if unit else None,
        "unit_name": _clean_text(item.get("name")) if unit else None,
        "group_id": _optional_integer(group.get("id")) if unit else None,
        "group_name": _clean_text(group.get("name")) if unit else None,
        "type": _clean_text(item.get("type")),
        "coalition": _coalition(item.get("coalition")),
        "player_name": _clean_text(item.get("player_name")) if unit else None,
        "object_id": _optional_integer(item.get("id")),
        "object_name": _clean_text(item.get("name")),
    }


def _native_entity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "kind": _clean_text(value.get("kind")),
        "unit_id": _optional_integer(value.get("unit_id")),
        "unit_name": _clean_text(value.get("unit_name")),
        "group_id": _optional_integer(value.get("group_id")),
        "group_name": _clean_text(value.get("group_name")),
        "type": _clean_text(value.get("type")),
        "coalition": _coalition(value.get("coalition")),
        "player_name": _clean_text(value.get("player_name")),
        "object_id": _optional_integer(value.get("object_id")),
        "object_name": _clean_text(value.get("object_name")),
    }


def _grpc_weapon(value: Any, event_weapon_name: Any) -> dict[str, Any] | None:
    value = value if isinstance(value, Mapping) else {}
    result = {
        "runtime_id": _optional_integer(value.get("id")),
        "type": _clean_text(value.get("type")),
        "event_weapon_name": _clean_text(event_weapon_name),
    }
    return result if any(item is not None for item in result.values()) else None


def _native_weapon(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result = {
        "runtime_id": _optional_integer(value.get("runtime_id")),
        "type": _clean_text(value.get("type")),
        "event_weapon_name": _clean_text(value.get("event_weapon_name")),
    }
    return result if any(item is not None for item in result.values()) else None


def _entity_fingerprint(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, Mapping):
        return None
    identity = entity_identity(value)
    if identity is None:
        return None
    return identity + (_clean_text(value.get("type")),)


def _coalition(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.casefold().removeprefix("coalition_")
        return cleaned if cleaned in {"neutral", "red", "blue"} else None
    if isinstance(value, int) and not isinstance(value, bool):
        return {0: "neutral", 1: "neutral", 2: "red", 3: "blue"}.get(value)
    return None


def _optional_integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _clean_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
