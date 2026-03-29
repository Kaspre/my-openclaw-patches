#!/usr/bin/env python3
"""
Patch: Fix inverted levelToMinLevel mapping in logger
Issue: openclaw/openclaw#29448 / PR #44646

The levelToMinLevel function maps log level names to numeric values that tslog
uses as minLevel. The shipped mapping is inverted: fatal=0, trace=5. But tslog
treats lower minLevel = more permissive, so setting logging.level="debug" gives
minLevel=4, which is WARN level — silently dropping debug/trace entries.

Also fixes the comparison in isFileLogLevelEnabled and shouldLogToConsole from
<= to >= to match the corrected mapping direction.

This patch:
1. Inverts the mapping: fatal=6, error=5, warn=4, info=3, debug=2, trace=1
2. Changes <= to >= in levelToMinLevel comparisons

Usage:
  python3 apply-loglevel-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-loglevel"

# --- Replacement patterns ---

OLD_MAPPING = """\t\tfatal: 0,
\t\terror: 1,
\t\twarn: 2,
\t\tinfo: 3,
\t\tdebug: 4,
\t\ttrace: 5,"""

NEW_MAPPING = """\t\tfatal: 6,
\t\terror: 5,
\t\twarn: 4,
\t\tinfo: 3,
\t\tdebug: 2,
\t\ttrace: 1,"""

OLD_COMPARISON = "return levelToMinLevel(level) <= levelToMinLevel(settings.level);"
NEW_COMPARISON = "return levelToMinLevel(level) >= levelToMinLevel(settings.level);"

# File patterns to search
FILE_PATTERNS = [
    "logger-*.js",
    "subsystem-*.js",
    "utils-*.js",
    "plugin-sdk/logger-*.js",
    "plugin-sdk/subsystem-*.js",
]


def find_files(dist_dir):
    """Find all JS files matching our patterns (excluding backups)."""
    files = []
    for pattern in FILE_PATTERNS:
        full_pattern = os.path.join(dist_dir, pattern)
        for f in glob.glob(full_pattern):
            if not f.endswith(BACKUP_SUFFIX):
                files.append(f)
    return sorted(files)


def patch_file(filepath, dry_run=False):
    """Apply the loglevel fix to a single file. Returns (patched, already_patched, skipped)."""
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)
    mapping_count = content.count(OLD_MAPPING)
    comparison_count = content.count(OLD_COMPARISON)
    already_mapping = content.count(NEW_MAPPING)
    already_comparison = content.count(NEW_COMPARISON)

    if mapping_count == 0 and comparison_count == 0:
        if already_mapping > 0 or already_comparison > 0:
            print(f"  {basename}: already patched (mapping={already_mapping}, comparison={already_comparison})")
            return False, True, False
        print(f"  {basename}: no matching patterns found, skipping")
        return False, False, True

    changes = []
    new_content = content

    if mapping_count > 0:
        new_content = new_content.replace(OLD_MAPPING, NEW_MAPPING)
        changes.append(f"mapping x{mapping_count}")

    if comparison_count > 0:
        new_content = new_content.replace(OLD_COMPARISON, NEW_COMPARISON)
        changes.append(f"comparison x{comparison_count}")

    if dry_run:
        print(f"  {basename}: WOULD patch ({', '.join(changes)})")
        return True, False, False

    # Backup
    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)

    with open(filepath, "w") as f:
        f.write(new_content)

    print(f"  {basename}: patched ({', '.join(changes)})")
    return True, False, False


def main():
    parser = argparse.ArgumentParser(description="Fix inverted levelToMinLevel mapping")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist directory not found: {args.dist_dir}")
        sys.exit(1)

    files = find_files(args.dist_dir)
    if not files:
        print("ERROR: no matching files found")
        sys.exit(1)

    print(f"{'DRY RUN — ' if args.dry_run else ''}Patching levelToMinLevel in {len(files)} files:")

    patched = 0
    already = 0
    skipped = 0

    for f in files:
        p, a, s = patch_file(f, args.dry_run)
        patched += p
        already += a
        skipped += s

    print(f"\nResults: {patched} patched, {already} already patched, {skipped} skipped")

    if patched > 0 and not args.dry_run:
        print("\nRestart the gateway to activate:")
        print("  systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
