"""Canonical invocation boundary for the capability runtime."""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Mapping

from .logging_utils import LifecycleLogger
from .result import ErrorCode, HarnessError, ResultEnvelope

if TYPE_CHECKING:
    from .resident import CapabilityRuntime


class Dispatcher:
    def __init__(
        self,
        runtime: "CapabilityRuntime",
        *,
        lifecycle_logger: LifecycleLogger | None = None,
    ) -> None:
        self.runtime = runtime
        self.repository_root = runtime.repository_root
        self.backend = runtime.backend
        self.lifecycle_logger = lifecycle_logger or LifecycleLogger(
            self.repository_root / "runtime" / "logs" / "calls.jsonl"
        )

    def dispatch(
        self,
        plugin: str,
        command: str,
        args: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> ResultEnvelope:
        request_id = request_id or uuid.uuid4().hex
        started = time.perf_counter()
        started_at = time.time()
        plugin_source: str | None = None
        plugin_load = "not_loaded"
        plugin_runtime: str | None = None
        error_code: str | None = None

        try:
            if not command:
                raise HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "A plugin command is required.",
                )
            request_args = dict(args or {})
            loaded, plugin_load = self.runtime.prepare_plugin(plugin)
            plugin_source = loaded.spec.source.value
            plugin_runtime = loaded.runtime.value
            data = loaded.invoke(self.runtime.context, command, request_args)
            try:
                json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError) as error:
                raise HarnessError(
                    ErrorCode.INTERNAL_ERROR,
                    f"Plugin {plugin!r} returned non-JSON-serializable data.",
                    details={"exception_type": type(error).__name__},
                ) from error
            result_error: HarnessError | None = None
        except HarnessError as error:
            data = None
            result_error = error
            error_code = error.code.value
        except Exception as error:
            data = None
            result_error = HarnessError(
                ErrorCode.INTERNAL_ERROR,
                f"Plugin {plugin!r} failed unexpectedly.",
                details={"exception_type": type(error).__name__},
            )
            error_code = result_error.code.value

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        finished_at = time.time()
        meta: dict[str, Any] = {
            "backend": self.backend,
            "plugin_source": plugin_source,
            "plugin_load": plugin_load,
            "plugin_runtime": plugin_runtime,
            "duration_ms": duration_ms,
        }
        if result_error is None:
            envelope = ResultEnvelope.success(
                request_id=request_id,
                plugin=plugin,
                command=command,
                data=data,
                meta=meta,
            )
        else:
            envelope = ResultEnvelope.failure(
                request_id=request_id,
                plugin=plugin,
                command=command,
                error=result_error,
                meta=meta,
            )

        logged = self.lifecycle_logger.write(
            {
                "request_id": request_id,
                "timestamp": started_at,
                "started_at": started_at,
                "finished_at": finished_at,
                "backend": self.backend,
                "plugin": plugin,
                "command": command,
                "plugin_source": plugin_source,
                "plugin_load": plugin_load,
                "plugin_runtime": plugin_runtime,
                "duration_ms": duration_ms,
                "status": "ok" if envelope.ok else "error",
                "error_code": error_code,
            }
        )
        if not logged:
            envelope.meta["lifecycle_log"] = "failed"
        return envelope
