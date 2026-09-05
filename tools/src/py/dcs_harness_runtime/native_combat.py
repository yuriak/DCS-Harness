"""Polling transport for the fixed mission-side native combat observer."""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .event_normalization import COMBAT_EVENT_TYPES, normalize_combat_event
from .event_store import EventStore, EventStoreCatalog
from .grpc_support import GrpcSupport
from .logging_utils import LifecycleLogger
from .lua_support import LuaSupport
from .result import ErrorCode, HarnessError


NATIVE_COMBAT_VERSION = 1
POLL_INTERVAL_SECONDS = 1.5
POLL_TIMEOUT_SECONDS = 3.0
POLL_BATCH_LIMIT = 200
MAX_BACKOFF_SECONDS = 5.0
MISSION_SERVICE = "dcs.mission.v0.MissionService"


class NativeCombatObserver:
    def __init__(
        self,
        context: Any,
        stores: EventStoreCatalog,
        logger: LifecycleLogger,
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        session_reader: Callable[[], str] | None = None,
        evaluator: Callable[[str], Any] | None = None,
    ) -> None:
        self.context = context
        self.stores = stores
        self.logger = logger
        self.poll_interval = max(0.01, poll_interval)
        self._session_reader = session_reader
        self._evaluator = evaluator
        self._lock = threading.RLock()
        self._collector = "starting"
        self._session_id: str | None = None
        self._store: EventStore | None = None
        self._installed = False
        self._cursor = 0
        self._oldest_sequence: int | None = None
        self._latest_sequence: int | None = None
        self._overwritten = 0
        self._queue_gaps = 0
        self._extraction_errors = 0
        self._received_events = 0
        self._inserted_events = 0
        self._merged_events = 0
        self._duplicate_events = 0
        self._polls = 0
        self._last_poll_at: str | None = None
        self._last_event_at: str | None = None
        self._last_error: dict[str, Any] | None = None
        self._source: str | None = None

    def run(self, stop_event: threading.Event) -> None:
        delay = self.poll_interval
        self._set(collector="running")
        try:
            while not stop_event.is_set():
                try:
                    self.poll_once()
                    delay = self.poll_interval
                except sqlite3.Error as error:
                    self._record_error(error, state="failed")
                    raise
                except (HarnessError, OSError, ValueError) as error:
                    state = (
                        "unavailable"
                        if isinstance(error, HarnessError)
                        and error.code is ErrorCode.CAPABILITY_UNAVAILABLE
                        else "degraded"
                    )
                    self._record_error(error, state=state)
                    delay = min(max(delay * 2, self.poll_interval), MAX_BACKOFF_SECONDS)
                if stop_event.wait(delay):
                    break
        finally:
            with self._lock:
                if self._collector != "failed":
                    self._collector = "stopped"
            self._log("native_combat_stop")

    def poll_once(self) -> None:
        endpoint = self.context.require_grpc_client_endpoint()
        if not endpoint.eval_enabled:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS-gRPC Eval is disabled; native combat observation is unavailable.",
                details={"reason": "EVAL_DISABLED"},
            )
        session_before = self._read_session()
        with self._lock:
            if session_before != self._session_id:
                self._session_id = session_before
                self._store = self.stores.select(session_before)
                self._installed = False
                self._cursor = 0
                self._oldest_sequence = None
                self._latest_sequence = None
                self._log("native_combat_session", {"session_id": session_before})
            cursor = self._cursor
            installed = self._installed
            store = self._store

        response = self._evaluate(self._poll_code(cursor, install=not installed))
        session_after = self._read_session()
        if session_after != session_before:
            with self._lock:
                self._session_id = session_after
                self._store = self.stores.select(session_after)
                self._installed = False
                self._cursor = 0
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS session changed during native combat polling.",
                details={
                    "reason": "SESSION_CHANGED_DURING_POLL",
                    "session_before": session_before,
                    "session_after": session_after,
                },
            )
        if store is None:
            raise HarnessError(
                ErrorCode.INTERNAL_ERROR,
                "Native combat session store was not selected.",
            )
        batch = self._validate_batch(response, cursor)
        last_sequence = cursor
        outcomes = {"inserted": 0, "merged": 0, "duplicate": 0}
        with store.writer() as writer:
            for raw in batch["events"]:
                mission_time = float(raw["mission_time"])
                event_type = str(raw["event_type"])
                normalized = normalize_combat_event(
                    event_type,
                    mission_time,
                    raw,
                    source="native_combat",
                )
                if normalized is None:
                    raise HarnessError(
                        ErrorCode.INTERNAL_ERROR,
                        "Native observer returned a non-combat event.",
                    )
                outcome = writer.append(
                    session_id=session_before,
                    mission_time=mission_time,
                    event_type=event_type,
                    payload=dict(raw),
                    source="native_combat",
                    normalized=normalized,
                )
                outcomes[outcome.outcome] += 1
                last_sequence = int(raw["native_sequence"])

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._collector = "running"
            self._installed = True
            self._cursor = last_sequence
            self._oldest_sequence = batch["oldest_sequence"]
            self._latest_sequence = batch["latest_sequence"]
            self._overwritten = batch["overwritten"]
            self._extraction_errors = batch["extraction_errors"]
            self._queue_gaps += int(batch["gap"])
            self._received_events += len(batch["events"])
            self._inserted_events += outcomes["inserted"]
            self._merged_events += outcomes["merged"]
            self._duplicate_events += outcomes["duplicate"]
            self._polls += 1
            self._last_poll_at = now
            if batch["events"]:
                self._last_event_at = now
            self._last_error = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "collector": self._collector,
                "session_id": self._session_id,
                "installed": self._installed,
                "cursor": self._cursor,
                "oldest_sequence": self._oldest_sequence,
                "latest_sequence": self._latest_sequence,
                "overwritten": self._overwritten,
                "queue_gaps": self._queue_gaps,
                "extraction_errors": self._extraction_errors,
                "received_events": self._received_events,
                "inserted_events": self._inserted_events,
                "merged_events": self._merged_events,
                "duplicate_events": self._duplicate_events,
                "polls": self._polls,
                "poll_interval_seconds": self.poll_interval,
                "last_poll_at": self._last_poll_at,
                "last_event_at": self._last_event_at,
                "last_error": dict(self._last_error) if self._last_error else None,
            }

    def session_store(self) -> tuple[str | None, EventStore | None]:
        with self._lock:
            return self._session_id, self._store

    def _read_session(self) -> str:
        if self._session_reader is not None:
            return str(self._session_reader())
        response = GrpcSupport(self.context).call(
            MISSION_SERVICE,
            "GetSessionId",
            {},
            timeout=POLL_TIMEOUT_SECONDS,
        )
        session_id = response.get("session_id") if isinstance(response, Mapping) else None
        if isinstance(session_id, bool) or not isinstance(session_id, (str, int)):
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC returned malformed session metadata.",
            )
        return str(session_id)

    def _evaluate(self, code: str) -> Any:
        if self._evaluator is not None:
            return self._evaluator(code)
        return LuaSupport(self.context).eval(
            code, timeout=POLL_TIMEOUT_SECONDS
        ).get("result")

    def _poll_code(self, cursor: int, *, install: bool) -> str:
        call = f"\nreturn DCS_HARNESS_COMBAT_POLL({cursor}, {POLL_BATCH_LIMIT})"
        if not install:
            return call.lstrip()
        if self._source is None:
            path = _observer_source_path(self.context.repository_root)
            try:
                self._source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "Harness native combat Lua source is unavailable.",
                    details={"path": str(path), "exception_type": type(error).__name__},
                ) from error
        return self._source + call

    @staticmethod
    def _validate_batch(value: Any, cursor: int) -> dict[str, Any]:
        if not isinstance(value, Mapping) or value.get("available") is not True:
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer returned malformed status.",
            )
        if value.get("version") != NATIVE_COMBAT_VERSION:
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer version is incompatible.",
            )
        if value.get("capacity") != 512:
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer capacity is incompatible.",
            )
        events = value.get("events", [])
        if events == {}:
            events = []
        if not isinstance(events, list) or len(events) > POLL_BATCH_LIMIT:
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer returned an invalid event batch.",
            )
        oldest = _nonnegative_integer(value.get("oldest_sequence"), "oldest_sequence")
        latest = _nonnegative_integer(value.get("latest_sequence"), "latest_sequence")
        overwritten = _nonnegative_integer(value.get("overwritten"), "overwritten")
        errors = _nonnegative_integer(value.get("extraction_errors"), "extraction_errors")
        if not isinstance(value.get("gap"), bool):
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer gap flag is invalid.",
            )
        if oldest < 1 or oldest > latest + 1:
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer sequence range is invalid.",
            )
        previous = max(cursor, oldest - 1)
        cleaned: list[dict[str, Any]] = []
        for item in events:
            if not isinstance(item, Mapping):
                raise HarnessError(
                    ErrorCode.LUA_EXECUTION_FAILED,
                    "Native combat observer event is not an object.",
                )
            sequence = _nonnegative_integer(item.get("native_sequence"), "native_sequence")
            mission_time = item.get("mission_time")
            event_type = item.get("event_type")
            if (
                sequence != previous + 1
                or sequence > latest
                or isinstance(mission_time, bool)
                or not isinstance(mission_time, (int, float))
                or not math.isfinite(float(mission_time))
                or event_type not in COMBAT_EVENT_TYPES
            ):
                raise HarnessError(
                    ErrorCode.LUA_EXECUTION_FAILED,
                    "Native combat observer event fields are invalid.",
                )
            previous = sequence
            cleaned.append(dict(item))
        if not cleaned and cursor < latest:
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Native combat observer omitted available queued events.",
            )
        return {
            "oldest_sequence": oldest,
            "latest_sequence": latest,
            "overwritten": overwritten,
            "extraction_errors": errors,
            "gap": value.get("gap") is True,
            "events": cleaned,
        }

    def _record_error(self, error: Exception, *, state: str) -> None:
        with self._lock:
            self._collector = state
            self._last_error = {
                "type": type(error).__name__,
                "message": str(error),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        self._log("native_combat_error", {"error_type": type(error).__name__})

    def _set(self, **values: Any) -> None:
        with self._lock:
            for name, value in values.items():
                setattr(self, f"_{name}", value)

    def _log(self, event: str, extra: dict[str, Any] | None = None) -> None:
        self.logger.write(
            {
                "timestamp": time.time(),
                "source": "native_combat",
                "event": event,
                **(extra or {}),
            }
        )


def _observer_source_path(repository_root: Path) -> Path:
    return repository_root / "tools" / "src" / "lua" / "native_combat_observer.lua"


def _nonnegative_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessError(
            ErrorCode.LUA_EXECUTION_FAILED,
            f"Native combat observer {field} is invalid.",
        )
    return value
