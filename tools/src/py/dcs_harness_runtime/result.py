"""Runtime result and error model shared by all DCS-Harness backends."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ErrorCode(str, Enum):
    PLUGIN_NOT_FOUND = "PLUGIN_NOT_FOUND"
    PLUGIN_NAME_CONFLICT = "PLUGIN_NAME_CONFLICT"
    PLUGIN_IMPORT_FAILED = "PLUGIN_IMPORT_FAILED"
    PLUGIN_API_INCOMPATIBLE = "PLUGIN_API_INCOMPATIBLE"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    SERVER_UNAVAILABLE = "SERVER_UNAVAILABLE"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    GRPC_CONNECTION_FAILED = "GRPC_CONNECTION_FAILED"
    GRPC_CALL_FAILED = "GRPC_CALL_FAILED"
    LUA_EXECUTION_FAILED = "LUA_EXECUTION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class HarnessError(Exception):
    """Expected capability error that can safely cross the CLI/API boundary."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details) if details else None


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            value["details"] = dict(self.details)
        return value


@dataclass
class ResultEnvelope:
    ok: bool
    request_id: str
    plugin: str
    command: str
    data: Any = None
    error: ErrorInfo | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        *,
        request_id: str,
        plugin: str,
        command: str,
        data: Any,
        meta: Mapping[str, Any] | None = None,
    ) -> "ResultEnvelope":
        return cls(
            ok=True,
            request_id=request_id,
            plugin=plugin,
            command=command,
            data=data,
            error=None,
            meta=dict(meta or {}),
        )

    @classmethod
    def failure(
        cls,
        *,
        request_id: str,
        plugin: str,
        command: str,
        error: HarnessError,
        meta: Mapping[str, Any] | None = None,
    ) -> "ResultEnvelope":
        return cls(
            ok=False,
            request_id=request_id,
            plugin=plugin,
            command=command,
            data=None,
            error=ErrorInfo(
                code=error.code.value,
                message=error.message,
                details=error.details,
            ),
            meta=dict(meta or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "request_id": self.request_id,
            "plugin": self.plugin,
            "command": self.command,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResultEnvelope":
        required = {"ok", "request_id", "plugin", "command", "data", "error", "meta"}
        if not required.issubset(value):
            raise ValueError("Result envelope is missing required fields")
        error_value = value["error"]
        error = None
        if error_value is not None:
            if not isinstance(error_value, Mapping):
                raise ValueError("Result envelope error must be an object or null")
            error = ErrorInfo(
                code=str(error_value["code"]),
                message=str(error_value["message"]),
                details=(
                    dict(error_value["details"])
                    if isinstance(error_value.get("details"), Mapping)
                    else None
                ),
            )
        meta_value = value["meta"]
        if not isinstance(meta_value, Mapping):
            raise ValueError("Result envelope meta must be an object")
        return cls(
            ok=bool(value["ok"]),
            request_id=str(value["request_id"]),
            plugin=str(value["plugin"]),
            command=str(value["command"]),
            data=value["data"],
            error=error,
            meta=dict(meta_value),
        )

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
