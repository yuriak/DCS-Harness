from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.aircraft_catalog import AircraftCatalogRegistry  # noqa: E402
from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.plugin_api import PluginResolver  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from plugins import catalog  # noqa: E402


class CatalogPluginTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(
            repository_root=ROOT,
            environment_path=ROOT / "config" / "environment.yaml",
            environment={},
            runtime_root=ROOT / "runtime",
            generated_root=ROOT / "runtime" / "generated",
        )

    def test_status_fast_report_and_plugin_contract(self):
        status = catalog.invoke(self.context, "status", {})
        self.assertEqual(status["counts"]["aircraft"], 170)
        self.assertEqual(status["scope"], "static_pinned_definitions")
        self.assertFalse(status["preset_enrichment"]["included"])
        report = catalog.fast_report(self.context, None)
        self.assertEqual(report["health"], "ready")
        self.assertEqual(report["source_warning_count"], 15)

        resolver = PluginResolver(ROOT)
        self.assertIn("catalog", resolver.discover()["builtin"])
        metadata = resolver.describe(resolver.resolve("catalog"))
        self.assertEqual(metadata["runtime"], "stateless")
        self.assertIn("loadout-validate", metadata["commands"])

    def test_aircraft_search_preserves_exact_type_distinctions(self):
        result = catalog.invoke(
            self.context,
            "aircraft-search",
            {"query": "F16", "kind": "fixed_wing", "limit": 10},
        )
        ids = {item["type_id"] for item in result["aircraft"]}
        self.assertIn("F-16C bl.50", ids)
        self.assertIn("F-16C_50", ids)
        flags = {item["type_id"]: item["flyable"] for item in result["aircraft"]}
        self.assertFalse(flags["F-16C bl.50"])
        self.assertTrue(flags["F-16C_50"])

        only_flyable = catalog.invoke(
            self.context,
            "aircraft-search",
            {"query": "F16C50", "flyable": True},
        )
        self.assertEqual(
            [item["type_id"] for item in only_flyable["aircraft"]], ["F-16C_50"]
        )

    def test_aircraft_show_and_pylons_are_bounded(self):
        shown = catalog.invoke(
            self.context, "aircraft-show", {"aircraft": "j-11a"}
        )
        self.assertEqual(shown["type_id"], "J-11A")
        self.assertNotIn("allowed_store_clsids", shown["pylons"][0])

        pylons = catalog.invoke(
            self.context,
            "loadout-pylons",
            {"aircraft": "J-11A", "expand": True, "store_limit": 2},
        )
        self.assertTrue(pylons["expanded"])
        self.assertTrue(all(len(item["stores"]) <= 2 for item in pylons["pylons"]))
        self.assertTrue(any(item["stores_truncated"] for item in pylons["pylons"]))

    def test_store_query_returns_legal_pylons_and_respects_limit(self):
        result = catalog.invoke(
            self.context,
            "loadout-stores",
            {"aircraft": "J-11A", "query": "R-77", "limit": 5},
        )
        self.assertGreaterEqual(result["matched_count"], 1)
        r77 = next(item for item in result["stores"] if item["name"].startswith("R-77"))
        self.assertIn(3, r77["allowed_pylons"])
        self.assertLessEqual(result["count"], 5)

        pylon = catalog.invoke(
            self.context,
            "loadout-stores",
            {"aircraft": "J-11A", "pylon": 3, "query": "R77"},
        )
        self.assertTrue(all(item["allowed_pylons"] == [3] for item in pylon["stores"]))

    def test_presets_report_unavailable_enrichment_without_inventing_recommendations(self):
        result = catalog.invoke(
            self.context, "loadout-presets", {"aircraft": "J-11A"}
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["presets"], [])
        self.assertIn("pinned pydcs", result["enrichment"]["reason"])

    def test_loadout_validation_reports_compatibility_failures(self):
        stores = catalog.invoke(
            self.context,
            "loadout-stores",
            {"aircraft": "J-11A", "pylon": 3, "query": "R-77"},
        )["stores"]
        clsid = stores[0]["clsid"]
        valid = catalog.invoke(
            self.context,
            "loadout-validate",
            {
                "aircraft": "J-11A",
                "pylons": {"3": {"clsid": clsid, "settings": {"mode": 1}}},
            },
        )
        self.assertTrue(valid["valid"], valid)
        self.assertEqual(valid["settings_validation"], "shape_only_no_catalog_schema")

        invalid = catalog.invoke(
            self.context,
            "loadout-validate",
            {
                "aircraft": "J-11A",
                "pylons": {
                    "0": clsid,
                    "99": clsid,
                    "1": "{NOT-A-REAL-CLSID}",
                    "2": {"clsid": clsid, "settings": math.nan},
                },
            },
        )
        self.assertFalse(invalid["valid"])
        self.assertEqual(
            {item["code"] for item in invalid["failures"]},
            {
                "INVALID_PYLON_KEY",
                "PYLON_NOT_DECLARED",
                "STORE_NOT_FOUND",
                "INVALID_SETTINGS",
            },
        )

        unknown = catalog.invoke(
            self.context,
            "loadout-validate",
            {"aircraft": "Missing Plane", "pylons": {}},
        )
        self.assertFalse(unknown["valid"])
        self.assertEqual(unknown["failures"][0]["code"], "AIRCRAFT_NOT_FOUND")

        unavailable = catalog.invoke(
            self.context,
            "loadout-validate",
            {"aircraft": "MB-339APAN", "pylons": {"2": clsid}},
        )
        self.assertEqual(
            unavailable["failures"][0]["code"],
            "PYLON_DEFINITION_UNAVAILABLE",
        )

        huge_setting = catalog.invoke(
            self.context,
            "loadout-validate",
            {
                "aircraft": "J-11A",
                "pylons": {"3": {"clsid": clsid, "settings": {"huge": 10**10000}}},
            },
        )
        self.assertEqual(huge_setting["failures"][0]["code"], "INVALID_SETTINGS")

    def test_malformed_catalog_and_bad_arguments_are_structured(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "aircraft.json"
            path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            with self.assertRaises(HarnessError) as raised:
                AircraftCatalogRegistry(ROOT, path)
        self.assertEqual(raised.exception.code, ErrorCode.CAPABILITY_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason"], "CATALOG_INVALID")

        bad_calls = (
            lambda: catalog.invoke(
                self.context, "aircraft-search", {"query": "F-16", "limit": 101}
            ),
            lambda: catalog.invoke(
                self.context, "loadout-stores", {"aircraft": "J-11A", "pylon": 0}
            ),
            lambda: catalog.invoke(self.context, "status", {"extra": True}),
        )
        for call in bad_calls:
            with self.subTest(call=call), self.assertRaises(HarnessError) as error:
                call()
            self.assertEqual(error.exception.code, ErrorCode.INVALID_ARGUMENT)


if __name__ == "__main__":
    unittest.main()
