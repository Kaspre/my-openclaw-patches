# Patch: Approval Prefix Matching

## Purpose
Fixes the bug where non-dashboard channels (Discord, TUI, Telegram) display an 8-character approval slug but `/approve` requires the full UUID. This patch adds prefix matching so short slugs resolve correctly.

## Issue References
- GitHub Issue: openclaw/openclaw#9591 (`/approve` fails with short ID)
- Related PR: openclaw/openclaw#9641 (closed without merge)
- Related PR: openclaw/openclaw#10001 (same pattern for memory_forget, still open)

## Files Patched

### v2026.3.8
1. `~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/gateway-cli-C2ZZYgwu.js`
2. `~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/gateway-cli-CbAOelvx.js`

### v2026.3.2 (superseded)
1. `~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/gateway-cli-CuFEx2ht.js`
2. `~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/gateway-cli-vk3t7zJU.js`

## Backups
- `gateway-cli-C2ZZYgwu.js.bak-approval`
- `gateway-cli-CbAOelvx.js.bak-approval`

## What Was Changed
Added `_findPending(recordId)` helper method to `ExecApprovalManager` class, and updated all methods that look up pending approvals by ID to use it.

### The helper method:
```javascript
_findPending(recordId) {
    const exact = this.pending.get(recordId);
    if (exact) return { key: recordId, entry: exact };
    if (recordId.length >= 36) return null;  // already full UUID, no match
    let match = null;
    for (const [key, val] of this.pending) {
        if (key.startsWith(recordId)) {
            if (match) return null;  // ambiguous: multiple matches, reject
            match = { key, entry: val };
        }
    }
    return match;
}
```

### Methods updated to use `_findPending()`:
- `resolve(recordId, decision, resolvedBy)` — main approval resolution
- `expire(recordId, resolvedBy)` — approval timeout
- `getSnapshot(recordId)` — read approval state
- `consumeAllowOnce(recordId)` — consume one-time approvals
- `awaitDecision(recordId)` — wait for pending decision

### Key design decisions:
- **Exact match first** — no behavior change for full UUIDs (backward compatible)
- **Ambiguity guard** — if multiple pending approvals share the same prefix, returns null (fails safely rather than approving the wrong thing)
- **Canonical key used for cleanup** — the `setTimeout` cleanup uses the resolved full key, not the user-provided slug

## How to Re-Apply After Updates
1. Back up the new files: `cp gateway-cli-*.js gateway-cli-*.js.bak-approval`
2. Find `resolve(recordId, decision, resolvedBy)` in each gateway-cli file (~line 2136)
3. Add `_findPending()` method before `resolve()`
4. Replace `this.pending.get(recordId)` calls in resolve/expire/getSnapshot/consumeAllowOnce/awaitDecision with `this._findPending(recordId)` pattern
5. Restart gateway: `systemctl --user restart openclaw-gateway`

## Applied
- Originally: 2026-03-08, v2026.3.2
- Re-applied: 2026-03-09, v2026.3.8
- Dropped: 2026-03-09 (suspected cause of auto-expire bug — later disproven)
- Re-applied: 2026-03-11, v2026.3.8 (via apply-approval-prefix-match.py)
- Applied by: Claude Code (Claude Opus 4.6)
