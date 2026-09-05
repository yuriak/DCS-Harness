"""Authoritative capability runtime and resident plugin lifecycle."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .background import BackgroundTarget, BackgroundTask, BackgroundTaskManager
from .context import Context
from .dispatcher import Dispatcher
from .logging_utils import LifecycleLogger
from .plugin_api import LoadedPlugin, PluginCache, PluginResolver, PluginRuntimeKind
from .result import ErrorCode, HarnessError, ResultEnvelope


AUTOSTART_BUILTINS: tuple[str, ...] = ("events", "f10", "logs", "telemetry")
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

    def fast_status(self) -> dict[str, Any]:
        """Aggregate bounded plugin reports without starting resident plugins."""
        started = time.monotonic()
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        discovery = self.resolver.discover()
        conflicts = set(discovery["conflicts"])
        names = sorted(
            set(discovery["builtin"])
            | set(discovery["runtime"])
            | conflicts
        )
        reports: dict[str, dict[str, Any]] = {}
        for name in names:
            if name in conflicts:
                reports[name] = {
                    "report_status": "conflict",
                    "error": {
                        "code": ErrorCode.PLUGIN_NAME_CONFLICT.value,
                        "message": "A runtime plugin conflicts with a built-in plugin.",
                    },
                    "duration_ms": 0.0,
                }
                continue
            reports[name] = self._fast_plugin_report(name)

        warnings = self._fast_status_warnings(reports)
        session_id = self._first_report_value(
            reports, "session_id", ("telemetry", "events", "grpc", "lua", "geo")
        )
        theatre = self._first_report_value(
            reports, "theatre", ("grpc", "lua", "geo", "telemetry", "events")
        )
        mission_time = self._first_report_value(
            reports, "mission_time", ("telemetry", "lua", "events", "grpc", "geo")
        )
        warnings.extend(self._fact_conflict_warnings(reports))
        return {
            "timestamp_utc": timestamp_utc,
            "health": (
                "unavailable"
                if self._lifecycle_state != "running"
                or (session_id is None and mission_time is None)
                else "degraded" if warnings else "ready"
            ),
            "runtime": {"mode": self.mode, "state": self._lifecycle_state},
            "session_id": session_id,
            "theatre": theatre,
            "mission_time": mission_time,
            "plugins": reports,
            "warnings": warnings,
            "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
        }

    def _fast_plugin_report(self, name: str) -> dict[str, Any]:
        started = time.monotonic()
        source = "unknown"
        loaded: LoadedPlugin | None = None
        try:
            spec = self.resolver.resolve(name)
            source = spec.source.value
            loaded, load_status = self.cache.load(spec)
            common: dict[str, Any] = {
                "source": source,
                "runtime": loaded.runtime.value,
                "autostart": loaded.autostart,
                "plugin_load": load_status,
            }
            if loaded.fast_report is None:
                common["report_status"] = "not_reportable"
                return self._finish_fast_report(common, started)

            handle: PluginRuntimeHandle | None = None
            if loaded.runtime is PluginRuntimeKind.RESIDENT:
                with self._lock:
                    instance = self._plugins.get(name)
                    if instance is None:
                        common.update(
                            report_status="not_started",
                            lifecycle_state="not_started",
                        )
                        return self._finish_fast_report(common, started)
                    common["lifecycle_state"] = instance.lifecycle_state
                    if instance.lifecycle_state != "running":
                        common["report_status"] = instance.lifecycle_state
                        if instance.last_error:
                            common["error"] = {
                                "code": ErrorCode.CAPABILITY_UNAVAILABLE.value,
                                "message": f"Resident plugin {name!r} is {instance.lifecycle_state}.",
                                "details": {"last_error": dict(instance.last_error)},
                            }
                        return self._finish_fast_report(common, started)
                    handle = PluginRuntimeHandle(self, name)

            value = loaded.fast_report(self.context, handle)
            if not isinstance(value, Mapping):
                raise HarnessError(
                    ErrorCode.PLUGIN_API_INCOMPATIBLE,
                    f"Plugin {name!r} fast_report() must return a mapping.",
                )
            data = dict(value)
            try:
                json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError) as error:
                raise HarnessError(
                    ErrorCode.PLUGIN_API_INCOMPATIBLE,
                    f"Plugin {name!r} fast_report() must return JSON-safe data.",
                    details={"exception_type": type(error).__name__},
                ) from error
            common.update(report_status="ok", data=data)
            return self._finish_fast_report(common, started)
        except HarnessError as error:
            value = {
                "source": source,
                "runtime": loaded.runtime.value if loaded else None,
                "report_status": "error",
                "error": {
                    "code": error.code.value,
                    "message": error.message,
                    **({"details": error.details} if error.details else {}),
                },
            }
            return self._finish_fast_report(value, started)
        except Exception as error:
            value = {
                "source": source,
                "runtime": loaded.runtime.value if loaded else None,
                "report_status": "error",
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": f"Plugin {name!r} fast report failed unexpectedly.",
                    "details": {"exception_type": type(error).__name__},
                },
            }
            return self._finish_fast_report(value, started)

    @staticmethod
    def _finish_fast_report(
        value: dict[str, Any], started: float
    ) -> dict[str, Any]:
        value["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        return value

    @staticmethod
    def _first_report_value(
        reports: Mapping[str, Mapping[str, Any]],
        field: str,
        preferred_plugins: Sequence[str],
    ) -> Any:
        for name in preferred_plugins:
            entry = reports.get(name)
            if not entry or entry.get("report_status") != "ok":
                continue
            data = entry.get("data")
            if isinstance(data, Mapping) and data.get(field) is not None:
                return data[field]
        return None

    @staticmethod
    def _fast_status_warnings(
        reports: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        for name, entry in reports.items():
            status = entry.get("report_status")
            if status in {"error", "failed", "conflict"}:
                warnings.append(
                    {
                        "plugin": name,
                        "code": f"PLUGIN_{str(status).upper()}",
                        "message": f"Plugin {name!r} status is {status}.",
                    }
                )
                continue
            if status == "not_started" and entry.get("autostart"):
                warnings.append(
                    {
                        "plugin": name,
                        "code": "PLUGIN_NOT_STARTED",
                        "message": f"Autostart plugin {name!r} is not running.",
                    }
                )
                continue
            data = entry.get("data")
            if (
                status == "ok"
                and isinstance(data, Mapping)
                and data.get("health") in {"degraded", "unavailable"}
            ):
                warnings.append(
                    {
                        "plugin": name,
                        "code": f"PLUGIN_{str(data['health']).upper()}",
                        "message": f"Plugin {name!r} reports {data['health']} health.",
                    }
                )
        return warnings

    @staticmethod
    def _fact_conflict_warnings(
        reports: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        facts: dict[str, list[tuple[str, Any]]] = {
            "session_id": [],
            "theatre": [],
            "mission_time": [],
        }
        for name, entry in reports.items():
            if entry.get("report_status") != "ok":
                continue
            data = entry.get("data")
            if not isinstance(data, Mapping):
                continue
            for field in facts:
                if data.get(field) is not None:
                    facts[field].append((name, data[field]))

        warnings: list[dict[str, str]] = []
        for field in ("session_id", "theatre"):
            distinct = {str(value) for _, value in facts[field]}
            if len(distinct) > 1:
                warnings.append(
                    {
                        "plugin": "runtime",
                        "code": f"CONFLICTING_{field.upper()}",
                        "message": f"Successful plugin reports disagree on {field}.",
                    }
                )
        # Event mission time is the chronology time of the newest event, not a
        # current clock observation. Compare only reports that sample current
        # state so a quiet event stream does not look inconsistent.
        mission_times = [
            float(value)
            for name, value in facts["mission_time"]
            if name in {"telemetry", "lua"}
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ]
        if mission_times and max(mission_times) - min(mission_times) > 30.0:
            warnings.append(
                {
                    "plugin": "runtime",
                    "code": "MISSION_TIME_DIVERGENCE",
                    "message": "Successful plugin mission-time observations differ by more than 30 seconds.",
                }
            )
        return warnings

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
