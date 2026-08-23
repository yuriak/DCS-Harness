from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.lua_support import LuaSupport  # noqa: E402
from dcs_harness_runtime.plugin_api import PluginResolver  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402


EVAL_PATH = "/dcs.custom.v0.CustomService/Eval"


class FakeChannel:
    def __init__(self) -> None:
        self.response: Any = None
        self.error: Exception | None = None
        self.registrations: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def unary_unary(
        self,
        path: str,
        request_serializer: Any,
        response_deserializer: Any,
        **kwargs: Any,
    ) -> Any:
        self.registrations.append(path)

        def call(
            request: Any, *, timeout: float | None = None, **call_kwargs: Any
        ) -> Any:
            self.calls.append(
                {"path": path, "request": request, "timeout": timeout}
            )
            if path == EVAL_PATH and self.error is not None:
                raise self.error
            if path != EVAL_PATH or self.response is None:
                raise AssertionError(f"Unexpected fake RPC: {path}")
            return response_deserializer(self.response.SerializeToString())

        return call

    def unary_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def stream_unary(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: None

    def stream_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def close(self) -> None:
        pass


class FakeRpcError(Exception):
    def __init__(self, status: Any, details: str) -> None:
        super().__init__(details)
        self._status = status
        self._details = details

    def code(self) -> Any:
        return self._status

    def details(self) -> str:
        return self._details


class LuaPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "runtime" / "workspace").mkdir(parents=True)
        (self.root / "runtime" / "plugins" / "lua").mkdir(parents=True)
        self.context = Context(
            repository_root=self.root,
            environment_path=self.root / "config" / "environment.yaml",
            environment={
                "setup": {"status": "READY"},
                "grpc": {
                    "client_host": "127.0.0.1",
                    "port": 50051,
                    "eval_enabled": True,
                },
            },
            runtime_root=self.root / "runtime",
            generated_root=REPOSITORY_ROOT / "runtime" / "generated",
        )
        self.channel = FakeChannel()
        self.context._grpc_channel = self.channel
        self.support = LuaSupport(self.context)
        self.context.ensure_generated_import_path()
        from dcs_grpc.dcs.custom.v0 import custom_pb2

        self.response_class = custom_pb2.EvalResponse

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def respond(self, encoded: str) -> None:
        self.channel.response = self.response_class(json=encoded)

    def test_raw_eval_parses_json_result(self) -> None:
        self.respond('{"alive":true,"count":2}')
        result = self.support.eval("return {alive=true, count=2}", timeout=1.5)
        self.assertEqual(result, {"result": {"alive": True, "count": 2}})
        call = self.channel.calls[-1]
        self.assertEqual(call["path"], EVAL_PATH)
        self.assertEqual(call["request"].lua, "return {alive=true, count=2}")
        self.assertEqual(call["timeout"], 1.5)

    def test_eval_disabled_is_capability_error_before_stub_creation(self) -> None:
        self.context.environment["grpc"]["eval_enabled"] = False
        with self.assertRaises(HarnessError) as raised:
            self.support.eval("return true")
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)
        self.assertEqual(self.channel.registrations, [])

    def test_server_permission_denied_is_capability_error(self) -> None:
        import grpc

        class PermissionError(FakeRpcError, grpc.RpcError):
            pass

        self.channel.error = PermissionError(
            grpc.StatusCode.PERMISSION_DENIED, "eval operation is disabled"
        )
        with self.assertRaises(HarnessError) as raised:
            self.support.eval("return true")
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)

    def test_lua_runtime_failure_is_translated(self) -> None:
        import grpc

        class InternalError(FakeRpcError, grpc.RpcError):
            pass

        self.channel.error = InternalError(
            grpc.StatusCode.INTERNAL,
            "Failed to execute Lua code: attempt to index a nil value",
        )
        with self.assertRaises(HarnessError) as raised:
            self.support.eval("return missing.value")
        self.assertEqual(raised.exception.code, ErrorCode.LUA_EXECUTION_FAILED)
        self.assertIn("attempt to index", raised.exception.details["reason"])

    def test_malformed_eval_responses_are_structured(self) -> None:
        for response in (self.response_class(), self.response_class(json="{")):
            with self.subTest(response=response):
                self.channel.response = response
                with self.assertRaises(HarnessError) as raised:
                    self.support.eval("return true")
                self.assertEqual(raised.exception.code, ErrorCode.GRPC_CALL_FAILED)

    def test_allowed_workspace_file_is_evaluated(self) -> None:
        path = self.root / "runtime" / "workspace" / "debug.lua"
        path.write_text("return 42\n", encoding="utf-8")
        self.respond("42")
        result = self.support.eval_file("runtime/workspace/debug.lua")
        self.assertEqual(
            result,
            {"result": 42, "path": "runtime/workspace/debug.lua"},
        )
        self.assertEqual(self.channel.calls[-1]["request"].lua, "return 42\n")

    def test_allowed_plugin_lua_file_is_evaluated(self) -> None:
        path = self.root / "runtime" / "plugins" / "lua" / "helpers.lua"
        path.write_text("return nil\n", encoding="utf-8")
        self.respond("null")
        result = self.support.eval_file(path.as_posix())
        self.assertIsNone(result["result"])

    def test_disallowed_and_invalid_paths_are_rejected(self) -> None:
        text_file = self.root / "runtime" / "workspace" / "notes.txt"
        text_file.write_text("not lua", encoding="utf-8")
        cases = (
            "../outside.lua",
            str(REPOSITORY_ROOT / "pyproject.toml"),
            "runtime/workspace/notes.txt",
            "runtime/workspace/missing.lua",
        )
        for path in cases:
            with self.subTest(path=path), self.assertRaises(HarnessError) as raised:
                self.support.resolve_file(path)
            self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)

    def test_symlink_cannot_escape_allowed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory) / "secret.lua"
            outside.write_text("return 'secret'", encoding="utf-8")
            link = self.root / "runtime" / "workspace" / "linked.lua"
            link.symlink_to(outside)
            with self.assertRaises(HarnessError) as raised:
                self.support.resolve_file(str(link))
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)

    def test_builtin_plugin_contract_includes_load_file(self) -> None:
        resolver = PluginResolver(REPOSITORY_ROOT)
        spec = resolver.resolve("lua")
        metadata = resolver.describe(spec)
        self.assertEqual(
            set(metadata["commands"]), {"eval", "eval-file", "load-file"}
        )

        path = self.root / "runtime" / "plugins" / "lua" / "definitions.lua"
        path.write_text("HARNESS_TEST_VALUE = 7\nreturn true\n", encoding="utf-8")
        self.respond("true")
        loaded = resolver.load(spec)
        result = loaded.invoke(
            self.context,
            "load-file",
            {"path": str(path), "timeout": 3},
        )
        self.assertEqual(result["result"], True)
        self.assertEqual(
            self.channel.calls[-1]["request"].lua,
            "HARNESS_TEST_VALUE = 7\nreturn true\n",
        )


if __name__ == "__main__":
    unittest.main()
