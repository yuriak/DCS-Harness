from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.context import Context  # noqa: E402
from dcs_harness_runtime.event_collector import EventCollector  # noqa: E402
from dcs_harness_runtime.event_normalization import (  # noqa: E402
    combat_fingerprint,
    normalize_combat_event,
)
from dcs_harness_runtime.event_store import EventStore, EventStoreCatalog  # noqa: E402
from dcs_harness_runtime.logging_utils import LifecycleLogger  # noqa: E402
from dcs_harness_runtime.native_combat import NativeCombatObserver  # noqa: E402
from dcs_harness_runtime.result import HarnessError  # noqa: E402
from plugins.events import _combat_view  # noqa: E402


def native_event(
    sequence: int,
    mission_time: float,
    event_type: str,
    *,
    initiator_id: int = 10,
    target_id: int | None = 20,
    weapon_type: str | None = "R-27ER",
) -> dict:
    return {
        "native_sequence": sequence,
        "mission_time": mission_time,
        "event_type": event_type,
        "initiator": {
            "kind": "unit",
            "unit_id": initiator_id,
            "unit_name": f"Unit {initiator_id}",
            "group_id": initiator_id + 100,
            "group_name": f"Group {initiator_id}",
            "type": "J-11A",
            "coalition": "red",
        },
        "target": (
            {
                "kind": "unit",
                "unit_id": target_id,
                "unit_name": f"Unit {target_id}",
                "group_id": target_id + 100,
                "group_name": f"Group {target_id}",
                "type": "F-16C_50",
                "coalition": "blue",
            }
            if target_id is not None
            else None
        ),
        "weapon": (
            {"runtime_id": None, "type": weapon_type, "event_weapon_name": None}
            if weapon_type
            else None
        ),
    }


def normalized(raw: dict) -> dict:
    value = normalize_combat_event(
        raw["event_type"],
        raw["mission_time"],
        raw,
        source="native_combat",
    )
    assert value is not None
    return value


def ready_context(root: Path) -> Context:
    return Context(
        repository_root=root,
        environment_path=root / "config" / "environment.yaml",
        environment={
            "setup": {"status": "READY"},
            "grpc": {
                "client_host": "127.0.0.1",
                "port": 50051,
                "eval_enabled": True,
            },
        },
        runtime_root=root / "runtime",
        generated_root=root / "runtime" / "generated",
    )


class CombatNormalizationTests(unittest.TestCase):
    def test_ejection_parachute_source_shapes_share_fingerprint(self) -> None:
        first = {"event_type": "ejection", "initiator": {"unit_id": 1},
                 "target": {"kind": "static", "object_name": "pilot"}}
        second = {**first, "target": {"kind": "unknown", "object_id": 0,
                                      "object_name": "pilot"}}
        self.assertEqual(combat_fingerprint(first), combat_fingerprint(second))
        second["target"]["object_name"] = "another pilot"
        self.assertNotEqual(combat_fingerprint(first), combat_fingerprint(second))
    def test_grpc_and_native_paths_share_a_fingerprint_without_weapon_id(self) -> None:
        grpc_payload = {
            "time": 10.0,
            "hit": {
                "initiator": {
                    "unit": {
                        "id": 10,
                        "name": "Unit 10",
                        "type": "J-11A",
                        "coalition": "COALITION_RED",
                        "group": {"id": 110, "name": "Group 10"},
                    }
                },
                "target": {
                    "unit": {
                        "id": 20,
                        "name": "Unit 20",
                        "type": "F-16C_50",
                        "coalition": "COALITION_BLUE",
                        "group": {"id": 120, "name": "Group 20"},
                    }
                },
                "weapon": {"type": "R-27ER"},
            },
        }
        grpc = normalize_combat_event("hit", 10.0, grpc_payload, source="grpc")
        native = normalized(native_event(1, 10.0, "hit"))

        self.assertEqual(combat_fingerprint(grpc), combat_fingerprint(native))
        self.assertEqual(grpc["initiator"]["coalition"], "red")
        self.assertIsNone(native["weapon"]["runtime_id"])

    def test_weapon_only_event_is_not_a_safe_dedup_fingerprint(self) -> None:
        value = {
            "event_type": "shot",
            "initiator": None,
            "target": None,
            "weapon": {"type": "shell", "event_weapon_name": None},
        }
        self.assertIsNone(combat_fingerprint(value))


class EventStoreCombatTests(unittest.TestCase):
    def test_same_time_kill_arriving_after_loss_still_attributes_loss(self) -> None:
        rows = []
        for event_id, raw in [(1, native_event(1, 10, "unit_lost", initiator_id=20,
                                               target_id=None, weapon_type=None)),
                              (2, native_event(2, 10, "kill"))]:
            rows.append(dict(id=event_id, session_id="1", mission_time=10,
                             received_at="now", event_type=raw["event_type"],
                             source="native_combat", sources=["native_combat"],
                             normalized=normalized(raw)))
        result = _combat_view(list(reversed(rows)))
        self.assertEqual(result[1]["attribution"]["evidence_event_id"], 2)

    def test_group_command_filter_reads_noncombat_payload(self) -> None:
        identities = dict.fromkeys(["initiator_unit", "initiator_group", "target_unit",
                                    "target_group", "unit", "group"])
        identities["group"] = "Pilot"
        row = {"event_type": "group_command", "normalized": None,
               "payload": {"group_command": {"group": {
                   "id": 1, "name": "Pilot", "coalition": "COALITION_RED"}}}}
        self.assertTrue(EventStore._matches(row, identities=identities, coalition="red"))
        identities["group"] = "Other"
        self.assertFalse(EventStore._matches(row, identities=identities, coalition=None))

    def test_cross_source_dedup_preserves_provenance_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.sqlite")
            store.initialize()
            native = native_event(1, 10.1, "hit")
            grpc_payload = {
                "hit": {
                    "initiator": {
                        "unit": {
                            "id": 10,
                            "name": "Unit 10",
                            "type": "J-11A",
                            "coalition": "COALITION_RED",
                            "group": {"id": 110, "name": "Group 10"},
                        }
                    },
                    "target": {
                        "unit": {
                            "id": 20,
                            "name": "Unit 20",
                            "type": "F-16C_50",
                            "coalition": "COALITION_BLUE",
                            "group": {"id": 120, "name": "Group 20"},
                        }
                    },
                    "weapon": {"type": "R-27ER"},
                }
            }
            grpc_normalized = normalize_combat_event(
                "hit", 10.0, grpc_payload, source="grpc"
            )
            with store.writer() as writer:
                first = writer.append(
                    session_id="1",
                    mission_time=10.0,
                    event_type="hit",
                    payload=grpc_payload,
                    source="grpc",
                    normalized=grpc_normalized,
                )
                merged = writer.append(
                    session_id="1",
                    mission_time=10.1,
                    event_type="hit",
                    payload=native,
                    source="native_combat",
                    normalized=normalized(native),
                )
                duplicate = writer.append(
                    session_id="1",
                    mission_time=10.1,
                    event_type="hit",
                    payload=native,
                    source="native_combat",
                    normalized=normalized(native),
                )

            self.assertEqual(first.outcome, "inserted")
            self.assertEqual(merged.outcome, "merged")
            self.assertEqual(duplicate.outcome, "duplicate")
            self.assertEqual(store.count(), 1)
            event = store.query(source="native_combat", target_unit=20)[0]
            self.assertEqual(event["source"], "merged")
            self.assertEqual(event["sources"], ["grpc", "native_combat"])
            self.assertEqual(event["normalized"]["target"]["unit_name"], "Unit 20")
            self.assertEqual(store.query(unit="Unit 10", coalition="blue")[0]["id"], event["id"])
            self.assertEqual(store.query(group=120, event_types=["hit"])[0]["id"], event["id"])
            self.assertEqual(store.query(after_id=event["id"]), [])

    def test_different_fingerprint_is_not_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.sqlite")
            store.initialize()
            first = native_event(1, 1.0, "shot", weapon_type="R-27ER")
            second = native_event(2, 1.1, "shot", weapon_type="R-73")
            with store.writer() as writer:
                for raw in (first, second):
                    writer.append(
                        session_id="1",
                        mission_time=raw["mission_time"],
                        event_type="shot",
                        payload=raw,
                        source="native_combat",
                        normalized=normalized(raw),
                    )
            self.assertEqual(store.count(), 2)

    def test_legacy_schema_is_migrated_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "dcs_session_id TEXT, mission_time REAL, received_at TEXT NOT NULL, "
                "event_type TEXT NOT NULL, payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO events (dcs_session_id, mission_time, received_at, "
                "event_type, payload_json) VALUES ('1', 1, 'now', 'birth', '{}')"
            )
            connection.commit()
            connection.close()

            store = EventStore(path)
            store.initialize()
            event = store.query()[0]
            self.assertEqual(event["source"], "grpc")
            self.assertEqual(event["sources"], ["grpc"])
            self.assertIsNone(event["normalized"])

    def test_compact_view_labels_correlation_without_promoting_it(self) -> None:
        hit = native_event(1, 10.0, "hit")
        loss = native_event(2, 20.0, "unit_lost", initiator_id=20, target_id=None, weapon_type=None)
        events = []
        for event_id, raw in ((1, hit), (2, loss)):
            events.append(
                {
                    "id": event_id,
                    "session_id": "1",
                    "mission_time": raw["mission_time"],
                    "received_at": "now",
                    "event_type": raw["event_type"],
                    "source": "native_combat",
                    "sources": ["native_combat"],
                    "normalized": normalized(raw),
                }
            )
        view = _combat_view(list(reversed(events)))
        self.assertEqual(
            view[0]["attribution"]["status"], "correlated_hit_then_loss"
        )
        self.assertIn("correlated_initiator", view[0]["attribution"])

    def test_incomplete_kill_event_does_not_claim_attribution(self) -> None:
        raw = native_event(1, 10.0, "kill", target_id=None)
        event = {
            "id": 1,
            "session_id": "1",
            "mission_time": 10.0,
            "received_at": "now",
            "event_type": "kill",
            "source": "native_combat",
            "sources": ["native_combat"],
            "normalized": normalized(raw),
        }
        attribution = _combat_view([event])[0]["attribution"]
        self.assertEqual(attribution["status"], "unattributed_loss")
        self.assertEqual(attribution["reason"], "incomplete_kill_event_identity")

    def test_expanded_query_rejects_unbounded_or_ambiguous_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.sqlite")
            store.initialize()
            invalid_queries = (
                {"event_types": []},
                {"event_types": ["hit"] * 21},
                {"event_type": "hit", "event_types": ["hit"]},
                {"after_id": -1},
                {"unit": True},
                {"coalition": "orange"},
                {"source": "unknown"},
            )
            for query in invalid_queries:
                with self.subTest(query=query), self.assertRaises(HarnessError):
                    store.query(**query)


class NativeObserverTests(unittest.TestCase):
    @staticmethod
    def _copy_source(root: Path) -> None:
        source = root / "tools" / "src" / "lua"
        source.mkdir(parents=True)
        shutil.copyfile(
            REPOSITORY_ROOT / "tools" / "src" / "lua" / "native_combat_observer.lua",
            source / "native_combat_observer.lua",
        )

    def test_poll_installs_once_persists_and_tracks_queue_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_source(root)
            responses = iter(
                [
                    {
                        "available": True,
                        "version": 1,
                        "capacity": 512,
                        "oldest_sequence": 2,
                        "latest_sequence": 2,
                        "overwritten": 1,
                        "extraction_errors": 0,
                        "gap": True,
                        "events": [native_event(2, 10.0, "shot")],
                    },
                    {
                        "available": True,
                        "version": 1,
                        "capacity": 512,
                        "oldest_sequence": 2,
                        "latest_sequence": 2,
                        "overwritten": 1,
                        "extraction_errors": 0,
                        "gap": False,
                        "events": {},
                    },
                ]
            )
            codes: list[str] = []

            def evaluate(code: str) -> dict:
                codes.append(code)
                return next(responses)

            observer = NativeCombatObserver(
                ready_context(root),
                EventStoreCatalog(root / "runtime" / "events", root),
                LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl"),
                session_reader=lambda: "7",
                evaluator=evaluate,
            )
            observer.poll_once()
            observer.poll_once()

            status = observer.status()
            self.assertEqual(status["collector"], "running")
            self.assertEqual(status["cursor"], 2)
            self.assertEqual(status["queue_gaps"], 1)
            self.assertEqual(status["inserted_events"], 1)
            self.assertIn("world.addEventHandler", codes[0])
            self.assertEqual(codes[1], "return DCS_HARNESS_COMBAT_POLL(2, 200)")

    def test_session_change_discards_cross_session_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_source(root)
            sessions = iter(("1", "2"))
            observer = NativeCombatObserver(
                ready_context(root),
                EventStoreCatalog(root / "runtime" / "events", root),
                LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl"),
                session_reader=lambda: next(sessions),
                evaluator=lambda code: {
                    "available": True,
                    "version": 1,
                    "capacity": 512,
                    "oldest_sequence": 1,
                    "latest_sequence": 1,
                    "overwritten": 0,
                    "extraction_errors": 0,
                    "gap": False,
                    "events": [native_event(1, 1.0, "shot")],
                },
            )
            with self.assertRaises(HarnessError):
                observer.poll_once()
            self.assertEqual(observer.status()["session_id"], "2")
            self.assertEqual(observer.status()["cursor"], 0)
            self.assertEqual(EventStoreCatalog(root / "runtime" / "events", root).select("1").count(), 0)

    def test_event_collector_reads_native_only_current_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_source(root)
            context = ready_context(root)
            stores = EventStoreCatalog(root / "runtime" / "events", root)
            logger = LifecycleLogger(root / "runtime" / "logs" / "runtime.jsonl")
            observer = NativeCombatObserver(
                context,
                stores,
                logger,
                session_reader=lambda: "9",
                evaluator=lambda code: {
                    "available": True,
                    "version": 1,
                    "capacity": 512,
                    "oldest_sequence": 1,
                    "latest_sequence": 1,
                    "overwritten": 0,
                    "extraction_errors": 0,
                    "gap": False,
                    "events": [native_event(1, 5.0, "shot")],
                },
            )
            observer.poll_once()
            collector = EventCollector(context, stores, logger)
            collector.native_combat = observer

            self.assertEqual(collector.status()["session_id"], "9")
            self.assertEqual(collector.status()["stored_events"], 1)
            self.assertEqual(collector.current_store().query()[0]["source"], "native_combat")

    def test_lua_source_is_bounded_and_only_registers_combat_events(self) -> None:
        source = (
            REPOSITORY_ROOT / "tools" / "src" / "lua" / "native_combat_observer.lua"
        ).read_text(encoding="utf-8")
        self.assertIn("local CAPACITY = 512", source)
        self.assertIn("pcall", source)
        self.assertIn("world.addEventHandler", source)
        self.assertNotIn("S_EVENT_BIRTH", source)
        self.assertNotIn("trigger.action", source)

    def test_malformed_or_noncontiguous_batch_is_rejected(self) -> None:
        base = {
            "available": True,
            "version": 1,
            "capacity": 512,
            "oldest_sequence": 1,
            "latest_sequence": 2,
            "overwritten": 0,
            "extraction_errors": 0,
            "gap": False,
            "events": [native_event(2, 1.0, "shot")],
        }
        with self.assertRaises(HarnessError):
            NativeCombatObserver._validate_batch(base, 0)


if __name__ == "__main__":
    unittest.main()
