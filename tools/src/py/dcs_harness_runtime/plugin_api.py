"""Minimal plugin contract and deterministic target-only plugin resolution."""

from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

from .result import ErrorCode, HarnessError


PLUGIN_API_VERSION = 1
PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class PluginSource(str, Enum):
    BUILTIN = "builtin"
    RUNTIME = "runtime"


class PluginRuntimeKind(str, Enum):
    STATELESS = "stateless"
    RESIDENT = "resident"


@dataclass(frozen=True)
class PluginSpec:
    name: str
    source: PluginSource
    path: Path


@dataclass(frozen=True)
class LoadedPlugin:
    spec: PluginSpec
    module: ModuleType
    invoke: Callable[[Any, str, Mapping[str, Any]], Any]
    runtime: PluginRuntimeKind
    autostart: bool
    start: Callable[[Any, Any], Any] | None
    stop: Callable[[Any, Any], Any] | None


@dataclass(frozen=True)
class PluginFileSignature:
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class PluginCacheEntry:
    loaded: LoadedPlugin
    signature: PluginFileSignature | None


class PluginResolver:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.builtin_dir = self.repository_root / "tools" / "src" / "py" / "plugins"
        self.runtime_dir = self.repository_root / "runtime" / "plugins" / "py"

    @staticmethod
    def validate_name(name: str) -> None:
        if not PLUGIN_NAME_PATTERN.fullmatch(name):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"Invalid plugin name: {name!r}.",
                details={"expected": PLUGIN_NAME_PATTERN.pattern},
            )

    def resolve(self, name: str) -> PluginSpec:
        self.validate_name(name)
        builtin_path = self.builtin_dir / f"{name}.py"
        runtime_path = self.runtime_dir / f"{name}.py"
        builtin_exists = builtin_path.is_file()
        runtime_exists = runtime_path.is_file()

        if builtin_exists and runtime_exists:
            raise HarnessError(
                ErrorCode.PLUGIN_NAME_CONFLICT,
                f"Runtime plugin {name!r} conflicts with a built-in plugin.",
                details={
                    "builtin": str(builtin_path),
                    "runtime": str(runtime_path),
                },
            )
        if builtin_exists:
            return PluginSpec(name, PluginSource.BUILTIN, builtin_path)
        if runtime_exists:
            return PluginSpec(name, PluginSource.RUNTIME, runtime_path)
        raise HarnessError(
            ErrorCode.PLUGIN_NOT_FOUND,
            f"Plugin {name!r} was not found.",
        )

    def spec_for_path(self, path: Path) -> PluginSpec:
        candidate = path.expanduser().resolve()
        try:
            relative_builtin = candidate.relative_to(self.builtin_dir.resolve())
        except ValueError:
            relative_builtin = None
        try:
            relative_runtime = candidate.relative_to(self.runtime_dir.resolve())
        except ValueError:
            relative_runtime = None

        if relative_builtin is not None and relative_builtin.parent == Path("."):
            source = PluginSource.BUILTIN
        elif relative_runtime is not None and relative_runtime.parent == Path("."):
            source = PluginSource.RUNTIME
        else:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "Plugin path must be directly inside a built-in or runtime plugin directory.",
                details={"path": str(candidate)},
            )
        if candidate.suffix != ".py" or not candidate.is_file():
            raise HarnessError(
                ErrorCode.PLUGIN_NOT_FOUND,
                f"Plugin file was not found: {candidate}",
            )
        self.validate_name(candidate.stem)
        return PluginSpec(candidate.stem, source, candidate)

    def resolve_name_or_path(self, value: str) -> PluginSpec:
        if "/" in value or "\\" in value or value.endswith(".py"):
            return self.spec_for_path(Path(value))
        return self.resolve(value)

    def load(self, spec: PluginSpec) -> LoadedPlugin:
        module_key = f"_dcs_harness_{spec.source.value}_{spec.name}_{abs(hash(spec.path))}"
        module = ModuleType(module_key)
        module.__file__ = str(spec.path)
        module.__package__ = ""
        sys.modules[module_key] = module
        try:
            source = spec.path.read_bytes()
            code = compile(source, str(spec.path), "exec")
            exec(code, module.__dict__)
            invoke = self._validate_module(module, spec)
            runtime, autostart, start, stop = self._runtime_contract(module, spec)
        except HarnessError:
            sys.modules.pop(module_key, None)
            raise
        except Exception as error:
            sys.modules.pop(module_key, None)
            raise HarnessError(
                ErrorCode.PLUGIN_IMPORT_FAILED,
                f"Plugin {spec.name!r} could not be imported.",
                details={"exception_type": type(error).__name__},
            ) from error
        return LoadedPlugin(
            spec=spec,
            module=module,
            invoke=invoke,
            runtime=runtime,
            autostart=autostart,
            start=start,
            stop=stop,
        )

    @staticmethod
    def file_signature(spec: PluginSpec) -> PluginFileSignature:
        try:
            stat = spec.path.stat()
        except OSError as error:
            raise HarnessError(
                ErrorCode.PLUGIN_NOT_FOUND,
                f"Plugin file is unavailable: {spec.path}",
            ) from error
        return PluginFileSignature(mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    @staticmethod
    def _validate_module(
        module: ModuleType, spec: PluginSpec
    ) -> Callable[[Any, str, Mapping[str, Any]], Any]:
        plugin_name = getattr(module, "PLUGIN_NAME", None)
        if plugin_name != spec.name:
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} must declare PLUGIN_NAME = {spec.name!r}.",
            )
        api_version = getattr(module, "PLUGIN_API_VERSION", None)
        if api_version != PLUGIN_API_VERSION:
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} uses unsupported API version {api_version!r}.",
                details={"supported_api_version": PLUGIN_API_VERSION},
            )
        invoke = getattr(module, "invoke", None)
        if not callable(invoke):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} must expose callable invoke(context, command, args).",
            )
        describe = getattr(module, "describe", None)
        if describe is not None and not callable(describe):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} describe attribute must be callable.",
            )
        return invoke

    @staticmethod
    def _runtime_contract(
        module: ModuleType,
        spec: PluginSpec,
    ) -> tuple[
        PluginRuntimeKind,
        bool,
        Callable[[Any, Any], Any] | None,
        Callable[[Any, Any], Any] | None,
    ]:
        runtime_value = getattr(module, "PLUGIN_RUNTIME", PluginRuntimeKind.STATELESS.value)
        try:
            runtime = PluginRuntimeKind(runtime_value)
        except ValueError as error:
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} declares invalid PLUGIN_RUNTIME {runtime_value!r}.",
                details={"allowed": [item.value for item in PluginRuntimeKind]},
            ) from error
        autostart = getattr(module, "PLUGIN_AUTOSTART", False)
        if not isinstance(autostart, bool):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} PLUGIN_AUTOSTART must be boolean.",
            )
        if autostart and runtime is not PluginRuntimeKind.RESIDENT:
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} cannot autostart unless it is resident.",
            )
        start = getattr(module, "start", None)
        stop = getattr(module, "stop", None)
        if start is not None and not callable(start):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} start attribute must be callable.",
            )
        if stop is not None and not callable(stop):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} stop attribute must be callable.",
            )
        if runtime is PluginRuntimeKind.STATELESS and (start is not None or stop is not None):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Stateless plugin {spec.name!r} cannot declare resident lifecycle hooks.",
            )
        return runtime, autostart, start, stop

    def describe(self, spec: PluginSpec) -> dict[str, Any]:
        return self.describe_loaded(self.load(spec))

    @staticmethod
    def describe_loaded(loaded: LoadedPlugin) -> dict[str, Any]:
        spec = loaded.spec
        describe = getattr(loaded.module, "describe", None)
        if describe is None:
            return {
                "name": spec.name,
                "api_version": PLUGIN_API_VERSION,
                "source": spec.source.value,
                "runtime": loaded.runtime.value,
                "autostart": loaded.autostart,
                "commands": None,
            }
        try:
            metadata = describe()
        except Exception as error:
            raise HarnessError(
                ErrorCode.PLUGIN_IMPORT_FAILED,
                f"Plugin {spec.name!r} describe() failed.",
                details={"exception_type": type(error).__name__},
            ) from error
        if not isinstance(metadata, dict):
            raise HarnessError(
                ErrorCode.PLUGIN_API_INCOMPATIBLE,
                f"Plugin {spec.name!r} describe() must return a dictionary.",
            )
        value = dict(metadata)
        value.setdefault("name", spec.name)
        value.setdefault("api_version", PLUGIN_API_VERSION)
        value["source"] = spec.source.value
        value["runtime"] = loaded.runtime.value
        value["autostart"] = loaded.autostart
        return value

    def discover(self) -> dict[str, list[str]]:
        def names(directory: Path) -> set[str]:
            if not directory.is_dir():
                return set()
            return {
                path.stem
                for path in directory.iterdir()
                if path.is_file()
                and path.suffix == ".py"
                and not path.name.startswith("_")
                and PLUGIN_NAME_PATTERN.fullmatch(path.stem)
            }

        builtin = names(self.builtin_dir)
        runtime = names(self.runtime_dir)
        return {
            "builtin": sorted(builtin),
            "runtime": sorted(runtime - builtin),
            "conflicts": sorted(builtin & runtime),
        }


class PluginCache:
    """Target-only resident cache with runtime plugin change detection."""

    def __init__(self, resolver: PluginResolver) -> None:
        self.resolver = resolver
        self._entries: dict[tuple[PluginSource, str], PluginCacheEntry] = {}
        self._immutable: set[tuple[PluginSource, str]] = set()
        self._lock = threading.RLock()

    def load(self, spec: PluginSpec) -> tuple[LoadedPlugin, str]:
        key = (spec.source, spec.name)
        with self._lock:
            existing = self._entries.get(key)
            if (
                spec.source is PluginSource.BUILTIN or key in self._immutable
            ) and existing is not None:
                return existing.loaded, "cache_hit"

            signature = (
                self.resolver.file_signature(spec)
                if spec.source is PluginSource.RUNTIME
                else None
            )
            if existing is not None and existing.signature == signature:
                return existing.loaded, "cache_hit"

            loaded = self.resolver.load(spec)
            load_status = "reloaded" if existing is not None else "loaded"
            self._entries[key] = PluginCacheEntry(loaded=loaded, signature=signature)
            return loaded, load_status

    def mark_immutable(self, spec: PluginSpec) -> None:
        with self._lock:
            self._immutable.add((spec.source, spec.name))

    def describe(self, spec: PluginSpec) -> tuple[dict[str, Any], str]:
        loaded, load_status = self.load(spec)
        return self.resolver.describe_loaded(loaded), load_status
