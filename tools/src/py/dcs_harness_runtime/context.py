"""Shared technical context for DCS-Harness plugin invocations."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .result import ErrorCode, HarnessError


@dataclass(frozen=True)
class GrpcEndpoint:
    bind_host: str | None
    client_host: str | None
    port: int
    eval_enabled: bool


@dataclass
class Context:
    repository_root: Path
    environment_path: Path
    environment: dict[str, Any]
    runtime_root: Path
    generated_root: Path
    resolver: Any = None
    _grpc_channel: Any = field(default=None, init=False, repr=False)

    @classmethod
    def load(cls, repository_root: Path) -> "Context":
        repository_root = repository_root.resolve()
        environment_path = repository_root / "config" / "environment.yaml"
        environment: dict[str, Any] = {}
        if environment_path.is_file():
            try:
                import yaml

                loaded = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
            except Exception as error:
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "Technical environment config could not be loaded.",
                    details={"exception_type": type(error).__name__},
                ) from error
            if not isinstance(loaded, dict):
                raise HarnessError(
                    ErrorCode.CAPABILITY_UNAVAILABLE,
                    "Technical environment config must contain a mapping.",
                )
            environment = loaded

        return cls(
            repository_root=repository_root,
            environment_path=environment_path,
            environment=environment,
            runtime_root=repository_root / "runtime",
            generated_root=repository_root / "runtime" / "generated",
        )

    @property
    def environment_ready(self) -> bool:
        setup = self.environment.get("setup", {})
        return isinstance(setup, Mapping) and setup.get("status") in {
            "READY",
            "READY_WITH_WARNINGS",
        }

    def require_environment(self) -> None:
        if not self.environment_ready:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "A READY technical environment is required; run setup first.",
                details={"environment_path": str(self.environment_path)},
            )

    @property
    def grpc_endpoint(self) -> GrpcEndpoint:
        self.require_environment()
        grpc_config = self.environment.get("grpc", {})
        platform_config = self.environment.get("platform", {})
        if not isinstance(grpc_config, Mapping):
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Technical environment has no usable gRPC configuration.",
            )
        bind_host_value = grpc_config.get("bind_host", grpc_config.get("host"))
        bind_host = str(bind_host_value) if bind_host_value is not None else None
        client_value = grpc_config.get("client_host")
        client_host = str(client_value) if client_value else None
        is_wsl = isinstance(platform_config, Mapping) and bool(
            platform_config.get("is_wsl")
        )
        if client_host is None and bind_host not in {None, "0.0.0.0", "::"} and not is_wsl:
            client_host = bind_host
        port = grpc_config.get("port", 50051)
        try:
            port = int(port)
        except (TypeError, ValueError) as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Configured gRPC port is invalid.",
            ) from error
        return GrpcEndpoint(
            bind_host=bind_host,
            client_host=client_host,
            port=port,
            eval_enabled=bool(grpc_config.get("eval_enabled", False)),
        )

    def require_grpc_client_endpoint(self) -> GrpcEndpoint:
        endpoint = self.grpc_endpoint
        if not endpoint.client_host:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "DCS-gRPC client_host is not configured or verified.",
                details={
                    "bind_host": endpoint.bind_host,
                    "port": endpoint.port,
                    "environment_path": str(self.environment_path),
                },
            )
        return endpoint

    def ensure_generated_import_path(self) -> Path:
        grpc_config = self.environment.get("grpc", {})
        configured = (
            grpc_config.get("generated_stub_dir")
            if isinstance(grpc_config, Mapping)
            else None
        )
        generated = (
            Path(str(configured))
            if configured
            else self.generated_root / "grpc"
        )
        if not generated.is_dir():
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Generated DCS-gRPC Python bindings are unavailable; run setup.",
                details={"generated_stub_dir": str(generated)},
            )
        generated_text = str(generated)
        if generated_text not in sys.path:
            sys.path.insert(0, generated_text)
        return generated

    def grpc_channel(self) -> Any:
        endpoint = self.require_grpc_client_endpoint()
        self.ensure_generated_import_path()
        if self._grpc_channel is None:
            try:
                import grpc

                self._grpc_channel = grpc.insecure_channel(
                    f"{endpoint.client_host}:{endpoint.port}"
                )
            except Exception as error:
                raise HarnessError(
                    ErrorCode.GRPC_CONNECTION_FAILED,
                    "Could not create the DCS-gRPC client channel.",
                    details={"exception_type": type(error).__name__},
                ) from error
        return self._grpc_channel

    def close(self) -> None:
        if self._grpc_channel is not None:
            close = getattr(self._grpc_channel, "close", None)
            if callable(close):
                close()
            self._grpc_channel = None
