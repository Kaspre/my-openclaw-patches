#!/usr/bin/env python3
"""
Apply all OpenClaw patches in the correct order.

Usage:
  python3 apply-all.py [--dry-run] [--dist-dir PATH]

Patches are applied in dependency order:
  1. exec-host-override      — exec tool sandbox→gateway override
  2. approval-auto-expire    — Discord native approval recognition
  3. approval-prefix-match   — /approve with 8-char slugs
  4. approval-desc-routing   — approval embeds stay in source channel
  5. heartbeat-sessionkey    — exec notification delivery (Changes 2-5)
  6. memoryflush-fix         — flush fires every compaction
  7. session-key-cli         — --session-key flag for openclaw agent
  8. ui-message-vanish       — dashboard user message persistence
  9. loglevel-fix            — inverted levelToMinLevel mapping
 10. plugin-cache-global     — process-global plugin registry cache
 11. sessions-manage-tool    — programmatic session compact/reset
"""

import argparse
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

PATCHES = [
    ("exec-host-override", "apply-exec-host-override.py"),
    ("approval-auto-expire", "apply-approval-auto-expire-fix.py"),
    ("approval-prefix-match", "apply-approval-prefix-match.py"),
    ("approval-desc-routing", "apply-approval-desc-routing.py"),
    ("heartbeat-sessionkey", "apply-heartbeat-sessionkey-fix.py"),
    ("memoryflush-fix", "apply-memoryflush-fix.py"),
    ("session-key-cli", "apply-session-key-cli.py"),
    ("ui-message-vanish", "apply-ui-message-vanish-fix.py"),
    ("loglevel-fix", "apply-loglevel-fix.py"),
    ("plugin-cache-global", "apply-plugin-cache-global.py"),
    ("sessions-manage-tool", "apply-sessions-manage-tool.py"),
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
