# Runtime model

DCS-Harness has a transient direct runtime and a long-lived resident runtime.
Both use the same plugin resolver, dispatcher, context, result envelope, and
error model.

## Plugin lifetimes

A stateless plugin can run in either direct or resident mode. Direct mode owns
its context only for that invocation.

A resident plugin owns state and runtime-managed background tasks. It requires
the resident server. The current autostart resident plugins are **events** and
**logs**; they start once for the server lifetime and stop during graceful
shutdown.

Built-in plugins already started in a resident runtime are immutable until that
server restarts. Runtime plugins may be reloaded only under the current plugin
cache rules; never assume a running resident task was hot-swapped.

## Backends

- **direct** never probes the server and cannot run resident plugins.
- **server** requires the published loopback server state and never falls back.
- **auto** checks a published server for readiness and uses it when ready;
  otherwise it dispatches directly according to current implementation.

The server binds to loopback and publishes local state under
runtime/server.json. Treat that file as Harness-owned runtime state.

Readiness checks are deliberately short. Invocation has a separate transport
timeout because a valid gRPC or Lua operation can take longer than a health
probe.

## Three lifecycle scopes

~~~text
DCS process log epoch
-> logs

DCS-gRPC mission/session
-> events

future campaign continuity
-> memory
~~~

Events uses one SQLite ledger per numeric DCS-gRPC session. A mission reload,
new mission, or DCS restart that changes the session ID changes the current
ledger. Normal event commands do not merge historical sessions.

Logs uses timestamped raw mirrors for DCS and DCS-gRPC source-log epochs.
Source replacement, recreation, or truncation begins a new current mirror.
Normal log commands do not search older epochs.

runtime/memory/ is currently an empty placeholder reserved for future
campaign-level continuity across missions and sessions. Do not define a
schema, commands, retention policy, embedding system, or memory plugin yet.

## Directory ownership

Agent-owned:

- runtime/workspace/
- task-local files in runtime/plugins/py/
- task-local files in runtime/plugins/lua/

Harness-owned:

- runtime/events/
- runtime/logs/
- runtime/server.json
- generated bindings and setup artifacts

Inspect Harness-owned data through capabilities during normal tasks. Direct
database or mirror inspection is a Human/debug/research fallback, not the
normal Agent interface.

## Runtime status

Use plugin status commands to distinguish:

- resident process health;
- background task health;
- upstream DCS connectivity;
- last-known data that remains queryable after disconnect.

A running collector is not identical to a connected DCS stream. Preserve that
distinction in live decisions.

For exact current behavior, inspect:

- tools/src/py/dcs_harness.py
- tools/src/py/dcs_harness_runtime/resident.py
- tools/src/py/dcs_harness_runtime/server.py
- tools/src/py/dcs_harness_runtime/server_client.py
- tools/src/py/dcs_harness_runtime/plugin_api.py
