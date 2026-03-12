#!/usr/bin/env python3
"""
Patch: Approval Description Channel Routing
Related: openclaw/openclaw#28753

Fixes approval description embeds appearing in wrong Discord channel.
Adds turnSourceChannel check to DiscordExecApprovalHandler.shouldHandle()
so non-Discord requests are skipped.

Usage:
  python3 apply-approval-desc-routing.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-approval-desc"

# --- Replacement pattern ---
# The unique anchor: end of shouldHandle() into start of async start()
# Uses real tabs as in the minified bundle

OLD_SHOULD_HANDLE = "\t\t}\n\t\treturn true;\n\t}\n\tasync start() {"

NEW_SHOULD_HANDLE = "\t\t}\n\t\tconst turnSource = request.request?.turnSourceChannel;\n\t\tif (turnSource && turnSource !== \"discord\") return false;\n\t\treturn true;\n\t}\n\tasync start() {"

# File glob patterns to search
FILE_PATTERNS = [
    "reply-*.js",
    "compact-*.js",
    "pi-embedded-*.js",
    "plugin-sdk/dispatch-*.js",
    "plugin-sdk/reply-*.js",
]


def find_target_files(dist_dir):
    files = []
    for pattern in FILE_PATTERNS:
        full_pattern = os.path.join(dist_dir, pattern)
        for f in glob.glob(full_pattern):
            if ".bak" not in f:
                files.append(f)
    return sorted(files)


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    original = content

    # Check if already patched
    if "turnSourceChannel" in content:
        print(f"  SKIP (already patched): {os.path.relpath(filepath, os.path.dirname(filepath))}")
        return False

    count = content.count(OLD_SHOULD_HANDLE)
    if count == 0:
        # This file doesn't contain the pattern (no DiscordExecApprovalHandler)
        return None
    if count > 1:
        print(f"  ERROR: pattern found {count} times (expected 1) in {os.path.basename(filepath)}")
        return False

    content = content.replace(OLD_SHOULD_HANDLE, NEW_SHOULD_HANDLE)

    if content == original:
        print(f"  SKIP (no changes): {os.path.basename(filepath)}")
        return False

    if dry_run:
        print(f"  DRY RUN OK: {os.path.basename(filepath)}")
        return True

    # Backup
    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backup: {os.path.basename(backup)}")

    with open(filepath, "w") as f:
        f.write(content)

    print(f"  PATCHED: {os.path.basename(filepath)}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Apply approval description channel routing fix")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_target_files(args.dist_dir)
    if not files:
        print(f"ERROR: No matching files found in {args.dist_dir}")
        sys.exit(1)

    print(f"Approval Description Channel Routing Fix {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Scanning {len(files)} candidate file(s)")
    print()

    patched = 0
    skipped = 0
    no_pattern = 0
    for f in files:
        result = apply_patch(f, dry_run=args.dry_run)
        if result is True:
            patched += 1
        elif result is False:
            skipped += 1
        elif result is None:
            no_pattern += 1

    print()
    total_relevant = patched + skipped
    if args.dry_run:
        print(f"Dry run complete: {patched} files would be patched, {skipped} already patched/skipped, {no_pattern} files without pattern")
    else:
        print(f"Done: {patched} files patched, {skipped} already patched/skipped, {no_pattern} files without pattern")
        if patched > 0:
            print("Restart gateway: systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
