# Patch: Backgrounded Exec Heartbeat Delivery

## Purpose
Fixes the bug where backgrounded (non-approval) exec completions did not trigger a heartbeat reply to the chat channel. The agent would only respond if the user sent another message.

## Root Causes (3 bugs)

### Bug 1: `resolveHeartbeatReasonKind` — wrong reason kind
The function only matched exact string `"exec-event"`, but backgrounded exec completions generate reason `exec:<id>:exit`. This caused `isExecEventReason=false` → `shouldInspectPendingEvents=false` → `hasExecCompletion=false` → regular heartbeat sent instead of exec-event prompt.

**Fix:** Added `if (trimmed.startsWith("exec:")) return "exec-event";`  
**Files (10):** `reply-C5LKjXcC.js` + 9 others containing `resolveHeartbeatReasonKind`

### Bug 2: `isExecCompletionEvent` — wrong event string
The function checked for `"exec finished"` but `maybeNotifyOnExit` generates `"Exec completed"`. So even when `shouldInspectPendingEvents=true`, `hasExecCompletion` remained false.

**Fix:** Added `"exec completed"`, `"exec failed"`, `"exec killed"` to the check.  
**Files (2):** `health-B8fez0Ex.js`, `health-MD58MQui.js`

### Bug 3: `forceLastTargetWhenNone` — delivery target not resolving
Delivery to Discord wasn't resolving when no explicit target was set on the heartbeat session. Fixed separately (details in earlier Claude Code session).

## Result
Full chain confirmed working via HB-DEBUG logs:
- `maybeNotifyOnExit` fires → event enqueued
- `run()` receives wake with correct reason + sessionKey
- `resolveDelivery` resolves to `channel=discord`
- `hasExecCompletion=true` → exec-event prompt used
- Response delivered to Discord automatically

## Applied
- Date: 2026-03-08
- OpenClaw version: 2026.3.7
- Applied by: Claude Code (debugging session with Kaspre/Bossman)

## Re-apply After Updates
1. Back up gateway-cli, reply, and health dist files
2. In `resolveHeartbeatReasonKind`: add `if (trimmed.startsWith("exec:")) return "exec-event";` after the `"exec-event"` line
3. In `isExecCompletionEvent`: add `"exec completed"`, `"exec failed"`, `"exec killed"` checks
4. Restart gateway: `systemctl --user restart openclaw-gateway`
