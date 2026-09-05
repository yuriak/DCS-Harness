from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SOURCE_ROOT))

from dcs_harness_runtime.f10_runtime import F10Runtime, F10Scope  # noqa: E402
from dcs_harness_runtime.logging_utils import LifecycleLogger  # noqa: E402
from dcs_harness_runtime.resident import AUTOSTART_BUILTINS  # noqa: E402
from dcs_harness_runtime.result import ErrorCode, HarnessError  # noqa: E402
from plugins import f10  # noqa: E402


class FakeRpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, float]] = []

    def __call__(self, service: str, method: str, request: dict, timeout: float) -> dict:
        self.calls.append((service, method, dict(request), timeout))
        if method == "GetTime":
            return {"time": 12.5}
        if method.startswith("Add"):
            return {"path": [*request.get("path", []), request["name"]]}
        return {}


def runtime_state(
    session: list[str] | None = None,
    events: list[dict] | None = None,
) -> tuple[F10Runtime, FakeRpc]:
    current = session if session is not None else ["101"]
    rpc = FakeRpc()
    state = F10Runtime(
        SimpleNamespace(),
        mock.Mock(),
        session_reader=lambda: current[0],
        rpc_caller=rpc,
        event_reader=lambda after_id, limit: [
            value for value in (events or []) if value["id"] > after_id
        ][:limit],
    )
    state.poll_once()
    return state, rpc


class F10RuntimeTests(unittest.TestCase):
    def test_lagging_old_ledger_cannot_advance_new_session_cursor(self) -> None:
        session = ["101"]
        rows = [{"id": 40, "session_id": "101"}]
        state, _ = runtime_state(session, rows)
        session[0] = "202"
        state.poll_once()
        state.poll_inputs_once()
        self.assertEqual(state.status()["input_cursor"], 0)
        state.init(F10Scope("mission"), "New")
        state.add_command(item_id="new", parent_id="root:mission", name="New",
                          interaction_id="new", choice_id="yes", action=None, data={})
        rows[:] = [{"id": 1, "session_id": "202", "mission_time": 1,
                   "received_at": "9999-01-01T00:00:00+00:00",
                   "event_type": "mission_command", "payload": {"mission_command": {
                       "details": {"source": "dcs_harness", "command_id": "new",
                                   "interaction_id": "new", "choice_id": "yes"}}}}]
        self.assertEqual(state.poll_inputs_once(), 1)
        self.assertEqual(state.status()["input_cursor"], 1)

    def test_graceful_monitor_stop_removes_owned_root(self) -> None:
        state, rpc = runtime_state()
        state.init(F10Scope("mission"), "Owned")
        stopped = threading.Event()
        stopped.set()
        state.run(stopped)
        self.assertEqual(state.status()["registered_item_count"], 0)
        self.assertTrue(any(call[1] == "RemoveMissionCommandItem" for call in rpc.calls))

    def test_session_rotation_invalidates_registered_state(self) -> None:
        current = ["101"]
        state, _ = runtime_state(current)
        state.init(F10Scope("mission"), "DCS-Harness")
        state.add_menu(item_id="orders", parent_id="root:mission", name="Orders")
        with state._lock:
            state._pending_inputs.append({"interaction_id": "choice-1"})
            state._latest_input_mission_time = 12.0
            state._latest_outbound_message_mission_time = 13.0

        current[0] = "202"
        state.poll_once()
        status = state.status()

        self.assertEqual(status["session_id"], "202")
        self.assertFalse(status["initialized"])
        self.assertEqual(status["registered_item_count"], 0)
        self.assertEqual(status["pending_player_inputs"], 0)
        self.assertIsNone(status["latest_input_mission_time"])
        self.assertIsNone(status["latest_outbound_message_mission_time"])
        self.assertEqual(status["session_rotations"], 1)

    def test_malformed_session_is_structured_failure(self) -> None:
        state = F10Runtime(SimpleNamespace(), mock.Mock(), session_reader=lambda: True)

        with self.assertRaises(HarnessError) as raised:
            state.poll_once()

        self.assertEqual(raised.exception.code, ErrorCode.GRPC_CALL_FAILED)
        self.assertEqual(raised.exception.details["reason"], "MALFORMED_SESSION_ID")

    def test_monitor_records_disconnect_without_losing_last_session(self) -> None:
        calls = iter(("101", HarnessError(ErrorCode.GRPC_CALL_FAILED, "offline")))

        def read_session() -> str:
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        state = F10Runtime(SimpleNamespace(), mock.Mock(), session_reader=read_session)
        state.poll_once()
        with self.assertRaises(HarnessError) as raised:
            state.poll_once()
        state._record_error(raised.exception)

        status = state.status()
        self.assertEqual(status["session_monitor"], "disconnected")
        self.assertEqual(status["session_id"], "101")
        self.assertEqual(status["last_error"]["code"], "GRPC_CALL_FAILED")

    def test_send_selects_typed_scope_methods_and_records_mission_time(self) -> None:
        state, rpc = runtime_state()

        mission = state.send(
            scope="mission", text="One", display_time=10, clear_view=False
        )
        coalition = state.send(
            scope="coalition",
            coalition="COALITION_BLUE",
            text="Two",
            display_time=15,
            clear_view=True,
        )
        group = state.send(
            scope="group",
            group_id=17,
            text="Three",
            display_time=20,
            clear_view=False,
        )

        trigger_calls = [call for call in rpc.calls if call[0].endswith("TriggerService")]
        self.assertEqual(
            [call[1] for call in trigger_calls],
            ["OutText", "OutTextForCoalition", "OutTextForGroup"],
        )
        self.assertEqual(trigger_calls[1][2]["coalition"], "COALITION_BLUE")
        self.assertEqual(trigger_calls[2][2]["group_id"], 17)
        self.assertEqual(mission["mission_time"], 12.5)
        self.assertEqual(coalition["mission_time"], 12.5)
        self.assertEqual(group["mission_time"], 12.5)
        self.assertEqual(
            state.status()["latest_outbound_message_mission_time"], 12.5
        )

    def test_mission_menu_crud_uses_returned_paths_and_structured_details(self) -> None:
        state, rpc = runtime_state()

        root = state.init(F10Scope("mission"), "DCS-Harness")
        repeated = state.init(F10Scope("mission"), "DCS-Harness")
        menu = state.add_menu(
            item_id="tasking", parent_id="root:mission", name="Tasking"
        )
        command = state.add_command(
            item_id="tasking.accept",
            parent_id="tasking",
            name="Accept",
            interaction_id="task-001",
            choice_id="accept",
            action="acknowledge_task",
            data={"task": "CAP"},
        )
        removed = state.remove("tasking")

        self.assertTrue(root["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(menu["registration"]["path"], ["DCS-Harness", "Tasking"])
        self.assertEqual(
            command["registration"]["path"],
            ["DCS-Harness", "Tasking", "Accept"],
        )
        add_command = next(call for call in rpc.calls if call[1] == "AddMissionCommand")
        self.assertEqual(add_command[2]["details"]["source"], "dcs_harness")
        self.assertEqual(add_command[2]["details"]["interaction_id"], "task-001")
        self.assertEqual(add_command[2]["details"]["choice_id"], "accept")
        self.assertEqual(add_command[2]["details"]["data"], {"task": "CAP"})
        self.assertEqual(removed["removed"], ["tasking", "tasking.accept"])
        self.assertEqual(state.status()["registered_item_count"], 1)

    def test_coalition_and_group_roots_use_exact_typed_contracts(self) -> None:
        state, rpc = runtime_state()

        state.init(F10Scope("coalition", coalition="COALITION_RED"), "Red Root")
        state.init(F10Scope("group", group_name="Springfield 1"), "Group Root")
        cleared = state.clear()

        methods = [call[1] for call in rpc.calls]
        self.assertIn("AddCoalitionCommandSubMenu", methods)
        self.assertIn("AddGroupCommandSubMenu", methods)
        self.assertIn("RemoveCoalitionCommandItem", methods)
        self.assertIn("RemoveGroupCommandItem", methods)
        coalition_add = next(call for call in rpc.calls if call[1] == "AddCoalitionCommandSubMenu")
        group_add = next(call for call in rpc.calls if call[1] == "AddGroupCommandSubMenu")
        self.assertEqual(coalition_add[2]["coalition"], "COALITION_RED")
        self.assertEqual(group_add[2]["group_name"], "Springfield 1")
        self.assertEqual(cleared["removed_count"], 2)
        self.assertFalse(state.status()["initialized"])

    def test_session_change_during_action_discards_local_registration(self) -> None:
        sessions = iter(("101", "202"))
        rpc = FakeRpc()
        state = F10Runtime(
            SimpleNamespace(),
            mock.Mock(),
            session_reader=lambda: next(sessions),
            rpc_caller=rpc,
        )

        with self.assertRaises(HarnessError) as raised:
            state.init(F10Scope("mission"), "DCS-Harness")

        self.assertEqual(
            raised.exception.details["reason"], "SESSION_CHANGED_DURING_F10_OPERATION"
        )
        self.assertEqual(state.status()["session_id"], "202")
        self.assertEqual(state.status()["registered_item_count"], 0)

    def test_command_events_become_acknowledgeable_player_inputs(self) -> None:
        events: list[dict] = []
        state, _ = runtime_state(events=events)
        state.init(F10Scope("mission"), "DCS-Harness")
        state.add_command(
            item_id="task.accept",
            parent_id="root:mission",
            name="Accept",
            interaction_id="task-001",
            choice_id="accept",
            action="acknowledge_task",
            data={"task": "CAP"},
        )
        events.append(
            {
                "id": 7,
                "session_id": "101",
                "mission_time": 44.5,
                "received_at": "9999-01-01T00:00:00+00:00",
                "event_type": "mission_command",
                "payload": {
                    "time": 44.5,
                    "mission_command": {
                        "details": {
                            "source": "dcs_harness",
                            "command_id": "task.accept",
                            "interaction_id": "task-001",
                            "choice_id": "accept",
                            "action": "acknowledge_task",
                            "data": {"task": "CAP"},
                        }
                    },
                },
            }
        )

        self.assertEqual(state.poll_inputs_once(), 1)
        recent = state.recent_inputs(
            limit=10, pending_only=True, interaction_id="task-001"
        )
        acknowledged = state.acknowledge(["101:7"])
        pending = state.recent_inputs(
            limit=10, pending_only=True, interaction_id=None
        )

        self.assertEqual(recent["inputs"][0]["input_id"], "101:7")
        self.assertEqual(recent["inputs"][0]["action"], "acknowledge_task")
        self.assertEqual(recent["inputs"][0]["data"], {"task": "CAP"})
        self.assertEqual(acknowledged["acknowledged"], ["101:7"])
        self.assertEqual(pending["count"], 0)
        self.assertEqual(state.status()["pending_player_inputs"], 0)
        self.assertEqual(state.status()["latest_input_mission_time"], 44.5)

    def test_input_poll_ignores_foreign_and_scope_mismatched_events(self) -> None:
        events: list[dict] = []
        state, _ = runtime_state(events=events)
        state.init(F10Scope("coalition", coalition="COALITION_BLUE"), "Blue")
        state.add_command(
            item_id="blue.choice",
            parent_id="root:coalition:COALITION_BLUE",
            name="Choice",
            interaction_id="blue-task",
            choice_id="yes",
            action=None,
            data=None,
        )
        base = {
            "session_id": "101",
            "mission_time": 5.0,
            "received_at": "9999-01-01T00:00:00+00:00",
            "event_type": "coalition_command",
        }
        details = {
            "source": "dcs_harness",
            "command_id": "blue.choice",
            "interaction_id": "blue-task",
            "choice_id": "yes",
        }
        events.extend(
            (
                {
                    **base,
                    "id": 1,
                    "payload": {
                        "coalition_command": {
                            "coalition": "COALITION_RED",
                            "details": details,
                        }
                    },
                },
                {
                    **base,
                    "id": 2,
                    "payload": {
                        "coalition_command": {
                            "coalition": "COALITION_BLUE",
                            "details": {**details, "source": "another_client"},
                        }
                    },
                },
            )
        )

        self.assertEqual(state.poll_inputs_once(), 0)
        self.assertEqual(state.status()["ignored_inputs"], 2)
        self.assertEqual(state.status()["input_cursor"], 2)


class F10PluginTests(unittest.TestCase):
    def test_f10_is_an_explicit_autostart_builtin(self) -> None:
        self.assertIn("f10", AUTOSTART_BUILTINS)
        self.assertTrue(f10.PLUGIN_AUTOSTART)

    def test_plugin_dispatches_crud_and_returns_bounded_status(self) -> None:
        state, rpc = runtime_state()
        handle = SimpleNamespace(
            state=state,
            task_status=lambda: {"session-monitor": {"state": "running"}},
        )
        context = SimpleNamespace(runtime=SimpleNamespace(plugin_handle=lambda name: handle))

        initialized = f10.invoke(context, "init", {"scope": "mission"})
        added = f10.invoke(
            context,
            "add-command",
            {
                "item_id": "ping",
                "parent_id": "root:mission",
                "name": "Request attention",
                "interaction_id": "player-ping",
                "choice_id": "ping",
                "action": "request_attention",
            },
        )
        state.poll_inputs_once()
        status = f10.invoke(context, "status", {})
        report = f10.fast_report(context, handle)

        self.assertTrue(initialized["created"])
        self.assertTrue(initialized["player_ping"]["created"])
        self.assertEqual(added["registration"]["interaction_id"], "player-ping")
        self.assertEqual(status["registered_item_count"], 3)
        self.assertEqual(status["registered_menu_count"], 1)
        self.assertEqual(status["registered_command_count"], 2)
        self.assertEqual(len(status["registrations"]), 3)
        self.assertEqual(report["health"], "ready")
        self.assertNotIn("registrations", report)
        ping_call = next(
            call
            for call in rpc.calls
            if call[1] == "AddMissionCommand"
            and call[2]["details"]["choice_id"] == "request-attention"
        )
        self.assertEqual(
            ping_call[2]["details"]["action"], "request_director_attention"
        )

    def test_plugin_validates_scope_targets_and_payload_bounds_before_rpc(self) -> None:
        state, rpc = runtime_state()
        handle = SimpleNamespace(state=state, task_status=lambda: {})
        context = SimpleNamespace(runtime=SimpleNamespace(plugin_handle=lambda name: handle))

        invalid_calls = (
            ("send", {"scope": "mission", "coalition": "blue", "text": "x"}),
            ("send", {"scope": "group", "text": "x", "group_id": 0}),
            ("init", {"scope": "coalition", "coalition": "all"}),
            ("add-menu", {"item_id": "root:fake", "parent_id": "root:mission", "name": "x"}),
            (
                "add-command",
                {
                    "item_id": "x",
                    "parent_id": "root:mission",
                    "name": "x",
                    "interaction_id": "x",
                    "choice_id": "x",
                    "callback": "lua",
                },
            ),
        )
        for command, args in invalid_calls:
            with self.subTest(command=command, args=args):
                with self.assertRaises(HarnessError) as raised:
                    f10.invoke(context, command, args)
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)

        self.assertEqual(rpc.calls, [])

    def test_plugin_recent_and_ack_use_current_session_input_ids(self) -> None:
        events: list[dict] = []
        state, _ = runtime_state(events=events)
        handle = SimpleNamespace(state=state, task_status=lambda: {})
        context = SimpleNamespace(runtime=SimpleNamespace(plugin_handle=lambda name: handle))
        initialized = f10.invoke(context, "init", {"scope": "mission"})
        ping = initialized["player_ping"]["registration"]
        events.append(
            {
                "id": 9,
                "session_id": "101",
                "mission_time": 60.0,
                "received_at": "9999-01-01T00:00:00+00:00",
                "event_type": "mission_command",
                "payload": {
                    "mission_command": {
                        "details": {
                            "source": "dcs_harness",
                            "command_id": ping["item_id"],
                            "interaction_id": "player-ping",
                            "choice_id": "request-attention",
                            "action": "request_director_attention",
                        }
                    }
                },
            }
        )
        state.poll_inputs_once()

        recent = f10.invoke(
            context,
            "recent",
            {"pending_only": True, "interaction_id": "player-ping"},
        )
        acknowledged = f10.invoke(
            context,
            "ack",
            {"input_ids": [recent["inputs"][0]["input_id"]]},
        )
        repeated = f10.invoke(
            context,
            "ack",
            {"input_ids": [recent["inputs"][0]["input_id"]]},
        )

        self.assertEqual(recent["count"], 1)
        self.assertEqual(recent["inputs"][0]["choice_id"], "request-attention")
        self.assertEqual(acknowledged["acknowledged_count"], 1)
        self.assertEqual(repeated["already_acknowledged"], ["101:9"])
        self.assertEqual(repeated["not_found"], [])
        with self.assertRaises(HarnessError) as stale:
            f10.invoke(context, "ack", {"input_ids": ["202:9"]})
        self.assertEqual(stale.exception.code, ErrorCode.INVALID_ARGUMENT)

    def test_group_root_id_with_spaces_can_be_used_as_parent(self) -> None:
        state, rpc = runtime_state()
        handle = SimpleNamespace(state=state, task_status=lambda: {})
        context = SimpleNamespace(runtime=SimpleNamespace(plugin_handle=lambda name: handle))

        root = f10.invoke(
            context,
            "init",
            {"scope": "group", "group_name": "Springfield 1"},
        )
        child = f10.invoke(
            context,
            "add-menu",
            {
                "item_id": "group.orders",
                "parent_id": root["registration"]["item_id"],
                "name": "Orders",
            },
        )

        self.assertEqual(child["registration"]["group_name"], "Springfield 1")
        self.assertEqual(
            [call[1] for call in rpc.calls if call[1].startswith("AddGroup")],
            [
                "AddGroupCommandSubMenu",
                "AddGroupCommand",
                "AddGroupCommandSubMenu",
            ],
        )

    def test_command_data_rejects_non_finite_and_excessive_nesting(self) -> None:
        state, rpc = runtime_state()
        handle = SimpleNamespace(state=state, task_status=lambda: {})
        context = SimpleNamespace(runtime=SimpleNamespace(plugin_handle=lambda name: handle))
        base = {
            "item_id": "choice",
            "parent_id": "root:mission",
            "name": "Choice",
            "interaction_id": "interaction",
            "choice_id": "choice",
        }

        values = (
            {"value": float("nan")},
            {"a": {"b": {"c": {"d": {"e": 1}}}}},
        )
        for data in values:
            with self.subTest(data=data):
                with self.assertRaises(HarnessError) as raised:
                    f10.invoke(context, "add-command", {**base, "data": data})
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)

        self.assertEqual(rpc.calls, [])

    def test_unknown_command_and_status_arguments_are_rejected(self) -> None:
        context = SimpleNamespace(runtime=None)

        with self.assertRaises(HarnessError) as unknown:
            f10.invoke(context, "eval", {})
        self.assertEqual(unknown.exception.code, ErrorCode.COMMAND_NOT_FOUND)

        state, _ = runtime_state()
        handle = SimpleNamespace(state=state, task_status=lambda: {})
        context = SimpleNamespace(runtime=SimpleNamespace(plugin_handle=lambda name: handle))
        with self.assertRaises(HarnessError) as arguments:
            f10.invoke(context, "status", {"unexpected": True})
        self.assertEqual(arguments.exception.code, ErrorCode.INVALID_ARGUMENT)

    def test_monitor_stops_cleanly(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        state = F10Runtime(SimpleNamespace(), mock.Mock(), session_reader=lambda: "42")

        state.run(stop_event)

        self.assertEqual(state.status()["session_monitor"], "stopped")


if __name__ == "__main__":
    unittest.main()
