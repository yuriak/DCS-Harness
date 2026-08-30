from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from dcs_harness_runtime.telemetry_capture import (  # noqa: E402
    SNAPSHOT_LUA,
    TelemetrySnapshotSource,
    normalize_snapshot,
)


SESSION_PATH = "/dcs.mission.v0.MissionService/GetSessionId"
EVAL_PATH = "/dcs.custom.v0.CustomService/Eval"


def raw_unit(index: int = 1, *, category: int = 0) -> dict[str, Any]:
    return {
        "unit_id": index,
        "unit_name": f"Unit {index}",
        "unit_type": "Su-25" if category == 0 else "T-55",
        "unit_country": "RUSSIA",
        "player_name": "Pilot" if index == 1 else None,
        "group_id": 10,
        "group_name": "Group 1",
        "group_category": category,
        "coalition": 1,
        "position": {"x": 100.0, "y": 200.0, "z": 300.0},
        "forward": {"x": 0.0, "y": 0.0, "z": 1.0},
        "velocity": {"x": 3.0, "y": -2.0, "z": 4.0},
        "life": 32.0,
        "life_initial": 32.0,
        "fuel_fraction": 0.5 if category in {0, 1} else None,
        "in_air": category == 0,
    }


def raw_snapshot(units: list[Any] | None = None) -> dict[str, Any]:
    units = [raw_unit()] if units is None else units
    return {
        "source": "mission_lua_batch",
        "mission_time": 123.5,
        "coalitions_enumerated": 3,
        "groups_seen": 1,
        "inactive_count": 0,
        "error_count": 0,
        "errors": [],
        "units": units,
        "unit_count": len(units),
        "partial": False,
    }


def normalize(raw: Any) -> dict[str, Any]:
    return normalize_snapshot(
        raw,
        session_id="42",
        snapshot_id=1,
        captured_at="2026-08-30T00:00:00+00:00",
        capture_duration_ms=12.5,
    )


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


class TelemetryNormalizationTests(unittest.TestCase):
    def test_normalizes_explicit_axes_heading_and_speed(self) -> None:
        result = normalize(raw_snapshot())
        sample = result["units"][0]
        self.assertEqual(result["unit_count"], 1)
        self.assertEqual(result["heading_reference"], "dcs_local_x_north_z_east")
        self.assertEqual(
            sample["position"],
            {
                "x_m": 100.0,
                "y_m": 200.0,
                "z_m": 300.0,
                "latitude_deg": None,
                "longitude_deg": None,
            },
        )
        self.assertEqual(sample["heading_deg"], 90.0)
        self.assertEqual(sample["ground_speed_mps"], 5.0)
        self.assertEqual(sample["vertical_speed_mps"], -2.0)
        self.assertEqual(sample["instance_id"], None)

    def test_unsupported_fuel_and_player_fields_remain_null(self) -> None:
        unit = raw_unit(category=2)
        unit["player_name"] = None
        sample = normalize(raw_snapshot([unit]))["units"][0]
        self.assertEqual(sample["unit"]["category"], "GROUND")
        self.assertIsNone(sample["fuel_fraction"])
        self.assertIsNone(sample["player_name"])

    def test_live_unit_with_empty_name_is_preserved_as_null(self) -> None:
        unit = raw_unit(category=2)
        unit["unit_name"] = ""
        result = normalize(raw_snapshot([unit]))
        self.assertEqual(result["unit_count"], 1)
        self.assertIsNone(result["units"][0]["unit"]["name"])
        self.assertFalse(result["partial"])

    def test_bad_unit_is_skipped_and_snapshot_marked_partial(self) -> None:
        bad = raw_unit(2)
        bad["position"]["y"] = math.inf
        result = normalize(raw_snapshot([raw_unit(1), bad]))
        self.assertEqual(result["unit_count"], 1)
        self.assertEqual(result["observed_unit_count"], 2)
        self.assertTrue(result["partial"])
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["scope"], "normalization")

    def test_source_partial_count_is_not_limited_to_error_samples(self) -> None:
        raw = raw_snapshot([])
        raw.update(
            partial=True,
            error_count=20,
            errors=[{"scope": "unit", "name": "u", "reason": "bad"}],
        )
        result = normalize(raw)
        self.assertEqual(result["error_count"], 20)
        self.assertEqual(len(result["errors"]), 1)
        self.assertTrue(result["partial"])

    def test_empty_lua_tables_are_accepted_only_as_empty_arrays(self) -> None:
        raw = raw_snapshot([])
        raw["units"] = {}
        raw["errors"] = {}
        result = normalize(raw)
        self.assertEqual(result["units"], [])
        self.assertEqual(result["errors"], [])

        raw["units"] = {"unexpected": True}
        with self.assertRaises(HarnessError) as raised:
            normalize(raw)
        self.assertEqual(raised.exception.code, ErrorCode.GRPC_CALL_FAILED)

    def test_malformed_or_failed_capture_is_structured(self) -> None:
        cases = [
            None,
            {},
            {**raw_snapshot([]), "coalitions_enumerated": 0},
            {**raw_snapshot([]), "partial": "false"},
            {**raw_snapshot([]), "unit_count": 1},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(HarnessError) as raised:
                normalize(value)
            self.assertEqual(raised.exception.code, ErrorCode.GRPC_CALL_FAILED)

    def test_synthetic_100_300_500_normalization(self) -> None:
        for size in (100, 300, 500):
            with self.subTest(size=size):
                result = normalize(
                    raw_snapshot([raw_unit(index + 1) for index in range(size)])
                )
                self.assertEqual(result["unit_count"], size)
                self.assertFalse(result["partial"])

    def test_lua_source_is_read_only_and_batch_oriented(self) -> None:
        self.assertEqual(SNAPSHOT_LUA.lstrip()[0:5], "local")
        self.assertIn("coalition.getGroups", SNAPSHOT_LUA)
        self.assertIn("group:getUnits", SNAPSHOT_LUA)
        self.assertIn("unit:getPosition", SNAPSHOT_LUA)
        for forbidden in ("coalition.addGroup", ":destroy(", ":getController("):
            self.assertNotIn(forbidden, SNAPSHOT_LUA)


class TelemetrySourceBoundaryTests(unittest.TestCase):
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

        self.eval_response = custom_pb2.EvalResponse
        self.session_response = mission_pb2.GetSessionIdResponse

    def tearDown(self) -> None:
        self.context.close()

    def queue(self, before: int, after: int, raw: Any) -> None:
        self.channel.responses[SESSION_PATH] = [
            self.session_response(session_id=before),
            self.session_response(session_id=after),
        ]
        self.channel.responses[EVAL_PATH] = [
            self.eval_response(json=json.dumps(raw))
        ]

    def test_capture_uses_one_eval_and_binds_current_session(self) -> None:
        self.queue(42, 42, raw_snapshot())
        result = TelemetrySnapshotSource(self.context).capture(snapshot_id=7)
        self.assertEqual(result["session_id"], "42")
        self.assertEqual(result["snapshot_id"], 7)
        self.assertEqual(
            [call["path"] for call in self.channel.calls],
            [SESSION_PATH, EVAL_PATH, SESSION_PATH],
        )
        self.assertEqual(
            next(call for call in self.channel.calls if call["path"] == EVAL_PATH)[
                "request"
            ].lua,
            SNAPSHOT_LUA,
        )

    def test_session_rollover_is_rejected(self) -> None:
        self.queue(42, 43, raw_snapshot())
        with self.assertRaises(HarnessError) as raised:
            TelemetrySnapshotSource(self.context).capture()
        self.assertEqual(
            raised.exception.details["reason"], "SESSION_CHANGED_DURING_CAPTURE"
        )

    def test_eval_disabled_is_capability_unavailable(self) -> None:
        self.context.environment["grpc"]["eval_enabled"] = False
        self.channel.responses[SESSION_PATH] = [
            self.session_response(session_id=42)
        ]
        with self.assertRaises(HarnessError) as raised:
            TelemetrySnapshotSource(self.context).capture()
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)

    def test_invalid_snapshot_id_is_rejected_before_rpc(self) -> None:
        with self.assertRaises(HarnessError) as raised:
            TelemetrySnapshotSource(self.context).capture(snapshot_id=0)
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(self.channel.calls, [])


if __name__ == "__main__":
    unittest.main()
