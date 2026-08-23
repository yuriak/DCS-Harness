"""Safe repository-local Lua loading and DCS-gRPC Eval integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

from .grpc_support import DEFAULT_TIMEOUT_SECONDS, GrpcSupport
from .result import ErrorCode, HarnessError


MAX_LUA_FILE_BYTES = 1024 * 1024
LUA_FAILURE_PREFIXES = (
    "Failed to load Lua code:",
    "Failed to execute Lua code:",
)


class LuaSupport:
    def __init__(self, context: Any) -> None:
        self.context = context

    def eval(
        self, code: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua code must be a non-empty string.",
            )
        endpoint = self.context.require_grpc_client_endpoint()
        if not endpoint.eval_enabled:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS-gRPC Eval is disabled; enable evalEnabled in dcs-grpc.lua.",
                details={"environment_path": str(self.context.environment_path)},
            )

        try:
            response = GrpcSupport(self.context).call(
                "dcs.custom.v0.CustomService",
                "Eval",
                {"lua": code},
                timeout=timeout,
            )
        except HarnessError as error:
            self._translate_eval_error(error)

        encoded = response.get("json") if isinstance(response, dict) else None
        if not isinstance(encoded, str):
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC Eval returned a malformed response.",
                details={"expected_field": "json"},
            )
        try:
            result = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "DCS-gRPC Eval returned malformed JSON.",
                details={"line": error.lineno, "column": error.colno},
            ) from error
        return {"result": result}

    def eval_file(
        self, path_value: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        path = self.resolve_file(path_value)
        try:
            size = path.stat().st_size
            if size > MAX_LUA_FILE_BYTES:
                raise HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"Lua file exceeds the {MAX_LUA_FILE_BYTES}-byte limit.",
                    details={"path": str(path), "size": size},
                )
            code = path.read_text(encoding="utf-8")
        except HarnessError:
            raise
        except (OSError, UnicodeError) as error:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua file could not be read as UTF-8 text.",
                details={
                    "path": str(path),
                    "exception_type": type(error).__name__,
                },
            ) from error
        value = self.eval(code, timeout=timeout)
        value["path"] = str(path.relative_to(self.context.repository_root.resolve()))
        return value

    def resolve_file(self, path_value: str) -> Path:
        if not isinstance(path_value, str) or not path_value.strip():
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua file path must be a non-empty string.",
            )
        root = self.context.repository_root.resolve()
        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as error:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua file path could not be resolved.",
                details={"exception_type": type(error).__name__},
            ) from error

        allowed_roots = tuple(
            resolved_root
            for configured_root in (
                root / "runtime" / "workspace",
                root / "runtime" / "plugins" / "lua",
            )
            if self._is_relative_to(
                resolved_root := configured_root.resolve(), root
            )
        )
        if not any(self._is_relative_to(resolved, allowed) for allowed in allowed_roots):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua file is outside the allowed runtime directories.",
                details={
                    "allowed_roots": [
                        str(path.relative_to(root)) for path in allowed_roots
                    ]
                },
            )
        if resolved.suffix.casefold() != ".lua":
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua file must use the .lua extension.",
            )
        if not resolved.is_file():
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Lua file does not exist or is not a regular file.",
                details={"path": str(resolved)},
            )
        return resolved

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _translate_eval_error(error: HarnessError) -> NoReturn:
        details = dict(error.details or {})
        status = details.get("grpc_status")
        grpc_details = str(details.get("grpc_details", ""))
        if status == "PERMISSION_DENIED":
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS-gRPC Eval is disabled or permission was denied.",
                details={"grpc_status": status},
            ) from error
        if status == "INTERNAL" and grpc_details.startswith(LUA_FAILURE_PREFIXES):
            raise HarnessError(
                ErrorCode.LUA_EXECUTION_FAILED,
                "Lua evaluation failed.",
                details={"reason": grpc_details},
            ) from error
        raise error
