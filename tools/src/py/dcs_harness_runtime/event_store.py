"""SQLite-backed factual DCS event ledger."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .event_normalization import (
    EVENT_SOURCE_NAMES,
    combat_fingerprint,
    merge_normalized,
)
from .result import ErrorCode, HarnessError


DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 500
MAX_EVENT_SCAN = 5000
MAX_EVENT_TYPES = 20
DEDUP_TIME_TOLERANCE_SECONDS = 0.25
SESSION_ID_PATTERN = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class EventWriteResult:
    event_id: int
    outcome: str


class EventWriter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append(
        self,
        *,
        session_id: str | None,
        mission_time: float | None,
        event_type: str,
        payload: dict[str, Any],
        source: str = "grpc",
        normalized: dict[str, Any] | None = None,
        received_at: str | None = None,
    ) -> EventWriteResult:
        if source not in EVENT_SOURCE_NAMES:
            raise ValueError(f"Unsupported event source: {source}")
        timestamp = received_at or datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        encoded_normalized = (
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            if normalized is not None
            else None
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            matched = self._dedup_candidate(
                mission_time=mission_time,
                event_type=event_type,
                source=source,
                normalized=normalized,
            )
            if matched is not None:
                event_id, existing_source, sources, old_payload, old_normalized = matched
                if source in sources:
                    self.connection.commit()
                    return EventWriteResult(event_id, "duplicate")
                merged_sources = _ordered_sources([*sources, source])
                payloads = (
                    dict(old_payload)
                    if existing_source == "merged" and isinstance(old_payload, dict)
                    else {existing_source: old_payload}
                )
                payloads[source] = payload
                merged_normalized = merge_normalized(old_normalized, normalized or {})
                self.connection.execute(
                    """
                    UPDATE events
                    SET source = 'merged', sources_json = ?, payload_json = ?,
                        normalized_json = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(merged_sources, separators=(",", ":")),
                        json.dumps(payloads, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(
                            merged_normalized,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        event_id,
                    ),
                )
                self.connection.commit()
                return EventWriteResult(event_id, "merged")

            cursor = self.connection.execute(
                """
                INSERT INTO events (
                    dcs_session_id, mission_time, received_at,
                    event_type, source, sources_json, payload_json, normalized_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    mission_time,
                    timestamp,
                    event_type,
                    source,
                    json.dumps([source], separators=(",", ":")),
                    encoded,
                    encoded_normalized,
                ),
            )
            self.connection.commit()
            return EventWriteResult(int(cursor.lastrowid), "inserted")
        except Exception:
            self.connection.rollback()
            raise

    def _dedup_candidate(
        self,
        *,
        mission_time: float | None,
        event_type: str,
        source: str,
        normalized: dict[str, Any] | None,
    ) -> tuple[int, str, list[str], Any, dict[str, Any]] | None:
        if mission_time is None or normalized is None:
            return None
        fingerprint = combat_fingerprint(normalized)
        sequence = normalized.get("source_sequence")
        rows = self.connection.execute(
            """
            SELECT id, source, sources_json, payload_json, normalized_json
            FROM events
            WHERE event_type = ? AND mission_time BETWEEN ? AND ?
            ORDER BY id DESC LIMIT 20
            """,
            (
                event_type,
                mission_time - DEDUP_TIME_TOLERANCE_SECONDS,
                mission_time + DEDUP_TIME_TOLERANCE_SECONDS,
            ),
        ).fetchall()
        for row in rows:
            try:
                sources = list(json.loads(row[2]))
                old_payload = json.loads(row[3])
                old_normalized = json.loads(row[4])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                source == "native_combat"
                and source in sources
                and sequence is not None
                and old_normalized.get("source_sequence") == sequence
            ):
                return int(row[0]), str(row[1]), sources, old_payload, old_normalized
            if source in sources or fingerprint is None:
                continue
            if combat_fingerprint(old_normalized) == fingerprint:
                return int(row[0]), str(row[1]), sources, old_payload, old_normalized
        return None


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dcs_session_id TEXT,
                    mission_time REAL,
                    received_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'grpc',
                    sources_json TEXT NOT NULL DEFAULT '["grpc"]',
                    payload_json TEXT NOT NULL,
                    normalized_json TEXT
                );
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
            migrations = {
                "source": "TEXT NOT NULL DEFAULT 'grpc'",
                "sources_json": "TEXT NOT NULL DEFAULT '[\"grpc\"]'",
                "normalized_json": "TEXT",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE events ADD COLUMN {name} {declaration}"
                    )
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS events_session_idx
                    ON events(dcs_session_id);
                CREATE INDEX IF NOT EXISTS events_type_idx
                    ON events(event_type);
                CREATE INDEX IF NOT EXISTS events_mission_time_idx
                    ON events(mission_time);
                CREATE INDEX IF NOT EXISTS events_source_idx
                    ON events(source);
                """
            )

    @contextmanager
    def writer(self) -> Iterator[EventWriter]:
        connection = self._connect()
        try:
            yield EventWriter(connection)
        finally:
            connection.close()

    def count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def query(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        event_type: str | None = None,
        event_types: Any = None,
        after_id: Any = None,
        initiator_unit: Any = None,
        initiator_group: Any = None,
        target_unit: Any = None,
        target_group: Any = None,
        unit: Any = None,
        group: Any = None,
        coalition: Any = None,
        source: Any = None,
        limit: int = DEFAULT_EVENT_LIMIT,
    ) -> list[dict[str, Any]]:
        limit = self.validate_limit(limit)
        since = self._optional_number(since, "since")
        until = self._optional_number(until, "until")
        if since is not None and until is not None and since > until:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Event query 'since' cannot be greater than 'until'.",
            )
        event_type = self._optional_text(event_type, "event_type")
        event_types = self._event_types(event_types)
        if event_type is not None and event_types is not None:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "event_type and event_types cannot be combined.",
            )
        after_id = self._optional_nonnegative_integer(after_id, "after_id")
        identities = {
            "initiator_unit": self._optional_identity(initiator_unit, "initiator_unit"),
            "initiator_group": self._optional_identity(initiator_group, "initiator_group"),
            "target_unit": self._optional_identity(target_unit, "target_unit"),
            "target_group": self._optional_identity(target_group, "target_group"),
            "unit": self._optional_identity(unit, "unit"),
            "group": self._optional_identity(group, "group"),
        }
        coalition = self._optional_coalition(coalition)
        source = self._optional_source(source)

        clauses: list[str] = []
        parameters: list[Any] = []
        if since is not None:
            clauses.append("mission_time >= ?")
            parameters.append(since)
        if until is not None:
            clauses.append("mission_time <= ?")
            parameters.append(until)
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(event_type)
        if event_types is not None:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            parameters.extend(event_types)
        if after_id is not None:
            clauses.append("id > ?")
            parameters.append(after_id)
        if source is not None:
            clauses.append("sources_json LIKE ?")
            parameters.append(f'%"{source}"%')
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        needs_python_filter = (
            any(item is not None for item in identities.values())
            or coalition is not None
        )
        parameters.append(MAX_EVENT_SCAN if needs_python_filter else limit)
        sql = (
            "SELECT id, dcs_session_id, mission_time, received_at, "
            "event_type, source, sources_json, payload_json, normalized_json FROM events"
            f"{where} ORDER BY id DESC LIMIT ?"
        )
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, parameters).fetchall()
        values = [self._row_value(row) for row in rows]
        filtered = [
            value
            for value in values
            if self._matches(value, identities=identities, coalition=coalition)
        ]
        return filtered[:limit]

    def query_after_id(
        self,
        *,
        event_types: Any,
        after_id: Any,
        limit: Any,
    ) -> list[dict[str, Any]]:
        """Return an oldest-first bounded event page for resident consumers."""
        limit = self.validate_limit(limit)
        after_id = self._optional_nonnegative_integer(after_id, "after_id")
        event_types = self._event_types(event_types)
        if after_id is None or event_types is None:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "query_after_id requires after_id and event_types.",
            )
        placeholders = ",".join("?" for _ in event_types)
        parameters: list[Any] = [after_id, *event_types, limit]
        sql = (
            "SELECT id, dcs_session_id, mission_time, received_at, "
            "event_type, source, sources_json, payload_json, normalized_json FROM events "
            f"WHERE id > ? AND event_type IN ({placeholders}) "
            "ORDER BY id ASC LIMIT ?"
        )
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row_value(row) for row in rows]

    @staticmethod
    def validate_limit(value: Any) -> int:
        valid = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= MAX_EVENT_LIMIT
        )
        if not valid:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event limit must be between 1 and {MAX_EVENT_LIMIT}.",
            )
        return value

    @staticmethod
    def _optional_number(value: Any, name: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            valid = False
        else:
            try:
                value = float(value)
                valid = math.isfinite(value)
            except (TypeError, ValueError):
                valid = False
        if not valid:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be a finite number.",
            )
        return value

    @staticmethod
    def _optional_text(value: Any, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be a non-empty string.",
            )
        value = str(value)
        if not value:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be non-empty.",
            )
        return value

    @classmethod
    def _event_types(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_EVENT_TYPES:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"event_types must contain between 1 and {MAX_EVENT_TYPES} strings.",
            )
        result = [cls._optional_text(item, "event_types") for item in value]
        return list(dict.fromkeys(item for item in result if item is not None))

    @staticmethod
    def _optional_nonnegative_integer(value: Any, name: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be a non-negative integer.",
            )
        return value

    @staticmethod
    def _optional_identity(value: Any, name: str) -> str | int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be a non-empty string or integer.",
            )
        return value

    @staticmethod
    def _optional_coalition(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value.casefold() not in {"neutral", "red", "blue"}:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Event query 'coalition' must be neutral, red, or blue.",
            )
        return value.casefold()

    @staticmethod
    def _optional_source(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in EVENT_SOURCE_NAMES:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Event query 'source' is unsupported.",
                details={"allowed": sorted(EVENT_SOURCE_NAMES)},
            )
        return value

    @staticmethod
    def _matches(
        value: dict[str, Any],
        *,
        identities: dict[str, str | int | None],
        coalition: str | None,
    ) -> bool:
        normalized = value.get("normalized")
        if not isinstance(normalized, Mapping) and value.get("event_type") == "group_command":
            payload = value.get("payload") or {}
            body = payload.get("group_command") or {}
            group = body.get("group") or {}
            coalition_value = group.get("coalition")
            normalized = {"initiator": {
                "group_id": group.get("id"),
                "group_name": group.get("name"),
                "coalition": coalition_value.removeprefix("COALITION_").lower()
                if isinstance(coalition_value, str) else None,
            }}
        if any(item is not None for item in identities.values()) or coalition is not None:
            if not isinstance(normalized, Mapping):
                return False
        initiator = normalized.get("initiator") if isinstance(normalized, Mapping) else None
        target = normalized.get("target") if isinstance(normalized, Mapping) else None
        initiator = initiator if isinstance(initiator, Mapping) else {}
        target = target if isinstance(target, Mapping) else {}

        checks = {
            "initiator_unit": _identity_matches(initiator, "unit", identities["initiator_unit"]),
            "initiator_group": _identity_matches(initiator, "group", identities["initiator_group"]),
            "target_unit": _identity_matches(target, "unit", identities["target_unit"]),
            "target_group": _identity_matches(target, "group", identities["target_group"]),
            "unit": _identity_matches(initiator, "unit", identities["unit"])
            or _identity_matches(target, "unit", identities["unit"]),
            "group": _identity_matches(initiator, "group", identities["group"])
            or _identity_matches(target, "group", identities["group"]),
        }
        if any(identities[name] is not None and not matched for name, matched in checks.items()):
            return False
        if coalition is not None and coalition not in {
            initiator.get("coalition"),
            target.get("coalition"),
        }:
            return False
        return True

    @staticmethod
    def _row_value(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {"malformed_payload": True}
        try:
            sources = json.loads(row["sources_json"])
        except (json.JSONDecodeError, TypeError):
            sources = [row["source"]]
        try:
            normalized = (
                json.loads(row["normalized_json"])
                if row["normalized_json"] is not None
                else None
            )
        except json.JSONDecodeError:
            normalized = None
        return {
            "id": row["id"],
            "session_id": row["dcs_session_id"],
            "mission_time": row["mission_time"],
            "received_at": row["received_at"],
            "event_type": row["event_type"],
            "source": row["source"],
            "sources": sources,
            "payload": payload,
            "normalized": normalized,
        }

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)


class EventStoreCatalog:
    """Creates and reopens one factual ledger per DCS-gRPC session."""

    def __init__(self, root: Path, repository_root: Path) -> None:
        self.root = root.resolve()
        self.repository_root = repository_root.resolve()

    def select(self, session_id: str) -> EventStore:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("DCS session ID is not a filesystem-safe integer.")
        self.root.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.root.glob(f"*_{session_id}.sqlite"))
        if existing:
            path = existing[-1]
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            path = self.root / f"{timestamp}_{session_id}.sqlite"
        store = EventStore(path)
        store.initialize()
        return store

    def display_path(self, store: EventStore) -> str:
        return store.path.resolve().relative_to(self.repository_root).as_posix()


def _ordered_sources(values: list[str]) -> list[str]:
    order = {"grpc": 0, "native_combat": 1}
    return sorted(set(values), key=lambda value: (order.get(value, 99), value))


def _identity_matches(
    entity: Mapping[str, Any], kind: str, expected: str | int | None
) -> bool:
    if expected is None:
        return False
    return expected in {entity.get(f"{kind}_id"), entity.get(f"{kind}_name")}
