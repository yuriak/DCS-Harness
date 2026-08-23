#!/usr/bin/env python3
"""Thin command-line frontend for the DCS-Harness capability runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence


def discover_repository_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".gitmodules").is_file():
            return candidate
    return Path(__file__).resolve().parents[3]


def _runtime_python(repository_root: Path) -> Path:
    if sys.platform == "win32":
        return repository_root / "runtime" / "venv" / "Scripts" / "python.exe"
    return repository_root / "runtime" / "venv" / "bin" / "python"


def _running_in_runtime_venv(target: Path, prefix: str | Path | None = None) -> bool:
    venv_root = target.parent.parent
    try:
        return Path(prefix or sys.prefix).resolve() == venv_root.resolve()
    except OSError:
        return False


def _ensure_runtime_python(repository_root: Path) -> None:
    target = _runtime_python(repository_root)
    if not target.is_file() or os.environ.get("DCS_HARNESS_RUNTIME_PYTHON") == "1":
        return
    if _running_in_runtime_venv(target):
        return
    environment = os.environ.copy()
    environment["DCS_HARNESS_RUNTIME_PYTHON"] = "1"
    os.execve(
        str(target),
        [str(target), str(Path(__file__).resolve()), *sys.argv[1:]],
        environment,
    )


class CanonicalArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        from dcs_harness_runtime.result import ErrorCode, HarnessError

        raise HarnessError(ErrorCode.INVALID_ARGUMENT, message)


def parse_request(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = CanonicalArgumentParser(
        description="Invoke a DCS-Harness capability plugin."
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "direct", "server"),
        default="auto",
        help="Invocation backend (default: auto).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7777,
        help="Loopback port for the serve command (default: 7777).",
    )
    parser.add_argument(
        "--args-json",
        default="{}",
        help="JSON object merged with trailing positional plugin arguments.",
    )
    parser.add_argument("plugin", nargs="?", help="Target plugin name, or serve.")
    parser.add_argument("command", nargs="?", help="Target plugin command.")
    parser.add_argument("plugin_args", nargs="*", help="Plugin positional arguments.")
    return parser.parse_args(argv)


def _parse_plugin_args(namespace: argparse.Namespace) -> dict[str, Any]:
    from dcs_harness_runtime.result import ErrorCode, HarnessError

    try:
        value = json.loads(namespace.args_json)
    except json.JSONDecodeError as error:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "--args-json must contain valid JSON.",
            details={"line": error.lineno, "column": error.colno},
        ) from error
    if not isinstance(value, dict):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "--args-json must contain a JSON object.",
        )
    if namespace.plugin_args:
        value["_"] = list(namespace.plugin_args)
    return value


def invoke_request(
    repository_root: Path,
    *,
    backend: str,
    plugin: str,
    command: str,
    args: dict[str, Any],
    request_id: str,
):
    from dcs_harness_runtime.resident import CapabilityRuntime
    from dcs_harness_runtime.result import ErrorCode, HarnessError
    from dcs_harness_runtime.server_client import ServerClient

    def dispatch_direct():
        with CapabilityRuntime(repository_root, mode="direct") as runtime:
            return runtime.dispatch(
                plugin, command, args, request_id=request_id
            )

    if backend == "direct":
        return dispatch_direct()

    client = ServerClient(repository_root)
    if backend == "server":
        return client.invoke(
            plugin, command, args, request_id=request_id
        )

    try:
        return client.invoke(
            plugin, command, args, request_id=request_id
        )
    except HarnessError as error:
        if error.code is not ErrorCode.SERVER_UNAVAILABLE:
            raise
        return dispatch_direct()


def main(argv: Sequence[str] | None = None) -> int:
    repository_root = discover_repository_root()
    _ensure_runtime_python(repository_root)

    from dcs_harness_runtime.result import ErrorCode, HarnessError, ResultEnvelope

    request_id = uuid.uuid4().hex
    plugin = ""
    command = ""
    try:
        namespace = parse_request(argv)
        plugin = namespace.plugin or ""
        command = namespace.command or ""
        if plugin == "serve" and not command and not namespace.plugin_args:
            from dcs_harness_runtime.server import run_server

            return run_server(repository_root, port=namespace.port)
        if not plugin or not command:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "A plugin name and command are required.",
            )
        plugin_args = _parse_plugin_args(namespace)
        result = invoke_request(
            repository_root,
            backend=namespace.backend,
            plugin=plugin,
            command=command,
            args=plugin_args,
            request_id=request_id,
        )
        result.meta["backend_requested"] = namespace.backend
    except HarnessError as error:
        result = ResultEnvelope.failure(
            request_id=request_id,
            plugin=plugin,
            command=command,
            error=error,
            meta={
                "backend": (
                    namespace.backend if "namespace" in locals() else "none"
                ),
                "backend_requested": (
                    namespace.backend if "namespace" in locals() else None
                ),
                "duration_ms": 0.0,
            },
        )
    except Exception as error:
        result = ResultEnvelope.failure(
            request_id=request_id,
            plugin=plugin,
            command=command,
            error=HarnessError(
                ErrorCode.INTERNAL_ERROR,
                "CLI invocation failed unexpectedly.",
                details={"exception_type": type(error).__name__},
            ),
            meta={"backend": "none", "duration_ms": 0.0},
        )

    print(result.to_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
