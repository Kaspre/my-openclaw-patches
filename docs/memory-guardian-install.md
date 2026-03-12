# Memory Guardian Plugin Installation

**Date**: 2026-03-10
**Type**: New plugin (not a patch to compiled code)
**Reversible**: Yes — delete `~/.openclaw/extensions/memory-guardian/` and remove from `plugins.allow`

## What It Does
Active memory enforcement through OpenClaw plugin hooks:
- `before_agent_start`: Injects daily notes, shared state, checkpoint reminders into every turn via `prependContext`
- `after_tool_call`: Tracks tool calls, updates cross-session shared state, auto-breadcrumbs every 10 calls
- `before_compaction`/`before_reset`: Writes breadcrumbs before context loss (status unknown on v2026.3.8)

## Files Added
- `~/.openclaw/extensions/memory-guardian/index.ts` — plugin code (MIT, from joe-rlo gist)
- `~/.openclaw/extensions/memory-guardian/openclaw.plugin.json` — manifest

## Config Changed
- `openclaw.json`: Added `"memory-guardian"` to `plugins.allow` array

## State Files Created (auto)
- `~/.openclaw/workspace/memory/.memory-guardian-state.json` — turn/tool counters
- `~/.openclaw/workspace/memory/shared-state.md` — cross-session write log (when active)

## Source
https://gist.github.com/joe-rlo/3c3193285804b05c99bbfe541ed53c4d

## Re-Application After Upgrade
1. `mkdir -p ~/.openclaw/extensions/memory-guardian`
2. Copy `index.ts` and `openclaw.plugin.json` from backup (`~/my-openclaw-backup/extensions/memory-guardian/`)
3. Ensure `plugins.allow` in `openclaw.json` includes `"memory-guardian"`
4. Restart gateway

## Rollback
1. Remove `~/.openclaw/extensions/memory-guardian/`
2. Remove `"memory-guardian"` from `plugins.allow` in `openclaw.json`
3. Restart gateway
4. Optionally remove state files from `~/.openclaw/workspace/memory/`
