#!/usr/bin/env python3
"""Build the static aircraft/loadout catalog from the pinned pydcs checkout."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYDCS_ROOT = REPOSITORY_ROOT / "third_party" / "pydcs"
OUTPUT_PATH = REPOSITORY_ROOT / "tools" / "data" / "catalog" / "aircraft.json"
SCHEMA_VERSION = 1
GENERATOR_VERSION = "1"


def _pydcs_revision() -> str:
    completed = subprocess.run(
        ["git", "-C", str(PYDCS_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _store_catalog(weapons: type) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    stores = []
    by_clsid = {}
    for symbol, value in vars(weapons).items():
        if symbol.startswith("_") or not isinstance(value, dict):
            continue
        if set(value) != {"clsid", "name", "weight"}:
            raise ValueError(f"unexpected weapon shape for Weapons.{symbol}")
        clsid = value["clsid"]
        name = value["name"]
        weight = value["weight"]
        if not isinstance(clsid, str) or not clsid:
            raise ValueError(f"invalid CLSID for Weapons.{symbol}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"invalid name for Weapons.{symbol}")
        if weight is not None and (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(float(weight))
            or weight < 0
        ):
            raise ValueError(f"invalid weight for Weapons.{symbol}")
        if clsid in by_clsid:
            raise ValueError(f"duplicate pydcs weapon CLSID {clsid!r}")
        item = {
            "clsid": clsid,
            "name": name,
            "weight_kg": weight,
            "source_symbol": symbol,
        }
        stores.append(item)
        by_clsid[clsid] = value
    stores.sort(key=lambda item: (item["name"].casefold(), item["clsid"]))
    return stores, by_clsid


def _task_record(task_type: Any) -> dict[str, Any]:
    task_id = getattr(task_type, "id", None)
    name = getattr(task_type, "name", None)
    internal_name = getattr(task_type, "internal_name", None)
    if not isinstance(task_id, int) or not isinstance(name, str):
        raise ValueError(f"unexpected aircraft task definition {task_type!r}")
    if not isinstance(internal_name, str):
        raise ValueError(f"task {name!r} has no internal name")
    return {"id": task_id, "name": name, "internal_name": internal_name}


def _pylon_record(
    aircraft_id: str,
    aircraft_type: type,
    pylon_index: int,
    known_stores: dict[str, dict],
) -> dict[str, Any]:
    pylon_type = getattr(aircraft_type, f"Pylon{pylon_index}", None)
    if pylon_type is None:
        return {
            "index": pylon_index,
            "definition_available": False,
            "allowed_store_count": 0,
            "allowed_store_clsids": [],
        }

    clsids = []
    for symbol, value in vars(pylon_type).items():
        if symbol.startswith("_"):
            continue
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError(
                f"unexpected pylon entry {aircraft_id}.Pylon{pylon_index}.{symbol}"
            )
        declared_index, store = value
        if declared_index != pylon_index or not isinstance(store, dict):
            raise ValueError(
                f"invalid pylon entry {aircraft_id}.Pylon{pylon_index}.{symbol}"
            )
        clsid = store.get("clsid")
        if clsid not in known_stores:
            raise ValueError(
                f"unknown store in {aircraft_id}.Pylon{pylon_index}.{symbol}: {clsid!r}"
            )
        clsids.append(clsid)
    if len(clsids) != len(set(clsids)):
        raise ValueError(f"duplicate CLSID in {aircraft_id}.Pylon{pylon_index}")
    clsids.sort()
    return {
        "index": pylon_index,
        "definition_available": True,
        "allowed_store_count": len(clsids),
        "allowed_store_clsids": clsids,
    }


def _aircraft_record(
    aircraft_id: str,
    aircraft_type: type,
    kind: str,
    known_stores: dict[str, dict],
) -> dict[str, Any]:
    if aircraft_id != getattr(aircraft_type, "id", None):
        raise ValueError(f"aircraft map key/id mismatch for {aircraft_id!r}")
    pylon_indexes = sorted(getattr(aircraft_type, "pylons", set()))
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 1 for index in pylon_indexes):
        raise ValueError(f"invalid pylon index for {aircraft_id}")
    pylons = [
        _pylon_record(aircraft_id, aircraft_type, index, known_stores)
        for index in pylon_indexes
    ]
    tasks = sorted(
        (_task_record(task_type) for task_type in aircraft_type.tasks),
        key=lambda item: (item["name"].casefold(), item["id"]),
    )
    category = getattr(aircraft_type, "category", "Air")
    if not isinstance(category, str) or not category:
        raise ValueError(f"invalid category for {aircraft_id}")
    return {
        "type_id": aircraft_id,
        "display_name": aircraft_id,
        "kind": kind,
        "category": category,
        "flyable": bool(getattr(aircraft_type, "flyable", False)),
        "pylon_count": len(pylons),
        "known_tasks": tasks,
        "pylons": pylons,
    }


def build_catalog(generated_at: str | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(PYDCS_ROOT))
    try:
        from dcs import helicopters, planes
        from dcs.weapons_data import Weapons
    finally:
        try:
            sys.path.remove(str(PYDCS_ROOT))
        except ValueError:
            pass

    stores, known_stores = _store_catalog(Weapons)
    aircraft = []
    for kind, mapping in (
        ("fixed_wing", planes.plane_map),
        ("helicopter", helicopters.helicopter_map),
    ):
        aircraft.extend(
            _aircraft_record(aircraft_id, aircraft_type, kind, known_stores)
            for aircraft_id, aircraft_type in mapping.items()
        )
    aircraft.sort(key=lambda item: (item["display_name"].casefold(), item["type_id"]))

    missing_definitions = [
        {"aircraft": item["type_id"], "pylon": pylon["index"]}
        for item in aircraft
        for pylon in item["pylons"]
        if not pylon["definition_available"]
    ]
    compatibility_count = sum(
        pylon["allowed_store_count"]
        for item in aircraft
        for pylon in item["pylons"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": "pydcs-aircraft-loadouts",
        "generator": {
            "name": "tools/build_aircraft_catalog.py",
            "version": GENERATOR_VERSION,
            "generated_at": generated_at or _generated_at(),
        },
        "source": {
            "type": "pinned_pydcs",
            "revision": _pydcs_revision(),
            "paths": [
                "third_party/pydcs/dcs/planes.py",
                "third_party/pydcs/dcs/helicopters.py",
                "third_party/pydcs/dcs/unittype.py",
                "third_party/pydcs/dcs/weapons_data.py",
                "third_party/pydcs/dcs/task.py",
            ],
        },
        "preset_enrichment": {
            "included": False,
            "source": None,
            "reason": "base catalog contains pinned pydcs static definitions only",
        },
        "counts": {
            "aircraft": len(aircraft),
            "fixed_wing": sum(item["kind"] == "fixed_wing" for item in aircraft),
            "helicopter": sum(item["kind"] == "helicopter" for item in aircraft),
            "stores": len(stores),
            "pylon_compatibility_entries": compatibility_count,
            "declared_pylons_without_definition": len(missing_definitions),
        },
        "source_warnings": [
            {
                "code": "DECLARED_PYLON_WITHOUT_DEFINITION",
                "message": "pydcs declares this pylon index but exposes no matching PylonN definition",
                **item,
            }
            for item in missing_definitions
        ],
        "aircraft": aircraft,
        "stores": stores,
    }


def render_catalog(generated_at: str | None = None) -> str:
    return json.dumps(
        build_catalog(generated_at), ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail if the checked catalog differs"
    )
    args = parser.parse_args()
    if args.check:
        if not OUTPUT_PATH.is_file():
            print(f"catalog is stale: {OUTPUT_PATH}")
            return 1
        try:
            current = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            generated_at = current["generator"]["generated_at"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
            print(f"catalog is stale: {OUTPUT_PATH}")
            return 1
        if OUTPUT_PATH.read_text(encoding="utf-8") != render_catalog(generated_at):
            print(f"catalog is stale: {OUTPUT_PATH}")
            return 1
        print(f"catalog is current: {OUTPUT_PATH}")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_catalog(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
