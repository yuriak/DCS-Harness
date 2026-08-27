# Current-process DCS diagnostics

The resident **logs** plugin follows raw DCS log sources into byte-preserving
repository-local mirrors. Logs explain implementation and server behavior;
they are diagnostics, not authoritative battlefield state.

## Sources and commands

Current source names:

- **dcs**: the configured DCS log.
- **grpc**: Saved Games Logs/gRPC.log.

Commands:

- **status**: show collector and per-source state, paths, current mirror,
  offset, update time, and errors.
- **tail**: return a bounded number of decoded lines from one current mirror.
- **search**: return bounded, case-sensitive substring matches from one current
  mirror.

Discover current argument bounds from the plugin description or source rather
than copying old values into task logic.

## Process-log epochs

When a source appears, is replaced, is recreated with a new identity, or
shrinks below the followed offset, the follower begins a new timestamped
mirror. Older mirrors remain on disk.

Normal tail and search query only the current epoch. They intentionally do not
mix prior DCS process diagnostics into current context.

On Harness restart, the newest mirror is reused only if it is an exact byte
prefix of the source. Following then resumes at that offset without duplicating
bytes or inventing a DCS process boundary.

If a source is missing or temporarily unreadable, the plugin reports that
source state without failing the entire resident runtime. No current mirror
means tail and search cannot serve that source.

## Diagnostic workflow

Use logs after preserving the exact structured Harness failure:

1. Check logs status.
2. Select dcs for mission scripting/native Lua context or grpc for DCS-gRPC
   server behavior.
3. Search for a unique marker or specific error substring.
4. Tail nearby current lines if more context is required.
5. Correlate timestamps and source epoch with the active DCS process.

Lua, MIST, and MOOSE failures may surface in DCS logs; request and server
details may surface in gRPC logs. Absence from a log is not proof that a
battlefield action did or did not occur.

Do not modify mirrors during normal operation. Historical mirror inspection is
a Human/debug/research fallback outside the current-only capability contract.

For exact current behavior, inspect:

- tools/src/py/plugins/logs.py
- tools/src/py/dcs_harness_runtime/log_collector.py
- config/environment.yaml for current technical source paths
