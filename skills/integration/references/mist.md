# MIST

## Role

Mission Scripting Tools, MIST, is a mission-side Lua utility library. The pinned source exposes table-oriented helpers, maintained mission databases, scheduling, routing, geometry, and dynamic group utilities.

MIST complements native DCS Lua. It is not an external service and does not replace DCS-gRPC.

## Availability

The mission must load the MIST Lua script before an Eval snippet can use the global mist table. Keeping the MIST submodule in this repository or placing mist.lua in a runtime directory does not load it into the active mission.

Probe for the required table or function and return a clear missing-library error. Do not infer availability from a previous mission because reloads can change mission initialization.

## Good fits

- Querying or transforming MIST's mission databases.
- Scheduling repeated or delayed Lua work owned by the mission.
- Route, geometry, coordinate, and unit-table utilities.
- Dynamic group construction with MIST's table-oriented helpers.
- Converting common DCS mission data into a simpler result for Harness.

Exact functions and table shapes must be checked at the pinned revision rather than recalled from current upstream documentation.

## Boundary rules

- MIST tables and functions remain in mission-side Lua.
- Return only JSON-safe summaries through Eval.
- Treat database entries as mission snapshots whose objects may later disappear.
- Avoid returning the complete database when a filtered, bounded result answers the question.
- Do not make an agent-facing API depend on undocumented internal fields without an explicit compatibility decision.

## Source lookup

The single-file implementation is the primary reference:

- third_party/mist/mist.lua
- third_party/mist/README.md

At the pinned revision, inspect the implementation for the mist version declaration, mist.DBs, and the specific helper being used. Search by the fully qualified function name and read nearby validation and return logic.

## MIST versus MOOSE

Choose MIST when utilities and data tables are sufficient. Choose MOOSE when the mission behavior needs a higher-level, persistent object model, coordinated tasking, or framework-managed state. Loading both is a mission-design decision, not a Harness prerequisite.
