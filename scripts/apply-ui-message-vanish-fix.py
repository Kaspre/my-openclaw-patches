#!/usr/bin/env python3
"""
Patch: UI User Message Vanish Fix
Issues: openclaw/openclaw#14928, #9183

Preserves optimistic user messages in the control-ui chat when
loadChatHistory() refreshes from the server. Without this patch,
user messages vanish briefly because the server hasn't indexed them yet.

UI-only fix — no gateway/agent runtime impact.

Usage:
  python3 apply-ui-message-vanish-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import re
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-ui-vanish"

# Regex pattern: captures the variable filter function name (e.g. Ts, Qs, etc.)
# between  e.chatMessages=n.filter(s=>!  and  (s)),e.chatThinkingLevel=t.thinkingLevel
PATTERN = re.compile(
    r'e\.chatMessages=n\.filter\(s=>!([A-Za-z_$][A-Za-z0-9_$]*)\(s\)\),e\.chatThinkingLevel=t\.thinkingLevel'
)

ALREADY_PATCHED_MARKER = "_pend"


def build_replacement(fn_name):
    """Build the replacement string using the captured filter function name."""
    return (
        f'const _fm=n.filter(s=>!{fn_name}(s)),'
        f'_om=e.chatMessages,'
        f'_pend=_om.filter(s=>s.role==="user"&&s.timestamp&&!_fm.some(x=>x.role==="user"&&x.timestamp===s.timestamp));'
        f'e.chatMessages=_pend.length>0?[..._fm,..._pend]:_fm,'
        f'e.chatThinkingLevel=t.thinkingLevel'
    )


def find_ui_index_files(dist_dir):
    pattern = os.path.join(dist_dir, "control-ui", "assets", "index-*.js")
    files = [f for f in glob.glob(pattern) if ".bak" not in f]
    return sorted(files)


def apply_patch(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    # Check if already patched
    if ALREADY_PATCHED_MARKER in content:
        print(f"  SKIP (already patched): {basename}")
        return False

    # Find the pattern with regex
    match = PATTERN.search(content)
    if not match:
        print(f"  ERROR: pattern not found in {basename}")
        return False

    fn_name = match.group(1)
    old_text = match.group(0)
    new_text = build_replacement(fn_name)

    # Verify uniqueness
    count = len(PATTERN.findall(content))
    if count > 1:
        print(f"  ERROR: pattern found {count} times (expected 1) in {basename}")
        return False

    content = content.replace(old_text, new_text, 1)

    if dry_run:
        print(f"  DRY RUN OK: {basename} — filter function: {fn_name}")
        return True

    # Backup
    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backup: {os.path.basename(backup)}")

    with open(filepath, "w") as f:
        f.write(content)

    print(f"  PATCHED: {basename} — filter function: {fn_name}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Apply UI user message vanish fix")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_ui_index_files(args.dist_dir)
    if not files:
        print(f"ERROR: No control-ui/assets/index-*.js files found in {args.dist_dir}")
        sys.exit(1)

    print(f"UI Message Vanish Fix {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Found {len(files)} control-ui index file(s)")
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
