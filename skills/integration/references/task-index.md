# Integration task index

Use this page to find the smallest relevant reference. It is a router, not an
API catalog. Read only the destination needed for the current task.

| Need | Start here | Then, only if needed |
| --- | --- | --- |
| Current Harness or DCS readiness | [Operation skill](../../operation/SKILL.md) and the target plugin's current description/status | [Logs](../../operation/references/logs.md) for a preserved failure |
| Coordinates, distance, bearing, or unit conventions | [Coordinates and units](coordinates-and-units.md) | `geo` capability; [world entities](world-entities.md) |
| Current unit state or a sustained trajectory | [Telemetry](../../operation/references/telemetry.md) | [Observation and verification](observation-and-verification.md) |
| Discrete chronology or missing combat events | [Events](../../operation/references/events.md) | [Third-party known issues](third-party-known-issues.md); [DCS-gRPC](dcs-grpc.md) |
| Typed live query or action | [DCS-gRPC](dcs-grpc.md) | Generated descriptors and pinned DCS-gRPC source |
| Focused mission-runtime observation or action | [DCS Lua](dcs-lua.md) | [MIST](mist.md) or [MOOSE](moose.md) if the mission loaded it |
| Offline `.miz` authoring or final validation | [`.miz` and mission authoring](miz-and-mission-authoring.md) | [pydcs](pydcs.md); known issues before source archaeology |
| Structural Mission Contract validation | [Mission authoring validation](mission-authoring-validation.md) | [Aircraft payload authoring](payload-authoring.md); [mission planning and route semantics](mission-planning-and-routes.md) |
| Aircraft payload or pylon compatibility | [Aircraft payload authoring](payload-authoring.md) and current `catalog` capability | [pydcs](pydcs.md); pinned definitions only for source diagnosis |
| Scenario geography, bullseye, or meaningful offline route design | [Mission planning and route semantics](mission-planning-and-routes.md) | [Coordinates and units](coordinates-and-units.md); [`.miz` and mission authoring](miz-and-mission-authoring.md) |
| Persistent CAP, AWACS, tanker, orbit, escort, or RTB behavior | [Persistent air tasks](persistent-air-tasks.md) | [Dynamic aircraft and tasking](dynamic-aircraft-and-tasking.md); [Observation and verification](observation-and-verification.md) |
| Country, coalition, group, unit, or physical identity | [World entities](world-entities.md) | Current live observation |
| Optional community scripting discovery | [Cold source registry](cold-sources.md) | Focused local Hoggit cache, then stronger pinned/live evidence |
| Unexpected third-party behavior | Relevant hot reference | [Third-party known issues](third-party-known-issues.md), then current diagnostics and one bounded probe |

For exact signatures or fields, inspect current generated contracts or pinned
source after the focused reference. Do not use this index as authority for live
state or version-sensitive behavior.
