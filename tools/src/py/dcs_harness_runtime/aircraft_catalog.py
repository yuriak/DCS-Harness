"""Strict loading and bounded queries for the generated aircraft catalog."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from .result import ErrorCode, HarnessError


CATALOG_SCHEMA_VERSION = 1
DEFAULT_RESULT_LIMIT = 20
MAX_RESULT_LIMIT = 100
MAX_LOADOUT_ASSIGNMENTS = 64
MAX_SETTINGS_DEPTH = 4
MAX_SETTINGS_BYTES = 4096
PYLON_KEY_PATTERN = re.compile(r"^[1-9][0-9]*$")


def _search_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and char.isalnum()
    )


class AircraftCatalogRegistry:
    def __init__(self, repository_root: Path, data_path: Path | None = None) -> None:
        self.repository_root = repository_root.resolve()
        self.data_path = (
            data_path.resolve()
            if data_path is not None
            else self.repository_root
            / "tools"
            / "data"
            / "catalog"
            / "aircraft.json"
        )
        self.catalog = self._load()
        self.aircraft_by_id = {
            item["type_id"]: item for item in self.catalog["aircraft"]
        }
        self.stores_by_clsid = {
            item["clsid"]: item for item in self.catalog["stores"]
        }

    def status(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog["catalog_id"],
            "schema_version": self.catalog["schema_version"],
            "generator": dict(self.catalog["generator"]),
            "source": dict(self.catalog["source"]),
            "preset_enrichment": dict(self.catalog["preset_enrichment"]),
            "counts": dict(self.catalog["counts"]),
            "source_warning_count": len(self.catalog["source_warnings"]),
            "scope": "static_pinned_definitions",
        }

    def search_aircraft(
        self,
        query_value: Any,
        *,
        kind: Any = None,
        flyable: Any = None,
        limit: Any = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        query = self._required_text(query_value, "query")
        query_key = _search_key(query)
        if not query_key:
            self._invalid("query must contain letters or numbers", "INVALID_QUERY")
        kind_value = self._kind(kind)
        flyable_value = self._optional_bool(flyable, "flyable")
        result_limit = self._limit(limit)
        ranked = []
        for aircraft in self.catalog["aircraft"]:
            if kind_value is not None and aircraft["kind"] != kind_value:
                continue
            if flyable_value is not None and aircraft["flyable"] != flyable_value:
                continue
            candidate = _search_key(aircraft["type_id"])
            if query_key == candidate:
                rank = 0
            elif candidate.startswith(query_key):
                rank = 1
            elif query_key in candidate:
                rank = 2
            else:
                continue
            ranked.append(
                (
                    rank,
                    aircraft["display_name"].casefold(),
                    aircraft["type_id"],
                    self._aircraft_summary(aircraft),
                )
            )
        ranked.sort(key=lambda item: item[:3])
        matches = [item[-1] for item in ranked[:result_limit]]
        return {
            "query": query,
            "kind": kind_value,
            "flyable": flyable_value,
            "aircraft": matches,
            "count": len(matches),
            "matched_count": len(ranked),
            "truncated": len(ranked) > result_limit,
        }

    def show_aircraft(self, aircraft_value: Any) -> dict[str, Any]:
        aircraft = self._aircraft(aircraft_value)
        value = self._aircraft_summary(aircraft)
        value["pylons"] = [self._pylon_summary(item) for item in aircraft["pylons"]]
        value["source"] = {
            "type": self.catalog["source"]["type"],
            "revision": self.catalog["source"]["revision"],
        }
        return value

    def pylons(
        self,
        aircraft_value: Any,
        *,
        expand: Any = False,
        store_limit: Any = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        aircraft = self._aircraft(aircraft_value)
        expand_value = self._bool(expand, "expand")
        expansion_limit = self._limit(store_limit)
        pylons = []
        for item in aircraft["pylons"]:
            value = self._pylon_summary(item)
            if expand_value:
                stores = sorted(
                    (
                        self._store_summary(self.stores_by_clsid[clsid], [item["index"]])
                        for clsid in item["allowed_store_clsids"]
                    ),
                    key=lambda store: (store["name"].casefold(), store["clsid"]),
                )
                value["stores"] = stores[:expansion_limit]
                value["returned_store_count"] = len(value["stores"])
                value["stores_truncated"] = len(stores) > expansion_limit
            pylons.append(value)
        return {
            "aircraft": self._aircraft_summary(aircraft),
            "pylons": pylons,
            "count": len(pylons),
            "expanded": expand_value,
            "per_pylon_store_limit": expansion_limit if expand_value else None,
        }

    def stores(
        self,
        aircraft_value: Any,
        *,
        pylon: Any = None,
        query: Any = None,
        limit: Any = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        aircraft = self._aircraft(aircraft_value)
        pylon_value = self._optional_pylon(pylon)
        selected_pylons = aircraft["pylons"]
        if pylon_value is not None:
            selected_pylons = [
                item for item in selected_pylons if item["index"] == pylon_value
            ]
            if not selected_pylons:
                self._invalid(
                    f"Aircraft {aircraft['type_id']!r} has no declared pylon {pylon_value}.",
                    "PYLON_NOT_FOUND",
                    aircraft=aircraft["type_id"],
                    pylon=pylon_value,
                )
        query_value = self._optional_text(query, "query")
        query_key = _search_key(query_value) if query_value is not None else None
        result_limit = self._limit(limit)

        allowed: dict[str, list[int]] = {}
        for pylon_item in selected_pylons:
            for clsid in pylon_item["allowed_store_clsids"]:
                allowed.setdefault(clsid, []).append(pylon_item["index"])

        ranked = []
        for clsid, allowed_pylons in allowed.items():
            store = self.stores_by_clsid[clsid]
            if query_key is None:
                rank = 0
            else:
                candidates = (
                    _search_key(store["name"]),
                    _search_key(store["clsid"]),
                    _search_key(store["source_symbol"]),
                )
                if query_key in candidates:
                    rank = 0
                elif any(candidate.startswith(query_key) for candidate in candidates):
                    rank = 1
                elif any(query_key in candidate for candidate in candidates):
                    rank = 2
                else:
                    continue
            ranked.append(
                (
                    rank,
                    store["name"].casefold(),
                    clsid,
                    self._store_summary(store, sorted(allowed_pylons)),
                )
            )
        ranked.sort(key=lambda item: item[:3])
        stores = [item[-1] for item in ranked[:result_limit]]
        return {
            "aircraft": self._aircraft_summary(aircraft),
            "pylon": pylon_value,
            "query": query_value,
            "stores": stores,
            "count": len(stores),
            "matched_count": len(ranked),
            "truncated": len(ranked) > result_limit,
        }

    def presets(self, aircraft_value: Any) -> dict[str, Any]:
        aircraft = self._aircraft(aircraft_value)
        enrichment = dict(self.catalog["preset_enrichment"])
        return {
            "aircraft": self._aircraft_summary(aircraft),
            "available": bool(enrichment["included"]),
            "presets": [],
            "count": 0,
            "enrichment": enrichment,
        }

    def validate_loadout(self, aircraft_value: Any, pylons_value: Any) -> dict[str, Any]:
        requested_aircraft = self._required_text(aircraft_value, "aircraft")
        try:
            aircraft = self._aircraft(requested_aircraft)
        except HarnessError as error:
            if not error.details or error.details.get("reason") != "AIRCRAFT_NOT_FOUND":
                raise
            return {
                "valid": False,
                "aircraft": {"requested": requested_aircraft, "known": False},
                "assignments": [],
                "failures": [
                    {
                        "code": "AIRCRAFT_NOT_FOUND",
                        "aircraft": requested_aircraft,
                        "message": "aircraft is not present in the static catalog",
                    }
                ],
                "settings_validation": "not_available_in_base_catalog",
            }
        if not isinstance(pylons_value, Mapping):
            self._invalid("pylons must be an object keyed by pylon number", "INVALID_PYLONS")
        if len(pylons_value) > MAX_LOADOUT_ASSIGNMENTS:
            self._invalid(
                f"pylons may contain at most {MAX_LOADOUT_ASSIGNMENTS} assignments",
                "TOO_MANY_PYLON_ASSIGNMENTS",
            )

        declared = {item["index"]: item for item in aircraft["pylons"]}
        assignments = []
        failures = []
        for raw_key, raw_value in pylons_value.items():
            if not isinstance(raw_key, str) or not PYLON_KEY_PATTERN.fullmatch(raw_key):
                failures.append(
                    {
                        "code": "INVALID_PYLON_KEY",
                        "pylon": str(raw_key),
                        "message": "pylon keys must be canonical positive integer strings",
                    }
                )
                continue
            pylon_index = int(raw_key)
            clsid, settings, input_failure = self._loadout_value(raw_value, pylon_index)
            if input_failure is not None:
                failures.append(input_failure)
                continue
            assert clsid is not None
            pylon_record = declared.get(pylon_index)
            store = self.stores_by_clsid.get(clsid)
            assignment = {
                "pylon": pylon_index,
                "clsid": clsid,
                "store_known": store is not None,
                "store_name": store["name"] if store is not None else None,
                "pylon_declared": pylon_record is not None,
                "pylon_definition_available": (
                    pylon_record["definition_available"] if pylon_record else None
                ),
                "allowed": False,
                "settings_present": settings is not None,
            }
            if pylon_record is None:
                failures.append(
                    {
                        "code": "PYLON_NOT_DECLARED",
                        "pylon": pylon_index,
                        "message": "aircraft does not declare this pylon",
                    }
                )
            elif not pylon_record["definition_available"]:
                failures.append(
                    {
                        "code": "PYLON_DEFINITION_UNAVAILABLE",
                        "pylon": pylon_index,
                        "message": "pinned pydcs declares the pylon but exposes no compatibility definition",
                    }
                )
            elif store is None:
                failures.append(
                    {
                        "code": "STORE_NOT_FOUND",
                        "pylon": pylon_index,
                        "clsid": clsid,
                        "message": "CLSID is not present in the static store catalog",
                    }
                )
            elif clsid not in pylon_record["allowed_store_clsids"]:
                failures.append(
                    {
                        "code": "STORE_NOT_ALLOWED_ON_PYLON",
                        "pylon": pylon_index,
                        "clsid": clsid,
                        "store_name": store["name"],
                        "message": "pinned pydcs does not list this store for the pylon",
                    }
                )
            else:
                assignment["allowed"] = True
            assignments.append(assignment)

        assignments.sort(key=lambda item: item["pylon"])
        failures.sort(key=lambda item: (str(item.get("pylon", "")), item["code"]))
        return {
            "valid": not failures,
            "aircraft": {**self._aircraft_summary(aircraft), "known": True},
            "assignments": assignments,
            "failures": failures,
            "settings_validation": "shape_only_no_catalog_schema",
        }

    def _loadout_value(
        self, value: Any, pylon_index: int
    ) -> tuple[str | None, Mapping[str, Any] | None, dict[str, Any] | None]:
        settings = None
        if isinstance(value, str):
            clsid = value
        elif isinstance(value, Mapping):
            unknown = sorted(set(value) - {"clsid", "settings"})
            if unknown:
                return None, None, {
                    "code": "INVALID_LOADOUT_ENTRY",
                    "pylon": pylon_index,
                    "unknown_fields": unknown,
                    "message": "loadout entry contains unknown fields",
                }
            clsid = value.get("clsid")
            settings = value.get("settings")
            if settings is not None and not isinstance(settings, Mapping):
                return None, None, {
                    "code": "INVALID_SETTINGS",
                    "pylon": pylon_index,
                    "message": "settings must be an object when supplied",
                }
            if settings is not None:
                try:
                    self._validate_json_value(settings, 0)
                    encoded = json.dumps(
                        settings, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                    ).encode("utf-8")
                except (TypeError, ValueError):
                    return None, None, {
                        "code": "INVALID_SETTINGS",
                        "pylon": pylon_index,
                        "message": "settings must contain bounded finite JSON values",
                    }
                if len(encoded) > MAX_SETTINGS_BYTES:
                    return None, None, {
                        "code": "INVALID_SETTINGS",
                        "pylon": pylon_index,
                        "message": f"settings exceed {MAX_SETTINGS_BYTES} UTF-8 bytes",
                    }
        else:
            return None, None, {
                "code": "INVALID_LOADOUT_ENTRY",
                "pylon": pylon_index,
                "message": "loadout entry must be a CLSID string or object",
            }
        if not isinstance(clsid, str) or not clsid:
            return None, None, {
                "code": "INVALID_CLSID",
                "pylon": pylon_index,
                "message": "clsid must be a non-empty string",
            }
        return clsid, settings, None

    def _validate_json_value(self, value: Any, depth: int) -> None:
        if depth > MAX_SETTINGS_DEPTH:
            raise ValueError("settings nesting is too deep")
        if value is None or isinstance(value, (str, bool)):
            return
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                finite = math.isfinite(float(value))
            except OverflowError as error:
                raise ValueError("settings number is not finite") from error
            if not finite:
                raise ValueError("settings number is not finite")
            return
        if isinstance(value, list):
            for item in value:
                self._validate_json_value(item, depth + 1)
            return
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("settings keys must be strings")
            for item in value.values():
                self._validate_json_value(item, depth + 1)
            return
        raise TypeError("settings value is not JSON-safe")

    @staticmethod
    def _aircraft_summary(aircraft: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "type_id": aircraft["type_id"],
            "display_name": aircraft["display_name"],
            "kind": aircraft["kind"],
            "category": aircraft["category"],
            "flyable": aircraft["flyable"],
            "pylon_count": aircraft["pylon_count"],
            "known_tasks": [dict(item) for item in aircraft["known_tasks"]],
        }

    @staticmethod
    def _pylon_summary(pylon: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "index": pylon["index"],
            "definition_available": pylon["definition_available"],
            "allowed_store_count": pylon["allowed_store_count"],
        }

    @staticmethod
    def _store_summary(store: Mapping[str, Any], pylons: list[int]) -> dict[str, Any]:
        return {
            "clsid": store["clsid"],
            "name": store["name"],
            "weight_kg": store["weight_kg"],
            "source_symbol": store["source_symbol"],
            "allowed_pylons": pylons,
            "settings_metadata": None,
        }

    def _aircraft(self, value: Any) -> dict[str, Any]:
        requested = self._required_text(value, "aircraft")
        exact = [
            item
            for item in self.catalog["aircraft"]
            if requested.casefold() == item["type_id"].casefold()
        ]
        if not exact:
            key = _search_key(requested)
            exact = [
                item
                for item in self.catalog["aircraft"]
                if key == _search_key(item["type_id"])
            ]
        if not exact:
            self._invalid(
                f"Aircraft {requested!r} was not found.",
                "AIRCRAFT_NOT_FOUND",
                aircraft=requested,
            )
        if len(exact) > 1:
            self._invalid(
                f"Aircraft {requested!r} is ambiguous.",
                "AIRCRAFT_AMBIGUOUS",
                aircraft=requested,
                matches=[item["type_id"] for item in exact],
            )
        return exact[0]

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            AircraftCatalogRegistry._invalid(
                f"{field} is required and must be a string of at most 200 characters",
                "INVALID_TEXT",
                field=field,
            )
        return value.strip()

    @staticmethod
    def _optional_text(value: Any, field: str) -> str | None:
        if value is None:
            return None
        return AircraftCatalogRegistry._required_text(value, field)

    @staticmethod
    def _kind(value: Any) -> str | None:
        if value is None:
            return None
        if value not in {"fixed_wing", "helicopter"}:
            AircraftCatalogRegistry._invalid(
                "kind must be fixed_wing or helicopter", "INVALID_KIND"
            )
        return value

    @staticmethod
    def _bool(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            AircraftCatalogRegistry._invalid(
                f"{field} must be boolean", "INVALID_BOOLEAN", field=field
            )
        return value

    @staticmethod
    def _optional_bool(value: Any, field: str) -> bool | None:
        if value is None:
            return None
        return AircraftCatalogRegistry._bool(value, field)

    @staticmethod
    def _optional_pylon(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            AircraftCatalogRegistry._invalid(
                "pylon must be a positive integer", "INVALID_PYLON"
            )
        return value

    @staticmethod
    def _limit(value: Any) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= MAX_RESULT_LIMIT
        ):
            AircraftCatalogRegistry._invalid(
                f"limit must be an integer from 1 to {MAX_RESULT_LIMIT}",
                "INVALID_LIMIT",
            )
        return value

    @staticmethod
    def _invalid(message: str, reason: str, **details: Any) -> None:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            message,
            details={"reason": reason, **details},
        )

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.data_path.read_text(encoding="utf-8"))
            self._validate_catalog(value)
            return value
        except HarnessError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Aircraft catalog could not be loaded.",
                details={
                    "reason": "CATALOG_INVALID",
                    "path": str(self.data_path),
                    "exception_type": type(error).__name__,
                },
            ) from error

    def _validate_catalog(self, value: Any) -> None:
        if not isinstance(value, dict) or value.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported aircraft catalog schema")
        if value.get("catalog_id") != "pydcs-aircraft-loadouts":
            raise ValueError("unexpected aircraft catalog id")
        for field in (
            "generator",
            "source",
            "preset_enrichment",
            "counts",
        ):
            if not isinstance(value.get(field), dict):
                raise TypeError(f"catalog {field} must be an object")
        if value["preset_enrichment"].get("included") is not False:
            raise ValueError("schema v1 supports only the pinned base catalog")
        if not isinstance(value.get("source_warnings"), list):
            raise TypeError("catalog source_warnings must be an array")
        aircraft_items = value.get("aircraft")
        store_items = value.get("stores")
        if not isinstance(aircraft_items, list) or not isinstance(store_items, list):
            raise TypeError("catalog aircraft and stores must be arrays")

        stores = {}
        for store in store_items:
            if not isinstance(store, dict):
                raise TypeError("store must be an object")
            clsid = store["clsid"]
            if not isinstance(clsid, str) or not clsid or clsid in stores:
                raise ValueError("store CLSIDs must be unique non-empty strings")
            if not isinstance(store["name"], str) or not isinstance(store["source_symbol"], str):
                raise TypeError("store name and source_symbol must be strings")
            weight = store["weight_kg"]
            if weight is not None and (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(float(weight))
                or weight < 0
            ):
                raise ValueError("store weight must be null or non-negative finite")
            stores[clsid] = store

        aircraft_ids = set()
        compatibility_count = 0
        missing_count = 0
        for aircraft in aircraft_items:
            if not isinstance(aircraft, dict):
                raise TypeError("aircraft must be an object")
            aircraft_id = aircraft["type_id"]
            if not isinstance(aircraft_id, str) or not aircraft_id or aircraft_id in aircraft_ids:
                raise ValueError("aircraft type IDs must be unique non-empty strings")
            aircraft_ids.add(aircraft_id)
            if aircraft["kind"] not in {"fixed_wing", "helicopter"}:
                raise ValueError("invalid aircraft kind")
            if not isinstance(aircraft["flyable"], bool):
                raise TypeError("aircraft flyable must be boolean")
            if not isinstance(aircraft["known_tasks"], list) or not isinstance(aircraft["pylons"], list):
                raise TypeError("aircraft tasks and pylons must be arrays")
            if aircraft["pylon_count"] != len(aircraft["pylons"]):
                raise ValueError("aircraft pylon count mismatch")
            indexes = set()
            for pylon in aircraft["pylons"]:
                index = pylon["index"]
                if not isinstance(index, int) or isinstance(index, bool) or index < 1 or index in indexes:
                    raise ValueError("invalid or duplicate pylon index")
                indexes.add(index)
                clsids = pylon["allowed_store_clsids"]
                if not isinstance(pylon["definition_available"], bool) or not isinstance(clsids, list):
                    raise TypeError("invalid pylon definition")
                if len(clsids) != len(set(clsids)) or pylon["allowed_store_count"] != len(clsids):
                    raise ValueError("pylon store count or uniqueness mismatch")
                if any(clsid not in stores for clsid in clsids):
                    raise ValueError("pylon references unknown store")
                if not pylon["definition_available"]:
                    missing_count += 1
                    if clsids:
                        raise ValueError("undefined pylon cannot expose stores")
                compatibility_count += len(clsids)
        counts = value["counts"]
        expected_counts = {
            "aircraft": len(aircraft_items),
            "fixed_wing": sum(item["kind"] == "fixed_wing" for item in aircraft_items),
            "helicopter": sum(item["kind"] == "helicopter" for item in aircraft_items),
            "stores": len(store_items),
            "pylon_compatibility_entries": compatibility_count,
            "declared_pylons_without_definition": missing_count,
        }
        if counts != expected_counts:
            raise ValueError("catalog counts do not match content")
