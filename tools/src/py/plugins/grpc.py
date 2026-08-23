"""Built-in descriptor-driven DCS-gRPC capability."""

from __future__ import annotations

from typing import Any, Mapping

from dcs_harness_runtime.grpc_support import DEFAULT_TIMEOUT_SECONDS, GrpcSupport
from dcs_harness_runtime.plugin_api import PLUGIN_API_VERSION as SUPPORTED_API_VERSION
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "grpc"
PLUGIN_API_VERSION = SUPPORTED_API_VERSION


def describe() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "runtime": "stateless",
        "commands": {
            "services": {
                "description": "List services from generated protobuf descriptors.",
                "arguments": {},
            },
            "describe": {
                "description": "Describe a gRPC service or one of its methods.",
                "arguments": {
                    "service": {"type": "string", "required": True},
                    "method": {"type": "string", "required": False},
                },
            },
            "call": {
                "description": "Invoke a unary DCS-gRPC method with a JSON request.",
                "arguments": {
                    "service": {"type": "string", "required": True},
                    "method": {"type": "string", "required": True},
                    "request": {"type": "object", "required": False, "default": {}},
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
    if command not in {"services", "describe", "call"}:
        raise HarnessError(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
        )
    support = GrpcSupport(context)
    if command == "services":
        return {"services": support.services()}
    if command == "describe":
        service, method = _service_and_method(args, method_required=False)
        return support.describe(service, method)
    if command == "call":
        service, method = _service_and_method(args, method_required=True)
        request = args.get("request", {})
        timeout = args.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        return support.call(service, method, request, timeout=timeout)
    raise AssertionError("validated gRPC command was not dispatched")


def _service_and_method(
    args: Mapping[str, Any], *, method_required: bool
) -> tuple[str, str | None]:
    service = args.get("service")
    method = args.get("method")
    positional = args.get("_")
    if not isinstance(service, str) and isinstance(positional, list) and positional:
        service = positional[0]
    if (
        not isinstance(method, str)
        and isinstance(positional, list)
        and len(positional) > 1
    ):
        method = positional[1]
    if not isinstance(service, str) or not service:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT, "Argument 'service' is required."
        )
    if method is not None and (not isinstance(method, str) or not method):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT, "Argument 'method' must be a string."
        )
    if method_required and not method:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT, "Argument 'method' is required."
        )
    return service, method
