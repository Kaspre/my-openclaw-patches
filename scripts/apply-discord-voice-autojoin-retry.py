#!/usr/bin/env python3
"""
Patch: Discord voice autoJoin retries non-fatal failures with backoff.

PROBLEM
-------
DiscordVoiceManager.autoJoin() iterates `voice.autoJoin` config entries and
calls this.join({guildId, channelId}) for each. If join fails:
  - Fatal patterns (auth/forbidden/etc.) → marked in fatalAutoJoinFailures
    and suppressed on subsequent runs (correct behavior; needs manual fix).
  - Non-fatal failures (e.g. "OpenAI realtime connection timeout") → logged
    and skipped, but NOT retried. The only way to retry is for Discord to
    fire Ready or Resumed again, which means a full gateway restart or
    Discord-side WebSocket reconnect.

That gap defeats always-on voice presence: a single transient OpenAI hiccup
during gateway startup leaves Captain out of voice until the next restart.

FIX
---
Inside the non-fatal branch (the existing `if (!result.ok)` block), schedule
a 30-second deferred retry of this.autoJoin(). The autoJoin method already
guards against re-entrance (`if (this.autoJoinTask) return this.autoJoinTask`)
and clears autoJoinTask in its finally block, so setTimeout-based retries
are safe and compose: each non-fatal failure schedules its own retry; the
first one to fire kicks off a fresh autoJoin run, the rest no-op.

Fatal failures continue to short-circuit (no retry — the user must intervene).
Mid-session disconnects continue to use the existing handleVoiceStateUpdate
residency-target rejoin path; this patch only addresses the
"autoJoin failed entirely, no voice state to track" gap.

TARGET FILE
-----------
~/.openclaw/npm/node_modules/@openclaw/discord/dist/manager.runtime-*.js
(rollup bundles voice/manager.ts; hash suffix varies per build)

RETIRE-WHEN
-----------
Upstream OC adds retry-on-non-fatal-failure to autoJoin natively. As of
@openclaw/discord 2026.5.18 the behavior is "fire once on Ready/Resumed,
no internal retry." If a future release exposes a `voice.autoJoin.retry`
config option or adds the setTimeout retry, remove this patch.

Usage:
  python3 apply-discord-voice-autojoin-retry.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.openclaw/npm/node_modules/@openclaw/discord/dist"
)

BACKUP_SUFFIX = ".bak-autojoin-retry"

# Exact tab-indented block from the unpatched manager.runtime bundle.
OLD_BLOCK = (
    "\t\t\t\tif (!result.ok) {\n"
    "\t\t\t\t\tlogger.warn(`discord voice: autoJoin skipped guild=${entry.guildId} channel=${entry.channelId}: ${result.message}`);\n"
    "\t\t\t\t\tif (isFatalAutoJoinFailure(result.message)) this.fatalAutoJoinFailures.set(failureKey, {\n"
    "\t\t\t\t\t\tmessage: result.message,\n"
    "\t\t\t\t\t\tskipLogged: false\n"
    "\t\t\t\t\t});\n"
    "\t\t\t\t}"
)

NEW_BLOCK = (
    "\t\t\t\tif (!result.ok) {\n"
    "\t\t\t\t\tlogger.warn(`discord voice: autoJoin skipped guild=${entry.guildId} channel=${entry.channelId}: ${result.message}`);\n"
    "\t\t\t\t\tif (isFatalAutoJoinFailure(result.message)) this.fatalAutoJoinFailures.set(failureKey, {\n"
    "\t\t\t\t\t\tmessage: result.message,\n"
    "\t\t\t\t\t\tskipLogged: false\n"
    "\t\t\t\t\t});\n"
    "\t\t\t\t\telse {\n"
    "\t\t\t\t\t\tconst __retryMs = 30000;\n"
    "\t\t\t\t\t\tlogger.warn(`discord voice: autoJoin will retry guild=${entry.guildId} channel=${entry.channelId} in ${__retryMs}ms (non-fatal failure; apply-discord-voice-autojoin-retry)`);\n"
    "\t\t\t\t\t\tsetTimeout(() => { this.autoJoin().catch(() => {}); }, __retryMs);\n"
    "\t\t\t\t\t}\n"
    "\t\t\t\t}"
)

# Sentinel that confirms we're patching the right file: the autoJoin method
# definition. If absent, the bundle layout has changed materially.
SENTINEL = "async autoJoin() {"


def find_target_files(dist_dir: str) -> list[str]:
    """Locate manager.runtime-*.js bundles that contain the autoJoin code."""
    candidates = glob.glob(os.path.join(dist_dir, "manager.runtime-*.js"))
    targets = []
    for path in candidates:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if SENTINEL in content and OLD_BLOCK in content:
            targets.append(path)
        elif SENTINEL in content and NEW_BLOCK in content:
            # already patched
            targets.append(path)
    return targets


def apply_patch(path: str, dry_run: bool) -> tuple[bool, list[str]]:
    """Apply the replacement to a single file. Returns (changed, messages)."""
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    if NEW_BLOCK in original and OLD_BLOCK not in original:
        messages.append("  Already patched (NEW_BLOCK present, OLD_BLOCK absent)")
        return False, messages
    if OLD_BLOCK not in original:
        messages.append("  MISS: OLD_BLOCK not found (and not already patched)")
        return False, messages

    patched = original.replace(OLD_BLOCK, NEW_BLOCK, 1)
    if patched == original:
        messages.append("  MISS: replacement produced no change")
        return False, messages

    if dry_run:
        messages.append(f"  DRY-RUN: would replace 1 block; size delta = {len(patched) - len(original):+d} bytes")
        return True, messages

    backup = path + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        messages.append(f"  Backup: {backup}")
    tmp = path + ".tmp-autojoin-retry"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(patched)
    os.replace(tmp, path)
    messages.append(f"  Wrote {len(patched)} bytes to {path}")
    return True, messages


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    ap.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help=f"discord plugin dist dir (default: {DIST_DIR_DEFAULT})")
    args = ap.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir not found: {args.dist_dir}", file=sys.stderr)
        return 2

    targets = find_target_files(args.dist_dir)
    if not targets:
        print(f"ERROR: no manager.runtime-*.js found containing the autoJoin code under {args.dist_dir}", file=sys.stderr)
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
        print("\nFAIL: replacement target not found. Check upstream shape.", file=sys.stderr)
        return 4
    if not any_changed:
        print("\nNo changes needed (patch already applied).")
        return 0
    print(f"\nSUCCESS: patch applied{' (dry-run)' if args.dry_run else ''}.")
    if not args.dry_run:
        print("Restart the gateway for the change to take effect:")
        print("  bash /home/captain/.openclaw/workspace/scripts/graceful-restart.sh --immediate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
