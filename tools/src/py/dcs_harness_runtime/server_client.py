"""Loopback HTTP client for the resident DCS-Harness server."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .result import ErrorCode, HarnessError, ResultEnvelope


SERVER_API_VERSION = 1
LOOPBACK_HOST = "127.0.0.1"


@dataclass(frozen=True)
class ServerState:
    pid: int
    host: str
    port: int
    started_at: str
    api_version: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ServerState":
        try:
            state = cls(
                pid=int(value["pid"]),
                host=str(value["host"]),
                port=int(value["port"]),
                started_at=str(value["started_at"]),
                api_version=int(value["api_version"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server state is invalid.",
            ) from error
        if state.host != LOOPBACK_HOST or not (1 <= state.port <= 65535):
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server state does not identify an allowed loopback endpoint.",
            )
        if state.api_version != SERVER_API_VERSION:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server API version is incompatible.",
                details={
                    "server_api_version": state.api_version,
                    "supported_api_version": SERVER_API_VERSION,
                },
            )
        return state


class ServerClient:
    def __init__(self, repository_root: Path, *, timeout: float = 0.25) -> None:
        self.state_path = repository_root.resolve() / "runtime" / "server.json"
        self.timeout = timeout

    def load_state(self) -> ServerState:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server state is unavailable.",
                details={"state_path": str(self.state_path)},
            ) from error
        if not isinstance(value, dict):
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server state must be a JSON object.",
            )
        return ServerState.from_dict(value)

    @staticmethod
    def _url(state: ServerState, path: str) -> str:
        return f"http://{state.host}:{state.port}{path}"

    def _request(
        self,
        state: ServerState,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = Request(
            self._url(state, path), data=data, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server is unreachable.",
                details={"exception_type": type(error).__name__},
            ) from error
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server returned invalid JSON.",
            ) from error
        return value

    def health(self, state: ServerState | None = None) -> ServerState:
        state = state or self.load_state()
        value = self._request(state, "/health")
        if not isinstance(value, dict) or not value.get("ok"):
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server health check failed.",
            )
        try:
            matches_state = (
                int(value["api_version"]) == state.api_version
                and int(value["pid"]) == state.pid
                and str(value["host"]) == state.host
                and int(value["port"]) == state.port
            )
        except (KeyError, TypeError, ValueError):
            matches_state = False
        if not matches_state:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server health response does not match its state file.",
            )
        return state

    def invoke(
        self,
        plugin: str,
        command: str,
        args: Mapping[str, Any],
        *,
        request_id: str,
        state: ServerState | None = None,
    ) -> ResultEnvelope:
        state = self.health(state)
        value = self._request(
            state,
            "/invoke",
            payload={
                "plugin": plugin,
                "command": command,
                "args": dict(args),
                "request_id": request_id,
            },
        )
        if not isinstance(value, dict):
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server returned an invalid result envelope.",
            )
        try:
            return ResultEnvelope.from_dict(value)
        except (KeyError, TypeError, ValueError) as error:
            raise HarnessError(
                ErrorCode.SERVER_UNAVAILABLE,
                "Resident server returned an invalid result envelope.",
            ) from error
