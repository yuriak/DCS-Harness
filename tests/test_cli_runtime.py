from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SETUP_SOURCE = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SETUP_SOURCE))

from dcs_harness_runtime.dispatcher import Dispatcher  # noqa: E402
from dcs_harness import _running_in_runtime_venv  # noqa: E402
from dcs_harness_runtime.plugin_api import PluginResolver, PluginSource  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402


VALID_PLUGIN = """\
PLUGIN_NAME = {name!r}
PLUGIN_API_VERSION = 1

def describe():
    return {{"name": PLUGIN_NAME, "api_version": PLUGIN_API_VERSION, "commands": {{"ping": {{}}}}}}

def invoke(context, command, args):
    if command != "ping":
        from dcs_harness_runtime.result import ErrorCode, HarnessError
        raise HarnessError(ErrorCode.COMMAND_NOT_FOUND, f"Unknown command: {{command}}")
    return {{"pong": True, "args": dict(args)}}
"""


class RuntimeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.builtin = root / "tools" / "src" / "py" / "plugins"
        self.runtime = root / "runtime" / "plugins" / "py"
        self.builtin.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        (root / ".gitmodules").touch()

    def write_builtin(self, name: str, content: str | None = None) -> Path:
        path = self.builtin / f"{name}.py"
        path.write_text(content or VALID_PLUGIN.format(name=name), encoding="utf-8")
        return path

    def write_runtime(self, name: str, content: str | None = None) -> Path:
        path = self.runtime / f"{name}.py"
        path.write_text(content or VALID_PLUGIN.format(name=name), encoding="utf-8")
        return path


class PluginResolutionTests(unittest.TestCase):
    def test_builtin_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            target = repository.write_builtin("alpha")
            spec = PluginResolver(repository.root).resolve("alpha")
        self.assertEqual(spec.source, PluginSource.BUILTIN)
        self.assertEqual(spec.path, target)

    def test_runtime_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            target = repository.write_runtime("alpha")
            spec = PluginResolver(repository.root).resolve("alpha")
        self.assertEqual(spec.source, PluginSource.RUNTIME)
        self.assertEqual(spec.path, target)

    def test_missing_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            with self.assertRaises(HarnessError) as raised:
                PluginResolver(repository.root).resolve("missing")
        self.assertEqual(raised.exception.code, ErrorCode.PLUGIN_NOT_FOUND)

    def test_builtin_runtime_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha")
            repository.write_runtime("alpha")
            with self.assertRaises(HarnessError) as raised:
                PluginResolver(repository.root).resolve("alpha")
        self.assertEqual(raised.exception.code, ErrorCode.PLUGIN_NAME_CONFLICT)

    def test_invalid_name_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            with self.assertRaises(HarnessError) as raised:
                PluginResolver(repository.root).resolve("../alpha")
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)


class LazyLoadingTests(unittest.TestCase):
    def test_discovery_does_not_import_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            marker = repository.root / "imported"
            side_effect = (
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
                + VALID_PLUGIN.format(name="sleeping")
            )
            repository.write_runtime("sleeping", side_effect)

            discovered = PluginResolver(repository.root).discover()

            self.assertEqual(discovered["runtime"], ["sleeping"])
            self.assertFalse(marker.exists())

    def test_dispatch_imports_only_target_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            marker = repository.root / "beta-imported"
            repository.write_builtin("alpha")
            beta = (
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
                + VALID_PLUGIN.format(name="beta")
            )
            repository.write_runtime("beta", beta)

            dispatcher = Dispatcher(repository.root)
            try:
                result = dispatcher.dispatch("alpha", "ping")
            finally:
                dispatcher.close()

            self.assertTrue(result.ok)
            self.assertFalse(marker.exists())

    def test_dispatch_ignores_one_hundred_unrelated_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("target")
            for index in range(100):
                name = f"dummy_{index}"
                repository.write_runtime(
                    name,
                    "raise RuntimeError('unrelated plugin was eagerly imported')\n",
                )

            dispatcher = Dispatcher(repository.root)
            try:
                result = dispatcher.dispatch("target", "ping")
            finally:
                dispatcher.close()

            self.assertTrue(result.ok)


class PluginContractTests(unittest.TestCase):
    def _load_error(self, content: str) -> HarnessError:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_runtime("broken", content)
            resolver = PluginResolver(repository.root)
            with self.assertRaises(HarnessError) as raised:
                resolver.load(resolver.resolve("broken"))
        return raised.exception

    def test_missing_name(self) -> None:
        error = self._load_error("PLUGIN_API_VERSION = 1\ndef invoke(c, x, a): return {}\n")
        self.assertEqual(error.code, ErrorCode.PLUGIN_API_INCOMPATIBLE)

    def test_wrong_api_version(self) -> None:
        error = self._load_error(
            "PLUGIN_NAME = 'broken'\nPLUGIN_API_VERSION = 2\ndef invoke(c, x, a): return {}\n"
        )
        self.assertEqual(error.code, ErrorCode.PLUGIN_API_INCOMPATIBLE)

    def test_missing_invoke(self) -> None:
        error = self._load_error("PLUGIN_NAME = 'broken'\nPLUGIN_API_VERSION = 1\n")
        self.assertEqual(error.code, ErrorCode.PLUGIN_API_INCOMPATIBLE)

    def test_describe_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_runtime("alpha")
            resolver = PluginResolver(repository.root)
            metadata = resolver.describe(resolver.resolve("alpha"))
        self.assertEqual(metadata["name"], "alpha")
        self.assertEqual(metadata["source"], "runtime")


class DispatcherTests(unittest.TestCase):
    def test_success_envelope_and_lifecycle_without_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha")
            dispatcher = Dispatcher(repository.root)
            try:
                result = dispatcher.dispatch(
                    "alpha", "ping", {"secret_payload": "not-for-log"}
                )
            finally:
                dispatcher.close()

            self.assertTrue(result.ok)
            self.assertEqual(result.meta["plugin_source"], "builtin")
            record_text = (
                repository.root / "runtime" / "logs" / "calls.jsonl"
            ).read_text(encoding="utf-8")
            record = json.loads(record_text)
            self.assertEqual(record["plugin"], "alpha")
            self.assertNotIn("not-for-log", record_text)

    def test_structured_command_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_builtin("alpha")
            dispatcher = Dispatcher(repository.root)
            try:
                result = dispatcher.dispatch("alpha", "unknown")
            finally:
                dispatcher.close()
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ErrorCode.COMMAND_NOT_FOUND.value)

    def test_non_serializable_plugin_result_is_structured_error(self) -> None:
        content = """\
PLUGIN_NAME = "broken"
PLUGIN_API_VERSION = 1
def invoke(context, command, args):
    return {object()}
"""
        with tempfile.TemporaryDirectory() as temporary:
            repository = RuntimeRepository(Path(temporary))
            repository.write_runtime("broken", content)
            dispatcher = Dispatcher(repository.root)
            try:
                result = dispatcher.dispatch("broken", "anything")
            finally:
                dispatcher.close()
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ErrorCode.INTERNAL_ERROR.value)


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [
                sys.executable,
                str(SETUP_SOURCE / "dcs_harness.py"),
                *arguments,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1, completed.stdout)
        return completed, json.loads(lines[0])

    def test_stdout_is_one_canonical_json_document(self) -> None:
        completed, payload = self.run_cli("--backend", "direct", "plugins", "list")
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["meta"]["backend"], "direct")

    def test_invalid_json_argument(self) -> None:
        completed, payload = self.run_cli(
            "--args-json", "[", "plugins", "list"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")

    def test_explicit_server_is_structured_unavailable(self) -> None:
        completed, payload = self.run_cli(
            "--backend", "server", "plugins", "list"
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["error"]["code"], "SERVER_UNAVAILABLE")

    def test_auto_currently_dispatches_direct(self) -> None:
        completed, payload = self.run_cli("plugins", "list")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(payload["meta"]["backend"], "direct")
        self.assertEqual(payload["meta"]["backend_requested"], "auto")

    def test_runtime_venv_detection_uses_prefix_not_resolved_executable(self) -> None:
        runtime_python = REPOSITORY_ROOT / "runtime" / "venv" / "bin" / "python"
        self.assertTrue(
            _running_in_runtime_venv(
                runtime_python,
                REPOSITORY_ROOT / "runtime" / "venv",
            )
        )
        self.assertFalse(
            _running_in_runtime_venv(runtime_python, REPOSITORY_ROOT / "not-the-venv")
        )


if __name__ == "__main__":
    unittest.main()
