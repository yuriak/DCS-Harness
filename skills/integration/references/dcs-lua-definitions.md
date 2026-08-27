# dcs-lua-definitions

## Role

dcs-lua-definitions is a community-maintained set of Lua Language Server definitions for DCS scripting APIs. It describes classes, enums, fields, and function signatures so editors and static tooling can provide completion, navigation, and diagnostics.

It is an authoring reference, not executable runtime code.

## Good fits

- Discovering likely DCS Lua object types and method names.
- Checking expected parameters and return shapes while writing Lua.
- Finding related enums and constants.
- Configuring LuaLS for mission scripting code.
- Narrowing which live behavior should be verified in a controlled probe.

## Limits

- Definitions can be incomplete, approximate, or behind the installed DCS version.
- A declared symbol may not exist in every scripting context.
- Static types cannot model object lifetime, mission reloads, sandbox restrictions, or all nil-return cases.
- Definitions do not load a library into DCS.
- Runtime behavior and the supported DCS version take precedence.

Use definitions to form a hypothesis, then verify version-sensitive or safety-relevant behavior against the actual environment.

## Context matters

DCS exposes different APIs in mission, hook/server, export, and other Lua environments. Ensure the definition library and symbol being consulted match the execution context. Harness Eval normally targets mission-side Lua; a server-hook or export-only API should not be assumed available there.

## Source lookup

Start with:

- third_party/dcs-lua-definitions/README.md
- third_party/dcs-lua-definitions/config.json
- third_party/dcs-lua-definitions/library/mission

Search class and function annotations under library/mission for mission-side work. Read nearby annotations and referenced types rather than relying only on a completion popup.

## Working method

1. Identify the Lua execution context.
2. Search the pinned definitions for the symbol and related types.
3. Check Harness and framework code for boundary assumptions.
4. If the behavior matters and remains uncertain, run a narrow read-only probe in the supported DCS version.
5. Record any observed version-specific difference near the integration code or test that depends on it.

## Relationship to other references

Use [DCS Lua](dcs-lua.md) for runtime and Eval design. Use the pinned MIST or MOOSE implementation for those frameworks; their behavior is not made authoritative by generic DCS definitions. Use [pydcs](pydcs.md) for offline Python mission authoring.
