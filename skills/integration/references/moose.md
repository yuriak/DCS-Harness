# MOOSE

## Role

MOOSE is a high-level, object-oriented mission-side Lua framework. Use it when a mission needs persistent orchestration, coordinated tasking, lifecycle management, or reusable domain abstractions beyond a few native DCS or MIST calls.

MOOSE runs inside the mission. DCS Harness reaches it through mission-side Lua, normally via a bounded operation backed by DCS-gRPC Eval.

## Availability

The active mission must load a compatible MOOSE Lua build before its globals and classes exist. A MOOSE submodule or runtime Lua file on the Harness filesystem does not load MOOSE into DCS.

Check the required global or class at the point of use. Mission reloads can change which initialization scripts ran, so do not cache availability across sessions without revalidation.

## Architectural concepts

The pinned tree contains framework abstractions including:

- SPAWN for template-based spawning;
- GROUP wrappers for DCS groups;
- SET_GROUP for filtered group collections;
- ZONE abstractions for spatial behavior;
- FLIGHTGROUP for higher-level flight management;
- AUFTRAG for mission and task modeling;
- AIRWING for coordinated air assets.

These names describe where to investigate, not a stable method catalog. Confirm constructors, method names, event behavior, and return values in the pinned source before implementation.

## Boundary rules

- Keep MOOSE instances, callbacks, schedulers, and internal state inside Lua.
- Return small JSON-safe facts through Eval rather than framework objects.
- Give Harness operations bounded inputs and explicit outcomes.
- Treat framework-managed state as mission-session state; reloads invalidate assumptions.
- Do not introduce MOOSE for a simple operation already represented by a typed RPC or a short native Lua call.

## Source lookup

The pinned implementation is authoritative. Useful entry points include:

- third_party/moose/Moose Development/Moose/Core/Spawn.lua
- third_party/moose/Moose Development/Moose/Wrapper/Group.lua
- third_party/moose/Moose Development/Moose/Core/Set.lua
- third_party/moose/Moose Development/Moose/Core/Zone.lua
- third_party/moose/Moose Development/Moose/Ops/FlightGroup.lua
- third_party/moose/Moose Development/Moose/Ops/Auftrag.lua
- third_party/moose/Moose Development/Moose/Ops/AirWing.lua
- third_party/moose/README.md

Search for the class declaration, then read inheritance, initialization, event registration, and cleanup paths. MOOSE examples from another release can differ significantly from this revision.

## Use another layer when

- A typed DCS-gRPC or Harness operation already exists.
- A small native DCS Lua snippet is sufficient.
- Table utilities and mission databases are the main need: use [MIST](mist.md).
- The mission file must be created or changed before launch: use [pydcs](pydcs.md).
