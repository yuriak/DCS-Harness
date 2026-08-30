# Dynamic aircraft and AI tasking

Use this reference before dynamically spawning aircraft, assigning routes or
controller tasks, or declaring a persistent AI behavior ready.

## Select and verify the runtime layer

**[project convention]** Use the narrowest layer that owns the required
behavior: native DCS Lua for a small direct action, MIST for table-oriented
utilities and routes, or MOOSE for framework-managed orchestration. Keep a
scenario-specific adapter under `runtime/` until repeated dogfooding proves a
generic Harness capability is needed.

**[known caveat]** A file or submodule on the Harness filesystem does not load
MIST or MOOSE into a mission. Probe the exact symbol required by the chosen
path in every new session.

**[verified current-version behavior]** During the first directing
dogfooding, `MOOSE ~= nil` falsely suggested that MOOSE was absent while DCS
logs showed MOOSE dynamic-group registration and `BASE` was present. MOOSE
does not require a global named `MOOSE`. Probe required classes such as
`BASE`, `SPAWN`, or `GROUP` individually; do not turn that example list into a
universal readiness test.

## Spawn data and routes

**[stable API fact]** `coalition.addGroup` accepts a country, group category,
and group data table. The exact aircraft/group table, route points, tasks,
payload, identifiers, and optional fields are DCS-version-sensitive; derive
them from a known mission template or inspect the pinned definitions and
framework source.

**[project convention]** Keep world and route coordinate shapes explicit.
Route `x/y` is the horizontal world `x/z` plane; altitude and speed remain
separate, canonically metres and metres/second at Harness boundaries.

**[version-sensitive observation]** In the first dogfooding, airborne groups
whose helper supplied only one spawn-position turning point later returned
and landed. Event evidence proves those landings, but the run changed several
route/task variables and does not prove that every single-point route always
causes RTB.

**[known caveat]** Treat a nontrivial continuing route or persistent task as a
precondition for sustained airborne behavior, then verify it over time. Do
not promote the preceding observation into a universal DCS rule until the
targeted dynamic-aircraft HIL is completed.

## Controller task model

**[stable API fact]** In the pinned mission definitions, `setTask` replaces
the controller's current task, while `pushTask` places a task at the front of
the task queue. MIST `goRoute` constructs a DCS `Mission` task and passes it to
`setTask`. Inspect exact task tables before using either path.

**[stable API fact]** Pinned MOOSE constructs a circle orbit with one point.
Its general `TaskOrbit` changes to `RACE_TRACK` only when a second coordinate
is supplied, and then emits `point2`. A race-track adapter must therefore
provide and validate both ends rather than only changing the pattern label.

**[known caveat]** Task APIs do not provide a universal readable mirror of
all current AI intent. The pinned static `Controller` definition contains
`hasTask`, `setTask`, `pushTask`, `popTask`, and `resetTask`, but no
`getTask`. Do not invent a getter because another environment or model memory
suggests one.

## Acceptance is not effect

**[project convention]** A successful spawn, `setTask`, `pushTask`, MIST
helper call, MOOSE method, or Lua envelope proves only that the call completed.
For a continuing aircraft behavior, verify at least:

- the expected group and units exist;
- identity is the expected current instance;
- multiple telemetry samples retain plausible altitude, position, heading,
  and speed;
- the path changes consistently with the intended route or orbit;
- events do not instead show runway touch, landing, destruction, or respawn;
- relevant diagnostics contain no task/framework error.

Use telemetry history rather than shell sleep followed by two unrelated
position reads. Correlate discrete events and logs where they add independent
evidence.

## Readiness and recovery

**[project convention]** “Helper loaded” and “initial world verified” are
different readiness states. Validate one disposable minimal aircraft or one
formal group before creating the rest of a dynamic initial world.

Change one variable per recovery attempt. If the same low-level path keeps
failing, stop live scenario progression, preserve structured evidence, and
switch to a controlled diagnostic or abort. Do not hard-code a universal
time budget; choose a stop condition proportional to the current task and
human experience.

## Sources to inspect

- `third_party/dcs-lua-definitions/library/mission/coalition.lua`
- `third_party/dcs-lua-definitions/library/mission/controller.lua`
- `third_party/mist/mist.lua` (`mist.goRoute` and the selected helper)
- `third_party/moose/Moose Development/Moose/Wrapper/Controllable.lua`
- the exact task-local adapter under `runtime/`
