#!/usr/bin/env python3
"""
Patch: Prevent duplicate cron job execution after gateway restart
Issue: openclaw/openclaw#42640 / PR #42729

After gateway restart, the cron scheduler re-executes jobs that already ran
before the restart. The root cause: isRunnableJob treats stale nextRunAtMs
values (where nextRunAtMs <= now) as due without checking whether they were
already executed (lastRunAtMs > nextRunAtMs).

This patch adds a guard in isRunnableJob: if lastRunAtMs > nextRunAtMs, the
expired slot was already executed — skip it. Uses strict > (not >=) so that
retry slots finishing in the same millisecond are still permitted.

Mirrors existing logic in recomputeNextRunsForMaintenance (jobs.ts line 469).

Usage:
  python3 apply-cron-duplicate-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-cron-dup"

# The target file contains isRunnableJob. In v2026.4.9 it moved from
# gateway-cli-*.js to server.impl-*.js. We glob for both and pick whichever
# contains the actual function body.
TARGET_FILENAME = "server.impl-*.js"  # legacy: "gateway-cli-ChUE8Mp7.js"

# --- Replacement patterns ---
# The original line returns true unconditionally when nowMs >= next.
# The fix adds a guard: skip if lastRunAtMs > next (already executed this slot).

OLD_CODE = (
    'if (typeof next === "number" && Number.isFinite(next) && nowMs >= next) return true;'
)

NEW_CODE = (
    'if (typeof next === "number" && Number.isFinite(next) && nowMs >= next) {\n'
    '\t\tconst lastRunAtMs = job.state.lastRunAtMs;\n'
    '\t\tif (!(typeof lastRunAtMs === "number" && Number.isFinite(lastRunAtMs) && lastRunAtMs > next)) return true;\n'
    '\t}'
)


def find_target(dist_dir):
    """Find the bundle file containing isRunnableJob.

    History:
      - v2026.4.8 and earlier: function lives in gateway-cli-*.js
      - v2026.4.9+: function moved to server.impl-*.js
    Try both patterns and pick whichever contains the function.
    """
    import glob
    patterns = ["server.impl-*.js", "gateway-cli-*.js"]
    for pattern in patterns:
        candidates = glob.glob(os.path.join(dist_dir, pattern))
        candidates = [c for c in candidates if not c.endswith(BACKUP_SUFFIX)]
        for c in candidates:
            try:
                with open(c, "r") as f:
                    content = f.read()
                if "function isRunnableJob" in content or "isRunnableJob(" in content:
                    # Require the specific OLD_CODE or ALREADY_PATCHED marker too
                    if OLD_CODE in content or "lastRunAtMs > next)) return true;" in content:
                        return c
            except OSError:
                pass
    return None


def patch_file(filepath, dry_run=False):
    """Apply the cron duplicate fix. Returns (patched, already_patched, error_msg)."""
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    # Check if already patched
    if "lastRunAtMs > next)) return true;" in content:
        return False, True, None

    # Verify the old code exists
    count = content.count(OLD_CODE)
    if count == 0:
        return False, False, f"old pattern not found in {basename} — code may have changed upstream"
    if count > 1:
        return False, False, f"old pattern found {count} times in {basename} — expected exactly 1"

    if dry_run:
        # Find the line number for reporting
        lines = content[:content.index(OLD_CODE)].count("\n") + 1
        print(f"  {basename}:{lines}: WOULD patch isRunnableJob (1 replacement)")
        return True, False, None

    # Backup
    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)

    new_content = content.replace(OLD_CODE, NEW_CODE, 1)

    with open(filepath, "w") as f:
        f.write(new_content)

    lines = content[:content.index(OLD_CODE)].count("\n") + 1
    print(f"  {basename}:{lines}: patched isRunnableJob (added hasAlreadyExecutedScheduledSlot guard)")
    return True, False, None


def main():
    parser = argparse.ArgumentParser(
        description="Fix duplicate cron job execution after gateway restart (#42640)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist directory not found: {args.dist_dir}")
        sys.exit(1)

    target = find_target(args.dist_dir)
    if not target:
        print(f"ERROR: gateway bundle not found in {args.dist_dir}")
        print("  Expected: gateway-cli-*.js containing isRunnableJob")
        sys.exit(1)

    print(f"{'DRY RUN — ' if args.dry_run else ''}Patching cron duplicate execution fix:")

    patched, already, error = patch_file(target, args.dry_run)

    if error:
        print(f"  ERROR: {error}")
        sys.exit(1)
    elif already:
        print(f"  {os.path.basename(target)}: already patched")
    elif patched and not args.dry_run:
        print(f"\nRestart the gateway to activate:")
        print(f"  bash ~/.openclaw/workspace/scripts/graceful-restart.sh")


if __name__ == "__main__":
    main()
