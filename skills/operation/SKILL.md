---
name: operation
description: >
  Operate, inspect, extend, and debug DCS-Harness during live DCS work.
  Use when querying DCS through Harness capabilities, invoking gRPC or Lua,
  reading current telemetry, events, or logs, working with runtime plugins or
  workspace, or diagnosing failed Harness operations.
---

# Operate DCS-Harness

Use DCS-Harness as a thin capability substrate around the current DCS runtime.
Keep interpretation, planning, and mission-specific decisions in the Agent
reasoning loop.

## Operational model

- **grpc** provides descriptor-driven, typed access to unary DCS-gRPC methods.
- **lua** evaluates code in the current mission Lua environment.
- **geo** provides maintained reference geography, calculations, units, and a
  bounded live coordinate bridge.
- **catalog** provides bounded static aircraft, pylon, store, and loadout
  compatibility discovery from the pinned pydcs revision.
- **telemetry** samples bounded current-session factual unit state and history.
- **events** records source-tagged factual chronology from DCS-gRPC and a
  bounded redundant native combat observer for the current session.
- **f10** sends scoped in-game text and owns bounded current-session menus and
  structured player selections.
- **logs** exposes diagnostics from the current DCS process-log epoch.
- `runtime/memory/` holds selective Agent-owned task/mission context in ordinary
  files; it is not a Harness capability or factual ledger.
- Runtime workspace and plugins hold task-local or experimental code.

Do not treat events as a complete world-state database or logs as battlefield
truth. Use typed queries or focused Lua observations for current state.

## Standard workflow

1. Run the aggregated fast status report to establish runtime, session, and
   observation-path health.
2. Observe the current DCS/session state before acting.
3. Select the narrowest capability that fits the task.
4. Discover the current interface instead of guessing its schema.
5. Execute the smallest useful action.
6. Verify the effect through an independent observation when practical.
7. On failure, inspect the structured result, capability status, and current
   logs before retrying.
8. Keep task-local composition under runtime/.
9. Re-observe and continue reasoning.

Avoid long deterministic scripts that stop observing while a live mission
changes.

## Choose the operating mode

Keep one explicit mode in task context or `runtime/memory/MEMORY.md`:

- **PREPARATION**: source/knowledge inspection, helper construction, mission
  authoring, controlled experiments, and preflight are allowed. The goal is a
  verified substrate before the Human begins the actual flight/scenario.
- **LIVE**: the Human is flying or the scenario is formally in progress.
  Prioritize battle awareness, player interaction, the immediate directing
  decision, and effect verification ahead of technical research. Do not perform
  prolonged open-ended API/source archaeology.
- **DIAGNOSTIC**: a critical capability failed, the battle substrate is invalid,
  or the same bounded path failed repeatedly. Pause normal progression, preserve
  evidence, notify the pilot in DCS when appropriate, diagnose, and repeat
  preflight before returning to LIVE.

In LIVE, use fast status, the relevant hot/known-issue reference, and one
bounded technical probe for a technical blocker, then re-observe the battle.
Before a second deeper research pass, explicitly re-check player state, combat
changes, pending F10 input, and mission time. Aim to refresh important state
within roughly 30 seconds during active combat and 60 seconds during quiet
transit, adjusted to the scenario; these are directing guidelines, not Harness
real-time guarantees.

## Live preflight for an authored mission

A Human-approved Mission Contract or final `.miz` provides expected initial
facts, not live truth. After DCS loads the mission, establish the current
session, theatre, and player state; probe the exact required startup-library
symbols; observe expected background groups; and check telemetry, events, and
logs before treating the mission as ready. Verify any critical continuing
route or task over an appropriate observation window.

An authored reserve definition does not establish that the reserve is active
or committed. Confirm its current state through live observation and test a
required activation path narrowly before broad scenario progression.

The final `.miz` remains a Human-owned mission file. Routine live operation
does not authorize rewriting it. Use the
[integration mission-authoring reference](../integration/references/miz-and-mission-authoring.md)
for offline construction, Human review, final read-only validation, and the
task-local Mission Contract boundary.

## Select a capability

Prefer **grpc** when a current protobuf service already exposes the required
state or action and structured request/response data is useful. Follow
services -> describe -> call; never guess a request schema.

Use **lua** when typed coverage is absent, native DCS scripting is required,
mission-loaded MIST or MOOSE must be called, or a focused mission-runtime
diagnostic is needed. Return JSON-safe primitives and tables rather than
expecting Lua objects or functions to cross the boundary.

Use **geo** for named reference locations, geographic calculations, unit
conversion, and bounded live geographic/local conversion. Do not recreate a
theatre projection in task-local code; read the
[integration coordinate reference](../integration/references/coordinates-and-units.md)
when axes, headings, or route points matter.

Use **catalog** before authoring an aircraft payload. Search and resolve the
exact aircraft `type_id`, query compatible stores by aircraft and optional
pylon, and validate the proposed pylon/CLSID map. Catalog facts are static
pinned-source evidence, not proof that a module is installed or usable in the
player's current DCS environment.

Use **telemetry** for current-session unit snapshots and sustained trajectory
questions. Check its collector and background-task status before treating a
sample as current, and use bounded history rather than shell sleep plus two
point queries.

Use **events** to answer what factually happened in the current or last-known
mission session. Check connection status before using it for live decisions.

Use **f10** when information or choices are addressed to the pilot inside DCS.
Initialize only the required scope, keep dynamic menus short-lived, and
acknowledge observed selections after the directing loop handles them.

Use **logs** to diagnose Lua, MIST, MOOSE, DCS, or DCS-gRPC behavior. Normal
queries address only the current process-log epoch.

Use a runtime plugin only after a composite operation repeats within the
current task. A mission-specific sequence is not sufficient reason to modify
the durable Harness core.

## Discover before invoking

The CLI emits one canonical JSON document on stdout. Global options precede
the plugin and command:

~~~bash
runtime/venv/bin/python tools/src/py/dcs_harness.py \
  --backend auto status

runtime/venv/bin/python tools/src/py/dcs_harness.py \
  --backend auto plugins list

runtime/venv/bin/python tools/src/py/dcs_harness.py \
  --backend auto plugins describe grpc
~~~

Use --args-json for structured arguments. Read the returned ok, error, and
meta fields rather than inferring success from process output alone.

`status` is shorthand for `plugins status`. It asks the runtime to aggregate
bounded optional plugin reports. It may explicitly import discovered plugins,
but does not start resident plugins. A direct fallback therefore reports
resident collectors as `not_started`; it does not create parallel collectors.
Treat top-level `health` as Harness operability, never as a tactical judgment.
Read per-plugin freshness, lifecycle, and warnings before using last-known
facts as current evidence.

For exact current CLI syntax and dispatch behavior, inspect:

- tools/src/py/dcs_harness.py
- tools/src/py/plugins/plugins.py
- the target plugin description output

## Understand backends

- **direct** creates a transient runtime for one invocation. It supports
  stateless capabilities but rejects resident capabilities.
- **server** requires the loopback resident server and preserves resident
  plugin state and background tasks.
- **auto** uses a ready resident server when available and otherwise follows
  the current fallback behavior.

Start the resident runtime with:

~~~bash
runtime/venv/bin/python tools/src/py/dcs_harness.py serve
~~~

Keep health/readiness probing conceptually separate from capability invocation:
a quick readiness result does not define how long a valid capability call may
take.

## Debug methodically

Use this sequence:

~~~text
capture the exact failure
-> inspect the structured Harness result
-> inspect capability and runtime status
-> inspect the relevant current log
-> correlate with current DCS state
-> make one minimal correction
-> retry once
-> verify independently
~~~

Do not use blind infinite retries, change several variables at once, or assume
an action succeeded merely because the local client did not crash.

If events is disconnected or telemetry is degraded/failed, their current
session data can remain queryable. Last-known events or samples are not proof
that the mission or unit state is currently active.

## Work within ownership boundaries

- runtime/workspace/: Agent scratch files and task-local artifacts.
- runtime/plugins/py/: Agent-created Python capability plugins.
- runtime/plugins/lua/: Agent-created Lua files usable through the Lua
  capability.
- runtime/events/: Harness-owned factual ledgers; do not edit in normal work.
- runtime/telemetry/: Harness-owned optional per-session telemetry archives;
  do not edit in normal work.
- runtime/logs/: Harness-owned mirrors and lifecycle diagnostics; do not edit.
- runtime/memory/: Agent-owned selective task/mission memory files. Keep them
  lightweight and lower-authority than current live evidence; do not invent a
  built-in memory system.

Do not modify the DCS installation, Saved Games DCS-gRPC installation,
MissionScripting.lua, missions, firewall, registry, networking, or shell
profiles without specific Human authorization.

## Load focused references

- Read [runtime.md](references/runtime.md) for lifecycle, ownership, and
  backend semantics.
- Read [grpc.md](references/grpc.md) before discovering or calling typed RPCs.
- Read [lua.md](references/lua.md) before Eval or mission-side Lua work.
- Read [events.md](references/events.md) before interpreting event history.
- Read [f10.md](references/f10.md) before sending pilot communication or
  creating interactive F10 menus.
- Read [telemetry.md](references/telemetry.md) before using trajectories or
  diagnosing collector freshness, lag, session rotation, or persistence.
- Read [logs.md](references/logs.md) when diagnosing current DCS behavior.
- Read [runtime-plugins.md](references/runtime-plugins.md) before creating a
  task-local capability.
- Read [memory.md](references/memory.md) before creating or updating persistent
  task/mission context under `runtime/memory/`.

If a reference conflicts with observed behavior, inspect current source,
generated protobuf descriptors, plugin description output, and the live
runtime. Current behavior is authoritative.
