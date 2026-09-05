"""Resident in-game player communication capability."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from dcs_harness_runtime.f10_runtime import F10Runtime, F10Scope
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.reporting import age_seconds
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "f10"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True
DEFAULT_ROOT_NAME = "DCS-Harness"
DEFAULT_DISPLAY_TIME = 10
MAX_DISPLAY_TIME = 120
MAX_TEXT_LENGTH = 2000
MAX_NAME_LENGTH = 80
MAX_DETAILS_BYTES = 4096
DEFAULT_INPUT_LIMIT = 50
MAX_INPUT_LIMIT = 100
MAX_ACK_INPUTS = 100
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
COALITIONS = {
    "neutral": "COALITION_NEUTRAL",
    "red": "COALITION_RED",
    "blue": "COALITION_BLUE",
}


def describe() -> dict[str, Any]:
    scope_arguments = {
        "scope": {
            "type": "string",
            "enum": ["mission", "coalition", "group"],
            "required": True,
        },
        "coalition": {
            "type": "string",
            "enum": sorted(COALITIONS),
            "required": False,
        },
        "group_name": {"type": "string", "required": False},
    }
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": PLUGIN_RUNTIME,
        "autostart": PLUGIN_AUTOSTART,
        "commands": {
            "status": {
                "description": "Show current-session F10 state and Harness-owned items."
            },
            "send": {
                "description": "Send bounded in-game text using typed DCS-gRPC.",
                "arguments": {
                    "scope": scope_arguments["scope"],
                    "coalition": scope_arguments["coalition"],
                    "group_id": {"type": "integer", "required": False},
                    "text": {"type": "string", "required": True},
                    "display_time": {
                        "type": "integer",
                        "default": DEFAULT_DISPLAY_TIME,
                    },
                    "clear_view": {"type": "boolean", "default": False},
                },
            },
            "init": {
                "description": "Explicitly create one Harness-owned root submenu.",
                "arguments": {
                    **scope_arguments,
                    "root_name": {"type": "string", "default": DEFAULT_ROOT_NAME},
                    "player_ping": {"type": "boolean", "default": True},
                },
            },
            "add-menu": {
                "description": "Add a Harness-owned submenu below a registered menu.",
                "arguments": {
                    "item_id": {"type": "string", "required": True},
                    "parent_id": {"type": "string", "required": True},
                    "name": {"type": "string", "required": True},
                },
            },
            "add-command": {
                "description": (
                    "Add a structured player-choice command without arbitrary callbacks."
                ),
                "arguments": {
                    "item_id": {"type": "string", "required": True},
                    "parent_id": {"type": "string", "required": True},
                    "name": {"type": "string", "required": True},
                    "interaction_id": {"type": "string", "required": True},
                    "choice_id": {"type": "string", "required": True},
                    "action": {"type": "string", "required": False},
                    "data": {"type": "object", "required": False},
                },
            },
            "remove": {
                "description": "Remove a registered Harness-owned item and its descendants.",
                "arguments": {"item_id": {"type": "string", "required": True}},
            },
            "clear": {
                "description": "Remove every currently registered Harness-owned root menu.",
                "arguments": {},
            },
            "recent": {
                "description": "Return bounded newest-first player selections.",
                "arguments": {
                    "limit": {"type": "integer", "default": DEFAULT_INPUT_LIMIT},
                    "pending_only": {"type": "boolean", "default": False},
                    "interaction_id": {"type": "string", "required": False},
                },
            },
            "ack": {
                "description": "Acknowledge current-session player selections by input ID.",
                "arguments": {
                    "input_ids": {"type": "array[string]", "required": True}
                },
            },
        },
        "notes": [
            "Starting the resident plugin does not modify the mission menu; init is explicit.",
            "Group text uses group_id; group menu operations use group_name.",
            "Group menu behavior remains pending focused HIL; no Lua fallback is implicit.",
        ],
    }


def start(context: Any, runtime: Any) -> F10Runtime:
    state = F10Runtime(context, context.runtime.runtime_logger)
    runtime.state = state
    runtime.start_background("session-monitor", state.run)
    return state


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    commands = {
        "status",
        "send",
        "init",
        "add-menu",
        "add-command",
        "remove",
        "clear",
        "recent",
        "ack",
    }
    if command not in commands:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    state, runtime = _state(context)
    if command == "status":
        _reject_unknown(args, set())
        value = state.status()
        value["registrations"] = state.registrations()
        value["background_task"] = runtime.task_status().get("session-monitor")
        return value
    if command == "send":
        _reject_unknown(
            args,
            {"scope", "coalition", "group_id", "text", "display_time", "clear_view"},
        )
        scope = _scope_kind(args.get("scope"))
        coalition = _coalition(args.get("coalition")) if scope == "coalition" else None
        group_id = (
            _positive_integer(args.get("group_id"), "group_id")
            if scope == "group"
            else None
        )
        _reject_inapplicable_target(args, scope, menu=False)
        text = _bounded_text(args.get("text"), "text", MAX_TEXT_LENGTH)
        display_time = _display_time(args.get("display_time", DEFAULT_DISPLAY_TIME))
        clear_view = args.get("clear_view", False)
        if not isinstance(clear_view, bool):
            raise _invalid("clear_view must be boolean.")
        return state.send(
            scope=scope,
            coalition=coalition,
            group_id=group_id,
            text=text,
            display_time=display_time,
            clear_view=clear_view,
        )
    if command == "init":
        _reject_unknown(
            args,
            {"scope", "coalition", "group_name", "root_name", "player_ping"},
        )
        scope = _menu_scope(args)
        root_name = _bounded_text(
            args.get("root_name", DEFAULT_ROOT_NAME), "root_name", MAX_NAME_LENGTH
        )
        player_ping = args.get("player_ping", True)
        if not isinstance(player_ping, bool):
            raise _invalid("player_ping must be boolean.")
        result = state.init(scope, root_name)
        result["player_ping"] = (
            _ensure_player_ping(state, scope, result["registration"]["item_id"])
            if player_ping
            else None
        )
        return result
    if command == "add-menu":
        _reject_unknown(args, {"item_id", "parent_id", "name"})
        return state.add_menu(
            item_id=_identifier(args.get("item_id"), "item_id"),
            parent_id=_identifier(args.get("parent_id"), "parent_id", allow_root=True),
            name=_bounded_text(args.get("name"), "name", MAX_NAME_LENGTH),
        )
    if command == "add-command":
        _reject_unknown(
            args,
            {"item_id", "parent_id", "name", "interaction_id", "choice_id", "action", "data"},
        )
        action = args.get("action")
        if action is not None:
            action = _identifier(action, "action")
        data = _details(args.get("data"))
        return state.add_command(
            item_id=_identifier(args.get("item_id"), "item_id"),
            parent_id=_identifier(args.get("parent_id"), "parent_id", allow_root=True),
            name=_bounded_text(args.get("name"), "name", MAX_NAME_LENGTH),
            interaction_id=_identifier(args.get("interaction_id"), "interaction_id"),
            choice_id=_identifier(args.get("choice_id"), "choice_id"),
            action=action,
            data=data,
        )
    if command == "remove":
        _reject_unknown(args, {"item_id"})
        return state.remove(_identifier(args.get("item_id"), "item_id", allow_root=True))
    if command == "clear":
        _reject_unknown(args, set())
        return state.clear()
    if command == "recent":
        _reject_unknown(args, {"limit", "pending_only", "interaction_id"})
        limit = _bounded_integer(
            args.get("limit", DEFAULT_INPUT_LIMIT),
            "limit",
            minimum=1,
            maximum=MAX_INPUT_LIMIT,
        )
        pending_only = args.get("pending_only", False)
        if not isinstance(pending_only, bool):
            raise _invalid("pending_only must be boolean.")
        interaction_id = args.get("interaction_id")
        if interaction_id is not None:
            interaction_id = _identifier(interaction_id, "interaction_id")
        return state.recent_inputs(
            limit=limit,
            pending_only=pending_only,
            interaction_id=interaction_id,
        )
    if command == "ack":
        _reject_unknown(args, {"input_ids"})
        return state.acknowledge(_input_ids(args.get("input_ids")))
    raise AssertionError("validated F10 command was not dispatched")


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    state = runtime.state
    if not isinstance(state, F10Runtime):
        return {"health": "unavailable", "reason": "state_unavailable"}
    status = state.status()
    if (
        status["session_monitor"] == "connected"
        and status["session_id"] is not None
        and status["input_monitor"] == "running"
    ):
        health = "ready"
    elif status["session_id"] is not None:
        health = "degraded"
    else:
        health = "unavailable"
    return {
        "health": health,
        "session_id": status["session_id"],
        "initialized": status["initialized"],
        "initialized_scope_count": status["initialized_scope_count"],
        "registered_item_count": status["registered_item_count"],
        "registered_menu_count": status["registered_menu_count"],
        "registered_command_count": status["registered_command_count"],
        "pending_player_inputs": status["pending_player_inputs"],
        "stored_player_inputs": status["stored_player_inputs"],
        "latest_input_mission_time": status["latest_input_mission_time"],
        "latest_outbound_message_mission_time": status["latest_outbound_message_mission_time"],
        "latest_outbound_message_at": status["latest_outbound_message_at"],
        "session_monitor": status["session_monitor"],
        "last_session_check_at": status["last_session_check_at"],
        "last_session_check_age_seconds": age_seconds(status["last_session_check_at"]),
        "last_error": status["last_error"],
        "input_monitor": status["input_monitor"],
        "input_cursor": status["input_cursor"],
        "input_overflows": status["input_overflows"],
        "last_input_poll_at": status["last_input_poll_at"],
        "last_input_poll_age_seconds": age_seconds(status["last_input_poll_at"]),
        "last_input_error": status["last_input_error"],
    }


def _ensure_player_ping(
    state: F10Runtime,
    scope: F10Scope,
    parent_id: str,
) -> dict[str, Any]:
    suffix = hashlib.sha256(scope.key.encode("utf-8")).hexdigest()[:12]
    item_id = f"system.player_ping.{suffix}"
    existing = next(
        (
            value
            for value in state.registrations()
            if value["item_id"] == item_id
        ),
        None,
    )
    if existing is not None:
        if (
            existing.get("parent_id") != parent_id
            or existing.get("interaction_id") != "player-ping"
            or existing.get("choice_id") != "request-attention"
        ):
            raise HarnessError(
                ErrorCode.INTERNAL_ERROR,
                "The reserved Player Ping registration conflicts with local state.",
            )
        return {"created": False, "registration": existing}
    return state.add_command(
        item_id=item_id,
        parent_id=parent_id,
        name="Request Director Attention",
        interaction_id="player-ping",
        choice_id="request-attention",
        action="request_director_attention",
        data=None,
    )


def _state(context: Any) -> tuple[F10Runtime, Any]:
    runtime_owner = context.runtime
    if runtime_owner is None:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "Capability runtime is unavailable in the shared context.",
        )
    runtime = runtime_owner.plugin_handle(PLUGIN_NAME)
    state = runtime.state
    if not isinstance(state, F10Runtime):
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "F10 communication state is not initialized.",
        )
    return state, runtime


def _menu_scope(args: Mapping[str, Any]) -> F10Scope:
    kind = _scope_kind(args.get("scope"))
    _reject_inapplicable_target(args, kind, menu=True)
    if kind == "mission":
        return F10Scope(kind)
    if kind == "coalition":
        return F10Scope(kind, coalition=_coalition(args.get("coalition")))
    return F10Scope(
        kind,
        group_name=_bounded_text(args.get("group_name"), "group_name", MAX_NAME_LENGTH),
    )


def _scope_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in {"mission", "coalition", "group"}:
        raise _invalid("scope must be one of: mission, coalition, group.")
    return str(value)


def _coalition(value: Any) -> str:
    if not isinstance(value, str) or value.casefold() not in COALITIONS:
        raise _invalid("coalition must be one of: blue, neutral, red.")
    return COALITIONS[value.casefold()]


def _reject_inapplicable_target(args: Mapping[str, Any], scope: str, *, menu: bool) -> None:
    group_field = "group_name" if menu else "group_id"
    if scope != "coalition" and "coalition" in args:
        raise _invalid("coalition is only valid for coalition scope.")
    if scope != "group" and group_field in args:
        raise _invalid(f"{group_field} is only valid for group scope.")


def _identifier(value: Any, field: str, *, allow_root: bool = False) -> str:
    if (
        allow_root
        and isinstance(value, str)
        and value.startswith("root:")
        and len(value) <= 256
        and not any(ord(character) < 32 for character in value)
    ):
        return value
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise _invalid(
            f"{field} must match {IDENTIFIER_PATTERN.pattern}.",
            {"field": field},
        )
    if not allow_root and value.startswith("root:"):
        raise _invalid(f"{field} uses a reserved root: prefix.", {"field": field})
    return value


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _invalid(
            f"{field} must be a non-empty string of at most {maximum} characters.",
            {"field": field, "maximum": maximum},
        )
    return value


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _invalid(f"{field} must be a positive integer.", {"field": field})
    return value


def _display_time(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_DISPLAY_TIME
    ):
        raise _invalid(f"display_time must be an integer from 1 to {MAX_DISPLAY_TIME}.")
    return value


def _bounded_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise _invalid(
            f"{field} must be an integer from {minimum} to {maximum}.",
            {"field": field, "minimum": minimum, "maximum": maximum},
        )
    return value


def _input_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ACK_INPUTS:
        raise _invalid(
            f"input_ids must contain between 1 and {MAX_ACK_INPUTS} strings."
        )
    result = []
    for item in value:
        if (
            not isinstance(item, str)
            or len(item) > 160
            or not re.fullmatch(r"[0-9]+:[0-9]+", item)
        ):
            raise _invalid("Each input_id must use the current session:event format.")
        result.append(item)
    return list(dict.fromkeys(result))


def _details(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _invalid("data must be a JSON object.")
    _validate_json_value(value, depth=0)
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise _invalid(
            "data must contain only JSON-safe values.",
            {"exception_type": type(error).__name__},
        ) from error
    if len(encoded.encode("utf-8")) > MAX_DETAILS_BYTES:
        raise _invalid(
            f"data must encode to at most {MAX_DETAILS_BYTES} bytes.",
            {"maximum_bytes": MAX_DETAILS_BYTES},
        )
    return dict(value)


def _validate_json_value(value: Any, *, depth: int) -> None:
    if depth > 4:
        raise _invalid("data nesting depth must not exceed 4.")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _invalid("data object keys must be strings.")
        for child in value.values():
            _validate_json_value(child, depth=depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _validate_json_value(child, depth=depth + 1)
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise _invalid("data must contain only finite JSON-safe values.")


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise _invalid(
            "F10 command contains unsupported arguments.",
            {"unknown": unknown, "allowed": sorted(allowed)},
        )


def _invalid(message: str, details: Mapping[str, Any] | None = None) -> HarnessError:
    return HarnessError(ErrorCode.INVALID_ARGUMENT, message, details=details)
