# Patch: Async Approval Output Truncation Fix

## Status: NOT IMPLEMENTED — workaround in use, awaiting PR merge

## Purpose
When exec commands go through the async gateway approval flow, the `System:` event delivered back to the agent truncates output to the last 400 characters and strips all newlines. Agents receive incomplete, flattened output with no truncation indicator, leading to wrong follow-up decisions.

## Issue References
- GitHub Issue: openclaw/openclaw#41152 (filed by us, 2026-03-09)
- Upstream PR: openclaw/openclaw#41170 (open, by bde1)
- We left a supportive comment on the PR (2026-03-21)

## Current Workaround
Write exec output to a temp file, then use the `read` tool to retrieve it:
```bash
crontab -l > /tmp/exec-output.txt
# then read /tmp/exec-output.txt
```
The `read` tool bypasses the gateway relay path and returns full content. This has been reliable in production since 2026-03-09.

## Root Cause
The approval-completion path reuses the background `notifyOnExit` compact formatter:
```js
const output = normalizeNotifyOutput(tail(outcome.aggregated || "", 400));
```
- `tail()` keeps only the last 400 chars, silently dropping the front
- `normalizeNotifyOutput()` collapses all whitespace including newlines
- No truncation indicator is added

## Patch Details (if needed)

### Files to Patch (v2026.3.13)
7 dist files under `~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/`:
1. `reply-Bm8VrLQh.js`
2. `model-selection-CU2b7bN6.js`
3. `model-selection-46xMp11W.js`
4. `discord-CcCLMjHw.js`
5. `auth-profiles-DRjqKE3G.js`
6. `auth-profiles-DDVivXkv.js`
7. `plugin-sdk/thread-bindings-SYAnWHuW.js`

### Search Pattern
```
normalizeNotifyOutput(tail(outcome.aggregated || "", 400))
```

### Old Code Block (3 lines, tab-indented, identical in all 7 files)
```js
			const output = normalizeNotifyOutput(tail(outcome.aggregated || "", 400));
			const exitLabel = outcome.timedOut ? "timeout" : `code ${outcome.exitCode ?? "?"}`;
			await sendExecApprovalFollowupResult(followupTarget, output ? `Exec finished (gateway id=${approvalId}, session=${run.session.id}, ${exitLabel})\n${output}` : `Exec finished (gateway id=${approvalId}, session=${run.session.id}, ${exitLabel})`);
```

### Replacement (inlined, no new function)
```js
			const exitLabel = outcome.timedOut ? "timeout" : `code ${outcome.exitCode ?? "?"}`;
			const outputTruncatedToCap = run.session.totalOutputChars > run.session.aggregated.length;
			const truncationSuffix = outputTruncatedToCap ? ", output truncated to capture cap" : "";
			const header = `Exec finished (gateway id=${approvalId}, session=${run.session.id}, ${exitLabel}${truncationSuffix})`;
			const summary = outcome.aggregated ? `${header}\n${outcome.aggregated}` : header;
			await sendExecApprovalFollowupResult(followupTarget, summary);
```

### Implementation Notes
- `tail` and `normalizeNotifyOutput` are used elsewhere in these files (28 and 4 occurrences respectively) — do NOT remove imports, only replace the call site
- `totalOutputChars` already exists on the session object (initialized to 0, incremented per output chunk) — no new plumbing needed
- `aggregated` is already capped by `maxOutputChars` upstream, so output won't be unbounded
- The upstream PR uses `emitExecSystemEvent` (newer API) but v2026.3.13 has `sendExecApprovalFollowupResult` — patch must keep the old function name
- Python patch script pattern: follow `apply-exec-host-override.py` as template

## Decision Log
- 2026-03-09: Issue filed (#41152) with 3 reproductions across 2 sessions
- 2026-03-21: PR #41170 reviewed, supportive comment left. Decided to defer local patch — workaround is effective, 10 active patches already, PR likely to merge soon
- **Re-evaluate after next OC release**: if #41170 merged, done. If not, implement patch using details above.
