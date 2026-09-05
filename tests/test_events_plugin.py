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
from typing import Any, Iterator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
GENERATED_ROOT = REPOSITORY_ROOT / "runtime" / "generated" / "grpc"
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(GENERATED_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.event_collector import EventCollector  # noqa: E402
from dcs_harness_runtime.event_store import (  # noqa: E402
    EventStore,
    EventStoreCatalog,
)
from dcs_harness_runtime.logging_utils import LifecycleLogger  # noqa: E402
from dcs_harness_runtime.resident import CapabilityRuntime  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402


SESSION_PATH = "/dcs.mission.v0.MissionService/GetSessionId"
EVENT_PATH = "/dcs.mission.v0.MissionService/StreamEvents"


class FakeRpcError(Exception):
    def __init__(self, status: Any, details: str) -> None:
        super().__init__(details)
        self._status = status
        self._details = details

    def code(self) -> Any:
        return self._status

    def details(self) -> str:
        return self._details


class PlannedStream:
    def __init__(
        self,
        events: list[Any],
        *,
        terminal_error: Exception | None = None,
        block_after_events: bool = False,
    ) -> None:
        self.events = iter(events)
        self.terminal_error = terminal_error
        self.block_after_events = block_after_events
        self.cancelled = threading.Event()

    def __iter__(self) -> "PlannedStream":
        return self

    def __next__(self) -> Any:
        try:
            return next(self.events)
        except StopIteration:
            pass
        if self.block_after_events:
            self.cancelled.wait(5)
        if self.terminal_error is not None:
            raise self.terminal_error
        raise StopIteration

    def cancel(self) -> bool:
        self.cancelled.set()
        return True


class FakeMissionChannel:
    def __init__(self, sessions: list[int], streams: list[PlannedStream]) -> None:
        self.sessions = iter(sessions)
        self.streams = iter(streams)
        self.calls: list[str] = []
        self.closed = False

    def unary_unary(
        self,
        path: str,
        request_serializer: Any,
        response_deserializer: Any,
        **kwargs: Any,
    ) -> Any:
        def call(request: Any, **call_kwargs: Any) -> Any:
            self.calls.append(path)
            if path != SESSION_PATH:
                raise AssertionError(f"Unexpected unary RPC: {path}")
            from dcs_grpc.dcs.mission.v0 import mission_pb2

            response = mission_pb2.GetSessionIdResponse(
                session_id=next(self.sessions)
            )
            return response_deserializer(response.SerializeToString())

        return call

    def unary_stream(
        self,
        path: str,
        request_serializer: Any,
        response_deserializer: Any,
        **kwargs: Any,
    ) -> Any:
        def call(request: Any, **call_kwargs: Any) -> PlannedStream:
            self.calls.append(path)
            if path != EVENT_PATH:
                raise AssertionError(f"Unexpected stream RPC: {path}")
            return next(self.streams)

        return call

    def stream_unary(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: None

    def stream_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def close(self) -> None:
        self.closed = True


def event_messages() -> tuple[Any, Any, Any, Any]:
    from dcs_grpc.dcs.mission.v0 import mission_pb2

    return (
        mission_pb2.StreamEventsResponse(
            time=1.0,
            mission_start=mission_pb2.StreamEventsResponse.MissionStartEvent(),
        ),
        mission_pb2.StreamEventsResponse(
            time=2.0,
            score=mission_pb2.StreamEventsResponse.ScoreEvent(),
        ),
        mission_pb2.StreamEventsResponse(time=2.5),
        mission_pb2.StreamEventsResponse(
            time=3.0,
            mission_end=mission_pb2.StreamEventsResponse.MissionEndEvent(),
        ),
    )


def simulation_fps_event(time_value: float = 1.5) -> Any:
    from dcs_grpc.dcs.mission.v0 import mission_pb2

    return mission_pb2.StreamEventsResponse(
        time=time_value,
        simulation_fps=mission_pb2.StreamEventsResponse.SimulationFpsEvent(
            average=60.0
        ),
    )


def ready_context(root: Path, channel: FakeMissionChannel) -> Context:
    context = Context(
        repository_root=root,
        environment_path=root / "config" / "environment.yaml",
        environment={
            "setup": {"status": "READY"},
            "grpc": {"client_host": "127.0.0.1", "port": 50051},
        },
        runtime_root=root / "runtime",
        generated_root=REPOSITORY_ROOT / "runtime" / "generated",
    )
    context._grpc_channel = channel
    context.ensure_generated_import_path()
    return context


class EventStoreTests(unittest.TestCase):
    def test_persistence_filters_and_bounded_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.sqlite"
            store = EventStore(path)
            store.initialize()
            with store.writer() as writer:
                writer.append(
                    session_id="one",
                    mission_time=1,
                    event_type="birth",
                    payload={"time": 1, "birth": {}},
                )
                writer.append(
                    session_id="one",
                    mission_time=2,
                    event_type="shot",
                    payload={"time": 2, "shot": {}},
                )
                writer.append(
                    session_id="two",
                    mission_time=3,
                    event_type="birth",
                    payload={"time": 3, "birth": {}},
                )

            reopened = EventStore(path)
            reopened.initialize()
            self.assertEqual(reopened.count(), 3)
            self.assertEqual(
                [item["mission_time"] for item in reopened.query(limit=2)],
                [3.0, 2.0],
            )
            self.assertEqual(
                [item["event_type"] for item in reopened.query(event_type="shot")],
                ["shot"],
            )
            self.assertEqual(
                [item["mission_time"] for item in reopened.query(since=1.5, until=2.5)],
                [2.0],
            )
            for invalid in (0, 1.5, 501, True, "bad"):
                with self.subTest(limit=invalid), self.assertRaises(HarnessError):
                    reopened.query(limit=invalid)

    def test_query_after_id_pages_oldest_first_without_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.sqlite")
            store.initialize()
            with store.writer() as writer:
                for index, event_type in enumerate(
                    ("birth", "mission_command", "group_command", "mission_command"),
                    start=1,
                ):
                    writer.append(
                        session_id="101",
                        mission_time=index,
                        event_type=event_type,
                        payload={"time": index, event_type: {}},
                    )

            first = store.query_after_id(
                event_types=["mission_command", "group_command"],
                after_id=0,
                limit=2,
            )
            second = store.query_after_id(
                event_types=["mission_command", "group_command"],
                after_id=first[-1]["id"],
                limit=2,
            )

        self.assertEqual([value["mission_time"] for value in first], [2.0, 3.0])
        self.assertEqual([value["mission_time"] for value in second], [4.0])

    def test_background_writer_and_concurrent_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.sqlite")
            store.initialize()
            failures: list[Exception] = []

            def write_events() -> None:
                try:
                    with store.writer() as writer:
                        for index in range(100):
                            writer.append(
                                session_id="concurrent",
                                mission_time=index,
                                event_type="tick",
                                payload={"time": index, "tick": {}},
                            )
                except Exception as error:
                    failures.append(error)

            thread = threading.Thread(target=write_events)
            thread.start()
            while thread.is_alive():
                store.query(limit=10)
            thread.join()
            self.assertEqual(failures, [])
            self.assertEqual(store.count(), 100)


class EventStoreCatalogTests(unittest.TestCase):
    def test_session_ledgers_are_isolated_reused_and_legacy_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runtime_root = root / "runtime"
            runtime_root.mkdir()
            legacy = runtime_root / "events.sqlite"
            legacy.write_bytes(b"legacy-ledger")
            catalog = EventStoreCatalog(runtime_root / "events", root)

            first = catalog.select("100")
            with first.writer() as writer:
                writer.append(
                    session_id="100",
                    mission_time=1,
                    event_type="mission_start",
                    payload={"time": 1, "mission_start": {}},
                )
            reopened = catalog.select("100")
            second = catalog.select("200")

            self.assertEqual(reopened.path, first.path)
            self.assertNotEqual(second.path, first.path)
            self.assertEqual(reopened.count(), 1)
            self.assertEqual(second.count(), 0)
            self.assertRegex(first.path.name, r"^\d{8}-\d{6}_100\.sqlite$")
            self.assertEqual(
                catalog.display_path(first), f"runtime/events/{first.path.name}"
            )
            self.assertEqual(legacy.read_bytes(), b"legacy-ledger")
            self.assertEqual(len(list((runtime_root / "events").glob("*.sqlite"))), 2)

            with self.assertRaises(ValueError):
                catalog.select("../unsafe")


class EventCollectorTests(unittest.TestCase):
    def test_persist_reconnect_malformed_and_query(self) -> None:
        import grpc

        class UnavailableError(FakeRpcError, grpc.RpcError):
            pass

        first, second, malformed, third = event_messages()
        stream_one = PlannedStream(
            [first, simulation_fps_event(), malformed, second],
            terminal_error=UnavailableError(grpc.StatusCode.UNAVAILABLE, "restart"),
        )
        stream_two = PlannedStream([third], block_after_events=True)
        channel = FakeMissionChannel([101, 202], [stream_one, stream_two])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            context = ready_context(root, channel)
            stores = EventStoreCatalog(root / "runtime" / "events", root)
            logger = LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl")
            collector = EventCollector(
                context, stores, logger, initial_backoff=0.01, max_backoff=0.02
            )
            stop_event = threading.Event()
            thread = threading.Thread(target=collector.run, args=(stop_event,))
            thread.start()
            deadline = time.monotonic() + 3
            while (
                collector.status()["session_id"] != "202"
                or collector.status()["stored_events"] < 1
            ) and time.monotonic() < deadline:
                time.sleep(0.01)

            status = collector.status()
            self.assertEqual(status["collector"], "running")
            self.assertEqual(status["stream"], "connected")
            self.assertEqual(status["session_id"], "202")
            self.assertEqual(status["malformed_events"], 1)
            self.assertEqual(status["ignored_events"], 1)
            self.assertGreaterEqual(status["reconnects"], 1)
            self.assertEqual(status["stored_events"], 1)
            self.assertRegex(
                status["store_path"],
                r"^runtime/events/\d{8}-\d{6}_202\.sqlite$",
            )
            self.assertEqual(
                [item["event_type"] for item in collector.current_store().query()],
                ["mission_end"],
            )
            first_store = stores.select("101")
            self.assertEqual(
                [item["event_type"] for item in first_store.query()],
                ["score", "mission_start"],
            )
            self.assertNotIn(
                "simulation_fps",
                [item["event_type"] for item in first_store.query()],
            )
            self.assertEqual(len(list(stores.root.glob("*.sqlite"))), 2)

            stop_event.set()
            collector.cancel()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(collector.status()["collector"], "stopped")
            self.assertTrue(channel.closed is False)
            context.close()

    def test_fatal_database_error_marks_background_failure(self) -> None:
        first, _, _, _ = event_messages()
        stream = PlannedStream([first], block_after_events=True)
        channel = FakeMissionChannel([1], [stream])

        class FailingStore:
            path = Path("runtime/events/failing.sqlite")

            @contextmanager
            def writer(self) -> Iterator[Any]:
                class Writer:
                    def append(self, **kwargs: Any) -> None:
                        raise sqlite3.OperationalError("disk unavailable")

                yield Writer()

            def count(self) -> int:
                return 0

        class FailingCatalog:
            def select(self, session_id: str) -> FailingStore:
                return FailingStore()

            def display_path(self, store: FailingStore) -> str:
                return store.path.as_posix()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            context = ready_context(root, channel)
            logger = LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl")
            collector = EventCollector(context, FailingCatalog(), logger)
            from dcs_harness_runtime.background import BackgroundTaskManager

            manager = BackgroundTaskManager(logger)
            manager.start("events", "event-stream", collector.run)
            deadline = time.monotonic() + 3
            while (
                manager.status("events")["event-stream"]["state"] != "failed"
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            task = manager.status("events")["event-stream"]
            self.assertEqual(task["state"], "failed")
            self.assertEqual(collector.status()["collector"], "failed")
            context.close()


class EventsPluginIntegrationTests(unittest.TestCase):
    @staticmethod
    def prepare_events_plugin(root: Path) -> None:
        builtin = root / "tools" / "src" / "py" / "plugins"
        builtin.mkdir(parents=True)
        (root / ".gitmodules").touch()
        shutil.copyfile(
            SOURCE_ROOT / "plugins" / "events.py", builtin / "events.py"
        )

    def test_offline_dcs_does_not_fail_resident_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.prepare_events_plugin(root)
            runtime = CapabilityRuntime(root, mode="resident")
            runtime.autostart(("events",))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = runtime.dispatch("events", "status")
                if result.data and result.data["last_error"]:
                    break
                time.sleep(0.01)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["collector"], "running")
            self.assertEqual(result.data["stream"], "disconnected")
            self.assertIsNotNone(result.data["last_error"])
            self.assertIsNone(result.data["store_path"])
            self.assertEqual(result.data["ignored_events"], 0)
            self.assertEqual(runtime.status()["state"], "running")
            runtime.close()

    def test_resident_autostart_commands_and_shutdown(self) -> None:
        first, _, _, _ = event_messages()
        stream = PlannedStream([first], block_after_events=True)
        channel = FakeMissionChannel([77], [stream])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.prepare_events_plugin(root)

            runtime = CapabilityRuntime(root, mode="resident")
            runtime.context.environment = {
                "setup": {"status": "READY"},
                "grpc": {"client_host": "127.0.0.1", "port": 50051},
            }
            runtime.context.generated_root = REPOSITORY_ROOT / "runtime" / "generated"
            runtime.context._grpc_channel = channel
            runtime.autostart(("events",))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = runtime.dispatch("events", "status")
                if status.data and status.data["stored_events"] >= 1:
                    break
                time.sleep(0.01)

            recent = runtime.dispatch("events", "recent", {"limit": 1})
            query = runtime.dispatch(
                "events", "query", {"event_type": "mission_start"}
            )
            combat = runtime.dispatch("events", "combat", {"limit": 5})
            self.assertTrue(status.ok)
            self.assertEqual(status.data["stored_events"], 1)
            self.assertEqual(status.data["session_id"], "77")
            self.assertTrue(status.data["store_path"].endswith("_77.sqlite"))
            self.assertEqual(recent.data["events"][0]["event_type"], "mission_start")
            self.assertEqual(query.data["count"], 1)
            self.assertTrue(combat.ok)
            self.assertEqual(combat.data["count"], 0)
            self.assertIn("not a confirmed kill", combat.data["attribution_note"])
            self.assertEqual(len(runtime.background.status("events")), 2)

            rejected = runtime.dispatch("events", "query", {"sql": "DROP TABLE events"})
            self.assertFalse(rejected.ok)
            self.assertEqual(rejected.error.code, ErrorCode.INVALID_ARGUMENT.value)
            historical = runtime.dispatch(
                "events", "query", {"session_id": "77"}
            )
            self.assertFalse(historical.ok)
            self.assertEqual(
                historical.error.code, ErrorCode.INVALID_ARGUMENT.value
            )

            runtime.close()
            self.assertTrue(channel.closed)
            self.assertEqual(runtime.status()["state"], "stopped")

    def test_harness_restart_reopens_same_session_ledger(self) -> None:
        first, second, _, _ = event_messages()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.prepare_events_plugin(root)

            def start_runtime(
                event: Any,
            ) -> tuple[CapabilityRuntime, FakeMissionChannel]:
                channel = FakeMissionChannel(
                    [88], [PlannedStream([event], block_after_events=True)]
                )
                runtime = CapabilityRuntime(root, mode="resident")
                runtime.context.environment = {
                    "setup": {"status": "READY"},
                    "grpc": {"client_host": "127.0.0.1", "port": 50051},
                }
                runtime.context.generated_root = (
                    REPOSITORY_ROOT / "runtime" / "generated"
                )
                runtime.context._grpc_channel = channel
                runtime.autostart(("events",))
                return runtime, channel

            first_runtime, first_channel = start_runtime(first)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                first_status = first_runtime.dispatch("events", "status")
                if first_status.data and first_status.data["stored_events"] == 1:
                    break
                time.sleep(0.01)
            first_path = first_status.data["store_path"]
            first_runtime.close()
            self.assertTrue(first_channel.closed)

            second_runtime, second_channel = start_runtime(second)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                second_status = second_runtime.dispatch("events", "status")
                if second_status.data and second_status.data["stored_events"] == 2:
                    break
                time.sleep(0.01)

            self.assertEqual(second_status.data["store_path"], first_path)
            self.assertEqual(second_status.data["stored_events"], 2)
            self.assertEqual(
                [
                    item["event_type"]
                    for item in second_runtime.dispatch(
                        "events", "recent", {"limit": 2}
                    ).data["events"]
                ],
                ["score", "mission_start"],
            )
            self.assertEqual(
                len(list((root / "runtime" / "events").glob("*.sqlite"))), 1
            )

            second_runtime.close()
            self.assertTrue(second_channel.closed)


if __name__ == "__main__":
    unittest.main()
