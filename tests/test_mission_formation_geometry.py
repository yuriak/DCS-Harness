import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    ROOT
    / "skills"
    / "integration"
    / "assets"
    / "mission-authoring"
    / "formation_geometry.py"
)
PYDCS = ROOT / "third_party" / "pydcs"


def _load_helper():
    spec = importlib.util.spec_from_file_location("formation_geometry", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MissionFormationGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PYDCS))
        import dcs

        cls.dcs = dcs
        cls.geometry = _load_helper()

    @classmethod
    def tearDownClass(cls):
        try:
            sys.path.remove(str(PYDCS))
        except ValueError:
            pass

    def _group(self):
        dcs = self.dcs
        mission = dcs.mission.Mission(terrain=dcs.terrain.Caucasus())
        country = mission.coalition["red"].country("Russia")
        vehicle_type = dcs.countries.Russia.Vehicle.AirDefence.SA_11_Buk_SR_9S18M1
        anchor = dcs.Point(-285000, 655000, mission.terrain)
        group = mission.vehicle_group(country, "TEST BATTERY", vehicle_type, anchor)
        group.add_unit(mission.vehicle("TEST BATTERY 2", vehicle_type))
        group.add_unit(mission.vehicle("TEST BATTERY 3", vehicle_type))
        return mission, group, anchor

    def test_places_every_unit_and_survives_pydcs_reload(self):
        dcs = self.dcs
        mission, group, anchor = self._group()
        offsets = [(0, 0), (80, 20), (-60, 70)]

        self.geometry.place_group_units(group, anchor, offsets, headings_deg=[0, 90, 180])
        report = self.geometry.validate_group_geometry(
            group,
            anchor=anchor,
            max_anchor_distance_m=110,
            min_pairwise_separation_m=50,
            max_formation_radius_m=100,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual([unit.heading for unit in group.units], [0, 90, 180])
        json.dumps(report, allow_nan=False)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "formation.miz"
            mission.save(str(path))
            loaded = dcs.mission.Mission()
            self.assertFalse(loaded.load_file(str(path)))
            loaded_group = loaded.find_group("TEST BATTERY", "exact")
            self.assertIsNotNone(loaded_group)
            actual = [(unit.position.x, unit.position.y) for unit in loaded_group.units]

        expected = [(anchor.x + dx, anchor.y + dy) for dx, dy in offsets]
        self.assertEqual(actual, expected)
        self.assertTrue(all(math.hypot(x, y) > 1 for x, y in actual))

    def test_rejects_incomplete_placement_without_partial_mutation(self):
        _, group, anchor = self._group()
        before = [(unit.position.x, unit.position.y) for unit in group.units]

        with self.assertRaisesRegex(ValueError, "offset count"):
            self.geometry.place_group_units(group, anchor, [(0, 0), (20, 0)])

        after = [(unit.position.x, unit.position.y) for unit in group.units]
        self.assertEqual(after, before)

    def test_reports_origin_extent_and_separation_failures(self):
        _, group, anchor = self._group()
        group.units[0].position = anchor.new_in_same_map(0, 0)
        group.units[1].position = anchor.new_in_same_map(10, 0)
        group.units[2].position = anchor.new_in_same_map(500, 0)

        report = self.geometry.validate_group_geometry(
            group,
            anchor=anchor.new_in_same_map(0, 0),
            max_anchor_distance_m=200,
            min_pairwise_separation_m=20,
            max_formation_radius_m=250,
        )

        self.assertFalse(report["ok"])
        codes = {failure["code"] for failure in report["failures"]}
        self.assertEqual(
            codes,
            {
                "SUSPICIOUS_WORLD_ORIGIN",
                "ANCHOR_DISTANCE_EXCEEDED",
                "PAIRWISE_SEPARATION_BELOW_MINIMUM",
                "FORMATION_RADIUS_EXCEEDED",
            },
        )

    def test_rejects_nonfinite_inputs(self):
        _, group, anchor = self._group()

        with self.assertRaisesRegex(ValueError, "finite"):
            self.geometry.place_group_units(
                group, anchor, [(0, 0), (math.nan, 0), (10, 10)]
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.geometry.validate_group_geometry(
                group, min_pairwise_separation_m=-1
            )


if __name__ == "__main__":
    unittest.main()
