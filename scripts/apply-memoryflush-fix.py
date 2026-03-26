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
    "~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist"
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

# The reassignment line to remove (same across versions)
OLD_REASSIGN = """\t\t\tif (typeof nextCount === "number") memoryFlushCompactionCount = nextCount;"""

NEW_REASSIGN = """\t\t\t// FIX #12590: Do NOT reassign memoryFlushCompactionCount to post-increment value.
\t\t\t// Keep it at pre-increment (N) so next cycle's compactionCount (N+1) won't match,
\t\t\t// allowing flush to fire on every compaction instead of every other."""

REPLACEMENTS = [
    # Try v2026.3.24 pattern first (with newSessionId), then fall back to v2026.3.22
    ("incrementCompactionCount return capture (v324)", OLD_INCREMENT_V324, NEW_INCREMENT_V324),
    ("incrementCompactionCount return capture (v322)", OLD_INCREMENT_V322, OLD_INCREMENT_V322.replace("const nextCount = await", "await")),
    ("counter reassignment removal", OLD_REASSIGN, NEW_REASSIGN),
]


def find_target_files(dist_dir):
    """Find JS files containing the memoryFlush counter pattern.
    v2026.3.8 used compact-*.js; v2026.3.12+ bundles into config-*.js and others."""
    patterns = [
        os.path.join(dist_dir, "*.js"),
        os.path.join(dist_dir, "plugin-sdk", "*.js"),
    ]
    all_js = []
    for pat in patterns:
        all_js.extend(f for f in glob.glob(pat) if ".bak" not in f)
    target = []
    for f in sorted(all_js):
        with open(f, "r") as fh:
            if "incrementCompactionCount" in fh.read():
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

    applied_any = False
    for name, old, new in REPLACEMENTS:
        count = content.count(old)
        if count == 0:
            continue  # Try next pattern variant
        if count > 1:
            print(f"  ERROR: pattern for {name} found {count} times (expected 1) in {os.path.basename(filepath)}")
            return False
        content = content.replace(old, new)
        print(f"  Applied: {name}")
        applied_any = True

    if not applied_any:
        print(f"  ERROR: no matching patterns found in {os.path.basename(filepath)}")
        return False

    if content == original:
        print(f"  SKIP (no changes): {os.path.basename(filepath)}")
        return False

    if dry_run:
        print(f"  DRY RUN OK: {os.path.basename(filepath)} — all {len(REPLACEMENTS)} replacements matched")
        return True

    # Backup
    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backup: {os.path.basename(backup)}")

    with open(filepath, "w") as f:
        f.write(content)

    print(f"  PATCHED: {os.path.basename(filepath)} — {len(REPLACEMENTS)} replacements applied")
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
