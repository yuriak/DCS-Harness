# Mission Lua operations

The built-in **lua** plugin reaches the current mission Lua environment through
DCS-gRPC CustomService.Eval. It is the escape hatch for native DCS scripting
and mission-loaded Lua libraries that are not covered by typed RPCs.

## Commands

- **eval**: evaluate a non-empty Lua string.
- **eval-file**: read and evaluate an allowed repository-local .lua file.
- **load-file**: execute an allowed .lua file to load definitions or helpers;
  it currently shares the same evaluation path as eval-file.

Eval must be enabled in the active DCS-gRPC installation and represented as
enabled in the local environment configuration.

## Choose Lua deliberately

Use Lua when:

- typed DCS-gRPC coverage is absent;
- the native mission scripting environment is the authority;
- the mission has loaded MIST or MOOSE and their functions are required;
- a small diagnostic must inspect live mission-runtime behavior;
- task-local Lua is clearer and safer than generating a large inline string.

Prefer a typed gRPC method for covered, structured operations.

## JSON boundary

Return JSON-safe values: booleans, numbers, strings, nil, and tables that
serialize as simple arrays or objects. Do not assume userdata, functions,
metatables, DCS objects, or MOOSE objects can cross into Python. Keep such
objects in Lua and return only the facts or identifiers the Agent needs.

Execute the smallest useful expression and verify any side effect separately.
For substantial code, place a UTF-8 .lua file in an allowed runtime directory
instead of embedding a fragile command string.

## File boundary

eval-file and load-file accept regular UTF-8 .lua files only under:

- runtime/workspace/
- runtime/plugins/lua/

Resolved paths must remain within those roots. Traversal, symlink escape,
missing files, other extensions, and oversized input are rejected. Do not
broaden this boundary to arbitrary filesystem reads.

## Failures

Start with the structured Harness error:

- disabled or denied Eval is a capability-availability problem;
- recognized DCS-gRPC Lua load/runtime failures become
  LUA_EXECUTION_FAILED;
- malformed Eval JSON and other RPC failures retain their relevant structured
  classification.

Then inspect current dcs and grpc logs for the server-side context. Avoid
blindly repeating code that may have partially executed.

For exact native DCS, MIST, or MOOSE APIs, consult the integration knowledge
and pinned source rather than inventing a signature.

For exact current behavior, inspect:

- tools/src/py/plugins/lua.py
- tools/src/py/dcs_harness_runtime/lua_support.py
- third_party/dcs-grpc/protos/dcs/custom/v0/custom.proto
- mission-loaded library source under third_party/
