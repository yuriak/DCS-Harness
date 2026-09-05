# Current-session factual events

The resident **events** plugin records factual chronology for the current
mission/session. Broad coverage comes from DCS-gRPC StreamEvents; a fixed
Harness-owned native Lua handler redundantly captures only combat-critical
events through a bounded ring and periodic batch Eval. It is not a complete
world-state database, interpretation layer, or campaign memory.

## Commands

- **status**: report collector state, stream connectivity, current/last session,
  current ledger path, counts, errors, and background task status.
- **recent**: return a bounded newest-first list, optionally filtered by event
  type.
- **query**: apply fixed inclusive mission-time bounds, event-type filtering,
  identity/coalition/source filters, `after_id`, and a bounded limit.
- **combat**: return one compact combat chronology with explicit provenance
  and conservative attribution labels.

The normal Agent interface does not accept a session selector or raw SQL.

## Session model

Each numeric DCS-gRPC session has its own SQLite ledger under runtime/events/.
A session change selects or creates a different ledger. Mission reload, a new
mission, or DCS restart normally changes the session, but use the observed
session ID rather than assuming a boundary.

Normal recent and query calls read only the current or last-known session
ledger. Historical ledgers remain on disk for Human research and debugging but
are not automatically mixed into current battle context.

Harness restart during the same DCS-gRPC session reopens the matching ledger
and continues appending. The native observer ring can bridge a short Harness
restart while the same mission remains loaded, subject to its fixed capacity.

## Connectivity matters

Check status.stream:

- **connected**: the collector currently consumes the live stream.
- **connecting** or **disconnected**: the last-known ledger may remain
  queryable, but it is not proof of a live battle.

Combine last-known event history with typed gRPC or focused Lua observation
before making time-sensitive decisions.

Also check `status.native_combat`:

- `installed=true` with collector `running` means the fixed native handler is
  being polled;
- queue gaps or overwrites mean native chronology may be incomplete;
- unavailable Eval disables this redundant path without deleting the existing
  StreamEvents path.

## Data semantics

simulation_fps is filtered before persistence and increments the ignored_events
status counter. Other payloads are normalized protobuf event messages.
Individual event payloads may omit facts an Agent expects, use scenery rather
than mission names, or describe a transition without current state.

The resident F10 capability consumes only `mission_command`,
`coalition_command`, and `group_command` rows whose structured details match a
command registered by that current Harness process. Other clients' and
mission-owned command events remain factual ledger rows but are not presented
as Harness player selections.

Every returned row exposes `source` and `sources`. A matching gRPC/native pair
is stored once with both sources. Deduplication uses event type, a narrow
mission-time tolerance, entity identity, and weapon type/name; it is factual
normalization, not kill inference. Missing weapon IDs remain null and do not
discard the rest of a native event.

The compact combat view labels a `kill` event with identifiable initiator and
target as `confirmed_by_kill_event`. An incomplete kill payload remains
unattributed. A bounded preceding hit with identifiable attacker followed by a
matching loss may be `correlated_hit_then_loss`, which is explicitly not a
confirmed kill. Losses without that evidence are `unattributed_loss`.
Correlation processes mission-time order, with hit/kill evidence before losses
at the same time, regardless of source arrival order. Ejection cross-source
matching tolerates static/unknown parachute representation while preserving
different parachute names. Same-source repetitions remain factual rows.
Telemetry disappearance or a missing group must never be promoted into combat
attribution.

Use events for questions such as “what happened recently?” or “did this event
type occur?” Use live queries for “what exists now?” or “what is its current
state?”

Do not edit or routinely query SQLite directly. The bounded capability API is
the normal interface and preserves current-session isolation.

For exact current behavior, inspect:

- tools/src/py/plugins/events.py
- tools/src/py/dcs_harness_runtime/event_collector.py
- tools/src/py/dcs_harness_runtime/event_store.py
- runtime/generated/grpc/dcs_grpc/dcs/mission/v0/
