"""Serialization and atomic output helpers for DCS-Harness setup."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def dump_yaml(data: Mapping[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, Mapping):
            lines.append(f"{prefix}{key}:")
            lines.extend(dump_yaml(value, indent + 2).rstrip("\n").splitlines())
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                lines.append(f"{' ' * (indent + 2)}- {_yaml_scalar(item)}")
        else:
            lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    atomic_write_text(path, dump_yaml(data))


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def render_setup_log(
    *,
    generated_at: str,
    setup_version: str,
    status: str,
    repository_root: Path,
    platform_data: Mapping[str, Any],
    checks: Sequence[Mapping[str, str]],
) -> str:
    lines = [
        f"generated_at: {generated_at}",
        f"setup_version: {setup_version}",
        f"status: {status}",
        f"repository: {repository_root}",
        "platform: "
        f"host={platform_data['host_os']}, agent={platform_data['agent_os']}, "
        f"wsl={str(platform_data['is_wsl']).lower()}",
        "",
    ]
    labels = {
        "ok": "OK",
        "warning": "WARN",
        "error": "FAIL",
        "not_tested": "SKIP",
    }
    for check in checks:
        lines.append(
            f"[{labels[check['status']]}] {check['id']}: {check['message']}"
        )
    return "\n".join(lines) + "\n"
