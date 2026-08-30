from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from dcs_harness_runtime.telemetry_collector import TelemetryConfig  # noqa: E402
from dcs_harness_runtime.telemetry_memory import TelemetryMemory  # noqa: E402


def sample(unit_id: int, name: str | None = None) -> dict:
    return {
        "session_id": "one",
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


def snapshot(session: str, mission_time: float, units: list[dict], *, partial: bool = False) -> dict:
    for item in units:
        item["session_id"] = session
        item["mission_time"] = mission_time
    return {
        "session_id": session,
        "snapshot_id": 1,
        "mission_time": mission_time,
        "captured_at": "2026-08-30T00:00:00+00:00",
        "capture_duration_ms": 10.0,
        "unit_count": len(units),
        "observed_unit_count": len(units),
        "groups_seen": 1,
        "inactive_count": 0,
        "source": "mission_lua_batch",
        "heading_reference": "dcs_local_x_north_z_east",
        "partial": partial,
        "error_count": 1 if partial else 0,
        "errors": [],
        "units": units,
    }


def memory() -> TelemetryMemory:
    return TelemetryMemory(
        retention_seconds=1800,
        max_snapshots=10,
        max_entities=100,
    )


class TelemetryMemoryTests(unittest.TestCase):
    def test_identity_continuity_partial_absence_and_respawn_generation(self) -> None:
        store = memory()
        first = store.append(snapshot("one", 1, [sample(1)]))
        first_id = first["units"][0]["instance_id"]
        store.append(snapshot("one", 2, [], partial=True))
        third = store.append(snapshot("one", 3, [sample(1)]))
        self.assertEqual(third["units"][0]["instance_id"], first_id)

        store.append(snapshot("one", 4, []))
        respawn = store.append(snapshot("one", 5, [sample(1)]))
        self.assertEqual(respawn["units"][0]["instance_id"], "one:1:2")

    def test_same_name_different_id_and_session_rotation(self) -> None:
        store = memory()
        value = store.append(snapshot("one", 1, [sample(1, "Same"), sample(2, "Same")]))
        self.assertNotEqual(
            value["units"][0]["instance_id"], value["units"][1]["instance_id"]
        )
        rotated = store.append(snapshot("two", 1, [sample(1, "Same")]))
        self.assertEqual(rotated["snapshot_id"], 1)
        self.assertEqual(store.status()["snapshots_in_memory"], 1)
        self.assertTrue(rotated["units"][0]["instance_id"].startswith("two:1:"))

    def test_latest_list_snapshot_history_fields_and_downsample(self) -> None:
        store = memory()
        first = store.append(snapshot("one", 10, [sample(1), sample(2)]))
        store.append(snapshot("one", 15, [sample(1), sample(2)]))
        store.append(snapshot("one", 20, [sample(1), sample(2)]))

        latest = store.latest({"unit": "Unit 1", "fields": ["position"], "limit": 5})
        self.assertEqual(latest["returned_count"], 1)
        self.assertEqual(set(latest["units"][0]), {"position"})
        identities = store.list_units({"coalition": "red", "limit": 1})
        self.assertTrue(identities["truncated"])
        selected = store.snapshot({"mission_time": 14.0, "limit": 5})
        self.assertEqual(selected["snapshot"]["snapshot_id"], 2)
        history = store.history(
            {
                "instance_id": first["units"][0]["instance_id"],
                "step": 2,
                "fields": ["mission_time", "position"],
                "limit": 10,
            }
        )
        self.assertEqual([item["mission_time"] for item in history["samples"]], [10, 20])

    def test_queries_require_current_data_and_narrow_history_target(self) -> None:
        store = memory()
        for operation in (
            lambda: store.latest({}),
            lambda: store.history({"limit": 1}),
            lambda: store.history({"unit": "u", "group": "g"}),
        ):
            with self.subTest(operation=operation), self.assertRaises(HarnessError):
                operation()

    def test_retention_snapshot_and_entity_bounds_evict_oldest(self) -> None:
        retained = TelemetryMemory(
            retention_seconds=10,
            max_snapshots=2,
            max_entities=3,
        )
        with patch(
            "dcs_harness_runtime.telemetry_memory.time.monotonic",
            side_effect=[0.0, 5.0, 20.0],
        ):
            retained.append(snapshot("one", 1, [sample(1), sample(2)]))
            retained.append(snapshot("one", 2, [sample(1)]))
            retained.append(snapshot("one", 3, [sample(1)]))

        status = retained.status()
        self.assertEqual(status["snapshots_in_memory"], 1)
        self.assertEqual(status["samples_in_memory"], 1)
        self.assertEqual(status["latest_snapshot_id"], 3)
        with self.assertRaises(HarnessError):
            retained.snapshot({"snapshot_id": 1})

        safeguard = TelemetryMemory(
            retention_seconds=10,
            max_snapshots=2,
            max_entities=1,
        )
        with self.assertRaises(HarnessError) as raised:
            safeguard.append(snapshot("one", 1, [sample(1), sample(2)]))
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)
        self.assertIsNone(safeguard.status()["session_id"])

    def test_configuration_defaults_and_bounds(self) -> None:
        config = TelemetryConfig.from_environment({})
        self.assertTrue(config.enabled)
        self.assertEqual(config.sample_interval_seconds, 5.0)
        self.assertFalse(config.persistence)
        self.assertTrue(
            TelemetryConfig.from_environment(
                {"telemetry": {"persistence": True}}
            ).persistence
        )
        for raw in (
            {"telemetry": {"sample_interval_seconds": 0.5}},
        ):
            with self.subTest(raw=raw), self.assertRaises(HarnessError) as raised:
                TelemetryConfig.from_environment(raw)
            self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
