"""Resident raw DCS process-log diagnostic capability."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dcs_harness_runtime.log_collector import (
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_TAIL_LINES,
    MAX_SEARCH_LIMIT,
    MAX_TAIL_LINES,
    DcsLogCollector,
)
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError
from dcs_harness_runtime.reporting import age_seconds


PLUGIN_NAME = "logs"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True
SOURCES = {"dcs", "grpc"}


def describe() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": PLUGIN_RUNTIME,
        "autostart": PLUGIN_AUTOSTART,
        "commands": {
            "status": {"description": "Show current DCS log-source status."},
            "tail": {
                "description": "Return bounded lines from the current log epoch.",
                "arguments": {
                    "source": {"type": "string", "required": True},
                    "lines": {"type": "integer", "default": DEFAULT_TAIL_LINES},
                },
            },
            "search": {
                "description": "Search the current log epoch by substring.",
                "arguments": {
                    "source": {"type": "string", "required": True},
                    "query": {"type": "string", "required": True},
                    "limit": {
                        "type": "integer",
                        "default": DEFAULT_SEARCH_LIMIT,
                    },
                },
            },
        },
    }


def start(context: Any, runtime: Any) -> DcsLogCollector:
    collector = DcsLogCollector(
        _source_paths(context),
        context.runtime_root / "logs" / "dcs",
        context.repository_root,
        context.runtime.runtime_logger,
    )
    runtime.state = collector
    runtime.start_background("log-follow", collector.run)
    return collector


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    if command not in {"status", "tail", "search"}:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    collector, runtime = _collector(context)
    if command == "status":
        _reject_unknown(args, set())
        value = collector.status()
        value["background_task"] = runtime.task_status().get("log-follow")
        return value
    source = _source(args.get("source"))
    follower = collector.follower(source)
    if command == "tail":
        _reject_unknown(args, {"source", "lines"})
        lines = _bounded_integer(
            args.get("lines", DEFAULT_TAIL_LINES), "lines", MAX_TAIL_LINES
        )
        values = follower.tail(lines)
        return {"source": source, "lines": values, "count": len(values)}
    if command == "search":
        _reject_unknown(args, {"source", "query", "limit"})
        query = args.get("query")
        if not isinstance(query, str) or not query:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Log search query must be a non-empty string.",
            )
        limit = _bounded_integer(
            args.get("limit", DEFAULT_SEARCH_LIMIT), "limit", MAX_SEARCH_LIMIT
        )
        values = follower.search(query, limit)
        return {"source": source, "query": query, "lines": values, "count": len(values)}
    raise AssertionError("validated logs command was not dispatched")


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    collector = runtime.state
    if not isinstance(collector, DcsLogCollector):
        return {"health": "unavailable", "reason": "collector_unavailable"}
    status = collector.status()
    sources = {
        name: {
            "state": source["state"],
            "epoch": source["mirror_path"],
            "mirror_path": source["mirror_path"],
            "last_update_at": source["last_update_at"],
            "last_update_age_seconds": age_seconds(source["last_update_at"]),
            "last_error": source["last_error"],
        }
        for name, source in status["sources"].items()
    }
    following = sum(source["state"] == "following" for source in sources.values())
    health = (
        "ready"
        if status["collector"] == "running" and following
        else "degraded" if status["collector"] == "running" else "unavailable"
    )
    return {
        "health": health,
        "collector": status["collector"],
        "following_source_count": following,
        "sources": sources,
    }


def _source_paths(context: Any) -> dict[str, Path | None]:
    dcs = context.environment.get("dcs", {})
    if not isinstance(dcs, Mapping):
        dcs = {}
    saved_games_value = dcs.get("saved_games_dir")
    saved_games = Path(str(saved_games_value)) if saved_games_value else None
    dcs_log_value = dcs.get("log_file")
    dcs_log = Path(str(dcs_log_value)) if dcs_log_value else None
    if dcs_log is None and saved_games is not None:
        dcs_log = saved_games / "Logs" / "dcs.log"
    grpc_log = saved_games / "Logs" / "gRPC.log" if saved_games else None
    return {"dcs": dcs_log, "grpc": grpc_log}


def _collector(context: Any) -> tuple[DcsLogCollector, Any]:
    runtime_owner = context.runtime
    if runtime_owner is None:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "Capability runtime is unavailable in the shared context.",
        )
    runtime = runtime_owner.plugin_handle(PLUGIN_NAME)
    collector = runtime.state
    if not isinstance(collector, DcsLogCollector):
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "DCS log collector is not initialized.",
        )
    return collector, runtime


def _source(value: Any) -> str:
    if not isinstance(value, str) or value not in SOURCES:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Log source must be one of the supported names.",
            details={"allowed": sorted(SOURCES)},
        )
    return value


def _bounded_integer(value: Any, name: str, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= maximum
    ):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"Log {name} must be between 1 and {maximum}.",
        )
    return value


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Log command contains unsupported arguments.",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )
