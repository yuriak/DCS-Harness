# Mission planning and route semantics

Use this reference when translating scenario geography into an offline mission
layout, assigning meaning to aircraft waypoints, choosing bullseyes, or checking
whether background forces form a coherent initial world. Read
[coordinates and units](coordinates-and-units.md) before doing coordinate math
and [`.miz` and mission authoring](miz-and-mission-authoring.md) before changing a
mission file.

## Boundary

**[project convention]** Harness supplies maintained geography, coordinate
operations, pinned authoring surfaces, and validation evidence. The Agent and
Human decide the scenario's territory, objectives, force posture, route intent,
and acceptable risk. Keep those decisions in the current task under
`runtime/workspace/`; do not turn them into a universal planner or core doctrine.

Static geography is planning evidence, not current battlefield truth. Confirm
live ownership, surviving units, weather, and active threats through current
observation when they matter after mission start.

## Build the spatial picture first

Before placing forces or drawing routes, extract the scenario facts that are
actually established:

- theatre and the participating coalitions/countries;
- friendly, opposing, neutral, or politically constrained territory;
- any political boundary, front, conflict area, coastline, or transit corridor;
- relevant airbases, objective areas, defended zones, and rear support space;
- each coalition's intended bullseye, if the scenario uses one.

Resolve named places through maintained `geo` data where possible. Use bounded
lookup, distance, bearing, and offset operations instead of guessing local
coordinates. If maintained knowledge cannot establish a named feature, record
the uncertainty and use a Human-approved coordinate; optional cold/community
sources remain discovery aids rather than mission truth.

For every material position, preserve both its semantic source and coordinate
frame. DCS route and bullseye tables use local world `x/y` for the horizontal
world `x/z` plane. Geographic latitude/longitude is a different representation.
State whether bearings are true/geodesic or local-grid; do not silently mix
them. Keep Harness-boundary distance, altitude, and speed values canonical in
metres, metres, and metres/second, while noting that pinned pydcs convenience
methods may accept different units.

## Give waypoints operational meaning

**[project convention]** Do not leave a material route described only as WP1,
WP2, and WP3. Assign each point a task-local semantic role and preserve that
intent beside the authoring script or Mission Contract. Use only the roles the
mission needs; this is a vocabulary, not a required route shape:

- departure or runway departure;
- assembly or rejoin;
- transit;
- ingress;
- CAP or support station;
- orbit leg A and orbit leg B;
- initial point or attack start;
- objective or target area;
- egress;
- recovery rejoin;
- RTB or approach;
- landing.

An aircraft need not visit all of these points. A point can share more than one
role when that is explicit and unambiguous. Names are review aids; DCS behavior
comes from the serialized route/action/task fields, not from the label.

At the pinned pydcs revision, `FlyingGroup.add_waypoint()` creates a Turning
Point, stores altitude directly, and converts its speed argument from km/h to
m/s. `FlyingGroup.land_at()` creates the landing point. Inspect the current
pinned implementation before relying on further defaults. Persistent orbit and
escort behavior is covered by [persistent air tasks](persistent-air-tasks.md).

## Task-local route intent record

For important groups, retain a compact record near the authorer. Adapt fields
to the scenario rather than treating this example as a Harness schema:

~~~json
{
  "group": "BLUE-AWACS-1",
  "role": "awacs",
  "home_base": "Kutaisi",
  "start_mode": "cold",
  "route": [
    {"name": "DEPARTURE", "intent": "departure"},
    {"name": "STATION-A", "intent": "orbit_leg_a"},
    {"name": "STATION-B", "intent": "orbit_leg_b"},
    {"name": "RECOVERY", "intent": "rtb"}
  ],
  "station": {
    "altitude_m": 7000,
    "speed_mps": 180,
    "pattern": "race_track"
  },
  "acceptance": {
    "candidate_reload": "pending",
    "human_final": "pending",
    "live_sustained": "pending"
  }
}
~~~

Also record material ROE, reaction, engagement, payload, altitude datum, reserve
state, and accepted Human differences when the mission depends on them. Keep
scenario prose and tactical rationale outside the serialized DCS task itself.

## Bullseyes

**[project convention]** Treat coalition bullseyes as part of the initial
mission substrate when the scenario uses bullseye communication. Choose each
from the scenario's geography and communication needs. Red and blue bullseyes
may and usually should be considered independently; do not accept an origin or
terrain default merely because it serializes.

At the pinned pydcs revision, each `Mission.coalition["blue" or "red"]` owns a
`bullseye` value and `Coalition.set_bullseye()` assigns it. The serialized shape
is `{"x": local_x, "y": local_northing}`. Some terrain defaults are `(0, 0)`,
some are map centers, and some are explicit points, so a default is not evidence
of scenario fitness.

Record at least coalition, local coordinate, any geographic/named-place source,
selection rationale, and Human acceptance in the Mission Contract. After the
Human saves the final `.miz`, compare the bullseyes read-only. Live bullseye
calls can use the accepted reference, but live events or telemetry do not make
an offline default meaningful retroactively.

Harness intentionally does not auto-select a supposedly perfect bullseye.

## Background-force coherence

Review each background aircraft as part of the whole initial world, not as an
isolated valid group. Ask scenario-specific questions:

- Is its home base consistent with coalition, start mode, parking, and expected
  recovery?
- Does its route connect departure, station or objective, egress, and recovery
  without an unexplained discontinuity?
- Is the station in the intended friendly/rear/contested space, and is that
  interpretation supported by the scenario rather than a generic distance?
- Are altitude, speed, payload, endurance assumptions, task, and role mutually
  coherent for the selected exact type?
- Do AWACS, tanker, CAP, escort, and strike/support areas relate coherently to
  one another without encoding a universal formation or spacing rule?
- Is an authored reserve visibly distinct from an already committed background
  flight?

These questions detect contradictions; they do not prescribe tactics. Any
minimum standoff, patrol width, altitude block, timing, or force ratio belongs
to the task/Human decision.

## Acceptance

Candidate reload and final read-only validation can establish coordinates,
labels, route points, tasks, bullseyes, and cross-record consistency. They
cannot establish that an AI departs, remains on station, escorts effectively,
or returns to base. Use the live acceptance matrix in
[persistent air tasks](persistent-air-tasks.md), sustained telemetry, events,
and current diagnostics before promoting a route from structurally present to
operationally verified.

