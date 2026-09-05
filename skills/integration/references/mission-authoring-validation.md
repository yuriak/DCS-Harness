# Mission authoring structural validation

Use this reference after authoring a candidate `.miz` and again, read-only,
after the Human saves the approved final mission. This is structural Mission
Contract validation, not a universal mission generator and not live acceptance.

## Asset and scope

Copy and adapt
[`../assets/mission-authoring/mission_validator.py`](../assets/mission-authoring/mission_validator.py)
beside the current authorer under `runtime/workspace/`. Call
`validate_mission_structure()` with only the ground groups, aircraft groups,
geography points, and startup resources material to the current task.

The asset checks:

- exact expected unit counts/type counts and every unit's finite position;
- ground-group anchor distance and formation radius, with task-supplied limits;
- aircraft CLSID/settings payloads, finite fuel and task-supplied fuel ranges;
- route point count, every point coordinate, altitude, and speed;
- aircraft late activation, first-point start mode, home-airport relation, and
  inspectable group task;
- explicit geography point finiteness, suspicious origin, intended-area
  proximity, and optional expected-airport proximity;
- ordered Mission Start `DoScriptFile` actions, resource-key resolution, and,
  when given the `.miz` path, matching compiled trigger expressions.

All thresholds and expected values are task inputs. The asset contains no
formation doctrine, station geometry, airbase allocation, payload choice,
altitude block, or tactical role decision. Its input dictionaries are helper
arguments, not a public Harness Mission Contract schema.

## Example shape

Adapt rather than copy this literally:

~~~python
report = validate_mission_structure(
    final_mission,
    miz_path=final_path,
    ground_groups=[{
        "name": "SITE-1",
        "unit_count": 3,
        "unit_types": {"SensorType": 1, "LauncherType": 2},
        "anchor": intended_site,
        "max_anchor_distance_m": 300,
        "max_formation_radius_m": 250,
    }],
    aircraft_groups=[{
        "name": "SUPPORT-1",
        "unit_count": 1,
        "unit_types": {"ExactTypeId": 1},
        "payload": loadout_plan,
        "fuel": {"min": task_minimum_fuel, "max": aircraft_fuel_capacity},
        "route": {
            "min_point_count": 3,
            "altitude": {"min": task_minimum_altitude_m},
            "speed": {"min": task_minimum_speed_mps},
        },
        "late_activation": False,
        "start_mode": "cold",
        "home_base": "Human-approved exact airport name",
        "task": "AWACS",
    }],
    geography=[{
        "label": "blue bullseye",
        "point": final_mission.coalition["blue"].bullseye,
        "intended_area": {
            "x": planned_bullseye.x,
            "y": planned_bullseye.y,
            "max_distance_m": accepted_tolerance_m,
        },
    }],
    startup={
        "resources": ["mist.lua", "Moose_.lua", "startup.lua"],
    },
)
~~~

Pinned pydcs stores route speed in m/s after its authoring helper converts a
km/h argument. Fuel is the serialized aircraft fuel quantity used by the exact
type, not a universal percentage. Start-mode checks use the first route point's
pinned type (`TakeOffParking`, `TakeOffParkingHot`, `TakeOff`, ground variants,
or an in-flight Turning Point). Home-base checking uses that point's airfield ID
and the terrain airport record.

## Candidate, Human final, and live boundaries

Run the same required structural invariants after candidate reload and after
read-only final reload. Classify final differences as invariant failures,
accepted Human modifications, harmless extra content, or unresolved changes;
do not rewrite the Human final to make a report green.

`ok=true` means only that selected serialized invariants match. It cannot prove
that DCS executes startup Lua, an aircraft departs, a task persists, a weapon is
usable, an escort follows, or an RTB completes. Preserve the report with the
task-local Mission Contract, then use current-session live preflight and the
acceptance procedures in [persistent air tasks](persistent-air-tasks.md).

