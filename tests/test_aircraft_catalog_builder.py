import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools" / "build_aircraft_catalog.py"
CATALOG = ROOT / "tools" / "data" / "catalog" / "aircraft.json"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_aircraft_catalog", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AircraftCatalogBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = _load_builder()
        cls.catalog = cls.builder.build_catalog("2026-09-04T00:00:00Z")

    def _aircraft(self, type_id):
        return next(
            item for item in self.catalog["aircraft"] if item["type_id"] == type_id
        )

    def test_builds_pinned_base_catalog_without_local_presets(self):
        catalog = self.catalog
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["source"]["type"], "pinned_pydcs")
        self.assertEqual(len(catalog["source"]["revision"]), 40)
        self.assertEqual(catalog["generator"]["version"], "1")
        self.assertFalse(catalog["preset_enrichment"]["included"])
        self.assertGreater(catalog["counts"]["fixed_wing"], 100)
        self.assertGreater(catalog["counts"]["helicopter"], 20)
        self.assertGreater(catalog["counts"]["stores"], 1000)
        json.dumps(catalog, allow_nan=False)

    def test_aircraft_tasks_and_pylon_compatibility_are_source_derived(self):
        legacy_f16 = self._aircraft("F-16C bl.50")
        self.assertFalse(legacy_f16["flyable"])
        f16 = self._aircraft("F-16C_50")
        self.assertTrue(f16["flyable"])
        self.assertEqual(f16["kind"], "fixed_wing")
        self.assertIn("SEAD", {task["name"] for task in f16["known_tasks"]})

        j11 = self._aircraft("J-11A")
        self.assertTrue(j11["flyable"])
        pylon3 = next(pylon for pylon in j11["pylons"] if pylon["index"] == 3)
        r77 = next(store for store in self.catalog["stores"] if store["name"].startswith("R-77"))
        self.assertTrue(pylon3["definition_available"])
        self.assertIn(r77["clsid"], pylon3["allowed_store_clsids"])

        known_clsids = {store["clsid"] for store in self.catalog["stores"]}
        referenced = {
            clsid
            for aircraft in self.catalog["aircraft"]
            for pylon in aircraft["pylons"]
            for clsid in pylon["allowed_store_clsids"]
        }
        self.assertLessEqual(referenced, known_clsids)

    def test_checked_catalog_matches_builder(self):
        completed = subprocess.run(
            [sys.executable, str(BUILDER), "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        checked = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(checked["counts"], self.catalog["counts"])


if __name__ == "__main__":
    unittest.main()
