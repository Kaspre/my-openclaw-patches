# Deferred scope from PR #50818 — 2026-04-26

One concern pulled out of #50818 to slim the PR per clawsweeper's 2026-04-26
review (issuecomment-4322767117). Park here so it can be revived when the
right home is ready (or abandoned if the project never accepts the behavior
change).

## Quick index

| Patch | Concern | Natural home | Priority |
|-------|---------|--------------|----------|
| `target-none-delivery-override.patch` | `forceLastTargetWhenNone` parameter on `resolveHeartbeatDeliveryTarget` (in `src/infra/outbound/targets.ts`) + matching call-site in `src/infra/heartbeat-runner.ts` (`hasExecCompletionPending` → override) + matching assertions in `heartbeat-runner.returns-default-unset.test.ts` and `heartbeat-runner.ghost-reminder.test.ts`. The override re-routes async exec-completion events to the session's last delivery target even when `heartbeat.target` is explicitly set to `none`. | **New issue + new PR** if the project ever wants this. The current project stance (per clawsweeper review and existing regression tests) is that `target: "none"` must keep exec completions internal-only. Don't re-pursue without a maintainer signal. | Low (rejected scope) |

## Preserved git state

- **Tag:** `preserve/50818-pre-rebase-2026-04-26` (in `~/openclaw-source`)
- **Branch:** `preserve/50818-scope-creep-1f588b0a` (same repo, same SHA)
- **Commit SHA at preserve point:** `1f588b0adb28f030982e487edca83cf2e816c47d` (obviyus's `fix: tighten exec finished event matching` on top of Kaspre's `d4e3c62219` original)

If the patch file drifts or gets lost, the full pre-slim state is reconstructable from either ref.

## How the patch was generated

```bash
cd ~/openclaw-source
git show d4e3c62219 -- \
  src/infra/outbound/targets.ts \
  src/infra/heartbeat-runner.ts \
  src/infra/heartbeat-runner.returns-default-unset.test.ts \
  src/infra/heartbeat-runner.ghost-reminder.test.ts \
  > target-none-delivery-override.patch
```

So the patch contains the **full delta** Kaspre's original commit applied to those four files, against the upstream main of 2026-04-02. That delta covers two intertwined concerns:

1. The **`forceLastTargetWhenNone` override** — the rejected behavior change. This is the part being preserved.
2. **Reshuffling** of the `resolveHeartbeatDeliveryTarget` call in `heartbeat-runner.ts` so it runs after `runSessionKey` is computed and after `activeSessionPendingEventEntries` is materialized — this part stays in the slimmed PR (it's necessary for the isolated-session improvements and harmless when `forceLastTargetWhenNone` is false).

When applying the deferred patch, only hunks #1 should be picked. Hunks #2 are already in the slimmed branch — `git apply --3way` will detect the overlap.

## Hunk boundaries within the patch

### `src/infra/outbound/targets.ts` — **all hunks are scope-trim**

The file is touched only for `forceLastTargetWhenNone`. The added lines:

- `forceLastTargetWhenNone?: boolean` parameter on `resolveHeartbeatDeliveryTarget`
- `const forceLastTargetWhenNone = params.forceLastTargetWhenNone === true`
- `if (!forceLastTargetWhenNone) { ... }` branch wrapping the existing `target === "none"` early-return
- `const forcedToLast = forceLastTargetWhenNone && rawTarget === "none"` flag
- The fallback path that returns the session's last delivery target when both `forceLastTargetWhenNone === true` and `rawTarget === "none"`

All of these come out cleanly when reverting to the upstream-main shape of the function.

### `src/infra/heartbeat-runner.ts` — **mixed**

- **Scope-trim** (drop): `hasExecCompletionPending` computation; `forceLastTargetWhenNone: hasExecCompletionPending` argument on the `resolveHeartbeatDeliveryTarget` call.
- **Keep** (already part of #52305 / isolated-session work): the reshuffle that moves `resolveHeartbeatDeliveryTarget` to after `runSessionKey` resolution; the `activeSessionPendingEventEntries` substitution in the `resolveHeartbeatRunPrompt` call.

### `src/infra/heartbeat-runner.returns-default-unset.test.ts` — **scope-trim**

The test changes asserting that exec-completion events DO route to the last channel under `target: "none"` are scope-trim. Restore upstream's "no WhatsApp/Telegram send when target is none" assertions.

### `src/infra/heartbeat-runner.ghost-reminder.test.ts` — **scope-trim**

Same pattern — assertions that the override fires are scope-trim. Restore upstream behavior assertions.

## Target commit message (for revival)

```
feat(heartbeat): override target:none for async exec-completion events

When a user runs an async exec command via channel A and heartbeat.target is
configured as "none", the exec completion event currently has nowhere to go.
This adds a forceLastTargetWhenNone path on resolveHeartbeatDeliveryTarget so
async exec completions can opt into routing to the session's last delivery
target even when the global heartbeat target is none.

Wire it from heartbeat-runner.ts only when an exec-completion event is
pending in the active session, so the existing target:none behavior is
preserved for cron pings and other non-exec heartbeat reasons.

Note: this is a narrow behavior change. The default-unset and ghost-reminder
regression tests are updated to assert the new routing for the exec-pending
case.
```

## When to revive (or abandon)

Revive only if:
- A maintainer (e.g., issue comment on a related #52305-class issue) explicitly says they want async exec completions to escape `target: "none"`, OR
- A user-visible regression around lost async exec output reopens the question with maintainer support.

Otherwise, leave parked. The `feedback_codex-fractal-termination.md` companion rule applies — once scope is rejected by reviewer signal, fold it down. Don't reintroduce.
