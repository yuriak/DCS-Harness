"""Built-in arbitrary Lua Eval bridge."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.grpc_support import DEFAULT_TIMEOUT_SECONDS
from dcs_harness_runtime.lua_support import LuaSupport
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "lua"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION


def describe() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": "stateless",
        "commands": {
            "eval": {
                "description": "Evaluate raw Lua in the DCS mission environment.",
                "arguments": {
                    "code": {"type": "string", "required": True},
                    "timeout": {
                        "type": "number",
                        "required": False,
                        "default": DEFAULT_TIMEOUT_SECONDS,
                    },
                },
            },
            "eval-file": {
                "description": "Evaluate an allowed repository-local Lua file.",
                "arguments": {
                    "path": {"type": "string", "required": True},
                    "timeout": {
                        "type": "number",
                        "required": False,
                        "default": DEFAULT_TIMEOUT_SECONDS,
                    },
                },
            },
            "load-file": {
                "description": "Execute an allowed Lua file to load definitions.",
                "arguments": {
                    "path": {"type": "string", "required": True},
                    "timeout": {
                        "type": "number",
                        "required": False,
                        "default": DEFAULT_TIMEOUT_SECONDS,
                    },
                },
            },
        },
    }


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    if command not in {"eval", "eval-file", "load-file"}:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    support = LuaSupport(context)
    timeout = args.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if command == "eval":
        return support.eval(_argument(args, "code"), timeout=timeout)
    return support.eval_file(_argument(args, "path"), timeout=timeout)


def _argument(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    positional = args.get("_")
    if value is None and isinstance(positional, list) and positional:
        value = positional[0]
    if not isinstance(value, str) or not value:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"Argument {name!r} is required and must be a string.",
        )
    return value
