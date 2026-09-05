"""Built-in deterministic geographic knowledge capability."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.geo_catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_RESULT_LIMIT,
    GeoCatalogRegistry,
)
from dcs_harness_runtime.geo_live import GeoLiveBridge
from dcs_harness_runtime.geo_math import (
    convert_unit,
    distance_result,
    geographic_distance_m,
    geographic_initial_bearing_deg,
    geographic_offset,
    geographic_point,
    local_bearing_deg,
    local_distance_m,
    local_point,
    normalize_bearing,
    distance_input_m,
)
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "geo"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    maps = GeoCatalogRegistry(context.repository_root).maps()
    compact_maps = [
        {
            key: item.get(key)
            for key in ("id", "name", "data_version", "location_count", "kinds")
        }
        for item in maps
    ]
    live_configured = False
    try:
        live_configured = context.require_grpc_client_endpoint().eval_enabled
    except HarnessError:
        pass
    return {
        "health": "ready" if maps else "degraded",
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "map_count": len(maps),
        "maps": compact_maps,
        "live_conversion_configured": live_configured,
    }


def describe() -> dict[str, Any]:
    geographic = {
        "type": "object",
        "fields": {
            "latitude_deg": {"type": "number"},
            "longitude_deg": {"type": "number"},
        },
    }
    local = {
        "type": "object",
        "fields": {"x_m": {"type": "number"}, "z_m": {"type": "number"}},
    }
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": "stateless",
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "commands": {
            "status": {
                "description": "Show installed offline Geo catalogs and capability scope.",
                "arguments": {},
            },
            "maps": {
                "description": "List installed versioned map catalogs.",
                "arguments": {},
            },
            "search": {
                "description": "Boundedly search names and aliases in one map catalog.",
                "arguments": {
                    "map": {"type": "string", "required": True},
                    "query": {"type": "string", "required": True},
                    "kind": {"type": "string", "required": False},
                    "limit": {"type": "integer", "default": DEFAULT_RESULT_LIMIT},
                },
            },
            "lookup": {
                "description": "Resolve one stable id, canonical name, or alias.",
                "arguments": {
                    "map": {"type": "string", "required": True},
                    "location": {"type": "string", "required": True},
                    "kind": {"type": "string", "required": False},
                },
            },
            "nearest": {
                "description": "Find nearest catalog points to a geographic origin.",
                "arguments": {
                    "map": {"type": "string", "required": True},
                    "origin": geographic,
                    "kind": {"type": "string", "required": False},
                    "limit": {"type": "integer", "default": DEFAULT_RESULT_LIMIT},
                    "max_distance_m": {"type": "number", "required": False},
                },
            },
            "distance": {
                "description": "Calculate geographic or DCS local x/z distance.",
                "arguments": {
                    "coordinate_system": {
                        "type": "string",
                        "enum": ["geographic", "dcs_local_xz"],
                    },
                    "a": {"one_of": [geographic, local]},
                    "b": {"one_of": [geographic, local]},
                },
            },
            "bearing": {
                "description": "Calculate initial true or DCS local x/z bearing.",
                "arguments": {
                    "coordinate_system": {
                        "type": "string",
                        "enum": ["geographic", "dcs_local_xz"],
                    },
                    "a": {"one_of": [geographic, local]},
                    "b": {"one_of": [geographic, local]},
                },
            },
            "offset": {
                "description": "Offset a geographic point by bearing and distance.",
                "arguments": {
                    "origin": geographic,
                    "bearing_deg": {"type": "number"},
                    "distance": {
                        "type": "object",
                        "fields": {
                            "value": {"type": "number"},
                            "unit": {"type": "string"},
                        },
                    },
                },
            },
            "convert": {
                "description": "Convert geographic and DCS local coordinates using the live mission API.",
                "arguments": {
                    "direction": {
                        "type": "string",
                        "enum": ["geographic_to_local", "local_to_geographic"],
                    },
                    "geographic": {
                        "type": "object",
                        "required": False,
                        "fields": {
                            "latitude_deg": {"type": "number"},
                            "longitude_deg": {"type": "number"},
                            "elevation_m": {"type": "number", "default": 0},
                        },
                    },
                    "local": {
                        "type": "object",
                        "required": False,
                        "fields": {
                            "x_m": {"type": "number"},
                            "y_m": {"type": "number", "default": 0},
                            "z_m": {"type": "number"},
                        },
                    },
                },
            },
            "convert-unit": {
                "description": "Convert supported distance or speed units.",
                "arguments": {
                    "value": {"type": "number", "required": True},
                    "from_unit": {"type": "string", "required": True},
                    "to_unit": {"type": "string", "required": True},
                },
            },
        },
    }


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    commands = {
        "status",
        "maps",
        "search",
        "lookup",
        "nearest",
        "distance",
        "bearing",
        "offset",
        "convert",
        "convert-unit",
    }
    if command not in commands:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    registry = GeoCatalogRegistry(context.repository_root)
    if command == "status":
        _reject_unknown(args, set())
        maps = registry.maps()
        return {
            "api_version": PLUGIN_API_VERSION,
            "catalog_schema_version": CATALOG_SCHEMA_VERSION,
            "maps": maps,
            "map_count": len(maps),
            "live_conversion": {
                **GeoLiveBridge(context).status(),
            },
        }
    if command == "maps":
        _reject_unknown(args, set())
        maps = registry.maps()
        return {"maps": maps, "count": len(maps)}
    if command == "search":
        _reject_unknown(args, {"map", "query", "kind", "limit"})
        return registry.search(
            args.get("map"),
            args.get("query"),
            kind=args.get("kind"),
            limit=args.get("limit", DEFAULT_RESULT_LIMIT),
        )
    if command == "lookup":
        _reject_unknown(args, {"map", "location", "kind"})
        return registry.lookup(
            args.get("map"), args.get("location"), kind=args.get("kind")
        )
    if command == "nearest":
        _reject_unknown(
            args, {"map", "origin", "kind", "limit", "max_distance_m"}
        )
        return registry.nearest(
            args.get("map"),
            args.get("origin"),
            kind=args.get("kind"),
            limit=args.get("limit", DEFAULT_RESULT_LIMIT),
            max_distance_m=args.get("max_distance_m"),
        )
    if command in {"distance", "bearing"}:
        _reject_unknown(args, {"coordinate_system", "a", "b"})
        return _two_point(command, args)
    if command == "offset":
        _reject_unknown(args, {"origin", "bearing_deg", "distance"})
        origin = geographic_point(args.get("origin"), "origin")
        bearing = normalize_bearing(args.get("bearing_deg"))
        distance_m = distance_input_m(args.get("distance"))
        latitude, longitude = geographic_offset(origin, bearing, distance_m)
        return {
            "coordinate_system": "geographic",
            "origin": {
                "latitude_deg": origin[0],
                "longitude_deg": origin[1],
            },
            "bearing_deg": bearing,
            **distance_result(distance_m),
            "destination": {
                "latitude_deg": latitude,
                "longitude_deg": longitude,
            },
            "model": "sphere",
        }
    if command == "convert-unit":
        _reject_unknown(args, {"value", "from_unit", "to_unit"})
        return convert_unit(
            args.get("value"), args.get("from_unit"), args.get("to_unit")
        )
    if command == "convert":
        return GeoLiveBridge(context).convert(args)
    raise AssertionError("validated Geo command was not dispatched")


def _two_point(command: str, args: Mapping[str, Any]) -> dict[str, Any]:
    coordinate_system = args.get("coordinate_system")
    if coordinate_system == "geographic":
        first = geographic_point(args.get("a"), "a")
        second = geographic_point(args.get("b"), "b")
        if command == "distance":
            return {
                "coordinate_system": coordinate_system,
                **distance_result(geographic_distance_m(first, second)),
                "model": "sphere",
            }
        return {
            "coordinate_system": coordinate_system,
            "bearing_deg": geographic_initial_bearing_deg(first, second),
            "reference": "true_north",
        }
    if coordinate_system == "dcs_local_xz":
        first = local_point(args.get("a"), "a")
        second = local_point(args.get("b"), "b")
        if command == "distance":
            return {
                "coordinate_system": coordinate_system,
                **distance_result(local_distance_m(first, second)),
                "model": "planar",
            }
        return {
            "coordinate_system": coordinate_system,
            "bearing_deg": local_bearing_deg(first, second),
            "reference": "local_x_north_z_east",
        }
    raise HarnessError(
        ErrorCode.INVALID_ARGUMENT,
        "coordinate_system must be 'geographic' or 'dcs_local_xz'.",
        details={"reason": "INVALID_COORDINATE_SYSTEM"},
    )


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Geo command contains unsupported arguments.",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )
