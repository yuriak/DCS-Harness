# Observation and verification semantics

Use this reference when deciding what evidence proves a current fact or the
effect of a live action.

## Evidence has different meanings

**[project convention]** Keep these surfaces distinct:

| Surface | What it establishes | What it does not establish |
| --- | --- | --- |
| typed RPC or focused Lua | a bounded current live observation or accepted action | complete world truth or sustained future effect |
| events | discrete factual chronology within one DCS-gRPC session | a complete current-world snapshot |
| telemetry | sampled factual unit state and trajectory within the current session | tactical intent or unsampled events |
| logs | process-epoch diagnostics and integration evidence | battlefield truth |
| Agent inference | an interpretation supported by evidence | a directly observed simulator fact |

Use live state and observed behavior first, then current Harness contracts,
pinned source, matching upstream documentation, community material, and model
memory. State uncertainty when the available surface does not establish the
answer.

## Verify effects over the right horizon

**[project convention]** Match verification to the claim:

- a one-shot lookup can be checked with one independent current observation;
- a continuing route, orbit, escort, or hold requires a time series;
- takeoff, shot, hit, landing, death, and respawn benefit from event
  correlation;
- Lua/framework failures require current process diagnostics;
- mission reload requires reacquiring the current session and assumptions.

When telemetry is healthy, use a bounded history query with a narrow unit,
instance, or group target, field selection, time window, and downsampling.
Do not approximate a trajectory with shell sleep and two disconnected
position calls.

**[known caveat]** Telemetry is sampled. A brief maneuver or lifecycle event
can occur between ticks. Events can fill some chronology gaps, but neither
surface is guaranteed to record every semantic fact an Agent might want.

## Interpret health before data

**[project convention]** Before relying on telemetry, inspect collector state,
current successful session, last sample time, capture duration, missed
samples, partial/failed counts, background task state, and persistence error.
A last-known snapshot can remain queryable while its collector is degraded or
failed; describe it as last-known, not necessarily current.

Event ledgers and telemetry databases are isolated by DCS-gRPC session. Logs
follow the DCS process epoch. A Harness restart is a different lifecycle and
does not by itself prove either upstream epoch changed.

## Readiness and recovery evidence

**[project convention]** Readiness must cover the critical path the task will
actually depend on. Importing a helper or receiving `ok=true` is insufficient
when the task requires an AI group to remain airborne and follow a route.

On unexpected behavior:

1. preserve the exact structured result;
2. inspect capability and background health;
3. inspect the relevant current events or diagnostics;
4. compare with current live state;
5. change one variable and retry once;
6. verify through an independent surface when practical.

Repeated failure is evidence to narrow the experiment or stop, not a reason
to make increasingly broad live changes.

## Sources to inspect

- `tools/src/py/plugins/telemetry.py`
- `tools/src/py/dcs_harness_runtime/telemetry_memory.py`
- `tools/src/py/plugins/events.py`
- `tools/src/py/plugins/logs.py`
- `skills/operation/references/events.md`
- `skills/operation/references/logs.md`
