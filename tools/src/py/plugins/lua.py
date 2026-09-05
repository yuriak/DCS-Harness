"""Built-in arbitrary Lua Eval bridge."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.grpc_support import DEFAULT_TIMEOUT_SECONDS, GrpcSupport
from dcs_harness_runtime.lua_support import LuaSupport
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError
from dcs_harness_runtime.reporting import unavailable_error


PLUGIN_NAME = "lua"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION
FAST_REPORT_TIMEOUT_SECONDS = 1.0
MISSION_SERVICE = "dcs.mission.v0.MissionService"
FAST_REPORT_LUA = """\
return {
  available = true,
  mission_time = timer and timer.getTime and timer.getTime() or nil
}
"""


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


def fast_report(context: Any, runtime: Any) -> Mapping[str, Any]:
    value: dict[str, Any] = {"health": "unavailable", "available": False}
    try:
        endpoint = context.require_grpc_client_endpoint()
        value["eval_enabled"] = endpoint.eval_enabled
        if not endpoint.eval_enabled:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS-gRPC Eval is disabled.",
                details={"reason": "EVAL_DISABLED"},
            )
        session = GrpcSupport(context).call(
            MISSION_SERVICE, "GetSessionId", {}, timeout=FAST_REPORT_TIMEOUT_SECONDS
        )
        result = LuaSupport(context).eval(
            FAST_REPORT_LUA, timeout=FAST_REPORT_TIMEOUT_SECONDS
        ).get("result")
        session_id = session.get("session_id") if isinstance(session, Mapping) else None
        mission_time = result.get("mission_time") if isinstance(result, Mapping) else None
        if (
            isinstance(session_id, bool)
            or not isinstance(session_id, (str, int))
            or not isinstance(result, Mapping)
            or result.get("available") is not True
            or isinstance(mission_time, bool)
            or not isinstance(mission_time, (int, float))
        ):
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Mission Lua status probe returned malformed data.",
            )
        value.update(
            health="ready",
            available=True,
            session_id=str(session_id),
            mission_time=mission_time,
        )
    except Exception as error:
        value["error"] = unavailable_error(error, "Mission Lua probe failed.")
    return value


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
