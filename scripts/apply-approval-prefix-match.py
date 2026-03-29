#!/usr/bin/env python3
"""
Patch: Approval Prefix Matching
Issue: openclaw/openclaw#9591
PRs: #9641 (closed), #10001 (open)

Adds _findPending() helper to ExecApprovalManager so /approve works with
8-char slugs (TUI, SSH, Telegram) instead of requiring full UUIDs.

Usage:
  python3 apply-approval-prefix-match.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-approval-prefix"

# --- The _findPending method to insert before resolve() ---
FIND_PENDING_METHOD = """\t_findPending(recordId) {
\t\tconst exact = this.pending.get(recordId);
\t\tif (exact) return { key: recordId, entry: exact };
\t\tif (recordId.length >= 36) return null;
\t\tlet match = null;
\t\tfor (const [key, val] of this.pending) {
\t\t\tif (key.startsWith(recordId)) {
\t\t\t\tif (match) return null;
\t\t\t\tmatch = { key, entry: val };
\t\t\t}
\t\t}
\t\treturn match;
\t}
"""

# --- Replacement patterns ---

# 1. Insert _findPending before resolve()
RESOLVE_ANCHOR_OLD = """\tresolve(recordId, decision, resolvedBy) {
\t\tconst pending = this.pending.get(recordId);
\t\tif (!pending) return false;"""

RESOLVE_ANCHOR_NEW = FIND_PENDING_METHOD + """\tresolve(recordId, decision, resolvedBy) {
\t\tconst _found = this._findPending(recordId);
\t\tif (!_found) return false;
\t\tconst pending = _found.entry;
\t\trecordId = _found.key;"""

# 2. resolve() cleanup timeout — use canonical key
RESOLVE_CLEANUP_OLD = """\t\tsetTimeout(() => {
\t\t\tif (this.pending.get(recordId) === pending) this.pending.delete(recordId);
\t\t}, RESOLVED_ENTRY_GRACE_MS);
\t\treturn true;
\t}
\texpire(recordId"""

RESOLVE_CLEANUP_NEW = """\t\tsetTimeout(() => {
\t\t\tif (this.pending.get(recordId) === pending) this.pending.delete(recordId);
\t\t}, RESOLVED_ENTRY_GRACE_MS);
\t\treturn true;
\t}
\texpire(recordId"""
# No change needed here — recordId is already reassigned to canonical key above

# 3. expire() method
EXPIRE_OLD = """\texpire(recordId, resolvedBy) {
\t\tconst pending = this.pending.get(recordId);
\t\tif (!pending) return false;"""

EXPIRE_NEW = """\texpire(recordId, resolvedBy) {
\t\tconst _found = this._findPending(recordId);
\t\tif (!_found) return false;
\t\tconst pending = _found.entry;
\t\trecordId = _found.key;"""

# 4. expire() cleanup timeout — same pattern, no change needed (recordId reassigned)

# 5. getSnapshot()
GETSNAPSHOT_OLD = """\tgetSnapshot(recordId) {
\t\treturn this.pending.get(recordId)?.record ?? null;
\t}"""

GETSNAPSHOT_NEW = """\tgetSnapshot(recordId) {
\t\tconst _found = this._findPending(recordId);
\t\treturn _found?.entry?.record ?? null;
\t}"""

# 6. consumeAllowOnce()
CONSUME_OLD = """\tconsumeAllowOnce(recordId) {
\t\tconst entry = this.pending.get(recordId);
\t\tif (!entry) return false;"""

CONSUME_NEW = """\tconsumeAllowOnce(recordId) {
\t\tconst _found = this._findPending(recordId);
\t\tif (!_found) return false;
\t\tconst entry = _found.entry;
\t\trecordId = _found.key;"""

# 7. awaitDecision()
AWAIT_OLD = """\tawaitDecision(recordId) {
\t\treturn this.pending.get(recordId)?.promise ?? null;
\t}"""

AWAIT_NEW = """\tawaitDecision(recordId) {
\t\tconst _found = this._findPending(recordId);
\t\treturn _found?.entry?.promise ?? null;
\t}"""

REPLACEMENTS = [
    ("resolve() + _findPending insert", RESOLVE_ANCHOR_OLD, RESOLVE_ANCHOR_NEW),
    ("expire()", EXPIRE_OLD, EXPIRE_NEW),
    ("getSnapshot()", GETSNAPSHOT_OLD, GETSNAPSHOT_NEW),
    ("consumeAllowOnce()", CONSUME_OLD, CONSUME_NEW),
    ("awaitDecision()", AWAIT_OLD, AWAIT_NEW),
]


def find_gateway_cli_files(dist_dir):
    pattern = os.path.join(dist_dir, "gateway-cli-*.js")
    files = [f for f in glob.glob(pattern) if ".bak" not in f]
    return sorted(files)


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    original = content

    # Check if already patched
    if "_findPending" in content:
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
    parser = argparse.ArgumentParser(description="Apply approval prefix-match patch")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_gateway_cli_files(args.dist_dir)
    if not files:
        print(f"ERROR: No gateway-cli-*.js files found in {args.dist_dir}")
        sys.exit(1)

    print(f"Approval Prefix-Match Patch {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Found {len(files)} gateway-cli file(s)")
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
