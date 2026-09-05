import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "skills/integration/assets/mission-authoring/payload_application.py"
PYDCS = ROOT / "third_party/pydcs"


def _load_asset():
    spec = importlib.util.spec_from_file_location("payload_application", ASSET)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PayloadApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PYDCS))
        import dcs

        cls.dcs = dcs
        cls.asset = _load_asset()

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
        position = dcs.Point(-250000, 600000, mission.terrain)
        group = mission.flight_group_inflight(
            country, "PAYLOAD TEST", dcs.planes.Su_27, position, 5000, 600,
            group_size=2,
        )
        return mission, group

    def _plan(self):
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
                    return {
                        str(pylon): {
                            "clsid": value[1]["clsid"],
                            "settings": {"test_option": 1},
                        }
                    }
        self.fail("Su-27 has no usable pinned pylon definition")

    def test_applies_every_unit_and_survives_pydcs_reload(self):
        mission, group = self._group()
        plan = self._plan()

        self.asset.apply_group_loadout(group, plan)
        report = self.asset.validate_group_loadout(group, plan)
        self.assertTrue(report["ok"], report)
        json.dumps(report, allow_nan=False)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.miz"
            mission.save(str(path))
            loaded = self.dcs.mission.Mission()
            self.assertFalse(loaded.load_file(str(path)))
            loaded_group = loaded.find_group("PAYLOAD TEST", "exact")
            reloaded = self.asset.validate_group_loadout(loaded_group, plan)

        self.assertTrue(reloaded["ok"], reloaded)
        self.assertEqual(len(reloaded["units"]), 2)
        json.dumps(reloaded, allow_nan=False)

    def test_invalid_plan_does_not_clear_existing_payload(self):
        _, group = self._group()
        before = [dict(unit.pylons) for unit in group.units]
        invalid_pylon = max(group.units[0].unit_type.pylons) + 100

        with self.assertRaisesRegex(ValueError, "does not declare"):
            self.asset.apply_group_loadout(group, {invalid_pylon: "{BAD}"})

        self.assertEqual([unit.pylons for unit in group.units], before)

    def test_reports_exact_payload_differences(self):
        _, group = self._group()
        plan = self._plan()
        self.asset.apply_group_loadout(group, plan)
        group.units[1].pylons.clear()

        report = self.asset.validate_group_loadout(group, plan)

        self.assertFalse(report["ok"])
        self.assertIn("MISSING_PYLONS", {item["code"] for item in report["failures"]})


if __name__ == "__main__":
    unittest.main()

