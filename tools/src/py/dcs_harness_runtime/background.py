"""Owned background threads for resident capability plugins."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .logging_utils import LifecycleLogger
from .result import ErrorCode, HarnessError


BackgroundTarget = Callable[[threading.Event], None]


@dataclass
class BackgroundTask:
    plugin: str
    name: str
    target: BackgroundTarget = field(repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    state: str = "starting"
    started_at: float | None = None
    finished_at: float | None = None
    last_error: dict[str, str] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "alive": bool(self.thread and self.thread.is_alive()),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_error": dict(self.last_error) if self.last_error else None,
        }


class BackgroundTaskManager:
    """Starts, observes, signals, and joins plugin-owned threads."""

    def __init__(self, logger: LifecycleLogger) -> None:
        self.logger = logger
        self._tasks: dict[tuple[str, str], BackgroundTask] = {}
        self._lock = threading.RLock()
        self._accepting = True

    def start(self, plugin: str, name: str, target: BackgroundTarget) -> BackgroundTask:
        if not name:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "A background task name is required.",
            )
        if not callable(target):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "A background task target must be callable.",
            )
        key = (plugin, name)
        with self._lock:
            if not self._accepting:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "The runtime is shutting down and cannot start background tasks.",
                )
            existing = self._tasks.get(key)
            if existing is not None:
                return existing
            task = BackgroundTask(plugin=plugin, name=name, target=target)
            thread = threading.Thread(
                target=self._run,
                args=(task,),
                name=f"dcs-harness:{plugin}:{name}",
                daemon=True,
            )
            task.thread = thread
            self._tasks[key] = task
            thread.start()
            return task

    def _run(self, task: BackgroundTask) -> None:
        with self._lock:
            task.state = "running"
            task.started_at = time.time()
        self._log("background_start", task)
        try:
            task.target(task.stop_event)
        except Exception as error:
            with self._lock:
                task.state = "failed"
                task.last_error = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
            self._log("background_exception", task)
        else:
            with self._lock:
                task.state = "stopped"
            self._log("background_stop", task)
        finally:
            with self._lock:
                task.finished_at = time.time()

    def _log(self, event: str, task: BackgroundTask) -> None:
        self.logger.write(
            {
                "timestamp": time.time(),
                "source": "background",
                "event": event,
                "plugin": task.plugin,
                "task": task.name,
                "state": task.state,
                "error_type": (
                    task.last_error["type"] if task.last_error else None
                ),
            }
        )

    def signal_plugin(self, plugin: str) -> None:
        with self._lock:
            tasks = [
                task for (owner, _), task in self._tasks.items() if owner == plugin
            ]
            for task in tasks:
                if task.state in {"starting", "running"}:
                    task.state = "stopping"
                task.stop_event.set()

    def join_plugin(self, plugin: str, timeout: float) -> None:
        with self._lock:
            tasks = [
                task for (owner, _), task in self._tasks.items() if owner == plugin
            ]
        deadline = time.monotonic() + max(timeout, 0.0)
        for task in tasks:
            thread = task.thread
            if thread is None:
                continue
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                with self._lock:
                    task.state = "stuck"
                self._log("background_join_timeout", task)

    def stop_accepting(self) -> None:
        with self._lock:
            self._accepting = False

    def status(self, plugin: str | None = None) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                name: task.status()
                for (owner, name), task in self._tasks.items()
                if plugin is None or owner == plugin
            }
