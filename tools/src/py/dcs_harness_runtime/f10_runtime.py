"""Resident session-scoped state and typed RPC operations for F10 communication."""

from __future__ import annotations

import copy
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .grpc_support import GrpcSupport
from .logging_utils import LifecycleLogger
from .reporting import unavailable_error
from .result import ErrorCode, HarnessError


MISSION_SERVICE = "dcs.mission.v0.MissionService"
TIMER_SERVICE = "dcs.timer.v0.TimerService"
TRIGGER_SERVICE = "dcs.trigger.v0.TriggerService"
SESSION_POLL_INTERVAL_SECONDS = 1.0
SESSION_TIMEOUT_SECONDS = 1.0
ACTION_TIMEOUT_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 5.0
MAX_REGISTRATIONS = 256
MAX_RECENT_INPUTS = 256
INPUT_POLL_LIMIT = 500
COMMAND_EVENT_TYPES = (
    "mission_command",
    "coalition_command",
    "group_command",
)


@dataclass(frozen=True)
class F10Scope:
    kind: str
    coalition: str | None = None
    group_name: str | None = None

    @property
    def key(self) -> str:
        if self.kind == "mission":
            return "mission"
        if self.kind == "coalition":
            return f"coalition:{self.coalition}"
        return f"group:{self.group_name}"

    def request_target(self) -> dict[str, Any]:
        if self.kind == "coalition":
            return {"coalition": self.coalition}
        if self.kind == "group":
            return {"group_name": self.group_name}
        return {}


@dataclass(frozen=True)
class F10Registration:
    item_id: str
    kind: str
    scope: F10Scope
    name: str
    path: tuple[str, ...]
    parent_id: str | None
    interaction_id: str | None
    choice_id: str | None
    action: str | None
    data: Mapping[str, Any] | None
    created_at: str

    def summary(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "item_id": self.item_id,
            "kind": self.kind,
            "scope": self.scope.kind,
            "name": self.name,
            "path": list(self.path),
            "parent_id": self.parent_id,
            "created_at": self.created_at,
        }
        if self.scope.coalition is not None:
            value["coalition"] = self.scope.coalition
        if self.scope.group_name is not None:
            value["group_name"] = self.scope.group_name
        if self.interaction_id is not None:
            value["interaction_id"] = self.interaction_id
        if self.choice_id is not None:
            value["choice_id"] = self.choice_id
        if self.action is not None:
            value["action"] = self.action
        return value


RpcCaller = Callable[[str, str, Mapping[str, Any], float], Mapping[str, Any]]
EventReader = Callable[[int, int], list[dict[str, Any]]]


class F10Runtime:
    """Own F10 state, typed actions, and destructive session rotation."""

    def __init__(
        self,
        context: Any,
        logger: LifecycleLogger,
        *,
        poll_interval: float = SESSION_POLL_INTERVAL_SECONDS,
        session_reader: Callable[[], str] | None = None,
        rpc_caller: RpcCaller | None = None,
        event_reader: EventReader | None = None,
    ) -> None:
        self.context = context
        self.logger = logger
        self.poll_interval = max(0.01, float(poll_interval))
        self._session_reader = session_reader
        self._rpc_caller = rpc_caller
        self._event_reader = event_reader
        self._support: GrpcSupport | None = None
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._monitor = "starting"
        self._session_id: str | None = None
        self._registrations: dict[str, F10Registration] = {}
        self._pending_inputs: list[dict[str, Any]] = []
        self._input_cursor = 0
        self._input_monitor = "starting"
        self._input_polls = 0
        self._ignored_inputs = 0
        self._input_overflows = 0
        self._last_input_poll_at: str | None = None
        self._last_input_error: dict[str, Any] | None = None
        self._latest_input_mission_time: float | None = None
        self._latest_outbound_message_mission_time: float | None = None
        self._latest_outbound_message_at: str | None = None
        self._session_rotations = 0
        self._last_session_check_at: str | None = None
        self._last_error: dict[str, Any] | None = None

    def run(self, stop_event: threading.Event) -> None:
        delay = self.poll_interval
        try:
            while not stop_event.is_set():
                try:
                    self.poll_once()
                    delay = self.poll_interval
                except (HarnessError, OSError, ValueError) as error:
                    self._record_error(error)
                    delay = min(max(delay * 2, self.poll_interval), MAX_BACKOFF_SECONDS)
                try:
                    self.poll_inputs_once()
                except (HarnessError, OSError, ValueError) as error:
                    self._record_input_error(error)
                if stop_event.wait(delay):
                    break
        finally:
            # Only remove paths this process owns. A session change makes clear()
            # discard stale registrations without touching the new mission.
            try:
                self.clear()
            except Exception as error:
                self._log("f10_shutdown_cleanup_error", {"error": str(error)})
            with self._lock:
                self._monitor = "stopped"
                self._input_monitor = "stopped"
            self._log("f10_session_monitor_stop")

    def poll_once(self) -> None:
        self._observe_session(self._read_session())

    def poll_inputs_once(self) -> int:
        with self._lock:
            session_id = self._session_id
            cursor = self._input_cursor
        if session_id is None:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "F10 input polling requires a current DCS session.",
            )
        events = self._read_events(cursor, INPUT_POLL_LIMIT)
        newest_id = cursor
        accepted = 0
        for event in sorted(events, key=lambda value: _event_id(value)):
            # The event collector can lag behind the session monitor on reload.
            # Foreign-session IDs must never advance this session's cursor.
            if str(event.get("session_id")) != session_id:
                continue
            event_id = _event_id(event)
            newest_id = max(newest_id, event_id)
            selection = self._selection(event, session_id)
            if selection is None:
                with self._lock:
                    self._ignored_inputs += 1
                continue
            self._append_input(selection, session_id)
            accepted += 1
        polled_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._require_session(session_id)
            self._input_cursor = newest_id
            self._input_monitor = "running"
            self._input_polls += 1
            self._last_input_poll_at = polled_at
            self._last_input_error = None
        return accepted

    def send(
        self,
        *,
        scope: str,
        text: str,
        display_time: int,
        clear_view: bool,
        coalition: str | None = None,
        group_id: int | None = None,
    ) -> dict[str, Any]:
        if scope == "mission":
            method = "OutText"
            request: dict[str, Any] = {}
        elif scope == "coalition":
            method = "OutTextForCoalition"
            request = {"coalition": coalition}
        else:
            method = "OutTextForGroup"
            request = {"group_id": group_id}
        request.update(text=text, display_time=display_time, clear_view=clear_view)
        with self._operation_lock:
            session_id = self._prepare_operation()
            time_response = self._call(TIMER_SERVICE, "GetTime", {})
            mission_time = _mission_time(time_response)
            self._call(TRIGGER_SERVICE, method, request)
            self._confirm_operation(session_id)
            sent_at = datetime.now(timezone.utc).isoformat()
            with self._lock:
                self._require_session(session_id)
                self._latest_outbound_message_mission_time = mission_time
                self._latest_outbound_message_at = sent_at
        self._log(
            "f10_text_sent",
            {"session_id": session_id, "scope": scope, "mission_time": mission_time},
        )
        return {
            "session_id": session_id,
            "scope": scope,
            "coalition": coalition,
            "group_id": group_id,
            "mission_time": mission_time,
            "sent_at": sent_at,
        }

    def init(self, scope: F10Scope, root_name: str) -> dict[str, Any]:
        item_id = f"root:{scope.key}"
        with self._operation_lock:
            session_id = self._prepare_operation()
            existing = self._registration(item_id)
            if existing is not None:
                if existing.name != root_name:
                    raise HarnessError(
                        ErrorCode.INVALID_ARGUMENT,
                        "This F10 scope is already initialized with a different root name.",
                        details={"item_id": item_id, "existing_name": existing.name},
                    )
                return {
                    "created": False,
                    "session_id": session_id,
                    "registration": existing.summary(),
                }
            self._ensure_capacity()
            response = self._call(
                MISSION_SERVICE,
                _menu_method(scope, "Add", "SubMenu"),
                {"name": root_name, "path": [], **scope.request_target()},
            )
            self._confirm_operation(session_id)
            registration = self._new_registration(
                item_id=item_id,
                kind="menu",
                scope=scope,
                name=root_name,
                path=_response_path(response),
                parent_id=None,
            )
            self._store_registration(registration, session_id)
        return {
            "created": True,
            "session_id": session_id,
            "registration": registration.summary(),
        }

    def add_menu(self, *, item_id: str, parent_id: str, name: str) -> dict[str, Any]:
        with self._operation_lock:
            parent = self._require_parent(parent_id)
            self._require_new_item(item_id)
            session_id = self._prepare_operation()
            parent = self._require_parent(parent_id, expected_session=session_id)
            self._ensure_capacity()
            response = self._call(
                MISSION_SERVICE,
                _menu_method(parent.scope, "Add", "SubMenu"),
                {
                    "name": name,
                    "path": list(parent.path),
                    **parent.scope.request_target(),
                },
            )
            self._confirm_operation(session_id)
            registration = self._new_registration(
                item_id=item_id,
                kind="menu",
                scope=parent.scope,
                name=name,
                path=_response_path(response),
                parent_id=parent_id,
            )
            self._store_registration(registration, session_id)
        return {
            "created": True,
            "session_id": session_id,
            "registration": registration.summary(),
        }

    def add_command(
        self,
        *,
        item_id: str,
        parent_id: str,
        name: str,
        interaction_id: str,
        choice_id: str,
        action: str | None,
        data: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        with self._operation_lock:
            parent = self._require_parent(parent_id)
            self._require_new_item(item_id)
            session_id = self._prepare_operation()
            parent = self._require_parent(parent_id, expected_session=session_id)
            self._ensure_capacity()
            details: dict[str, Any] = {
                "source": "dcs_harness",
                "interaction_id": interaction_id,
                "choice_id": choice_id,
                "command_id": item_id,
            }
            if action is not None:
                details["action"] = action
            if data:
                details["data"] = dict(data)
            response = self._call(
                MISSION_SERVICE,
                _menu_method(parent.scope, "Add", "Command"),
                {
                    "name": name,
                    "path": list(parent.path),
                    "details": details,
                    **parent.scope.request_target(),
                },
            )
            self._confirm_operation(session_id)
            registration = self._new_registration(
                item_id=item_id,
                kind="command",
                scope=parent.scope,
                name=name,
                path=_response_path(response),
                parent_id=parent_id,
                interaction_id=interaction_id,
                choice_id=choice_id,
                action=action,
                data=data,
            )
            self._store_registration(registration, session_id)
        return {
            "created": True,
            "session_id": session_id,
            "registration": registration.summary(),
        }

    def remove(self, item_id: str) -> dict[str, Any]:
        with self._operation_lock:
            registration = self._require_registration(item_id, None)
            session_id = self._prepare_operation()
            registration = self._require_registration(item_id, session_id)
            self._call(
                MISSION_SERVICE,
                _menu_method(registration.scope, "Remove", "Item"),
                {"path": list(registration.path), **registration.scope.request_target()},
            )
            self._confirm_operation(session_id)
            removed = self._discard_tree(registration, session_id)
        return {"removed": removed, "item_id": item_id, "session_id": session_id}

    def clear(self) -> dict[str, Any]:
        removed: list[str] = []
        with self._operation_lock:
            session_id = self._prepare_operation()
            roots = self._roots(session_id)
            for root in roots:
                self._call(
                    MISSION_SERVICE,
                    _menu_method(root.scope, "Remove", "Item"),
                    {"path": list(root.path), **root.scope.request_target()},
                )
                self._confirm_operation(session_id)
                removed.extend(self._discard_tree(root, session_id))
        return {
            "session_id": session_id,
            "removed": removed,
            "removed_count": len(removed),
        }

    def registrations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                registration.summary()
                for registration in sorted(
                    self._registrations.values(), key=lambda value: value.item_id
                )
            ]

    def recent_inputs(
        self,
        *,
        limit: int,
        pending_only: bool,
        interaction_id: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            values = [
                copy.deepcopy(value)
                for value in reversed(self._pending_inputs)
                if (not pending_only or not value["acknowledged"])
                and (
                    interaction_id is None
                    or value["interaction_id"] == interaction_id
                )
            ][:limit]
            session_id = self._session_id
        return {
            "session_id": session_id,
            "inputs": values,
            "count": len(values),
            "pending_only": pending_only,
            "interaction_id": interaction_id,
        }

    def acknowledge(self, input_ids: list[str]) -> dict[str, Any]:
        requested = set(input_ids)
        acknowledged: list[str] = []
        already_acknowledged: list[str] = []
        matched: set[str] = set()
        with self._lock:
            session_id = self._session_id
            if session_id is None:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "F10 acknowledgement requires a current session.",
                )
            stale = sorted(
                input_id
                for input_id in requested
                if not input_id.startswith(f"{session_id}:")
            )
            if stale:
                raise HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "F10 input IDs must belong to the current session.",
                    details={"session_id": session_id, "stale_input_ids": stale},
                )
            for value in self._pending_inputs:
                if value["input_id"] not in requested:
                    continue
                matched.add(value["input_id"])
                if value["acknowledged"]:
                    already_acknowledged.append(value["input_id"])
                else:
                    value["acknowledged"] = True
                    value["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
                    acknowledged.append(value["input_id"])
        return {
            "session_id": session_id,
            "acknowledged": sorted(acknowledged),
            "acknowledged_count": len(acknowledged),
            "already_acknowledged": sorted(already_acknowledged),
            "not_found": sorted(requested - matched),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            root_count = sum(
                value.parent_id is None for value in self._registrations.values()
            )
            menu_count = sum(
                value.kind == "menu" for value in self._registrations.values()
            )
            command_count = len(self._registrations) - menu_count
            pending_count = sum(
                not value["acknowledged"] for value in self._pending_inputs
            )
            return {
                "session_monitor": self._monitor,
                "session_id": self._session_id,
                "initialized": root_count > 0,
                "initialized_scope_count": root_count,
                "registered_item_count": len(self._registrations),
                "registered_menu_count": menu_count,
                "registered_command_count": command_count,
                "stored_player_inputs": len(self._pending_inputs),
                "pending_player_inputs": pending_count,
                "latest_input_mission_time": self._latest_input_mission_time,
                "latest_outbound_message_mission_time": (
                    self._latest_outbound_message_mission_time
                ),
                "latest_outbound_message_at": self._latest_outbound_message_at,
                "session_rotations": self._session_rotations,
                "session_poll_interval_seconds": self.poll_interval,
                "last_session_check_at": self._last_session_check_at,
                "last_error": dict(self._last_error) if self._last_error else None,
                "input_monitor": self._input_monitor,
                "input_cursor": self._input_cursor,
                "input_polls": self._input_polls,
                "ignored_inputs": self._ignored_inputs,
                "input_overflows": self._input_overflows,
                "last_input_poll_at": self._last_input_poll_at,
                "last_input_error": (
                    dict(self._last_input_error) if self._last_input_error else None
                ),
            }

    def _prepare_operation(self) -> str:
        session_id = self._read_session()
        self._observe_session(session_id)
        return session_id

    def _confirm_operation(self, expected_session: str) -> None:
        actual_session = self._read_session()
        self._observe_session(actual_session)
        if actual_session != expected_session:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS session changed during the F10 operation.",
                details={
                    "reason": "SESSION_CHANGED_DURING_F10_OPERATION",
                    "session_before": expected_session,
                    "session_after": actual_session,
                },
            )

    def _observe_session(self, session_id: str) -> None:
        checked_at = datetime.now(timezone.utc).isoformat()
        previous: str | None
        with self._lock:
            previous = self._session_id
            if session_id != previous:
                if previous is not None:
                    self._session_rotations += 1
                self._session_id = session_id
                self._registrations.clear()
                self._pending_inputs.clear()
                self._input_cursor = 0
                self._input_monitor = "starting"
                self._input_polls = 0
                self._ignored_inputs = 0
                self._input_overflows = 0
                self._last_input_poll_at = None
                self._last_input_error = None
                self._latest_input_mission_time = None
                self._latest_outbound_message_mission_time = None
                self._latest_outbound_message_at = None
            self._monitor = "connected"
            self._last_session_check_at = checked_at
            self._last_error = None
        if session_id != previous:
            self._log(
                "f10_session",
                {"session_id": session_id, "previous_session_id": previous},
            )

    def _read_session(self) -> str:
        if self._session_reader is not None:
            value: Any = self._session_reader()
        else:
            response = self._call(
                MISSION_SERVICE,
                "GetSessionId",
                {},
                timeout=SESSION_TIMEOUT_SECONDS,
            )
            value = response.get("session_id") if isinstance(response, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC returned malformed session metadata.",
                details={"reason": "MALFORMED_SESSION_ID"},
            )
        return str(value)

    def _call(
        self,
        service: str,
        method: str,
        request: Mapping[str, Any],
        *,
        timeout: float = ACTION_TIMEOUT_SECONDS,
    ) -> Mapping[str, Any]:
        if self._rpc_caller is not None:
            return self._rpc_caller(service, method, request, timeout)
        with self._lock:
            if self._support is None:
                self._support = GrpcSupport(self.context)
            support = self._support
        return support.call(service, method, request, timeout=timeout)

    def _read_events(self, after_id: int, limit: int) -> list[dict[str, Any]]:
        if self._event_reader is not None:
            values = self._event_reader(after_id, limit)
        else:
            runtime_owner = getattr(self.context, "runtime", None)
            if runtime_owner is None:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "F10 input polling requires the resident events capability.",
                )
            events_handle = runtime_owner.plugin_handle("events")
            collector = events_handle.state
            current_store = getattr(collector, "current_store", None)
            if not callable(current_store):
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "The resident event collector is unavailable to F10 input polling.",
                )
            values = current_store().query_after_id(
                event_types=list(COMMAND_EVENT_TYPES),
                after_id=after_id,
                limit=limit,
            )
        if not isinstance(values, list) or any(
            not isinstance(value, Mapping) for value in values
        ):
            raise HarnessError(
                ErrorCode.INTERNAL_ERROR,
                "F10 event reader returned malformed data.",
            )
        return [dict(value) for value in values]

    def _selection(
        self,
        event: Mapping[str, Any],
        expected_session: str,
    ) -> dict[str, Any] | None:
        if str(event.get("session_id")) != expected_session:
            return None
        event_type = event.get("event_type")
        if event_type not in COMMAND_EVENT_TYPES:
            return None
        payload = event.get("payload")
        body = payload.get(event_type) if isinstance(payload, Mapping) else None
        details = body.get("details") if isinstance(body, Mapping) else None
        if not isinstance(details, Mapping) or details.get("source") != "dcs_harness":
            return None
        command_id = details.get("command_id")
        interaction_id = details.get("interaction_id")
        choice_id = details.get("choice_id")
        if not all(isinstance(value, str) and value for value in (
            command_id,
            interaction_id,
            choice_id,
        )):
            return None
        with self._lock:
            self._require_session(expected_session)
            registration = self._registrations.get(command_id)
        if (
            registration is None
            or registration.kind != "command"
            or registration.interaction_id != interaction_id
            or registration.choice_id != choice_id
            or not _scope_matches(registration.scope, str(event_type), body)
        ):
            return None
        received_at = event.get("received_at")
        if not isinstance(received_at, str) or received_at < registration.created_at:
            return None
        mission_time = event.get("mission_time")
        if (
            isinstance(mission_time, bool)
            or not isinstance(mission_time, (int, float))
            or not math.isfinite(float(mission_time))
        ):
            return None
        event_id = _event_id(event)
        selection: dict[str, Any] = {
            "input_id": f"{expected_session}:{event_id}",
            "event_id": event_id,
            "session_id": expected_session,
            "mission_time": float(mission_time),
            "received_at": received_at,
            "event_type": event_type,
            "scope": registration.scope.kind,
            "command_id": command_id,
            "interaction_id": interaction_id,
            "choice_id": choice_id,
            "player_id": None,
            "player_name": None,
            "acknowledged": False,
            "acknowledged_at": None,
        }
        if registration.action is not None:
            selection["action"] = registration.action
        if registration.data is not None:
            selection["data"] = copy.deepcopy(dict(registration.data))
        if registration.scope.coalition is not None:
            selection["coalition"] = registration.scope.coalition
        if registration.scope.group_name is not None:
            selection["group_name"] = registration.scope.group_name
            group = body.get("group")
            if isinstance(group, Mapping):
                group_id = group.get("id")
                if isinstance(group_id, int) and not isinstance(group_id, bool):
                    selection["group_id"] = group_id
        return selection

    def _append_input(self, selection: dict[str, Any], expected_session: str) -> None:
        with self._lock:
            self._require_session(expected_session)
            if any(
                value["input_id"] == selection["input_id"]
                for value in self._pending_inputs
            ):
                return
            if len(self._pending_inputs) >= MAX_RECENT_INPUTS:
                self._pending_inputs.pop(0)
                self._input_overflows += 1
            self._pending_inputs.append(selection)
            self._latest_input_mission_time = selection["mission_time"]
        self._log(
            "f10_player_input",
            {
                "session_id": expected_session,
                "input_id": selection["input_id"],
                "interaction_id": selection["interaction_id"],
                "choice_id": selection["choice_id"],
            },
        )

    def _registration(self, item_id: str) -> F10Registration | None:
        with self._lock:
            return self._registrations.get(item_id)

    def _require_new_item(self, item_id: str) -> None:
        if self._registration(item_id) is not None:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "An F10 item with this item_id is already registered.",
                details={"item_id": item_id},
            )

    def _require_parent(
        self,
        item_id: str,
        *,
        expected_session: str | None = None,
    ) -> F10Registration:
        registration = self._require_registration(item_id, expected_session)
        if registration.kind != "menu":
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "An F10 item can only be added below a registered menu.",
                details={"parent_id": item_id, "kind": registration.kind},
            )
        return registration

    def _require_registration(
        self,
        item_id: str,
        expected_session: str | None,
    ) -> F10Registration:
        with self._lock:
            if expected_session is not None:
                self._require_session(expected_session)
            registration = self._registrations.get(item_id)
        if registration is None:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "The requested F10 item is not registered by Harness.",
                details={"item_id": item_id},
            )
        return registration

    def _ensure_capacity(self) -> None:
        with self._lock:
            count = len(self._registrations)
        if count >= MAX_REGISTRATIONS:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "The bounded F10 registration limit has been reached.",
                details={"limit": MAX_REGISTRATIONS},
            )

    @staticmethod
    def _new_registration(
        *,
        item_id: str,
        kind: str,
        scope: F10Scope,
        name: str,
        path: tuple[str, ...],
        parent_id: str | None,
        interaction_id: str | None = None,
        choice_id: str | None = None,
        action: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> F10Registration:
        return F10Registration(
            item_id=item_id,
            kind=kind,
            scope=scope,
            name=name,
            path=path,
            parent_id=parent_id,
            interaction_id=interaction_id,
            choice_id=choice_id,
            action=action,
            data=copy.deepcopy(dict(data)) if data is not None else None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def _store_registration(
        self,
        registration: F10Registration,
        expected_session: str,
    ) -> None:
        with self._lock:
            self._require_session(expected_session)
            if registration.item_id in self._registrations:
                raise HarnessError(
                    ErrorCode.INTERNAL_ERROR,
                    "F10 registration changed during a serialized operation.",
                )
            self._registrations[registration.item_id] = registration

    def _roots(self, expected_session: str) -> list[F10Registration]:
        with self._lock:
            self._require_session(expected_session)
            return sorted(
                (
                    registration
                    for registration in self._registrations.values()
                    if registration.parent_id is None
                ),
                key=lambda value: value.item_id,
            )

    def _discard_tree(
        self,
        registration: F10Registration,
        expected_session: str,
    ) -> list[str]:
        with self._lock:
            self._require_session(expected_session)
            removed = sorted(
                item_id
                for item_id, candidate in self._registrations.items()
                if candidate.scope == registration.scope
                and candidate.path[: len(registration.path)] == registration.path
            )
            for item_id in removed:
                self._registrations.pop(item_id, None)
            return removed

    def _require_session(self, expected_session: str) -> None:
        if self._session_id != expected_session:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "F10 local state no longer belongs to the current DCS session.",
                details={"reason": "STALE_F10_SESSION"},
            )

    def _record_error(self, error: Exception) -> None:
        value = unavailable_error(error, "F10 session observation failed.")
        with self._lock:
            self._monitor = "disconnected"
            self._last_error = value
        self._log("f10_session_monitor_error", {"error_code": value["code"]})

    def _record_input_error(self, error: Exception) -> None:
        value = unavailable_error(error, "F10 player-input polling failed.")
        with self._lock:
            self._input_monitor = "degraded"
            self._last_input_error = value
        self._log("f10_input_monitor_error", {"error_code": value["code"]})

    def _log(self, event: str, extra: Mapping[str, Any] | None = None) -> None:
        self.logger.write(
            {
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "source": "f10",
                "event": event,
                **dict(extra or {}),
            }
        )


def _menu_method(scope: F10Scope, operation: str, kind: str) -> str:
    prefix = {
        "mission": "MissionCommand",
        "coalition": "CoalitionCommand",
        "group": "GroupCommand",
    }[scope.kind]
    if operation == "Add" and kind == "SubMenu":
        return f"Add{prefix}SubMenu"
    if operation == "Add" and kind == "Command":
        return f"Add{prefix}"
    return f"Remove{prefix}Item"


def _response_path(response: Mapping[str, Any]) -> tuple[str, ...]:
    path = response.get("path") if isinstance(response, Mapping) else None
    if (
        not isinstance(path, list)
        or not path
        or any(not isinstance(value, str) or not value for value in path)
    ):
        raise HarnessError(
            ErrorCode.GRPC_CALL_FAILED,
            "DCS-gRPC returned a malformed F10 menu path.",
            details={"reason": "MALFORMED_MENU_PATH"},
        )
    return tuple(path)


def _mission_time(response: Mapping[str, Any]) -> float:
    value = response.get("time") if isinstance(response, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessError(
            ErrorCode.GRPC_CALL_FAILED,
            "DCS-gRPC returned malformed mission time.",
            details={"reason": "MALFORMED_MISSION_TIME"},
        )
    return float(value)


def _event_id(event: Mapping[str, Any]) -> int:
    value = event.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "F10 event reader returned an invalid event id.",
        )
    return value


def _scope_matches(scope: F10Scope, event_type: str, body: Mapping[str, Any]) -> bool:
    if scope.kind == "mission":
        return event_type == "mission_command"
    if scope.kind == "coalition":
        return (
            event_type == "coalition_command"
            and body.get("coalition") == scope.coalition
        )
    group = body.get("group")
    return (
        event_type == "group_command"
        and isinstance(group, Mapping)
        and group.get("name") == scope.group_name
    )
