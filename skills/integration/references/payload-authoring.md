# Aircraft payload authoring

Use this reference when assigning a pydcs aircraft payload before mission
launch or validating that a candidate/Human-approved final `.miz` preserved
the intended pylon entries. Read [pydcs](pydcs.md) and
[`.miz` and mission authoring](miz-and-mission-authoring.md) for the wider
offline/Human/live boundaries.

## Authority and scope

The `catalog` capability reports compatibility from the exact pinned pydcs
definitions. It does not select a tactically appropriate payload and does not
prove module ownership, local installation, Mission Editor acceptance, weapon
release, mass/drag/CG behavior, or live usability. Keep mission role and
loadout choice in the Agent/Human task context.

Resolve the exact aircraft `type_id` before selecting stores. Similar-looking
types can have different flyability, task, and pylon data. Query compatible
stores for that exact aircraft and optional pylon, then submit the complete
pylon/CLSID map to `catalog loadout-validate`. Do not apply a plan whose
validation result is false or whose required pylon definition is unavailable.

## Minimal application pattern

Copy
[`../assets/mission-authoring/payload_application.py`](../assets/mission-authoring/payload_application.py)
into the task's `runtime/workspace/` directory. Keep the catalog-validated
plan in task-local data rather than embedding a remembered CLSID in durable
Harness knowledge. The relevant authorer shape is:

~~~python
import json
from pathlib import Path

from dcs.mission import Mission
from payload_application import apply_group_loadout, validate_group_loadout

here = Path(__file__).resolve().parent
plan = json.loads((here / "loadout_plan.json").read_text(encoding="utf-8"))
pylons = plan["pylons"]

# `group` was created by the task-local authorer. By default this clears the
# existing payload and applies the same validated plan to every group unit.
apply_group_loadout(group, pylons, replace=True)
mission.save(str(here / "candidate.miz"))

candidate = Mission()
load_status = candidate.load_file(str(here / "candidate.miz"))
candidate_group = candidate.find_group(group.name, "exact")
payload_report = validate_group_loadout(candidate_group, pylons, exact=True)
~~~

At the pinned revision, `FlyingUnit.load_pylon` accepts a tuple shaped as
`(pylon_number, {"clsid": CLSID, optional "settings": object})` and stores it
in the serialized mission payload as `{"CLSID": CLSID, ...}`. The task asset
performs that case-sensitive shape conversion and checks all units/pylons
before clearing any existing loadout.

Treat non-empty pydcs load status, a missing group, or `payload_report.ok=false`
as candidate rejection. Include the structured report in task-local authoring
evidence when payload identity is mission-critical.

## Candidate, Human, and final checks

Use the same explicit plan at both read-only boundaries:

~~~text
catalog search/query/validate
  -> apply to task-local pydcs group
  -> save candidate
  -> reload candidate read-only and compare every unit's CLSID/settings
  -> Human reviews/edits/saves a distinct final in Mission Editor
  -> reload final read-only and compare every unit's CLSID/settings
  -> accept, classify an intentional Human change, or request correction
~~~

Do not rewrite the Human-approved final merely because it differs. Classify
the difference against the Mission Contract. A payload match after pydcs
reload is offline structural evidence only; retain Mission Editor review and
any material live preflight/HIL required by the scenario.

The helper's default `exact=True` also reports unexpected pylon entries. Use
`exact=False` only when the Mission Contract explicitly permits additional
Human-selected stores; intended pylons must still exist and match exactly.
