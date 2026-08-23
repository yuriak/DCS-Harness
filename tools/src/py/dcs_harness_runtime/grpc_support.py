"""Descriptor-driven discovery and unary invocation for generated DCS-gRPC APIs."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Mapping

from .result import ErrorCode, HarnessError


DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 300.0
SCHEMA_DEPTH_LIMIT = 4


@dataclass(frozen=True)
class ResolvedMethod:
    service: Any
    method: Any


class GrpcSupport:
    def __init__(self, context: Any) -> None:
        self.context = context
        self._services = self._load_services()

    def _load_services(self) -> dict[str, Any]:
        self.context.ensure_generated_import_path()
        try:
            root = importlib.import_module("dcs_grpc.dcs.dcs_pb2").DESCRIPTOR
        except Exception as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Generated DCS-gRPC descriptors could not be loaded.",
                details={"exception_type": type(error).__name__},
            ) from error

        files: list[Any] = []
        seen: set[str] = set()

        def visit(file_descriptor: Any) -> None:
            if file_descriptor.name in seen:
                return
            seen.add(file_descriptor.name)
            files.append(file_descriptor)
            for dependency in file_descriptor.dependencies:
                visit(dependency)

        visit(root)
        services = {
            service.full_name: service
            for file_descriptor in files
            for service in file_descriptor.services_by_name.values()
        }
        if not services:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Generated DCS-gRPC descriptors contain no services.",
            )
        return services

    def services(self) -> list[dict[str, Any]]:
        return [
            self._service_summary(self._services[name])
            for name in sorted(self._services)
        ]

    def describe(
        self, service_name: str, method_name: str | None = None
    ) -> dict[str, Any]:
        if method_name is None:
            try:
                service = self.resolve_service(service_name)
            except HarnessError as service_error:
                if "." not in service_name:
                    raise
                service_part, method_part = service_name.rsplit(".", 1)
                try:
                    service = self.resolve_service(service_part)
                except HarnessError:
                    raise service_error
                return self._method_description(
                    self.resolve_method(service.full_name, method_part)
                )
        else:
            service = self.resolve_service(service_name)
        if method_name is None:
            value = self._service_summary(service)
            value["methods"] = [
                self._method_summary(method) for method in service.methods
            ]
            return value
        return self._method_description(
            self.resolve_method(service.full_name, method_name)
        )

    def resolve_service(self, name: str) -> Any:
        exact = self._services.get(name)
        if exact is not None:
            return exact
        requested = name.casefold()
        matches = [
            service
            for full_name, service in self._services.items()
            if requested in self._service_aliases(service)
            or full_name.casefold().endswith(f".{requested}")
        ]
        if len(matches) == 1:
            return matches[0]
        details: dict[str, Any] = {"service": name}
        if matches:
            details["matches"] = sorted(item.full_name for item in matches)
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unknown or ambiguous gRPC service: {name!r}.",
            details=details,
        )

    @staticmethod
    def _service_aliases(service: Any) -> set[str]:
        package_parts = service.full_name.split(".")[:-1]
        aliases = {
            service.full_name.casefold(),
            service.name.casefold(),
            service.name.removesuffix("Service").casefold(),
        }
        if len(package_parts) >= 2 and package_parts[-1].startswith("v"):
            aliases.add(package_parts[-2].casefold())
        return aliases

    def resolve_method(self, service_name: str, method_name: str) -> ResolvedMethod:
        service = self.resolve_service(service_name)
        method = service.methods_by_name.get(method_name)
        if method is None:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"gRPC service {service.full_name!r} has no method {method_name!r}.",
                details={
                    "service": service.full_name,
                    "method": method_name,
                    "available_methods": [item.name for item in service.methods],
                },
            )
        return ResolvedMethod(service=service, method=method)

    def call(
        self,
        service_name: str,
        method_name: str,
        request: Mapping[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        resolved = self.resolve_method(service_name, method_name)
        method = resolved.method
        if method.client_streaming or method.server_streaming:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "The generic gRPC caller currently supports unary methods only.",
                details={
                    "service": resolved.service.full_name,
                    "method": method.name,
                    "client_streaming": method.client_streaming,
                    "server_streaming": method.server_streaming,
                },
            )
        if not isinstance(request, Mapping):
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "gRPC request must be a JSON object.",
            )
        timeout_value = self._validate_timeout(timeout)

        try:
            from google.protobuf import json_format, message, message_factory

            request_class = message_factory.GetMessageClass(method.input_type)
            request_message = json_format.ParseDict(
                dict(request), request_class(), ignore_unknown_fields=False
            )
        except (TypeError, ValueError) as error:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                "gRPC request could not be converted to its protobuf message.",
                details={
                    "exception_type": type(error).__name__,
                    "reason": str(error),
                },
            ) from error
        except Exception as error:
            # ParseError is intentionally handled without binding this module to a
            # particular protobuf implementation version.
            if error.__class__.__module__.startswith("google.protobuf"):
                raise HarnessError(
                    ErrorCode.INVALID_ARGUMENT,
                    "gRPC request could not be converted to its protobuf message.",
                    details={
                        "exception_type": type(error).__name__,
                        "reason": str(error),
                    },
                ) from error
            raise

        stub = self._stub(resolved.service)
        rpc = getattr(stub, method.name, None)
        if not callable(rpc):
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Generated gRPC stub does not expose the described method.",
                details={"service": resolved.service.full_name, "method": method.name},
            )
        try:
            response = rpc(request_message, timeout=timeout_value)
        except Exception as error:
            self._raise_call_error(error, resolved)

        if not isinstance(response, message.Message):
            raise HarnessError(
                ErrorCode.GRPC_CALL_FAILED,
                "gRPC method returned an invalid protobuf response.",
                details={"response_type": type(response).__name__},
            )
        return json_format.MessageToDict(
            response,
            preserving_proto_field_name=True,
        )

    def _stub(self, service: Any) -> Any:
        proto_module = service.file.name.removesuffix(".proto").replace("/", ".")
        module_name = f"dcs_grpc.{proto_module}_pb2_grpc"
        class_name = f"{service.name}Stub"
        try:
            module = importlib.import_module(module_name)
            stub_class = getattr(module, class_name)
        except Exception as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Generated gRPC stub could not be loaded.",
                details={
                    "service": service.full_name,
                    "module": module_name,
                    "class": class_name,
                    "exception_type": type(error).__name__,
                },
            ) from error
        try:
            return self.context.grpc_stub(service.full_name, stub_class)
        except HarnessError:
            raise
        except Exception as error:
            raise HarnessError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "Generated gRPC stub could not be initialized.",
                details={
                    "service": service.full_name,
                    "exception_type": type(error).__name__,
                },
            ) from error

    @staticmethod
    def _validate_timeout(value: Any) -> float:
        if isinstance(value, bool):
            valid = False
        else:
            try:
                value = float(value)
                valid = 0 < value <= MAX_TIMEOUT_SECONDS
            except (TypeError, ValueError):
                valid = False
        if not valid:
            raise HarnessError(
                ErrorCode.INVALID_ARGUMENT,
                f"gRPC timeout must be greater than 0 and at most {MAX_TIMEOUT_SECONDS} seconds.",
            )
        return value

    @staticmethod
    def _raise_call_error(error: Exception, resolved: ResolvedMethod) -> None:
        try:
            import grpc
        except ImportError:
            grpc = None
        if grpc is not None and isinstance(error, grpc.RpcError):
            status = error.code()
            status_name = getattr(status, "name", str(status))
            details = error.details()
            code = (
                ErrorCode.GRPC_CONNECTION_FAILED
                if status == grpc.StatusCode.UNAVAILABLE
                else ErrorCode.GRPC_CALL_FAILED
            )
            raise HarnessError(
                code,
                "DCS-gRPC call failed.",
                details={
                    "service": resolved.service.full_name,
                    "method": resolved.method.name,
                    "grpc_status": status_name,
                    "grpc_details": str(details),
                },
            ) from error
        raise HarnessError(
            ErrorCode.GRPC_CALL_FAILED,
            "DCS-gRPC call failed.",
            details={
                "service": resolved.service.full_name,
                "method": resolved.method.name,
                "exception_type": type(error).__name__,
            },
        ) from error

    @staticmethod
    def _service_summary(service: Any) -> dict[str, Any]:
        return {
            "name": service.name,
            "full_name": service.full_name,
            "proto_file": service.file.name,
            "method_count": len(service.methods),
        }

    @staticmethod
    def _method_summary(method: Any) -> dict[str, Any]:
        return {
            "name": method.name,
            "full_name": method.full_name,
            "input_type": method.input_type.full_name,
            "output_type": method.output_type.full_name,
            "client_streaming": method.client_streaming,
            "server_streaming": method.server_streaming,
        }

    def _method_description(self, resolved: ResolvedMethod) -> dict[str, Any]:
        value = self._method_summary(resolved.method)
        value["service"] = resolved.service.full_name
        value["input_schema"] = self._message_schema(resolved.method.input_type)
        value["output_schema"] = self._message_schema(resolved.method.output_type)
        return value

    def _message_schema(
        self, descriptor: Any, *, depth: int = 0, seen: frozenset[str] = frozenset()
    ) -> dict[str, Any]:
        value: dict[str, Any] = {"type": "message", "full_name": descriptor.full_name}
        if descriptor.full_name in seen or depth >= SCHEMA_DEPTH_LIMIT:
            value["ref"] = descriptor.full_name
            return value
        next_seen = seen | {descriptor.full_name}
        value["fields"] = [
            self._field_schema(field, depth=depth, seen=next_seen)
            for field in descriptor.fields
        ]
        return value

    def _field_schema(
        self, field: Any, *, depth: int, seen: frozenset[str]
    ) -> dict[str, Any]:
        from google.protobuf.descriptor_pb2 import FieldDescriptorProto

        type_name = (
            FieldDescriptorProto.Type.Name(field.type)
            .removeprefix("TYPE_")
            .lower()
        )
        value: dict[str, Any] = {
            "name": field.name,
            "json_name": field.json_name,
            "number": field.number,
            "type": type_name,
            "repeated": field.is_repeated,
            "required": field.is_required,
        }
        if field.containing_oneof is not None:
            value["oneof"] = field.containing_oneof.name
        if field.enum_type is not None:
            value["type_name"] = field.enum_type.full_name
            value["enum_values"] = [
                {"name": item.name, "number": item.number}
                for item in field.enum_type.values
            ]
        if field.message_type is not None:
            value["type_name"] = field.message_type.full_name
            value["schema"] = self._message_schema(
                field.message_type, depth=depth + 1, seen=seen
            )
        return value
