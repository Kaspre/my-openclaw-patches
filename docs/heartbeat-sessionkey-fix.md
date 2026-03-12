# Patch: Heartbeat SessionKey Propagation (PR #21682)

## Purpose
Fixes the bug where the agent model goes silent after exec approval on non-main channels (Discord DM, etc.). The heartbeat system was checking the wrong session key for exec system events, so the model was never woken up after approval+exec completed.

## Issue References
- GitHub Issue: openclaw/openclaw#14191 (heartbeat checks wrong session key)
- PR: openclaw/openclaw#21682 (open, not merged)

## Files Patched

### v2026.3.8 / v2026.3.7: Change 1 MERGED UPSTREAM — only Changes 2-5 needed

v2026.3.7+ uses `scopedHeartbeatWakeOptions(sessionKey, { reason: "exec-event" })` natively.
Only Changes 2-5 still require local patching.

### Change 2: Add `forceLastTargetWhenNone` guard in `resolveHeartbeatDeliveryTarget`

**v2026.3.8 (2 files):**
1. `reply-DeXK9BLT.js:19309`
2. `compact-D3emcZgv.js:47155`

**v2026.3.7 (2 files — superseded):**
1. `reply-C5LKjXcC.js:13275`
2. `compact-B247y5Qt.js:47087`

**v2026.3.2 (2 files — superseded):**
1. `reply-DhtejUNZ.js:20448`
2. `subagent-registry-CkqrXKq4.js:30781`

**Before:**
```javascript
if (target === "none") {
    const base = resolveSessionDeliveryTarget({ entry });
    return buildNoHeartbeatDeliveryTarget({
        reason: "target-none",
        lastChannel: base.lastChannel,
        lastAccountId: base.lastAccountId
    });
}
```

**After:**
```javascript
if (target === "none") {
    if (!params.forceLastTargetWhenNone) {
        const base = resolveSessionDeliveryTarget({ entry });
        return buildNoHeartbeatDeliveryTarget({
            reason: "target-none",
            lastChannel: base.lastChannel,
            lastAccountId: base.lastAccountId
        });
    }
    target = "last";
}
```

### Change 3: Pass `forceLastTargetWhenNone` from heartbeat runner

**v2026.3.8 (2 files, `.bak-heartbeat` backups):**
1. `health-CwgmZsQL.js:458`
2. `health-DL8GZdZB.js:458`

**v2026.3.7 (2 files — superseded):**
1. `health-B8fez0Ex.js:459`
2. `health-MD58MQui.js:459`

**v2026.3.2 (2 files — superseded):**
1. `health-GBxhlVbm.js:458`
2. `health-fOOBvmWF.js:458`

**Before:**
```javascript
const delivery = resolveHeartbeatDeliveryTarget({
    cfg,
    entry,
    heartbeat
});
```

**After:**
```javascript
const delivery = resolveHeartbeatDeliveryTarget({
    cfg,
    entry,
    heartbeat,
    forceLastTargetWhenNone: opts.reason === "exec-event" || (typeof opts.reason === "string" && opts.reason.startsWith("exec:"))
});
```

**Why both patterns?** Two code paths emit exec heartbeats:
- `emitExecSystemEvent` → reason `"exec-event"` (approval-gated execs)
- `maybeNotifyOnExit` → reason `"exec:<sessionId>:exit"` (all backgrounded execs including allowlisted)
The original PR #21682 only covered the first. Without the `startsWith("exec:")` match, allowlisted commands that background themselves complete silently with no model notification.

### Change 4: Recognize `exec:<id>:exit` reason in `resolveHeartbeatReasonKind`

**v2026.3.8 (10 files):**
1. `reply-DeXK9BLT.js`
2. `compact-D3emcZgv.js`
3. `pi-embedded-jHMb7qEG.js`
4. `pi-embedded-CrsFdYam.js`
5. `plugin-sdk/dispatch-F_Zbttj6.js`
6. `plugin-sdk/dispatch-DwgTiP0N.js`
7. `plugin-sdk/dispatch-BCrTbhbt.js`
8. `plugin-sdk/dispatch-CJdFmoH9.js`
9. `plugin-sdk/dispatch-CM4tRXYq.js`
10. `plugin-sdk/reply-UQ7w3uFC.js`

**v2026.3.7 (10 files — superseded):**
1. `reply-C5LKjXcC.js:28703`
2. `compact-B247y5Qt.js`
3. `pi-embedded-C6ITuRXf.js`
4. `pi-embedded-DoQsYfIY.js`
5. `plugin-sdk/dispatch-UogiJYul.js`
6. `plugin-sdk/dispatch-CQsjmw7g.js`
7. `plugin-sdk/dispatch-BP0viZiL.js`
8. `plugin-sdk/dispatch-Cndjtt0g.js`
9. `plugin-sdk/dispatch-Cerq29sy.js`
10. `plugin-sdk/reply-DbZnH8-h.js`

**Before:**
```javascript
if (trimmed === "exec-event") return "exec-event";
if (trimmed === "wake") return "wake";
```

**After:**
```javascript
if (trimmed === "exec-event") return "exec-event";
if (trimmed.startsWith("exec:")) return "exec-event";
if (trimmed === "wake") return "wake";
```

**Why?** `maybeNotifyOnExit` sends reason `exec:<sessionId>:exit` but `resolveHeartbeatReasonKind` only matched exact `"exec-event"`. Without this fix, `isExecEventReason` is false → `shouldInspectPendingEvents` is false → pending exec events are never read → `hasExecCompletion` is false → model gets regular heartbeat prompt instead of exec-event prompt.

### Change 5: Fix `isExecCompletionEvent` string match

**v2026.3.8 (2 files):**
1. `health-CwgmZsQL.js:108`
2. `health-DL8GZdZB.js:108`

**v2026.3.7 (2 files — superseded):**
1. `health-B8fez0Ex.js:109`
2. `health-MD58MQui.js:109`

**Before:**
```javascript
function isExecCompletionEvent(evt) {
    return evt.toLowerCase().includes("exec finished");
}
```

**After:**
```javascript
function isExecCompletionEvent(evt) {
    const lower = evt.toLowerCase();
    return lower.includes("exec finished") || lower.includes("exec completed") || lower.includes("exec failed") || lower.includes("exec killed");
}
```

**Why?** `maybeNotifyOnExit` generates `"Exec completed (...)"` but `isExecCompletionEvent` only checked for `"exec finished"`. The event was present in the queue but never recognized as an exec completion.

## How It Works (end-to-end)

### Approval-gated exec (worked since Changes 1-3):
1. Exec approved → `emitExecSystemEvent` queues event with `reason: "exec-event"`
2. `resolveHeartbeatReasonKind("exec-event")` → `"exec-event"` ✓
3. Heartbeat uses exec-event prompt, model wakes and responds

### Backgrounded (non-approval) exec (requires all 5 Changes):
1. Allowlisted command backgrounds after yieldMs (10s default)
2. Command completes → `maybeNotifyOnExit` enqueues `"Exec completed (...)"` with `reason: "exec:<id>:exit"`
3. `resolveHeartbeatReasonKind("exec:<id>:exit")` → `"exec-event"` ✓ (Change 4)
4. `isExecEventReason = true` → `shouldInspectPendingEvents = true` → pending events read
5. `isExecCompletionEvent("Exec completed ...")` → `true` ✓ (Change 5)
6. `hasExecCompletion = true` → exec-event prompt used
7. `forceLastTargetWhenNone = true` ✓ (Change 3) → delivery targets Discord
8. Model wakes on Discord DM, sees exec result, responds automatically

## How to Re-Apply After Updates
1. Check if Change 1 is still upstream: search for `scopedHeartbeatWakeOptions.*exec-event`. If found, skip Change 1.
2. If Change 1 still needed: find `requestHeartbeatNow({ reason: "exec-event" })` and add `, sessionKey` before the closing `}`
3. **Change 2**: Find `function resolveHeartbeatDeliveryTarget` — wrap the `target === "none"` block with `if (!params.forceLastTargetWhenNone)` guard and add `target = "last"` else branch
4. **Change 3**: In health-*.js files, find `resolveHeartbeatDeliveryTarget({` call and add `forceLastTargetWhenNone: opts.reason === "exec-event" || (typeof opts.reason === "string" && opts.reason.startsWith("exec:"))` parameter
5. **Change 4**: In all files containing `resolveHeartbeatReasonKind`, add `if (trimmed.startsWith("exec:")) return "exec-event";` after the `exec-event` exact match
6. **Change 5**: In health-*.js files, update `isExecCompletionEvent` to also match `"exec completed"`, `"exec failed"`, `"exec killed"`
7. Back up all changed files with `.bak-heartbeat` suffix
8. Restart gateway

## Workarounds Also Applied
- **Symlink**: `/home/captain/openclaw` → `/home/captain/.openclaw` — prevents ENOENT when heartbeat model hallucinates wrong workspace path
- **Debug instrumentation**: `[HB-DEBUG]` console.error traces in health-*.js and reply-*.js (temporary, can remove after stabilization)

## Applied
- Originally: 2026-03-08, v2026.3.2 (Changes 1+2+3, 9 files)
- Re-applied: 2026-03-08, v2026.3.7 (Changes 2+3 only, 4 files — Change 1 merged upstream)
- Changes 4+5 added: 2026-03-08, v2026.3.7 (12 additional files — fixes backgrounded exec notification)
- Re-applied: 2026-03-09, v2026.3.8 (Changes 2-5, 14 files total)
- Applied by: Claude Code (Claude Opus 4.6)
