# Third-party known issues

Consult this registry after the relevant focused reference and before broad
source archaeology. Entries separate current project observations from upstream
or community reports. They are warnings and bounded workarounds, not universal
claims about every DCS release or environment.

## DCS core events

### `S_EVENT_KILL` can be incomplete or inconsistent

- **Component:** DCS mission event system.
- **Symptom:** `hit` and terminal loss/crash chronology exists, but no matching
  `kill` event is emitted; other reported versions have also duplicated a kill
  when wreckage later hit the ground.
- **Affected context:** Community reports cover DCS 2.9-era single-player and
  multiplayer behavior. Exact behavior remains version- and event-dependent.
- **Confidence:** High that `kill` must not be treated as complete; low for any
  universal per-aircraft rule.
- **Recommended workaround:** Preserve `hit`, `unit_lost`, `dead`, `crash`, and
  ejection chronology. Report `confirmed_by_kill_event`,
  `correlated_hit_then_loss`, and `unattributed_loss` separately.
- **Do not do:** Infer that no combat kill occurred solely because `kill` is
  absent, or convert current absence into a confirmed kill.
- **Evidence:** [Inconsistent kill-event report](https://forum.dcs.world/topic/355395-kill-event-does-not-consistently-activate/), [DCS 2.9.8 PvP missing-kill report](https://forum.dcs.world/topic/358356-2981107-no-kill_event-pvp-multiplayer/).
- **Last verified:** Registry review 2026-09-04; a controlled local HIL remains pending.

### Event objects can be nil, partial, or no longer valid

- **Component:** DCS mission event payloads.
- **Symptom:** `weapon`, `initiator`, or `target` is absent, or an object method
  fails after the object has ceased to exist. A dedicated-server report found
  nil weapon values for some `S_EVENT_KILL`/`HIT` cases.
- **Affected context:** Event type, weapon, lifecycle timing, and server mode can
  all matter.
- **Confidence:** High as a defensive extraction requirement; individual
  community examples are not a complete compatibility matrix.
- **Recommended workaround:** Nil-check, use `Object.isExist`/`Unit.isExist`
  where meaningful, protect object access with `pcall`, and emit unavailable
  primitive fields as null without discarding the entire event.
- **Do not do:** Call object methods unguarded or require weapon identity before
  retaining the rest of a combat event.
- **Evidence:** [Nil weapon in `S_EVENT_KILL` report](https://forum.dcs.world/topic/313702-weapon-object-is-nil-during-event-id-28-s_event_kill/).
- **Last verified:** Registry review 2026-09-04.

### Group names do not prove one continuous physical object

- **Component:** DCS group/unit lifecycle.
- **Symptom:** A group definition may be inactive, destroyed, absent from active
  observations, or later recreated under the same name with different runtime
  IDs. Framework wrappers can become ready later than native DCS objects.
- **Affected context:** Late activation, destruction/recreation, session reload,
  and dynamically added groups.
- **Confidence:** High for current Harness identity/session handling; exact DCS
  lookup semantics remain context-sensitive.
- **Recommended workaround:** Reacquire by name after session changes and verify
  active state plus current physical IDs. Treat same-name generations as
  distinct identities.
- **Do not do:** Treat a mission definition, cached object, or stable name as
  proof of current existence or continuity.
- **Evidence:** [World entities](world-entities.md), [dynamic aircraft and tasking](dynamic-aircraft-and-tasking.md).
- **Last verified:** DCS 2.9.29.27278 MT HIL, 2026-08-31.

## DCS-gRPC

### Shot events can be dropped when weapon ID is absent

- **Component:** DCS-gRPC `MissionService.StreamEvents` serialization.
- **Symptom:** DCS-gRPC logs `failed to deserialize event: ... missing field
  id`; the corresponding event does not reach the client stream.
- **Affected context:** Upstream issue #258 and the pinned revision
  `9bb12cfb31bd9ccc364f38fa6d835ba6a371a969`. Its weapon exporter still emits
  `id = weapon.id_`, which can be nil.
- **Confidence:** Confirmed in local dogfooding evidence: 301 matching errors
  across three DCS-gRPC sessions, including 255 during the final directing
  session.
- **Recommended workaround:** Keep StreamEvents for broad chronology, but use a
  bounded native combat observer as redundant factual acquisition. Preserve
  source provenance and deduplicate deterministically.
- **Do not do:** Depend on StreamEvents alone for complete shot/hit/kill
  chronology, or substitute a fake weapon ID and present it as factual.
- **Evidence:** [DCS-gRPC issue #258](https://github.com/DCS-gRPC/rust-server/issues/258), pinned `third_party/dcs-grpc/lua/DCS-gRPC/exporters/object.lua`.
- **Last verified:** Archived DCS 2.9.29.27278 MT dogfooding logs reviewed 2026-09-04.

### `GroupService` lookup disagreed with direct mission Lua

- **Component:** DCS-gRPC `GroupService.Activate` and `GetUnits`.
- **Symptom:** Six calls returned `NOT_FOUND: group does not exist` for exact
  names that direct mission Lua and live telemetry could observe. This affected
  dormant `EAGLE 31` and active `RIVET 71`, `LONG BOW 1`, and `VITYAZ 1`.
- **Affected context:** Local DCS 2.9.29.27278 MT dogfooding, DCS-gRPC revision
  `9bb12cfb31bd9ccc364f38fa6d835ba6a371a969`, one current session. Both
  `group_name` and `groupName` requests failed for `EAGLE 31`.
- **Confidence:** High for the observed discrepancy; root cause and wider scope
  are unknown pending focused HIL.
- **Recommended workaround:** Preserve the structured typed failure, confirm
  the same exact name with one focused Lua lookup, and use a bounded Lua
  fallback only when the task requires it. Record fallback provenance.
- **Do not do:** Generalize that late-activation groups are unfindable, that all
  GroupService methods are broken, or patch the player's DCS-gRPC installation.
- **Evidence:** Pinned `third_party/dcs-grpc/lua/DCS-gRPC/methods/group.lua` uses
  `Group.getByName(params.groupName)` for both methods; local archive audit dated
  2026-09-04. No matching upstream root cause has been established.
- **Last verified:** DCS 2.9.29.27468 HIL on 2026-09-05 successfully queried
  active and inactive late groups, activated the late group, and created/removed
  group menus before and after activation. The earlier scenario discrepancy was
  not reproduced; its cause remains unresolved, and immediate post-spawn timing
  is not established by the delayed post-activation checks.

## pydcs and Mission Editor

### Added ground units need explicit positions

- **Component:** pydcs multi-unit ground-group authoring.
- **Symptom:** Units appended without assigned positions survived save/reload at
  local `(0,0)` while only the lead unit retained the intended battery anchor.
- **Affected context:** Pinned pydcs
  `e20f328390aecaac2a7f82444b4f5a96ac6bb2c3`, second Exercise Fracture
  dogfooding authorer.
- **Confidence:** Confirmed by read-only pydcs reload and live telemetry.
- **Recommended workaround:** Assign every unit an explicit finite position and
  validate origin, anchor distance, formation extent, and role-appropriate
  separation.
- **Do not do:** Assume `group.add_unit(...)` derives a useful formation from
  the lead position, or validate composition/count alone.
- **Evidence:** Local dogfooding archive review, 2026-09-03.
- **Last verified:** 2026-09-03.

### Route shape does not establish persistence

- **Component:** pydcs-authored aircraft routes and DCS AI task semantics.
- **Symptom:** A multi-point or geometrically closed-looking route is accepted,
  then the aircraft finishes it and returns rather than patrolling indefinitely.
- **Affected context:** Task type, route points, aircraft, and DCS version all
  matter.
- **Confidence:** Confirmed for the authored fighter routes in the second
  dogfooding; not a universal failure of multi-point routes.
- **Recommended workaround:** Author an explicit persistent task recipe and
  verify it over a bounded multi-sample telemetry history with events/logs.
- **Do not do:** Treat point count, save/load, or command acceptance as proof of
  sustained behavior.
- **Evidence:** [Dynamic aircraft and tasking](dynamic-aircraft-and-tasking.md), [`.miz` and mission authoring](miz-and-mission-authoring.md).
- **Last verified:** DCS 2.9.29.27278 MT dogfooding, 2026-09-03.

### Payload CLSIDs are aircraft- and pylon-specific

- **Component:** pydcs aircraft/loadout definitions.
- **Symptom:** A known store or CLSID is not necessarily valid on an arbitrary
  aircraft pylon; discoverability currently requires inspecting generated
  aircraft and weapon definitions.
- **Affected context:** Pinned pydcs and optional local DCS payload presets.
- **Confidence:** High from the pinned type model; a generated Harness catalog
  is planned but not yet available.
- **Recommended workaround:** Check the pinned aircraft pylon compatibility and
  validate the intended pylon/CLSID pair before saving. Reload the candidate and
  Human-approved final.
- **Do not do:** Guess a CLSID or infer compatibility from store name alone.
- **Evidence:** [pydcs](pydcs.md), pinned `third_party/pydcs/dcs/planes.py` and
  `third_party/pydcs/dcs/weapons_data.py`.
- **Last verified:** Pinned source review 2026-09-04.

### Mission Editor round-trip is not fully symmetric

- **Component:** pydcs `.miz` serialization and DCS Mission Editor rewrite.
- **Symptom:** Mission Editor can normalize archive structure or rewrite
  serialized trigger expressions while pydcs still reloads the high-level
  object successfully.
- **Affected context:** Version-sensitive mission fields, triggers, resources,
  payloads, and tasks.
- **Confidence:** Confirmed for the current candidate/Human/final workflow.
- **Recommended workaround:** Preserve separate candidate and Human-approved
  final files; reload the final read-only, inspect material compiled structures,
  and perform a live preflight for behavior.
- **Do not do:** Rewrite the Human final from the candidate or equate a pydcs
  round-trip with DCS runtime acceptance.
- **Evidence:** [`.miz` and mission authoring](miz-and-mission-authoring.md), [pydcs](pydcs.md).
- **Last verified:** DCS 2.9.29.27278 MT HIL, 2026-09-01.

### Translatable inline `DoScript` failed after Mission Editor save

- **Component:** pydcs `DoScript` plus Mission Editor compiled trigger output.
- **Symptom:** The dictionary retained the intended Lua, but Mission Editor
  rewrote the compiled action to execute the literal translation key; DCS
  reported a Lua syntax error and the startup marker was absent.
- **Affected context:** The tested pinned pydcs and DCS 2.9.29.27278 MT path.
- **Confidence:** Confirmed for that exact path; not generalized to every inline
  script arrangement.
- **Recommended workaround:** Use the verified resource-backed `DoScriptFile`
  pattern for critical startup helpers; inspect compiled action and verify the
  live symbol.
- **Do not do:** Accept translated dictionary content alone as proof that the
  compiled trigger executes it.
- **Evidence:** [pydcs](pydcs.md), [`.miz` and mission authoring](miz-and-mission-authoring.md).
- **Last verified:** Corrected HIL 2026-09-01.

## MOOSE

### Do not use a global named `MOOSE` as the availability check

- **Component:** MOOSE mission runtime.
- **Symptom:** A mission can have the required MOOSE classes loaded without a
  meaningful global named `MOOSE`.
- **Affected context:** Pinned MOOSE build and mission loading arrangement.
- **Confidence:** Confirmed in current HIL workflow.
- **Recommended workaround:** Probe the exact symbol needed, such as `BASE`,
  `GROUP`, `SPAWN`, or `_DATABASE`, after each mission/session change.
- **Do not do:** Declare MOOSE absent solely because `_G.MOOSE` is nil.
- **Evidence:** [MOOSE](moose.md), [`.miz` and mission authoring](miz-and-mission-authoring.md).
- **Last verified:** DCS 2.9.29.27278 MT HIL, 2026-09-01.

### Native group existence can precede MOOSE wrapper readiness

- **Component:** MOOSE dynamic group registration.
- **Symptom:** `coalition.addGroup` succeeds and native lookup finds the group,
  while same-call `GROUP:FindByName` is not ready until a later mission frame.
- **Affected context:** Dynamically added groups in the tested MOOSE build.
- **Confidence:** Confirmed in one bounded HIL; delay length is not universal.
- **Recommended workaround:** Separate native existence from wrapper readiness
  and retry only the wrapper-bound step once after a bounded delay.
- **Do not do:** Spawn a duplicate group because the first MOOSE lookup missed.
- **Evidence:** [Dynamic aircraft and tasking](dynamic-aircraft-and-tasking.md).
- **Last verified:** DCS 2.9.29.27278 MT HIL, 2026-08-31.

### Race-track orbit requires a second coordinate

- **Component:** Pinned MOOSE task construction.
- **Symptom:** Selecting the race-track pattern without the second point does
  not construct the intended two-leg task.
- **Affected context:** Current pinned MOOSE `TaskOrbit` behavior.
- **Confidence:** High from pinned source and current reference review.
- **Recommended workaround:** Supply and validate both endpoints, then verify
  sustained behavior with telemetry history.
- **Do not do:** Change only a pattern label or infer persistence from task
  acceptance.
- **Evidence:** [Dynamic aircraft and tasking](dynamic-aircraft-and-tasking.md).
- **Last verified:** Pinned source/reference review 2026-09-04.
