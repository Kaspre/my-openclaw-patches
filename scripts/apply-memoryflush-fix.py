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
OLD_INCREMENT = """\t\t\tconst nextCount = await incrementCompactionCount({
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath
\t\t\t});
\t\t\tif (typeof nextCount === "number") memoryFlushCompactionCount = nextCount;"""

NEW_INCREMENT = """\t\t\tawait incrementCompactionCount({
\t\t\t\tsessionEntry: activeSessionEntry,
\t\t\t\tsessionStore: activeSessionStore,
\t\t\t\tsessionKey: params.sessionKey,
\t\t\t\tstorePath: params.storePath
\t\t\t});
\t\t\t// FIX #12590: Do NOT reassign memoryFlushCompactionCount to post-increment value.
\t\t\t// Keep it at pre-increment (N) so next cycle's compactionCount (N+1) won't match,
\t\t\t// allowing flush to fire on every compaction instead of every other."""

REPLACEMENTS = [
    ("incrementCompactionCount + counter reassignment removal", OLD_INCREMENT, NEW_INCREMENT),
]


def find_compact_files(dist_dir):
    pattern = os.path.join(dist_dir, "compact-*.js")
    files = [f for f in glob.glob(pattern) if ".bak" not in f]
    return sorted(files)


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    original = content

    # Check if already patched
    if "FIX #12590" in content:
        print(f"  SKIP (already patched): {os.path.basename(filepath)}")
        return False

    for name, old, new in REPLACEMENTS:
        count = content.count(old)
        if count == 0:
            print(f"  ERROR: pattern not found for {name} in {os.path.basename(filepath)}")
            return False
        if count > 1:
            print(f"  ERROR: pattern for {name} found {count} times (expected 1) in {os.path.basename(filepath)}")
            return False
        content = content.replace(old, new)

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

    files = find_compact_files(args.dist_dir)
    if not files:
        print(f"ERROR: No compact-*.js files found in {args.dist_dir}")
        sys.exit(1)

    print(f"memoryFlush Skip-Every-Other Fix {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Found {len(files)} compact file(s)")
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
