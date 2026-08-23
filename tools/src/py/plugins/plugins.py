"""Explicit plugin discovery, description, and validation capability."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dcs_harness_runtime.plugin_api import (
    PLUGIN_API_VERSION as SUPPORTED_PLUGIN_API_VERSION,
)
from dcs_harness_runtime.result import ErrorCode, HarnessError


PLUGIN_NAME = "plugins"
PLUGIN_API_VERSION = SUPPORTED_PLUGIN_API_VERSION


def describe() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "api_version": PLUGIN_API_VERSION,
        "commands": {
            "list": {
                "description": "List built-in and runtime plugin file names without importing them.",
                "arguments": {},
            },
            "describe": {
                "description": "Load and describe one target plugin.",
                "arguments": {"name": {"type": "string", "required": True}},
            },
            "validate": {
                "description": "Validate one plugin name or allowed plugin path.",
                "arguments": {"target": {"type": "string", "required": True}},
            },
        },
    }


def _argument(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if isinstance(value, str) and value:
        return value
    positional = args.get("_")
    if isinstance(positional, list) and positional and isinstance(positional[0], str):
        return positional[0]
    raise HarnessError(
        ErrorCode.INVALID_ARGUMENT,
        f"Argument {key!r} is required.",
    )


def invoke(context: Any, command: str, args: Mapping[str, Any]) -> Any:
    runtime = context.runtime
    if runtime is None:
        raise HarnessError(
            ErrorCode.INTERNAL_ERROR,
            "Capability runtime is unavailable in the shared context.",
        )

    if command == "list":
        return runtime.resolver.discover()
    if command == "describe":
        name = _argument(args, "name")
        metadata, _ = runtime.describe_plugin(name)
        return metadata
    if command == "validate":
        target = _argument(args, "target")
        loaded, _ = runtime.validate_plugin(target)
        spec = loaded.spec
        return {
            "valid": True,
            "name": spec.name,
            "api_version": getattr(loaded.module, "PLUGIN_API_VERSION"),
            "source": spec.source.value,
            "path": str(Path(spec.path)),
            "has_describe": callable(getattr(loaded.module, "describe", None)),
        }
    raise HarnessError(
        ErrorCode.COMMAND_NOT_FOUND,
        f"Plugin {PLUGIN_NAME!r} has no command {command!r}.",
    )
