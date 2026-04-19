#!/usr/bin/env python3
"""
Patch: memoryFlush skip-every-other compaction fix
Issue: openclaw/openclaw#12590

Removes the counter reassignment that causes memoryFlush to skip every other
compaction cycle. After the patch, flush fires on every compaction.

Usage:
  python3 apply-memoryflush-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-memflush"

# --- Replacement patterns ---

# 1. Change `const nextCount = await incrementCompactionCount({` to `await incrementCompactionCount({`
# v2026.3.22 pattern (without newSessionId)
OLD_INCREMENT_V322 = """\t\t\tconst nextCount = await incrementCompactionCount({
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath
\t\t\t});
\t\t\tif (typeof nextCount === "number") memoryFlushCompactionCount = nextCount;"""

# v2026.3.24 pattern (with newSessionId)
OLD_INCREMENT_V324 = """\t\t\tconst nextCount = await incrementCompactionCount({
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath,
\t\t\t\tnewSessionId: postCompactionSessionId
\t\t\t});"""

NEW_INCREMENT_V324 = """\t\t\tawait incrementCompactionCount({
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath,
\t\t\t\tnewSessionId: postCompactionSessionId
\t\t\t});"""

# v2026.4.15 pattern (memoryDeps wrapper + cfg param)
OLD_INCREMENT_V415 = """\t\t\tconst nextCount = await memoryDeps.incrementCompactionCount({
\t\t\t\tcfg: params.cfg,
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath,
\t\t\t\tnewSessionId: postCompactionSessionId
\t\t\t});"""

NEW_INCREMENT_V415 = """\t\t\tawait memoryDeps.incrementCompactionCount({
\t\t\t\tcfg: params.cfg,
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath,
\t\t\t\tnewSessionId: postCompactionSessionId
\t\t\t});"""

# The reassignment line to remove (same across versions)
OLD_REASSIGN = """\t\t\tif (typeof nextCount === "number") memoryFlushCompactionCount = nextCount;"""

NEW_REASSIGN = """\t\t\t// FIX #12590: Do NOT reassign memoryFlushCompactionCount to post-increment value.
\t\t\t// Keep it at pre-increment (N) so next cycle's compactionCount (N+1) won't match,
\t\t\t// allowing flush to fire on every compaction instead of every other."""

# Tuples are (group, name, old, new). Two groups exist:
#   "capture"  — removes the `const nextCount = await ...` variable capture.
#                Any ONE variant (v415/v324/v322) should match per file.
#   "reassign" — removes the `memoryFlushCompactionCount = nextCount` line. Always one.
# Post-apply assertion: each group must have matched at least once. If only
# "reassign" matches, the dist has been refactored to a new shape and a new
# capture variant needs to be added to this file — partial patches are LOUD,
# not silent.
REPLACEMENTS = [
    # Try v2026.4.15 pattern first (memoryDeps wrapper), then v2026.3.24, then v2026.3.22
    ("capture", "incrementCompactionCount return capture (v415)", OLD_INCREMENT_V415, NEW_INCREMENT_V415),
    ("capture", "incrementCompactionCount return capture (v324)", OLD_INCREMENT_V324, NEW_INCREMENT_V324),
    ("capture", "incrementCompactionCount return capture (v322)", OLD_INCREMENT_V322, OLD_INCREMENT_V322.replace("const nextCount = await", "await")),
    ("reassign", "counter reassignment removal", OLD_REASSIGN, NEW_REASSIGN),
]
REQUIRED_GROUPS = {"capture", "reassign"}


def find_target_files(dist_dir):
    """Find JS files that actually contain the memoryFlush counter-reassignment bug.

    We look for a tight signature — the REASSIGN line or a FIX-comment marker —
    rather than any file referencing `incrementCompactionCount`, because the
    function has many callers that don't have the bug (imports, resets, etc.)
    and broad matching produces scary-but-cosmetic "no matching patterns found"
    errors in apply-all output.

    Signatures that count as a target:
      - contains the buggy REASSIGN line (unpatched)
      - contains our FIX #12590 marker (already patched — still report it so the
        caller can see "SKIP already patched" instead of "file not found")
    """
    patterns = [
        os.path.join(dist_dir, "*.js"),
        os.path.join(dist_dir, "plugin-sdk", "*.js"),
    ]
    all_js = []
    for pat in patterns:
        all_js.extend(f for f in glob.glob(pat) if ".bak" not in f)
    target = []
    for f in sorted(all_js):
        try:
            with open(f, "r") as fh:
                content = fh.read()
        except OSError:
            continue
        if OLD_REASSIGN in content or "FIX #12590" in content:
            target.append(f)
    return target


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    original = content

    # Check if already patched
    if "FIX #12590" in content:
        print(f"  SKIP (already patched): {os.path.basename(filepath)}")
        return False

    applied_groups = set()
    applied_count = 0
    for group, name, old, new in REPLACEMENTS:
        count = content.count(old)
        if count == 0:
            continue  # Try next pattern variant
        if count > 1:
            print(f"  ERROR: pattern for {name} found {count} times (expected 1) in {os.path.basename(filepath)}")
            return False
        content = content.replace(old, new)
        print(f"  Applied: {name}")
        applied_groups.add(group)
        applied_count += 1

    missing_groups = REQUIRED_GROUPS - applied_groups
    if missing_groups:
        # Partial patch: behavioral fix may still hold if "reassign" matched,
        # but the dist has drifted from our patterns. Fail loudly so the next
        # upgrade prompts a pattern refresh instead of silently accumulating
        # dead code or missing pieces of the fix.
        print(
            f"  ERROR: partial patch on {os.path.basename(filepath)} — "
            f"groups matched: {sorted(applied_groups) or 'none'}; missing: {sorted(missing_groups)}. "
            f"Upstream likely refactored the shape; add a new variant to REPLACEMENTS."
        )
        return False

    # Post-apply sanity check: the `const nextCount = await …` form should be
    # gone from the patched region. If any survive, a new capture variant slipped
    # through selection without being rewritten.
    if "const nextCount = await" in content:
        print(
            f"  ERROR: {os.path.basename(filepath)} still contains `const nextCount = await` "
            f"after patching — unmatched capture variant. Add a new pattern to REPLACEMENTS."
        )
        return False

    if content == original:
        print(f"  SKIP (no changes): {os.path.basename(filepath)}")
        return False

    if dry_run:
        print(f"  DRY RUN OK: {os.path.basename(filepath)} — {applied_count} replacement(s) matched ({sorted(applied_groups)})")
        return True

    # Backup
    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backup: {os.path.basename(backup)}")

    with open(filepath, "w") as f:
        f.write(content)

    print(f"  PATCHED: {os.path.basename(filepath)} — {applied_count} replacement(s) applied ({sorted(applied_groups)})")
    return True


def main():
    parser = argparse.ArgumentParser(description="Apply memoryFlush skip-every-other fix")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_target_files(args.dist_dir)
    if not files:
        print(f"ERROR: No files containing incrementCompactionCount found in {args.dist_dir}")
        sys.exit(1)

    print(f"memoryFlush Skip-Every-Other Fix {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Found {len(files)} file(s) with compaction counter")
    print()

    patched = 0
    for f in files:
        if apply_patch(f, dry_run=args.dry_run):
            patched += 1

    print()
    if args.dry_run:
        print(f"Dry run complete: {patched}/{len(files)} files would be patched")
    else:
        print(f"Done: {patched}/{len(files)} files patched")
        if patched > 0:
            print("Restart gateway: systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
