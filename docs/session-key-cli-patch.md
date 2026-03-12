# Patch: --session-key CLI flag (PR #35241)

## Problem
`openclaw agent --session-id <id>` ignores the session ID for routing purposes. All CLI agent calls route to `agent:<agentId>:main` regardless, causing session bleed. Confirmed bugs: #22085, #23635.

## Fix
Cherry-picked PR #35241 (zhangzhejian) — adds `--session-key <key>` flag that overrides the session routing key. The downstream `resolveSessionKeyForRequest()` already supports a `sessionKey` field; this patch just wires up the CLI option.

## Files Patched (2 files, 3 edits each)
- `register.agent-DuRsxfgU.js` (backup: `.bak`)
- `register.agent-BMiSilfY.js` (backup: `.bak`)

Path: `/home/captain/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/`

### Edit 1: CLI option registration (registerAgentCommands function)
After `.option("--session-id <id>", "Use an explicit session id")`, add:
```
.option("--session-key <key>", "Use an explicit session key")
```

### Edit 2: Validation guard (agentViaGatewayCommand function)
Change:
```js
if (!opts.to && !opts.sessionId && !opts.agent)
```
To:
```js
if (!opts.to && !opts.sessionId && !opts.sessionKey && !opts.agent)
```
Also update error message to include `--session-key`.

### Edit 3: Session resolution (resolveSessionKeyForRequest call)
Add `sessionKey: opts.sessionKey` to the options object:
```js
const sessionKey = resolveSessionKeyForRequest({
    cfg,
    agentId,
    to: opts.to,
    sessionId: opts.sessionId,
    sessionKey: opts.sessionKey  // <-- added
}).sessionKey;
```

## Usage
```bash
openclaw agent --agent eval --session-key "agent:eval:my-unique-key" -m "prompt"
```

## Applied
- Version: v2026.3.8
- Date: 2026-03-10
- Upstream: PR #35241 (open, unmerged). Also PR #24117 (more complete, also unmerged).
