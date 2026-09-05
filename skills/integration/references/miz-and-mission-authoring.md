# `.miz` and mission authoring

Use this reference when deciding what to author before mission launch, building
or inspecting a `.miz` with pydcs, reviewing a Human-edited mission, or moving
from offline acceptance to live preflight. Use [pydcs](pydcs.md) for the
technical library boundary and exact pinned source paths.

## Role

**[project convention]** Treat a `.miz` as a pre-authored initial-world state
and mission substrate. It is neither a player-only container nor a fully
scripted story. Dynamic generation should serve dynamic intent, not replace
stable offline authoring merely because live spawning is possible.

**[project convention]** Keep mission-specific authoring scripts and outputs
under `runtime/workspace/`. Use pinned pydcs directly until repeated work
establishes a small, scenario-independent capability worth reviewing for
durable Harness code.

## Mission lifecycle

**[project convention]** Use these boundaries in order:

~~~text
Scenario and Human intent
  -> Agent authors candidate .miz offline
  -> Human reviews, edits, and saves in DCS Mission Editor
  -> Human-approved final .miz
  -> Agent reloads final .miz for read-only validation
  -> task-local Mission Contract accepted
  -> Human launches mission
  -> Agent performs live preflight
  -> READY
  -> Human begins operation
  -> live directing loop
~~~

Each boundary establishes something different:

- Offline acceptance establishes what the mission definition contains.
- Human acceptance establishes which mission file is approved for launch.
- Live acceptance establishes what DCS actually loaded and what behavior is
  currently occurring.

Do not collapse these into one save/load success condition.

## What belongs in `.miz`

**[project convention]** Prefer offline authoring for stable initial facts that
benefit from structured construction and Mission Editor review, including the
theatre, coalition-country mapping, player/client slots, initial airbase
ownership, bullseyes, date/time/weather, warehouse baseline, startup
resources/triggers, initial order of battle, static infrastructure, and
routes/tasks or dormant definitions whose details are known before launch.

Keep narrative pacing, escalation timing, tactical response, commitment of
uncommitted resources, player-responsive decisions, and genuinely emergent
forces in the live directing loop.

Before placing initial forces, use
[mission planning and route semantics](mission-planning-and-routes.md) to build
the task-local spatial picture, assign operational meaning to material route
points, select coalition bullseyes deliberately, and check background-force
coherence. Use [persistent air tasks](persistent-air-tasks.md) for continuing
CAP, AWACS, tanker, support orbit, escort, and RTB construction/acceptance.

## Hybrid mission model

**[project convention]** Classify assets by directing semantics, not by whether
their definition is already present in the mission file.

### Background and persistent world units

These should exist at mission start and normally follow stable routes or tasks;
their appearance is not itself a directing decision. Examples include routine
AWACS, tankers, patrols, defenses, convoys, naval patrols, and rear-area
support when the current scenario calls for them.

Author complex placement, parking, payload, formation, route, task, altitude,
speed, or home-base details offline when practical. A pre-authored background
unit remains a live asset: the Director may later retask, redirect, withdraw,
or otherwise command it.

### Authored reserve and dormant assets

These belong to the initial resource pool or order of battle, but are not yet
committed to the current fight. Their stable identity, type, payload, callsign,
home base, route, and task may be authored in advance while their initial state
uses a mechanism verified for the supported pydcs and DCS versions.

**[project convention]** Authored does not mean committed. Finding a reserve
definition in the `.miz` does not establish that it is active, launched, or
allocated. Commitment, activation, retasking, withdrawal, and reassignment
remain live decisions.

### Truly dynamic and emergent assets

Use live DCS Lua, MIST, or MOOSE creation when the asset's appearance is itself
an emergent decision or consequence: an unexpected reinforcement, improvised
response, emergency package, rescue element, branch-specific force, temporary
convoy, relocation, or actor outside the initial order of battle.

Do not pre-author every possible branch merely to avoid live creation. Do not
live-create every stable background asset merely to demonstrate spawning.
Choose the boundary that improves accuracy, reviewability, and efficiency
without freezing decisions that belong to live directing.

## Human Mission Editor authority

**[project convention]** The Human-approved `.miz` selected for launch is the
final authoring authority. After the Human saves it, reload that final file and
validate it read-only. Do not immediately rewrite it from the earlier candidate
or treat every difference as an error.

Classify relevant differences as:

- required invariant violation;
- accepted Human modification;
- harmless additional content; or
- unsupported or uncertain change requiring review.

Mission Editor and pydcs serialization need not be perfectly symmetric, and a
Human may intentionally adjust parking, placement, payload, weather, routes,
triggers, or decoration. Request correction only when a required invariant is
broken or uncertainty prevents safe acceptance.

## Mission Contract

**[project convention]** A Mission Contract is a task-local Human/Agent
agreement about the final mission selected for launch, not a Harness schema or
public API. Keep any `mission_contract.json` or `mission_contract.md` under
`runtime/workspace/` and choose fields according to the scenario.

Useful facts can include the final mission path and hash, theatre,
coalition-country mapping, player slots, airbase ownership, bullseyes,
environment, warehouse policy, startup resources, background groups, dormant
reserves, expected initial states, accepted Human changes, and critical
invariants. Do not freeze this example into a universal contract model.

## Offline validation

**[project convention]** Reload the Human-approved final `.miz` without
rewriting it. Validate only invariants material to the current mission, report
warnings and accepted Human modifications separately from failures, and record
a final content hash when identity matters.

For a reusable task-local structural checker, copy and adapt the
[Mission Authoring Validator v2 asset](mission-authoring-validation.md). It can
check every ground-unit position and formation extent, aircraft payload/fuel/
route/start/base/task fields, explicit geography relations, and ordered
resource-backed startup actions without creating a universal mission schema.

Offline inspection can establish that definitions exist, such as:

- theatre and coalition-country structure;
- player/client slots and authored start configuration;
- airbase ownership, bullseyes, date/time/weather, or warehouse structures;
- group/unit types, names, routes, tasks, and dormant flags;
- resource entries and startup trigger/action definitions.

It cannot establish that a script loaded in DCS, a unit is alive, an AI route
persists, a reserve activates correctly, or a task has the intended tactical
effect. A pydcs save/load round trip is not live verification.

### Multi-unit ground geometry

**[project convention]** Treat every ground-unit position as an explicit
authoring input. Appending a unit with `Mission.vehicle(...)` leaves that unit
at the pinned pydcs default local `(0, 0)` until the author assigns a position;
`group.add_unit(...)` does not derive a useful formation from the lead unit.

For a task-local authorer, copy and adapt
[`../assets/mission-authoring/formation_geometry.py`](../assets/mission-authoring/formation_geometry.py).
Supply one `(x/east, y/north)` offset per unit, including the lead, and choose
the offsets from the current mission's intent. Before saving and again after
read-only reload, validate finite coordinates, suspicious local-world origin,
distance from the intended anchor, pairwise separation when material, and the
formation bounding radius. Set the distance thresholds in the task's Mission
Contract; the template intentionally contains no universal battery layout or
role doctrine.

The helper validates the complete placement request before changing the group.
Its validator returns a JSON-safe report suitable for inclusion in a task-local
candidate/final validation result. A clean structural report establishes only
authored geometry, not terrain suitability, line of sight, tactical quality,
or live DCS behavior.

For critical `Do Script` actions, also inspect the compiled
`mission.trig.actions` expression in the archive. Resolving the action's text
through `l10n/DEFAULT/dictionary` proves that the text is preserved, but does
not prove that DCS will dereference the dictionary key when it executes the
compiled trigger. Live startup-symbol and log checks remain required.

## Live preflight

**[project convention]** After DCS loads the final mission, reacquire the
current DCS-gRPC session and verify the critical runtime path before reporting
READY. Depending on the mission, this includes:

- current theatre and player state;
- exact required MIST/MOOSE symbols;
- expected background groups and current physical identities;
- telemetry freshness and sustained behavior over an appropriate window;
- event and log health plus absence of contrary failures;
- the intended activation/tasking path for any critical reserve.

Mission definition exists does not mean runtime effect exists. Verify
continuing routes, orbits, holds, or patrols through bounded telemetry history
and relevant events/logs, following
[observation and verification](observation-and-verification.md). If reserve
activation must be established, test one disposable or narrowly scoped path
before broad scenario progression.

## MIST and MOOSE resources

**[stable API fact]** At the pinned pydcs revision, `Mission` owns a
`MapResource` that can add and preserve mission resource files, trigger rules
include mission-start rules, and `DoScriptFile` serializes a resource-keyed
script action. These static interfaces make an embedded startup-library path
plausible; inspect their current source and tests before constructing it.

**[version-sensitive HIL evidence]** With pinned pydcs
`e20f328390aecaac2a7f82444b4f5a96ac6bb2c3` and DCS 2.9.29.27278 MT, two
mission-start `DoScriptFile` actions survived a pydcs -> Mission Editor -> DCS
round trip and loaded MIST 4.5.126 plus the tested MOOSE `BASE`, `GROUP`,
`SPAWN`, and `_DATABASE` globals. Keep resource/action inspection and exact
live-symbol probes in the acceptance path; this result does not establish
every resource arrangement or later DCS release.

The same HIL found a narrower failure: a pydcs `DoScript` whose text was stored
under `DictKey_Translation_7` remained readable through the archive dictionary
and pydcs reload, but after Mission Editor save the compiled trigger executed
the literal key. DCS logged `'= expected near <eof>'`, and the expected global
was absent. Do not use this observed inline `DoScript` round-trip for critical
startup code. A corrected follow-up embedded the same marker as a third
`DoScriptFile`; it survived Mission Editor save, produced a resource-keyed
compiled action, set the expected global live in a new session, and produced no
new mission-script error. Prefer that tested resource-backed pattern for small
critical startup helpers, while still verifying the exact resource and live
effect for each supported workflow.

## Dormant reserve caveats

**[stable API fact]** The pinned pydcs group model reads and writes moving-group
`lateActivation`, and its flying-group model reads and writes `uncontrolled`.
The source also contains task/trigger helpers related to delayed starts. These
facts establish static model surfaces, not the correct dormant pattern for a
particular aircraft, start mode, trigger path, or DCS release.

**[version-sensitive HIL evidence]** With the pinned pydcs revision and DCS
2.9.29.27278 MT, a two-ship cold-start Su-27 group using
`late_activation = True` survived Mission Editor save, was visible live as two
defined but inactive units, remained absent from active telemetry, and
activated exactly once through `GroupService.Activate`. Two new physical
identities, birth events, engine-start events, and sustained taxi movement
were then observed. This establishes that exact disposable pattern for the
tested versions; it does not establish `uncontrolled`, other start modes,
aircraft, parking layouts, activation paths, or automatic airborne completion.

## From repeated authoring to durable capability

**[project convention]** Do not add a `miz`, `mission`, or pydcs-wrapper plugin
for this workflow now. First collect evidence across multiple scenarios or
HILs that the same bounded, deterministic, scenario-independent primitive is
being reimplemented and that wrapping it materially reduces errors or tool
cost.

Possible future candidates might be a narrowly bounded startup-resource
embedder or invariant validator, but their interfaces must be derived from
observed repetition and verified round-trip behavior. A future capability
should not attempt to wrap the whole pydcs API or move scenario intent into
Harness core.

## Sources to inspect

- `third_party/pydcs/dcs/mission.py` (`Mission`, `MapResource`, load/save)
- `third_party/pydcs/dcs/action.py` (`DoScriptFile`)
- `third_party/pydcs/dcs/triggers.py`
- `third_party/pydcs/dcs/coalition.py`
- `third_party/pydcs/dcs/unitgroup.py` (group/task/dormant fields)
- `third_party/pydcs/dcs/weather.py`
- `third_party/pydcs/dcs/terrain/terrain.py` (warehouse structures)
- `third_party/pydcs/tests/test_mission.py`
- `third_party/pydcs/tests/test_triggers.py`
- `third_party/pydcs/tests/test_weather.py`
