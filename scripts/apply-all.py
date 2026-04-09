#!/usr/bin/env python3
"""
Apply all OpenClaw patches in the correct order.

Usage:
  python3 apply-all.py [--dry-run] [--dist-dir PATH]

Active patches (v2026.3.31):
  1. heartbeat-sessionkey    — exec notification delivery (Changes 2+4 of PR #21682)
  2. memoryflush-fix         — flush fires every compaction (#12590)
  3. loglevel-fix            — inverted levelToMinLevel mapping (#29448)
  4. cron-duplicate-fix      — prevent duplicate job execution after restart (#42640)

On hold:
  4. sessions-manage-tool    — programmatic session compact/reset (PR #52422, apply on demand)

Retired on v2026.3.31:
  - exec-host-override      — fixed upstream (#57689)
  - approval-auto-expire    — tabled (OC Firewall handles security)
  - approval-prefix-match   — tabled (OC Firewall handles security)
  - approval-desc-routing   — tabled (OC Firewall handles security)
  - session-key-cli         — superseded by native --session-id (v2026.3.22)
  - cache-trace-redact      — no unredacted files remain
"""

import argparse
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

PATCHES = [
    ("heartbeat-sessionkey", "apply-heartbeat-sessionkey-fix.py"),
    ("memoryflush-fix", "apply-memoryflush-fix.py"),
    # Retired in v2026.4.9: PR #44646 landed upstream. All 5 loglevel issues
    # (inverted mapping, <= file/console comparisons, child logger minLevel
    # inheritance, sub-logger minLevel propagation) are fixed in v4.9 bundles.
    # Verified 2026-04-09 in logger--Y4fLUmQ.js + subsystem-C1arrdPy.js.
    # Script kept on disk for archaeology.
    # ("loglevel-fix", "apply-loglevel-fix.py"),
    ("cron-duplicate-fix", "apply-cron-duplicate-fix.py"),
    ("bootstrap-missing-marker-fix", "apply-bootstrap-missing-marker-fix.py"),
    # Retired in v2026.4.9: merged upstream (PR #58928 effectively landed as a
    # fallback read `context.systemPrompt ?? context.system` at the recordStage
    # call site in pi-embedded-*.js). Script kept on disk for archaeology.
    # ("cache-trace-systemprompt-fix", "apply-cache-trace-systemprompt-fix.py"),
    ("plugin-register-skip-on-inspection", "apply-plugin-register-skip-on-inspection.py"),
    ("channels-before-ws-handlers", "apply-channels-before-ws-handlers.py"),
    # On hold — apply with: --only sessions-manage-tool
    # ("sessions-manage-tool", "apply-sessions-manage-tool.py"),
]


def main():
    parser = argparse.ArgumentParser(description="Apply all OpenClaw patches")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", help="OpenClaw dist directory (passed to each script)")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="Only apply specific patches by name")
    parser.add_argument("--skip", nargs="+", metavar="NAME", help="Skip specific patches by name")
    args = parser.parse_args()

    patches = PATCHES
    if args.only:
        patches = [(n, s) for n, s in patches if n in args.only]
        if not patches:
            print(f"ERROR: No patches matched --only {args.only}")
            print(f"Available: {', '.join(n for n, _ in PATCHES)}")
            sys.exit(1)
    if args.skip:
        patches = [(n, s) for n, s in patches if n not in args.skip]

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"OpenClaw Patch Suite — {mode}")
    print(f"Patches: {len(patches)}")
    print("=" * 60)
    print()

    results = {}
    for name, script in patches:
        print(f"--- {name} ---")
        cmd = [sys.executable, os.path.join(SCRIPTS_DIR, script)]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.dist_dir:
            cmd.extend(["--dist-dir", args.dist_dir])

        result = subprocess.run(cmd, capture_output=False)
        results[name] = result.returncode
        print()

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, code in results.items():
        status = "OK" if code == 0 else f"FAILED (exit {code})"
        print(f"  {name:30s} {status}")

    failed = sum(1 for c in results.values() if c != 0)
    if failed:
        print(f"\n{failed} patch(es) failed!")
        sys.exit(1)
    else:
        print(f"\nAll {len(results)} patches completed successfully.")
        if not args.dry_run:
            print("Restart gateway: systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
