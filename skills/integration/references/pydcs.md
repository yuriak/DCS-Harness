# pydcs

## Role

pydcs is a Python library for offline DCS mission authoring. It can create, load, modify, and save mission data and provides static models for countries, unit types, terrain, groups, waypoints, and related mission structures.

Use pydcs before DCS loads a mission. It is not a live simulator API and does not report the current state of units in a running mission.

## Good fits

- Generating a new .miz mission from structured inputs.
- Applying deterministic edits to an existing mission package.
- Creating or inspecting coalitions, countries, groups, units, routes, tasks,
  triggers, actions, resources, and mission metadata supported by the pinned
  library.
- Authoring player/client slots, background order of battle, or dormant
  reserves where the exact pinned models support the required form.
- Inspecting bullseyes, weather, warehouses, airbase state, and other static
  mission structures where current source and tests establish support.
- Reloading a Human-approved final `.miz` for read-only invariant validation.
- Looking up static aircraft, vehicle, ship, weapon, country, and terrain definitions.
- Building reproducible mission fixtures for integration or HIL testing.

Check exact support, load/save symmetry, and serialization behavior at the
pinned revision before assuming any item above. DCS mission format changes can
outpace an older library release, and static support does not establish
Mission Editor round-trip or live DCS behavior.

At the pinned revision, `Action.__repr__` renders a translatable `DoScript`
parameter with `getValueDictByKey(...)`. A DCS 2.9.29.27278 MT HIL nevertheless
observed Mission Editor rewriting the compiled action to execute the literal
dictionary key while leaving the dictionary value and pydcs model readable.
Do not validate critical inline scripts only through the action object or
dictionary; inspect compiled trigger text and verify the live effect. The same
round trip did preserve and execute the tested resource-backed
`DoScriptFile` actions. A corrected follow-up also verified the exact small
startup marker as a third embedded `DoScriptFile` after Mission Editor save and
in a fresh live DCS session.

For decisions about what should be authored versus left dynamic, the
candidate/Human/final lifecycle, background/reserve/emergent semantics,
Mission Contract, and live preflight, read
[`miz-and-mission-authoring.md`](miz-and-mission-authoring.md). This reference
owns pydcs technical boundaries; the mission-authoring reference owns the
workflow and directing doctrine.

## Offline boundary

~~~text
Python and pydcs -> candidate .miz -> Human Mission Editor -> final .miz
final .miz -> pydcs read-only validation -> Human loads mission in DCS
~~~

Saving a .miz file does not update an already running mission. Live observations and actions belong to Harness, DCS-gRPC, or mission-side Lua.

Generated mission changes should be written to an explicitly chosen workspace output. Do not overwrite user missions, DCS Saved Games content, or an active test fixture without clear authorization and a recoverable workflow.

## Source lookup

Start with:

- third_party/pydcs/dcs/mission.py
- third_party/pydcs/dcs/action.py
- third_party/pydcs/dcs/triggers.py
- third_party/pydcs/dcs/coalition.py
- third_party/pydcs/dcs/unitgroup.py
- third_party/pydcs/dcs/weather.py
- third_party/pydcs/dcs/terrain/terrain.py
- third_party/pydcs/dcs/terrain
- third_party/pydcs/dcs/planes.py
- third_party/pydcs/dcs/vehicles.py
- third_party/pydcs/tests/test_mission.py
- third_party/pydcs/tests/test_triggers.py
- third_party/pydcs/tests/test_weather.py
- third_party/pydcs/README.md

Mission.load_file and Mission.save are central entry points in the pinned mission model. Inspect the concrete class and tests for required arguments, generated identifiers, archive handling, and persistence semantics.

## Design rules

- Separate pure mission construction from filesystem output.
- Make generated inputs and random seeds explicit when reproducibility matters.
- Write candidates to an explicitly chosen workspace path. After Human review,
  reload the approved final file without overwriting it and validate only
  mission-critical invariants.
- Validate output by loading it again and, for material behavior, by controlled DCS testing.
- For critical trigger scripts, distinguish preserved localized text from the
  executable expression in `mission.trig.actions`; validate both before live
  testing.
- Do not treat static catalogs as proof that an asset is installed or usable in the player's DCS configuration.
- Do not couple live agent decisions to a stale offline mission model.

## Generated aircraft/loadout catalog

The reviewed base catalog at `tools/data/catalog/aircraft.json` is generated
by `tools/build_aircraft_catalog.py` from the pinned fixed-wing, helicopter,
task, pylon, and weapon definitions. It records the exact pydcs revision,
generator version and timestamp, aircraft category/flyability/task facts,
global store metadata, and per-pylon compatibility. Run the builder with
`--check` when the pinned pydcs revision or generator changes.

The base catalog deliberately does not scan the player's DCS installation and
does not include local `UnitPayloads` presets. A declared pylon for which the
pinned generated class exposes no matching `PylonN` definition is retained
with `definition_available=false` and no invented compatible stores. Catalog
presence establishes only a pinned static definition; it does not establish
module ownership, installation, Mission Editor acceptance, or live usability.

Use the stateless `catalog` capability for bounded discovery rather than
opening the full generated JSON: search or show the exact aircraft type, list
pylon summaries, query compatible stores, inspect preset-enrichment status,
and validate a proposed pylon/CLSID mapping. Similar names can identify
different upstream types—for example, the pinned definitions distinguish
`F-16C bl.50` from `F-16C_50`—so preserve the returned exact `type_id` instead
of merging results by punctuation or remembered display name. Validation
checks static compatibility only; it does not calculate aircraft performance
or recommend a mission loadout.

For the catalog-to-`FlyingUnit.load_pylon` data-shape conversion and the
candidate/Human-final read-only comparison workflow, use
[aircraft payload authoring](payload-authoring.md). Keep the selected plan and
its validation evidence task-local.

## Use another layer when

- The question concerns current mission state: use a typed live operation.
- The change must happen inside a running mission: use DCS Lua, MIST, or MOOSE through a bounded operation.
- Only Lua signature discovery is needed: use [dcs-lua-definitions](dcs-lua-definitions.md).
