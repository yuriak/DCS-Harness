# Current-session factual events

The resident **events** plugin records factual DCS-gRPC stream events for the
current mission/session. It is a chronology, not a complete world-state
database, interpretation layer, or campaign memory.

## Commands

- **status**: report collector state, stream connectivity, current/last session,
  current ledger path, counts, errors, and background task status.
- **recent**: return a bounded newest-first list, optionally filtered by event
  type.
- **query**: apply fixed inclusive mission-time bounds, event-type filtering,
  and a bounded limit.

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
and continues appending.

## Connectivity matters

Check status.stream:

- **connected**: the collector currently consumes the live stream.
- **connecting** or **disconnected**: the last-known ledger may remain
  queryable, but it is not proof of a live battle.

Combine last-known event history with typed gRPC or focused Lua observation
before making time-sensitive decisions.

## Data semantics

simulation_fps is filtered before persistence and increments the ignored_events
status counter. Other payloads are normalized protobuf event messages.
Individual event payloads may omit facts an Agent expects, use scenery rather
than mission names, or describe a transition without current state.

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
