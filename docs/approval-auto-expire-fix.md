# Patch: Approval Auto-Expire Fix

## Purpose
Fixes the bug where exec approvals instantly expire with `auto-expire:no-approver-clients` when using Discord native approval buttons. The approval is created, Discord shows "Allowed (once/always)", but the gateway has already expired it — so `waitDecision` returns "approval expired or not found" in ~1ms.

## Issue References
- Root cause analysis: `approval-auto-expire-investigation.md` (same directory)
- Related upstream: The auto-expire check assumes all approvers are WS clients; Discord native approval is a server-side module that isn't accounted for.
- No upstream issue filed yet (latent bug, only affects self-hosted with Discord-only approval)

## Root Cause
In `createExecApprovalHandlers`, after an approval record is created:

1. The forwarder's `handleRequested` returns `false` — because `shouldSkipDiscordForwarding` filters Discord out when `channels.discord.execApprovals.enabled` is `true`
2. `hasApprovalClients(context)` calls `hasExecApprovalClients()`, which only iterates WS gateway clients checking for `operator.admin` or `operator.approvals` scopes. Discord's native approval handler is built-in (not a WS client), so it returns `false`
3. `!hasApprovalClients && !forwardedToTargets` → `true` → `manager.expire(record.id, "auto-expire:no-approver-clients")` fires immediately
4. Discord's native handler eventually tries to resolve the approval, but it's already expired

## Files Patched

### Change: Recognize Discord native approvals in `hasExecApprovalClients`

**v2026.3.8 (2 files, `.bak-autoexpire` backups):**
1. `gateway-cli-CbAOelvx.js` (line ~23948)
2. `gateway-cli-C2ZZYgwu.js` (line ~23945)

**Before:**
```javascript
hasExecApprovalClients: () => {
    for (const gatewayClient of clients) {
        const scopes = Array.isArray(gatewayClient.connect.scopes) ? gatewayClient.connect.scopes : [];
        if (scopes.includes("operator.admin") || scopes.includes("operator.approvals")) return true;
    }
    return false;
},
```

**After:**
```javascript
hasExecApprovalClients: () => {
    for (const gatewayClient of clients) {
        const scopes = Array.isArray(gatewayClient.connect.scopes) ? gatewayClient.connect.scopes : [];
        if (scopes.includes("operator.admin") || scopes.includes("operator.approvals")) return true;
    }
    const discordCfg = cfgAtStart?.channels?.discord;
    if (discordCfg?.execApprovals?.enabled && discordCfg?.enabled !== false) return true;
    return false;
},
```

**Why `cfgAtStart`?** The `hasExecApprovalClients` closure is defined inside the gateway server setup scope where `cfgAtStart` (the loaded config object) is in scope. It's safe to reference because Discord native approval config doesn't change at runtime.

**Why `enabled !== false` guard?** If the Discord channel is explicitly disabled but `execApprovals.enabled` is still `true` in the config, we shouldn't claim an approver exists.

## How It Works
1. Exec needs approval → approval record created
2. Forwarder returns `false` (unchanged — Discord is skipped because native approval handles it)
3. `hasApprovalClients` → `hasExecApprovalClients()` → WS client loop finds nothing → **new check**: `cfgAtStart.channels.discord.execApprovals.enabled` is `true` → returns `true`
4. Auto-expire condition: `!true && !false` → `false` → **expire does NOT fire**
5. Discord native handler receives the approval event, shows button to approver
6. User clicks approve → `manager.resolve()` succeeds (record still pending)
7. `waitDecision` resolves with the approval decision

## How to Re-Apply After Upgrades
1. Find the gateway-cli files: `ls dist/gateway-cli-*.js` (file hashes change each version)
2. Search for `hasExecApprovalClients` — find the closure that iterates `clients` checking for `operator.admin`/`operator.approvals` scopes
3. Add the 2 lines between the `for` loop's closing `}` and `return false`:
   ```javascript
   const discordCfg = cfgAtStart?.channels?.discord;
   if (discordCfg?.execApprovals?.enabled && discordCfg?.enabled !== false) return true;
   ```
4. Verify `cfgAtStart` is in scope (search for `let cfgAtStart` in the same file — should be ~300 lines above)
5. Backup originals as `*.bak-autoexpire`
6. Restart gateway: `systemctl --user restart openclaw-gateway`
7. **Verify**: Run a command that requires approval (e.g., `node --version && head -1 /etc/hostname`). Gateway logs should show `exec.approval.waitDecision` completing in seconds (not 1ms), and the Discord button should work.

## Config Dependencies
| Path in openclaw.json | Value | Required |
|----------------------|-------|----------|
| `channels.discord.enabled` | `true` (or absent) | Yes — patch checks `enabled !== false` |
| `channels.discord.execApprovals.enabled` | `true` | Yes — this is what the patch reads |
| `channels.discord.execApprovals.approvers` | `["399076319649857537"]` | Yes — Discord needs to know who can approve |

## Applied
- 2026-03-09, v2026.3.8 (2 files)
- Applied by: Claude Code (Claude Opus 4.6)

## Related
- `approval-auto-expire-investigation.md` — full investigation with gateway log analysis, two-phase protocol details, and why it manifested after reboot (cold-start timing)
- `heartbeat-sessionkey-fix.md` — separate patch for exec notification delivery
- `exec-host-enforcement-override.md` — separate patch for sandbox host override
