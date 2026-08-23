"""Runtime lifecycle logger that never records plugin result payloads."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping


class LifecycleLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, record: Mapping[str, Any]) -> bool:
        try:
            payload = json.dumps(
                dict(record),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(payload + "\n")
            return True
        except (OSError, TypeError, ValueError):
            return False
