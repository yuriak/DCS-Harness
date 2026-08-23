from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness import invoke_request  # noqa: E402
from dcs_harness_runtime.plugin_api import PluginCache, PluginResolver  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from dcs_harness_runtime.server import CapabilityServer  # noqa: E402
from dcs_harness_runtime.server_client import ServerClient  # noqa: E402


PLUGIN_TEMPLATE = """\
PLUGIN_NAME = {name!r}
PLUGIN_API_VERSION = 1

def describe():
    return {{"name": PLUGIN_NAME, "commands": {{"value": {{}}}}}}

def invoke(context, command, args):
    if command != "value":
        from dcs_harness_runtime.result import ErrorCode, HarnessError
        raise HarnessError(ErrorCode.COMMAND_NOT_FOUND, "unknown command")
    return {{"value": {value}}}
"""


class RuntimeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.builtin = root / "tools" / "src" / "py" / "plugins"
        self.runtime = root / "runtime" / "plugins" / "py"
        self.builtin.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        (root / ".gitmodules").touch()

    def write_builtin(self, name: str, value: int = 1) -> Path:
        path = self.builtin / f"{name}.py"
        path.write_text(
            PLUGIN_TEMPLATE.format(name=name, value=value), encoding="utf-8"
        )
        return path

    def write_builtin_source(self, name: str, content: str) -> Path:
        path = self.builtin / f"{name}.py"
        path.write_text(content, encoding="utf-8")
        return path

    def write_runtime(self, name: str, value: int = 1) -> Path:
        path = self.runtime / f"{name}.py"
        path.write_text(
            PLUGIN_TEMPLATE.format(name=name, value=value), encoding="utf-8"
        )
        return path


class PluginCacheTests(unittest.TestCase):
    def test_builtin_is_immutable_for_server_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha", 1)
            resolver = PluginResolver(repository.root)
            cache = PluginCache(resolver)

            first, first_status = cache.load(resolver.resolve("alpha"))
            repository.write_builtin("alpha", 2)
            second, second_status = cache.load(resolver.resolve("alpha"))

        self.assertEqual(first_status, "loaded")
        self.assertEqual(second_status, "cache_hit")
        self.assertIs(first, second)

    def test_runtime_target_is_reloaded_after_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            path = repository.write_runtime("alpha", 1)
            resolver = PluginResolver(repository.root)
            cache = PluginCache(resolver)

            first, first_status = cache.load(resolver.resolve("alpha"))
            signature = path.stat()
            repository.write_runtime("alpha", 2)
            os.utime(
                path,
                ns=(signature.st_atime_ns, signature.st_mtime_ns + 1_000_000),
            )
            second, second_status = cache.load(resolver.resolve("alpha"))

        self.assertEqual(first_status, "loaded")
        self.assertEqual(second_status, "reloaded")
        self.assertEqual(first.invoke(None, "value", {})["value"], 1)
        self.assertEqual(second.invoke(None, "value", {})["value"], 2)


class ResidentServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = RuntimeRepository(Path(self.temporary.name))
        self.repository.write_builtin("alpha", 1)
        self.server = CapabilityServer(
            self.repository.root, port=0, autostart_plugins=()
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = ServerClient(self.repository.root, timeout=1.0)
        deadline = time.monotonic() + 3
        while True:
            try:
                self.state = self.client.health()
                break
            except HarnessError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.assertFalse((self.repository.root / "runtime" / "server.json").exists())
        self.temporary.cleanup()

    def test_health_is_loopback_and_state_is_published(self) -> None:
        self.assertEqual(self.state.host, "127.0.0.1")
        self.assertEqual(self.state.port, self.server.port)
        self.assertEqual(self.state.api_version, 1)
        with urlopen(
            f"http://{self.state.host}:{self.state.port}/health", timeout=1
        ) as response:
            health = json.loads(response.read())
        self.assertEqual(health["runtime"]["mode"], "resident")
        self.assertEqual(health["runtime"]["state"], "running")

    def test_invoke_uses_canonical_envelope_and_cache(self) -> None:
        first = self.client.invoke(
            "alpha", "value", {}, request_id="first", state=self.state
        )
        second = self.client.invoke(
            "alpha", "value", {}, request_id="second", state=self.state
        )

        self.assertTrue(first.ok)
        self.assertEqual(first.request_id, "first")
        self.assertEqual(first.meta["backend"], "server")
        self.assertEqual(first.meta["plugin_load"], "loaded")
        self.assertEqual(second.meta["plugin_load"], "cache_hit")

    def test_new_runtime_plugin_and_targeted_reload(self) -> None:
        path = self.repository.write_runtime("dynamic", 1)
        first = self.client.invoke(
            "dynamic", "value", {}, request_id="one", state=self.state
        )
        stat = path.stat()
        self.repository.write_runtime("dynamic", 2)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        second = self.client.invoke(
            "dynamic", "value", {}, request_id="two", state=self.state
        )

        self.assertEqual(first.data["value"], 1)
        self.assertEqual(first.meta["plugin_load"], "loaded")
        self.assertEqual(second.data["value"], 2)
        self.assertEqual(second.meta["plugin_load"], "reloaded")

    def test_plugin_listing_does_not_import_plugins(self) -> None:
        marker = self.repository.root / "imported"
        self.repository.write_runtime(
            "sleeping",
            1,
        ).write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n"
            + PLUGIN_TEMPLATE.format(name="sleeping", value=1),
            encoding="utf-8",
        )
        with urlopen(
            f"http://{self.state.host}:{self.state.port}/plugins", timeout=1
        ) as response:
            value = json.loads(response.read())

        self.assertIn("sleeping", value["plugins"]["runtime"])
        self.assertFalse(marker.exists())

    def test_targeted_plugin_description_uses_cache(self) -> None:
        url = f"http://{self.state.host}:{self.state.port}/plugins/alpha"
        with urlopen(url, timeout=1) as response:
            first = json.loads(response.read())
        with urlopen(url, timeout=1) as response:
            second = json.loads(response.read())

        self.assertTrue(first["ok"])
        self.assertEqual(first["plugin"]["name"], "alpha")
        self.assertEqual(first["meta"]["plugin_load"], "loaded")
        self.assertEqual(second["meta"]["plugin_load"], "cache_hit")

    def test_auto_uses_server(self) -> None:
        result = invoke_request(
            self.repository.root,
            backend="auto",
            plugin="alpha",
            command="value",
            args={},
            request_id="auto",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.meta["backend"], "server")


class BackendSelectionTests(unittest.TestCase):
    def test_auto_falls_back_to_direct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha")
            result = invoke_request(
                repository.root,
                backend="auto",
                plugin="alpha",
                command="value",
                args={},
                request_id="auto-direct",
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.meta["backend"], "direct")

    def test_explicit_server_fails_when_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha")
            with self.assertRaises(HarnessError) as raised:
                invoke_request(
                    repository.root,
                    backend="server",
                    plugin="alpha",
                    command="value",
                    args={},
                    request_id="server",
                )
        self.assertEqual(raised.exception.code, ErrorCode.SERVER_UNAVAILABLE)

    def test_direct_does_not_read_server_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha")
            state = repository.root / "runtime" / "server.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text("not-json", encoding="utf-8")
            result = invoke_request(
                repository.root,
                backend="direct",
                plugin="alpha",
                command="value",
                args={},
                request_id="direct",
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.meta["backend"], "direct")


class ServerResidentLifecycleTests(unittest.TestCase):
    def test_server_autostarts_and_stops_resident_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            start_marker = repository.root / "resident-started"
            stop_marker = repository.root / "resident-stopped"
            repository.write_builtin_source(
                "resident",
                f'''\
from pathlib import Path

PLUGIN_NAME = "resident"
PLUGIN_API_VERSION = 1
PLUGIN_RUNTIME = "resident"
PLUGIN_AUTOSTART = True

def worker(stop_event):
    stop_event.wait()

def start(context, runtime):
    Path({str(start_marker)!r}).write_text("started")
    runtime.start_background("worker", worker)

def invoke(context, command, args):
    return {{"running": True}}

def stop(context, runtime):
    Path({str(stop_marker)!r}).write_text("stopped")
''',
            )
            server = CapabilityServer(
                repository.root,
                port=0,
                autostart_plugins=("resident",),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = ServerClient(repository.root, timeout=1.0)
            deadline = time.monotonic() + 3
            while True:
                try:
                    state = client.health()
                    break
                except HarnessError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            with urlopen(
                f"http://{state.host}:{state.port}/health", timeout=1
            ) as response:
                health = json.loads(response.read())

            server.shutdown()
            thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(start_marker.read_text(), "started")
            self.assertEqual(stop_marker.read_text(), "stopped")
            plugin = health["runtime"]["plugins"]["resident"]
            self.assertEqual(plugin["state"], "running")
            self.assertEqual(plugin["background_tasks"]["worker"]["state"], "running")

if __name__ == "__main__":
    unittest.main()
