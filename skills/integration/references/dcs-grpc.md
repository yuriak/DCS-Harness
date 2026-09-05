# DCS-gRPC

## Role

DCS-gRPC runs on the DCS side and exposes live mission capabilities over gRPC. DCS-Harness connects to it as an external client: it reads endpoint configuration, imports generated protobuf bindings, creates and caches the client channel and stubs, invokes services, and normalizes results and errors through its Agent-facing capabilities.

DCS-Harness exposes those capabilities through its CLI and loopback resident HTTP server. It does not launch, embed, own, or supervise the DCS-side DCS-gRPC server or its lifecycle. Prefer a typed RPC when one already models the desired operation.

## Two live paths

### Typed RPCs

Typed services provide protobuf-defined requests and responses. They are the best fit for stable queries and commands because their data shape, errors, and compatibility surface are explicit.

MissionService includes live mission operations such as event streaming and session identity. The exact contract is authoritative in:

- third_party/dcs-grpc/protos/dcs/mission/v0/mission.proto
- third_party/dcs-grpc/protos/dcs/common/v0/common.proto

The pinned MissionService also exposes typed mission/coalition/group F10 menu
CRUD and emits corresponding structured command events. TriggerService exposes
scoped in-game text, and TimerService supplies mission time. The built-in F10
capability narrows these into Harness-owned current-session operations; use the
[F10 operation reference](../../operation/references/f10.md) rather than
manually inventing callbacks.

### CustomService.Eval

Eval executes Lua in DCS and is the escape hatch for capabilities not exposed by a typed RPC. Its contract is defined in:

- third_party/dcs-grpc/protos/dcs/custom/v0/custom.proto

Eval does not make arbitrary Lua a good public API. Harness operations should wrap recurring behaviors with bounded inputs, explicit outputs, timeouts, and useful errors.

## Events and sessions

Mission event streams are live and connection-oriented. Consumers must tolerate cancellation, transport loss, mission reload, and a changed session identity. A reconnect is not proof that the same mission session continued.

Use the mission service's StreamEvents and GetSessionId definitions and the corresponding pinned implementation when reasoning about exact behavior. Harness runtime code remains authoritative for its reconnect, readiness, and normalization policy.

## Boundary rules

- Protobuf messages cross the external-process boundary.
- Eval crosses into mission-side Lua and must return a representable result.
- The Harness resident HTTP server and the DCS-side DCS-gRPC server are separate processes with separate lifecycle and transport boundaries.
- A successful transport call does not by itself prove an optional mission library is loaded.
- DCS-gRPC availability does not eliminate DCS mission context, pause state, or version sensitivity.
- Keep agent-facing operations narrower than the full upstream RPC surface unless the product contract explicitly requires otherwise.

## Source lookup

Inspect these pinned locations before relying on signatures or behavior:

- third_party/dcs-grpc/protos/dcs
- third_party/dcs-grpc/lua
- third_party/dcs-grpc/src
- third_party/dcs-grpc/README.md

Search protobuf service and method names first, then trace the matching generated or runtime implementation. Do not assume examples from a newer upstream release match the submodule revision.

## Use another layer when

- The capability is a small mission-only Lua action: use Eval and [DCS Lua](dcs-lua.md).
- It depends on MIST or MOOSE: verify mission loading and read the corresponding reference.
- It changes a mission file before launch: use [pydcs](pydcs.md).
- The task is process lifecycle or recovery: use the operation skill.
