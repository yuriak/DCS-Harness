"""Resident byte-preserving mirrors for current DCS process log epochs."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .logging_utils import LifecycleLogger
from .result import ErrorCode, HarnessError


DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_TAIL_LINES = 100
MAX_TAIL_LINES = 1000
DEFAULT_SEARCH_LIMIT = 100
MAX_SEARCH_LIMIT = 1000
COPY_CHUNK_SIZE = 64 * 1024


class LogFollower:
    def __init__(
        self,
        name: str,
        source_path: Path | None,
        mirror_root: Path,
        repository_root: Path,
    ) -> None:
        self.name = name
        self.source_path = source_path.resolve() if source_path else None
        self.mirror_root = mirror_root.resolve()
        self.repository_root = repository_root.resolve()
        self._lock = threading.RLock()
        self._state = "missing"
        self._mirror_path: Path | None = None
        self._source_identity: tuple[int, int] | None = None
        self._offset = 0
        self._last_update_at: str | None = None
        self._last_error: dict[str, str] | None = None
        self._resume_allowed = True

    def poll(self) -> str | None:
        with self._lock:
            if self.source_path is None:
                return None
            try:
                stat = self.source_path.stat()
            except FileNotFoundError:
                return self._mark_missing()
            except OSError as error:
                return self._mark_error(error)

            identity = (int(stat.st_dev), int(stat.st_ino))
            rotate = (
                self._mirror_path is None
                or self._source_identity != identity
                or stat.st_size < self._offset
            )
            try:
                if rotate:
                    self._begin_epoch(identity)
                appended = self._copy_available()
            except OSError as error:
                return self._mark_error(error)
            self._state = "following"
            self._last_error = None
            return "epoch" if rotate else ("append" if appended else None)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "source_path": (
                    str(self.source_path) if self.source_path is not None else None
                ),
                "mirror_path": (
                    self._display_path(self._mirror_path)
                    if self._mirror_path is not None
                    else None
                ),
                "offset": self._offset,
                "last_update_at": self._last_update_at,
                "last_error": dict(self._last_error) if self._last_error else None,
            }

    def tail(self, lines: int) -> list[str]:
        mirror = self._query_path()
        values: deque[str] = deque(maxlen=lines)
        try:
            with mirror.open("rb") as stream:
                for raw_line in stream:
                    values.append(self._decode_line(raw_line))
        except OSError as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                f"Current {self.name} log mirror is unavailable.",
                details={"exception_type": type(error).__name__},
            ) from error
        return list(values)

    def search(self, query: str, limit: int) -> list[str]:
        mirror = self._query_path()
        matches: deque[str] = deque(maxlen=limit)
        try:
            with mirror.open("rb") as stream:
                for raw_line in stream:
                    line = self._decode_line(raw_line)
                    if query in line:
                        matches.append(line)
        except OSError as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                f"Current {self.name} log mirror is unavailable.",
                details={"exception_type": type(error).__name__},
            ) from error
        return list(matches)

    def _begin_epoch(self, identity: tuple[int, int]) -> None:
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        mirror = self._resume_mirror() if self._resume_allowed else None
        if mirror is None:
            mirror = self._new_mirror_path()
            mirror.touch(exist_ok=False)
        self._mirror_path = mirror
        self._source_identity = identity
        self._offset = mirror.stat().st_size
        self._last_update_at = None
        self._resume_allowed = False

    def _resume_mirror(self) -> Path | None:
        candidates = list(self.mirror_root.glob(f"{self.name}-*.log"))
        if not candidates or self.source_path is None:
            return None
        candidate = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        try:
            if candidate.stat().st_size > self.source_path.stat().st_size:
                return None
            with candidate.open("rb") as mirror, self.source_path.open("rb") as source:
                while True:
                    expected = mirror.read(COPY_CHUNK_SIZE)
                    if not expected:
                        return candidate
                    if source.read(len(expected)) != expected:
                        return None
        except OSError:
            return None

    def _new_mirror_path(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        candidate = self.mirror_root / f"{self.name}-{timestamp}.log"
        suffix = 2
        while candidate.exists():
            candidate = self.mirror_root / f"{self.name}-{timestamp}-{suffix}.log"
            suffix += 1
        return candidate

    def _copy_available(self) -> bool:
        assert self.source_path is not None
        assert self._mirror_path is not None
        appended = False
        with self.source_path.open("rb") as source, self._mirror_path.open(
            "ab"
        ) as mirror:
            source.seek(self._offset)
            while True:
                chunk = source.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                mirror.write(chunk)
                self._offset += len(chunk)
                appended = True
        if appended:
            self._last_update_at = datetime.now(timezone.utc).isoformat()
        return appended

    def _mark_missing(self) -> str | None:
        changed = self._state != "missing" or self._mirror_path is not None
        self._state = "missing"
        self._mirror_path = None
        self._source_identity = None
        self._offset = 0
        self._last_update_at = None
        self._last_error = None
        self._resume_allowed = False
        return "missing" if changed else None

    def _mark_error(self, error: OSError) -> str | None:
        value = {
            "type": type(error).__name__,
            "message": str(error),
        }
        changed = self._state != "error" or self._last_error != value
        self._state = "error"
        self._last_error = value
        return "error" if changed else None

    def _query_path(self) -> Path:
        with self._lock:
            mirror = self._mirror_path
        if mirror is None:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                f"No current {self.name} log epoch is available.",
            )
        return mirror

    def _display_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.repository_root).as_posix()

    @staticmethod
    def _decode_line(raw_line: bytes) -> str:
        return raw_line.decode("utf-8", errors="replace").rstrip("\r\n")


class DcsLogCollector:
    def __init__(
        self,
        sources: Mapping[str, Path | None],
        mirror_root: Path,
        repository_root: Path,
        logger: LifecycleLogger,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self.logger = logger
        self.poll_interval = max(poll_interval, 0.01)
        self.followers = {
            name: LogFollower(name, path, mirror_root, repository_root)
            for name, path in sources.items()
        }
        self._lock = threading.RLock()
        self._collector = "starting"

    def run(self, stop_event: threading.Event) -> None:
        with self._lock:
            self._collector = "running"
        try:
            while not stop_event.is_set():
                for name, follower in self.followers.items():
                    transition = follower.poll()
                    if transition and transition != "append":
                        self._log("log_source_" + transition, name)
                if stop_event.wait(self.poll_interval):
                    break
        finally:
            with self._lock:
                self._collector = "stopped"
            self._log("log_collector_stop")

    def status(self) -> dict[str, Any]:
        with self._lock:
            collector = self._collector
        return {
            "collector": collector,
            "sources": {
                name: follower.status()
                for name, follower in self.followers.items()
            },
        }

    def follower(self, source: str) -> LogFollower:
        try:
            return self.followers[source]
        except KeyError as error:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Unknown DCS log source.",
                details={"source": source, "allowed": sorted(self.followers)},
            ) from error

    def _log(self, event: str, source: str | None = None) -> None:
        self.logger.write(
            {
                "timestamp": time.time(),
                "source": "logs",
                "event": event,
                **({"log_source": source} if source else {}),
            }
        )
