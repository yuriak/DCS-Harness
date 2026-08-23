from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.resident import CapabilityRuntime  # noqa: E402
from dcs_harness_runtime.result import ErrorCode  # noqa: E402


STATELESS_PLUGIN = """\
PLUGIN_NAME = "stateless"
PLUGIN_API_VERSION = 1

def invoke(context, command, args):
    return {"value": "ok"}
"""


def resident_plugin(start_marker: Path, stop_marker: Path) -> str:
    return f'''\
from pathlib import Path

PLUGIN_NAME = "resident"
PLUGIN_API_VERSION = 1
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True

START_MARKER = Path({str(start_marker)!r})
STOP_MARKER = Path({str(stop_marker)!r})

def worker(stop_event):
    stop_event.wait()

def start(context, runtime):
    starts = int(START_MARKER.read_text()) + 1 if START_MARKER.exists() else 1
    START_MARKER.write_text(str(starts))
    runtime.state = {{"starts": starts}}
    runtime.start_background("worker", worker)

def invoke(context, command, args):
    runtime = context.runtime.plugin_handle(PLUGIN_NAME)
    return {{"starts": runtime.state["starts"], "tasks": runtime.task_status()}}

def stop(context, runtime):
    STOP_MARKER.write_text("stopped")
'''


FAILED_BACKGROUND_PLUGIN = """\
PLUGIN_NAME = "failing"
PLUGIN_API_VERSION = 1
PLUGIN_RUNTIME = "resident"

def fail(stop_event):
    raise RuntimeError("background boom")

def start(context, runtime):
    runtime.start_background("failure", fail)

def invoke(context, command, args):
    return {"runtime_alive": True}
"""


class RuntimeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.builtin = root / "tools" / "src" / "py" / "plugins"
        self.runtime = root / "runtime" / "plugins" / "py"
        self.builtin.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        (root / ".gitmodules").touch()

    def write_builtin(self, name: str, content: str) -> Path:
        path = self.builtin / f"{name}.py"
        path.write_text(content, encoding="utf-8")
        return path

    def write_runtime(self, name: str, content: str) -> Path:
        path = self.runtime / f"{name}.py"
        path.write_text(content, encoding="utf-8")
        return path


class ResidentLifecycleTests(unittest.TestCase):
    def test_resident_start_once_and_graceful_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            start_marker = repository.root / "started"
            stop_marker = repository.root / "stopped"
            repository.write_builtin(
                "resident", resident_plugin(start_marker, stop_marker)
            )
            runtime = CapabilityRuntime(repository.root, mode="resident")
            try:
                first = runtime.dispatch("resident", "status")
                second = runtime.dispatch("resident", "status")
                status = runtime.status()
            finally:
                runtime.close()

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertEqual(start_marker.read_text(), "1")
            self.assertEqual(first.data["starts"], 1)
            self.assertEqual(len(first.data["tasks"]), 1)
            self.assertEqual(status["plugins"]["resident"]["state"], "running")
            self.assertEqual(stop_marker.read_text(), "stopped")
            self.assertEqual(runtime.status()["state"], "stopped")
            self.assertEqual(
                runtime.status()["plugins"]["resident"]["background_tasks"][
                    "worker"
                ]["state"],
                "stopped",
            )

    def test_autostart_starts_only_explicit_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            start_marker = repository.root / "started"
            stop_marker = repository.root / "stopped"
            repository.write_builtin(
                "resident", resident_plugin(start_marker, stop_marker)
            )
            repository.write_builtin("stateless", STATELESS_PLUGIN)

            with CapabilityRuntime(repository.root, mode="resident") as runtime:
                runtime.autostart(("resident",))
                status = runtime.status()

            self.assertEqual(start_marker.read_text(), "1")
            self.assertIn("resident", status["plugins"])
            self.assertNotIn("stateless", status["plugins"])

    def test_direct_runtime_rejects_resident_plugin_without_starting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            start_marker = repository.root / "started"
            repository.write_builtin(
                "resident",
                resident_plugin(start_marker, repository.root / "stopped"),
            )

            with CapabilityRuntime(repository.root, mode="direct") as runtime:
                result = runtime.dispatch("resident", "status")

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, ErrorCode.CAPABILITY_UNAVAILABLE.value)
            self.assertFalse(start_marker.exists())

    def test_background_exception_is_visible_and_runtime_survives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("failing", FAILED_BACKGROUND_PLUGIN)
            repository.write_builtin("stateless", STATELESS_PLUGIN)
            with CapabilityRuntime(repository.root, mode="resident") as runtime:
                result = runtime.dispatch("failing", "status")
                deadline = time.monotonic() + 2
                while True:
                    task = runtime.status()["plugins"]["failing"][
                        "background_tasks"
                    ]["failure"]
                    if task["state"] == "failed":
                        break
                    if time.monotonic() >= deadline:
                        self.fail("background exception was not captured")
                    time.sleep(0.01)
                stateless_result = runtime.dispatch("stateless", "status")

            self.assertTrue(result.ok)
            self.assertEqual(task["last_error"]["type"], "RuntimeError")
            self.assertTrue(stateless_result.ok)

    def test_started_runtime_resident_plugin_is_immutable_until_restart(self) -> None:
        def source(value: int) -> str:
            return f'''\
PLUGIN_NAME = "resident"
PLUGIN_API_VERSION = 1
PLUGIN_RUNTIME = "resident"

def invoke(context, command, args):
    return {{"value": {value}}}
'''

        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            path = repository.write_runtime("resident", source(1))
            with CapabilityRuntime(repository.root, mode="resident") as runtime:
                first = runtime.dispatch("resident", "value")
                stat = path.stat()
                repository.write_runtime("resident", source(2))
                os.utime(
                    path,
                    ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
                )
                second = runtime.dispatch("resident", "value")

        self.assertEqual(first.data["value"], 1)
        self.assertEqual(second.data["value"], 1)
        self.assertEqual(second.meta["plugin_load"], "cache_hit")


class ContextEndpointTests(unittest.TestCase):
    def test_context_requires_new_client_host_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config" / "environment.yaml"
            config.parent.mkdir()
            config.write_text(
                "setup:\n  status: READY\n"
                "grpc:\n  host: \"192.0.2.1\"\n  port: 50051\n",
                encoding="utf-8",
            )
            context = Context.load(root)

            endpoint = context.grpc_endpoint

        self.assertIsNone(endpoint.bind_host)
        self.assertIsNone(endpoint.client_host)

    def test_context_uses_explicit_bind_and_client_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config" / "environment.yaml"
            config.parent.mkdir()
            config.write_text(
                "setup:\n  status: READY\n"
                "grpc:\n  bind_host: \"0.0.0.0\"\n"
                "  client_host: \"127.0.0.1\"\n  port: 50051\n",
                encoding="utf-8",
            )
            context = Context.load(root)

            endpoint = context.grpc_endpoint

        self.assertEqual(endpoint.bind_host, "0.0.0.0")
        self.assertEqual(endpoint.client_host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
