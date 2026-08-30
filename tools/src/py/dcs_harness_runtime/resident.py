"""Authoritative capability runtime and resident plugin lifecycle."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .background import BackgroundTarget, BackgroundTask, BackgroundTaskManager
from .context import Context
from .dispatcher import Dispatcher
from .logging_utils import LifecycleLogger
from .plugin_api import LoadedPlugin, PluginCache, PluginResolver, PluginRuntimeKind
from .result import ErrorCode, HarnessError, ResultEnvelope


AUTOSTART_BUILTINS: tuple[str, ...] = ("events", "logs", "telemetry")
DEFAULT_TASK_JOIN_TIMEOUT = 5.0


@dataclass
class ResidentPluginInstance:
    loaded: LoadedPlugin
    state: Any = field(default=None, repr=False)
    lifecycle_state: str = "starting"
    started_at: float | None = None
    stopped_at: float | None = None
    last_error: dict[str, str] | None = None


class PluginRuntimeHandle:
    """Plugin-scoped access to owned state and background tasks."""

    def __init__(self, runtime: "CapabilityRuntime", plugin: str) -> None:
        self._runtime = runtime
        self.plugin = plugin

    @property
    def state(self) -> Any:
        return self._runtime._instance(self.plugin).state

    @state.setter
    def state(self, value: Any) -> None:
        self._runtime._instance(self.plugin).state = value

    @property
    def stop_event(self) -> threading.Event:
        return self._runtime.stop_event

    def start_background(
        self, name: str, target: BackgroundTarget
    ) -> BackgroundTask:
        return self._runtime.background.start(self.plugin, name, target)

    def task_status(self) -> dict[str, dict[str, Any]]:
        return self._runtime.background.status(self.plugin)


class CapabilityRuntime:
    """Owns Context, plugin loading, dispatch, state, tasks, and shutdown."""

    def __init__(self, repository_root: Path, *, mode: str) -> None:
        if mode not in {"direct", "resident"}:
            raise ValueError(f"Unsupported runtime mode: {mode}")
        self.repository_root = repository_root.resolve()
        self.mode = mode
        self.backend = "server" if mode == "resident" else "direct"
        self.context = Context.load(self.repository_root)
        self.context.runtime = self
        self.resolver = PluginResolver(self.repository_root)
        self.cache = PluginCache(self.resolver)
        self.call_logger = LifecycleLogger(
            self.repository_root / "runtime" / "logs" / "calls.jsonl"
        )
        self.runtime_logger = LifecycleLogger(
            self.repository_root / "runtime" / "logs" / "runtime.jsonl"
        )
        self.background = BackgroundTaskManager(self.runtime_logger)
        self.stop_event = threading.Event()
        self.dispatcher = Dispatcher(self, lifecycle_logger=self.call_logger)
        self._plugins: dict[str, ResidentPluginInstance] = {}
        self._start_order: list[str] = []
        self._lock = threading.RLock()
        self._lifecycle_state = "running"

    def dispatch(
        self,
        plugin: str,
        command: str,
        args: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> ResultEnvelope:
        return self.dispatcher.dispatch(
            plugin, command, args, request_id=request_id
        )

    def prepare_plugin(self, name: str) -> tuple[LoadedPlugin, str]:
        spec = self.resolver.resolve(name)
        loaded, load_status = self.cache.load(spec)
        if loaded.runtime is PluginRuntimeKind.RESIDENT:
            if self.mode != "resident":
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    f"Resident plugin {name!r} requires the resident server backend.",
                )
            self._ensure_started(loaded)
        return loaded, load_status

    def _ensure_started(self, loaded: LoadedPlugin) -> ResidentPluginInstance:
        name = loaded.spec.name
        with self._lock:
            existing = self._plugins.get(name)
            if existing is not None:
                if existing.lifecycle_state == "failed":
                    raise HarnessError(
                        ErrorCode.CAPABILITY_UNAVAILABLE,
                        f"Resident plugin {name!r} failed to start.",
                        details={"last_error": existing.last_error},
                    )
                return existing
            if self._lifecycle_state != "running":
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "The capability runtime is not accepting plugin starts.",
                )
            instance = ResidentPluginInstance(loaded=loaded)
            self._plugins[name] = instance
            self.cache.mark_immutable(loaded.spec)
            handle = PluginRuntimeHandle(self, name)
            self._log("plugin_starting", name)
            try:
                if loaded.start is not None:
                    returned_state = loaded.start(self.context, handle)
                    if returned_state is not None:
                        instance.state = returned_state
            except Exception as error:
                instance.lifecycle_state = "failed"
                instance.last_error = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
                self.background.signal_plugin(name)
                try:
                    if loaded.stop is not None:
                        loaded.stop(self.context, handle)
                except Exception as cleanup_error:
                    self._log(
                        "plugin_start_cleanup_failed",
                        name,
                        {"type": type(cleanup_error).__name__},
                    )
                self.background.join_plugin(name, DEFAULT_TASK_JOIN_TIMEOUT)
                self._log("plugin_start_failed", name, instance.last_error)
                if isinstance(error, HarnessError):
                    raise
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    f"Resident plugin {name!r} failed to start.",
                    details={"exception_type": type(error).__name__},
                ) from error
            instance.lifecycle_state = "running"
            instance.started_at = time.time()
            self._start_order.append(name)
            self._log("plugin_started", name)
            return instance

    def _instance(self, name: str) -> ResidentPluginInstance:
        with self._lock:
            instance = self._plugins.get(name)
            if instance is None:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    f"Resident plugin {name!r} has not been started.",
                )
            return instance

    def plugin_handle(self, name: str) -> PluginRuntimeHandle:
        self._instance(name)
        return PluginRuntimeHandle(self, name)

    def describe_plugin(self, name: str) -> tuple[dict[str, Any], str]:
        spec = self.resolver.resolve(name)
        return self.cache.describe(spec)

    def validate_plugin(self, value: str) -> tuple[LoadedPlugin, str]:
        spec = self.resolver.resolve_name_or_path(value)
        return self.cache.load(spec)

    def autostart(self, names: Sequence[str] = AUTOSTART_BUILTINS) -> None:
        if self.mode != "resident":
            return
        for name in names:
            loaded, _ = self.cache.load(self.resolver.resolve(name))
            if not loaded.autostart:
                raise HarnessError(
                    ErrorCode.PLUGIN_API_INCOMPATIBLE,
                    f"Autostart plugin {name!r} must declare PLUGIN_AUTOSTART = True.",
                )
            if loaded.runtime is not PluginRuntimeKind.RESIDENT:
                raise HarnessError(
                    ErrorCode.PLUGIN_API_INCOMPATIBLE,
                    f"Autostart plugin {name!r} must be resident.",
                )
            self._ensure_started(loaded)

    def status(self) -> dict[str, Any]:
        with self._lock:
            plugins = {
                name: {
                    "runtime": instance.loaded.runtime.value,
                    "state": instance.lifecycle_state,
                    "started_at": instance.started_at,
                    "stopped_at": instance.stopped_at,
                    "last_error": (
                        dict(instance.last_error) if instance.last_error else None
                    ),
                    "background_tasks": self.background.status(name),
                }
                for name, instance in self._plugins.items()
            }
            return {
                "mode": self.mode,
                "state": self._lifecycle_state,
                "plugins": plugins,
            }

    def close(self) -> None:
        with self._lock:
            if self._lifecycle_state in {"stopping", "stopped"}:
                return
            self._lifecycle_state = "stopping"
            names = list(reversed(self._start_order))
            self.stop_event.set()
            self.background.stop_accepting()
            for name in names:
                self.background.signal_plugin(name)

        for name in names:
            instance = self._instance(name)
            handle = PluginRuntimeHandle(self, name)
            try:
                if instance.loaded.stop is not None:
                    instance.loaded.stop(self.context, handle)
            except Exception as error:
                instance.last_error = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
                self._log("plugin_stop_failed", name, instance.last_error)
            self.background.join_plugin(name, DEFAULT_TASK_JOIN_TIMEOUT)
            instance.lifecycle_state = "stopped"
            instance.stopped_at = time.time()
            self._log("plugin_stopped", name)

        self.context.close()
        with self._lock:
            self._lifecycle_state = "stopped"

    def _log(
        self,
        event: str,
        plugin: str,
        error: Mapping[str, str] | None = None,
    ) -> None:
        self.runtime_logger.write(
            {
                "timestamp": time.time(),
                "source": "resident_runtime",
                "event": event,
                "plugin": plugin,
                "error_type": error.get("type") if error else None,
            }
        )

    def __enter__(self) -> "CapabilityRuntime":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
