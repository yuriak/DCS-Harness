# DCS mission-side Lua

## Role

Mission-side Lua is the live scripting environment provided by DCS. In this project it is normally reached through DCS-gRPC CustomService.Eval, directly or behind a bounded Harness operation.

Use native DCS Lua for simple facts and actions that are unavailable through an existing typed operation. Use an optional framework only when its abstractions materially simplify the mission behavior.

## Eval boundary

Treat an Eval snippet as a small adapter:

1. Validate its inputs before constructing or invoking Lua.
2. Perform one coherent query or action.
3. Convert the result to scalars, arrays, and plain acyclic tables.
4. Return explicit success or error facts.

Do not return DCS userdata, functions, metatables, MOOSE objects, or cyclic tables. Avoid retaining hidden state between unrelated calls unless the operation contract explicitly owns that state.

## Runtime caveats

- Available globals depend on the DCS scripting context.
- Mission state, pause state, reloads, and object lifetime affect results.
- Objects can disappear between lookup and use.
- API behavior can vary across DCS releases.
- Mission scripting sandbox policy can restrict standard Lua facilities.
- A Lua file on disk is not loaded merely because Harness can see it.

Handle missing objects and missing globals as ordinary runtime conditions and report them precisely.

## Choosing native Lua, MIST, or MOOSE

- Native Lua: a direct DCS API call or small transformation is enough.
- MIST: table-oriented utility, database, scheduling, routing, or geometry help is needed.
- MOOSE: complex stateful orchestration benefits from its object model and tasking abstractions.

Do not introduce a framework dependency for a trivial typed operation.

## Source lookup

There is no vendored copy of the DCS executable's runtime implementation. Use:

- the supported DCS version observed during HIL testing;
- repository Harness Lua and Eval adapters under tools/src and runtime;
- [dcs-lua-definitions](dcs-lua-definitions.md) for static discovery;
- pinned MIST or MOOSE source when those libraries are involved.

Static definitions are hints, not evidence that a function exists in the current scripting context. When uncertainty matters, use a narrow read-only probe in a controlled mission.

## Design rules

- Prefer idempotent reads and explicitly named actions.
- Keep string interpolation out of Lua where structured argument encoding is possible.
- Bound execution time and result size at the Harness layer.
- Normalize coordinates, coalition identifiers, units, and absent values at a documented boundary.
- Include enough error context to distinguish transport failure, Lua error, missing library, and missing mission object.
