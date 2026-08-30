"""Bounded current-session in-memory telemetry history and factual queries."""

from __future__ import annotations

import copy
import math
import threading
import time
from collections import deque
from typing import Any, Mapping

from .result import ErrorCode, HarnessError


DEFAULT_QUERY_LIMIT = 200
MAX_QUERY_LIMIT = 1000
MAX_SNAPSHOT_LIMIT = 500
COALITION_NAMES = frozenset({"NEUTRAL", "RED", "BLUE"})
CATEGORY_NAMES = frozenset({"AIRPLANE", "HELICOPTER", "GROUND", "SHIP", "TRAIN"})
SAMPLE_FIELDS = frozenset(
    {
        "session_id",
        "snapshot_id",
        "mission_time",
        "captured_at",
        "instance_id",
        "unit",
        "group",
        "position",
        "velocity",
        "heading_deg",
        "ground_speed_mps",
        "vertical_speed_mps",
        "life",
        "life_initial",
        "fuel_fraction",
        "in_air",
        "player_name",
    }
)


class TelemetryMemory:
    def __init__(
        self,
        *,
        retention_seconds: float,
        max_snapshots: int,
        max_entities: int,
    ) -> None:
        self.retention_seconds = retention_seconds
        self.max_snapshots = max_snapshots
        self.max_entities = max_entities
        self._lock = threading.RLock()
        self._snapshots: deque[tuple[float, dict[str, Any]]] = deque()
        self._entity_count = 0
        self._session_id: str | None = None
        self._next_snapshot_id = 1
        self._active_instances: dict[int, tuple[str | None, str]] = {}
        self._generations: dict[tuple[int, str | None], int] = {}

    def resume_session(
        self,
        session_id: str,
        *,
        next_snapshot_id: int,
        active_instances: Mapping[int, tuple[str | None, str]],
        generations: Mapping[tuple[int, str | None], int],
    ) -> None:
        """Seed identity counters from the current session's persistent ledger."""
        with self._lock:
            self._rotate(str(session_id))
            self._next_snapshot_id = next_snapshot_id
            self._active_instances = dict(active_instances)
            self._generations = dict(generations)

    def append(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(snapshot))
        session_id = str(value["session_id"])
        units = value.get("units")
        if not isinstance(units, list):
            raise HarnessError(
                ErrorCode.INTERNAL_ERROR,
                "Normalized telemetry snapshot has no units array.",
            )
        if len(units) > self.max_entities:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "One telemetry snapshot exceeds the configured entity safeguard.",
                details={
                    "unit_count": len(units),
                    "max_entities": self.max_entities,
                },
            )

        now = time.monotonic()
        with self._lock:
            if session_id != self._session_id:
                self._rotate(session_id)
            snapshot_id = self._next_snapshot_id
            self._next_snapshot_id += 1
            value["snapshot_id"] = snapshot_id
            seen_ids: set[int] = set()
            for sample in units:
                sample["snapshot_id"] = snapshot_id
                unit = sample.get("unit", {})
                unit_id = int(unit["id"])
                unit_name = unit.get("name")
                seen_ids.add(unit_id)
                active = self._active_instances.get(unit_id)
                if active is None or active[0] != unit_name:
                    key = (unit_id, unit_name)
                    generation = self._generations.get(key, 0) + 1
                    self._generations[key] = generation
                    instance_id = f"{session_id}:{unit_id}:{generation}"
                    self._active_instances[unit_id] = (unit_name, instance_id)
                else:
                    instance_id = active[1]
                sample["instance_id"] = instance_id

            if not value.get("partial", False):
                for unit_id in set(self._active_instances) - seen_ids:
                    self._active_instances.pop(unit_id, None)

            self._snapshots.append((now, value))
            self._entity_count += len(units)
            self._evict(now)
            return copy.deepcopy(value)

    def status(self) -> dict[str, Any]:
        with self._lock:
            latest = self._snapshots[-1][1] if self._snapshots else None
            return {
                "session_id": self._session_id,
                "snapshots_in_memory": len(self._snapshots),
                "samples_in_memory": self._entity_count,
                "memory_retention_seconds": self.retention_seconds,
                "max_snapshots": self.max_snapshots,
                "max_entities": self.max_entities,
                "latest_snapshot_id": latest.get("snapshot_id") if latest else None,
                "latest_mission_time": latest.get("mission_time") if latest else None,
            }

    def latest(self, args: Mapping[str, Any]) -> dict[str, Any]:
        _validate_filters(args)
        with self._lock:
            snapshot = self._require_latest()
            return self._snapshot_result(snapshot, args, MAX_SNAPSHOT_LIMIT)

    def snapshot(self, args: Mapping[str, Any]) -> dict[str, Any]:
        snapshot_id = args.get("snapshot_id")
        mission_time = args.get("mission_time")
        if (snapshot_id is None) == (mission_time is None):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Exactly one of snapshot_id or mission_time is required.",
            )
        _validate_filters(args)
        with self._lock:
            snapshots = [value for _, value in self._snapshots]
            if snapshot_id is not None:
                requested = _positive_integer(snapshot_id, "snapshot_id")
                selected = next(
                    (item for item in snapshots if item["snapshot_id"] == requested),
                    None,
                )
            else:
                requested_time = _finite(mission_time, "mission_time")
                selected = min(
                    snapshots,
                    key=lambda item: abs(item["mission_time"] - requested_time),
                    default=None,
                )
            if selected is None:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "No matching telemetry snapshot is available in current memory.",
                )
            query_args = {
                key: value
                for key, value in args.items()
                if key not in {"snapshot_id", "mission_time"}
            }
            return self._snapshot_result(selected, query_args, MAX_SNAPSHOT_LIMIT)

    def list_units(self, args: Mapping[str, Any]) -> dict[str, Any]:
        _validate_filters(args)
        limit = _limit(args.get("limit", DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT)
        with self._lock:
            snapshot = self._require_latest()
            samples = _filter_samples(snapshot["units"], args)
            identities = [
                {
                    "unit": sample["unit"],
                    "instance_id": sample["instance_id"],
                    "group": sample["group"],
                }
                for sample in samples[:limit]
            ]
            return {
                "session_id": snapshot["session_id"],
                "snapshot_id": snapshot["snapshot_id"],
                "mission_time": snapshot["mission_time"],
                "units": copy.deepcopy(identities),
                "requested_count": len(samples),
                "returned_count": len(identities),
                "truncated": len(samples) > limit,
            }

    def history(self, args: Mapping[str, Any]) -> dict[str, Any]:
        targets = [name for name in ("unit", "instance_id", "group") if args.get(name) is not None]
        if len(targets) != 1:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Exactly one of unit, instance_id, or group is required.",
            )
        if targets[0] == "instance_id":
            if not isinstance(args[targets[0]], str) or not args[targets[0]]:
                raise HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "instance_id must be a non-empty string.",
                )
        else:
            _validate_identity(args[targets[0]], targets[0])
        limit = _limit(args.get("limit", DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT)
        step = _positive_integer(args.get("step", 1), "step")
        fields = _fields(args.get("fields"))
        since = _optional_finite(args.get("since"), "since")
        until = _optional_finite(args.get("until"), "until")
        last_seconds = _optional_finite(args.get("last_seconds"), "last_seconds")
        if last_seconds is not None and last_seconds < 0:
            raise HarnessError(ErrorCode.INVALID_ARGUMENT, "last_seconds must not be negative.")
        if last_seconds is not None and since is not None:
            raise HarnessError(ErrorCode.INVALID_ARGUMENT, "since and last_seconds cannot be combined.")
        if since is not None and until is not None and since > until:
            raise HarnessError(ErrorCode.INVALID_ARGUMENT, "since must not exceed until.")

        with self._lock:
            snapshots = [value for _, value in self._snapshots]
            if not snapshots:
                raise HarnessError(ErrorCode.CAPABILITY_UNAVAILABLE, "No telemetry snapshot is available yet.")
            if last_seconds is not None:
                since = snapshots[-1]["mission_time"] - last_seconds
            selected = [
                item
                for item in snapshots
                if (since is None or item["mission_time"] >= since)
                and (until is None or item["mission_time"] <= until)
            ][::step]
            matches: list[dict[str, Any]] = []
            for snapshot in selected:
                for sample in snapshot["units"]:
                    if _target_match(sample, targets[0], args[targets[0]]):
                        matches.append(_select_fields(sample, fields))
            returned = matches[:limit]
            return {
                "session_id": self._session_id,
                "target": {targets[0]: args[targets[0]]},
                "samples": copy.deepcopy(returned),
                "requested_count": len(matches),
                "returned_count": len(returned),
                "truncated": len(matches) > limit,
                "step": step,
            }

    def _snapshot_result(
        self, snapshot: Mapping[str, Any], args: Mapping[str, Any], maximum: int
    ) -> dict[str, Any]:
        limit = _limit(args.get("limit", DEFAULT_QUERY_LIMIT), maximum)
        fields = _fields(args.get("fields"))
        samples = _filter_samples(snapshot["units"], args)
        returned = [_select_fields(item, fields) for item in samples[:limit]]
        metadata = {
            key: copy.deepcopy(value)
            for key, value in snapshot.items()
            if key != "units"
        }
        return {
            "snapshot": metadata,
            "units": copy.deepcopy(returned),
            "requested_count": len(samples),
            "returned_count": len(returned),
            "truncated": len(samples) > limit,
        }

    def _require_latest(self) -> dict[str, Any]:
        if not self._snapshots:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "No telemetry snapshot is available yet.",
            )
        return self._snapshots[-1][1]

    def _rotate(self, session_id: str) -> None:
        self._snapshots.clear()
        self._entity_count = 0
        self._session_id = session_id
        self._next_snapshot_id = 1
        self._active_instances.clear()
        self._generations.clear()

    def _evict(self, now: float) -> None:
        cutoff = now - self.retention_seconds
        while self._snapshots and (
            self._snapshots[0][0] < cutoff
            or len(self._snapshots) > self.max_snapshots
            or self._entity_count > self.max_entities
        ):
            _, removed = self._snapshots.popleft()
            self._entity_count -= len(removed["units"])


def _filter_samples(samples: list[dict[str, Any]], args: Mapping[str, Any]) -> list[dict[str, Any]]:
    unit = args.get("unit")
    group = args.get("group")
    coalition = args.get("coalition")
    category = args.get("category")
    return [
        sample
        for sample in samples
        if (unit is None or sample["unit"]["name"] == unit or sample["unit"]["id"] == unit)
        and (group is None or sample["group"]["name"] == group or sample["group"]["id"] == group)
        and (coalition is None or sample["unit"]["coalition"] == str(coalition).upper())
        and (category is None or sample["unit"]["category"] == str(category).upper())
    ]


def _validate_filters(args: Mapping[str, Any]) -> None:
    for name in ("unit", "group"):
        if args.get(name) is not None:
            _validate_identity(args[name], name)
    coalition = args.get("coalition")
    if coalition is not None and (
        not isinstance(coalition, str)
        or coalition.upper() not in COALITION_NAMES
    ):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "coalition must be NEUTRAL, RED, or BLUE.",
        )
    category = args.get("category")
    if category is not None and (
        not isinstance(category, str)
        or category.upper() not in CATEGORY_NAMES
    ):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "category must be a supported DCS group category.",
            details={"allowed": sorted(CATEGORY_NAMES)},
        )


def _validate_identity(value: Any, name: str) -> None:
    if isinstance(value, bool) or not (
        (isinstance(value, int) and value >= 0)
        or (isinstance(value, str) and bool(value))
    ):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a non-empty name/id string or non-negative integer.",
        )


def _target_match(sample: Mapping[str, Any], target: str, value: Any) -> bool:
    if target == "unit":
        return sample["unit"]["name"] == value or sample["unit"]["id"] == value
    if target == "group":
        return sample["group"]["name"] == value or sample["group"]["id"] == value
    return sample["instance_id"] == value


def _fields(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise HarnessError(ErrorCode.INVALID_ARGUMENT, "fields must be a non-empty string array.")
    unknown = sorted(set(value) - SAMPLE_FIELDS)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Unsupported telemetry fields were requested.",
            details={"unknown": unknown, "allowed": sorted(SAMPLE_FIELDS)},
        )
    return tuple(dict.fromkeys(value))


def _select_fields(sample: Mapping[str, Any], fields: tuple[str, ...] | None) -> dict[str, Any]:
    if fields is None:
        return copy.deepcopy(dict(sample))
    return {name: copy.deepcopy(sample[name]) for name in fields}


def _limit(value: Any, maximum: int) -> int:
    limit = _positive_integer(value, "limit")
    if limit > maximum:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"Telemetry limit must not exceed {maximum}.",
        )
    return limit


def _positive_integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise HarnessError(ErrorCode.INVALID_ARGUMENT, f"{name} must be a positive integer.")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise HarnessError(ErrorCode.INVALID_ARGUMENT, f"{name} must be finite.")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise HarnessError(ErrorCode.INVALID_ARGUMENT, f"{name} must be finite.") from error
    if not math.isfinite(number):
        raise HarnessError(ErrorCode.INVALID_ARGUMENT, f"{name} must be finite.")
    return number


def _optional_finite(value: Any, name: str) -> float | None:
    return None if value is None else _finite(value, name)
