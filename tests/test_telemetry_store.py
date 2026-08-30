from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.telemetry_memory import TelemetryMemory  # noqa: E402
from dcs_harness_runtime.telemetry_store import (  # noqa: E402
    TelemetryStoreCatalog,
)


def sample(unit_id: int, name: str | None = None) -> dict:
    return {
        "session_id": "100",
        "snapshot_id": 1,
        "mission_time": 0.0,
        "captured_at": "2026-08-30T00:00:00+00:00",
        "instance_id": None,
        "unit": {
            "id": unit_id,
            "name": name if name is not None else f"Unit {unit_id}",
            "type": "Su-25",
            "category": "AIRPLANE",
            "coalition": "RED",
            "country": "RUSSIA",
        },
        "group": {"id": 10, "name": "Flight"},
        "position": {"x_m": 1.0, "y_m": 2.0, "z_m": 3.0},
        "velocity": {"x_mps": 4.0, "y_mps": 5.0, "z_mps": 6.0},
        "heading_deg": 7.0,
        "ground_speed_mps": 8.0,
        "vertical_speed_mps": 5.0,
        "life": 10.0,
        "life_initial": 10.0,
        "fuel_fraction": 0.5,
        "in_air": True,
        "player_name": None,
    }


def snapshot(
    session_id: str,
    mission_time: float,
    units: list[dict],
    *,
    partial: bool = False,
) -> dict:
    for item in units:
        item["session_id"] = session_id
        item["mission_time"] = mission_time
    return {
        "session_id": session_id,
        "snapshot_id": 1,
        "mission_time": mission_time,
        "captured_at": "2026-08-30T00:00:00+00:00",
        "capture_duration_ms": 10.0,
        "unit_count": len(units),
        "partial": partial,
        "units": units,
    }


def memory() -> TelemetryMemory:
    return TelemetryMemory(
        retention_seconds=1800,
        max_snapshots=20,
        max_entities=100,
    )


class TelemetryStoreTests(unittest.TestCase):
    def test_schema_indexes_atomic_write_and_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            catalog = TelemetryStoreCatalog(root / "runtime" / "telemetry", root)
            store = catalog.select("100")
            first = memory().append(snapshot("100", 1, [sample(1), sample(2)]))
            first["units"][1]["unit"]["name"] = None
            first["units"][1]["fuel_fraction"] = None
            first["units"][1]["in_air"] = None
            with store.writer() as writer:
                writer.append(first)

            with closing(sqlite3.connect(store.path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }
                row = connection.execute(
                    """
                    SELECT unit_name, fuel_fraction, in_air
                    FROM unit_samples WHERE unit_id = 2
                    """
                ).fetchone()
            self.assertTrue({"snapshots", "unit_samples"}.issubset(tables))
            self.assertTrue(
                {
                    "unit_samples_snapshot_idx",
                    "unit_samples_instance_time_idx",
                    "unit_samples_unit_name_time_idx",
                    "unit_samples_group_name_time_idx",
                    "unit_samples_coalition_time_idx",
                }.issubset(indexes)
            )
            self.assertEqual(row, (None, None, None))
            self.assertEqual(store.count(), 1)

            invalid = memory().append(snapshot("100", 2, [sample(3)]))
            invalid["snapshot_id"] = 2
            invalid["units"][0]["snapshot_id"] = 2
            invalid["units"][0]["player_name"] = object()
            with store.writer() as writer, self.assertRaises(sqlite3.Error):
                writer.append(invalid)
            with closing(sqlite3.connect(store.path)) as connection:
                snapshot_count = connection.execute(
                    "SELECT COUNT(*) FROM snapshots"
                ).fetchone()[0]
                sample_count = connection.execute(
                    "SELECT COUNT(*) FROM unit_samples"
                ).fetchone()[0]
            self.assertEqual((snapshot_count, sample_count), (1, 2))

    def test_catalog_is_per_session_reopens_and_rejects_unsafe_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            telemetry_root = root / "runtime" / "telemetry"
            catalog = TelemetryStoreCatalog(telemetry_root, root)
            first = catalog.select("100")
            reopened = catalog.select("100")
            second = catalog.select("200")

            self.assertEqual(first.path, reopened.path)
            self.assertNotEqual(first.path, second.path)
            self.assertRegex(first.path.name, r"^\d{8}-\d{6}_100\.sqlite$")
            self.assertEqual(
                catalog.display_path(first),
                f"runtime/telemetry/{first.path.name}",
            )
            self.assertEqual(len(list(telemetry_root.glob("*.sqlite"))), 2)
            with self.assertRaises(ValueError):
                catalog.select("../unsafe")

    def test_resume_preserves_partial_absence_and_advances_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            catalog = TelemetryStoreCatalog(root / "runtime" / "telemetry", root)
            store = catalog.select("100")
            first_memory = memory()
            first = first_memory.append(
                snapshot("100", 1, [sample(1), sample(2)])
            )
            second = first_memory.append(
                snapshot("100", 2, [sample(1)], partial=True)
            )
            with store.writer() as writer:
                writer.append(first)
                writer.append(second)

            state = store.resume_state()
            self.assertEqual(state["next_snapshot_id"], 3)
            self.assertEqual(set(state["active_instances"]), {1, 2})

            resumed = memory()
            resumed.resume_session("100", **state)
            third = resumed.append(snapshot("100", 3, [sample(2)]))
            self.assertEqual(third["snapshot_id"], 3)
            self.assertEqual(third["units"][0]["instance_id"], "100:2:1")

            absent = resumed.append(snapshot("100", 4, []))
            reappeared = resumed.append(snapshot("100", 5, [sample(2)]))
            with store.writer() as writer:
                writer.append(third)
                writer.append(absent)
                writer.append(reappeared)
            final_state = store.resume_state()
            self.assertEqual(final_state["next_snapshot_id"], 6)
            self.assertEqual(
                final_state["active_instances"][2][1],
                "100:2:2",
            )


if __name__ == "__main__":
    unittest.main()
