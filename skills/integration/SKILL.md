---
name: integration
description: Guide DCS Harness integration work across Geo, Telemetry, DCS-gRPC, mission-side DCS Lua, MIST, MOOSE, pydcs, offline .miz mission authoring and validation, and dcs-lua-definitions. Use when choosing an integration layer, tracing an external API to simulator behavior, preparing or inspecting a mission before launch, writing or reviewing Eval snippets, or determining which pinned upstream source is authoritative. Do not use for routine start, stop, health, recovery, or operator procedures; use the operation skill for those.
---

# DCS Harness integration

Use this skill to choose the smallest correct integration surface and verify its behavior against the versions pinned in this repository. Read only the reference files needed for the current question.

## Mental model

The integration layers have different jobs:

~~~text
External agent
  -> DCS-Harness capability interface (CLI or loopback HTTP)
       -> grpc capability
            -> DCS-gRPC client -> DCS-side DCS-gRPC server
                 -> typed RPCs -> DCS mission runtime
       -> lua capability
            -> DCS-gRPC client -> DCS-side DCS-gRPC server
                 -> CustomService.Eval -> mission-side Lua
                      -> native DCS Lua
                      -> MIST, if the mission loaded it
                      -> MOOSE, if the mission loaded it

Offline mission authoring and validation
  -> pydcs -> candidate .miz
  -> Human DCS Mission Editor review -> final .miz
  -> pydcs read-only final validation

Static Lua authoring help
  -> dcs-lua-definitions
~~~

DCS-Harness is not the DCS-gRPC server and does not own its lifecycle. Harness exposes Agent-facing capabilities and connects to the DCS-side DCS-gRPC server as an external client.

Do not treat these layers as interchangeable. In particular, pydcs and dcs-lua-definitions do not provide live simulator access, while MIST and MOOSE exist only inside a mission that loads them.

## Choose the integration layer

1. Use `geo` for maintained reference geography, coordinate calculations,
   canonical unit conversion, and bounded current-mission LL/local conversion.
2. Use `telemetry` for sampled current-session unit state and sustained
   trajectory questions; do not replace it with shell sleeps and point reads.
3. Prefer another typed Harness or DCS-gRPC operation when it already
   expresses the required live action or query.
4. Use CustomService.Eval for a narrowly scoped live capability that is only
   available in mission-side Lua.
5. Use native DCS Lua for simple simulator facts and actions.
6. Use MIST for mission utilities, databases, scheduling, routing, and
   table-oriented helpers when the mission loads MIST.
7. Use MOOSE for complex, persistent, object-oriented mission orchestration
   when the mission loads MOOSE.
8. Use pydcs for pre-mission `.miz` creation, structured edits, static mission
   inspection, and read-only validation of the Human-approved final mission
   before DCS loads it. Read the mission-authoring reference before deciding
   what belongs offline versus in live directing.
9. Use dcs-lua-definitions for editor assistance and API discovery, then
   verify behavior against the running DCS version and pinned runtime sources.

Typed RPC is usually the preferred interface when it covers the task because it provides structured schemas, bounded request and response types, descriptor discovery, and clearer errors. This is an interface-selection preference, not an absolute truth hierarchy: a current typed RPC result and a focused current mission-runtime Lua result are both live observations, and their relevance depends on what each path actually exposes and observes.

## Authority order

When documentation and behavior disagree, use this order:

1. Observed behavior in the supported DCS and Harness environment.
2. This repository's Harness implementation and protobuf contracts.
3. Source code at the pinned submodule revision.
4. Upstream documentation matching that revision.
5. General examples, community posts, and memory.

Record a version-sensitive conclusion rather than silently generalizing it.

## Investigation workflow

1. State whether the task is live runtime control, mission-side scripting, offline mission authoring, or static API research.
2. Identify the boundary crossed and the data shape on each side.
3. Read the relevant focused reference below.
   For offline mission authoring or final `.miz` validation, read the
   mission-authoring reference before inspecting the pinned pydcs source.
4. Check whether Geo or Telemetry already provides the maintained factual
   operation before designing a new adapter.
5. Inspect the exact pinned source paths named by that reference before asserting an API signature.
6. For Eval, keep Lua snippets small and return JSON-safe facts rather than framework objects.
7. Confirm optional mission libraries are actually loaded; repository submodules do not load them into DCS.
8. Test the narrowest possible behavior, preserving the human-authorization boundaries described by the operation skill.

When the focused local references cannot answer an interface question and Web
search is available, consult upstream official documentation or a credible
community reference before undertaking broad source archaeology or repeated
live trial-and-error. External documentation is a knowledge source, not
simulator truth; current supported behavior, Harness contracts, and pinned
source retain their higher authority.

## Diagnose the boundary first

Classify an integration failure before changing code:

- A transport or deadline failure belongs at the Harness-to-DCS-gRPC boundary.
- An unknown service, method, or field requires checking current generated descriptors and pinned proto.
- A Lua error means transport succeeded far enough to reach the mission scripting path.
- A missing mist or MOOSE global normally means the active mission did not load that optional library.
- A serialization failure means the Lua result was not reduced to a supported data shape.
- A pydcs result that differs from a running mission can reflect the offline/live boundary or mission reload state.
- A definition/runtime mismatch requires checking scripting context and the installed DCS version.

Do not switch libraries merely to conceal an error at a different boundary. Preserve the exact structured error, inspect the relevant current source or diagnostics, make one minimal correction, and verify again.

## From exploration to capability

Use a narrow Eval probe to learn uncertain mission behavior. If the same action repeats within one task, move it into authorized task-local runtime code with explicit inputs and JSON-safe outputs. Consider durable Harness code only after repeated dogfooding shows a generic need; keep scenario intent and framework objects outside the core contract.

## References

- [DCS-gRPC](references/dcs-grpc.md): typed live RPCs, event streams, sessions, and Eval.
- [DCS Lua](references/dcs-lua.md): mission-side Lua execution and the Eval boundary.
- [MIST](references/mist.md): utility functions and mission databases.
- [MOOSE](references/moose.md): object-oriented mission orchestration.
- [pydcs](references/pydcs.md): offline mission creation and editing.
- [`.miz` and mission authoring](references/miz-and-mission-authoring.md): hybrid mission design, Human review, final validation, Mission Contract, and live preflight boundaries.
- [dcs-lua-definitions](references/dcs-lua-definitions.md): static Lua language-server definitions.
- [Coordinates and units](references/coordinates-and-units.md): world/route axes, geographic conversion, headings, and canonical units.
- [World entities](references/world-entities.md): country/coalition, physical identity, airbases, and session boundaries.
- [Dynamic aircraft and tasking](references/dynamic-aircraft-and-tasking.md): spawn readiness, routes, controller tasks, orbit shape, and sustained verification.
- [Observation and verification](references/observation-and-verification.md): evidence semantics, time-series verification, and recovery discipline.
- [Cold source registry](references/cold-sources.md): pinned sources, upstream/community discovery, license status, and Hoggit cache policy.

For setup, process lifecycle, health checks, recovery, logs, and operator safety, use [the operation skill](../operation/SKILL.md).

## Guardrails

- Do not copy large upstream API catalogs into this repository; point to pinned source paths.
- Do not claim optional mission libraries are available merely because their submodules or Lua files exist locally.
- Do not expose arbitrary Eval as a default agent-facing primitive when a bounded typed operation is appropriate.
- Do not return userdata, framework instances, cyclic tables, or other non-serializable values across the Lua-to-gRPC boundary.
- Do not use offline mission tooling to imply live-world state.
