#!/usr/bin/env python3
"""
Patch: skip plugin register() in inspection mode (shouldActivate === false)
Upstream: not yet filed — this patch is the basis for our own PR.

Problem
-------
`loadOpenClawPlugins()` is called many times during gateway startup with
different cache keys (discovery, validation, activation, deferred reload,
per-scope setup, etc.). Each call re-enters the plugin loop and invokes every
plugin's `register(api)` function again. For v2026.4.7+, this produces ~58
register() calls per plugin per startup (vs 1 call on v2026.4.2 and earlier).

Some of those calls run with `shouldActivate === true` (real activation) and
some with `shouldActivate === false` (inspection / discovery mode). In
inspection mode, the loader:

  1. Captures 6-7 provider/memory state snapshots
  2. Calls `register(api)` — letting the plugin touch those captured globals
  3. Immediately restores the snapshots, discarding all side effects

Plugins whose register() only touches the captured globals pay negligible cost.
Plugins that do other work in register() — opening OTel exporters, spawning
timers, starting network I/O, initializing heavy SDKs — pay the full cost and
then watch the captured part of their effects get rolled back.

In our environment, this produces a ~130-second delay on gateway restart,
dominated by the otel-observability plugin re-initializing its OpenTelemetry
pipeline ~56 times in inspection mode.

Fix
---
Only call `register(api)` when `shouldActivate === true`. The loader's
existing rollback branch (`if (!shouldActivate) { restoreX; restoreY; ... }`)
is an implicit acknowledgement that inspection-mode effects are unwanted —
running register() in that mode and then undoing its output is pure waste.

With this patch applied, inspection-mode iterations skip register() entirely
but still walk the rest of the loader body (config validation, record push,
seenIds update, etc.). Every observable side effect of inspection mode is
preserved because inspection mode was never supposed to keep register()'s
side effects in the first place.

Measured impact (v2026.4.8, our environment)
---------------------------------------------

Before patch (n=3):
  - gateway-ready:  130-148s
  - register() calls during startup:  58

After patch (n=3):
  - gateway-ready:  17.5-27.3s (17-19s warm, ~27s cold after patch edit)
  - register() calls during startup:  2  (the two legitimate activation
                                           passes — gateway pre-sidecar and
                                           deferred reload — both retained)

Returns warm-state restart time to the v2026.4.2 baseline of ~17s median.
First restart after the patch is written sees a one-time ~10s V8 compile
overhead on the modified module.

Related upstream reports (all OPEN, none with a merged fix)
-----------------------------------------------------------
  - #56522  register() called multiple times per startup (v2026.3.24 baseline)
  - #61920  [Bug] Plugin register function called multiple times during
            startup (50+ times)
  - #61922  openclaw dashboard crashes with Maximum call stack size exceeded
  - #61938  LCM plugin enters infinite reload loop causing stack overflow
  - #61946  memory-core promote causes Maximum call stack size exceeded
  - #61951  memory-core Dreaming Maximum call stack size exceeded
  - #54835  fix: prevent duplicate plugin registration logs during CLI init
            (abandoned drive-by, logger-swap approach, scope-crept)
  - #56520  fix(feishu): prevent duplicate tool registration on cache miss
            (abandoned drive-by, unconditional-skip on a bundled plugin,
             author notes "this is a workaround")

Target file
-----------
`dist/loader-*.js` (hash varies; script globs and filters by the unique
pattern at the register call site).

Usage
-----
  python3 apply-plugin-register-skip-on-inspection.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-plugin-register-skip"
TARGET_GLOB = "loader-*.js"

# The exact block in the bundled loader. Indentation is tabs, matching Rollup output.
# v4.23+ shape: register call moved into runPluginRegisterSync wrapper, invoked
# via withProfile(). The amplification-path call site is in loadOpenClawPlugins'
# per-plugin loop (registrationMode is dynamic via template literal); the
# separate cli-metadata:register call site (literal string) is left untouched.
OLD_CODE = """\t\t\ttry {
\t\t\t\twithProfile({
\t\t\t\t\tpluginId: record.id,
\t\t\t\t\tsource: record.source
\t\t\t\t}, `${registrationMode}:register`, () => runPluginRegisterSync(register, api));"""

NEW_CODE = """\t\t\ttry {
\t\t\t\t// LOCAL PATCH (Option A, register-amplification): when shouldActivate
\t\t\t\t// is false the loader captures a state snapshot, calls register(),
\t\t\t\t// then immediately rolls the snapshot back — pure waste for plugins
\t\t\t\t// that do expensive work (OTel init, DB connections, network I/O) in
\t\t\t\t// register(). Skip the call entirely when in inspection mode. The
\t\t\t\t// rollback branch below is the existing code's acknowledgement that
\t\t\t\t// "we don't want the effects here anyway." Benefits all plugins.
\t\t\t\t// See: ~/.openclaw/workspace/docs/findings/2026-04-08-plugin-register-amplification.md
\t\t\t\tif (shouldActivate) {
\t\t\t\t\twithProfile({
\t\t\t\t\t\tpluginId: record.id,
\t\t\t\t\t\tsource: record.source
\t\t\t\t\t}, `${registrationMode}:register`, () => runPluginRegisterSync(register, api));
\t\t\t\t}"""

# Present-after-patch marker that is NOT present in the unpatched code.
# Used to detect idempotent re-application.
ALREADY_PATCHED_MARKER = "LOCAL PATCH (Option A, register-amplification)"


def find_target(dist_dir):
    candidates = [
        p for p in glob.glob(os.path.join(dist_dir, TARGET_GLOB))
        if not p.endswith(BACKUP_SUFFIX)
    ]
    hits = []
    for c in candidates:
        try:
            with open(c, "r") as f:
                content = f.read()
            if OLD_CODE in content or ALREADY_PATCHED_MARKER in content:
                hits.append(c)
        except OSError:
            pass
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # Prefer the one with the exact OLD_CODE pattern
    for c in hits:
        with open(c, "r") as f:
            if OLD_CODE in f.read():
                return c
    return hits[0]


def patch_file(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    if ALREADY_PATCHED_MARKER in content:
        return ("already_patched", basename)

    if OLD_CODE not in content:
        return ("pattern_not_found", basename)

    count = content.count(OLD_CODE)
    if count != 1:
        return (f"pattern_matched_{count}_times", basename)

    new_content = content.replace(OLD_CODE, NEW_CODE)

    if dry_run:
        return ("would_patch", basename)

    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)

    with open(filepath, "w") as f:
        f.write(new_content)

    return ("patched", basename)


def main():
    parser = argparse.ArgumentParser(
        description="Apply plugin register() skip-on-inspection patch (Option A)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Check pattern without modifying file")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT,
                        help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir does not exist: {args.dist_dir}", file=sys.stderr)
        sys.exit(1)

    target = find_target(args.dist_dir)
    if not target:
        print(f"ERROR: no loader-*.js bundle containing the target pattern found "
              f"in {args.dist_dir}", file=sys.stderr)
        print(f"  The bundled loader code may have changed shape. Review "
              f"loader-*.js manually and update OLD_CODE in this script.",
              file=sys.stderr)
        sys.exit(1)

    status, basename = patch_file(target, dry_run=args.dry_run)

    if status == "patched":
        print(f"OK: patched {basename} (backup at {basename}{BACKUP_SUFFIX})")
        sys.exit(0)
    elif status == "would_patch":
        print(f"DRY-RUN: would patch {basename}")
        sys.exit(0)
    elif status == "already_patched":
        print(f"SKIP: {basename} already patched")
        sys.exit(0)
    elif status == "pattern_not_found":
        print(f"ERROR: expected pattern not found in {basename}", file=sys.stderr)
        print(f"  The loader bundle shape may have changed in this OC version.",
              file=sys.stderr)
        print(f"  Review {basename} manually and update OLD_CODE in this script.",
              file=sys.stderr)
        sys.exit(2)
    else:
        print(f"ERROR: unexpected status '{status}' for {basename}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
