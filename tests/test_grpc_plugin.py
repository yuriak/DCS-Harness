from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.grpc_support import GrpcSupport  # noqa: E402
from dcs_harness_runtime.plugin_api import PluginResolver  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402


class FakeChannel:
    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, Exception] = {}
        self.registrations: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def unary_unary(
        self,
        path: str,
        request_serializer: Any,
        response_deserializer: Any,
        **kwargs: Any,
    ) -> Any:
        self.registrations.append(path)

        def call(
            request: Any, *, timeout: float | None = None, **call_kwargs: Any
        ) -> Any:
            self.calls.append(
                {
                    "path": path,
                    "request": request,
                    "serialized_request": request_serializer(request),
                    "timeout": timeout,
                }
            )
            if path in self.errors:
                raise self.errors[path]
            response = self.responses[path]
            return response_deserializer(response.SerializeToString())

        return call

    def unary_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def stream_unary(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: None

    def stream_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def close(self) -> None:
        self.closed = True


class FakeRpcError(Exception):
    def __init__(self, status: Any, details: str) -> None:
        super().__init__(details)
        self._status = status
        self._details = details

    def code(self) -> Any:
        return self._status

    def details(self) -> str:
        return self._details


class GrpcPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = Context(
            repository_root=REPOSITORY_ROOT,
            environment_path=REPOSITORY_ROOT / "config" / "environment.yaml",
            environment={
                "setup": {"status": "READY"},
                "grpc": {"client_host": "127.0.0.1", "port": 50051},
            },
            runtime_root=REPOSITORY_ROOT / "runtime",
            generated_root=REPOSITORY_ROOT / "runtime" / "generated",
        )
        self.channel = FakeChannel()
        self.context._grpc_channel = self.channel
        self.support = GrpcSupport(self.context)

        from dcs_grpc.dcs.atmosphere.v0 import atmosphere_pb2
        from dcs_grpc.dcs.coalition.v0 import coalition_pb2
        from dcs_grpc.dcs.metadata.v0 import metadata_pb2

        self.channel.responses.update(
            {
                "/dcs.metadata.v0.MetadataService/GetHealth": metadata_pb2.GetHealthResponse(
                    alive=True
                ),
                "/dcs.coalition.v0.CoalitionService/GetGroups": coalition_pb2.GetGroupsResponse(),
                "/dcs.atmosphere.v0.AtmosphereService/GetWind": atmosphere_pb2.GetWindResponse(
                    heading=123.5, strength=8.25
                ),
            }
        )

    def tearDown(self) -> None:
        self.context.close()

    def test_services_are_discovered_from_descriptors(self) -> None:
        services = self.support.services()
        names = {service["full_name"] for service in services}
        self.assertIn("dcs.metadata.v0.MetadataService", names)
        self.assertIn("dcs.mission.v0.MissionService", names)
        self.assertEqual(names, set(sorted(names)))

    def test_describe_service_and_method(self) -> None:
        service = self.support.describe("MetadataService")
        self.assertEqual(service["full_name"], "dcs.metadata.v0.MetadataService")
        self.assertEqual(
            [item["name"] for item in service["methods"]],
            ["GetHealth", "GetVersion"],
        )

        method = self.support.describe("CoalitionService", "GetGroups")
        fields = {field["name"]: field for field in method["input_schema"]["fields"]}
        self.assertEqual(fields["coalition"]["type"], "enum")
        self.assertIn(
            {"name": "COALITION_RED", "number": 2},
            fields["coalition"]["enum_values"],
        )

        shorthand = self.support.describe("metadata.GetHealth")
        self.assertEqual(
            shorthand["full_name"],
            "dcs.metadata.v0.MetadataService.GetHealth",
        )

    def test_nested_message_schema_is_described(self) -> None:
        method = self.support.describe("AtmosphereService", "GetWind")
        position = method["input_schema"]["fields"][0]
        self.assertEqual(position["type_name"], "dcs.common.v0.InputPosition")
        self.assertEqual(
            [field["name"] for field in position["schema"]["fields"]],
            ["lat", "lon", "alt"],
        )

    def test_call_converts_enum_json_and_reuses_stub(self) -> None:
        request = {
            "coalition": "COALITION_RED",
            "category": "GROUP_CATEGORY_AIRPLANE",
        }
        self.assertEqual(self.support.call("CoalitionService", "GetGroups", request), {})
        registrations = len(self.channel.registrations)
        self.assertEqual(self.support.call("CoalitionService", "GetGroups", request), {})
        self.assertEqual(len(self.channel.registrations), registrations)
        captured = self.channel.calls[-1]["request"]
        self.assertEqual(captured.coalition, 2)
        self.assertEqual(captured.category, 1)

    def test_call_converts_nested_json_and_response(self) -> None:
        response = self.support.call(
            "AtmosphereService",
            "GetWind",
            {"position": {"lat": -35.3, "lon": 149.1, "alt": 600.0}},
            timeout=2.5,
        )
        self.assertEqual(response, {"heading": 123.5, "strength": 8.25})
        call = self.channel.calls[-1]
        self.assertEqual(call["request"].position.lat, -35.3)
        self.assertEqual(call["timeout"], 2.5)

    def test_unknown_service_method_and_invalid_field_are_structured(self) -> None:
        cases = (
            lambda: self.support.describe("MissingService"),
            lambda: self.support.describe("MetadataService", "MissingMethod"),
            lambda: self.support.call(
                "MetadataService", "GetHealth", {"bogus": True}
            ),
        )
        for operation in cases:
            with self.subTest(operation=operation), self.assertRaises(
                HarnessError
            ) as raised:
                operation()
            self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)

    def test_streaming_call_is_rejected_without_touching_channel(self) -> None:
        count = len(self.channel.calls)
        with self.assertRaises(HarnessError) as raised:
            self.support.call("MissionService", "StreamEvents", {})
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)
        self.assertEqual(len(self.channel.calls), count)

    def test_grpc_unavailable_is_translated(self) -> None:
        import grpc

        path = "/dcs.metadata.v0.MetadataService/GetHealth"

        class UnavailableError(FakeRpcError, grpc.RpcError):
            pass

        self.channel.errors[path] = UnavailableError(
            grpc.StatusCode.UNAVAILABLE, "offline"
        )
        with self.assertRaises(HarnessError) as raised:
            self.support.call("MetadataService", "GetHealth", {})
        self.assertEqual(raised.exception.code, ErrorCode.GRPC_CONNECTION_FAILED)
        self.assertEqual(raised.exception.details["grpc_status"], "UNAVAILABLE")

    def test_builtin_plugin_contract_is_discoverable(self) -> None:
        resolver = PluginResolver(REPOSITORY_ROOT)
        discovered = resolver.discover()
        self.assertIn("grpc", discovered["builtin"])
        metadata = resolver.describe(resolver.resolve("grpc"))
        self.assertEqual(set(metadata["commands"]), {"services", "describe", "call"})


if __name__ == "__main__":
    unittest.main()
