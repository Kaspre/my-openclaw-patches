#!/usr/bin/env python3
"""
Patch: Approval Auto-Expire Fix
Issue: Discord native approval buttons cause instant expiry with
       "auto-expire:no-approver-clients" because hasExecApprovalClients()
       only checks connected gateway clients, not Discord config.

Adds 2 lines to hasExecApprovalClients() in gateway-cli files so it also
returns true when Discord native exec approvals are enabled in config.

Usage:
  python3 apply-approval-auto-expire-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-autoexpire"

# --- Replacement patterns (tab-indented to match bundle code) ---

PATCH_OLD = """\t\t\thasExecApprovalClients: () => {
\t\t\t\tfor (const gatewayClient of clients) {
\t\t\t\t\tconst scopes = Array.isArray(gatewayClient.connect.scopes) ? gatewayClient.connect.scopes : [];
\t\t\t\t\tif (scopes.includes("operator.admin") || scopes.includes("operator.approvals")) return true;
\t\t\t\t}
\t\t\t\treturn false;
\t\t\t},"""

PATCH_NEW = """\t\t\thasExecApprovalClients: () => {
\t\t\t\tfor (const gatewayClient of clients) {
\t\t\t\t\tconst scopes = Array.isArray(gatewayClient.connect.scopes) ? gatewayClient.connect.scopes : [];
\t\t\t\t\tif (scopes.includes("operator.admin") || scopes.includes("operator.approvals")) return true;
\t\t\t\t}
\t\t\t\tconst discordCfg = cfgAtStart?.channels?.discord;
\t\t\t\tif (discordCfg?.execApprovals?.enabled && discordCfg?.enabled !== false) return true;
\t\t\t\treturn false;
\t\t\t},"""

REPLACEMENTS = [
    ("hasExecApprovalClients()", PATCH_OLD, PATCH_NEW),
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
    if "discordCfg?.execApprovals" in content:
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
    parser = argparse.ArgumentParser(description="Apply approval auto-expire fix patch")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_gateway_cli_files(args.dist_dir)
    if not files:
        print(f"ERROR: No gateway-cli-*.js files found in {args.dist_dir}")
        sys.exit(1)

    print(f"Approval Auto-Expire Fix {'(DRY RUN)' if args.dry_run else ''}")
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
