"""Resident no-overlap telemetry sampling into current-session memory."""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .logging_utils import LifecycleLogger
from .result import ErrorCode, HarnessError
from .telemetry_capture import TelemetrySnapshotSource
from .telemetry_memory import TelemetryMemory
from .telemetry_store import TelemetryStore, TelemetryStoreCatalog, TelemetryWriter


DEFAULT_SAMPLE_INTERVAL_SECONDS = 5.0
DEFAULT_RETENTION_SECONDS = 1800.0
DEFAULT_MAX_ENTITIES = 200_000
MIN_SAMPLE_INTERVAL_SECONDS = 1.0
MAX_SAMPLE_INTERVAL_SECONDS = 60.0
MAX_RETRY_DELAY_SECONDS = 30.0


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool
    sample_interval_seconds: float
    memory_retention_seconds: float
    max_snapshots: int
    max_entities: int
    persistence: bool

    @classmethod
    def from_environment(cls, environment: Mapping[str, Any]) -> "TelemetryConfig":
        raw = environment.get("telemetry", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Telemetry configuration must be an object.",
            )
        enabled = raw.get("enabled", True)
        persistence = raw.get("persistence", False)
        if not isinstance(enabled, bool) or not isinstance(persistence, bool):
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Telemetry enabled and persistence settings must be booleans.",
            )
        interval = _config_number(
            raw.get("sample_interval_seconds", DEFAULT_SAMPLE_INTERVAL_SECONDS),
            "sample_interval_seconds",
            MIN_SAMPLE_INTERVAL_SECONDS,
            MAX_SAMPLE_INTERVAL_SECONDS,
        )
        retention = _config_number(
            raw.get("memory_retention_seconds", DEFAULT_RETENTION_SECONDS),
            "memory_retention_seconds",
            interval,
            86_400.0,
        )
        default_snapshots = math.ceil(retention / interval) + 1
        max_snapshots = _config_integer(
            raw.get("max_snapshots", default_snapshots), "max_snapshots", 2, 86_400
        )
        max_entities = _config_integer(
            raw.get("max_entities", DEFAULT_MAX_ENTITIES),
            "max_entities",
            1,
            10_000_000,
        )
        return cls(
            enabled=enabled,
            sample_interval_seconds=interval,
            memory_retention_seconds=retention,
            max_snapshots=max_snapshots,
            max_entities=max_entities,
            persistence=persistence,
        )


class TelemetryCollector:
    def __init__(
        self,
        context: Any,
        logger: LifecycleLogger,
        config: TelemetryConfig,
    ) -> None:
        self.context = context
        self.logger = logger
        self.config = config
        self.source = TelemetrySnapshotSource(context)
        self.memory = TelemetryMemory(
            retention_seconds=config.memory_retention_seconds,
            max_snapshots=config.max_snapshots,
            max_entities=config.max_entities,
        )
        self.stores = TelemetryStoreCatalog(
            context.runtime_root / "telemetry", context.repository_root
        )
        self._lock = threading.RLock()
        self._collector = "starting" if config.enabled else "stopped"
        self._last_error: dict[str, Any] | None = None
        self._last_successful_sample: str | None = None
        self._last_capture_duration_ms: float | None = None
        self._latest_unit_count = 0
        self._failed_captures = 0
        self._partial_captures = 0
        self._late_missed_samples = 0
        self._consecutive_failures = 0
        self._session_rotations = 0
        self._last_session_id: str | None = None
        self._store_session_id: str | None = None
        self._store: TelemetryStore | None = None
        self._writer_manager: Any = None
        self._writer: TelemetryWriter | None = None
        self._store_path: str | None = None
        self._persisted_count = 0

    def run(self, stop_event: threading.Event) -> None:
        if not self.config.enabled:
            return
        self._set(collector="running")
        next_due = time.monotonic()
        try:
            while not stop_event.is_set():
                now = time.monotonic()
                if now < next_due and stop_event.wait(next_due - now):
                    break
                started = time.monotonic()
                try:
                    captured = self.source.capture(snapshot_id=1)
                    if self.config.persistence:
                        self._prepare_persistence(captured["session_id"])
                    stored = self.memory.append(captured)
                except HarnessError as error:
                    self._capture_failed(error)
                    delay = min(
                        self.config.sample_interval_seconds
                        * (2 ** min(self._consecutive_failures - 1, 3)),
                        MAX_RETRY_DELAY_SECONDS,
                    )
                    next_due = time.monotonic() + delay
                    continue

                if self._writer is not None:
                    self._writer.append(stored)
                    with self._lock:
                        self._persisted_count += 1

                previous_session = self._last_session_id
                session_id = stored["session_id"]
                with self._lock:
                    if previous_session is not None and previous_session != session_id:
                        self._session_rotations += 1
                    self._last_session_id = session_id
                    self._collector = "degraded" if stored["partial"] else "running"
                    self._last_successful_sample = stored["captured_at"]
                    self._last_capture_duration_ms = stored["capture_duration_ms"]
                    self._latest_unit_count = stored["unit_count"]
                    self._consecutive_failures = 0
                    if stored["partial"]:
                        self._partial_captures += 1
                        self._last_error = {
                            "code": "PARTIAL_CAPTURE",
                            "message": "Telemetry snapshot contained partial data.",
                            "details": {"error_count": stored["error_count"]},
                        }
                    else:
                        self._last_error = None
                self._log(
                    "telemetry_capture",
                    {
                        "session_id": session_id,
                        "snapshot_id": stored["snapshot_id"],
                        "unit_count": stored["unit_count"],
                        "partial": stored["partial"],
                        "duration_ms": stored["capture_duration_ms"],
                    },
                )

                next_due += self.config.sample_interval_seconds
                ended = time.monotonic()
                if ended > next_due:
                    missed = math.floor(
                        (ended - next_due) / self.config.sample_interval_seconds
                    ) + 1
                    with self._lock:
                        self._late_missed_samples += missed
                    next_due += missed * self.config.sample_interval_seconds
        except Exception as error:
            self._collector_failed(error)
            raise
        finally:
            self._close_writer()
            with self._lock:
                if self._collector != "failed":
                    self._collector = "stopped"
            self._log("telemetry_collector_stop")

    def status(self) -> dict[str, Any]:
        with self._lock:
            value = {
                "collector": self._collector,
                "enabled": self.config.enabled,
                "session_id": self._last_session_id,
                "sample_interval_seconds": self.config.sample_interval_seconds,
                "last_successful_sample": self._last_successful_sample,
                "last_capture_duration_ms": self._last_capture_duration_ms,
                "latest_unit_count": self._latest_unit_count,
                "failed_captures": self._failed_captures,
                "partial_captures": self._partial_captures,
                "late_missed_samples": self._late_missed_samples,
                "consecutive_failures": self._consecutive_failures,
                "session_rotations": self._session_rotations,
                "persistence_enabled": self.config.persistence,
                "store_path": self._store_path,
                "persisted_count": self._persisted_count,
                "last_error": dict(self._last_error) if self._last_error else None,
            }
        memory_status = self.memory.status()
        # The collector session advances only after a complete memory/persistence
        # cycle. Do not let the memory's preparatory rotation publish it early.
        memory_status.pop("session_id", None)
        value.update(memory_status)
        return value

    def _capture_failed(self, error: HarnessError) -> None:
        with self._lock:
            self._collector = "degraded"
            self._failed_captures += 1
            self._consecutive_failures += 1
            self._last_error = {
                "code": error.code.value,
                "message": error.message,
                "details": dict(error.details) if error.details else None,
            }
        self._log(
            "telemetry_capture_failed",
            {"code": error.code.value, "consecutive": self._consecutive_failures},
        )

    def _prepare_persistence(self, session_id: str) -> None:
        if self._store_session_id == session_id:
            return
        self._close_writer()
        store = self.stores.select(session_id)
        resume = store.resume_state()
        manager = store.writer()
        writer = manager.__enter__()
        try:
            self.memory.resume_session(session_id, **resume)
            persisted_count = store.count()
            display_path = self.stores.display_path(store)
        except Exception:
            manager.__exit__(None, None, None)
            raise
        self._store_session_id = session_id
        self._store = store
        self._writer_manager = manager
        self._writer = writer
        with self._lock:
            self._store_path = display_path
            self._persisted_count = persisted_count

    def _close_writer(self) -> None:
        manager = self._writer_manager
        self._writer_manager = None
        self._writer = None
        self._store = None
        self._store_session_id = None
        if manager is not None:
            manager.__exit__(None, None, None)

    def _collector_failed(self, error: Exception) -> None:
        storage_failure = isinstance(error, (sqlite3.Error, OSError))
        details = {
            "reason": (
                "TELEMETRY_STORAGE_FAILURE"
                if storage_failure
                else "TELEMETRY_COLLECTOR_FAILURE"
            ),
            "exception_type": type(error).__name__,
        }
        with self._lock:
            self._collector = "failed"
            self._last_error = {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": (
                    "Telemetry persistence failed."
                    if storage_failure
                    else "The telemetry collector failed unexpectedly."
                ),
                "details": details,
            }
        self._log("telemetry_collector_failed", details)

    def _set(self, *, collector: str) -> None:
        with self._lock:
            self._collector = collector

    def _log(self, event: str, details: Mapping[str, Any] | None = None) -> None:
        self.logger.write(
            {
                "timestamp": time.time(),
                "source": "telemetry",
                "event": event,
                **dict(details or {}),
            }
        )


def _config_number(
    value: Any, name: str, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool):
        valid = False
    else:
        try:
            value = float(value)
            valid = math.isfinite(value) and minimum <= value <= maximum
        except (TypeError, ValueError):
            valid = False
    if not valid:
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            f"Telemetry {name} must be between {minimum:g} and {maximum:g}.",
        )
    return value


def _config_integer(
    value: Any, name: str, minimum: int, maximum: int
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            f"Telemetry {name} must be between {minimum} and {maximum}.",
        )
    return value
