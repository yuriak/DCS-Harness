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

**[verified current-version behavior]** In the targeted HIL on DCS
2.9.29.27278, the mission exposed MIST 4.5.126 and the MOOSE `BASE`, `SPAWN`,
`GROUP`, and `_DATABASE` tables. The same symbols were reacquired after a
mission reload rather than assumed to survive the DCS-gRPC session boundary.
Probe the exact function or class needed by the current adapter in every new
session; a version marker or one framework table does not prove every required
surface is usable.

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

**[verified current-version behavior]** In the targeted HIL, a dynamically
created KJ-2000 with four explicit turning points remained airborne for more
than three minutes at approximately 7,000 m and 180 m/s, began the expected
waypoint turn, and produced no landing or RTB event before retask. This proves
that the tested multi-point route shape was sufficient for that controlled
run. It does not prove that four points, that aircraft type, or those values
are universally required.

**[project convention]** Give continuing airborne behavior a nontrivial route
or persistent task appropriate to the aircraft, then verify the result over
the needed horizon. Do not infer a universal single-point-route rule from the
failed dogfooding or infer a universal route recipe from the successful HIL.

**[known caveat]** The HIL KJ-2000 flew and accepted tasking, but current DCS
logs also reported missing livery files and a corrupt damage model. Flight and
task evidence does not validate that aircraft's visual assets or damage model;
use a cleaner disposable support type when those surfaces matter.

## Controller task model

**[stable API fact]** In the pinned mission definitions, `setTask` replaces
the controller's current task, while `pushTask` places a task at the front of
the task queue. MIST `goRoute` constructs a DCS `Mission` task and passes it to
`setTask`. Inspect exact task tables before using either path.

**[stable API fact]** Pinned MOOSE constructs a circle orbit with one point.
Its general `TaskOrbit` changes to `RACE_TRACK` only when a second coordinate
is supplied, and then emits `point2`. A race-track adapter must therefore
provide and validate both ends rather than only changing the pattern label.

**[verified current-version behavior]** A group created through native
`coalition.addGroup` existed immediately, while `GROUP:FindByName` was not yet
ready in the same Eval call. One later bounded lookup succeeded after MOOSE
registered the dynamic group. Treat native DCS existence and framework-wrapper
readiness as separate states; report not-ready and retry only the wrapper-bound
step instead of spawning a duplicate group.

**[verified current-version behavior]** The targeted HIL used the live MOOSE
`GROUP` wrapper to build and `SetTask` a one-point Circle Orbit. Subsequent
telemetry showed sustained heading and position changes around the orbit while
altitude and speed remained plausible. This validates the tested Circle path;
the pinned two-coordinate requirement remains authoritative for Race-Track.

**[verified current-version behavior]** A dynamically registered two-ship
J-11A group reported `OptionROEHoldFirePossible=true`, accepted
`OptionROEHoldFire`, retained a controller task, and remained on its initial
route during the focused observation window. No hostile engagement occurred,
so this proves option acceptance and sustained flight, not a weapons-release
outcome under combat conditions.

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

**[verified current-version behavior]** The HIL destroyed one support group,
waited for a complete telemetry snapshot to observe its absence, then spawned
the same group and unit names once. DCS assigned new group/unit IDs, telemetry
assigned a new `instance_id`, and events recorded a new birth. For lifecycle
verification, require an observed absence between generations and use physical
identity rather than treating a reused name as continuity.

## Readiness and recovery

**[project convention]** “Helper loaded” and “initial world verified” are
different readiness states. Validate one disposable minimal aircraft or one
formal group before creating the rest of a dynamic initial world.

The successful targeted path established these distinct checkpoints:

1. mission library symbols and the exact required methods are available;
2. the native group and expected units exist;
3. any required MOOSE wrapper has registered;
4. telemetry establishes sustained route behavior over an appropriate window;
5. a retask is followed by a new sustained trajectory; and
6. events and current diagnostics do not instead show landing, destruction,
   or a task/framework failure.

The three-minute support-aircraft window was suitable evidence for this HIL,
not a universal readiness duration. Choose the observation horizon according
to the behavior being claimed.

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
