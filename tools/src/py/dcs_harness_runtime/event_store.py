"""SQLite-backed factual DCS event ledger."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .result import ErrorCode, HarnessError


DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 500


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
        received_at: str | None = None,
    ) -> int:
        timestamp = received_at or datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO events (
                    dcs_session_id, mission_time, received_at,
                    event_type, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, mission_time, timestamp, event_type, encoded),
            )
        return int(cursor.lastrowid)


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
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_session_idx
                    ON events(dcs_session_id);
                CREATE INDEX IF NOT EXISTS events_type_idx
                    ON events(event_type);
                CREATE INDEX IF NOT EXISTS events_mission_time_idx
                    ON events(mission_time);
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
        session_id: str | None = None,
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
        session_id = self._optional_text(
            session_id, "session_id", allow_integer=True
        )

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
        if session_id is not None:
            clauses.append("dcs_session_id = ?")
            parameters.append(session_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        sql = (
            "SELECT id, dcs_session_id, mission_time, received_at, "
            "event_type, payload_json FROM events"
            f"{where} ORDER BY id DESC LIMIT ?"
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
    def _optional_text(
        value: Any, name: str, *, allow_integer: bool = False
    ) -> str | None:
        if value is None:
            return None
        allowed_type = isinstance(value, str) or (
            allow_integer and isinstance(value, int) and not isinstance(value, bool)
        )
        if not allowed_type:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be a non-empty string"
                + (" or integer." if allow_integer else "."),
            )
        value = str(value)
        if not value:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Event query {name!r} must be non-empty.",
            )
        return value

    @staticmethod
    def _row_value(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {"malformed_payload": True}
        return {
            "id": row["id"],
            "session_id": row["dcs_session_id"],
            "mission_time": row["mission_time"],
            "received_at": row["received_at"],
            "event_type": row["event_type"],
            "payload": payload,
        }

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)
