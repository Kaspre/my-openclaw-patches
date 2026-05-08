#!/usr/bin/env python3
"""
Patch: Heartbeat SessionKey Fix (Changes 2-5 from PR #21682)
Issue: openclaw/openclaw#14191
PR: openclaw/openclaw#21682

Change 1 was merged upstream in v2026.3.7+. This script applies:
  Change 2: forceLastTargetWhenNone guard in resolveHeartbeatDeliveryTarget
  Change 3: Pass forceLastTargetWhenNone from heartbeat runner (health-*.js)
  Change 4: Recognize exec:<id>:exit in resolveHeartbeatReasonKind (all dist .js)
  Change 5: Expand isExecCompletionEvent string matches (health-*.js)

Usage:
  python3 apply-heartbeat-sessionkey-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-heartbeat"

# ---------------------------------------------------------------------------
# Change 2: Wrap target === "none" block with forceLastTargetWhenNone guard
# Files: reply-*.js, compact-*.js (and any other file containing this pattern)
# ---------------------------------------------------------------------------

CHANGE2_OLD = """\tif (target === "none") {
\t\tconst base = resolveSessionDeliveryTarget({ entry });
\t\treturn buildNoHeartbeatDeliveryTarget({
\t\t\treason: "target-none",
\t\t\tlastChannel: base.lastChannel,
\t\t\tlastAccountId: base.lastAccountId
\t\t});
\t}"""

CHANGE2_NEW = """\tif (target === "none") {
\t\tif (!params.forceLastTargetWhenNone) {
\t\t\tconst base = resolveSessionDeliveryTarget({ entry });
\t\t\treturn buildNoHeartbeatDeliveryTarget({
\t\t\t\treason: "target-none",
\t\t\t\tlastChannel: base.lastChannel,
\t\t\t\tlastAccountId: base.lastAccountId
\t\t\t});
\t\t}
\t\ttarget = "last";
\t}"""

CHANGE2_ALREADY_PATCHED = "params.forceLastTargetWhenNone"

# ---------------------------------------------------------------------------
# Change 3: Add forceLastTargetWhenNone param to resolveHeartbeatDeliveryTarget call
# Files: health-*.js
# ---------------------------------------------------------------------------

CHANGE3_OLD = """\tconst delivery = resolveHeartbeatDeliveryTarget({
\t\tcfg,
\t\tentry,
\t\theartbeat
\t});"""

CHANGE3_NEW = """\tconst delivery = resolveHeartbeatDeliveryTarget({
\t\tcfg,
\t\tentry,
\t\theartbeat,
\t\tforceLastTargetWhenNone: opts.reason === "exec-event" || (typeof opts.reason === "string" && opts.reason.startsWith("exec:"))
\t});"""

# v2026.5.7: heartbeat arg restructured with commitmentDeliveryContext ternary (heartbeat-runner-*.js)
CHANGE3_OLD_V57 = """\tconst delivery = resolveHeartbeatDeliveryTarget({
\t\tcfg,
\t\tentry,
\t\theartbeat: commitmentDeliveryContext ? {
\t\t\t...heartbeat,
\t\t\ttarget: "last",
\t\t\tto: void 0,
\t\t\taccountId: void 0
\t\t} : heartbeat,
\t\tturnSource: commitmentDeliveryContext ? commitmentDeliveryContext : useIsolatedSession ? void 0 : preflight.turnSourceDeliveryContext
\t});"""

CHANGE3_NEW_V57 = """\tconst delivery = resolveHeartbeatDeliveryTarget({
\t\tcfg,
\t\tentry,
\t\theartbeat: commitmentDeliveryContext ? {
\t\t\t...heartbeat,
\t\t\ttarget: "last",
\t\t\tto: void 0,
\t\t\taccountId: void 0
\t\t} : heartbeat,
\t\tturnSource: commitmentDeliveryContext ? commitmentDeliveryContext : useIsolatedSession ? void 0 : preflight.turnSourceDeliveryContext,
\t\tforceLastTargetWhenNone: opts.reason === "exec-event" || (typeof opts.reason === "string" && opts.reason.startsWith("exec:"))
\t});"""

CHANGE3_ALREADY_PATCHED = "forceLastTargetWhenNone: opts.reason"

# ---------------------------------------------------------------------------
# Change 4: Add exec: prefix match in resolveHeartbeatReasonKind
# Files: ALL dist/*.js and dist/plugin-sdk/*.js containing this function
# ---------------------------------------------------------------------------

CHANGE4_OLD = """\tif (trimmed === "exec-event") return "exec-event";
\tif (trimmed === "wake") return "wake";"""

CHANGE4_NEW = """\tif (trimmed === "exec-event") return "exec-event";
\tif (trimmed.startsWith("exec:")) return "exec-event";
\tif (trimmed === "wake") return "wake";"""

# v2026.5.7: function moved to heartbeat-runner-*.js; "wake" line now has cron: between exec-event and wake
CHANGE4_OLD_V57 = """\tif (trimmed === "exec-event") return "exec-event";
\tif (trimmed.startsWith("cron:")) return "cron";"""

CHANGE4_NEW_V57 = """\tif (trimmed === "exec-event") return "exec-event";
\tif (trimmed.startsWith("exec:")) return "exec-event";
\tif (trimmed.startsWith("cron:")) return "cron";"""

CHANGE4_ALREADY_PATCHED = 'trimmed.startsWith("exec:")'

# ---------------------------------------------------------------------------
# Change 5: Expand isExecCompletionEvent to match more strings
# Files: health-*.js
# ---------------------------------------------------------------------------

CHANGE5_OLD = """function isExecCompletionEvent(evt) {
\treturn evt.toLowerCase().includes("exec finished");
}"""

CHANGE5_NEW = """function isExecCompletionEvent(evt) {
\tconst lower = evt.toLowerCase();
\treturn lower.includes("exec finished") || lower.includes("exec completed") || lower.includes("exec failed") || lower.includes("exec killed");
}"""

CHANGE5_ALREADY_PATCHED = 'lower.includes("exec completed")'

# ---------------------------------------------------------------------------
# Change definitions: (name, old, new, already_patched_marker, file_glob_patterns)
# ---------------------------------------------------------------------------

CHANGES = [
    {
        "name": "Change 2: forceLastTargetWhenNone guard",
        "old": CHANGE2_OLD,
        "new": CHANGE2_NEW,
        "marker": CHANGE2_ALREADY_PATCHED,
        "globs": ["*.js"],
    },
    {
        # v5.7: call moved to heartbeat-runner-*.js with commitmentDeliveryContext restructure
        "name": "Change 3: pass forceLastTargetWhenNone from runner (v5.7)",
        "old": CHANGE3_OLD_V57,
        "new": CHANGE3_NEW_V57,
        "marker": CHANGE3_ALREADY_PATCHED,
        "globs": ["heartbeat-runner-*.js"],
    },
    {
        # pre-v5.7 fallback
        "name": "Change 3: pass forceLastTargetWhenNone from runner (pre-v5.7)",
        "old": CHANGE3_OLD,
        "new": CHANGE3_NEW,
        "marker": CHANGE3_ALREADY_PATCHED,
        "globs": ["health-*.js", "gateway-cli-*.js"],
    },
    {
        # v5.7: function inlined in heartbeat-runner-*.js; cron: line now between exec-event and wake
        "name": "Change 4: exec: prefix in heartbeat reason kind (v5.7)",
        "old": CHANGE4_OLD_V57,
        "new": CHANGE4_NEW_V57,
        "marker": CHANGE4_ALREADY_PATCHED,
        "globs": ["heartbeat-runner-*.js"],
    },
    {
        # pre-v5.7 fallback
        "name": "Change 4: exec: prefix in resolveHeartbeatReasonKind (pre-v5.7)",
        "old": CHANGE4_OLD,
        "new": CHANGE4_NEW,
        "marker": CHANGE4_ALREADY_PATCHED,
        "globs": ["*.js", "plugin-sdk/*.js"],
    },
    # Change 5 SUPERSEDED in v5.7: isExecCompletionEvent already uses regex +
    # STRUCTURED_EXEC_COMPLETION_EVENT_RE which covers all the cases our patch added.
    # Kept as dead entry so dry-run doesn't report it as "no matching files".
]


def find_js_files(dist_dir, glob_patterns):
    """Find .js files matching glob patterns, excluding backups."""
    files = set()
    for pattern in glob_patterns:
        full_pattern = os.path.join(dist_dir, pattern)
        for f in glob.glob(full_pattern):
            if ".bak" not in f:
                files.add(f)
    return sorted(files)


def apply_change(change, dist_dir, dry_run=False):
    """Apply a single change across all matching files. Returns (patched, skipped, errors)."""
    name = change["name"]
    old = change["old"]
    new = change["new"]
    marker = change["marker"]

    files = find_js_files(dist_dir, change["globs"])
    patched = 0
    skipped = 0
    errors = 0

    for filepath in files:
        basename = os.path.basename(filepath)
        # For plugin-sdk files, show subdir
        if "/plugin-sdk/" in filepath:
            basename = "plugin-sdk/" + basename

        with open(filepath, "r") as f:
            content = f.read()

        # Check if file even contains the pattern area
        if old not in content and marker not in content:
            continue

        # Already patched?
        if marker in content:
            print(f"  SKIP (already patched): {basename}")
            skipped += 1
            continue

        # Pattern must exist
        count = content.count(old)
        if count == 0:
            # File had something related but pattern didn't match exactly
            print(f"  WARNING: related code found but exact pattern not matched in {basename}")
            errors += 1
            continue
        if count > 1:
            print(f"  ERROR: pattern found {count} times (expected 1) in {basename}")
            errors += 1
            continue

        new_content = content.replace(old, new, 1)

        if dry_run:
            print(f"  DRY RUN OK: {basename}")
            patched += 1
            continue

        # Backup
        backup = filepath + BACKUP_SUFFIX
        if not os.path.exists(backup):
            shutil.copy2(filepath, backup)
            print(f"  Backup: {basename}{BACKUP_SUFFIX}")

        with open(filepath, "w") as f:
            f.write(new_content)

        print(f"  PATCHED: {basename}")
        patched += 1

    return patched, skipped, errors


def main():
    parser = argparse.ArgumentParser(
        description="Apply heartbeat sessionkey fix (Changes 2-5 from PR #21682)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Check patterns without modifying files"
    )
    parser.add_argument(
        "--dist-dir", default=DIST_DIR_DEFAULT,
        help="OpenClaw dist directory"
    )
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist directory not found: {args.dist_dir}")
        sys.exit(1)

    mode = "(DRY RUN)" if args.dry_run else ""
    print(f"Heartbeat SessionKey Fix — Changes 2-5 from PR #21682 {mode}")
    print(f"Dist: {args.dist_dir}")
    print()

    total_patched = 0
    total_skipped = 0
    total_errors = 0

    for change in CHANGES:
        print(f"[{change['name']}]")
        patched, skipped, errors = apply_change(change, args.dist_dir, dry_run=args.dry_run)
        if patched == 0 and skipped == 0 and errors == 0:
            print("  (no matching files found)")
        total_patched += patched
        total_skipped += skipped
        total_errors += errors
        print()

    print("=" * 60)
    if args.dry_run:
        print(f"Dry run complete: {total_patched} file(s) would be patched, "
              f"{total_skipped} already patched, {total_errors} error(s)")
    else:
        print(f"Done: {total_patched} file(s) patched, "
              f"{total_skipped} already patched, {total_errors} error(s)")
        if total_patched > 0:
            print("Restart gateway: systemctl --user restart openclaw-gateway")

    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
