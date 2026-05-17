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

## 5b. Anchor on Stable Identifiers, Treat Cosmetic Bits as Placeholders

OpenClaw's bundler shuffles two kinds of things on every release:

- **Stable**: user-defined function names, exported identifiers, string literals, JSON keys, public API surface. These survive rebuilds because they're meaningful in source.
- **Cosmetic**: chunk content-hashes (`model-fallback-DiS9IGQs.js`), minified short names (`o`, `i`, `r`), bundler-renamed module aliases (`process$1`, `process$2`). These regenerate on every build.

**The rule:** anchor on stable identifiers. When cosmetic bits MUST appear in a search/replace string (e.g., importing from another chunk, referencing a renamed alias), treat them as placeholders captured at apply time.

**Anti-pattern (what bit us 2026-05-17 with `infer-model-run-ephemeral-session` on beta.4):**
```python
# OLD_IMPORTS line hardcodes the chunk hash from when the patch was authored.
# Next release: chunk renamed, this import is now dangling.
NEW = OLD + '\nimport { o as buildFn } from "./model-fallback-DiS9IGQs.js";'
```

**Correct pattern — discover cosmetic bits dynamically:**
```python
import glob, os, re

def find_chunk(dist_dir, basename_prefix):
    """Locate active chunk; hash drifts every release."""
    pattern = os.path.join(dist_dir, f"{basename_prefix}-*.js")
    candidates = [
        os.path.basename(f) for f in glob.glob(pattern)
        if ".bak" not in f
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly 1 {basename_prefix} chunk, found {len(candidates)}")
    return candidates[0]

def find_export_alias(dist_dir, chunk_filename, local_name):
    """Find the minified short name for `local_name` in chunk's exports.
    Bundler renames `o`, `i`, `r` etc. arbitrarily per release."""
    with open(os.path.join(dist_dir, chunk_filename)) as f:
        content = f.read()
    # Chunk exports as `localName as <X>`; we then `import { <X> as localName }`.
    match = re.search(rf"{re.escape(local_name)}\s+as\s+(\w+)", content)
    if not match:
        raise RuntimeError(f"{local_name} export alias not found in {chunk_filename}")
    return match.group(1)
```

Then build the import line dynamically in `main()` after we know the dist dir.

**For in-bundle anchors that reference bundler-renamed aliases like `process$1`:**

```python
# Anti-pattern: hardcoded process$1
"search": "if (!tryHandleRootVersionFastPath(process$1.argv)) await runMainOrRootHelp(process$1.argv);"

# Better: regex anchor with placeholder, then build the literal `search` string at apply time
ANCHOR_RE = re.compile(
    r'if \(!tryHandleRootVersionFastPath\((process\$?\d*)\.argv\)\) '
    r'await runMainOrRootHelp\(\1\.argv\);'
)
match = ANCHOR_RE.search(content)
if not match:
    raise RuntimeError("anchor pattern not found")  # fail loud (structural change)
process_alias = match.group(1)  # e.g., "process$1" or "process$2"
old_literal = match.group(0)
new_literal = f"if (!tryHandleRootVersionFastPath({process_alias}.argv)) ..."
content = content.replace(old_literal, new_literal)
```

The backreference `\1` reuses the captured alias on the second occurrence so both stay consistent.

**What still fails loud (and should):** if upstream actually reorders/rewrites the code (the regex doesn't match anymore), the patch errors out cleanly. That's the signal to review whether our patch is still needed — often it isn't, because the upstream change implements the same idea.

**Existing examples to study:**
- `apply-infer-model-run-ephemeral-session.py` — dynamic chunk filename + export alias discovery
- Other 6 active patches anchor entirely on stable identifiers (no cosmetic bits in their search strings) — that's also a valid solution and is preferable when achievable

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
