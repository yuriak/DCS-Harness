# Typed DCS-gRPC operations

The built-in **grpc** plugin discovers services and schemas from the generated
protobuf descriptor graph. It is a generic typed unary caller, not a
hand-maintained RPC catalog.

## Commands

- **services**: list descriptor-known services and their proto files.
- **describe**: describe a service or method, including input/output schemas.
- **call**: convert a JSON object to the described protobuf request, invoke one
  unary method, and normalize the protobuf response back to JSON.

Use this sequence:

~~~text
services
-> describe the selected service or method
-> construct the smallest valid JSON request
-> call
-> verify the result or effect
~~~

Do not guess field names, enum values, nested message shapes, or method
streaming behavior.

## Discovery examples

~~~bash
runtime/venv/bin/python tools/src/py/dcs_harness.py \
  --backend auto grpc services

runtime/venv/bin/python tools/src/py/dcs_harness.py \
  --backend auto --args-json '{"service":"metadata","method":"GetHealth"}' \
  grpc describe
~~~

The implementation accepts canonical service names and descriptor-derived
unambiguous aliases. Prefer discovery output over memorized names.

For a call, pass service, method, and a JSON-object request through
--args-json. Unknown request fields are rejected rather than silently
discarded.

## Boundaries

The generic caller supports unary request/response methods only. It rejects
client- or server-streaming methods before invocation. The resident events
plugin separately owns the long-lived MissionService.StreamEvents workflow.

Generated protobuf descriptors are the request-schema authority. DCS-gRPC is
not a mechanical one-to-one mirror of all native DCS Lua APIs; use the Lua
capability when typed service coverage is absent.

Transport availability, RPC errors, schema conversion failures, and unsupported
streaming return distinct structured Harness errors. Inspect the result before
retrying. For server-side behavior, correlate with the current grpc log source.

## Verification

For observations, compare important results with another live query when
practical. For actions, query the affected state or observe a resulting event;
do not treat a successful RPC response as sufficient evidence when the method
semantics require an external effect.

For exact current behavior, inspect:

- tools/src/py/plugins/grpc.py
- tools/src/py/dcs_harness_runtime/grpc_support.py
- runtime/generated/grpc/
- third_party/dcs-grpc/protos/
