from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.background import BackgroundTaskManager  # noqa: E402
from dcs_harness_runtime.logging_utils import LifecycleLogger  # noqa: E402
from dcs_harness_runtime.resident import (  # noqa: E402
    AUTOSTART_BUILTINS,
    CapabilityRuntime,
)
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from dcs_harness_runtime.telemetry_collector import (  # noqa: E402
    TelemetryCollector,
    TelemetryConfig,
)
from dcs_harness_runtime.telemetry_store import TelemetryStore  # noqa: E402


def sample(unit_id: int = 1) -> dict:
    return {
        "session_id": "100",
        "snapshot_id": 1,
        "mission_time": 0.0,
        "captured_at": "2026-08-30T00:00:00+00:00",
        "instance_id": None,
        "unit": {
            "id": unit_id,
            "name": f"Unit {unit_id}",
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


def snapshot(session_id: str, mission_time: float) -> dict:
    unit = sample()
    unit["session_id"] = session_id
    unit["mission_time"] = mission_time
    return {
        "session_id": session_id,
        "snapshot_id": 1,
        "mission_time": mission_time,
        "captured_at": f"2026-08-30T00:00:{int(mission_time):02d}+00:00",
        "capture_duration_ms": 10.0,
        "unit_count": 1,
        "observed_unit_count": 1,
        "groups_seen": 1,
        "inactive_count": 0,
        "source": "mission_lua_batch",
        "heading_reference": "dcs_local_x_north_z_east",
        "partial": False,
        "error_count": 0,
        "errors": [],
        "units": [unit],
    }


def config(*, persistence: bool = False, enabled: bool = True) -> TelemetryConfig:
    return TelemetryConfig(
        enabled=enabled,
        sample_interval_seconds=0.01,
        memory_retention_seconds=10,
        max_snapshots=100,
        max_entities=1000,
        persistence=persistence,
    )


def context(root: Path) -> Any:
    return SimpleNamespace(
        repository_root=root,
        runtime_root=root / "runtime",
    )


def wait_until(predicate: Any, message: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(message)


class PlannedSource:
    def __init__(self, plan: list[Any], *, delay: float = 0.0) -> None:
        self.plan = list(plan)
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def capture(self, *, snapshot_id: int) -> dict:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            index = self.calls - 1
        try:
            if self.delay:
                time.sleep(self.delay)
            value = self.plan[min(index, len(self.plan) - 1)]
            if isinstance(value, Exception):
                raise value
            result = dict(value)
            result["mission_time"] = value["mission_time"] + index
            result["units"] = [dict(item) for item in value["units"]]
            for item in result["units"]:
                item["mission_time"] = result["mission_time"]
            return result
        finally:
            with self._lock:
                self.active -= 1


class TelemetryCollectorTests(unittest.TestCase):
    def make_collector(
        self,
        root: Path,
        source: PlannedSource,
        *,
        persistence: bool = False,
    ) -> TelemetryCollector:
        collector = TelemetryCollector(
            context(root),
            LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl"),
            config(persistence=persistence),
        )
        collector.source = source
        return collector

    def test_transient_failure_recovers_and_slow_capture_does_not_overlap(self) -> None:
        failure = HarnessError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "DCS unavailable",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = PlannedSource([failure, snapshot("100", 1)], delay=0.03)
            collector = self.make_collector(root, source)
            stop_event = threading.Event()
            thread = threading.Thread(target=collector.run, args=(stop_event,))
            thread.start()
            wait_until(
                lambda: collector.status()["latest_snapshot_id"] is not None,
                "collector did not recover",
            )
            status = collector.status()
            stop_event.set()
            thread.join(3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(status["failed_captures"], 1)
            self.assertEqual(status["consecutive_failures"], 0)
            self.assertGreaterEqual(status["late_missed_samples"], 1)
            self.assertEqual(source.max_active, 1)
            self.assertEqual(collector.status()["collector"], "stopped")

    def test_persistence_rotates_session_and_memory_exposes_only_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = PlannedSource([snapshot("101", 1), snapshot("202", 1)])
            collector = self.make_collector(root, source, persistence=True)
            stop_event = threading.Event()
            thread = threading.Thread(target=collector.run, args=(stop_event,))
            thread.start()
            wait_until(
                lambda: (
                    (status := collector.status())["session_id"] == "202"
                    and status["session_rotations"] == 1
                    and status["persisted_count"] >= 1
                ),
                "collector did not rotate sessions",
            )
            status = collector.status()
            latest = collector.memory.latest({})
            stop_event.set()
            thread.join(3)

            stores = sorted((root / "runtime" / "telemetry").glob("*.sqlite"))
            self.assertEqual(len(stores), 2)
            self.assertEqual(status["session_rotations"], 1)
            self.assertTrue(status["store_path"].endswith("_202.sqlite"))
            self.assertGreaterEqual(status["persisted_count"], 1)
            self.assertEqual(latest["snapshot"]["session_id"], "202")
            self.assertGreaterEqual(latest["snapshot"]["snapshot_id"], 1)
            self.assertTrue(
                all(item["session_id"] == "202" for item in latest["units"])
            )
            counts = [TelemetryStore(path).count() for path in stores]
            self.assertTrue(all(count >= 1 for count in counts))

    def test_persistence_off_never_creates_telemetry_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            collector = self.make_collector(
                root,
                PlannedSource([snapshot("100", 1)]),
            )
            stop_event = threading.Event()
            thread = threading.Thread(target=collector.run, args=(stop_event,))
            thread.start()
            wait_until(
                lambda: collector.status()["latest_snapshot_id"] is not None,
                "collector did not capture",
            )
            stop_event.set()
            thread.join(3)

            self.assertFalse((root / "runtime" / "telemetry").exists())
            self.assertFalse(collector.status()["persistence_enabled"])
            self.assertIsNone(collector.status()["store_path"])

    def test_fatal_storage_failure_does_not_stop_other_task(self) -> None:
        class FailingWriter:
            def append(self, value: dict) -> None:
                raise sqlite3.OperationalError("disk unavailable")

        class FailingStore:
            path = Path("runtime/telemetry/failing.sqlite")

            def resume_state(self) -> dict:
                return {
                    "next_snapshot_id": 1,
                    "active_instances": {},
                    "generations": {},
                }

            @contextmanager
            def writer(self) -> Iterator[FailingWriter]:
                yield FailingWriter()

            def count(self) -> int:
                return 0

        class FailingCatalog:
            def select(self, session_id: str) -> FailingStore:
                return FailingStore()

            def display_path(self, store: FailingStore) -> str:
                return store.path.as_posix()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            logger = LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl")
            collector = self.make_collector(
                root,
                PlannedSource([snapshot("100", 1)]),
                persistence=True,
            )
            collector.stores = FailingCatalog()
            manager = BackgroundTaskManager(logger)
            manager.start("telemetry", "snapshot-loop", collector.run)
            manager.start("healthy", "waiter", lambda stop: stop.wait())
            wait_until(
                lambda: manager.status("telemetry")["snapshot-loop"]["state"]
                == "failed",
                "storage failure was not surfaced",
            )
            task = manager.status("telemetry")["snapshot-loop"]
            healthy = manager.status("healthy")["waiter"]
            status = collector.status()
            manager.signal_plugin("healthy")
            manager.join_plugin("healthy", 1)

            self.assertEqual(task["last_error"]["type"], "OperationalError")
            self.assertEqual(status["collector"], "failed")
            self.assertIsNone(status["session_id"])
            self.assertIsNone(status["last_successful_sample"])
            self.assertEqual(
                status["last_error"]["details"]["reason"],
                "TELEMETRY_STORAGE_FAILURE",
            )
            self.assertEqual(healthy["state"], "running")


class TelemetryPluginIntegrationTests(unittest.TestCase):
    @staticmethod
    def prepare_plugin(root: Path) -> None:
        builtin = root / "tools" / "src" / "py" / "plugins"
        builtin.mkdir(parents=True)
        (root / ".gitmodules").touch()
        shutil.copyfile(
            SOURCE_ROOT / "plugins" / "telemetry.py",
            builtin / "telemetry.py",
        )

    def test_offline_dcs_degrades_without_failing_resident_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.prepare_plugin(root)
            runtime = CapabilityRuntime(root, mode="resident")
            try:
                runtime.autostart(("telemetry",))
                wait_until(
                    lambda: runtime.dispatch("telemetry", "status").data[
                        "last_error"
                    ]
                    is not None,
                    "offline capture failure was not visible",
                )
                status = runtime.dispatch("telemetry", "status")
                rejected = runtime.dispatch(
                    "telemetry", "history", {"sql": "DROP TABLE snapshots"}
                )
                runtime_status = runtime.status()
            finally:
                runtime.close()

            self.assertTrue(status.ok)
            self.assertEqual(status.data["collector"], "degraded")
            self.assertEqual(
                status.data["background_task"]["state"],
                "running",
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(rejected.error.code, ErrorCode.INVALID_ARGUMENT.value)
            self.assertEqual(runtime_status["state"], "running")
            self.assertFalse((root / "runtime" / "telemetry").exists())

    def test_telemetry_is_an_explicit_autostart_builtin(self) -> None:
        self.assertIn("telemetry", AUTOSTART_BUILTINS)


if __name__ == "__main__":
    unittest.main()
