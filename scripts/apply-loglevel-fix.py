#!/usr/bin/env python3
"""
Patch: Fix logging level config (levelToMinLevel mapping, child logger inheritance,
       subsystem console filter) — matches upstream PR #44646.

Issues: openclaw/openclaw#29448, #47529 / PR #44646

Four bugs in the logging system prevent logging.level config from working:

1. levelToMinLevel mapping is inverted relative to tslog v4's logLevelId convention.
   tslog: 0=silly, 1=trace, 2=debug, 3=info, 4=warn, 5=error, 6=fatal.
   OC ships: fatal=0, error=1, warn=2, info=3, debug=4, trace=5.
   So levelToMinLevel("debug")=4 is interpreted by tslog as "warn and above."

2. isFileLogLevelEnabled uses <= comparison which is inverted for the same reason.

3. getChildLogger spreads minLevel: undefined when no level is specified, which
   overrides the parent logger's configured minLevel. All child loggers (cron,
   plugins, subsystems) ignore the configured level.

4. toPinoLikeLogger creates sub-loggers without inheriting parent minLevel.

5. shouldLogToConsole in subsystem uses <= comparison (same as #2).

This patch aligns with PR #44646 which fixes all five issues.

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

# Fix 1: Invert the mapping to match tslog v4 logLevelId
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

# Fix 2: Flip comparison direction in isFileLogLevelEnabled
OLD_FILE_COMPARISON = "return levelToMinLevel(level) <= levelToMinLevel(settings.level);"
NEW_FILE_COMPARISON = "return levelToMinLevel(level) >= levelToMinLevel(settings.level);"

# Fix 3: Child logger inherits parent minLevel instead of spreading undefined
OLD_CHILD_LOGGER = "const minLevel = opts?.level ? levelToMinLevel(opts.level) : void 0;"
NEW_CHILD_LOGGER = "const minLevel = opts?.level ? levelToMinLevel(opts.level) : base.settings.minLevel;"

# Fix 4: toPinoLikeLogger inherits parent minLevel
OLD_PINO_SUBLOGGER = "const buildChild = (bindings) => toPinoLikeLogger(logger.getSubLogger({ name: bindings ? JSON.stringify(bindings) : void 0 }), level);"
NEW_PINO_SUBLOGGER = "const buildChild = (bindings) => toPinoLikeLogger(logger.getSubLogger({ name: bindings ? JSON.stringify(bindings) : void 0, minLevel: logger.settings.minLevel }), level);"

# Fix 5: shouldLogToConsole comparison direction (in subsystem file)
OLD_CONSOLE_COMPARISON = "return levelToMinLevel(level) <= levelToMinLevel(settings.level);"
NEW_CONSOLE_COMPARISON = "return levelToMinLevel(level) >= levelToMinLevel(settings.level);"
# Note: same string as Fix 2, but appears in a different file (subsystem-*.js)

# File patterns to search
FILE_PATTERNS = [
    "logger-*.js",
    "subsystem-*.js",
    "utils-*.js",
    "plugin-sdk/logger-*.js",
    "plugin-sdk/subsystem-*.js",
]

# All fix patterns: (old, new, description)
FIX_PATTERNS = [
    (OLD_MAPPING, NEW_MAPPING, "mapping inversion"),
    (OLD_FILE_COMPARISON, NEW_FILE_COMPARISON, "comparison direction"),
    (OLD_CHILD_LOGGER, NEW_CHILD_LOGGER, "child logger minLevel inheritance"),
    (OLD_PINO_SUBLOGGER, NEW_PINO_SUBLOGGER, "pino sub-logger minLevel inheritance"),
    # Note: OLD_CONSOLE_COMPARISON == OLD_FILE_COMPARISON, so Fix 2 handles both
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
    """Apply all loglevel fixes to a single file. Returns (patched, already_patched, skipped)."""
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)
    changes = []
    new_content = content

    for old, new, desc in FIX_PATTERNS:
        count = new_content.count(old)
        already = new_content.count(new)
        if count > 0:
            new_content = new_content.replace(old, new)
            changes.append(f"{desc} x{count}")
        elif already > 0:
            pass  # Already patched for this pattern

    if not changes:
        # Check if any new patterns exist (already patched)
        any_new = any(content.count(new) > 0 for _, new, _ in FIX_PATTERNS)
        if any_new:
            print(f"  {basename}: already patched")
            return False, True, False
        print(f"  {basename}: no matching patterns found, skipping")
        return False, False, True

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
    parser = argparse.ArgumentParser(description="Fix logging level config (PR #44646)")
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

    print(f"{'DRY RUN — ' if args.dry_run else ''}Patching loglevel (PR #44646) in {len(files)} files:")

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
