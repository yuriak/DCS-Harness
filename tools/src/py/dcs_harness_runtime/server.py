"""Loopback-only resident HTTP server for DCS-Harness capabilities."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from setup_support.outputs import write_json

from .resident import AUTOSTART_BUILTINS, CapabilityRuntime
from .result import ErrorCode, HarnessError, ResultEnvelope
from .server_client import LOOPBACK_HOST, SERVER_API_VERSION


MAX_REQUEST_BYTES = 1024 * 1024


class CapabilityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        repository_root: Path,
        port: int,
        autostart_plugins: Sequence[str],
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.runtime = CapabilityRuntime(self.repository_root, mode="resident")
        try:
            super().__init__((LOOPBACK_HOST, port), CapabilityRequestHandler)
        except Exception:
            self.runtime.close()
            raise
        try:
            self.runtime.autostart(autostart_plugins)
        except Exception:
            super().server_close()
            self.runtime.close()
            raise
        host, bound_port = self.server_address
        self._state = {
            "pid": os.getpid(),
            "host": host,
            "port": bound_port,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "api_version": SERVER_API_VERSION,
        }

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def server_close(self) -> None:
        try:
            self.runtime.close()
        finally:
            super().server_close()


class CapabilityRequestHandler(BaseHTTPRequestHandler):
    server: CapabilityHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_json(self, status: int, value: Mapping[str, Any]) -> None:
        body = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The client can time out or disconnect while a capability is still
            # producing its response.  That is an expected connection boundary,
            # not a server failure worth a socketserver traceback.
            return

    def _failure(
        self,
        status: int,
        error: HarnessError,
        *,
        plugin: str = "",
        command: str = "",
        request_id: str | None = None,
    ) -> None:
        envelope = ResultEnvelope.failure(
            request_id=request_id or uuid.uuid4().hex,
            plugin=plugin,
            command=command,
            error=error,
            meta={"backend": "server", "duration_ms": 0.0},
        )
        self._write_json(status, envelope.to_dict())

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            state = self.server.state
            state.update(
                {
                    "ok": True,
                    "status": "healthy",
                    "runtime": self.server.runtime.status(),
                }
            )
            self._write_json(200, state)
            return
        if path == "/plugins":
            self._write_json(
                200,
                {
                    "ok": True,
                    "plugins": self.server.runtime.resolver.discover(),
                },
            )
            return
        prefix = "/plugins/"
        if path.startswith(prefix) and len(path) > len(prefix):
            name = unquote(path[len(prefix) :])
            try:
                metadata, load_status = self.server.runtime.describe_plugin(name)
            except HarnessError as error:
                self._failure(
                    404 if error.code is ErrorCode.PLUGIN_NOT_FOUND else 400,
                    error,
                    plugin=name,
                    command="describe",
                )
                return
            self._write_json(
                200,
                {"ok": True, "plugin": metadata, "meta": {"plugin_load": load_status}},
            )
            return
        self._failure(
            404,
            HarnessError(ErrorCode.INVALID_ARGUMENT, "HTTP endpoint was not found."),
        )

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/invoke":
            self._failure(
                404,
                HarnessError(ErrorCode.INVALID_ARGUMENT, "HTTP endpoint was not found."),
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._failure(
                400,
                HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"Request body must be between 0 and {MAX_REQUEST_BYTES} bytes.",
                ),
            )
            return
        try:
            value = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._failure(
                400,
                HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Request body must be valid JSON.",
                ),
            )
            return
        if not isinstance(value, dict):
            self._failure(
                400,
                HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Request body must be a JSON object.",
                ),
            )
            return
        plugin = value.get("plugin")
        command = value.get("command")
        args = value.get("args", {})
        request_id = value.get("request_id")
        if (
            not isinstance(plugin, str)
            or not plugin
            or not isinstance(command, str)
            or not command
            or not isinstance(args, dict)
            or (request_id is not None and not isinstance(request_id, str))
        ):
            self._failure(
                400,
                HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "plugin and command must be non-empty strings, args must be an object, and request_id must be a string when supplied.",
                ),
                plugin=plugin if isinstance(plugin, str) else "",
                command=command if isinstance(command, str) else "",
                request_id=request_id if isinstance(request_id, str) else None,
            )
            return
        result = self.server.runtime.dispatch(
            plugin, command, args, request_id=request_id
        )
        self._write_json(200, result.to_dict())


class CapabilityServer:
    """Owns server state publication and graceful cleanup."""

    def __init__(
        self,
        repository_root: Path,
        *,
        port: int = 7777,
        autostart_plugins: Sequence[str] = AUTOSTART_BUILTINS,
    ) -> None:
        if not (0 <= port <= 65535):
            raise HarnessError(ErrorCode.INVALID_ARGUMENT, "Server port is invalid.")
        self.repository_root = repository_root.resolve()
        self.state_path = self.repository_root / "runtime" / "server.json"
        self.httpd = CapabilityHTTPServer(
            self.repository_root,
            port,
            autostart_plugins,
        )
        self._published_state: dict[str, Any] | None = None

    @property
    def host(self) -> str:
        return str(self.httpd.server_address[0])

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def publish_state(self) -> dict[str, Any]:
        state = self.httpd.state
        write_json(self.state_path, state)
        self._published_state = state
        return state

    def serve_forever(self) -> None:
        try:
            self.publish_state()
            self.httpd.serve_forever(poll_interval=0.1)
        finally:
            self.close()

    def shutdown(self) -> None:
        self.httpd.shutdown()

    def _remove_owned_state(self) -> None:
        if self._published_state is None:
            return
        try:
            current = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if (
            isinstance(current, dict)
            and current.get("pid") == self._published_state["pid"]
            and current.get("port") == self._published_state["port"]
        ):
            self.state_path.unlink(missing_ok=True)

    def close(self) -> None:
        self._remove_owned_state()
        self.httpd.server_close()


def run_server(repository_root: Path, *, port: int = 7777) -> int:
    server = CapabilityServer(repository_root, port=port)
    stop_requested = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        if stop_requested.is_set():
            return
        stop_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)
    print(
        f"DCS-Harness server listening on http://{server.host}:{server.port}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0
