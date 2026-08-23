"""Setup support for DCS-gRPC inspection and binding generation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    check_id: str
    status: str
    message: str


@dataclass(frozen=True)
class GrpcInspection:
    diagnostics: tuple[Diagnostic, ...]
    installed: bool
    installation_dir: Path | None
    config_file: Path
    version: str | None
    host: str
    port: int
    eval_enabled: bool
    autostart: bool
    proto_source: Path
    proto_source_kind: str


def _strip_lua_comments(content: str) -> str:
    content = re.sub(r"--\[\[.*?\]\]", "", content, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", content)


def _read_version(version_file: Path) -> str | None:
    try:
        content = _strip_lua_comments(version_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    match = re.search(r"\bGRPC\.version\s*=\s*['\"]([^'\"]+)['\"]", content)
    return match.group(1) if match else None


def _find_plugin_dir(saved_games_dir: Path) -> Path:
    candidates = (
        saved_games_dir / "Mods" / "tech" / "DCS-gRPC",
        saved_games_dir / "Mods" / "Tech" / "DCS-gRPC",
    )
    return next((path for path in candidates if path.is_dir()), candidates[0])


def _parse_config(
    config_file: Path,
) -> tuple[str, int, bool, bool, list[Diagnostic]]:
    host = "127.0.0.1"
    port = 50051
    eval_enabled = False
    autostart = False
    diagnostics: list[Diagnostic] = []

    if not config_file.is_file():
        diagnostics.append(
            Diagnostic(
                "grpc_config",
                "ok",
                "Optional dcs-grpc.lua config is absent; upstream defaults apply.",
            )
        )
        return host, port, eval_enabled, autostart, diagnostics

    try:
        content = _strip_lua_comments(config_file.read_text(encoding="utf-8"))
    except OSError as error:
        diagnostics.append(
            Diagnostic(
                "grpc_config",
                "error",
                f"Could not read DCS-gRPC config {config_file}: {error}",
            )
        )
        return host, port, eval_enabled, autostart, diagnostics

    host_match = re.search(
        r"(?m)^\s*(?:GRPC\.)?host\s*=\s*['\"]([^'\"]+)['\"]", content
    )
    port_match = re.search(r"(?m)^\s*(?:GRPC\.)?port\s*=\s*(\d+)\b", content)
    eval_match = re.search(
        r"(?mi)^\s*(?:GRPC\.)?evalEnabled\s*=\s*(true|false)\b", content
    )
    autostart_match = re.search(
        r"(?mi)^\s*(?:GRPC\.)?autostart\s*=\s*(true|false)\b", content
    )

    if host_match:
        host = host_match.group(1)
    elif re.search(r"(?m)^\s*(?:GRPC\.)?host\s*=", content):
        diagnostics.append(
            Diagnostic("grpc_config_host", "warning", "Could not parse configured gRPC host.")
        )

    if port_match:
        port = int(port_match.group(1))
        if not 1 <= port <= 65535:
            diagnostics.append(
                Diagnostic(
                    "grpc_config_port",
                    "error",
                    f"Configured gRPC port is outside 1-65535: {port}",
                )
            )
    elif re.search(r"(?m)^\s*(?:GRPC\.)?port\s*=", content):
        diagnostics.append(
            Diagnostic("grpc_config_port", "error", "Could not parse configured gRPC port.")
        )

    if eval_match:
        eval_enabled = eval_match.group(1).lower() == "true"
    elif re.search(r"(?mi)^\s*(?:GRPC\.)?evalEnabled\s*=", content):
        diagnostics.append(
            Diagnostic(
                "grpc_config_eval",
                "warning",
                "Could not parse configured evalEnabled value.",
            )
        )

    if autostart_match:
        autostart = autostart_match.group(1).lower() == "true"

    diagnostics.append(
        Diagnostic(
            "grpc_config",
            "ok",
            f"DCS-gRPC config: host={host}, port={port}, "
            f"evalEnabled={str(eval_enabled).lower()}, autostart={str(autostart).lower()}.",
        )
    )
    return host, port, eval_enabled, autostart, diagnostics


def _check_mission_scripting(mission_scripting_file: Path) -> list[Diagnostic]:
    try:
        content = _strip_lua_comments(
            mission_scripting_file.read_text(encoding="utf-8", errors="replace")
        )
    except OSError as error:
        return [
            Diagnostic(
                "grpc_mission_hook",
                "error",
                f"Could not read MissionScripting.lua: {error}",
            )
        ]

    hook_match = re.search(
        r"dofile\s*\([^\n]*Scripts[\\/]DCS-gRPC[\\/]grpc-mission\.lua",
        content,
        flags=re.IGNORECASE,
    )
    if not hook_match:
        return [
            Diagnostic(
                "grpc_mission_hook",
                "error",
                "DCS-gRPC mission hook is missing from MissionScripting.lua; setup will not edit it.",
            )
        ]

    sanitize_match = re.search(
        r"sanitizeModule\s*\(\s*['\"](?:os|io|lfs)['\"]\s*\)",
        content,
        flags=re.IGNORECASE,
    )
    if sanitize_match and hook_match.start() > sanitize_match.start():
        return [
            Diagnostic(
                "grpc_mission_hook",
                "error",
                "DCS-gRPC mission hook appears after sanitizeModule calls; move it before sanitization manually.",
            )
        ]
    if not sanitize_match:
        return [
            Diagnostic(
                "grpc_mission_hook",
                "warning",
                "DCS-gRPC mission hook exists, but sanitize ordering could not be confirmed.",
            )
        ]
    return [
        Diagnostic(
            "grpc_mission_hook",
            "ok",
            "DCS-gRPC mission hook exists before MissionScripting.lua sanitization.",
        )
    ]


def _select_proto_source(
    repository_root: Path, saved_games_dir: Path
) -> tuple[Path, str]:
    installed_candidates = (
        saved_games_dir / "Docs" / "DCS-gRPC" / "protos",
        saved_games_dir / "Tools" / "DCS-gRPC" / "protos",
        saved_games_dir / "Mods" / "tech" / "DCS-gRPC" / "protos",
        saved_games_dir / "Mods" / "Tech" / "DCS-gRPC" / "protos",
        saved_games_dir / "Scripts" / "DCS-gRPC" / "protos",
    )
    for candidate in installed_candidates:
        if (candidate / "dcs" / "dcs.proto").is_file():
            return candidate, "installed"
    return repository_root / "third_party" / "dcs-grpc" / "protos", "submodule"


def inspect_dcs_grpc(
    repository_root: Path,
    dcs_install_dir: Path,
    saved_games_dir: Path,
) -> GrpcInspection:
    diagnostics: list[Diagnostic] = []
    lua_dir = saved_games_dir / "Scripts" / "DCS-gRPC"
    hook_file = saved_games_dir / "Scripts" / "Hooks" / "DCS-gRPC.lua"
    plugin_dir = _find_plugin_dir(saved_games_dir)
    config_file = saved_games_dir / "Config" / "dcs-grpc.lua"

    required_lua_files = ("grpc-mission.lua", "grpc-hook.lua", "grpc.lua")
    missing_lua_files = [name for name in required_lua_files if not (lua_dir / name).is_file()]
    if missing_lua_files:
        diagnostics.append(
            Diagnostic(
                "grpc_lua_install",
                "error",
                f"DCS-gRPC Lua installation is missing: {', '.join(missing_lua_files)} in {lua_dir}",
            )
        )
    else:
        diagnostics.append(
            Diagnostic("grpc_lua_install", "ok", f"Found DCS-gRPC Lua files in {lua_dir}.")
        )

    if hook_file.is_file():
        diagnostics.append(
            Diagnostic("grpc_hook_install", "ok", f"Found DCS-gRPC hook loader: {hook_file}")
        )
    else:
        diagnostics.append(
            Diagnostic(
                "grpc_hook_install",
                "error",
                f"DCS-gRPC hook loader is missing: {hook_file}",
            )
        )

    dlls = list(plugin_dir.rglob("dcs_grpc*.dll")) if plugin_dir.is_dir() else []
    if dlls:
        diagnostics.append(
            Diagnostic(
                "grpc_native_install",
                "ok",
                f"Found DCS-gRPC native plugin under {plugin_dir}.",
            )
        )
    else:
        diagnostics.append(
            Diagnostic(
                "grpc_native_install",
                "error",
                f"No dcs_grpc DLL found under {plugin_dir}.",
            )
        )

    version = _read_version(lua_dir / "version.lua")
    if version:
        diagnostics.append(
            Diagnostic("grpc_version", "ok", f"Installed DCS-gRPC version is {version}.")
        )
    else:
        diagnostics.append(
            Diagnostic(
                "grpc_version",
                "warning",
                f"Could not determine DCS-gRPC version from {lua_dir / 'version.lua'}.",
            )
        )

    diagnostics.extend(
        _check_mission_scripting(dcs_install_dir / "Scripts" / "MissionScripting.lua")
    )
    host, port, eval_enabled, autostart, config_diagnostics = _parse_config(config_file)
    diagnostics.extend(config_diagnostics)

    grpc_log = saved_games_dir / "Logs" / "grpc.log"
    if grpc_log.is_file():
        diagnostics.append(
            Diagnostic("grpc_log", "ok", f"Found DCS-gRPC log: {grpc_log}")
        )
    else:
        diagnostics.append(
            Diagnostic(
                "grpc_log",
                "not_tested",
                f"grpc.log is not present at {grpc_log}; DCS-gRPC may not have run yet.",
            )
        )

    proto_source, proto_source_kind = _select_proto_source(
        repository_root, saved_games_dir
    )
    if not (proto_source / "dcs" / "dcs.proto").is_file():
        diagnostics.append(
            Diagnostic(
                "grpc_proto_source",
                "error",
                f"No usable DCS-gRPC proto source found at {proto_source}.",
            )
        )
    else:
        diagnostics.append(
            Diagnostic(
                "grpc_proto_source",
                "ok",
                f"Using {proto_source_kind} DCS-gRPC proto source: {proto_source}",
            )
        )

    upstream_version = _read_version(
        repository_root / "third_party" / "dcs-grpc" / "lua" / "DCS-gRPC" / "version.lua"
    )
    if (
        proto_source_kind == "submodule"
        and version
        and upstream_version
        and version != upstream_version
    ):
        diagnostics.append(
            Diagnostic(
                "grpc_proto_version_match",
                "warning",
                f"Installed DCS-gRPC {version} differs from pinned proto version {upstream_version}.",
            )
        )

    installed = not any(item.status == "error" for item in diagnostics)
    return GrpcInspection(
        diagnostics=tuple(diagnostics),
        installed=installed,
        installation_dir=plugin_dir if plugin_dir.is_dir() else None,
        config_file=config_file,
        version=version,
        host=host,
        port=port,
        eval_enabled=eval_enabled,
        autostart=autostart,
        proto_source=proto_source,
        proto_source_kind=proto_source_kind,
    )


def _proto_fingerprint(proto_source: Path, python_executable: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"dcs-grpc-python-package-v1\0")
    for proto_file in sorted(proto_source.rglob("*.proto")):
        digest.update(proto_file.relative_to(proto_source).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(proto_file.read_bytes())
        digest.update(b"\0")
    version_query = subprocess.run(
        [
            str(python_executable),
            "-c",
            "import importlib.metadata as m; "
            "print(m.version('grpcio-tools')); print(m.version('protobuf'))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    digest.update(version_query.stdout.encode("utf-8"))
    return digest.hexdigest()


def _validate_generated_imports(
    python_executable: Path, output_dir: Path, proto_files: list[Path]
) -> tuple[bool, str]:
    modules: list[str] = []
    for proto_file in proto_files:
        module_base = ".".join(proto_file.with_suffix("").parts)
        modules.extend(
            (
                f"dcs_grpc.{module_base}_pb2",
                f"dcs_grpc.{module_base}_pb2_grpc",
            )
        )
    statement = (
        "import sys; "
        f"sys.path.insert(0, {str(output_dir)!r}); "
        + "; ".join(f"import {module}" for module in modules)
    )
    completed = subprocess.run(
        [str(python_executable), "-c", statement],
        check=False,
        capture_output=True,
        text=True,
    )
    detail = (completed.stderr or completed.stdout).strip()
    return completed.returncode == 0, detail


def _isolate_generated_package(package_root: Path) -> None:
    """Keep generated DCS-gRPC modules separate from pydcs's ``dcs`` package."""
    for python_file in package_root.rglob("*.py"):
        content = python_file.read_text(encoding="utf-8")
        content = re.sub(
            r"(?m)^from dcs(?P<suffix>(?:\.[A-Za-z_][A-Za-z0-9_]*)*) import ",
            r"from dcs_grpc.dcs\g<suffix> import ",
            content,
        )
        python_file.write_text(content, encoding="utf-8")

    package_dirs = {package_root}
    package_dirs.update(path.parent for path in package_root.rglob("*.py"))
    for package_dir in package_dirs:
        (package_dir / "__init__.py").touch(exist_ok=True)


def prepare_bindings(
    repository_root: Path,
    python_executable: Path,
    inspection: GrpcInspection,
    dry_run: bool,
) -> list[Diagnostic]:
    output_dir = repository_root / "runtime" / "generated" / "grpc"
    proto_files = sorted(
        path.relative_to(inspection.proto_source)
        for path in inspection.proto_source.rglob("*.proto")
    )
    if dry_run:
        return [
            Diagnostic(
                "grpc_bindings",
                "not_tested",
                f"Would compile {len(proto_files)} proto files into {output_dir}.",
            )
        ]
    if not proto_files:
        return [
            Diagnostic(
                "grpc_bindings",
                "error",
                f"No proto files found under {inspection.proto_source}.",
            )
        ]

    try:
        fingerprint = _proto_fingerprint(inspection.proto_source, python_executable)
    except OSError as error:
        return [
            Diagnostic("grpc_bindings", "error", f"Could not fingerprint proto files: {error}")
        ]

    metadata_file = output_dir / ".generation.json"
    if metadata_file.is_file():
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        imports_work, _ = _validate_generated_imports(
            python_executable, output_dir, proto_files
        )
        if metadata.get("fingerprint") == fingerprint and imports_work:
            return [
                Diagnostic(
                    "grpc_bindings",
                    "ok",
                    f"Existing gRPC Python bindings are current and importable at {output_dir}.",
                )
            ]

    generated_root = output_dir.parent
    generated_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=".grpc-build-", dir=generated_root))
    package_root = temporary_dir / "dcs_grpc"
    package_root.mkdir()
    command = [
        str(python_executable),
        "-m",
        "grpc_tools.protoc",
        f"-I{inspection.proto_source}",
        f"--python_out={package_root}",
        f"--grpc_python_out={package_root}",
        *(str(path) for path in proto_files),
    ]
    completed = subprocess.run(command, cwd=inspection.proto_source, check=False)
    if completed.returncode != 0:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        return [
            Diagnostic("grpc_bindings", "error", "grpc_tools.protoc failed to generate bindings.")
        ]

    try:
        _isolate_generated_package(package_root)
    except OSError as error:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        return [
            Diagnostic(
                "grpc_bindings",
                "error",
                f"Could not isolate generated bindings from the pydcs package: {error}",
            )
        ]

    imports_work, import_detail = _validate_generated_imports(
        python_executable, temporary_dir, proto_files
    )
    if not imports_work:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        return [
            Diagnostic(
                "grpc_bindings",
                "error",
                "Generated gRPC Python bindings failed import validation"
                + (f": {import_detail}" if import_detail else "."),
            )
        ]

    metadata = {
        "fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "installed_grpc_version": inspection.version,
        "proto_source": str(inspection.proto_source),
        "proto_source_kind": inspection.proto_source_kind,
        "python_package": "dcs_grpc",
        "proto_files": [path.as_posix() for path in proto_files],
    }
    (temporary_dir / ".generation.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if output_dir.exists():
        shutil.rmtree(output_dir)
    temporary_dir.replace(output_dir)
    return [
        Diagnostic(
            "grpc_bindings",
            "ok",
            f"Generated and verified {len(proto_files)} DCS-gRPC proto bindings at {output_dir}.",
        )
    ]
