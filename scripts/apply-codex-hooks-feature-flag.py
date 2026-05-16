#!/usr/bin/env python3
"""
Patch: Codex native-hook-relay uses stable `features.hooks` flag (not deprecated `features.codex_hooks`).

Upstream: openclaw/openclaw#82078 (merged 2026-05-15 20:35Z, commit 3de97057d0bc)
Filed:    openclaw/openclaw#82350 (closed as already-implemented by #82078)
Findings: workspace/docs/findings/2026-05-15-oc-codex-firewall-bypass.md

PROBLEM
-------
OpenClaw's codex plugin builds per-thread config with `features.codex_hooks: true`
to enable hooks.PreToolUse/PostToolUse/etc. routing back to OC's before_tool_call
hook chain. Recent codex-cli versions removed the `codex_hooks` feature flag in
favor of the stable `hooks` flag. As a result, codex receives the hooks config but
silently drops it (the feature gate evaluates false), and oc-firewall + any other
before_tool_call-based plugin enforcement is bypassed for every codex-harness agent.

FIX
---
Replace `"features.codex_hooks"` with `"features.hooks"` in two places in the
codex plugin's `native-hook-relay.ts` (bundled into the dist file):

  - buildCodexNativeHookRelayConfig:           `"features.codex_hooks": true`  → `"features.hooks": true`
  - buildCodexNativeHookRelayDisabledConfig:   `"features.codex_hooks": false` → `"features.hooks": false`

This is exactly the change in upstream PR #82078.

TARGET FILE
-----------
~/.openclaw/npm/node_modules/@openclaw/codex/dist/run-attempt-*.js
(rollup bundles native-hook-relay.ts source into this file; the hash suffix varies
per build but the file is uniquely identifiable by containing the
buildCodexNativeHookRelayConfig function definition)

RETIRE-WHEN
-----------
The next OC release that includes commit 3de97057d0bc (later than v2026.5.14-beta.2).
On upgrade, verify the upstream version emits `"features.hooks"` natively, then
remove this patch from apply-all.py rotation.

Usage:
  python3 apply-codex-hooks-feature-flag.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.openclaw/npm/node_modules/@openclaw/codex/dist"
)

BACKUP_SUFFIX = ".bak-codex-hooks-feature-flag"

REPLACEMENTS = [
    ('"features.codex_hooks": true',  '"features.hooks": true'),
    ('"features.codex_hooks": false', '"features.hooks": false'),
]

# The function signatures we expect to find; if they're missing, the upstream
# code shape has changed materially and this patch may be obsolete.
SENTINEL_SIGNATURES = [
    "function buildCodexNativeHookRelayConfig(params)",
    "function buildCodexNativeHookRelayDisabledConfig()",
]


def find_target_files(dist_dir: str) -> list[str]:
    """Locate the run-attempt-*.js file(s) that bundle native-hook-relay.ts."""
    candidates = glob.glob(os.path.join(dist_dir, "run-attempt-*.js"))
    targets = []
    for path in candidates:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if all(sig in content for sig in SENTINEL_SIGNATURES):
            targets.append(path)
    return targets


def apply_patch(path: str, dry_run: bool) -> tuple[bool, list[str]]:
    """Apply REPLACEMENTS to a single file. Returns (changed, log_messages)."""
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()
    patched = original
    applied = []
    skipped_already_applied = []
    for old, new in REPLACEMENTS:
        if old in patched:
            patched = patched.replace(old, new)
            applied.append(old)
        elif new in patched:
            skipped_already_applied.append(new)
        else:
            messages.append(f"  MISS: neither {old!r} nor {new!r} found")
    if applied:
        messages.append(f"  Applied {len(applied)} replacement(s):")
        for s in applied:
            messages.append(f"    {s!r} → '{REPLACEMENTS[[a for a, _ in REPLACEMENTS].index(s)][1]}'")
    if skipped_already_applied:
        messages.append(f"  Already patched ({len(skipped_already_applied)} replacement(s) present in new form)")
    if patched == original:
        return False, messages
    if dry_run:
        messages.append(f"  DRY-RUN: would write {len(patched)} bytes (unchanged size: {len(patched) == len(original)})")
        return True, messages
    backup = path + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        messages.append(f"  Backup: {backup}")
    tmp = path + ".tmp-codex-hooks"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(patched)
    os.replace(tmp, path)
    messages.append(f"  Wrote {len(patched)} bytes to {path}")
    return True, messages


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    ap.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help=f"codex plugin dist dir (default: {DIST_DIR_DEFAULT})")
    args = ap.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir not found: {args.dist_dir}", file=sys.stderr)
        return 2

    targets = find_target_files(args.dist_dir)
    if not targets:
        print(f"ERROR: no run-attempt-*.js containing buildCodexNativeHookRelayConfig found under {args.dist_dir}", file=sys.stderr)
        print("Upstream shape may have changed; check whether this patch is still needed.", file=sys.stderr)
        return 3
    if len(targets) > 1:
        print(f"WARNING: multiple candidates found ({len(targets)}), patching all:", file=sys.stderr)
        for t in targets:
            print(f"  {t}", file=sys.stderr)

    any_changed = False
    any_failed = False
    for path in targets:
        print(f"=== {path} ===")
        changed, messages = apply_patch(path, args.dry_run)
        for m in messages:
            print(m)
        if changed:
            any_changed = True
        if any("MISS" in m for m in messages):
            any_failed = True

    if any_failed:
        print("\nFAIL: some replacements did not find their target. Check upstream shape.", file=sys.stderr)
        return 4
    if not any_changed:
        print("\nNo changes needed (patch already applied or upstream already at new flag).")
        return 0
    print(f"\nSUCCESS: patch applied{' (dry-run)' if args.dry_run else ''}.")
    if not args.dry_run:
        print("Restart the gateway for the change to take effect:")
        print("  systemctl --user restart openclaw-gateway.service")
        print("OR rely on next --local agent dispatch (each fresh process loads from dist).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
