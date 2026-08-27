from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.log_collector import LogFollower  # noqa: E402
from dcs_harness_runtime.resident import (  # noqa: E402
    AUTOSTART_BUILTINS,
    CapabilityRuntime,
)
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402


class LogFollowerTests(unittest.TestCase):
    def test_missing_source_appears_and_bytes_partial_lines_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "saved-games" / "Logs" / "dcs.log"
            follower = LogFollower(
                "dcs", source, root / "runtime" / "logs" / "dcs", root
            )

            self.assertIsNone(follower.poll())
            self.assertEqual(follower.status()["state"], "missing")
            with self.assertRaises(HarnessError) as raised:
                follower.tail(10)
            self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)

            source.parent.mkdir(parents=True)
            source.write_bytes(b"one\npartial")
            self.assertEqual(follower.poll(), "epoch")
            with source.open("ab") as stream:
                stream.write(b"-done\nbad-\xff\n")
            self.assertEqual(follower.poll(), "append")

            status = follower.status()
            mirror = root / status["mirror_path"]
            self.assertEqual(status["state"], "following")
            self.assertEqual(status["offset"], source.stat().st_size)
            self.assertEqual(mirror.read_bytes(), source.read_bytes())
            self.assertEqual(follower.tail(2), ["partial-done", "bad-�"])
            self.assertEqual(follower.search("partial", 10), ["partial-done"])

    def test_truncate_and_replace_create_new_current_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "dcs.log"
            mirror_root = root / "runtime" / "logs" / "dcs"
            source.write_bytes(b"A\nB\n")
            follower = LogFollower("dcs", source, mirror_root, root)
            follower.poll()
            first = root / follower.status()["mirror_path"]

            source.write_bytes(b"C\n")
            self.assertEqual(follower.poll(), "epoch")
            second = root / follower.status()["mirror_path"]
            self.assertNotEqual(second, first)
            self.assertEqual(first.read_bytes(), b"A\nB\n")
            self.assertEqual(follower.tail(10), ["C"])

            replacement = root / "replacement.log"
            replacement.write_bytes(b"D\nE\n")
            replacement.replace(source)
            self.assertEqual(follower.poll(), "epoch")
            third = root / follower.status()["mirror_path"]
            self.assertNotEqual(third, second)
            self.assertEqual(second.read_bytes(), b"C\n")
            self.assertEqual(follower.tail(10), ["D", "E"])

            source.unlink()
            self.assertEqual(follower.poll(), "missing")
            with self.assertRaises(HarnessError):
                follower.tail(10)
            source.write_bytes(b"F\n")
            self.assertEqual(follower.poll(), "epoch")
            fourth = root / follower.status()["mirror_path"]
            self.assertNotEqual(fourth, third)
            self.assertEqual(follower.tail(10), ["F"])
            self.assertEqual(len(list(mirror_root.glob("dcs-*.log"))), 4)

    def test_harness_restart_resumes_matching_source_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "dcs.log"
            mirror_root = root / "runtime" / "logs" / "dcs"
            source.write_bytes(b"before\n")
            first = LogFollower("dcs", source, mirror_root, root)
            first.poll()
            first_path = first.status()["mirror_path"]

            with source.open("ab") as stream:
                stream.write(b"after\n")
            restarted = LogFollower("dcs", source, mirror_root, root)
            restarted.poll()

            self.assertEqual(restarted.status()["mirror_path"], first_path)
            self.assertEqual(restarted.tail(10), ["before", "after"])
            self.assertEqual(len(list(mirror_root.glob("dcs-*.log"))), 1)


class LogsPluginIntegrationTests(unittest.TestCase):
    @staticmethod
    def prepare_logs_plugin(root: Path) -> None:
        builtin = root / "tools" / "src" / "py" / "plugins"
        builtin.mkdir(parents=True)
        (root / ".gitmodules").touch()
        shutil.copyfile(SOURCE_ROOT / "plugins" / "logs.py", builtin / "logs.py")

    @staticmethod
    def wait_for(
        runtime: CapabilityRuntime,
        source: str,
        state: str,
        *,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            result = runtime.dispatch("logs", "status")
            status = result.data["sources"][source]
            if status["state"] == state:
                return result.data
            if time.monotonic() >= deadline:
                raise AssertionError(result.to_json())
            time.sleep(0.01)

    def test_autostart_tail_search_rotation_missing_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.prepare_logs_plugin(root)
            saved_games = root / "Saved Games" / "DCS"
            log_dir = saved_games / "Logs"
            log_dir.mkdir(parents=True)
            dcs_log = log_dir / "dcs.log"
            grpc_log = log_dir / "gRPC.log"
            dcs_log.write_text("INFO ready\nERROR first\n", encoding="utf-8")

            runtime = CapabilityRuntime(root, mode="resident")
            runtime.context.environment = {
                "setup": {"status": "READY"},
                "dcs": {
                    "saved_games_dir": str(saved_games),
                    "log_file": str(dcs_log),
                },
            }
            runtime.autostart(("logs",))

            status = self.wait_for(runtime, "dcs", "following")
            self.assertEqual(status["collector"], "running")
            self.assertEqual(status["sources"]["grpc"]["state"], "missing")
            first_mirror = status["sources"]["dcs"]["mirror_path"]
            tail = runtime.dispatch("logs", "tail", {"source": "dcs", "lines": 1})
            search = runtime.dispatch(
                "logs", "search", {"source": "dcs", "query": "ERROR"}
            )
            self.assertEqual(tail.data["lines"], ["ERROR first"])
            self.assertEqual(search.data["lines"], ["ERROR first"])

            grpc_log.write_text("grpc online\n", encoding="utf-8")
            self.wait_for(runtime, "grpc", "following")
            self.assertEqual(
                runtime.dispatch(
                    "logs", "tail", {"source": "grpc", "lines": 5}
                ).data["lines"],
                ["grpc online"],
            )

            dcs_log.write_text("NEW PROCESS\n", encoding="utf-8")
            rotated = self.wait_for(runtime, "dcs", "following")
            deadline = time.monotonic() + 3
            while (
                rotated["sources"]["dcs"]["mirror_path"] == first_mirror
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
                rotated = runtime.dispatch("logs", "status").data
            self.assertNotEqual(
                rotated["sources"]["dcs"]["mirror_path"], first_mirror
            )
            self.assertEqual(
                runtime.dispatch(
                    "logs", "tail", {"source": "dcs", "lines": 10}
                ).data["lines"],
                ["NEW PROCESS"],
            )

            invalid_calls = (
                ("tail", {"source": "dcs", "lines": 0}),
                ("tail", {"source": "dcs", "lines": 1001}),
                ("tail", {"source": "unknown"}),
                ("search", {"source": "dcs", "query": ""}),
                ("search", {"source": "dcs", "query": "x", "limit": 1001}),
                ("search", {"source": "dcs", "query": "x", "regex": True}),
            )
            for command, args in invalid_calls:
                with self.subTest(command=command, args=args):
                    result = runtime.dispatch("logs", command, args)
                    self.assertFalse(result.ok)
                    self.assertEqual(
                        result.error.code, ErrorCode.INVALID_ARGUMENT.value
                    )

            runtime.close()
            self.assertEqual(runtime.status()["state"], "stopped")

    def test_direct_rejects_resident_logs_without_starting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.prepare_logs_plugin(root)
            with CapabilityRuntime(root, mode="direct") as runtime:
                result = runtime.dispatch("logs", "status")
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, ErrorCode.CAPABILITY_UNAVAILABLE.value)
            self.assertFalse((root / "runtime" / "logs" / "dcs").exists())

    def test_logs_is_an_explicit_autostart_builtin(self) -> None:
        self.assertIn("logs", AUTOSTART_BUILTINS)


if __name__ == "__main__":
    unittest.main()
