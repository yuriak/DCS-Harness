#!/usr/bin/env python3
"""Build the reviewed Caucasus Geo catalog from pinned pydcs data."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPOSITORY_ROOT / "tools" / "data" / "maps" / "caucasus.json"
OPERATIONAL_LANDMARKS_PATH = (
    REPOSITORY_ROOT
    / "tools"
    / "data"
    / "maps"
    / "sources"
    / "caucasus-operational-landmarks.json"
)
DATA_VERSION = "2026-08-31.1"

AIRBASE_ALIASES = {
    "Anapa-Vityazevo": ["Anapa Airport", "Vityazevo"],
    "Krasnodar-Center": ["Krasnodar Center"],
    "Maykop-Khanskaya": ["Maykop", "Khanskaya"],
    "Sochi-Adler": ["Sochi Airport", "Adler Airport"],
    "Krasnodar-Pashkovsky": ["Krasnodar Pashkovsky"],
    "Sukhumi-Babushara": ["Sukhumi Airport", "Babushara"],
    "Senaki-Kolkhi": ["Senaki-Kolkhida", "Senaki Airport"],
    "Mineralnye Vody": ["Mineralnye-Vody"],
    "Tbilisi-Lochini": ["Tbilisi Airport", "Lochini"],
}

# Coordinates were retrieved by entity id from Wikidata on 2026-08-30.
# Wikidata structured data is licensed CC0 1.0.
LANDMARKS = (
    ("Anapa", [], 44.894444444, 37.316666666, "Q15758"),
    ("Batumi", [], 41.645833333, 41.641666666, "Q25475"),
    ("Gori", [], 41.981686111, 44.112416666, "Q19583"),
    ("Gudauta", [], 43.100833333, 40.633055555, "Q242593"),
    ("Kobuleti", [], 41.811111111, 41.775277777, "Q328975"),
    ("Kutaisi", [], 42.271666666, 42.705555555, "Q172415"),
    ("Novorossiysk", [], 44.716666666, 37.766666666, "Q15760"),
    ("Poti", [], 42.14194, 41.67639, "Q185345"),
    ("Senaki", [], 42.268888888, 42.067777777, "Q320519"),
    ("Sochi", [], 43.585277777, 39.720277777, "Q39420"),
    ("Sukhumi", ["Sokhumi"], 43.003611111, 41.019166666, "Q40811"),
    ("Tbilisi", [], 41.7225, 44.7925, "Q994"),
    ("Zugdidi", [], 42.508055555, 41.8725, "Q185336"),
)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def pydcs_revision() -> str:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(REPOSITORY_ROOT / "third_party" / "pydcs"),
            "rev-parse",
            "HEAD",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def operational_landmark_patch() -> dict[str, Any]:
    value = json.loads(OPERATIONAL_LANDMARKS_PATH.read_text(encoding="utf-8"))
    expected_header = {
        "patch_schema_version": 1,
        "target_catalog": "caucasus",
        "target_catalog_schema_version": 1,
    }
    if any(value.get(key) != expected for key, expected in expected_header.items()):
        raise ValueError(
            f"operational landmark source has an unexpected target or schema: "
            f"{OPERATIONAL_LANDMARKS_PATH}"
        )
    if not isinstance(value.get("source_additions"), dict) or not value[
        "source_additions"
    ]:
        raise ValueError("operational landmark source additions must be non-empty")
    if not isinstance(value.get("location_additions"), list) or not value[
        "location_additions"
    ]:
        raise ValueError("operational landmark location additions must be non-empty")
    return value


def build_catalog() -> dict[str, Any]:
    from dcs.terrain.caucasus import Caucasus

    terrain = Caucasus()
    airports = []
    for name, airport in sorted(terrain.airports.items(), key=lambda item: item[1].id):
        coordinates = airport.position.latlng()
        airports.append(
            {
                "id": f"caucasus.airbase.{slug(name)}",
                "kind": "airbase",
                "name": name,
                "aliases": AIRBASE_ALIASES.get(name, []),
                "latitude_deg": round(coordinates.lat, 10),
                "longitude_deg": round(coordinates.lng, 10),
                "elevation_m": None,
                "metadata": {
                    "dcs_airport_id": airport.id,
                    "civilian": airport.civilian,
                    "runways": [
                        {
                            "id": runway.id,
                            "name": runway.name,
                            "ends": [
                                {
                                    "name": runway.main.name,
                                    "heading_deg": runway.main.heading,
                                },
                                {
                                    "name": runway.opposite.name,
                                    "heading_deg": runway.opposite.heading,
                                },
                            ],
                        }
                        for runway in airport.runways
                    ],
                },
                "source_id": "pydcs-airports",
            }
        )

    landmarks = [
        {
            "id": f"caucasus.city.{slug(name)}",
            "kind": "city",
            "name": name,
            "aliases": aliases,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "elevation_m": None,
            "metadata": {
                "wikidata_id": wikidata_id,
                "entity_url": f"https://www.wikidata.org/wiki/{wikidata_id}",
            },
            "source_id": "wikidata-cities",
        }
        for name, aliases, latitude, longitude, wikidata_id in LANDMARKS
    ]
    operational_patch = operational_landmark_patch()
    operational_sources = operational_patch["source_additions"]
    operational_landmarks = operational_patch["location_additions"]
    base_source_ids = {"pydcs-airports", "wikidata-cities"}
    source_collisions = base_source_ids.intersection(operational_sources)
    if source_collisions:
        raise ValueError(
            "operational landmark source ids collide with catalog sources: "
            + ", ".join(sorted(source_collisions))
        )
    base_location_ids = {item["id"] for item in airports + landmarks}
    operational_ids = [item.get("id") for item in operational_landmarks]
    duplicate_operational_ids = {
        identifier
        for identifier in operational_ids
        if operational_ids.count(identifier) > 1
    }
    location_collisions = base_location_ids.intersection(operational_ids)
    if duplicate_operational_ids or location_collisions:
        collisions = duplicate_operational_ids.union(location_collisions)
        raise ValueError(
            "operational landmark ids are duplicated or collide with the catalog: "
            + ", ".join(sorted(str(identifier) for identifier in collisions))
        )
    return {
        "schema_version": 1,
        "id": "caucasus",
        "name": "Caucasus",
        "aliases": ["CaucasusMap"],
        "data_version": DATA_VERSION,
        "sources": {
            "pydcs-airports": {
                "type": "pydcs",
                "revision": pydcs_revision(),
                "paths": [
                    "third_party/pydcs/dcs/terrain/caucasus/airports.py",
                    "third_party/pydcs/dcs/terrain/caucasus/projection.py",
                ],
                "method": (
                    "Airport local positions transformed to WGS84 by the pinned "
                    "Caucasus projection."
                ),
                "verified_at": "2026-08-30",
            },
            "wikidata-cities": {
                "type": "Wikidata",
                "url": "https://www.wikidata.org/",
                "query_endpoint": "https://query.wikidata.org/sparql",
                "license": "CC0-1.0",
                "retrieved_at": "2026-08-30",
                "method": "Curated entity ids queried for WGS84 coordinate property P625.",
            },
            **operational_sources,
        },
        "locations": airports + landmarks + operational_landmarks,
    }


def render_catalog() -> str:
    return json.dumps(build_catalog(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail if the checked catalog differs"
    )
    args = parser.parse_args()
    rendered = render_catalog()
    if args.check:
        if not OUTPUT_PATH.is_file() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            print(f"catalog is stale: {OUTPUT_PATH}")
            return 1
        print(f"catalog is current: {OUTPUT_PATH}")
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
