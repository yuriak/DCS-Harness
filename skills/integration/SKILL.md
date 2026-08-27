---
name: integration
description: Guide DCS Harness integration work across DCS-gRPC, mission-side DCS Lua, MIST, MOOSE, pydcs, and dcs-lua-definitions. Use when choosing an integration layer, tracing an external API to simulator behavior, writing or reviewing Eval snippets, or determining which pinned upstream source is authoritative. Do not use for routine start, stop, health, recovery, or operator procedures; use the operation skill for those.
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

Offline mission authoring
  -> pydcs -> .miz files

Static Lua authoring help
  -> dcs-lua-definitions
~~~

DCS-Harness is not the DCS-gRPC server and does not own its lifecycle. Harness exposes Agent-facing capabilities and connects to the DCS-side DCS-gRPC server as an external client.

Do not treat these layers as interchangeable. In particular, pydcs and dcs-lua-definitions do not provide live simulator access, while MIST and MOOSE exist only inside a mission that loads them.

## Choose the integration layer

1. Prefer a typed Harness or DCS-gRPC operation when it already expresses the required live action or query.
2. Use CustomService.Eval for a narrowly scoped live capability that is only available in mission-side Lua.
3. Use native DCS Lua for simple simulator facts and actions.
4. Use MIST for mission utilities, databases, scheduling, routing, and table-oriented helpers when the mission loads MIST.
5. Use MOOSE for complex, persistent, object-oriented mission orchestration when the mission loads MOOSE.
6. Use pydcs to create or modify mission files before DCS loads them.
7. Use dcs-lua-definitions for editor assistance and API discovery, then verify behavior against the running DCS version and pinned runtime sources.

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
4. Inspect the exact pinned source paths named by that reference before asserting an API signature.
5. For Eval, keep Lua snippets small and return JSON-safe facts rather than framework objects.
6. Confirm optional mission libraries are actually loaded; repository submodules do not load them into DCS.
7. Test the narrowest possible behavior, preserving the human-authorization boundaries described by the operation skill.

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
- [dcs-lua-definitions](references/dcs-lua-definitions.md): static Lua language-server definitions.

For setup, process lifecycle, health checks, recovery, logs, and operator safety, use [the operation skill](../operation/SKILL.md).

## Guardrails

- Do not copy large upstream API catalogs into this repository; point to pinned source paths.
- Do not claim optional mission libraries are available merely because their submodules or Lua files exist locally.
- Do not expose arbitrary Eval as a default agent-facing primitive when a bounded typed operation is appropriate.
- Do not return userdata, framework instances, cyclic tables, or other non-serializable values across the Lua-to-gRPC boundary.
- Do not use offline mission tooling to imply live-world state.
