"""Resident factual DCS event ledger capability."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.event_collector import EventCollector
from dcs_harness_runtime.event_normalization import (
    COMBAT_EVENT_TYPES,
    entity_identity,
)
from dcs_harness_runtime.event_store import (
    DEFAULT_EVENT_LIMIT,
    MAX_EVENT_LIMIT,
    EventStoreCatalog,
)
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError
from dcs_harness_runtime.reporting import age_seconds


PLUGIN_NAME = "events"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True
ALLOWED_QUERY_ARGUMENTS = {
    "since",
    "until",
    "event_type",
    "event_types",
    "after_id",
    "initiator_unit",
    "initiator_group",
    "target_unit",
    "target_group",
    "unit",
    "group",
    "coalition",
    "source",
    "limit",
}
COMBAT_QUERY_ARGUMENTS = ALLOWED_QUERY_ARGUMENTS - {"event_type", "event_types"}
LOSS_EVENT_TYPES = frozenset({"unit_lost", "dead", "crash"})
ATTRIBUTION_WINDOW_SECONDS = 120.0


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
                    "event_types": {"type": "array[string]", "required": False},
                    "after_id": {"type": "integer", "required": False},
                    "initiator_unit": {"type": ["string", "integer"], "required": False},
                    "initiator_group": {"type": ["string", "integer"], "required": False},
                    "target_unit": {"type": ["string", "integer"], "required": False},
                    "target_group": {"type": ["string", "integer"], "required": False},
                    "unit": {"type": ["string", "integer"], "required": False},
                    "group": {"type": ["string", "integer"], "required": False},
                    "coalition": {"type": "string", "required": False},
                    "source": {"type": "string", "required": False},
                    "limit": {"type": "integer", "default": DEFAULT_EVENT_LIMIT},
                },
            },
            "combat": {
                "description": "Return bounded combat chronology with explicit factual attribution labels.",
                "arguments": {
                    "since": {"type": "number", "required": False},
                    "until": {"type": "number", "required": False},
                    "after_id": {"type": "integer", "required": False},
                    "unit": {"type": ["string", "integer"], "required": False},
                    "group": {"type": ["string", "integer"], "required": False},
                    "coalition": {"type": "string", "required": False},
                    "source": {"type": "string", "required": False},
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
    runtime.start_background("native-combat", collector.native_combat.run)
    return collector


def stop(context: Any, runtime: Any) -> None:
    collector = runtime.state
    if isinstance(collector, EventCollector):
        collector.cancel()


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    if command not in {"status", "recent", "query", "combat"}:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    collector, runtime = _collector(context)
    if command == "status":
        _reject_unknown(args, set())
        value = collector.status()
        value["background_task"] = runtime.task_status().get("event-stream")
        value["native_combat"]["background_task"] = runtime.task_status().get(
            "native-combat"
        )
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
        events = collector.current_store().query(**dict(args))
        return {"events": events, "count": len(events)}
    if command == "combat":
        _reject_unknown(args, COMBAT_QUERY_ARGUMENTS)
        query = dict(args)
        requested_limit = collector.current_store().validate_limit(
            query.get("limit", DEFAULT_EVENT_LIMIT)
        )
        scan_limit = min(MAX_EVENT_LIMIT, max(100, requested_limit * 4))
        query["limit"] = scan_limit
        query["event_types"] = sorted(COMBAT_EVENT_TYPES)
        events = collector.current_store().query(**query)
        compact = _combat_view(events)[:requested_limit]
        return {
            "events": compact,
            "count": len(compact),
            "correlation_scan_count": len(events),
            "correlation_scan_saturated": len(events) == scan_limit,
            "attribution_note": (
                "correlated_hit_then_loss is temporal correlation within the scanned "
                "query window, not a confirmed kill"
            ),
        }
    raise AssertionError("validated events command was not dispatched")


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    collector = runtime.state
    if not isinstance(collector, EventCollector):
        return {"health": "unavailable", "reason": "collector_unavailable"}
    status = collector.status()
    latest = None
    try:
        events = collector.current_store().query(limit=1)
        latest = events[0] if events else None
    except HarnessError:
        pass
    native = status["native_combat"]
    grpc_ready = status["collector"] == "running" and status["stream"] == "connected"
    native_ready = native["collector"] == "running" and native["installed"]
    if grpc_ready and native_ready:
        health = "ready"
    elif grpc_ready or native_ready:
        health = "degraded"
    else:
        health = "unavailable"
    return {
        "health": health,
        "collector": status["collector"],
        "stream": status["stream"],
        "session_id": status["session_id"],
        "stored_events": status["stored_events"],
        "last_event_at": status["last_event_at"],
        "last_event_age_seconds": age_seconds(status["last_event_at"]),
        "latest_event": (
            {
                "id": latest.get("id"),
                "event_type": latest.get("event_type"),
                "source": latest.get("source"),
                "sources": latest.get("sources"),
                "mission_time": latest.get("mission_time"),
                "received_at": latest.get("received_at"),
            }
            if latest
            else None
        ),
        "mission_time": latest.get("mission_time") if latest else None,
        "last_error": status["last_error"],
        "native_combat": {
            "collector": native["collector"],
            "session_id": native["session_id"],
            "installed": native["installed"],
            "cursor": native["cursor"],
            "queue_gaps": native["queue_gaps"],
            "extraction_errors": native["extraction_errors"],
            "last_poll_at": native["last_poll_at"],
            "last_poll_age_seconds": age_seconds(native["last_poll_at"]),
            "last_event_at": native["last_event_at"],
            "last_error": native["last_error"],
        },
    }


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


def _combat_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: dict[tuple[str, Any], dict[str, Any]] = {}
    kills: dict[tuple[str, Any], dict[str, Any]] = {}
    result: dict[int, dict[str, Any]] = {}
    # Arrival order differs between the stream and the polled native observer.
    # At equal simulation times, process evidence before terminal losses.
    for event in sorted(events, key=lambda item: (
        item.get("mission_time") if item.get("mission_time") is not None else float("inf"),
        {"hit": 0, "kill": 1}.get(item["event_type"], 2),
        item["id"],
    )):
        normalized = event.get("normalized")
        normalized = normalized if isinstance(normalized, Mapping) else {}
        event_type = event["event_type"]
        attribution = None
        if event_type == "hit":
            identity = entity_identity(normalized.get("target"))
            attacker = entity_identity(normalized.get("initiator"))
            if identity is not None and attacker is not None:
                hits[identity] = event
        elif event_type == "kill":
            identity = entity_identity(normalized.get("target"))
            attacker = entity_identity(normalized.get("initiator"))
            if identity is not None and attacker is not None:
                kills[identity] = event
                attribution = {
                    "status": "confirmed_by_kill_event",
                    "evidence_event_id": event["id"],
                }
            else:
                attribution = {
                    "status": "unattributed_loss",
                    "evidence_event_id": event["id"],
                    "reason": "incomplete_kill_event_identity",
                }
        elif event_type in LOSS_EVENT_TYPES:
            identity = entity_identity(normalized.get("initiator"))
            kill = kills.get(identity) if identity is not None else None
            hit = hits.get(identity) if identity is not None else None
            if kill is not None and _within_window(kill, event):
                attribution = {
                    "status": "confirmed_by_kill_event",
                    "evidence_event_id": kill["id"],
                }
            elif hit is not None and _within_window(hit, event):
                hit_normalized = hit.get("normalized") or {}
                attribution = {
                    "status": "correlated_hit_then_loss",
                    "evidence_event_id": hit["id"],
                    "correlated_initiator": hit_normalized.get("initiator"),
                }
            else:
                attribution = {"status": "unattributed_loss"}
        result[event["id"]] = {
            "id": event["id"],
            "session_id": event["session_id"],
            "mission_time": event["mission_time"],
            "received_at": event["received_at"],
            "event_type": event_type,
            "source": event["source"],
            "sources": event["sources"],
            "initiator": normalized.get("initiator"),
            "target": normalized.get("target"),
            "weapon": normalized.get("weapon"),
            "attribution": attribution,
        }
    return [result[event["id"]] for event in events]


def _within_window(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    first_time = first.get("mission_time")
    second_time = second.get("mission_time")
    return (
        isinstance(first_time, (int, float))
        and isinstance(second_time, (int, float))
        and 0 <= second_time - first_time <= ATTRIBUTION_WINDOW_SECONDS
    )
