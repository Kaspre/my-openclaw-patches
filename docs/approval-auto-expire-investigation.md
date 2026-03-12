# Investigation: Exec Approval Auto-Expire Bug

## Date
2026-03-09

## Symptom
After upgrading from v2026.3.7 to v2026.3.8, Captain's exec commands that require approval fail immediately. Discord shows "Exec Approval: Allowed (always) / Resolved by Discord Exec Approvals" but Captain reports "Session ID expired" after just a few seconds (not the expected 120s timeout).

## Gateway Logs (smoking gun)
Three `exec.approval.waitDecision` failures, each completing in **1ms**:
```
12:39:53 ⇄ res ✗ exec.approval.waitDecision 1ms errorCode=INVALID_REQUEST errorMessage=approval expired or not found
12:41:02 ⇄ res ✗ exec.approval.waitDecision 1ms errorCode=INVALID_REQUEST errorMessage=approval expired or not found
12:41:15 ⇄ res ✗ exec.approval.waitDecision 1ms errorCode=INVALID_REQUEST errorMessage=approval expired or not found
```

Zero `exec.approval.request` entries in logs (successes are not logged at INFO level).

Also notable: `[EventQueue] Slow listener detected: InteractionEventListener took 24662ms` at 12:38:41.

## Root Cause Analysis (narrowed but not confirmed)

### The auto-expire path
In `gateway-cli-C2ZZYgwu.js:16834`:
```javascript
if (!hasApprovalClients(context) && !forwardedToTargets)
    manager.expire(record.id, "auto-expire:no-approver-clients");
```

This immediately expires an approval if BOTH conditions are true:
1. No WS clients connected with `operator.admin` or `operator.approvals` scopes
2. The forwarder didn't forward to any targets

### Why condition 1 is true
`hasExecApprovalClients` (line 23956) iterates over WS gateway clients checking for approval scopes. The Discord channel provider is a built-in server-side module, NOT a WS client. In a self-hosted setup with only Discord, there are typically no external WS clients with approval scopes.

### Why condition 2 is true
The forwarder (`createExecApprovalForwarder`, line 1160) checks:
1. `cfg.approvals?.exec?.enabled` — this config path does NOT exist in our `openclaw.json`
2. Even if it did, `shouldSkipDiscordForwarding` (line 1029) returns `true` when `channels.discord.execApprovals.enabled` is `true` with approvers configured

Our config has `execApprovals` at `channels.discord.execApprovals` (for native Discord buttons), but NOT at `approvals.exec` (which the forwarder checks). The forwarder's `shouldForward` returns `false` immediately.

And even if `approvals.exec.enabled` were set to `true`, `shouldSkipDiscordForwarding` would filter out Discord as a target (because our Discord channel has native exec approvals enabled). With Discord as the only channel, `filteredTargets` would be empty, and `handleRequested` would return `false`.

### The paradox: Discord shows "Allowed (always)" but the approval is expired
Discord's native exec approval handler (buttons) processes the approval through a separate mechanism — likely via the `exec.approval.requested` broadcast event or direct interaction handling. It auto-approves and calls `manager.resolve()`. But by that point, `auto-expire:no-approver-clients` has ALREADY run (synchronously, before the broadcast is processed). So `resolve()` finds the entry already expired and returns `false`.

Discord still shows "Allowed (always)" because it sends its UI message independently of whether the gateway-side resolution succeeded.

### The two-phase protocol
The session uses a two-phase approval protocol:
1. **Phase 1**: `exec.approval.request` with `twoPhase: true` → gets immediate "accepted" response with approval ID
2. **Phase 2**: `exec.approval.waitDecision` with that ID → waits for the actual decision

Between phases, the approval is auto-expired and cleaned up (15s `RESOLVED_ENTRY_GRACE_MS`). When Phase 2 runs, `_findPending(recordId)` returns null → "approval expired or not found".

### Why commands needed approval at all
Captain ran compound commands like `openclaw --version && openclaw status 2>&1 | head -40`. While `openclaw` is on the allowlist in `exec-approvals.json`, `head` is NOT. The allowlist evaluator requires ALL segments of compound commands to pass. So the allowlist check fails and falls through to the approval system.

Config details:
- `exec-approvals.json` has `security: "allowlist"` for the "main" agent (overrides `openclaw.json`'s `tools.exec.security: "full"`)
- `ask: "on-miss"` means approval is required when the allowlist check fails
- `requiresExecApproval()`: `ask === "on-miss" && security === "allowlist" && !allowlistSatisfied` → true

## Unresolved Question
**Why did this work on v2026.3.7 but not v2026.3.8?**

The `shouldSkipDiscordForwarding`, `auto-expire:no-approver-clients`, `hasExecApprovalClients`, and `twoPhase` code all exist identically in the v2026.3.2 backup (`.bak-approval` files). The `shouldSkipDiscordForwarding` implementation is character-for-character identical between v2026.3.2 and v2026.3.8.

We do NOT have v2026.3.7 dist files to compare. Possible explanations:
1. A code path change in v2026.3.8 that we haven't found (different file, not in gateway-cli)
2. The exec-approvals-allowlist module was refactored into separate files in v2026.3.8 (new: `exec-approvals-allowlist-CmGNghDQ.js`, `exec-approvals-allowlist-CzvQC_qV.js`) — the allowlist evaluation may behave differently
3. A timing/ordering change in how the broadcast, forwarder, and auto-expire sequence executes
4. The issue existed on v2026.3.7 too but wasn't triggered because Captain's commands happened to pass the allowlist (simpler commands without `head`, `|`, etc.)
5. Our patches themselves introduced a subtle interaction (the `_findPending` patch changes how methods resolve IDs)

### Resolution (2026-03-09)
Rolled back to v2026.3.7. Baseline test confirmed:
- `openclaw --version && head -1 /etc/hostname` → **no approval needed** on v2026.3.7 (command passed allowlist)
- `node --version && jq --version` → **approval triggered and waited correctly** (no instant expiry)

This confirms **explanation #4 was partially correct**: v2026.3.7's allowlist evaluation is more lenient with compound commands containing common utilities like `head`. On v2026.3.8, the stricter allowlist caused more commands to need approval, which then hit the auto-expire bug (which likely also exists on v2026.3.7 but was never triggered because fewer commands needed approval).

The `exec-approvals-allowlist-*.js` module exists in BOTH versions (not a v2026.3.8 addition), but the evaluation logic differs. The auto-expire bug itself is a latent issue in both versions — it just wasn't reachable on v2026.3.7 with our typical command patterns.

**Next steps when re-upgrading to v2026.3.8+**: Either expand the allowlist (add `head`, `tail`, `wc`, etc.) OR patch the auto-expire check, in addition to the standard 3 patches.

## Key Files and Line Numbers (v2026.3.8)

| File | Line | What |
|------|------|------|
| `gateway-cli-C2ZZYgwu.js` | 16834 | Auto-expire check |
| `gateway-cli-C2ZZYgwu.js` | 23956 | `hasExecApprovalClients` implementation |
| `gateway-cli-C2ZZYgwu.js` | 1029 | `shouldSkipDiscordForwarding` |
| `gateway-cli-C2ZZYgwu.js` | 995 | `shouldForward` (checks `cfg.approvals?.exec`) |
| `gateway-cli-C2ZZYgwu.js` | 1166 | `handleRequested` (forwarder) |
| `gateway-cli-C2ZZYgwu.js` | 2150 | `RESOLVED_ENTRY_GRACE_MS = 15e3` |
| `gateway-cli-C2ZZYgwu.js` | 2200 | `_findPending` (our patch) |
| `compact-D3emcZgv.js` | 30806 | `registerExecApprovalRequest` (two-phase client) |
| `compact-D3emcZgv.js` | 30821 | `waitForExecApprovalDecision` |
| `compact-D3emcZgv.js` | 31032 | Gateway exec approval flow (caller) |
| `exec-approvals-DODENM6Z.js` | 63 | `DEFAULT_EXEC_APPROVAL_TIMEOUT_MS = 12e4` (120s) |
| `exec-approvals-DODENM6Z.js` | 309 | `requiresExecApproval` logic |

## Config Locations

| Path in openclaw.json | Value | Purpose |
|----------------------|-------|---------|
| `channels.discord.execApprovals.enabled` | `true` | Native Discord approval buttons |
| `channels.discord.execApprovals.approvers` | `["399076319649857537"]` | Discord user IDs who can approve |
| `tools.exec.security` | `"full"` | Overridden by exec-approvals.json |
| `tools.exec.ask` | `"on-miss"` | Ask for approval on allowlist miss |
| `approvals.exec` | **MISSING** | Forwarder config — not set |

| Path in exec-approvals.json | Value | Purpose |
|-----------------------------|-------|---------|
| `agents.main.security` | `"allowlist"` | Takes precedence over openclaw.json |
| `agents.main.ask` | `"on-miss"` | Approval on allowlist miss |
| `agents.main.allowlist` | 9+ entries | Allowed executables |

## Potential Fixes (to try after isolating the cause)

1. **Patch the auto-expire check** (line 16834) to also check if any channel has native approval handling
2. **Add `approvals.exec.enabled: true`** to openclaw.json — but this alone won't fix it because `shouldSkipDiscordForwarding` still filters Discord out
3. **Expand the allowlist** — add `/usr/bin/head`, `/usr/bin/tail`, `/usr/bin/wc`, `/usr/bin/tee` so compound commands with common utilities don't need approval
4. **Remove `channels.discord.execApprovals.enabled`** — revert to text-based approvals (but still needs `approvals.exec.enabled` for the forwarder)

## Related Docs
- `patches/approval-prefix-match.md` — the `_findPending` patch
- `patches/heartbeat-sessionkey-fix.md` — heartbeat patches
- `patches/upgrade-procedure.md` — updated with baseline testing step
