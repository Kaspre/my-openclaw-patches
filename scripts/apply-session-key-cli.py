#!/usr/bin/env python3
"""
Patch: --session-key CLI flag
PR: openclaw/openclaw#35241

Adds --session-key <key> flag to `openclaw agent` for true session isolation.
Wires the CLI option through to resolveSessionKeyForRequest().

Usage:
  python3 apply-session-key-cli.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.local/node-current/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-sessionkey"

# --- Replacement patterns (3 edits per file) ---

# Edit 1: Add --session-key option after --session-id option
OLD_OPTION = '.option("--session-id <id>", "Use an explicit session id").option("--agent <id>"'
NEW_OPTION = '.option("--session-id <id>", "Use an explicit session id").option("--session-key <key>", "Use an explicit session key").option("--agent <id>"'

# Edit 2: Validation guard — add !opts.sessionKey
OLD_GUARD = 'if (!opts.to && !opts.sessionId && !opts.agent) throw new Error("Pass --to <E.164>, --session-id, or --agent to choose a session")'
NEW_GUARD = 'if (!opts.to && !opts.sessionId && !opts.sessionKey && !opts.agent) throw new Error("Pass --to <E.164>, --session-id, --session-key, or --agent to choose a session")'

# Edit 3: Add sessionKey to resolveSessionKeyForRequest call
OLD_RESOLVE = "\tconst sessionKey = resolveSessionKeyForRequest({\n\t\tcfg,\n\t\tagentId,\n\t\tto: opts.to,\n\t\tsessionId: opts.sessionId\n\t}).sessionKey;"
NEW_RESOLVE = "\tconst sessionKey = resolveSessionKeyForRequest({\n\t\tcfg,\n\t\tagentId,\n\t\tto: opts.to,\n\t\tsessionId: opts.sessionId,\n\t\tsessionKey: opts.sessionKey\n\t}).sessionKey;"

REPLACEMENTS = [
    ("--session-key option registration", OLD_OPTION, NEW_OPTION),
    ("validation guard", OLD_GUARD, NEW_GUARD),
    ("resolveSessionKeyForRequest call", OLD_RESOLVE, NEW_RESOLVE),
]


def find_register_agent_files(dist_dir):
    pattern = os.path.join(dist_dir, "register.agent-*.js")
    files = [f for f in glob.glob(pattern) if ".bak" not in f]
    return sorted(files)


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    original = content

    # Check if already patched
    if "--session-key" in content:
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
    parser = argparse.ArgumentParser(description="Apply --session-key CLI flag patch")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_register_agent_files(args.dist_dir)
    if not files:
        print(f"ERROR: No register.agent-*.js files found in {args.dist_dir}")
        sys.exit(1)

    print(f"Session Key CLI Flag Patch {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Found {len(files)} register.agent file(s)")
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
