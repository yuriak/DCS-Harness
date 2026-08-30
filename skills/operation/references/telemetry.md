# Current-session factual telemetry

The resident **telemetry** plugin samples bounded factual unit state into
current-session memory. Optional SQLite persistence creates a postmortem
archive; normal Agent queries still address only the current in-memory battle
context.

## Check health before relying on samples

Use **status** first when the freshness or continuity of data matters. Read
these fields together:

- `collector`: `starting`, `running`, `degraded`, `failed`, or `stopped`;
- `background_task`: runtime-owned task state and any uncaught error;
- `session_id`: session of the last fully successful capture cycle;
- `last_successful_sample` and `last_capture_duration_ms`;
- `latest_unit_count`, `snapshots_in_memory`, and `samples_in_memory`;
- `partial_captures`, `failed_captures`, and `consecutive_failures`;
- `late_missed_samples` for capture work that overran scheduled ticks;
- `persistence_enabled`, `store_path`, `persisted_count`, and `last_error`.

A `running` task is not by itself proof that a recent sample exists. A
`degraded` collector can retain useful last-known memory while a DCS/gRPC/Eval
failure is being retried. Describe that data as last-known when its timestamp
or session cannot establish current freshness.

## Query the smallest useful slice

- **latest** returns a filtered view of the newest snapshot.
- **list** returns lightweight identities from the newest snapshot.
- **snapshot** selects a current-memory snapshot by ID or nearest mission
  time and reports the actual selected metadata.
- **history** returns a bounded time series for exactly one unit, physical
  `instance_id`, or group.

Use field selection, time windows, downsampling, and a small limit. Inspect
`requested_count`, `returned_count`, and `truncated`; narrow the query when the
result is truncated. Discover current arguments and bounds from the plugin
description rather than copying an old command example.

Unit-name history can include multiple physical generations. Prefer
`instance_id` when continuity of one physical object matters. Group history
can include several units per snapshot.

## Session and persistence semantics

A new DCS-gRPC session immediately becomes a new telemetry context. Current
memory is rotated and ordinary queries do not expose prior sessions. A
Harness restart in the same DCS session can resume persisted snapshot and
identity counters, but Harness process lifetime is not the session boundary.

With persistence enabled, databases live under `runtime/telemetry/`, one file
per numeric DCS-gRPC session. They are Harness-owned postmortem artifacts:
normal queries do not accept raw SQL or an arbitrary historical session ID.
Do not edit them during normal work.

Transient DCS, gRPC, mission-reload, Eval, or malformed-capture failures
degrade the collector and use bounded retry without creating a fake empty
snapshot. An unrecoverable SQLite/filesystem or internal invariant failure
marks the telemetry collector and its background task failed; other Harness
capabilities continue running.

## Correlate evidence

Telemetry is sampled state, not intent. Use it to establish positions,
movement, altitude, speed, life values, and trajectories that were observed at
ticks. Correlate with:

- **events** for discrete takeoff, shot, hit, runway, landing, death, and
  respawn chronology;
- **logs** for Lua, framework, DCS, or DCS-gRPC diagnostics;
- typed RPC or focused Lua for a current fact absent from the snapshot.

Do not replace a telemetry history query with shell sleep followed by two
unrelated position calls. Do not infer a controller's tactical intent solely
from a sampled path.

## Diagnostic sequence

1. Preserve the failed query or surprising sample.
2. Inspect telemetry status and background-task state.
3. Confirm the last successful session and sample freshness.
4. Check partial/failure/missed counters and the structured last error.
5. Use events or current typed/Lua observation to determine whether the world
   changed or only collection failed.
6. Inspect current logs for transport, Eval, or mission-side detail.
7. Correct one variable, then verify with a new successful sample.

For exact current behavior, inspect:

- `tools/src/py/plugins/telemetry.py`
- `tools/src/py/dcs_harness_runtime/telemetry_collector.py`
- `tools/src/py/dcs_harness_runtime/telemetry_memory.py`
- `tools/src/py/dcs_harness_runtime/telemetry_store.py`
