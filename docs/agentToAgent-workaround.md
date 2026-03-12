# Workaround: agentToAgent Disabled — Exec-Based Query Delegation

## The Problem
Bug #5813: Setting `agentToAgent.enabled: true` breaks `sessions_spawn` — subagents never start (0 tokens indefinitely). This forces a choice: named agent messaging OR subagent spawning, not both.

## Our Choice
Keep `agentToAgent.enabled: false` to preserve subagent spawning. Use exec-based CLI workaround for Captain → Query communication.

## Config (intentional — do not change)
```json
"agentToAgent": {
    "enabled": false,
    "allow": ["main", "query"]
}
```

## The Workaround
Captain delegates research to Query via exec instead of sessions_send:
```
openclaw agent --agent query -m "research prompt" --timeout 120
```

This sends the prompt to Query through the gateway CLI, runs a full agent turn, and returns Query's response as exec output.

## What Doesn't Work (and why)
- `sessions_send` to `agent:query:main` — requires `agentToAgent.enabled: true`, which breaks subagents
- `sessions_spawn` with agentToAgent enabled — bug #5813, subagent hangs at 0 tokens
- Partial fix from @waynelian (adding parent to allow list) — only fixes `sessions_spawn`, not `sessions_send`

## Where This Is Documented
- `AGENTS.md` → "Subagent Orchestration" section — Captain's operating instructions
- This file — technical background and rationale
- Claude Code memory (`MEMORY.md`) — quick reference

## When to Remove This Workaround
When bug #5813 is fixed upstream:
1. Set `agentToAgent.enabled: true`
2. Change AGENTS.md back to `sessions_send` to `agent:query:main`
3. Delete this file
4. Full gateway restart required

## Issue References
- GitHub Issue: openclaw/openclaw#5813 (agentToAgent breaks sessions_spawn)
- Workaround confirmed by @waynelian on 2026-02-24 (sessions_spawn only, not sessions_send)

## Applied
- Date: 2026-03-08
- OpenClaw version: 2026.3.7
- Applied by: Claude Code (Claude Opus 4.6)
