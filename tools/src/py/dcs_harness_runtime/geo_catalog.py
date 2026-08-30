"""Strict loading and bounded lookup of versioned geographic catalogs."""

from __future__ import annotations

import copy
import difflib
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .geo_math import (
    distance_result,
    geographic_distance_m,
    geographic_initial_bearing_deg,
    geographic_point,
)
from .result import ErrorCode, HarnessError


CATALOG_SCHEMA_VERSION = 1
DEFAULT_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 50
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


class GeoCatalogRegistry:
    def __init__(self, repository_root: Path, data_root: Path | None = None) -> None:
        self.repository_root = repository_root.resolve()
        self.data_root = (
            data_root.resolve()
            if data_root is not None
            else self.repository_root / "tools" / "data" / "maps"
        )
        self._catalogs = self._load_catalogs()

    def maps(self) -> list[dict[str, Any]]:
        return [self._map_summary(item) for item in self._catalogs.values()]

    def map_catalog(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, str) or not value.strip():
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Argument 'map' is required and must be a string.",
                details={"reason": "MAP_NOT_FOUND"},
            )
        query = normalize_name(value)
        matches = [
            item
            for item in self._catalogs.values()
            if query
            in {
                normalize_name(item["id"]),
                normalize_name(item["name"]),
                *(normalize_name(alias) for alias in item["aliases"]),
            }
        ]
        if len(matches) != 1:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Map catalog {value!r} was not found.",
                details={
                    "reason": "MAP_NOT_FOUND",
                    "available": [item["name"] for item in self._catalogs.values()],
                },
            )
        return matches[0]

    def search(
        self,
        map_value: Any,
        query_value: Any,
        *,
        kind: Any = None,
        limit: Any = DEFAULT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        catalog = self.map_catalog(map_value)
        query = self._required_text(query_value, "query")
        normalized_query = normalize_name(query)
        if not normalized_query:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Search query must contain letters or numbers.",
                details={"reason": "INVALID_QUERY"},
            )
        kind_value = self._optional_kind(kind)
        result_limit = self._limit(limit)
        ranked: list[tuple[int, float, str, str, dict[str, Any]]] = []
        for location in catalog["locations"]:
            if kind_value and location["kind"] != kind_value:
                continue
            match = self._match(location, normalized_query)
            if match is None:
                continue
            rank, score, match_type, matched_name = match
            value = self._location_summary(catalog, location, coordinates=False)
            value["match"] = {
                "type": match_type,
                "name": matched_name,
                "score": round(score, 6),
            }
            ranked.append((rank, -score, location["name"], location["id"], value))
        ranked.sort(key=lambda item: item[:4])
        matches = [item[-1] for item in ranked[:result_limit]]
        return {
            "map": catalog["name"],
            "query": query,
            "kind": kind_value,
            "locations": matches,
            "count": len(matches),
            "matched_count": len(ranked),
            "truncated": len(ranked) > result_limit,
        }

    def lookup(
        self, map_value: Any, identifier: Any, *, kind: Any = None
    ) -> dict[str, Any]:
        catalog = self.map_catalog(map_value)
        requested = self._required_text(identifier, "location")
        normalized = normalize_name(requested)
        kind_value = self._optional_kind(kind)
        matches = []
        for location in catalog["locations"]:
            if kind_value and location["kind"] != kind_value:
                continue
            exact_names = {
                normalize_name(location["id"]),
                normalize_name(location["name"]),
                *(normalize_name(alias) for alias in location["aliases"]),
            }
            if normalized in exact_names:
                matches.append(location)
        if not matches:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Location {requested!r} was not found in {catalog['name']}.",
                details={
                    "reason": "LOCATION_NOT_FOUND",
                    "map": catalog["name"],
                    "kind": kind_value,
                },
            )
        if len(matches) > 1:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Location {requested!r} is ambiguous in {catalog['name']}.",
                details={
                    "reason": "LOCATION_AMBIGUOUS",
                    "matches": [item["id"] for item in matches],
                },
            )
        return self._public_location(catalog, matches[0])

    def nearest(
        self,
        map_value: Any,
        origin_value: Any,
        *,
        kind: Any = None,
        limit: Any = DEFAULT_RESULT_LIMIT,
        max_distance_m: Any = None,
    ) -> dict[str, Any]:
        catalog = self.map_catalog(map_value)
        origin = geographic_point(origin_value, "origin")
        kind_value = self._optional_kind(kind)
        result_limit = self._limit(limit)
        distance_limit = None
        if max_distance_m is not None:
            if isinstance(max_distance_m, bool):
                valid = False
            else:
                try:
                    max_distance_m = float(max_distance_m)
                    valid = math.isfinite(max_distance_m) and max_distance_m >= 0.0
                except (TypeError, ValueError):
                    valid = False
            if not valid:
                raise HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "max_distance_m must be a non-negative finite number.",
                    details={"reason": "INVALID_DISTANCE"},
                )
            distance_limit = max_distance_m

        ranked = []
        for location in catalog["locations"]:
            if kind_value and location["kind"] != kind_value:
                continue
            point = (location["latitude_deg"], location["longitude_deg"])
            distance_m = geographic_distance_m(origin, point)
            if distance_limit is not None and distance_m > distance_limit:
                continue
            value = self._location_summary(catalog, location, coordinates=True)
            value.update(distance_result(distance_m))
            value["bearing_deg"] = (
                0.0
                if distance_m < 1e-9
                else geographic_initial_bearing_deg(origin, point)
            )
            ranked.append((distance_m, location["name"], location["id"], value))
        ranked.sort(key=lambda item: item[:3])
        locations = [item[-1] for item in ranked[:result_limit]]
        return {
            "map": catalog["name"],
            "origin": {
                "latitude_deg": origin[0],
                "longitude_deg": origin[1],
            },
            "kind": kind_value,
            "locations": locations,
            "count": len(locations),
            "matched_count": len(ranked),
            "truncated": len(ranked) > result_limit,
        }

    def _load_catalogs(self) -> dict[str, dict[str, Any]]:
        if not self.data_root.is_dir():
            self._catalog_error("Map catalog directory is unavailable.")
        paths = sorted(self.data_root.glob("*.json"))
        if not paths:
            self._catalog_error("No map catalogs are installed.")
        catalogs: dict[str, dict[str, Any]] = {}
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                catalog = self._validate_catalog(value, path)
            except HarnessError:
                raise
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                self._catalog_error(
                    "Map catalog could not be read.",
                    path=path,
                    exception_type=type(error).__name__,
                )
            if catalog["id"] in catalogs:
                self._catalog_error("Map catalog id is duplicated.", path=path)
            catalogs[catalog["id"]] = catalog
        return dict(sorted(catalogs.items()))

    def _validate_catalog(self, value: Any, path: Path) -> dict[str, Any]:
        if not isinstance(value, dict):
            self._catalog_error("Map catalog must contain an object.", path=path)
        required = {
            "schema_version",
            "id",
            "name",
            "aliases",
            "data_version",
            "sources",
            "locations",
        }
        if not required.issubset(value):
            self._catalog_error("Map catalog is missing required fields.", path=path)
        if value["schema_version"] != CATALOG_SCHEMA_VERSION:
            self._catalog_error("Map catalog schema version is unsupported.", path=path)
        catalog_id = self._catalog_id(value["id"], "map id", path)
        name = self._catalog_text(value["name"], "map name", path)
        aliases = self._catalog_text_list(value["aliases"], "map aliases", path)
        data_version = self._catalog_text(
            value["data_version"], "data version", path
        )
        sources = value["sources"]
        if not isinstance(sources, dict) or not sources:
            self._catalog_error("Map sources must be a non-empty object.", path=path)
        for source_id, source in sources.items():
            self._catalog_id(source_id, "source id", path)
            if not isinstance(source, dict) or not source:
                self._catalog_error("Every map source must be an object.", path=path)

        raw_locations = value["locations"]
        if not isinstance(raw_locations, list) or not raw_locations:
            self._catalog_error("Map locations must be a non-empty array.", path=path)
        ids: set[str] = set()
        locations = []
        for raw in raw_locations:
            if not isinstance(raw, dict):
                self._catalog_error("Each map location must be an object.", path=path)
            location_id = self._catalog_id(raw.get("id"), "location id", path)
            if not location_id.startswith(f"{catalog_id}.") or location_id in ids:
                self._catalog_error(
                    "Location ids must be unique and prefixed by the map id.", path=path
                )
            ids.add(location_id)
            try:
                latitude, longitude = geographic_point(
                    {
                        "latitude_deg": raw.get("latitude_deg"),
                        "longitude_deg": raw.get("longitude_deg"),
                    },
                    "catalog location",
                )
            except HarnessError:
                self._catalog_error(
                    "Location geographic coordinates are invalid.", path=path
                )
            elevation = raw.get("elevation_m")
            if elevation is not None and (
                isinstance(elevation, bool)
                or not isinstance(elevation, (int, float))
                or not math.isfinite(float(elevation))
            ):
                self._catalog_error("Location elevation must be finite or null.", path=path)
            source_id = raw.get("source_id")
            if source_id not in sources:
                self._catalog_error("Location references an unknown source.", path=path)
            metadata = raw.get("metadata", {})
            if not isinstance(metadata, dict):
                self._catalog_error("Location metadata must be an object.", path=path)
            locations.append(
                {
                    "id": location_id,
                    "kind": self._catalog_id(raw.get("kind"), "location kind", path),
                    "name": self._catalog_text(raw.get("name"), "location name", path),
                    "aliases": self._catalog_text_list(
                        raw.get("aliases", []), "location aliases", path
                    ),
                    "latitude_deg": latitude,
                    "longitude_deg": longitude,
                    "elevation_m": float(elevation) if elevation is not None else None,
                    "metadata": copy.deepcopy(metadata),
                    "source_id": source_id,
                }
            )
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "id": catalog_id,
            "name": name,
            "aliases": aliases,
            "data_version": data_version,
            "sources": copy.deepcopy(sources),
            "locations": locations,
            "path": path,
        }

    @staticmethod
    def _match(
        location: Mapping[str, Any], query: str
    ) -> tuple[int, float, str, str] | None:
        names = [(location["name"], "canonical")]
        names.extend((alias, "alias") for alias in location["aliases"])
        names.append((location["id"], "id"))
        normalized_names = [(name, kind, normalize_name(name)) for name, kind in names]
        for name, kind, normalized in normalized_names:
            if query == normalized:
                return 0, 1.0, f"{kind}_exact", name
        for name, kind, normalized in normalized_names:
            if normalized.startswith(query):
                return 1, len(query) / len(normalized), f"{kind}_prefix", name
        for name, kind, normalized in normalized_names:
            if query in normalized:
                return 2, len(query) / len(normalized), f"{kind}_substring", name
        if len(query) < 3:
            return None
        candidates = [
            (difflib.SequenceMatcher(None, query, normalized).ratio(), name, kind)
            for name, kind, normalized in normalized_names
        ]
        score, name, kind = max(candidates)
        if score < 0.6:
            return None
        return 3, score, f"{kind}_approximate", name

    @staticmethod
    def _public_location(
        catalog: Mapping[str, Any], location: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "id": location["id"],
            "map": catalog["name"],
            "kind": location["kind"],
            "name": location["name"],
            "aliases": list(location["aliases"]),
            "latitude_deg": location["latitude_deg"],
            "longitude_deg": location["longitude_deg"],
            "elevation_m": location["elevation_m"],
            "metadata": copy.deepcopy(location["metadata"]),
            "source": copy.deepcopy(catalog["sources"][location["source_id"]]),
        }

    @staticmethod
    def _location_summary(
        catalog: Mapping[str, Any],
        location: Mapping[str, Any],
        *,
        coordinates: bool,
    ) -> dict[str, Any]:
        value = {
            "id": location["id"],
            "map": catalog["name"],
            "kind": location["kind"],
            "name": location["name"],
            "aliases": list(location["aliases"]),
        }
        if coordinates:
            value.update(
                {
                    "latitude_deg": location["latitude_deg"],
                    "longitude_deg": location["longitude_deg"],
                    "elevation_m": location["elevation_m"],
                }
            )
        return value

    @staticmethod
    def _map_summary(catalog: Mapping[str, Any]) -> dict[str, Any]:
        kinds = Counter(item["kind"] for item in catalog["locations"])
        return {
            "id": catalog["id"],
            "name": catalog["name"],
            "aliases": list(catalog["aliases"]),
            "schema_version": catalog["schema_version"],
            "data_version": catalog["data_version"],
            "location_count": len(catalog["locations"]),
            "kinds": dict(sorted(kinds.items())),
            "sources": copy.deepcopy(catalog["sources"]),
        }

    @staticmethod
    def _required_text(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Argument {name!r} is required and must be a non-empty string.",
                details={"reason": "INVALID_QUERY", "field": name},
            )
        return value.strip()

    @staticmethod
    def _optional_kind(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Argument 'kind' must be a normalized non-empty string.",
                details={"reason": "INVALID_QUERY", "field": "kind"},
            )
        return value

    @staticmethod
    def _limit(value: Any) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= MAX_RESULT_LIMIT
        ):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Result limit must be between 1 and {MAX_RESULT_LIMIT}.",
                details={"reason": "INVALID_LIMIT"},
            )
        return value

    def _catalog_text(self, value: Any, field: str, path: Path) -> str:
        if not isinstance(value, str) or not value.strip():
            self._catalog_error(f"Catalog {field} must be non-empty text.", path=path)
        return value

    def _catalog_id(self, value: Any, field: str, path: Path) -> str:
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            self._catalog_error(f"Catalog {field} is invalid.", path=path)
        return value

    def _catalog_text_list(self, value: Any, field: str, path: Path) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            self._catalog_error(f"Catalog {field} must be a text array.", path=path)
        if len({normalize_name(item) for item in value}) != len(value):
            self._catalog_error(f"Catalog {field} contains duplicates.", path=path)
        return list(value)

    def _catalog_error(
        self,
        message: str,
        *,
        path: Path | None = None,
        exception_type: str | None = None,
    ) -> None:
        details: dict[str, Any] = {"reason": "CATALOG_INVALID"}
        if path is not None:
            try:
                details["path"] = path.resolve().relative_to(
                    self.repository_root
                ).as_posix()
            except ValueError:
                details["path"] = path.name
        if exception_type is not None:
            details["exception_type"] = exception_type
        raise HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            message,
            details=details,
        )
