"""Built-in bounded static aircraft and loadout catalog capability."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.aircraft_catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_RESULT_LIMIT,
    AircraftCatalogRegistry,
)
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "catalog"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    status = AircraftCatalogRegistry(context.repository_root).status()
    return {
        "health": "ready",
        "schema_version": status["schema_version"],
        "source_revision": status["source"]["revision"],
        "counts": status["counts"],
        "preset_enrichment_available": status["preset_enrichment"]["included"],
        "source_warning_count": status["source_warning_count"],
    }


def describe() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": "stateless",
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "commands": {
            "status": {
                "description": "Show static catalog source, scope, counts, and enrichment status.",
                "arguments": {},
            },
            "aircraft-search": {
                "description": "Boundedly search exact pinned aircraft type definitions.",
                "arguments": {
                    "query": {"type": "string", "required": True},
                    "kind": {
                        "type": "string",
                        "enum": ["fixed_wing", "helicopter"],
                        "required": False,
                    },
                    "flyable": {"type": "boolean", "required": False},
                    "limit": {"type": "integer", "default": DEFAULT_RESULT_LIMIT},
                },
            },
            "aircraft-show": {
                "description": "Show one exact aircraft type and pylon summaries.",
                "arguments": {"aircraft": {"type": "string", "required": True}},
            },
            "loadout-pylons": {
                "description": "List pylon compatibility counts with optional bounded store expansion.",
                "arguments": {
                    "aircraft": {"type": "string", "required": True},
                    "expand": {"type": "boolean", "default": False},
                    "store_limit": {"type": "integer", "default": DEFAULT_RESULT_LIMIT},
                },
            },
            "loadout-stores": {
                "description": "Boundedly find compatible stores and legal pylons for one aircraft.",
                "arguments": {
                    "aircraft": {"type": "string", "required": True},
                    "pylon": {"type": "integer", "required": False},
                    "query": {"type": "string", "required": False},
                    "limit": {"type": "integer", "default": DEFAULT_RESULT_LIMIT},
                },
            },
            "loadout-presets": {
                "description": "List source-backed presets when optional enrichment is present.",
                "arguments": {"aircraft": {"type": "string", "required": True}},
            },
            "loadout-validate": {
                "description": "Validate pylon/CLSID compatibility without tactical recommendation.",
                "arguments": {
                    "aircraft": {"type": "string", "required": True},
                    "pylons": {
                        "type": "object",
                        "required": True,
                        "description": "Canonical pylon-number keys mapped to CLSID strings or clsid/settings objects.",
                    },
                },
            },
        },
    }


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    commands = {
        "status",
        "aircraft-search",
        "aircraft-show",
        "loadout-pylons",
        "loadout-stores",
        "loadout-presets",
        "loadout-validate",
    }
    if command not in commands:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    registry = AircraftCatalogRegistry(context.repository_root)
    if command == "status":
        _reject_unknown(args, set())
        return registry.status()
    if command == "aircraft-search":
        _reject_unknown(args, {"query", "kind", "flyable", "limit"})
        return registry.search_aircraft(
            args.get("query"),
            kind=args.get("kind"),
            flyable=args.get("flyable"),
            limit=args.get("limit", DEFAULT_RESULT_LIMIT),
        )
    if command == "aircraft-show":
        _reject_unknown(args, {"aircraft"})
        return registry.show_aircraft(args.get("aircraft"))
    if command == "loadout-pylons":
        _reject_unknown(args, {"aircraft", "expand", "store_limit"})
        return registry.pylons(
            args.get("aircraft"),
            expand=args.get("expand", False),
            store_limit=args.get("store_limit", DEFAULT_RESULT_LIMIT),
        )
    if command == "loadout-stores":
        _reject_unknown(args, {"aircraft", "pylon", "query", "limit"})
        return registry.stores(
            args.get("aircraft"),
            pylon=args.get("pylon"),
            query=args.get("query"),
            limit=args.get("limit", DEFAULT_RESULT_LIMIT),
        )
    if command == "loadout-presets":
        _reject_unknown(args, {"aircraft"})
        return registry.presets(args.get("aircraft"))
    if command == "loadout-validate":
        _reject_unknown(args, {"aircraft", "pylons"})
        return registry.validate_loadout(args.get("aircraft"), args.get("pylons"))
    raise AssertionError("validated catalog command was not dispatched")


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unknown arguments: {', '.join(unknown)}.",
            details={"unknown_arguments": unknown},
        )
