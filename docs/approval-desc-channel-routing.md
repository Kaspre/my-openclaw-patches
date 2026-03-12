# Patch: Approval Description Channel Routing

## Purpose
Fixes a bug where exec approval description embeds ("Exec Approval: Allowed (once), Resolved by...") always appear in the Discord DM channel, even when the approval was triggered from the Gateway Dashboard (control-ui). After this patch, approval descriptions stay in the channel where they originated.

## Root Cause
`DiscordExecApprovalHandler.shouldHandle()` checks `enabled`, `approvers`, `accountId`, `agentFilter`, and `sessionFilter` but does NOT check `turnSourceChannel`. So when a control-ui session triggers an exec approval, the Discord handler picks it up, sends the approval embed to Discord DM, and later updates it with "Resolved by openclaw-control-ui".

## Fix
Add a `turnSourceChannel` check at the end of `shouldHandle()`, before `return true`. If the request has a `turnSourceChannel` and it's not `"discord"`, the Discord handler skips it.

## Observed `turnSourceChannel` Values
- `"webchat"` -- control-ui/Dashboard sessions
- `"discord"` -- Discord sessions
- `"exec-event"` -- exec system events (also correctly filtered)

## Files Patched (10 files, v2026.3.8)

The active runtime file is `reply-DeXK9BLT.js`. The other 9 are duplicate bundles with different chunk hashes that may be loaded via alternative entry points.

1. `reply-DeXK9BLT.js`
2. `compact-D3emcZgv.js`
3. `pi-embedded-jHMb7qEG.js`
4. `pi-embedded-CrsFdYam.js`
5. `plugin-sdk/dispatch-CM4tRXYq.js`
6. `plugin-sdk/dispatch-CJdFmoH9.js`
7. `plugin-sdk/dispatch-BCrTbhbt.js`
8. `plugin-sdk/dispatch-DwgTiP0N.js`
9. `plugin-sdk/dispatch-F_Zbttj6.js`
10. `plugin-sdk/reply-UQ7w3uFC.js`

Backups: `*.bak-approval-desc`

## Before
```javascript
		}
		return true;
	}
	async start() {
```

## After
```javascript
		}
		const turnSource = request.request?.turnSourceChannel;
		if (turnSource && turnSource !== "discord") return false;
		return true;
	}
	async start() {
```

## How to Re-Apply After Updates
1. Find all files containing `DiscordExecApprovalHandler` and `shouldHandle(request)`:
   `grep -rn "shouldHandle(request)" dist/*.js dist/plugin-sdk/*.js | grep -v .bak`
2. In each file, locate the `shouldHandle(request)` method inside `DiscordExecApprovalHandler`
3. Add two lines before `return true;` at the end of the method:
   ```javascript
   const turnSource = request.request?.turnSourceChannel;
   if (turnSource && turnSource !== "discord") return false;
   ```
4. The unique context to match is: the `return true` that immediately follows the `sessionFilter` block and precedes `async start()`.
5. Back up changed files with `.bak-approval-desc` suffix
6. Restart gateway

## Script for Re-Application
```python
files = [
    "reply-*.js",
    "compact-*.js",
    "pi-embedded-*.js",
    "plugin-sdk/dispatch-*.js",
    "plugin-sdk/reply-*.js",
]
# In each file, replace:
old = "\t\t}\n\t\treturn true;\n\t}\n\tasync start() {"
new = "\t\t}\n\t\tconst turnSource = request.request?.turnSourceChannel;\n\t\tif (turnSource && turnSource !== \"discord\") return false;\n\t\treturn true;\n\t}\n\tasync start() {"
# Only replace within DiscordExecApprovalHandler (the pattern is unique)
```

## Related Issues
- #28753 -- Feature request: route approval prompts to originating channel (open)
- #25864 -- Webchat hijacking Discord reply channel (closed, established turnSourceChannel pattern)
- #39648 -- Control UI approval doesn't propagate to agent session (open, related to our heartbeat patch)

## Applied
- 2026-03-10, v2026.3.8 (10 files)
- Applied by: Claude Code (Claude Opus 4.6)
