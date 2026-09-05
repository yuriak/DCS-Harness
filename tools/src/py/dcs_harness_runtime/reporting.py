"""Small helpers shared by bounded capability status reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .result import ErrorCode, HarnessError


def age_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return round(max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds()), 3)


def error_value(error: HarnessError) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": error.code.value,
        "message": error.message,
    }
    if error.details:
        value["details"] = dict(error.details)
    return value


def unavailable_error(error: Exception, message: str) -> dict[str, Any]:
    if isinstance(error, HarnessError):
        return error_value(error)
    return {
        "code": ErrorCode.CAPABILITY_UNAVAILABLE.value,
        "message": message,
        "details": {"exception_type": type(error).__name__},
    }
