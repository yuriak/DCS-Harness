#!/usr/bin/env python3
"""Pre-agent environment checks for DCS-Harness.

This bootstrap module intentionally uses only the Python standard library. It
may prepare files under this repository's runtime directory, but it never
modifies DCS or Saved Games.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence

from setup_support.dcs_grpc import GrpcInspection, inspect_dcs_grpc, prepare_bindings
from setup_support.outputs import (
    atomic_write_text,
    render_setup_log,
    write_json,
    write_yaml,
)


SETUP_VERSION = "0.7.0"
MINIMUM_PYTHON = (3, 10)
PYSOCKS_BOOTSTRAP_VERSION = "1.7.1"
REQUIRED_SUBMODULES = (
    "third_party/dcs-grpc",
    "third_party/moose",
    "third_party/mist",
    "third_party/pydcs",
    "third_party/dcs-lua-definitions",
)


class CheckStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    NOT_TESTED = "not_tested"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    message: str


@dataclass(frozen=True)
class PlatformInfo:
    host_os: str
    agent_os: str
    is_wsl: bool
    python_executable: str
    python_version: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and validate the DCS-Harness technical environment."
    )
    parser.add_argument(
        "--dcs-install-dir",
        help="DCS World installation directory.",
    )
    parser.add_argument(
        "--saved-games-dir",
        help="DCS Saved Games directory, such as Saved Games/DCS.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt for missing paths.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report setup actions without writing files or installing dependencies.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"DCS-Harness setup {SETUP_VERSION}",
    )
    return parser.parse_args(argv)


def discover_repository_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / ".gitmodules").is_file():
            return candidate

    raise RuntimeError("Could not find repository root containing .gitmodules")


def detect_platform() -> PlatformInfo:
    release = platform.release().lower()
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in release

    if sys.platform == "win32":
        agent_os = "windows"
        host_os = "windows"
    elif is_wsl:
        agent_os = "wsl"
        host_os = "windows"
    elif sys.platform.startswith("linux"):
        agent_os = "linux"
        host_os = "linux"
    elif sys.platform == "darwin":
        agent_os = "macos"
        host_os = "macos"
    else:
        agent_os = sys.platform
        host_os = platform.system().lower() or "unknown"

    return PlatformInfo(
        host_os=host_os,
        agent_os=agent_os,
        is_wsl=is_wsl,
        python_executable=sys.executable,
        python_version=platform.python_version(),
    )


def normalize_user_path(value: str, platform_info: PlatformInfo) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value.strip().strip('"')))

    if platform_info.is_wsl and re.match(r"^[A-Za-z]:[\\/]", expanded):
        wslpath = shutil.which("wslpath")
        if wslpath:
            converted = subprocess.run(
                [wslpath, "-u", expanded],
                check=False,
                capture_output=True,
                text=True,
            )
            if converted.returncode == 0 and converted.stdout.strip():
                expanded = converted.stdout.strip()

    return Path(expanded).resolve(strict=False)


def check_python_version() -> CheckResult:
    current = sys.version_info[:2]
    required = ".".join(map(str, MINIMUM_PYTHON))
    actual = platform.python_version()
    if current < MINIMUM_PYTHON:
        return CheckResult(
            "python_version",
            CheckStatus.ERROR,
            f"Python {actual} is unsupported; Python {required} or newer is required.",
        )
    return CheckResult(
        "python_version",
        CheckStatus.OK,
        f"Python {actual} at {sys.executable}",
    )


def check_submodules(repository_root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    git = shutil.which("git")

    for relative_path in REQUIRED_SUBMODULES:
        path = repository_root / relative_path
        check_id = f"submodule_{Path(relative_path).name.replace('-', '_')}"
        if not path.is_dir() or not (path / ".git").exists():
            results.append(
                CheckResult(
                    check_id,
                    CheckStatus.ERROR,
                    f"Missing submodule {relative_path}; run git submodule update --init --recursive.",
                )
            )
            continue

        revision = "revision unavailable"
        if git:
            completed = subprocess.run(
                [git, "-C", str(path), "rev-parse", "--short", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                revision = completed.stdout.strip()

        results.append(
            CheckResult(
                check_id,
                CheckStatus.OK,
                f"{relative_path} initialized at {revision}.",
            )
        )

    return results


def check_dcs_install(path: Path | None) -> list[CheckResult]:
    if path is None:
        return [
            CheckResult(
                "dcs_install_dir",
                CheckStatus.ERROR,
                "DCS installation directory was not provided.",
            )
        ]
    if not path.is_dir():
        return [
            CheckResult(
                "dcs_install_dir",
                CheckStatus.ERROR,
                f"DCS installation directory does not exist: {path}",
            )
        ]

    results = [
        CheckResult(
            "dcs_install_dir",
            CheckStatus.OK,
            f"DCS installation directory exists: {path}",
        )
    ]
    executables = (path / "bin-mt" / "DCS.exe", path / "bin" / "DCS.exe")
    existing_executables = [candidate for candidate in executables if candidate.is_file()]
    if existing_executables:
        results.append(
            CheckResult(
                "dcs_executable",
                CheckStatus.OK,
                f"Found DCS executable: {existing_executables[0]}",
            )
        )
    else:
        results.append(
            CheckResult(
                "dcs_executable",
                CheckStatus.ERROR,
                "No DCS.exe found under bin-mt or bin.",
            )
        )

    mission_scripting = path / "Scripts" / "MissionScripting.lua"
    if mission_scripting.is_file():
        results.append(
            CheckResult(
                "mission_scripting_file",
                CheckStatus.OK,
                f"Found MissionScripting.lua: {mission_scripting}",
            )
        )
    else:
        results.append(
            CheckResult(
                "mission_scripting_file",
                CheckStatus.ERROR,
                f"MissionScripting.lua not found at {mission_scripting}",
            )
        )
    return results


def check_saved_games(path: Path | None) -> list[CheckResult]:
    if path is None:
        return [
            CheckResult(
                "saved_games_dir",
                CheckStatus.ERROR,
                "DCS Saved Games directory was not provided.",
            )
        ]
    if not path.is_dir():
        return [
            CheckResult(
                "saved_games_dir",
                CheckStatus.ERROR,
                f"DCS Saved Games directory does not exist: {path}",
            )
        ]

    markers = ("Config", "Logs", "Missions", "Scripts")
    found_markers = [name for name in markers if (path / name).is_dir()]
    looks_named_for_dcs = path.name.lower().startswith("dcs")
    if not found_markers and not looks_named_for_dcs:
        return [
            CheckResult(
                "saved_games_dir",
                CheckStatus.ERROR,
                f"Directory does not look like a DCS Saved Games directory: {path}",
            )
        ]

    results = [
        CheckResult(
            "saved_games_dir",
            CheckStatus.OK,
            f"DCS Saved Games directory accepted: {path}",
        )
    ]
    logs_dir = path / "Logs"
    if logs_dir.is_dir():
        results.append(
            CheckResult(
                "dcs_logs_dir",
                CheckStatus.OK,
                f"Found DCS logs directory: {logs_dir}",
            )
        )
    else:
        results.append(
            CheckResult(
                "dcs_logs_dir",
                CheckStatus.WARNING,
                f"DCS logs directory does not exist yet: {logs_dir}",
            )
        )
    return results


def collect_path_inputs(
    args: argparse.Namespace, platform_info: PlatformInfo
) -> tuple[Path | None, Path | None]:
    dcs_value = args.dcs_install_dir
    saved_games_value = args.saved_games_dir

    can_prompt = not args.non_interactive and sys.stdin.isatty()
    if not dcs_value and can_prompt:
        dcs_value = input("DCS installation directory: ").strip()
    if not saved_games_value and can_prompt:
        saved_games_value = input("DCS Saved Games directory: ").strip()

    dcs_path = normalize_user_path(dcs_value, platform_info) if dcs_value else None
    saved_games_path = (
        normalize_user_path(saved_games_value, platform_info)
        if saved_games_value
        else None
    )
    return dcs_path, saved_games_path


def overall_status(results: Sequence[CheckResult]) -> str:
    if any(result.status is CheckStatus.ERROR for result in results):
        return "NOT READY"
    if any(
        result.status in {CheckStatus.WARNING, CheckStatus.NOT_TESTED}
        for result in results
    ):
        return "READY WITH WARNINGS"
    return "READY"


def has_errors(results: Sequence[CheckResult]) -> bool:
    return any(result.status is CheckStatus.ERROR for result in results)


def add_grpc_diagnostics(
    results: list[CheckResult], inspection: GrpcInspection
) -> None:
    for diagnostic in inspection.diagnostics:
        results.append(
            CheckResult(
                diagnostic.check_id,
                CheckStatus(diagnostic.status),
                diagnostic.message,
            )
        )


def machine_status(status: str) -> str:
    return status.replace(" ", "_")


def platform_data(platform_info: PlatformInfo) -> dict[str, object]:
    return {
        "host_os": platform_info.host_os,
        "agent_os": platform_info.agent_os,
        "is_wsl": platform_info.is_wsl,
    }


def checks_data(results: Sequence[CheckResult]) -> list[dict[str, str]]:
    return [
        {
            "id": result.check_id,
            "status": result.status.value,
            "message": result.message,
        }
        for result in results
    ]


def collect_submodule_metadata(repository_root: Path) -> dict[str, dict[str, object]]:
    git = shutil.which("git")
    metadata: dict[str, dict[str, object]] = {}
    for relative_path in REQUIRED_SUBMODULES:
        path = repository_root / relative_path
        revision: str | None = None
        if git and path.is_dir():
            completed = subprocess.run(
                [git, "-C", str(path), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                revision = completed.stdout.strip()
        key = Path(relative_path).name.replace("-", "_")
        metadata[key] = {"path": str(path), "revision": revision}
    return metadata


def read_existing_client_host(environment_path: Path) -> str | None:
    """Read the player-owned client host without requiring PyYAML at bootstrap."""
    try:
        content = environment_path.read_text(encoding="utf-8")
    except OSError:
        return None

    grpc_match = re.search(
        r"(?ms)^grpc:\s*\n(?P<body>(?:^[ \t]+.*(?:\n|$))*)",
        content,
    )
    if grpc_match is None:
        return None
    host_match = re.search(
        r"(?m)^\s+client_host:\s*(?P<value>[^#\r\n]*?)\s*$",
        grpc_match.group("body"),
    )
    if host_match is None:
        return None
    raw_value = host_match.group("value").strip()
    if not raw_value or raw_value.lower() in {"null", "~"}:
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        parsed = raw_value.strip("'\"")
    return parsed if isinstance(parsed, str) and parsed.strip() else None


def build_environment_data(
    *,
    repository_root: Path,
    generated_at: str,
    status: str,
    platform_info: PlatformInfo,
    dcs_path: Path,
    saved_games_path: Path,
    grpc_inspection: GrpcInspection,
    client_host: str = "127.0.0.1",
) -> dict[str, object]:
    venv_dir = repository_root / "runtime" / "venv"
    return {
        "setup": {
            "status": machine_status(status),
            "generated_at": generated_at,
            "version": SETUP_VERSION,
        },
        "platform": platform_data(platform_info),
        "dcs": {
            "install_dir": str(dcs_path),
            "saved_games_dir": str(saved_games_path),
            "mission_scripting_file": str(
                dcs_path / "Scripts" / "MissionScripting.lua"
            ),
            "log_file": str(saved_games_path / "Logs" / "dcs.log"),
        },
        "grpc": {
            "installed": grpc_inspection.installed,
            "installation_dir": (
                str(grpc_inspection.installation_dir)
                if grpc_inspection.installation_dir
                else None
            ),
            "version": grpc_inspection.version,
            "bind_host": grpc_inspection.bind_host,
            "client_host": client_host,
            "port": grpc_inspection.port,
            "eval_enabled": grpc_inspection.eval_enabled,
            "autostart": grpc_inspection.autostart,
            "config_file": str(grpc_inspection.config_file),
            "proto_source": str(grpc_inspection.proto_source),
            "proto_source_kind": grpc_inspection.proto_source_kind,
            "generated_stub_dir": str(repository_root / "runtime" / "generated" / "grpc"),
        },
        "python": {
            "executable": str(venv_python_path(venv_dir)),
            "bootstrap_executable": platform_info.python_executable,
            "version": platform_info.python_version,
            "venv_dir": str(venv_dir),
        },
        "third_party": collect_submodule_metadata(repository_root),
        "capabilities": {
            "grpc_bindings": True,
            "grpc_live_connectivity": "not_tested",
        },
        "telemetry": {
            "enabled": True,
            "sample_interval_seconds": 5,
            "memory_retention_seconds": 1800,
            "max_snapshots": 361,
            "max_entities": 200000,
            "persistence": False,
        },
    }


def venv_python_path(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def dependency_fingerprint(repository_root: Path) -> str:
    digest = hashlib.sha256()
    dependency_files = (
        repository_root / "pyproject.toml",
        repository_root / "third_party" / "pydcs" / "setup.py",
        repository_root / "third_party" / "pydcs" / "requirements.txt",
    )
    for path in dependency_files:
        digest.update(str(path.relative_to(repository_root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    digest.update(platform.python_version().encode("ascii"))
    return digest.hexdigest()


def verify_runtime_imports(python_executable: Path) -> bool:
    modules = ("grpc", "grpc_tools", "google.protobuf", "yaml", "dcs", "pyproj")
    statement = "; ".join(f"import {module}" for module in modules)
    completed = subprocess.run(
        [str(python_executable), "-c", statement],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def uses_socks_proxy() -> bool:
    proxy_variables = (
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    )
    return any(
        os.environ.get(name, "")
        .lower()
        .startswith(("socks://", "socks4://", "socks5://", "socks5h://"))
        for name in proxy_variables
    )


def ensure_pip_socks_support(
    python_executable: Path, bootstrap_dir: Path
) -> CheckResult | None:
    if not uses_socks_proxy():
        return None

    import_check = subprocess.run(
        [str(python_executable), "-c", "import socks"],
        check=False,
        capture_output=True,
        text=True,
    )
    if import_check.returncode == 0:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.OK,
            "pip SOCKS proxy support is available.",
        )

    curl = shutil.which("curl")
    if not curl:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            "A SOCKS proxy is configured, but curl is unavailable to bootstrap PySocks.",
        )

    try:
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            f"Could not create the proxy bootstrap directory: {error}",
        )

    metadata_path = bootstrap_dir / "pysocks.json"
    metadata_url = (
        f"https://pypi.org/pypi/PySocks/{PYSOCKS_BOOTSTRAP_VERSION}/json"
    )
    metadata_download = subprocess.run(
        [
            curl,
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--output",
            str(metadata_path),
            metadata_url,
        ],
        check=False,
    )
    if metadata_download.returncode != 0:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            "Could not download PySocks metadata through the configured proxy.",
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        wheel = next(
            item
            for item in metadata["urls"]
            if item["packagetype"] == "bdist_wheel"
            and item["filename"].endswith("py3-none-any.whl")
        )
        wheel_url = wheel["url"]
        wheel_sha256 = wheel["digests"]["sha256"]
        wheel_path = bootstrap_dir / wheel["filename"]
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError, OSError) as error:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            f"Could not parse trusted PySocks release metadata: {error}",
        )

    wheel_download = subprocess.run(
        [
            curl,
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--output",
            str(wheel_path),
            wheel_url,
        ],
        check=False,
    )
    if wheel_download.returncode != 0:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            "Could not download the PySocks wheel through the configured proxy.",
        )

    try:
        actual_sha256 = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    except OSError as error:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            f"Could not read the downloaded PySocks wheel: {error}",
        )
    if actual_sha256 != wheel_sha256:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            "Downloaded PySocks wheel failed SHA-256 verification.",
        )

    installation = subprocess.run(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-index",
            str(wheel_path),
        ],
        check=False,
    )
    if installation.returncode != 0:
        return CheckResult(
            "pip_socks_support",
            CheckStatus.ERROR,
            "Failed to install the verified PySocks bootstrap wheel.",
        )

    return CheckResult(
        "pip_socks_support",
        CheckStatus.OK,
        f"Bootstrapped PySocks {PYSOCKS_BOOTSTRAP_VERSION} for pip proxy support.",
    )


def ensure_runtime_directories(repository_root: Path) -> CheckResult:
    runtime_dir = repository_root / "runtime"
    directories = (
        runtime_dir / "generated" / "grpc",
        runtime_dir / "logs",
        runtime_dir / "logs" / "dcs",
        runtime_dir / "telemetry",
        runtime_dir / "workspace",
        runtime_dir / "plugins" / "py",
        runtime_dir / "plugins" / "lua",
        runtime_dir / "memory",
    )
    try:
        for path in directories:
            path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return CheckResult(
            "runtime_directories",
            CheckStatus.ERROR,
            f"Could not create runtime directories: {error}",
        )
    return CheckResult(
        "runtime_directories",
        CheckStatus.OK,
        f"Runtime directories are ready under {runtime_dir}.",
    )


def prepare_runtime(repository_root: Path, dry_run: bool) -> list[CheckResult]:
    runtime_dir = repository_root / "runtime"
    venv_dir = runtime_dir / "venv"
    generated_dir = runtime_dir / "generated" / "grpc"
    python_executable = venv_python_path(venv_dir)
    fingerprint_file = venv_dir / ".dcs-harness-dependencies.sha256"

    if dry_run:
        return [
            CheckResult(
                "runtime_preparation",
                CheckStatus.NOT_TESTED,
                f"Would prepare runtime directories and virtual environment at {venv_dir}.",
            ),
            CheckResult(
                "python_dependencies",
                CheckStatus.NOT_TESTED,
                "Would install project dependencies and the local pydcs submodule.",
            ),
        ]

    directory_result = ensure_runtime_directories(repository_root)
    if directory_result.status is CheckStatus.ERROR:
        return [directory_result]
    results = [directory_result]

    if not python_executable.is_file():
        print(f"Creating Python virtual environment at {venv_dir} ...", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=False,
        )
        if completed.returncode != 0 or not python_executable.is_file():
            results.append(
                CheckResult(
                    "python_venv",
                    CheckStatus.ERROR,
                    f"Failed to create Python virtual environment at {venv_dir}.",
                )
            )
            return results
        venv_message = f"Created Python virtual environment at {venv_dir}."
    else:
        venv_message = f"Reusing Python virtual environment at {venv_dir}."

    results.append(CheckResult("python_venv", CheckStatus.OK, venv_message))

    proxy_result = ensure_pip_socks_support(
        python_executable,
        runtime_dir / "generated" / "bootstrap",
    )
    if proxy_result:
        results.append(proxy_result)
        if proxy_result.status is CheckStatus.ERROR:
            return results

    try:
        expected_fingerprint = dependency_fingerprint(repository_root)
        current_fingerprint = (
            fingerprint_file.read_text(encoding="utf-8").strip()
            if fingerprint_file.is_file()
            else None
        )
    except OSError as error:
        results.append(
            CheckResult(
                "python_dependencies",
                CheckStatus.ERROR,
                f"Could not inspect dependency metadata: {error}",
            )
        )
        return results

    imports_work = verify_runtime_imports(python_executable)
    if current_fingerprint == expected_fingerprint and imports_work:
        results.append(
            CheckResult(
                "python_dependencies",
                CheckStatus.OK,
                "Python dependencies are already installed and importable.",
            )
        )
        return results

    print("Installing DCS-Harness dependencies into runtime/venv ...", flush=True)
    completed = subprocess.run(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            str(repository_root),
            str(repository_root / "third_party" / "pydcs"),
        ],
        check=False,
    )
    if completed.returncode != 0:
        results.append(
            CheckResult(
                "python_dependencies",
                CheckStatus.ERROR,
                "Failed to install Python dependencies into runtime/venv.",
            )
        )
        return results

    if not verify_runtime_imports(python_executable):
        results.append(
            CheckResult(
                "python_imports",
                CheckStatus.ERROR,
                "Dependencies were installed, but one or more required imports failed.",
            )
        )
        return results

    try:
        fingerprint_file.write_text(expected_fingerprint + "\n", encoding="utf-8")
    except OSError as error:
        results.append(
            CheckResult(
                "dependency_fingerprint",
                CheckStatus.WARNING,
                f"Dependencies work, but their fingerprint could not be saved: {error}",
            )
        )

    results.append(
        CheckResult(
            "python_dependencies",
            CheckStatus.OK,
            "Installed and verified project dependencies and local pydcs.",
        )
    )
    return results


def print_summary(
    repository_root: Path,
    platform_info: PlatformInfo,
    results: Sequence[CheckResult],
    dry_run: bool,
) -> str:
    status = overall_status(results)
    print(f"DCS-Harness setup {SETUP_VERSION}")
    print(f"Repository: {repository_root}")
    print(
        f"Platform: host={platform_info.host_os}, "
        f"agent={platform_info.agent_os}, WSL={str(platform_info.is_wsl).lower()}"
    )
    if dry_run:
        print("Mode: dry-run (no files will be written)")
    print()

    symbols = {
        CheckStatus.OK: "OK",
        CheckStatus.WARNING: "WARN",
        CheckStatus.ERROR: "FAIL",
        CheckStatus.NOT_TESTED: "SKIP",
    }
    for result in results:
        print(f"[{symbols[result.status]}] {result.check_id}: {result.message}")

    print()
    print(f"Status: {status}")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repository_root = discover_repository_root()
    except RuntimeError as error:
        print(f"Setup error: {error}", file=sys.stderr)
        return 1

    platform_info = detect_platform()
    dcs_path, saved_games_path = collect_path_inputs(args, platform_info)
    grpc_inspection: GrpcInspection | None = None

    results: list[CheckResult] = [check_python_version()]
    results.extend(check_submodules(repository_root))
    results.extend(check_dcs_install(dcs_path))
    results.extend(check_saved_games(saved_games_path))

    if has_errors(results):
        results.append(
            CheckResult(
                "grpc_installation",
                CheckStatus.NOT_TESTED,
                "Skipped because repository or DCS path checks failed.",
            )
        )
    else:
        assert dcs_path is not None
        assert saved_games_path is not None
        grpc_inspection = inspect_dcs_grpc(
            repository_root,
            dcs_path,
            saved_games_path,
        )
        add_grpc_diagnostics(results, grpc_inspection)

        if has_errors(results):
            results.append(
                CheckResult(
                    "runtime_preparation",
                    CheckStatus.NOT_TESTED,
                    "Skipped because DCS-gRPC static checks failed.",
                )
            )
        else:
            results.extend(prepare_runtime(repository_root, dry_run=args.dry_run))
            if not has_errors(results):
                bindings = prepare_bindings(
                    repository_root,
                    venv_python_path(repository_root / "runtime" / "venv"),
                    grpc_inspection,
                    dry_run=args.dry_run,
                )
                for diagnostic in bindings:
                    results.append(
                        CheckResult(
                            diagnostic.check_id,
                            CheckStatus(diagnostic.status),
                            diagnostic.message,
                        )
                    )

    generated_at = datetime.now(timezone.utc).isoformat()
    environment_path = repository_root / "config" / "environment.yaml"
    report_path = repository_root / "runtime" / "setup_report.json"
    log_path = repository_root / "runtime" / "logs" / "setup.log"
    environment_written = False

    if args.dry_run:
        results.append(
            CheckResult(
                "setup_artifacts",
                CheckStatus.NOT_TESTED,
                "Would update environment config, setup report, and setup log.",
            )
        )
    elif has_errors(results):
        results.append(
            CheckResult(
                "environment_config",
                CheckStatus.NOT_TESTED,
                "Preserved the previous environment config because setup is not ready.",
            )
        )
    else:
        assert dcs_path is not None
        assert saved_games_path is not None
        assert grpc_inspection is not None
        environment = build_environment_data(
            repository_root=repository_root,
            generated_at=generated_at,
            status=overall_status(results),
            platform_info=platform_info,
            dcs_path=dcs_path,
            saved_games_path=saved_games_path,
            grpc_inspection=grpc_inspection,
            client_host=(
                read_existing_client_host(environment_path) or "127.0.0.1"
            ),
        )
        try:
            write_yaml(environment_path, environment)
        except OSError as error:
            results.append(
                CheckResult(
                    "environment_config",
                    CheckStatus.ERROR,
                    f"Could not write environment config: {error}",
                )
            )
        else:
            environment_written = True
            results.append(
                CheckResult(
                    "environment_config",
                    CheckStatus.OK,
                    f"Wrote technical environment config to {environment_path}.",
                )
            )

    if not args.dry_run:
        log_written = False

        def current_log_content() -> str:
            return render_setup_log(
                generated_at=generated_at,
                setup_version=SETUP_VERSION,
                status=machine_status(overall_status(results)),
                repository_root=repository_root,
                platform_data=platform_data(platform_info),
                checks=checks_data(results),
            )

        try:
            atomic_write_text(log_path, current_log_content())
            log_written = True
        except OSError as error:
            results.append(
                CheckResult(
                    "setup_log",
                    CheckStatus.ERROR,
                    f"Could not write setup log: {error}",
                )
            )

        report = {
            "setup": {
                "status": machine_status(overall_status(results)),
                "generated_at": generated_at,
                "version": SETUP_VERSION,
            },
            "repository_root": str(repository_root),
            "platform": platform_data(platform_info),
            "inputs": {
                "dcs_install_dir": str(dcs_path) if dcs_path else None,
                "saved_games_dir": str(saved_games_path) if saved_games_path else None,
            },
            "environment_config": {
                "path": str(environment_path),
                "written": environment_written,
            },
            "checks": checks_data(results),
        }
        try:
            write_json(report_path, report)
        except OSError as error:
            results.append(
                CheckResult(
                    "setup_report",
                    CheckStatus.ERROR,
                    f"Could not write setup report: {error}",
                )
            )
            if log_written:
                try:
                    atomic_write_text(log_path, current_log_content())
                except OSError:
                    pass

    status = print_summary(
        repository_root,
        platform_info,
        results,
        dry_run=args.dry_run,
    )
    return 1 if status == "NOT READY" else 0


if __name__ == "__main__":
    raise SystemExit(main())
