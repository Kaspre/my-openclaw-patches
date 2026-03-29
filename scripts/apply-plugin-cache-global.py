#!/usr/bin/env python3
"""
Patch: Process-global plugin registry cache (fixes cross-chunk cache isolation)
Issue: openclaw/openclaw#47429 / #48380

The bundler duplicates loadOpenClawPlugins into multiple output chunks, each
with its own module-scoped registryCache Map. Even with identical cache keys,
a call from one chunk cannot see the cache populated by another chunk, causing
redundant full plugin discovery + activation cycles.

This patch replaces the module-scoped registryCache with a process-global one
on globalThis, so all chunks share a single cache.

This matters for plugins that spawn child processes (e.g. MCP bridge), where
each redundant activation spawns duplicate server processes.

Usage:
  python3 apply-plugin-cache-global.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-pluginfix"

# --- Replacement patterns ---

OLD_PATTERN = "const registryCache = /* @__PURE__ */ new Map();"
NEW_PATTERN = "const registryCache = (globalThis.__ocPluginRegistryCache ??= new Map());"

# File patterns to search — all chunks that contain loadOpenClawPlugins
FILE_PATTERNS = [
    "reply-*.js",
    "auth-profiles-*.js",
    "discord-*.js",
    "model-selection-*.js",
    "skills-*.js",
    "plugin-sdk/thread-bindings-*.js",
    # Catch-all for future chunk names
    "server-*.js",
    "agents-*.js",
]


def find_files(dist_dir):
    """Find all JS files matching our patterns (excluding backups)."""
    files = []
    for pattern in FILE_PATTERNS:
        full_pattern = os.path.join(dist_dir, pattern)
        for f in glob.glob(full_pattern):
            if not f.endswith(BACKUP_SUFFIX) and not f.endswith(".bak"):
                files.append(f)
    return sorted(set(files))


def patch_file(filepath, dry_run=False):
    """Apply the registry cache fix to a single file. Returns (patched, already_patched, skipped)."""
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    # Check if this file even contains the plugin loader
    if "function loadOpenClawPlugins" not in content and OLD_PATTERN not in content:
        # Not a relevant file
        return False, False, True

    old_count = content.count(OLD_PATTERN)
    already_count = content.count(NEW_PATTERN)

    if old_count == 0:
        if already_count > 0:
            print(f"  {basename}: already patched")
            return False, True, False
        # Has loadOpenClawPlugins but not our exact pattern — may be a different version
        if "function loadOpenClawPlugins" in content:
            print(f"  {basename}: contains loadOpenClawPlugins but pattern not found (version mismatch?)")
        return False, False, True

    if old_count != 1:
        print(f"  {basename}: WARNING — found {old_count} occurrences (expected 1), patching all")

    if dry_run:
        print(f"  {basename}: WOULD patch (registryCache x{old_count})")
        return True, False, False

    # Backup
    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)

    new_content = content.replace(OLD_PATTERN, NEW_PATTERN)
    with open(filepath, "w") as f:
        f.write(new_content)

    print(f"  {basename}: patched (registryCache x{old_count})")
    return True, False, False


def main():
    parser = argparse.ArgumentParser(description="Fix cross-chunk plugin registry cache isolation")
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

    print(f"{'DRY RUN — ' if args.dry_run else ''}Patching plugin registryCache in {len(files)} files:")

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
