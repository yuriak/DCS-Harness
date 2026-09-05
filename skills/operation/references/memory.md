# Agent-owned file memory

Use this reference when a task or live mission needs selective context to
survive Agent turns, context compaction, a Harness restart, or a DCS mission
session change. `runtime/memory/` is an Agent-owned file workspace, not a
Harness service or source of current simulator truth.

## Appropriate contents

A lightweight `runtime/memory/MEMORY.md` may retain:

- current mission/session identity and operating mode;
- the short current plan;
- a small set of high-value confirmed facts;
- finite resource commitments and important confirmed/correlated losses;
- pending decisions and unresolved uncertainty;
- open or recently handled player communication;
- technical caveats that still affect this task.

The suggested headings are optional. Adapt or omit them rather than growing a
universal schema. Future campaign work may use task-chosen files below
`runtime/memory/campaign/`, but no campaign structure is defined yet.

## Update discipline

Update memory on a material state change: a new commitment, important loss,
major player decision, phase change, or newly relevant unresolved uncertainty.
Do not copy every telemetry sample or event, rewrite the file every few
seconds, or maintain a giant narrative history. Raw factual chronology remains
owned by events; sampled state remains owned by telemetry; diagnostics remain
owned by logs.

Use compact evidence labels when outcome strength matters:

~~~text
Confirmed: direct current observation or decisive event establishes the fact.
Correlated: multiple facts support the conclusion but do not establish it fully.
Unknown: important state remains unresolved.
~~~

Retain only enough source context—session, mission time/event ID, identity, or
observation note—to re-check a consequential item. Do not paste unbounded raw
payloads into memory.

## Truth and session changes

Use this authority order for current-world decisions:

~~~text
current live DCS observation
  > current-session telemetry/events
  > current Human-approved Mission Contract
  > Agent file memory
  > prior narrative
~~~

At a new DCS-gRPC session, do not discard useful history automatically, but
mark prior current-world facts historical/stale until reverified. Reacquire
physical identities and current player, force, library, and task state before
acting. Memory never authorizes replay of an old directive or commitment.

## Architecture boundary

Harness supplies the directory and ownership boundary only. Agents may read
and edit ordinary files there. Do not add a memory plugin, database, embedding
index, retrieval API, automatic summarizer, retention daemon, or campaign
engine without new repeated dogfooding evidence and a separate reviewed task.

