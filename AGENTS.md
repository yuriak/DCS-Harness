# DCS-Harness Agent Guide

This file is the permanent starting context for agents working in this repository. It is a project constitution, map, and skill router, not a DCS manual, API catalog, architecture history, or development diary.

## Project identity

~~~text
DCS-Harness
= Prepared Environment
+ Pinned DCS Libraries
+ Thin Generic Capabilities
+ Agent Knowledge
~~~

DCS-Harness is a capability substrate for agent-driven work with Digital Combat Simulator. It prepares and hosts reusable ways to observe, record, execute, and normalize. It is not a fixed DCS manager, battlefield director, campaign engine, or narrative system.

~~~text
Harness = Observe + Record + Execute + Normalize
Agent   = Interpret + Decide + Direct + Narrate
~~~

Keep that boundary explicit in designs, code, runtime artifacts, and live behavior.

## Core architecture principles

1. Keep scenario-specific strategy, pacing, doctrine, objectives, and narrative intelligence out of Harness core.
2. Prefer orchestrating and normalizing pinned DCS, DCS-gRPC, native Lua, MIST, MOOSE, and pydcs capabilities over reimplementing them.
3. Do not add a built-in plugin merely because one task needs a composite action.
4. Put one-off and experimental task logic under runtime/ rather than durable source under tools/.
5. Promote behavior into an existing reference or skill only after repeated use demonstrates a stable lesson.
6. Promote a capability into built-in code only after dogfooding demonstrates a repeated, generic infrastructure need.
7. Keep built-in operations bounded, discoverable, structured, and independent of a particular scenario.
8. Let current code, generated contracts, pinned sources, and observed runtime behavior override remembered or generic documentation.

## Standard work loop

Use a continuous feedback loop:

~~~text
observe
  -> reason
  -> act
  -> verify
  -> observe again
~~~

Observe before material action. Choose the narrowest appropriate capability, inspect its current interface instead of guessing, execute the smallest useful action, and verify its effect independently where practical.

When behavior is unexpected, capture the structured failure and inspect relevant current diagnostics before changing multiple variables or retrying. Avoid blind repeated actions when the first outcome is unknown.

Do not observe once, generate a long deterministic battle script, and stop reasoning for the rest of the mission.

## Truth and authority

For the current battle, prefer evidence in this order:

~~~text
live DCS state and observed behavior
  > current typed RPC and focused mission-runtime Lua observations
  > current-session telemetry samples
  > current-session event history
  > static and reference knowledge
  > agent inference
~~~

State uncertainty when sources do not establish a fact. Telemetry is sampled factual state, not tactical intent. Events are factual chronology, not a complete current-world snapshot. Logs are diagnostics, not battlefield truth.

Prefer a typed RPC when it already exposes the required capability because its interface is structured, bounded, and discoverable. That interface preference does not make it categorically more truthful than a focused current mission-runtime Lua observation. When two live observation paths disagree, re-check the relevant runtime state, source contract, and observation context instead of mechanically trusting the typed path.

For technical API and ecosystem authority, use the integration skill's stricter source order. Inspect current Harness source and tests for Harness behavior, generated protobuf descriptors and pinned DCS-gRPC source for RPC contracts, and pinned third_party source for upstream libraries.

An optional ignored Hoggit SSE community-documentation cache may exist at `research/hoggit-sse-cache/pages/`. Use it only for focused DCS scripting and mission-authoring discovery when maintained references do not answer the question. It remains below current Harness contracts, pinned source, and live evidence in authority; consult the integration skill's cold-source guidance before relying on it.

## Lifecycle model

The three continuity horizons are distinct:

~~~text
DCS process epoch                     -> logs
DCS-gRPC mission/session              -> events + telemetry
Agent-selected task/campaign context  -> file memory
~~~

- A new DCS-gRPC session is a new current battle context. Re-establish live objects, player state, mission-loaded libraries, and relevant assumptions.
- Event ledgers and telemetry contexts are isolated by session and are not automatically mixed into current queries.
- Logs normally describe diagnostics from the current DCS process epoch.
- A Harness restart can occur within either upstream epoch; do not equate Harness process lifetime with DCS or mission lifetime.
- runtime/memory/ is an Agent-owned persistent file workspace for selective task/mission context and possible future campaign continuity.
- Memory is below current live evidence and the current Mission Contract in authority. After a new session, treat old current-world facts as historical or stale until reverified.
- No built-in memory architecture exists. Do not create a database, service, retrieval API, automatic summarizer, retention policy, embedding system, or plugin without new repeated evidence.

## Runtime ownership

| Path | Owner and purpose |
| --- | --- |
| runtime/workspace/ | Agent scratch space and task-local artifacts. |
| runtime/plugins/py/ | Agent-created task-local Python extensions. |
| runtime/plugins/lua/ | Agent-created task-local mission Lua. |
| runtime/events/ | Harness-owned per-session factual ledgers. |
| runtime/telemetry/ | Harness-owned optional per-session factual archives. |
| runtime/logs/ | Harness-owned diagnostics, mirrors, and lifecycle logs. |
| runtime/memory/ | Agent-owned selective task/mission memory files; no built-in architecture. |

Do not directly edit Harness-owned event/telemetry databases or log mirrors during normal tasks. Query them through the appropriate capability. Preserve unrelated local runtime artifacts.

Durable reviewed capabilities live under tools/. Generated protobuf artifacts and local environment configuration are setup outputs, not hand-maintained domain knowledge.

## Capability growth path

Use this progression as evidence accumulates:

~~~text
one-off agent behavior
  -> runtime/workspace artifact
  -> repeated task-local behavior
  -> runtime extension or existing skill/reference lesson
  -> stable repeatable workflow
  -> possible future skill or durable capability
~~~

There are currently exactly three agent skills. Do not create separate skills for debugging, events, logs, gRPC, Lua, MIST, MOOSE, memory, campaign logic, or mission planning. Extend the existing owner when repeated evidence justifies it.

## Environmental safety boundary

Normal repository or live-mission work does not authorize changes to:

- the DCS installation;
- the player's Saved Games DCS-gRPC installation;
- MissionScripting.lua or user-owned .miz files;
- Windows firewall, registry, or system networking;
- shell profiles or global Python installations.

Require a concrete task and explicit human authorization before changing those human-controlled surfaces. Prefer repository-local, recoverable changes and inspect exact targets before any destructive action.

The DCS-gRPC server bind address and Harness client endpoint are separate. Do not guess or automatically rewrite player networking configuration; use the configured grpc.client_host and report connectivity failures clearly.

## Repository working rules

- Preserve user changes and unrelated untracked files.
- Do not commit, push, or alter remotes unless explicitly requested.
- Keep canonical CLI stdout machine-readable; send diagnostics to stderr or lifecycle logs.
- Use structured errors at the Harness boundary rather than exposing raw tracebacks.
- Keep runtime plugins within the documented minimal plugin API and runtime lifecycle.
- Discover current CLI and plugin contracts from code or describe operations rather than copying stale examples.
- Treat tests as contract evidence, but confirm live DCS behavior when simulation semantics matter.

## Skill router

Load only the skill needed for the current work:

- To operate, inspect, extend, or debug DCS-Harness—including Geo, Telemetry, live state, gRPC, Lua, events, logs, runtime workspace, plugins, backends, and recovery—read [skills/operation/SKILL.md](skills/operation/SKILL.md).
- To understand or select DCS ecosystem layers—including Geo, Telemetry, DCS-gRPC, native DCS Lua, MIST, MOOSE, pydcs, and dcs-lua-definitions—read [skills/integration/SKILL.md](skills/integration/SKILL.md).
- To act as a dynamic mission director, battlefield director, game master, or adaptive battle controller—read [skills/directing/SKILL.md](skills/directing/SKILL.md). Live directing normally also requires operation.

Ordinary Python refactoring, unit-test fixes, setup maintenance, or repository documentation work should not automatically load all three skills merely because this is DCS-Harness.

## Where to inspect next

For current Harness behavior, start with tools/src/py/dcs_harness.py, tools/src/py/plugins/, tools/src/py/dcs_harness_runtime/, and tests/.

For exact RPC or third-party behavior, inspect runtime/generated/ when present and the pinned repositories under third_party/. Do not substitute Agent prior knowledge for the versions checked into this repository.
