# Task-local runtime extensions

Runtime code lets an Agent reuse task-local operations without turning every
mission need into durable Harness infrastructure.

## Maturity ladder

~~~text
one-off action
-> direct grpc or lua invocation

repeated task-local sequence
-> code in runtime/workspace/

repeated reusable task-local capability
-> runtime plugin

repeated across multiple dogfood tasks
-> candidate for a durable built-in
~~~

Keep strategic, scenario-specific, pacing, and narrative decisions in Agent
reasoning or task-local code. Do not embed them in the core merely because one
mission benefits from composition.

## Python plugin location and contract

Place task-local Python plugins directly under:

~~~text
runtime/plugins/py/<name>.py
~~~

Plugin names use the current resolver lowercase identifier convention. A
runtime plugin cannot silently override a built-in with the same name.

The minimal current contract is:

- PLUGIN_NAME matching the filename;
- PLUGIN_API_VERSION matching the supported plugin API;
- callable invoke(context, command, args);
- optional callable describe().
- optional callable fast_report(context, runtime).

`fast_report` returns a small JSON-safe mapping for the explicit aggregated
status path. It must be bounded and side-effect free apart from a small number
of short observations. Do not return histories, unit dumps, descriptor lists,
or raw log text. The `runtime` argument is a plugin handle only for a running
resident plugin and `None` for a stateless plugin. The aggregator does not call
or start a resident plugin that is not running.

Do not copy a contract from this summary when precision matters. Inspect
tools/src/py/dcs_harness_runtime/plugin_api.py and a small current built-in.

Use the built-in plugins validate command on the exact name or allowed path
before depending on a new plugin. Use plugins describe to confirm its current
surface and whether it exposes a fast report.

## Lifetime choice

Default task-local plugins to stateless. Resident plugins add owned state,
start/stop hooks, and runtime-managed background tasks; they require the
resident server and materially increase lifecycle risk.

Only choose resident behavior when the task genuinely needs state or a
long-lived background activity. Use the runtime task manager rather than
unmanaged threads. Already-started resident plugins are not hot-swapped.

## Workspace and Lua helpers

Use runtime/workspace/ for one-off scripts, generated inputs, intermediate
artifacts, and task reports. Use runtime/plugins/lua/ for reusable Lua files
that the lua capability may load.

Keep outputs bounded, JSON-safe, and suitable for the canonical result
envelope. Lifecycle logs intentionally record metadata and outcomes rather
than full plugin payloads.

## Promotion rule

Promote a runtime capability to tools/src/py/plugins/ only after repeated
dogfooding demonstrates a generic, stable need. Promotion requires deliberate
review, tests, and a separate core change; it is not part of ordinary live
mission work.

For exact current behavior, inspect:

- tools/src/py/dcs_harness_runtime/plugin_api.py
- tools/src/py/dcs_harness_runtime/resident.py
- tools/src/py/plugins/plugins.py
- small built-ins under tools/src/py/plugins/
