"""Resident current-session factual unit telemetry capability."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError
from dcs_harness_runtime.reporting import age_seconds
from dcs_harness_runtime.telemetry_collector import TelemetryCollector, TelemetryConfig


PLUGIN_NAME = "telemetry"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True

FILTER_ARGUMENTS = {"unit", "group", "coalition", "category", "fields", "limit"}
HISTORY_ARGUMENTS = {
    "unit",
    "instance_id",
    "group",
    "since",
    "until",
    "last_seconds",
    "fields",
    "step",
    "limit",
}


def describe() -> dict[str, Any]:
    filters = {
        "unit": {"type": ["string", "integer"], "required": False},
        "group": {"type": ["string", "integer"], "required": False},
        "coalition": {"type": "string", "required": False},
        "category": {"type": "string", "required": False},
        "fields": {"type": "array[string]", "required": False},
        "limit": {"type": "integer", "default": 200},
    }
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": PLUGIN_RUNTIME,
        "autostart": PLUGIN_AUTOSTART,
        "commands": {
            "status": {"description": "Show collector, memory, and task health."},
            "latest": {
                "description": "Return a bounded filtered latest factual snapshot.",
                "arguments": filters,
            },
            "snapshot": {
                "description": "Select a current-session snapshot by id or nearest mission time.",
                "arguments": {
                    "snapshot_id": {"type": "integer", "required": False},
                    "mission_time": {"type": "number", "required": False},
                    **filters,
                },
            },
            "history": {
                "description": "Return bounded current-session trajectory samples.",
                "arguments": {
                    "unit": {"type": ["string", "integer"], "required": False},
                    "instance_id": {"type": "string", "required": False},
                    "group": {"type": ["string", "integer"], "required": False},
                    "since": {"type": "number", "required": False},
                    "until": {"type": "number", "required": False},
                    "last_seconds": {"type": "number", "required": False},
                    "fields": {"type": "array[string]", "required": False},
                    "step": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 200},
                },
            },
            "list": {
                "description": "List bounded lightweight identities from the latest snapshot.",
                "arguments": {
                    key: value for key, value in filters.items() if key != "fields"
                },
            },
        },
    }


def start(context: Any, runtime: Any) -> TelemetryCollector:
    config = TelemetryConfig.from_environment(context.environment)
    collector = TelemetryCollector(
        context,
        context.runtime.runtime_logger,
        config,
    )
    runtime.state = collector
    if config.enabled:
        runtime.start_background("snapshot-loop", collector.run)
    return collector


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    if command not in {"status", "latest", "snapshot", "history", "list"}:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    collector, runtime = _collector(context)
    if command == "status":
        _reject_unknown(args, set())
        value = collector.status()
        value["background_task"] = runtime.task_status().get("snapshot-loop")
        return value
    if command == "latest":
        _reject_unknown(args, FILTER_ARGUMENTS)
        return collector.memory.latest(args)
    if command == "snapshot":
        _reject_unknown(args, FILTER_ARGUMENTS | {"snapshot_id", "mission_time"})
        return collector.memory.snapshot(args)
    if command == "history":
        _reject_unknown(args, HISTORY_ARGUMENTS)
        return collector.memory.history(args)
    if command == "list":
        _reject_unknown(args, FILTER_ARGUMENTS - {"fields"})
        return collector.memory.list_units(args)
    raise AssertionError("validated telemetry command was not dispatched")


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    collector = runtime.state
    if not isinstance(collector, TelemetryCollector):
        return {"health": "unavailable", "reason": "collector_unavailable"}
    status = collector.status()
    summary = collector.memory.fast_summary()
    if not status["enabled"]:
        health = "unavailable"
    elif status["collector"] == "running" and status["last_successful_sample"]:
        health = "ready"
    else:
        health = "degraded"
    return {
        "health": health,
        "collector": status["collector"],
        "enabled": status["enabled"],
        "session_id": status["session_id"],
        "snapshot_id": summary["snapshot_id"],
        "mission_time": summary["mission_time"],
        "captured_at": summary["captured_at"],
        "sample_age_seconds": age_seconds(status["last_successful_sample"]),
        "unit_count": summary["unit_count"],
        "group_count": summary["group_count"],
        "player_count": summary["player_count"],
        "players": summary["players"],
        "players_truncated": summary["players_truncated"],
        "partial": summary["partial"],
        "consecutive_failures": status["consecutive_failures"],
        "last_error": status["last_error"],
    }


def _collector(context: Any) -> tuple[TelemetryCollector, Any]:
    runtime_owner = context.runtime
    if runtime_owner is None:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "Capability runtime is unavailable in the shared context.",
        )
    runtime = runtime_owner.plugin_handle(PLUGIN_NAME)
    collector = runtime.state
    if not isinstance(collector, TelemetryCollector):
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "Telemetry collector is not initialized.",
        )
    return collector, runtime


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Telemetry command contains unsupported arguments.",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )
