# DCS-gRPC

## Role

DCS-gRPC is the live bridge between an external process and DCS. DCS Harness embeds and supervises that bridge, then presents its own stable API to agents. Prefer a typed RPC when one already models the desired operation.

## Two live paths

### Typed RPCs

Typed services provide protobuf-defined requests and responses. They are the best fit for stable queries and commands because their data shape, errors, and compatibility surface are explicit.

MissionService includes live mission operations such as event streaming and session identity. The exact contract is authoritative in:

- third_party/dcs-grpc/protos/dcs/mission/v0/mission.proto
- third_party/dcs-grpc/protos/dcs/common/v0/common.proto

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
