import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "skills/integration/assets/mission-authoring"
PYDCS = ROOT / "third_party/pydcs"


def _load_asset(name):
    spec = importlib.util.spec_from_file_location(name, ASSET_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MissionAuthoringValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PYDCS))
        import dcs
        from dcs import task, triggers
        from dcs.action import DoScriptFile
        from dcs.mission import StartType

        cls.dcs = dcs
        cls.task = task
        cls.triggers = triggers
        cls.DoScriptFile = DoScriptFile
        cls.StartType = StartType
        cls.geometry = _load_asset("formation_geometry")
        cls.payload = _load_asset("payload_application")
        cls.validator = _load_asset("mission_validator")

    @classmethod
    def tearDownClass(cls):
        try:
            sys.path.remove(str(PYDCS))
        except ValueError:
            pass

    def _payload_plan(self):
        plane_type = self.dcs.planes.Su_27
        for pylon in sorted(plane_type.pylons):
            definition = getattr(plane_type, f"Pylon{pylon}")
            for value in vars(definition).values():
                if (
                    isinstance(value, tuple)
                    and len(value) == 2
                    and value[0] == pylon
                    and isinstance(value[1], dict)
                    and value[1].get("clsid")
                ):
                    return {str(pylon): value[1]["clsid"]}
        self.fail("Su-27 has no usable pinned pylon definition")

    def _build_and_reload(self, root):
        dcs = self.dcs
        mission = dcs.mission.Mission(terrain=dcs.terrain.Caucasus())
        country = mission.coalition["red"].country("Russia")

        vehicle_type = dcs.countries.Russia.Vehicle.AirDefence.SA_11_Buk_SR_9S18M1
        anchor = dcs.Point(-285000, 655000, mission.terrain)
        ground = mission.vehicle_group(country, "SITE TEST", vehicle_type, anchor)
        ground.add_unit(mission.vehicle("SITE TEST 2", vehicle_type))
        self.geometry.place_group_units(ground, anchor, [(0, 0), (80, 20)])

        airport = mission.terrain.airports["Gudauta"]
        aircraft = mission.flight_group_from_airport(
            country,
            "AIR TEST",
            dcs.planes.Su_27,
            airport,
            maintask=self.task.CAP,
            start_type=self.StartType.Cold,
            group_size=1,
        )
        aircraft.add_runway_waypoint(airport)
        aircraft.add_waypoint(
            airport.position.point_from_heading(90, 50000), 6000, 700, "STATION"
        )
        plan = self._payload_plan()
        self.payload.apply_group_loadout(aircraft, plan)

        resources = []
        for name in ("mist.lua", "Moose_.lua", "startup.lua"):
            path = root / name
            path.write_text(f"-- {name}\n", encoding="utf-8")
            resources.append(mission.map_resource.add_resource_file(str(path)))
        trigger = self.triggers.TriggerStart(comment="validator test startup")
        for resource in resources:
            trigger.actions.append(self.DoScriptFile(resource))
        mission.triggerrules.triggers.append(trigger)

        path = root / "validator.miz"
        mission.save(str(path))
        loaded = dcs.mission.Mission()
        self.assertFalse(loaded.load_file(str(path)))
        return loaded, path, anchor, plan, vehicle_type.id

    def _spec(self, mission, anchor, plan, vehicle_type_id):
        airport = mission.terrain.airports["Gudauta"]
        return {
            "ground_groups": [{
                "name": "SITE TEST",
                "unit_count": 2,
                "unit_types": {vehicle_type_id: 2},
                "anchor": anchor,
                "max_anchor_distance_m": 100,
                "max_formation_radius_m": 100,
            }],
            "aircraft_groups": [{
                "name": "AIR TEST",
                "unit_count": 1,
                "unit_types": {"Su-27": 1},
                "payload": plan,
                "fuel": {"min": 1, "max": self.dcs.planes.Su_27.fuel_max},
                "route": {
                    "point_count": 3,
                    "altitude": {"min": 0},
                    "speed": {"min": 0},
                },
                "late_activation": False,
                "start_mode": "cold",
                "home_base": "Gudauta",
                "task": "CAP",
            }],
            "geography": [{
                "label": "red bullseye",
                "point": mission.coalition["red"].bullseye,
                "intended_area": {
                    "x": mission.coalition["red"].bullseye["x"],
                    "y": mission.coalition["red"].bullseye["y"],
                    "max_distance_m": 0,
                },
                "expected_airport": "Gudauta",
                "max_airport_distance_m": airport.position.distance_to_point(
                    self.dcs.Point(
                        mission.coalition["red"].bullseye["x"],
                        mission.coalition["red"].bullseye["y"],
                        mission.terrain,
                    )
                ) + 1,
            }],
            "startup": {"resources": ["mist.lua", "Moose_.lua", "startup.lua"]},
        }

    def test_full_structural_contract_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission, path, anchor, plan, vehicle_type = self._build_and_reload(Path(tmp))
            report = self.validator.validate_mission_structure(
                mission,
                miz_path=path,
                **self._spec(mission, anchor, plan, vehicle_type),
            )

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["summary"]["failure_count"], 0)
        self.assertTrue(report["startup"]["compiled_checked"])
        json.dumps(report, allow_nan=False)

    def test_reports_structural_drift_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission, path, anchor, plan, vehicle_type = self._build_and_reload(Path(tmp))
            ground = mission.find_group("SITE TEST", "exact")
            aircraft = mission.find_group("AIR TEST", "exact")
            ground.units[1].position = self.dcs.Point(0, 0, mission.terrain)
            aircraft.units[0].pylons.clear()
            aircraft.late_activation = True
            aircraft.task = "CAS"
            report = self.validator.validate_mission_structure(
                mission,
                miz_path=path,
                **self._spec(mission, anchor, plan, vehicle_type),
            )

        self.assertFalse(report["ok"])
        codes = {
            failure["code"]
            for section in (*report["ground_groups"], *report["aircraft_groups"])
            for failure in section["failures"]
        }
        self.assertTrue(
            {
                "SUSPICIOUS_WORLD_ORIGIN",
                "ANCHOR_DISTANCE_EXCEEDED",
                "FORMATION_EXTENT_EXCEEDED",
                "PAYLOAD_MISMATCH",
                "LATE_ACTIVATION_MISMATCH",
                "ROLE_TASK_MISMATCH",
            }.issubset(codes),
            codes,
        )

    def test_reports_missing_compiled_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission, path, _, _, _ = self._build_and_reload(Path(tmp))
            result = self.validator.validate_startup(
                mission,
                {"resources": ["mist.lua", "Moose_.lua", "startup.lua"]},
                miz_path=Path(tmp) / "missing.miz",
            )

        self.assertFalse(result["ok"])
        self.assertIn("MIZ_ARCHIVE_UNREADABLE", {item["code"] for item in result["failures"]})


if __name__ == "__main__":
    unittest.main()

