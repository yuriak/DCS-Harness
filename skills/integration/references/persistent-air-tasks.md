# Persistent air tasks

Use this reference when authoring or live-tasking CAP, AWACS, tanker, support
orbit, escort, or return-to-base behavior. It separates a valid task structure
from sustained simulator effect. For route placement and bullseyes, read
[mission planning and route semantics](mission-planning-and-routes.md). For
live task mechanics, read [dynamic aircraft and tasking](dynamic-aircraft-and-tasking.md).

## What “persistent” means

**[project convention]** A continuing task is persistent only for the behavior
and observation horizon actually established. A successful pydcs save, Mission
Editor inspection, controller call, or first telemetry sample is not sufficient.
A route with several turning points is not automatically a loop.

Keep construction and acceptance as separate task-local records:

~~~text
author candidate
  -> reload and inspect task structure
  -> Human Mission Editor review/save
  -> read-only final inspection
  -> DCS live preflight
  -> sustained telemetry + relevant events/logs
  -> accepted for the measured behavior/version
~~~

For the consolidated Mission Authoring v2 HIL, observe each continuing behavior
for at least ten minutes unless it reaches an independently decisive success or
failure first. That duration is this checkpoint's test design, not a universal
Harness timeout.

## Pinned authoring surfaces

**[stable API fact]** At pinned pydcs
`e20f328390aecaac2a7f82444b4f5a96ac6bb2c3`:

- `Mission.patrol_flight()`/`patrol_flight_to_group()` creates a CAP group,
  adds an air-engagement task, puts a Race-Track Orbit on `pos1`, and adds
  `pos2` as the second leg;
- `Mission.awacs_flight()` puts a Race-Track Orbit on the first station point
  and adds the second station point at `race_distance` along `heading`;
- `Mission.refuel_flight()` uses the same two-leg structure and also configures
  frequency plus TACAN when supported by the aircraft type;
- `Mission.escort_flight()` creates an Escort main-task group and installs an
  `EscortTaskAction` referring to the escorted group's numeric ID and route
  extent;
- `task.OrbitAction` supports Circle and Race-Track patterns, while a meaningful
  race-track requires two station points in the route/task structure;
- `FlyingGroup.land_at(airport)` authors a landing point, but the existence of
  that point does not prove live RTB completion.

These helpers encode source behavior, not scenario suitability. Inspect their
current source and serialized candidate rather than relying on this summary for
optional/default fields.

## Recipe status matrix

The following is the current evidence ledger, not a promise that every row is
already complete:

| Behavior | Candidate construction | Current live evidence | Status before combined HIL |
| --- | --- | --- | --- |
| Persistent CAP | pydcs two-point patrol/Race-Track | 900-second Su-27 trajectory, 2026-09-05 | verified sampled persistence |
| AWACS race-track | pydcs AWACS helper | 900-second A-50 trajectory | verified orbit, not radar service |
| Tanker race-track | pydcs refuel helper | 900-second IL-78M trajectory | verified orbit, not fuel transfer |
| Persistent support orbit | pydcs OrbitAction / MOOSE | 900-second authored An-26B Circle plus prior MOOSE evidence | verified tested paths |
| Escort | pydcs escort helper/action | Su-27 association with A-50 over 900 seconds | verified association, not combat effectiveness |
| RTB and landing | pydcs landing route | MiG-29S Anapa touch, land, taxi and stop | verified tested route |

Do not silently promote a pending row because a task call returns success.
After HIL, record the exact DCS version, pydcs revision, aircraft/start mode,
construction path, observation window, evidence, and limitations in the
appropriate hot reference.

## Construction rules

For each task-local recipe:

1. Select the exact aircraft type through `catalog`; static compatibility is not
   local installation or live behavior proof.
2. Choose base, start mode, route roles, station points, altitude datum, speed,
   payload, task, options, and recovery from the current Mission Contract.
3. For Race-Track, supply and validate both station endpoints. Do not set only
   the pattern label or assume a later waypoint will be interpreted correctly
   without inspecting the serialized task.
4. Keep station geometry and operational spacing scenario inputs. No fixed CAP
   width, tanker track, AWACS standoff, escort offset, or RTB trigger belongs in
   Harness doctrine.
5. Reload the candidate and inspect exact route/task fields. After Human save,
   reload the approved final read-only and classify differences.
6. Preserve group names and expected identity as review aids, but reacquire live
   physical identities after every DCS-gRPC mission/session change.

For escort, validate that the referenced group ID in the final mission belongs
to the intended current authored group. For RTB, state whether the intended
effect is simply a landing route, a live controller command, task reset, or a
framework method; these are different mechanisms and require separate evidence.

## Live acceptance matrix

Collect bounded, timestamped evidence appropriate to the claim:

| Check | CAP/AWACS/tanker/support orbit | Escort | RTB |
| --- | --- | --- | --- |
| Current identity | expected group and every unit | escort and escorted groups | commanded group |
| Initial task effect | reaches/transits toward station | closes/maintains intended association | turns/proceeds toward recovery |
| Sustained telemetry | plausible altitude, speed, heading, path changes; repeated station legs/circle | relative position and movement remain plausible while both exist | continuing recovery progress, then approach/touchdown when claimed |
| Events | no contradictory land/death/respawn | no contradictory loss/landing and relevant combat chronology if engaged | landing/runway-touch or equivalent event when available; no unexplained respawn |
| Diagnostics | no task/framework/script error | no missing target/wrapper/task error | no controller/task/airbase error |
| Session safety | evidence belongs to current DCS-gRPC session | both identities are current-session instances | command and outcome share the current session/instance |

Telemetry is evidence of motion, not intent. An orbit-like trace does not prove
that ROE, engagement limits, refueling service, radar operation, escort combat
effect, or fuel endurance is correct. Test those separately only when the
Mission Contract depends on them.

## Failure handling

**2026-09-05 HIL update, DCS 2.9.29.27468:** The authored Caucasus
CAP, A-50 AWACS racetrack, IL-78M tanker racetrack, An-26B Circle support,
and Su-27 escort of AWACS sustained airborne sampled trajectories over a
900-second post-Harness-restart window (181 samples per unit). Escort lead
followed AWACS; wingman showed larger turn/rejoin excursions. Authored
MiG-29S RTB produced Anapa runway-touch/land events, taxi and a stationary
ground outcome. This supersedes pending status for those exact recipes only;
it does not verify combat effectiveness, radar coverage or refueling transfer.

On unexpected behavior, retain the authored task structure, current identities,
telemetry window, relevant events, and structured diagnostics. Change one
variable at a time. Do not repeatedly respawn a same-named group or alternate
between `setTask`, `pushTask`, MIST, and MOOSE while the first outcome is
unknown. If a framework wrapper is merely not registered yet, retry only that
bounded lookup; do not duplicate the physical group.
