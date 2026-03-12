# Patching Guide — Lessons Learned

Hard-won lessons from patching OpenClaw's compiled JS bundles. Read this before starting any patch work.

## 1. Identify the Active Runtime File FIRST

OpenClaw's Vite/Rollup build duplicates code across 10+ bundle files (pi-embedded, compact, reply, plugin-sdk/dispatch, plugin-sdk/reply). Only ONE is loaded at runtime for a given subsystem.

**Do this:**
1. `grep -rn "YourTargetFunction" dist/*.js dist/plugin-sdk/*.js | grep -v .bak` to find ALL candidate files
2. Add a file-based debug probe to ALL of them (see #2 below)
3. Trigger the code path ONCE
4. Check which file logged — that's your active runtime file
5. NOW apply the real fix to all files

**Don't:** Guess which file is active based on name or size. We wasted 4 restart cycles patching pi-embedded and compact files before discovering reply-DeXK9BLT.js was the one actually loaded.

## 2. Worker Threads Swallow Console Output

The Discord provider (and likely other providers) runs in a worker thread. This means:
- `logDebug()` — suppressed unless verbose mode is on
- `console.error()` — goes to worker stderr, NOT captured by systemd journal
- `console.log()` — same problem

**Always use file-based logging for debug probes:**
```javascript
try { require("fs").appendFileSync("/tmp/debug.log", `[${new Date().toISOString()}] your message here\n`); } catch(e) {}
```

Clean up debug logging before finalizing the patch.

## 3. Expect 10+ File Duplicates

Every patch so far has required touching 10+ files:
- Heartbeat sessionKey fix: 14 files
- Approval desc routing: 10 files
- resolveHeartbeatReasonKind: 10 files

The duplicate bundles share identical source code but import from different chunk hashes. All must be patched for completeness, even though only one is loaded at runtime — the active file can change between versions.

## 4. Verify Edits Before Restarting

Especially with sed. Always check that code landed in the correct position:
```bash
grep -B2 -A3 "your_new_code" dist/the-file.js
```

Common pitfall: sed inserting code AFTER `return true` instead of BEFORE it (dead code).

## 5. Use Python for Multi-File Patches

sed is fragile with multiline patterns and tab-indented JS. Use Python with exact string replacement:
```python
old = "\t\t}\n\t\treturn true;\n\t}\n\tasync start() {"
new = "\t\t}\n\t\t# your new code here\n\t\treturn true;\n\t}\n\tasync start() {"
content = content.replace(old, new)
```

Verify exactly 1 replacement per file — if 0, the pattern changed; if >1, the pattern isn't unique enough.

## 6. Back Up Before Patching

Always create backups with a descriptive suffix:
```bash
cp file.js file.js.bak-patchname
```

This allows easy rollback and distinguishes backups from different patches (`.bak-heartbeat`, `.bak-approval-desc`, etc.).

## 7. Write the Patch Doc Immediately

Create a re-application doc in `~/.openclaw/workspace/patches/` for every patch right after confirming it works. Include:
- Purpose and root cause
- Exact before/after code
- All files patched (with line numbers for the active runtime file)
- Step-by-step re-application instructions
- A script or Python snippet for automated re-application

## 8. Gateway Restart Checklist

Before restarting:
- [ ] Ask user: "Anything running I'd interrupt?"
- [ ] Verify edits landed correctly (grep)
- [ ] Confirm all duplicate files are patched

After restarting:
- [ ] `systemctl --user status openclaw-gateway` — confirm active/running
- [ ] Test the patched behavior
- [ ] Test that unrelated behavior still works (e.g., Discord approvals after patching approval routing)
