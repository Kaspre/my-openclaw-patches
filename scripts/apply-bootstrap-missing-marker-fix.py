#!/usr/bin/env python3
"""
Patch: Suppress spurious [MISSING] BOOTSTRAP.md marker after onboarding
Upstream: openclaw/openclaw PR #42542 (open, not yet merged)
Related:  openclaw/openclaw PR #30388, issue #26877

Problem
-------
`BOOTSTRAP.md` is a one-time setup artifact; OC's own docs and the template's
closing line explicitly instruct "delete this file when setup is done", and
`setupCompletedAt` in `<workspace>/.openclaw/state.json` is set as a one-way
latch recording that the transition happened. After deletion, however, the
runtime bootstrap file loader (`loadWorkspaceBootstrapFiles` in workspace.ts)
still adds BOOTSTRAP.md to its entries list unconditionally, then reports it
as `missing: true`. This turns into a `[MISSING] Expected at: <path>` entry
in the injected Project Context section of every agent's system prompt.

The gateway's `listAgentFiles` RPC already has a `hideBootstrap =
isWorkspaceSetupCompleted(dir)` guard for the Control UI file browser — but
the equivalent guard was never added to the runtime loader.

Second-order effect: the same guard also silently kills the hardcoded
"Reminder: commit your changes in this workspace after edits." injection at
`pi-embedded-runner/run/attempt.ts:379-383`, which fires whenever
`bootstrapFiles.some(f => f.name === DEFAULT_BOOTSTRAP_FILENAME && !f.missing)`.
Once BOOTSTRAP.md is no longer in the list at all, that `.some()` returns
false, and the reminder stops firing — without needing to touch attempt.ts.

Fix
---
Port of upstream PR #42542 to the compiled bundle. Adds an
`isWorkspaceSetupCompleted(resolvedDir)` check before the loader loop and,
for `entry.name === DEFAULT_BOOTSTRAP_FILENAME` with onboarding complete and
a genuine ENOENT, `continue`s past the entry instead of pushing a
`missing: true` record. All other files retain their `[MISSING]` markers.
Non-ENOENT errors (ENOTDIR, ELOOP, security failures) remain visible.

Target file
-----------
`dist/workspace-R-NeOkBt.js` (hash may change on upgrade — script globs).
The pi-embedded bundle imports `loadWorkspaceBootstrapFiles` from this file,
so a single-file patch covers all call sites.

Usage
-----
  python3 apply-bootstrap-missing-marker-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-bootstrap-missing"
TARGET_GLOB = "workspace-*.js"

# Unique pattern: the else branch of the load loop inside
# loadWorkspaceBootstrapFiles. In the compiled bundle this appears exactly
# once (only loadWorkspaceBootstrapFiles pushes a {missing: true} record in
# this specific shape).
OLD_CODE = """	const memoryEntry = await resolveMemoryBootstrapEntry(resolvedDir);
	if (memoryEntry) entries.push(memoryEntry);
	const result = [];
	for (const entry of entries) {
		const loaded = await readWorkspaceFileWithGuards({
			filePath: entry.filePath,
			workspaceDir: resolvedDir
		});
		if (loaded.ok) result.push({
			name: entry.name,
			path: entry.filePath,
			content: loaded.content,
			missing: false
		});
		else result.push({
			name: entry.name,
			path: entry.filePath,
			missing: true
		});
	}
	return result;
}"""

NEW_CODE = """	const memoryEntry = await resolveMemoryBootstrapEntry(resolvedDir);
	if (memoryEntry) entries.push(memoryEntry);
	let onboardingCompleted = false;
	try {
		onboardingCompleted = await isWorkspaceSetupCompleted(resolvedDir);
	} catch {}
	const result = [];
	for (const entry of entries) {
		const loaded = await readWorkspaceFileWithGuards({
			filePath: entry.filePath,
			workspaceDir: resolvedDir
		});
		if (loaded.ok) result.push({
			name: entry.name,
			path: entry.filePath,
			content: loaded.content,
			missing: false
		});
		else if (entry.name === DEFAULT_BOOTSTRAP_FILENAME && onboardingCompleted && loaded.reason === "path" && loaded.error && loaded.error.code === "ENOENT") continue;
		else result.push({
			name: entry.name,
			path: entry.filePath,
			missing: true
		});
	}
	return result;
}"""

ALREADY_PATCHED_MARKER = "onboardingCompleted = await isWorkspaceSetupCompleted(resolvedDir)"


def find_target(dist_dir):
    candidates = [
        p for p in glob.glob(os.path.join(dist_dir, TARGET_GLOB))
        if not p.endswith(BACKUP_SUFFIX) and "plugin-sdk" not in p
    ]
    # Only want ones containing loadWorkspaceBootstrapFiles
    hits = []
    for c in candidates:
        try:
            with open(c, "r") as f:
                content = f.read()
            if "async function loadWorkspaceBootstrapFiles(" in content:
                hits.append(c)
        except OSError:
            pass
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # More than one — pick the one with the exact OLD_CODE pattern
    for c in hits:
        with open(c, "r") as f:
            if OLD_CODE in f.read():
                return c
    return hits[0]


def patch_file(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    if ALREADY_PATCHED_MARKER in content:
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
    parser = argparse.ArgumentParser(description="Apply bootstrap [MISSING] marker suppression patch (PR #42542)")
    parser.add_argument("--dry-run", action="store_true", help="Check pattern without modifying file")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir does not exist: {args.dist_dir}", file=sys.stderr)
        sys.exit(1)

    target = find_target(args.dist_dir)
    if not target:
        print(f"ERROR: no workspace-*.js bundle containing loadWorkspaceBootstrapFiles found in {args.dist_dir}", file=sys.stderr)
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
        print(f"  The OpenClaw version may have changed loadWorkspaceBootstrapFiles shape.", file=sys.stderr)
        print(f"  Review {basename} manually and update OLD_CODE in this script.", file=sys.stderr)
        sys.exit(2)
    else:
        print(f"ERROR: unexpected status '{status}' for {basename}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
