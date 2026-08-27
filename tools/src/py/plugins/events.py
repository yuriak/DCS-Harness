"""Resident factual DCS event ledger capability."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.event_collector import EventCollector
from dcs_harness_runtime.event_store import DEFAULT_EVENT_LIMIT, EventStoreCatalog
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "events"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True
ALLOWED_QUERY_ARGUMENTS = {
    "since",
    "until",
    "event_type",
    "limit",
}


def describe() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": PLUGIN_RUNTIME,
        "autostart": PLUGIN_AUTOSTART,
        "commands": {
            "status": {"description": "Show event collector and ledger status."},
            "recent": {
                "description": "Return a bounded newest-first event list.",
                "arguments": {
                    "limit": {"type": "integer", "default": DEFAULT_EVENT_LIMIT},
                    "event_type": {"type": "string", "required": False},
                },
            },
            "query": {
                "description": "Query factual events using fixed filters.",
                "arguments": {
                    "since": {
                        "type": "number",
                        "required": False,
                        "description": "Inclusive lower mission-time bound.",
                    },
                    "until": {
                        "type": "number",
                        "required": False,
                        "description": "Inclusive upper mission-time bound.",
                    },
                    "event_type": {"type": "string", "required": False},
                    "limit": {"type": "integer", "default": DEFAULT_EVENT_LIMIT},
                },
            },
        },
    }


def start(context: Any, runtime: Any) -> EventCollector:
    stores = EventStoreCatalog(
        context.runtime_root / "events", context.repository_root
    )
    collector = EventCollector(context, stores, context.runtime.runtime_logger)
    runtime.state = collector
    runtime.start_background("event-stream", collector.run)
    return collector


def stop(context: Any, runtime: Any) -> None:
    collector = runtime.state
    if isinstance(collector, EventCollector):
        collector.cancel()


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    if command not in {"status", "recent", "query"}:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    collector, runtime = _collector(context)
    if command == "status":
        _reject_unknown(args, set())
        value = collector.status()
        value["background_task"] = runtime.task_status().get("event-stream")
        return value
    if command == "recent":
        _reject_unknown(args, {"limit", "event_type"})
        events = collector.current_store().query(
            event_type=args.get("event_type"),
            limit=args.get("limit", DEFAULT_EVENT_LIMIT),
        )
        return {"events": events, "count": len(events)}
    if command == "query":
        _reject_unknown(args, ALLOWED_QUERY_ARGUMENTS)
        events = collector.current_store().query(
            since=args.get("since"),
            until=args.get("until"),
            event_type=args.get("event_type"),
            limit=args.get("limit", DEFAULT_EVENT_LIMIT),
        )
        return {"events": events, "count": len(events)}
    raise AssertionError("validated events command was not dispatched")


def _collector(context: Any) -> tuple[EventCollector, Any]:
    runtime_owner = context.runtime
    if runtime_owner is None:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "Capability runtime is unavailable in the shared context.",
        )
    runtime = runtime_owner.plugin_handle(PLUGIN_NAME)
    collector = runtime.state
    if not isinstance(collector, EventCollector):
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "Event collector is not initialized.",
        )
    return collector, runtime


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Event command contains unsupported arguments.",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )
