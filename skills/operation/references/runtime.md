# Runtime model

DCS-Harness has a transient direct runtime and a long-lived resident runtime.
Both use the same plugin resolver, dispatcher, context, result envelope, and
error model.

## Plugin lifetimes

A stateless plugin can run in either direct or resident mode. Direct mode owns
its context only for that invocation.

A resident plugin owns state and runtime-managed background tasks. It requires
the resident server. The current autostart resident plugins are **events**,
**f10**, **logs**, and **telemetry**; they start once for the server lifetime
and stop during graceful shutdown. F10 autostart creates local state and
monitors the session/event ledger; it does not create a mission menu until an
explicit init operation.

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
-> events + telemetry + f10

Agent-selected task/campaign context
-> file memory
~~~

Events uses one SQLite ledger per numeric DCS-gRPC session. Telemetry rotates
current memory per session and can optionally use one SQLite archive per
session. A mission reload, new mission, or DCS restart that changes the
session ID changes all three current contexts. F10 discards local menu handles
and player-input state instead of replaying old interactions. Normal commands
do not merge historical sessions.

Logs uses timestamped raw mirrors for DCS and DCS-gRPC source-log epochs.
Source replacement, recreation, or truncation begins a new current mirror.
Normal log commands do not search older epochs.

runtime/memory/ is an Agent-owned persistent file workspace for selective
task/mission context and possible future campaign continuity. It is not a
Harness ledger or capability. See [memory](memory.md); do not define a built-in
schema, database, commands, retrieval API, automatic summarizer, retention
policy, embedding system, or memory plugin without new repeated evidence.

## Directory ownership

Agent-owned:

- runtime/workspace/
- task-local files in runtime/plugins/py/
- task-local files in runtime/plugins/lua/
- selective files in runtime/memory/

Harness-owned:

- runtime/events/
- runtime/telemetry/
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
