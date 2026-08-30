# Coordinates and units

Use this reference when a task crosses geographic coordinates, DCS local
coordinates, mission routes, headings, distances, altitudes, or speeds.

## Coordinate shapes

**[stable API fact]** A DCS world position is a three-dimensional vector. In
Harness contracts it is written explicitly as `x_m`, `y_m`, and `z_m`; `y_m`
is elevation, while `x_m` and `z_m` form the horizontal map-local plane.

**[stable API fact]** Mission routes and many DCS task tables use a two-axis
point whose fields are named `x` and `y`. In that shape, route `y` represents
the second horizontal world axis, world `z`; it is not world elevation. Pinned
MOOSE code performs the same `Vec3 x/z -> Vec2 x/y` mapping when it constructs
orbit points.

**[project convention]** Never pass an unlabeled `x/y` pair across a Harness
boundary. Use geographic `latitude_deg/longitude_deg`, world
`x_m/y_m/z_m`, or a clearly identified route point.

## Geographic and local frames

**[project convention]** Use `geo` for maintained calculations and reference
locations:

- catalog search and lookup for static named reference points;
- nearest, distance, bearing, and offset for geographic calculations;
- live geographic-to-local or local-to-geographic conversion when the current
  mission is available.

Do not recreate an approximate theatre projection in task-local code when
`geo` already covers the operation. Inspect the current plugin description
before invoking it; do not assume a remembered argument schema.

**[verified current-version behavior]** Caucasus HIL confirmed the live
LL-to-local-to-LL bridge against the supported DCS runtime with negligible
round-trip horizontal error at Gudauta. The result is session- and
theatre-bound and does not establish the same measurement for every map or
DCS release.

**[known caveat]** A geographic initial true bearing and a bearing measured in
the DCS local grid can differ because the theatre projection rotates or
distorts the local axes. Keep the reference in the field name or surrounding
metadata; do not silently compare grid heading with true bearing.

## Canonical units

**[project convention]** Durable Harness capability boundaries use:

| Quantity | Canonical unit |
| --- | --- |
| horizontal distance | metre |
| elevation/altitude | metre |
| speed | metre/second |
| geographic angle | degree |
| heading/bearing | degree with an explicit reference |

Use the `geo` unit conversion operation for supported distance and speed
conversions instead of scattering constants through runtime scripts.

**[known caveat]** Framework methods can document different altitude
references. For example, pinned MOOSE has one circle helper that accepts AGL
and adds terrain height, while its general orbit helper accepts an altitude
placed directly in the DCS task. Inspect the exact pinned method rather than
assuming that every parameter named `altitude` has the same datum.

## Telemetry positions

**[project convention]** Telemetry retains factual DCS local `x/y/z` as its
canonical sampled position. Latitude and longitude can remain null. Convert
only selected points when a geographic answer is needed; do not add one live
coordinate conversion per unit per sampling tick.

Telemetry `heading_deg` is derived in the documented local grid. Treat it as
local-grid heading, not magnetic heading or guaranteed true heading.

## Sources to inspect

- `tools/src/py/plugins/geo.py`
- `tools/src/py/dcs_harness_runtime/geo_math.py`
- `tools/src/py/dcs_harness_runtime/geo_live.py`
- `tools/src/py/dcs_harness_runtime/telemetry_capture.py`
- `third_party/dcs-lua-definitions/library/mission/coord.lua`
- `third_party/dcs-lua-definitions/library/mission/object.lua`
- `third_party/moose/Moose Development/Moose/Wrapper/Controllable.lua`
