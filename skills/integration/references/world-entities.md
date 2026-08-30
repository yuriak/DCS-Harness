# World entities, coalition, and airbases

Use this reference when identifying live objects, correlating samples and
events, or choosing an airbase based on current mission state.

## Country and coalition

**[stable API fact]** Country and coalition are different facts. A country is
the nation used to create or own mission objects; coalition is the current
side such as neutral, red, or blue. DCS exposes mappings and object-side
queries separately.

**[project convention]** Do not infer coalition from aircraft type, country
name, livery, callsign, scenario prose, or a static catalogue. Query the live
object or current mission state when the answer affects an action.

**[version-sensitive observation]** In DCS 2.9.29.27278 mission Lua,
`Group:getCountry()` was absent even though the pinned static definitions
declare it. The telemetry batch therefore reads country from each unit. Check
the current scripting context before relying on a definition-only method.

## Group, unit, and physical identity

**[stable API fact]** A DCS group contains units and has its own identifier,
name, category, coalition, controller, and lifecycle. A unit ID and unit name
are not interchangeable with the containing group ID and group name.

**[project convention]** For current-session trajectory work, prefer
telemetry's `instance_id` over name alone. Harness derives it from session,
DCS unit ID, name, and a generation:

- continuous same ID/name observations retain one instance;
- same name with a different DCS ID is a different instance;
- disappearance from a complete snapshot followed by reappearance starts a
  new generation;
- absence from a partial snapshot does not by itself end an instance;
- session rollover resets current identity state.

Querying by unit name can legitimately return more than one physical
instance. Do not merge those instances into one scenario resource without an
explicit Agent-owned ledger and evidence.

**[verified current-version behavior]** The supported live telemetry mission
contained an active unit with an empty DCS name. Harness preserved it as
`unit.name = null` and retained its ID and group. Names are therefore useful
labels, not mandatory physical identity.

## Airbases and destinations

**[stable API fact]** DCS mission Lua exposes live airbase objects and
coalition queries. Airbases may include map airfields, FARPs, or ship-backed
objects depending on the API and mission.

**[project convention]** The `geo` catalogue supplies static reference
location and provenance. It does not assert current ownership, runway state,
serviceability, capture state, or suitability for a particular aircraft.
Obtain those mission facts live when they matter.

**[known caveat]** Do not choose an RTB or landing destination solely from
nearest geographic distance or static theatre assumptions. Current coalition,
aircraft constraints, tasking, damage, and observed AI behavior can all matter.

**[version-sensitive observation]** The first directing dogfooding recorded
several dynamically created aircraft landing at concrete airbases, but it did
not establish a general rule for how DCS AI selects an automatic RTB field.
Treat automatic destination choice as unverified until focused HIL supports a
narrower conclusion.

## Session boundary

**[project convention]** A new DCS-gRPC session is a new battle context.
Reacquire groups, units, players, airbases, mission-loaded libraries, and
identity assumptions. Do not use an old event ledger or telemetry database as
proof that an object exists in the new mission.

## Sources to inspect

- `tools/src/py/dcs_harness_runtime/telemetry_memory.py`
- `tools/src/py/dcs_harness_runtime/telemetry_capture.py`
- `third_party/dcs-grpc/protos/dcs/common/v0/common.proto`
- `third_party/dcs-lua-definitions/library/mission/coalition.lua`
- `third_party/dcs-lua-definitions/library/mission/group.lua`
- `third_party/dcs-lua-definitions/library/mission/unit.lua`
- `third_party/dcs-lua-definitions/library/mission/world.lua`
