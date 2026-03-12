# Patch: Exec Host Enforcement Override

## Purpose
Prevents the exec tool from throwing an error when the model requests `host: "sandbox"` but the configured host is `"gateway"`. Instead of erroring out, the patch silently overrides the model's requested host with the configured host. This keeps exec functional when models default to `host: "sandbox"` in their tool calls but the operator has configured `tools.exec.host: "gateway"`.

## Issue References
- GitHub Issue: openclaw/openclaw#11150 (exec schema host mismatch)
- PR: openclaw/openclaw#11185 (open, not merged)

## Files Patched

### v2026.3.8 (10 files, all have `.bak` backups)
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

### v2026.3.7 (10 files — superseded)
1. `reply-DhtejUNZ.js:9570`
2. `subagent-registry-CkqrXKq4.js:14300`
3. `pi-embedded-CtM2Mrrj.js:20855`
4. `pi-embedded-DgYXShcG.js:20851`
5. `plugin-sdk/reply-DFFRlayb.js:16628`

## What Was Changed

**Before (throws error):**
```javascript
if (!elevatedRequested && requestedHost && requestedHost !== configuredHost)
    throw new Error(`exec host not allowed (requested ${renderExecHostLabel(requestedHost)}; configure tools.exec.host=${renderExecHostLabel(configuredHost)} to allow).`);
```

**After (silently overrides):**
```javascript
if (!elevatedRequested && requestedHost && requestedHost !== configuredHost)
    host = configuredHost;
```

## Why This Is Needed
- The exec tool schema includes `host` as a parameter the model can set
- The schema default is `sandbox` (line 9524: `default: defaults?.host ?? "sandbox"`)
- Many models emit `host: "sandbox"` in their tool calls even when the operator has configured `tools.exec.host: "gateway"`
- Without the patch, every such exec call throws an error and the model retries or gives up
- The patch respects the operator's configured host while keeping exec functional

## Config Context
```json
"tools": {
    "exec": {
        "host": "gateway",
        "security": "full",
        "ask": "on-miss"
    }
}
```

## How to Re-Apply After Updates
1. Back up all 5 files with `.bak` suffix
2. In each file, search for `requestedHost !== configuredHost) throw new Error`
3. Replace the `throw new Error(...)` with `host = configuredHost;`
4. The line numbers will shift between versions — search by the throw pattern, not by line number
5. Restart gateway

## Applied
- Originally: 2026-03-07, v2026.3.2 (5 files)
- Re-applied: 2026-03-08, v2026.3.7 (10 files — new dist added compact-*.js and plugin-sdk/dispatch-*.js variants)
- Re-applied: 2026-03-09, v2026.3.8 (10 files, same count as v2026.3.7)
- Applied by: Claude Code (Claude Opus 4.6)

## Notes
- File count grew from 5 (v2026.3.2) to 10 (v2026.3.7) — check for new files after every upgrade
- Search pattern `requestedHost !== configuredHost) throw new Error` reliably finds all locations regardless of version
