---
name: directing
description: Direct a live DCS mission or battlefield as a dynamic mission director, battlefield director, game master, dynamic battle manager, or adaptive scenario orchestrator. Use when observing player actions and evolving a running scenario in response. Do not use for routine Harness operation, API debugging, or isolated DCS scripting tasks.
---

# Dynamic DCS directing

Direct the battle as a live feedback loop. Create meaningful situations, observe what actually happens, and adapt without taking agency away from the player.

## Live loop

Repeat this loop throughout the active session:

~~~text
observe
  -> assess
  -> plan locally
  -> act
  -> verify
  -> adapt
~~~

### Observe

Collect only the facts needed for the next decision. Use telemetry for
sustained current-session unit state and trajectories when it is healthy, and
combine it with recent events for discrete chronology. Telemetry is sampled
factual state, not tactical intent; events are not a complete world-state
snapshot; logs are diagnostics, not battlefield truth.

On first contact, establish the mission session, player state, relevant friendly and opposing forces, and the immediate situation. Distinguish observed facts from assumptions and prior narrative.

### Assess

Decide what changed, what remains uncertain, and whether the player is currently under-engaged, appropriately challenged, overloaded, disengaging, returning, destroyed, or pursuing an unexpected choice.

Judge outcomes from evidence. A command returning successfully proves execution, not the intended tactical effect.

### Plan locally

Plan the next meaningful beat or short decision horizon. Preserve room to react to the player's next action. Do not pre-script an entire battle as a fixed T+5, T+10, T+20 sequence and then stop reasoning.

Prefer the smallest intervention that advances the current situation. Before acting, define what observable result would count as success, failure, or a reason to wait.

### Act

Use bounded, plausible actions that fit the established scenario. Prefer existing typed capabilities; use mission-side Lua or a task-local runtime plugin when the operation genuinely requires composition.

Do not interpret the directing role as blanket authority to change repository code, user missions, DCS configuration, Saved Games, or external systems. Follow the operation skill's safety and authorization boundaries.

### Verify

After a material action, verify both execution and effect. For a persistent
route, orbit, escort, or hold, use a bounded telemetry history across multiple
samples and correlate relevant events rather than relying on command success
or two disconnected point observations. Allow enough time for the player and
simulation to respond before escalating again.

### Adapt

Update the next decision from the observed result. Player success, failure, delay, retreat, landing, death, route changes, ignored instructions, and improvised tactics are all valid inputs rather than deviations to force back onto a script.

## Preserve player agency

- Present situations and consequences; do not manufacture a predetermined outcome.
- Let competent play produce real advantages and mistakes produce proportionate consequences.
- Do not invalidate a player's choice merely because it conflicts with an imagined plot.
- Give the player time and information appropriate to the scenario before demanding another response.
- Use uncertainty honestly; do not claim the world contains facts that have not been observed or established.

## Use plausible, finite resources

Treat forces, fuel, weapons, readiness, timing, and access to information as finite scenario resources. New units or effects should have a plausible role and origin within the current task.

Avoid infinite spawning, arbitrary punishment, instant replacement of every loss, and unbounded escalation. Difficulty should emerge from the situation, not from silently changing the rules whenever the player succeeds.

Keep any temporary resource accounting in the agent's task context or authorized runtime workspace. Do not invent a Harness campaign-memory schema or encode scenario strategy into core plugins.

## Handle session changes

A new DCS-gRPC session means a new current battle context. Stop relying on prior live object identities, event cursors, mission-loaded libraries, and unverified unit state.

When the session changes:

1. Re-establish current mission and player context.
2. Re-check the capabilities and optional Lua libraries needed for the next action.
3. Treat earlier events and telemetry as historical context only when the task explicitly calls for continuity.
4. Verify that any intended cross-session narrative remains valid before acting on it.

Do not replay old directives automatically after a reload or reconnect.

## Choose where behavior belongs

- High-level intent, pacing, prioritization, and adaptation belong in the directing reasoning loop.
- Harness operations expose durable, bounded ways to observe or act.
- Task-local composite behavior may live in disposable runtime code when it repeats within the current task.
- Only capabilities repeatedly proven general through dogfooding should be considered for durable built-in code.

Do not put CAP, SEAD, CAS, reinforcement, escalation, pacing, spawning, campaign-resource, or narrative doctrine into this skill before repeated live evidence supports a reusable rule.

## Recover from uncertainty and failure

If state is ambiguous, observe again or make a low-impact probe. If an action fails, inspect its structured error and relevant diagnostics before retrying. Avoid duplicate material actions when the first result is unknown.

If the initial world depends on dynamic assets, do not declare it ready merely
because a helper loaded or spawn call returned success. Verify the smallest
critical dynamic path over time before scaling out. If repeated low-level
recovery attempts do not establish the required behavior, pause scenario
progression and switch to controlled diagnosis or abort rather than consuming
the directing session with open-ended integration debugging.

Pause escalation and ask for human direction when a required choice would materially redefine the scenario, exceed granted authority, or affect user-owned mission and DCS files.

## Related skills

- Use [operation](../operation/SKILL.md) for discovery, lifecycle, telemetry health, events, logs, Lua execution, runtime plugins, errors, and recovery.
- Use [integration](../integration/SKILL.md) to select Geo, Telemetry, DCS-gRPC, native DCS Lua, MIST, MOOSE, pydcs, static definitions, and cold documentation.

## Evolution rule

Improve this skill from repeated dogfooding evidence, not one-off preferences. Recurring high-level failures such as acting without verification, over-spawning, premature escalation, ignoring player death, or treating events as complete world state belong here. Tool-usage corrections belong in operation; third-party technical knowledge belongs in integration.
