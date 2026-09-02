# Cold source registry

Use this registry when focused Harness references do not answer a DCS,
DCS-gRPC, MIST, MOOSE, pydcs, or Lua API question. It is a source map, not a
vendored manual, search index, embedding database, or simulator truth store.

Revision values below describe the repository pins inspected on 2026-08-30.
Recheck `git submodule status` before relying on them after an update.

## Pinned local sources

| Name | Type | Upstream URL | Local path | Pinned revision/version | License status | Recommended use | Authority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DCS-gRPC rust-server | source, protobuf, Lua | `https://github.com/DCS-gRPC/rust-server` | `third_party/dcs-grpc/` | `9bb12cfb31bd9ccc364f38fa6d835ba6a371a969` (`0.8.1-13-g9bb12cf`) | AGPL-3.0 license file present | exact RPC schemas, Lua exporters, server behavior | pinned upstream source |
| MOOSE | mission Lua framework | `https://github.com/FlightControl-Master/MOOSE` | `third_party/moose/` | `8aa5e38ba2e7e43afa76d903ca4df7760e854f99` (`2.4.0-8743-g8aa5e38ba`) | GPL-3.0 license file present | exact classes, task construction, lifecycle behavior | pinned upstream source |
| MIST | mission Lua library | `https://github.com/mrSkortch/MissionScriptingTools` | `third_party/mist/` | `da53d53087677745443d5df9a7225eaff98438dc` (`4.5.126`) | GPL-3.0 license file present | exact helpers, databases, route and scheduling behavior | pinned upstream source |
| pydcs | offline Python mission authoring | `https://github.com/pydcs/dcs` | `third_party/pydcs/` | `e20f328390aecaac2a7f82444b4f5a96ac6bb2c3` (`v0.9.2-539-ge20f328`) | LGPL-3.0 license file present | `.miz` creation/loading/saving, static mission structure, groups/routes/tasks, triggers/actions/resources, supported weather/warehouse structures, and round-trip investigation | pinned upstream source |
| dcs-lua-definitions | static LuaLS definitions | `https://github.com/omltcat/dcs-lua-definitions` | `third_party/dcs-lua-definitions/` | `1bf47b673f1a063c5ef4d23ee4f9ebe7f948a923` | no license file found in pinned checkout; README describes Hoggit-derived content shared with permission | signature discovery only; verify against live runtime | pinned static reference, below observed behavior |

License labels summarize files in the pinned checkout and are not legal
advice. Reassess before copying, modifying, packaging, or redistributing
upstream content.

## Upstream and community documentation

| Name | Type | Upstream URL | Local path | Version | License status | Recommended use | Authority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MOOSE generated documentation | upstream official docs | `https://flightcontrol-master.github.io/MOOSE_DOCS/Documentation/index.html` | none | current site; may not match pin | redistribution not assessed | concepts and method discovery before checking pinned source | matching official docs when revision-compatible |
| MIST documentation | upstream/community wiki linked by pinned README | `http://wiki.hoggit.us/view/Mission_Scripting_Tools_Documentation` | none | legacy URL in pinned README | redistribution not assessed | helper discovery; confirm current location and pinned implementation | community/upstream docs below pinned source |
| pydcs documentation | upstream project docs | `http://dcs.readthedocs.org/en/latest` | none | current site; may not match pin | redistribution not assessed | authoring concepts before checking pinned code/tests | matching official docs when revision-compatible |
| DCS-gRPC project documentation | upstream project README/docs | `https://github.com/DCS-gRPC/rust-server` | `third_party/dcs-grpc/README.md` | pinned README plus current upstream | repository license applies to pinned copy; website redistribution not separately assessed | installation context and client concepts; use proto/source for exact contracts | matching official docs below Harness contracts/pinned source |
| Hoggit Simulator Scripting Engine Documentation | community wiki | `https://wiki.hoggitworld.com/view/Simulator_Scripting_Engine_Documentation` | `research/hoggit-sse-cache/pages/` when the optional ignored snapshot has been built | live community content; local retrieval/revision metadata is in `research/hoggit-sse-cache/manifest.json` | redistribution license not confirmed for this project | broad DCS Lua, mission-authoring, AI task, option, function, and event discovery | community reference; never simulator truth by itself |
| Eagle Dynamics documentation index | vendor documentation | `https://www.digitalcombatsimulator.com/en/downloads/documentation/` | none | current vendor site | redistribution not assessed | locate current vendor manuals and scripting material | vendor docs below supported live behavior/pinned contracts |

URLs in this table are discovery locations, not reachability guarantees. If a
legacy link has moved, search the named upstream project or official site and
record the replacement before changing this registry.

## Hoggit local-cache policy

**[project convention]** A developer may create a private or ignored local
cache for focused search, but the complete Hoggit wiki must not be committed
to the public repository until its redistribution terms and attribution
requirements have been established.

If a local cache is needed:

1. keep generated article content under the public working tree's ignored
   `research/hoggit-sse-cache/` directory so repository-root Agents can search
   it without reading private development material;
2. record source URLs, retrieval date, scope, and any observed license notice;
3. fetch only the material required for the current investigation when
   practical;
4. treat it as community documentation below current supported behavior,
   Harness contracts, and pinned source;
5. do not create a public sync script or redistribute the dump without a
   separate license and update-policy decision.

The cache is optional. If present, search `research/hoggit-sse-cache/pages/`
with bounded local text queries and inspect its manifest for provenance. Its
absence must not break Harness, its skills, or normal repository operation.

This policy must not block Geo, Telemetry, or focused integration work.
