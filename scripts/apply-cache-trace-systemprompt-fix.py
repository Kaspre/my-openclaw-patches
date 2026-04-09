#!/usr/bin/env python3
"""
Patch: cache-trace wrapStreamFn reads wrong field for system prompt
Upstream: openclaw/openclaw PR #58928 (open, not yet merged)

Problem
-------
`src/agents/cache-trace.ts` line 245 (in the compiled bundle: `pi-embedded-BYdcxQ5A.js`
line 32168) reads the system prompt from `context.system` when wrapping the stream
function for the `stream:context` trace stage. However, the actual field name in
OpenClaw's ModelContext (as passed to `wrapStreamFn`) is `systemPrompt`, not `system`.

Effect: for 100% of non-Anthropic provider traffic (openai-codex, ollama-cloud,
google-gemini), the `system` field is silently dropped from cache-trace.jsonl
entries despite `diagnostics.cacheTrace.includeSystem: true` being set.

Empirically verified on 2026-04-08: 25 `stream:context` entries in the last 20MB
of cache-trace.jsonl for ollama-cloud/kimi-k2.5, zero contain a `system` field.
Configuration was correct. Docs say it should be there. It wasn't.

Root cause per PR #58928: "Field name mismatch — cache-trace.ts wrapper assumed
Pi Agent's StreamFn context parameter had a system field, but the actual field
name in OpenClaw's usage is systemPrompt."

This bug makes the existing cache-trace pipeline effectively blind to the most
important diagnostic signal — the composed system prompt — for every provider
except Anthropic. Fixing it is a precondition for any meaningful before/after
context analysis, agent-behavior diffing, or historical prompt replay.

Fix
---
One-character class of change: `context.system` → `context.systemPrompt` in the
compiled bundle at the `stream:context` recordStage call inside wrapStreamFn.

Pattern is uniquely located — verified `grep -c "system: context.system,"` returns
exactly 1 across the entire dist directory.

Target file
-----------
`dist/pi-embedded-BYdcxQ5A.js` (hash may change on upgrade — script globs for
`pi-embedded-*.js` and filters by the unique pattern).

Usage
-----
  python3 apply-cache-trace-systemprompt-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-cache-trace-sysprompt"
TARGET_GLOB = "pi-embedded-*.js"

# Unique pattern: the exact line inside wrapStreamFn's recordStage call
OLD_CODE = "\t\t\t\tsystem: context.system,"

NEW_CODE = "\t\t\t\tsystem: context.systemPrompt,"

ALREADY_PATCHED_MARKER = "system: context.systemPrompt,"


def find_target(dist_dir):
    candidates = [
        p for p in glob.glob(os.path.join(dist_dir, TARGET_GLOB))
        if not p.endswith(BACKUP_SUFFIX)
    ]
    hits = []
    for c in candidates:
        try:
            with open(c, "r") as f:
                content = f.read()
            if OLD_CODE in content or ALREADY_PATCHED_MARKER in content:
                hits.append(c)
        except OSError:
            pass
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # Prefer the one with the exact OLD_CODE pattern
    for c in hits:
        with open(c, "r") as f:
            if OLD_CODE in f.read():
                return c
    return hits[0]


def patch_file(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    # Check already-patched FIRST (the marker string is a substring of NEW_CODE)
    if OLD_CODE not in content and ALREADY_PATCHED_MARKER in content:
        return ("already_patched", basename)

    if OLD_CODE not in content:
        return ("pattern_not_found", basename)

    count = content.count(OLD_CODE)
    if count != 1:
        return (f"pattern_matched_{count}_times", basename)

    new_content = content.replace(OLD_CODE, NEW_CODE)

    if dry_run:
        return ("would_patch", basename)

    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)

    with open(filepath, "w") as f:
        f.write(new_content)

    return ("patched", basename)


def main():
    parser = argparse.ArgumentParser(
        description="Apply cache-trace systemPrompt field-name fix (PR #58928)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Check pattern without modifying file")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir does not exist: {args.dist_dir}", file=sys.stderr)
        sys.exit(1)

    target = find_target(args.dist_dir)
    if not target:
        print(f"ERROR: no pi-embedded-*.js bundle containing wrapStreamFn found in {args.dist_dir}", file=sys.stderr)
        sys.exit(1)

    status, basename = patch_file(target, dry_run=args.dry_run)

    if status == "patched":
        print(f"OK: patched {basename} (backup at {basename}{BACKUP_SUFFIX})")
        sys.exit(0)
    elif status == "would_patch":
        print(f"DRY-RUN: would patch {basename}")
        sys.exit(0)
    elif status == "already_patched":
        print(f"SKIP: {basename} already patched")
        sys.exit(0)
    elif status == "pattern_not_found":
        print(f"ERROR: expected pattern not found in {basename}", file=sys.stderr)
        print(f"  The OpenClaw version may have changed cache-trace wrapStreamFn shape.", file=sys.stderr)
        print(f"  Review {basename} manually and update OLD_CODE in this script.", file=sys.stderr)
        sys.exit(2)
    else:
        print(f"ERROR: unexpected status '{status}' for {basename}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
