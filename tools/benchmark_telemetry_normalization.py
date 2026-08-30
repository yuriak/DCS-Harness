#!/usr/bin/env python3
"""Repeatable synthetic JSON-boundary and telemetry-normalization smoke."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.telemetry_capture import normalize_snapshot  # noqa: E402


SIZES = (100, 300, 500)
REPEATS = 7


def raw_unit(index: int) -> dict[str, Any]:
    category = index % 4
    return {
        "unit_id": index + 1,
        "unit_name": f"Synthetic Unit {index + 1}",
        "unit_type": ("Su-25", "Mi-8MT", "T-55", "MOSCOW")[category],
        "unit_country": "RUSSIA",
        "group_id": index // 4 + 1,
        "group_name": f"Synthetic Group {index // 4 + 1}",
        "group_category": category,
        "coalition": 1 if index % 2 else 2,
        "position": {"x": index * 10, "y": 1000, "z": index * -5},
        "forward": {"x": 1, "y": 0, "z": 0},
        "velocity": {"x": 100, "y": 0, "z": 10},
        "life": 100,
        "life_initial": 100,
        "fuel_fraction": 0.75 if category in {0, 1} else None,
        "in_air": category in {0, 1},
    }


def raw_snapshot(size: int) -> dict[str, Any]:
    return {
        "source": "mission_lua_batch",
        "mission_time": 100,
        "coalitions_enumerated": 3,
        "groups_seen": (size + 3) // 4,
        "inactive_count": 0,
        "error_count": 0,
        "errors": [],
        "units": [raw_unit(index) for index in range(size)],
        "unit_count": size,
        "partial": False,
    }


def main() -> int:
    results = []
    for size in SIZES:
        raw = raw_snapshot(size)
        encoded = json.dumps(raw, separators=(",", ":"))
        timings = []
        for _ in range(REPEATS):
            started = time.perf_counter()
            decoded = json.loads(encoded)
            snapshot = normalize_snapshot(
                decoded,
                session_id="synthetic",
                snapshot_id=1,
                captured_at="2026-08-30T00:00:00+00:00",
                capture_duration_ms=0,
            )
            timings.append((time.perf_counter() - started) * 1000)
        results.append(
            {
                "unit_count": size,
                "payload_bytes": len(encoded.encode("utf-8")),
                "returned_count": snapshot["unit_count"],
                "json_parse_and_normalize_ms": {
                    "min": min(timings),
                    "median": statistics.median(timings),
                    "max": max(timings),
                },
            }
        )
    print(
        json.dumps(
            {
                "benchmark": "synthetic_json_parse_and_normalization",
                "repeats": REPEATS,
                "results": results,
                "does_not_measure": [
                    "DCS mission getter cost",
                    "DCS-gRPC Eval serialization or transport",
                    "simulator frame impact",
                ],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
