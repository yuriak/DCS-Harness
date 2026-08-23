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
from dcs_harness_runtime.event_store import EventStore  # noqa: E402
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
                [item["session_id"] for item in reopened.query(session_id="one")],
                ["one", "one"],
            )
            self.assertEqual(
                [item["mission_time"] for item in reopened.query(since=1.5, until=2.5)],
                [2.0],
            )
            for invalid in (0, 1.5, 501, True, "bad"):
                with self.subTest(limit=invalid), self.assertRaises(HarnessError):
                    reopened.query(limit=invalid)

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


class EventCollectorTests(unittest.TestCase):
    def test_persist_reconnect_malformed_and_query(self) -> None:
        import grpc

        class UnavailableError(FakeRpcError, grpc.RpcError):
            pass

        first, second, malformed, third = event_messages()
        stream_one = PlannedStream(
            [first, malformed, second],
            terminal_error=UnavailableError(grpc.StatusCode.UNAVAILABLE, "restart"),
        )
        stream_two = PlannedStream([third], block_after_events=True)
        channel = FakeMissionChannel([101, 202], [stream_one, stream_two])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            context = ready_context(root, channel)
            store = EventStore(root / "runtime" / "events.sqlite")
            store.initialize()
            logger = LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl")
            collector = EventCollector(
                context, store, logger, initial_backoff=0.01, max_backoff=0.02
            )
            stop_event = threading.Event()
            thread = threading.Thread(target=collector.run, args=(stop_event,))
            thread.start()
            deadline = time.monotonic() + 3
            while store.count() < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(store.count(), 3)

            status = collector.status()
            self.assertEqual(status["collector"], "running")
            self.assertEqual(status["stream"], "connected")
            self.assertEqual(status["session_id"], "202")
            self.assertEqual(status["malformed_events"], 1)
            self.assertGreaterEqual(status["reconnects"], 1)
            self.assertEqual(
                [item["event_type"] for item in store.query(limit=3)],
                ["mission_end", "score", "mission_start"],
            )
            self.assertEqual(
                [item["event_type"] for item in store.query(session_id="101")],
                ["score", "mission_start"],
            )

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
            @contextmanager
            def writer(self) -> Iterator[Any]:
                class Writer:
                    def append(self, **kwargs: Any) -> None:
                        raise sqlite3.OperationalError("disk unavailable")

                yield Writer()

            def count(self) -> int:
                return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            context = ready_context(root, channel)
            logger = LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl")
            collector = EventCollector(context, FailingStore(), logger)
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
            database = root / "runtime" / "events.sqlite"
            store = EventStore(database)
            while (not database.exists() or store.count() < 1) and time.monotonic() < deadline:
                time.sleep(0.01)

            status = runtime.dispatch("events", "status")
            recent = runtime.dispatch("events", "recent", {"limit": 1})
            query = runtime.dispatch(
                "events", "query", {"session_id": "77", "event_type": "mission_start"}
            )
            self.assertTrue(status.ok)
            self.assertEqual(status.data["stored_events"], 1)
            self.assertEqual(recent.data["events"][0]["event_type"], "mission_start")
            self.assertEqual(query.data["count"], 1)
            self.assertEqual(
                len(runtime.background.status("events")), 1
            )

            rejected = runtime.dispatch("events", "query", {"sql": "DROP TABLE events"})
            self.assertFalse(rejected.ok)
            self.assertEqual(rejected.error.code, ErrorCode.INVALID_ARGUMENT.value)

            runtime.close()
            self.assertTrue(channel.closed)
            self.assertEqual(runtime.status()["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
