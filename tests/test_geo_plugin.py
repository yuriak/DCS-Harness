from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.geo_catalog import GeoCatalogRegistry  # noqa: E402
from dcs_harness_runtime.geo_math import (  # noqa: E402
    convert_unit,
    geographic_distance_m,
    geographic_initial_bearing_deg,
    geographic_offset,
    geographic_point,
)
from dcs_harness_runtime.plugin_api import PluginResolver  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from plugins import geo  # noqa: E402


SESSION_PATH = "/dcs.mission.v0.MissionService/GetSessionId"
THEATRE_PATH = "/dcs.world.v0.WorldService/GetTheatre"
EVAL_PATH = "/dcs.custom.v0.CustomService/Eval"


class FakeChannel:
    def __init__(self) -> None:
        self.responses: dict[str, list[Any]] = {}
        self.calls: list[dict[str, Any]] = []

    def unary_unary(
        self,
        path: str,
        request_serializer: Any,
        response_deserializer: Any,
        **kwargs: Any,
    ) -> Any:
        def call(
            request: Any, *, timeout: float | None = None, **call_kwargs: Any
        ) -> Any:
            self.calls.append({"path": path, "request": request, "timeout": timeout})
            responses = self.responses.get(path, [])
            if not responses:
                raise AssertionError(f"Unexpected fake RPC: {path}")
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response_deserializer(response.SerializeToString())

        return call

    def unary_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def stream_unary(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: None

    def stream_stream(self, *args: Any, **kwargs: Any) -> Any:
        return lambda *call_args, **call_kwargs: iter(())

    def close(self) -> None:
        pass


class GeoMathTests(unittest.TestCase):
    def test_zero_and_known_geographic_distance(self) -> None:
        self.assertEqual(geographic_distance_m((0.0, 0.0), (0.0, 0.0)), 0.0)
        self.assertAlmostEqual(
            geographic_distance_m((0.0, 0.0), (0.0, 1.0)),
            111_195.08,
            delta=1.0,
        )

    def test_cardinal_bearings_and_longitude_wrap(self) -> None:
        self.assertAlmostEqual(
            geographic_initial_bearing_deg((0.0, 0.0), (1.0, 0.0)), 0.0
        )
        self.assertAlmostEqual(
            geographic_initial_bearing_deg((0.0, 0.0), (0.0, 1.0)), 90.0
        )
        self.assertAlmostEqual(
            geographic_initial_bearing_deg((0.0, 179.5), (0.0, -179.5)),
            90.0,
        )

    def test_offset_round_trip(self) -> None:
        origin = (43.1143270846, 40.5697410759)
        destination = geographic_offset(origin, 315.0, 40.0 * 1_852.0)
        self.assertAlmostEqual(
            geographic_distance_m(origin, destination), 40.0 * 1_852.0, delta=0.01
        )
        self.assertAlmostEqual(
            geographic_initial_bearing_deg(origin, destination), 315.0, delta=0.01
        )

    def test_unit_round_trips(self) -> None:
        cases = (
            (12_345.0, "m", "km"),
            (12_345.0, "m", "NM"),
            (12_345.0, "m", "ft"),
            (250.0, "m/s", "km/h"),
            (250.0, "m/s", "knot"),
        )
        for value, source, target in cases:
            with self.subTest(source=source, target=target):
                converted = convert_unit(value, source, target)
                restored = convert_unit(
                    converted["output"]["value"], target, source
                )
                self.assertAlmostEqual(restored["output"]["value"], value)

    def test_invalid_non_finite_and_unit_dimension_are_structured(self) -> None:
        operations = (
            lambda: geographic_point(
                {"latitude_deg": math.nan, "longitude_deg": 0.0}
            ),
            lambda: geographic_point(
                {"latitude_deg": 91.0, "longitude_deg": 0.0}
            ),
            lambda: convert_unit(1.0, "m", "knot"),
            lambda: convert_unit(True, "m", "km"),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(
                HarnessError
            ) as raised:
                operation()
            self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)


class GeoCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = GeoCatalogRegistry(REPOSITORY_ROOT)

    def test_caucasus_catalog_contains_pinned_and_reviewed_locations(self) -> None:
        maps = self.registry.maps()
        self.assertEqual(len(maps), 1)
        caucasus = maps[0]
        self.assertEqual(caucasus["id"], "caucasus")
        self.assertEqual(caucasus["data_version"], "2026-08-31.1")
        self.assertEqual(
            caucasus["kinds"],
            {
                "airbase": 21,
                "bay": 2,
                "cape": 1,
                "city": 13,
                "gorge": 1,
                "hydroelectric-complex": 1,
                "lake": 2,
                "mountain": 2,
                "mountain-pass": 4,
                "port": 5,
                "reservoir": 5,
                "river-mouth": 4,
                "valley": 1,
            },
        )
        self.assertEqual(caucasus["location_count"], 62)
        self.assertEqual(
            caucasus["sources"]["pydcs-airports"]["revision"],
            "e20f328390aecaac2a7f82444b4f5a96ac6bb2c3",
        )
        self.assertEqual(
            caucasus["sources"]["wikidata-cities"]["license"], "CC0-1.0"
        )
        self.assertEqual(
            caucasus["sources"]["wikidata-operational-landmarks"]["license"],
            "CC0-1.0",
        )
        self.assertEqual(
            caucasus["sources"]["rosmorport-seaports"]["organization"],
            "FSUE Rosmorport",
        )

    def test_lookup_exact_alias_ambiguous_and_missing(self) -> None:
        gudauta = self.registry.lookup(
            "Caucasus", "caucasus.airbase.gudauta"
        )
        self.assertEqual(gudauta["name"], "Gudauta")
        self.assertEqual(gudauta["kind"], "airbase")
        self.assertAlmostEqual(gudauta["latitude_deg"], 43.1143270846)
        self.assertEqual(gudauta["metadata"]["runways"][0]["name"], "15-33")

        alias = self.registry.lookup(
            "CaucasusMap", "Senaki-Kolkhida", kind="airbase"
        )
        self.assertEqual(alias["name"], "Senaki-Kolkhi")

        with self.assertRaises(HarnessError) as ambiguous:
            self.registry.lookup("Caucasus", "Gudauta")
        self.assertEqual(
            ambiguous.exception.details["reason"], "LOCATION_AMBIGUOUS"
        )
        with self.assertRaises(HarnessError) as missing:
            self.registry.lookup("Caucasus", "Missing Place")
        self.assertEqual(missing.exception.details["reason"], "LOCATION_NOT_FOUND")

    def test_search_exact_substring_approximate_and_bounds(self) -> None:
        exact = self.registry.search("Caucasus", "Babushara", kind="airbase")
        self.assertEqual(exact["locations"][0]["name"], "Sukhumi-Babushara")
        self.assertEqual(exact["locations"][0]["match"]["type"], "alias_exact")

        substring = self.registry.search(
            "Caucasus", "Pashkov", kind="airbase"
        )
        self.assertEqual(substring["locations"][0]["name"], "Krasnodar-Pashkovsky")

        approximate = self.registry.search(
            "Caucasus", "gudata", kind="airbase"
        )
        self.assertEqual(approximate["locations"][0]["name"], "Gudauta")
        self.assertIn("approximate", approximate["locations"][0]["match"]["type"])

        limited = self.registry.search("Caucasus", "a", limit=1)
        self.assertEqual(limited["count"], 1)
        self.assertTrue(limited["truncated"])

    def test_operational_landmark_alias_elevation_and_provenance(self) -> None:
        poti_port = self.registry.lookup(
            "Caucasus", "Port of Poti", kind="port"
        )
        self.assertEqual(poti_port["id"], "caucasus.port.poti-sea-port")
        self.assertEqual(poti_port["source"]["type"], "Wikidata")
        self.assertEqual(poti_port["source"]["license"], "CC0-1.0")
        self.assertEqual(poti_port["metadata"]["wikidata_id"], "Q2917500")

        elbrus = self.registry.lookup("Caucasus", "Elbrus", kind="mountain")
        self.assertEqual(elbrus["id"], "caucasus.mountain.elbrus")
        self.assertEqual(elbrus["elevation_m"], 5642.0)

        inguri = self.registry.lookup(
            "Caucasus", "Inguri Mouth", kind="river-mouth"
        )
        self.assertEqual(inguri["id"], "caucasus.river-mouth.enguri")
        self.assertEqual(inguri["metadata"]["coordinate_role"], "river_mouth")

    def test_nearest_airbase(self) -> None:
        gudauta = self.registry.lookup(
            "Caucasus", "caucasus.airbase.gudauta"
        )
        nearest = self.registry.nearest(
            "Caucasus",
            {
                "latitude_deg": gudauta["latitude_deg"],
                "longitude_deg": gudauta["longitude_deg"],
            },
            kind="airbase",
            limit=3,
        )
        self.assertEqual(nearest["locations"][0]["id"], gudauta["id"])
        self.assertEqual(nearest["locations"][0]["distance_m"], 0.0)
        self.assertEqual(nearest["locations"][0]["bearing_deg"], 0.0)

    def test_unknown_map_and_malformed_catalog_are_structured(self) -> None:
        with self.assertRaises(HarnessError) as unknown:
            self.registry.maps()
            self.registry.map_catalog("MissingMap")
        self.assertEqual(unknown.exception.details["reason"], "MAP_NOT_FOUND")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "maps"
            data.mkdir()
            malformed = {
                "schema_version": 1,
                "id": "test",
                "name": "Test",
                "aliases": [],
                "data_version": "1",
                "sources": {"source": {"type": "test"}},
                "locations": [
                    {
                        "id": "test.city.bad",
                        "kind": "city",
                        "name": "Bad",
                        "aliases": [],
                        "latitude_deg": "not-a-number",
                        "longitude_deg": 0,
                        "source_id": "source",
                    }
                ],
            }
            (data / "test.json").write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(HarnessError) as raised:
                GeoCatalogRegistry(root, data)
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason"], "CATALOG_INVALID")

    def test_checked_catalog_matches_deterministic_builder(self) -> None:
        completed = subprocess.run(
            [sys.executable, "tools/build_geo_catalog.py", "--check"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class GeoPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = Context(
            repository_root=REPOSITORY_ROOT,
            environment_path=REPOSITORY_ROOT / "config" / "environment.yaml",
            environment={},
            runtime_root=REPOSITORY_ROOT / "runtime",
            generated_root=REPOSITORY_ROOT / "runtime" / "generated",
        )

    def test_status_maps_and_plugin_contract(self) -> None:
        status = geo.invoke(self.context, "status", {})
        self.assertEqual(status["map_count"], 1)
        self.assertTrue(status["live_conversion"]["implemented"])
        self.assertFalse(status["live_conversion"]["available"])
        self.assertEqual(
            status["live_conversion"]["error"]["code"],
            ErrorCode.CAPABILITY_UNAVAILABLE.value,
        )

        resolver = PluginResolver(REPOSITORY_ROOT)
        discovered = resolver.discover()
        self.assertIn("geo", discovered["builtin"])
        metadata = resolver.describe(resolver.resolve("geo"))
        self.assertEqual(metadata["runtime"], "stateless")
        self.assertIn("convert-unit", metadata["commands"])
        self.assertIn("convert", metadata["commands"])

    def test_geographic_and_local_calculations(self) -> None:
        distance = geo.invoke(
            self.context,
            "distance",
            {
                "coordinate_system": "geographic",
                "a": {"latitude_deg": 0, "longitude_deg": 0},
                "b": {"latitude_deg": 0, "longitude_deg": 1},
            },
        )
        self.assertAlmostEqual(distance["distance_km"], 111.19508, delta=0.001)

        north = geo.invoke(
            self.context,
            "bearing",
            {
                "coordinate_system": "dcs_local_xz",
                "a": {"x_m": 0, "z_m": 0},
                "b": {"x_m": 100, "z_m": 0},
            },
        )
        east = geo.invoke(
            self.context,
            "bearing",
            {
                "coordinate_system": "dcs_local_xz",
                "a": {"x_m": 0, "z_m": 0},
                "b": {"x_m": 0, "z_m": 100},
            },
        )
        self.assertEqual(north["bearing_deg"], 0.0)
        self.assertEqual(east["bearing_deg"], 90.0)
        self.assertEqual(east["reference"], "local_x_north_z_east")

    def test_offset_and_unit_conversion_contract(self) -> None:
        offset = geo.invoke(
            self.context,
            "offset",
            {
                "origin": {"latitude_deg": 43.1143270846, "longitude_deg": 40.5697410759},
                "bearing_deg": 315,
                "distance": {"value": 40, "unit": "NM"},
            },
        )
        self.assertAlmostEqual(offset["distance_nm"], 40.0)
        self.assertEqual(offset["bearing_deg"], 315.0)

        speed = geo.invoke(
            self.context,
            "convert-unit",
            {"value": 250, "from_unit": "m/s", "to_unit": "km/h"},
        )
        self.assertEqual(speed["quantity"], "speed")
        self.assertEqual(speed["input"], {"value": 250.0, "unit": "m/s"})
        self.assertEqual(speed["output"]["unit"], "km/h")
        self.assertAlmostEqual(speed["output"]["value"], 900.0)

    def test_invalid_arguments_and_unknown_command_are_structured(self) -> None:
        operations = (
            lambda: geo.invoke(self.context, "maps", {"extra": True}),
            lambda: geo.invoke(
                self.context,
                "distance",
                {
                    "coordinate_system": "geographic",
                    "a": {"latitude_deg": 0, "longitude_deg": 0, "y": 2},
                    "b": {"latitude_deg": 0, "longitude_deg": 1},
                },
            ),
            lambda: geo.invoke(self.context, "missing", {}),
        )
        expected = (
            ErrorCode.INVALID_ARGUMENT,
            ErrorCode.INVALID_ARGUMENT,
            ErrorCode.COMMAND_NOT_FOUND,
        )
        for operation, code in zip(operations, expected):
            with self.subTest(code=code), self.assertRaises(HarnessError) as raised:
                operation()
            self.assertEqual(raised.exception.code, code)


class GeoLiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = Context(
            repository_root=REPOSITORY_ROOT,
            environment_path=REPOSITORY_ROOT / "config" / "environment.yaml",
            environment={
                "setup": {"status": "READY"},
                "grpc": {
                    "client_host": "127.0.0.1",
                    "port": 50051,
                    "eval_enabled": True,
                },
            },
            runtime_root=REPOSITORY_ROOT / "runtime",
            generated_root=REPOSITORY_ROOT / "runtime" / "generated",
        )
        self.channel = FakeChannel()
        self.context._grpc_channel = self.channel
        self.context.ensure_generated_import_path()
        from dcs_grpc.dcs.custom.v0 import custom_pb2
        from dcs_grpc.dcs.mission.v0 import mission_pb2
        from dcs_grpc.dcs.world.v0 import world_pb2

        self.eval_response = custom_pb2.EvalResponse
        self.session_response = mission_pb2.GetSessionIdResponse
        self.theatre_response = world_pb2.GetTheatreResponse

    def tearDown(self) -> None:
        self.context.close()

    def queue_metadata(self, *session_ids: int) -> None:
        self.channel.responses.setdefault(SESSION_PATH, []).extend(
            self.session_response(session_id=value) for value in session_ids
        )
        self.channel.responses.setdefault(THEATRE_PATH, []).append(
            self.theatre_response(theatre="Caucasus")
        )

    def queue_eval(self, value: Any) -> None:
        self.channel.responses.setdefault(EVAL_PATH, []).append(
            self.eval_response(json=json.dumps(value))
        )

    def test_status_reports_live_session_theatre_and_fixed_probe(self) -> None:
        self.queue_metadata(42)
        self.queue_eval({"available": True})
        status = geo.invoke(self.context, "status", {})["live_conversion"]
        self.assertTrue(status["available"])
        self.assertEqual(status["session_id"], "42")
        self.assertEqual(status["theatre"], "Caucasus")
        eval_call = next(call for call in self.channel.calls if call["path"] == EVAL_PATH)
        self.assertIn("type(coord.LLtoLO)", eval_call["request"].lua)

    def test_geographic_to_local_is_bounded_and_explicit_xyz(self) -> None:
        self.queue_metadata(42, 42)
        self.queue_eval({"ok": True, "x_m": -1.25, "y_m": 17.0, "z_m": 2.5})
        result = geo.invoke(
            self.context,
            "convert",
            {
                "direction": "geographic_to_local",
                "geographic": {
                    "latitude_deg": 43.1143270846,
                    "longitude_deg": 40.5697410759,
                    "elevation_m": 17,
                },
            },
        )
        self.assertEqual(result["source"], "live_dcs")
        self.assertEqual(result["session_id"], "42")
        self.assertEqual(result["theatre"], "Caucasus")
        self.assertEqual(
            result["output"]["local"],
            {"x_m": -1.25, "y_m": 17.0, "z_m": 2.5},
        )
        lua = next(call for call in self.channel.calls if call["path"] == EVAL_PATH)[
            "request"
        ].lua
        self.assertIn("pcall(coord.LLtoLO", lua)
        self.assertNotIn("latitude_deg", lua)

    def test_local_to_geographic_does_not_confuse_y_and_z(self) -> None:
        self.queue_metadata(77, 77)
        self.queue_eval(
            {
                "ok": True,
                "latitude_deg": 43.1,
                "longitude_deg": 40.5,
                "elevation_m": 123.0,
            }
        )
        result = geo.invoke(
            self.context,
            "convert",
            {
                "direction": "local_to_geographic",
                "local": {"x_m": 100, "y_m": 123, "z_m": 900},
            },
        )
        self.assertEqual(result["output"]["geographic"]["elevation_m"], 123.0)
        lua = next(call for call in self.channel.calls if call["path"] == EVAL_PATH)[
            "request"
        ].lua
        self.assertIn("{x=100.0, y=123.0, z=900.0}", lua)

    def test_invalid_input_is_rejected_before_live_calls(self) -> None:
        with self.assertRaises(HarnessError) as raised:
            geo.invoke(
                self.context,
                "convert",
                {
                    "direction": "geographic_to_local",
                    "geographic": {"latitude_deg": math.inf, "longitude_deg": 0},
                },
            )
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(self.channel.calls, [])

    def test_eval_disabled_is_capability_unavailable_before_live_calls(self) -> None:
        self.context.environment["grpc"]["eval_enabled"] = False
        with self.assertRaises(HarnessError) as raised:
            geo.invoke(
                self.context,
                "convert",
                {
                    "direction": "local_to_geographic",
                    "local": {"x_m": 0, "z_m": 0},
                },
            )
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)
        self.assertEqual(self.channel.calls, [])

    def test_unavailable_and_malformed_results_are_structured(self) -> None:
        self.queue_metadata(1, 1)
        self.queue_eval({"ok": False, "reason": "coordinate_api_unavailable"})
        with self.assertRaises(HarnessError) as unavailable:
            geo.invoke(
                self.context,
                "convert",
                {
                    "direction": "local_to_geographic",
                    "local": {"x_m": 0, "z_m": 0},
                },
            )
        self.assertEqual(
            unavailable.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE
        )

        self.context._grpc_stubs.clear()
        self.queue_metadata(2)
        self.queue_eval({"ok": True, "x_m": "bad", "y_m": 0, "z_m": 0})
        with self.assertRaises(HarnessError) as malformed:
            geo.invoke(
                self.context,
                "convert",
                {
                    "direction": "geographic_to_local",
                    "geographic": {"latitude_deg": 43, "longitude_deg": 40},
                },
            )
        self.assertEqual(malformed.exception.code, ErrorCode.GRPC_CALL_FAILED)

    def test_session_rollover_during_conversion_is_rejected(self) -> None:
        self.queue_metadata(10, 11)
        self.queue_eval({"ok": True, "x_m": 1, "y_m": 2, "z_m": 3})
        with self.assertRaises(HarnessError) as raised:
            geo.invoke(
                self.context,
                "convert",
                {
                    "direction": "geographic_to_local",
                    "geographic": {"latitude_deg": 43, "longitude_deg": 40},
                },
            )
        self.assertEqual(
            raised.exception.details["reason"],
            "SESSION_CHANGED_DURING_CONVERSION",
        )


if __name__ == "__main__":
    unittest.main()
