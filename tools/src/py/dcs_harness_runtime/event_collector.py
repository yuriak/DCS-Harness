"""Resident DCS-gRPC StreamEvents collector."""

from __future__ import annotations

import importlib
import math
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .event_normalization import normalize_combat_event
from .event_store import EventStore, EventStoreCatalog
from .logging_utils import LifecycleLogger
from .native_combat import NativeCombatObserver
from .result import ErrorCode, HarnessError


INITIAL_RECONNECT_DELAY = 0.25
MAX_RECONNECT_DELAY = 5.0
SESSION_TIMEOUT_SECONDS = 3.0
MISSION_SERVICE = "dcs.mission.v0.MissionService"
IGNORED_EVENT_TYPES = frozenset({"simulation_fps"})


class EventCollector:
    def __init__(
        self,
        context: Any,
        stores: EventStoreCatalog,
        logger: LifecycleLogger,
        *,
        initial_backoff: float = INITIAL_RECONNECT_DELAY,
        max_backoff: float = MAX_RECONNECT_DELAY,
    ) -> None:
        self.context = context
        self.stores = stores
        self.logger = logger
        self.initial_backoff = max(initial_backoff, 0.001)
        self.max_backoff = max(max_backoff, self.initial_backoff)
        self._lock = threading.RLock()
        self._active_stream: Any = None
        self._collector = "starting"
        self._stream = "disconnected"
        self._session_id: str | None = None
        self._store: EventStore | None = None
        self._last_event_at: str | None = None
        self._last_error: dict[str, Any] | None = None
        self._reconnects = 0
        self._malformed_events = 0
        self._ignored_events = 0
        self.native_combat = NativeCombatObserver(context, stores, logger)

    def run(self, stop_event: threading.Event) -> None:
        self._set(collector="running")
        delay = self.initial_backoff
        try:
            while not stop_event.is_set():
                try:
                    consumed = self._connect_and_consume(stop_event)
                    if stop_event.is_set():
                        break
                    self._record_disconnect(
                        "Event stream ended.", error_type="StreamEnded"
                    )
                    if consumed:
                        delay = self.initial_backoff
                except sqlite3.Error:
                    self._set(collector="failed", stream="disconnected")
                    raise
                except (HarnessError, OSError) as error:
                    self._record_disconnect(str(error), error_type=type(error).__name__)
                except Exception as error:
                    if self._is_grpc_error(error):
                        self._record_disconnect(
                            self._grpc_error_text(error),
                            error_type=type(error).__name__,
                        )
                    else:
                        self._set(collector="failed", stream="disconnected")
                        raise

                if stop_event.wait(delay):
                    break
                with self._lock:
                    self._reconnects += 1
                self._log("event_stream_retry", {"delay_seconds": delay})
                delay = min(delay * 2, self.max_backoff)
        finally:
            self.cancel()
            with self._lock:
                if self._collector != "failed":
                    self._collector = "stopped"
                self._stream = "disconnected"
            self._log("event_collector_stop")

    def cancel(self) -> None:
        with self._lock:
            stream = self._active_stream
        cancel = getattr(stream, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        native_status = self.native_combat.status()
        with self._lock:
            grpc_session_id = self._session_id
            grpc_store = self._store
            native_session_id, native_store = self.native_combat.session_store()
            if native_session_id is not None and native_session_id != grpc_session_id:
                session_id = native_session_id
                store = native_store
            else:
                session_id = grpc_session_id or native_session_id
                store = grpc_store or native_store
            observed_times = [
                value
                for value in (self._last_event_at, native_status["last_event_at"])
                if value is not None
            ]
            last_event_at = max(observed_times, default=None)
            value = {
                "collector": self._collector,
                "stream": self._stream,
                "session_id": session_id,
                "grpc_session_id": grpc_session_id,
                "last_event_at": last_event_at,
                "last_error": (
                    dict(self._last_error) if self._last_error else None
                ),
                "reconnects": self._reconnects,
                "malformed_events": self._malformed_events,
                "ignored_events": self._ignored_events,
                "store_path": (
                    self.stores.display_path(store) if store else None
                ),
            }
        value["stored_events"] = store.count() if store else 0
        value["native_combat"] = native_status
        return value

    def current_store(self) -> EventStore:
        with self._lock:
            grpc_session_id = self._session_id
            grpc_store = self._store
        native_session_id, native_store = self.native_combat.session_store()
        if native_session_id is not None and native_session_id != grpc_session_id:
            store = native_store
        else:
            store = grpc_store or native_store
        if store is None:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "No DCS event session ledger is available yet.",
            )
        return store

    def _connect_and_consume(self, stop_event: threading.Event) -> bool:
        self._set(stream="connecting")
        stub, messages = self._mission_api()
        session_response = stub.GetSessionId(
            messages.GetSessionIdRequest(), timeout=SESSION_TIMEOUT_SECONDS
        )
        session_id = str(session_response.session_id)
        store = self.stores.select(session_id)
        with self._lock:
            # Switch Agent-facing reads as soon as the new epoch is known.  Do
            # not expose the prior battle while the new stream is starting.
            if self._session_id != session_id:
                self._last_event_at = None
            self._session_id = session_id
            self._store = store
        stream = stub.StreamEvents(messages.StreamEventsRequest())
        with self._lock:
            self._active_stream = stream
            self._stream = "connected"
            self._last_error = None
        self._log(
            "event_stream_connect",
            {
                "session_id": session_id,
                "store_path": self.stores.display_path(store),
            },
        )

        consumed = False
        try:
            with store.writer() as writer:
                for event in stream:
                    if stop_event.is_set():
                        break
                    try:
                        event_type, mission_time, payload = self._event_value(event)
                        normalized = normalize_combat_event(
                            event_type,
                            mission_time,
                            payload,
                            source="grpc",
                        )
                    except Exception as error:
                        self._record_malformed(error)
                        continue
                    consumed = True
                    if event_type in IGNORED_EVENT_TYPES:
                        with self._lock:
                            self._ignored_events += 1
                        continue
                    writer.append(
                        session_id=session_id,
                        mission_time=mission_time,
                        event_type=event_type,
                        payload=payload,
                        source="grpc",
                        normalized=normalized,
                    )
                    self._set(last_event_at=datetime.now(timezone.utc).isoformat())
        finally:
            with self._lock:
                if self._active_stream is stream:
                    self._active_stream = None
                self._stream = "disconnected"
            self._log("event_stream_disconnect", {"session_id": session_id})
        return consumed

    def _mission_api(self) -> tuple[Any, Any]:
        self.context.ensure_generated_import_path()
        messages = importlib.import_module(
            "dcs_grpc.dcs.mission.v0.mission_pb2"
        )
        stubs = importlib.import_module(
            "dcs_grpc.dcs.mission.v0.mission_pb2_grpc"
        )
        stub = self.context.grpc_stub(MISSION_SERVICE, stubs.MissionServiceStub)
        return stub, messages

    @staticmethod
    def _event_value(event: Any) -> tuple[str, float, dict[str, Any]]:
        from google.protobuf import json_format, message

        if not isinstance(event, message.Message):
            raise TypeError("Stream item is not a protobuf message.")
        event_type = event.WhichOneof("event")
        if not event_type:
            raise ValueError("Stream event has no event payload.")
        payload = json_format.MessageToDict(
            event,
            preserving_proto_field_name=True,
        )
        mission_time = float(event.time)
        if not math.isfinite(mission_time):
            raise ValueError("Stream event mission time is not finite.")
        return event_type, mission_time, payload

    def _record_malformed(self, error: Exception) -> None:
        with self._lock:
            self._malformed_events += 1
            self._last_error = {
                "type": type(error).__name__,
                "message": str(error),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        self._log("event_malformed", {"error_type": type(error).__name__})

    def _record_disconnect(self, message: str, *, error_type: str) -> None:
        with self._lock:
            self._stream = "disconnected"
            self._last_error = {
                "type": error_type,
                "message": message,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        self._log("event_stream_error", {"error_type": error_type})

    def _set(self, **values: Any) -> None:
        with self._lock:
            for name, value in values.items():
                setattr(self, f"_{name}", value)

    @staticmethod
    def _is_grpc_error(error: Exception) -> bool:
        try:
            import grpc
        except ImportError:
            return False
        return isinstance(error, grpc.RpcError)

    @staticmethod
    def _grpc_error_text(error: Exception) -> str:
        details = getattr(error, "details", None)
        if callable(details):
            try:
                return str(details())
            except Exception:
                pass
        return str(error)

    def _log(self, event: str, extra: dict[str, Any] | None = None) -> None:
        self.logger.write(
            {
                "timestamp": time.time(),
                "source": "events",
                "event": event,
                **(extra or {}),
            }
        )
