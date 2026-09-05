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


FAST_STATELESS_PLUGIN = """\
PLUGIN_NAME = "fast"
PLUGIN_API_VERSION = 1

def invoke(context, command, args):
    return {"invoked": True}

def fast_report(context, runtime):
    return {"health": "ready", "session_id": "42", "mission_time": 12.5}
"""


BROKEN_REPORT_PLUGIN = """\
PLUGIN_NAME = "broken"
PLUGIN_API_VERSION = 1

def invoke(context, command, args):
    return {}

def fast_report(context, runtime):
    raise RuntimeError("report boom")
"""


def fast_resident_plugin(start_marker: Path) -> str:
    return f'''\
from pathlib import Path

PLUGIN_NAME = "resident"
PLUGIN_API_VERSION = 1
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True
START_MARKER = Path({str(start_marker)!r})

def start(context, runtime):
    START_MARKER.write_text("started")
    runtime.state = {{"value": 7}}

def invoke(context, command, args):
    return {{"invoked": True}}

def fast_report(context, runtime):
    return {{"health": "ready", "value": runtime.state["value"]}}
'''


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
    def test_fast_status_isolates_failures_and_does_not_start_residents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            marker = repository.root / "started"
            repository.write_builtin(
                "grpc", FAST_STATELESS_PLUGIN.replace('"fast"', '"grpc"')
            )
            repository.write_builtin("stateless", STATELESS_PLUGIN)
            repository.write_builtin("broken", BROKEN_REPORT_PLUGIN)
            repository.write_runtime("import_bad", "raise RuntimeError('import boom')\n")
            repository.write_runtime(
                "json_bad",
                "PLUGIN_NAME = 'json_bad'\nPLUGIN_API_VERSION = 1\n"
                "def invoke(c, x, a): return {}\n"
                "def fast_report(c, r): return {'bad': {object()}}\n",
            )
            repository.write_builtin("resident", fast_resident_plugin(marker))
            repository.write_builtin("conflict", STATELESS_PLUGIN.replace('"stateless"', '"conflict"'))
            repository.write_runtime("conflict", STATELESS_PLUGIN.replace('"stateless"', '"conflict"'))

            with CapabilityRuntime(repository.root, mode="direct") as runtime:
                status = runtime.fast_status()

            self.assertEqual(status["health"], "degraded")
            self.assertEqual(status["session_id"], "42")
            self.assertEqual(status["mission_time"], 12.5)
            self.assertEqual(status["plugins"]["grpc"]["report_status"], "ok")
            self.assertEqual(
                status["plugins"]["stateless"]["report_status"],
                "not_reportable",
            )
            self.assertEqual(status["plugins"]["broken"]["report_status"], "error")
            self.assertEqual(
                status["plugins"]["import_bad"]["report_status"], "error"
            )
            self.assertEqual(status["plugins"]["json_bad"]["report_status"], "error")
            self.assertEqual(
                status["plugins"]["resident"]["report_status"], "not_started"
            )
            self.assertEqual(
                status["plugins"]["conflict"]["report_status"], "conflict"
            )
            self.assertFalse(marker.exists())

    def test_running_resident_fast_report_receives_runtime_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            marker = repository.root / "started"
            repository.write_builtin("resident", fast_resident_plugin(marker))
            with CapabilityRuntime(repository.root, mode="resident") as runtime:
                runtime.autostart(("resident",))
                status = runtime.fast_status()

            report = status["plugins"]["resident"]
            self.assertEqual(report["report_status"], "ok")
            self.assertEqual(report["lifecycle_state"], "running")
            self.assertEqual(report["data"]["value"], 7)
            self.assertTrue(marker.exists())
            stopped = runtime.fast_status()["plugins"]["resident"]
            self.assertEqual(stopped["report_status"], "stopped")

    def test_failed_resident_report_exposes_lifecycle_without_calling_report(self) -> None:
        content = """\
PLUGIN_NAME = "failed"
PLUGIN_API_VERSION = 1
PLUGIN_RUNTIME = "resident"

def start(context, runtime):
    raise RuntimeError("start boom")

def invoke(context, command, args):
    return {}

def fast_report(context, runtime):
    raise AssertionError("must not be called")
"""
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("failed", content)
            with CapabilityRuntime(repository.root, mode="resident") as runtime:
                result = runtime.dispatch("failed", "anything")
                status = runtime.fast_status()

        self.assertFalse(result.ok)
        report = status["plugins"]["failed"]
        self.assertEqual(report["report_status"], "failed")
        self.assertEqual(report["error"]["code"], "CAPABILITY_UNAVAILABLE")

    def test_fast_status_warns_on_conflicting_current_facts(self) -> None:
        second = FAST_STATELESS_PLUGIN.replace('"fast"', '"telemetry"').replace(
            '"42"', '"99"'
        ).replace("12.5", "80.0")
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin(
                "lua", FAST_STATELESS_PLUGIN.replace('"fast"', '"lua"')
            )
            repository.write_builtin("telemetry", second)
            with CapabilityRuntime(repository.root, mode="direct") as runtime:
                status = runtime.fast_status()

        codes = {warning["code"] for warning in status["warnings"]}
        self.assertIn("CONFLICTING_SESSION_ID", codes)
        self.assertIn("MISSION_TIME_DIVERGENCE", codes)
        self.assertEqual(status["session_id"], "99")
        self.assertEqual(status["mission_time"], 80.0)

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
