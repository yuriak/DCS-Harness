# pydcs

## Role

pydcs is a Python library for offline DCS mission authoring. It can create, load, modify, and save mission data and provides static models for countries, unit types, terrain, groups, waypoints, and related mission structures.

Use pydcs before DCS loads a mission. It is not a live simulator API and does not report the current state of units in a running mission.

## Good fits

- Generating a new .miz mission from structured inputs.
- Applying deterministic edits to an existing mission package.
- Creating coalitions, groups, units, routes, triggers, and mission metadata supported by the pinned library.
- Looking up static aircraft, vehicle, ship, weapon, country, and terrain definitions.
- Building reproducible mission fixtures for integration or HIL testing.

Check exact support and serialization behavior at the pinned revision. DCS mission format changes can outpace an older library release.

## Offline boundary

~~~text
Python and pydcs -> mission data -> saved .miz -> human loads mission in DCS
~~~

Saving a .miz file does not update an already running mission. Live observations and actions belong to Harness, DCS-gRPC, or mission-side Lua.

Generated mission changes should be written to an explicitly chosen workspace output. Do not overwrite user missions, DCS Saved Games content, or an active test fixture without clear authorization and a recoverable workflow.

## Source lookup

Start with:

- third_party/pydcs/dcs/mission.py
- third_party/pydcs/dcs/unitgroup.py
- third_party/pydcs/dcs/terrain
- third_party/pydcs/dcs/planes.py
- third_party/pydcs/dcs/vehicles.py
- third_party/pydcs/tests
- third_party/pydcs/README.md

Mission.load_file and Mission.save are central entry points in the pinned mission model. Inspect the concrete class and tests for required arguments, generated identifiers, archive handling, and persistence semantics.

## Design rules

- Separate pure mission construction from filesystem output.
- Make generated inputs and random seeds explicit when reproducibility matters.
- Validate output by loading it again and, for material changes, by controlled DCS testing.
- Do not treat static catalogs as proof that an asset is installed or usable in the player's DCS configuration.
- Do not couple live agent decisions to a stale offline mission model.

## Use another layer when

- The question concerns current mission state: use a typed live operation.
- The change must happen inside a running mission: use DCS Lua, MIST, or MOOSE through a bounded operation.
- Only Lua signature discovery is needed: use [dcs-lua-definitions](dcs-lua-definitions.md).
