#!/usr/bin/env python3
"""
Patch: Redact API keys from cache-trace diagnostic logs
Security: API key (sk-ant-oat01-...) logged in plaintext to cache-trace.jsonl

The cache trace writer passes LLM client options through redactImageDataForDiagnostics()
which strips image data but leaves apiKey, token, password, and secretKey intact.

Usage:
  python3 apply-cache-trace-redact-apikey.py [--dry-run] [--dist-dir PATH] [--restore]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-trace-redact"

OLD = 'if (payload.options) event.options = redactImageDataForDiagnostics(payload.options);'
NEW = (
    'if (payload.options) {'
    ' const _trOpts = redactImageDataForDiagnostics(payload.options);'
    ' if (_trOpts && typeof _trOpts === "object") {'
    ' delete _trOpts.apiKey; delete _trOpts.token;'
    ' delete _trOpts.password; delete _trOpts.secretKey;'
    ' }'
    ' event.options = _trOpts;'
    ' }'
)


def find_files(dist_dir):
    files = []
    for js_file in sorted(glob.glob(os.path.join(dist_dir, "**", "*.js"), recursive=True)):
        if ".bak-" in js_file or ".bak." in js_file:
            continue
        if not os.path.isfile(js_file):
            continue
        with open(js_file, "r") as f:
            content = f.read()
        if OLD in content:
            files.append(js_file)
    return files


def restore_backups(dist_dir):
    restored = 0
    for bak_file in sorted(glob.glob(os.path.join(dist_dir, f"**/*{BACKUP_SUFFIX}"), recursive=True)):
        original = bak_file[: -len(BACKUP_SUFFIX)]
        shutil.copy2(bak_file, original)
        os.remove(bak_file)
        print(f"  Restored: {os.path.basename(original)}")
        restored += 1
    return restored


def main():
    parser = argparse.ArgumentParser(description="Redact API keys from cache-trace logs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    dist_dir = args.dist_dir
    if not os.path.isdir(dist_dir):
        print(f"ERROR: dist directory not found: {dist_dir}", file=sys.stderr)
        sys.exit(1)

    if args.restore:
        print("Mode: RESTORE")
        count = restore_backups(dist_dir)
        print(f"Restored {count} files.")
        return

    print(f"Mode: {'DRY RUN' if args.dry_run else 'APPLY'}")

    files = find_files(dist_dir)
    print(f"Found {len(files)} files with unredacted cache-trace options:")

    for filepath in files:
        basename = os.path.basename(filepath)
        with open(filepath, "r") as f:
            content = f.read()

        if NEW in content:
            print(f"  {basename}: already patched")
            continue

        backup = filepath + BACKUP_SUFFIX
        if not os.path.exists(backup) and not args.dry_run:
            shutil.copy2(filepath, backup)

        content = content.replace(OLD, NEW, 1)
        if not args.dry_run:
            with open(filepath, "w") as f:
                f.write(content)
        print(f"  {basename}: \u2705 redact apiKey/token/password/secretKey from trace options")

    if args.dry_run:
        print("\nDRY RUN complete.")
    else:
        print("\nPatch applied. Restart gateway to activate.")


if __name__ == "__main__":
    main()
