#!/usr/bin/env python3
"""
Patch: Exec Host Enforcement Override
Issue: openclaw/openclaw#11150
PR: #11185

Prevents the exec tool from throwing an error when the model requests
host: "sandbox" but the configured host is "gateway". Instead of erroring,
it silently overrides with the configured host.

Usage:
  python3 apply-exec-host-override.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.local/node-current/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-exec-host"

SEARCH_PATTERN = 'requestedHost !== configuredHost) throw new Error'

# The full old pattern — single line in minified JS (v2026.3.12+)
THROW_OLD = (
    'if (!elevatedRequested && requestedHost && requestedHost !== configuredHost)'
    ' throw new Error(`exec host not allowed (requested ${renderExecHostLabel(requestedHost)}'
    '; configure tools.exec.host=${renderExecHostLabel(configuredHost)} to allow).`);'
)

THROW_NEW = (
    'if (!elevatedRequested && requestedHost && requestedHost !== configuredHost)'
    ' host = configuredHost;'
)


def find_js_files(dist_dir):
    """Find all .js files in dist/ and dist/plugin-sdk/, excluding .bak files."""
    patterns = [
        os.path.join(dist_dir, "*.js"),
        os.path.join(dist_dir, "plugin-sdk", "*.js"),
    ]
    files = []
    for pattern in patterns:
        files.extend(f for f in glob.glob(pattern) if ".bak" not in f)
    return sorted(files)


def file_has_pattern(filepath):
    """Check if a file contains the search pattern."""
    with open(filepath, "r") as f:
        return SEARCH_PATTERN in f.read()


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    # Check if already patched (throw replaced with assignment)
    if SEARCH_PATTERN not in content:
        # Pattern not present — either already patched or not a target file
        # Check for the replacement to distinguish
        if "requestedHost !== configuredHost)" in content and "host = configuredHost;" in content:
            print(f"  SKIP (already patched): {os.path.basename(filepath)}")
            return False
        # Not a target file at all (shouldn't happen since we pre-filter)
        print(f"  SKIP (pattern not found): {os.path.basename(filepath)}")
        return False

    count = content.count(THROW_OLD)
    if count == 0:
        print(f"  ERROR: search pattern matched but full throw pattern not found in {os.path.basename(filepath)}")
        print(f"         The throw statement may have changed format. Manual inspection needed.")
        return False
    if count > 1:
        print(f"  ERROR: throw pattern found {count} times (expected 1) in {os.path.basename(filepath)}")
        return False

    patched_content = content.replace(THROW_OLD, THROW_NEW)

    if dry_run:
        print(f"  DRY RUN OK: {os.path.basename(filepath)}")
        return True

    # Backup
    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backup: {os.path.basename(backup)}")

    with open(filepath, "w") as f:
        f.write(patched_content)

    print(f"  PATCHED: {os.path.basename(filepath)}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Apply exec host enforcement override patch")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist directory not found: {args.dist_dir}")
        sys.exit(1)

    # Find all JS files, then filter to only those containing the pattern
    all_js = find_js_files(args.dist_dir)
    target_files = [f for f in all_js if file_has_pattern(f)]

    print(f"Exec Host Override Patch {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Scanned {len(all_js)} .js files, found {len(target_files)} containing target pattern")
    print()

    if not target_files:
        # Check if already patched everywhere
        already_patched = []
        for f in all_js:
            with open(f, "r") as fh:
                c = fh.read()
            if "requestedHost !== configuredHost)" in c and "host = configuredHost;" in c:
                already_patched.append(f)
        if already_patched:
            print(f"All {len(already_patched)} target files are already patched.")
            sys.exit(0)
        else:
            print("ERROR: No files found with the exec host throw pattern.")
            print("The pattern may have changed in this OpenClaw version.")
            sys.exit(1)

    patched = 0
    for f in target_files:
        if apply_patch(f, dry_run=args.dry_run):
            patched += 1

    print()
    if args.dry_run:
        print(f"Dry run complete: {patched}/{len(target_files)} files would be patched")
    else:
        print(f"Done: {patched}/{len(target_files)} files patched")
        if patched > 0:
            print("Restart gateway: systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
