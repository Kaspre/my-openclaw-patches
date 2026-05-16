#!/usr/bin/env python3
"""
RETIRED 2026-05-16 on v2026.5.16-beta.2.

Upstream landed the same memoization pattern natively:
  - dist/plugin-metadata-snapshot-REEM32Mm.js (or successor content-hash):
    module-level `pluginMetadataSnapshotMemo` variable + check on line ~700
    + assignment on line ~727 inside loadPluginMetadataSnapshot.
  - Exports `clearLoadPluginMetadataSnapshotMemo` for explicit invalidation.
  - `canMemoizePluginMetadataSnapshotResult` gates which results get cached.

The native memo matches our patch's behavior for the 5-calls-per-startup
hot path (single-slot, key-fingerprint, clone-on-hit). Both versions are
process-scoped, so neither helps the cross-process `agent --local` cold-start
cost — if that becomes a priority, a file-cache approach is needed (much
bigger change than a snapshot-memo patch).

Original anchors below are preserved for reference / re-enabling if upstream
ever rolls back. To re-enable, restore the original docstring (replace this
block with the original "Patch: in-process memoization..." text) and the
patch will work against any bundle still matching OLD_FN_ANCHOR.

Behavior on retired versions: this script now reports
'upstream-native-memo' for files where the native memo is present and exits
0 (success, no-op). Falls back to old apply-or-fail logic for any file where
the native memo signature is absent (defensive — covers downgrade/rollback).

Original purpose (preserved):
  V8 cpu-prof of `openclaw plugins list` (91s wall, 100% CPU) showed
  loadPluginMetadataSnapshot called 5 times per CLI invocation, each ~16s
  of work building the same snapshot. Patch memoized at snapshot level.

Usage:
  python3 apply-plugin-metadata-snapshot-memo.py [--dry-run] [--dist-dir PATH]
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-snapshotmemo"

# Content anchors — survives across upgrade-driven file rename
# (plugin-metadata-snapshot-<hash>.js)
FILE_GLOB = "plugin-metadata-snapshot-*.js"

OLD_FN_ANCHOR = (
    'function loadPluginMetadataSnapshot(params) {\n'
    '\treturn measureDiagnosticsTimelineSpanSync("plugins.metadata.scan", () => loadPluginMetadataSnapshotImpl(params), {\n'
    '\t\tphase: getActiveDiagnosticsTimelineSpan()?.phase ?? "startup",\n'
    '\t\tconfig: params.config,\n'
    '\t\tenv: params.env,\n'
    '\t\tattributes: {\n'
    '\t\t\thasWorkspaceDir: params.workspaceDir !== void 0,\n'
    '\t\t\thasInstalledIndex: params.index !== void 0\n'
    '\t\t}\n'
    '\t});\n'
    '}'
)

NEW_FN_REPLACEMENT = (
    'function loadPluginMetadataSnapshot(params) {\n'
    '\t// LOCAL PATCH (apply-plugin-metadata-snapshot-memo): single-slot memo, globalThis-scoped.\n'
    '\t// CLI bootstrap calls this ~5x with identical params; rebuilding is ~16s each on a 104-plugin install.\n'
    '\tconst memoSlot = (globalThis.__ocPluginMetadataSnapshotMemo ??= { key: void 0, value: void 0 });\n'
    '\tlet memoKey;\n'
    '\ttry {\n'
    '\t\tmemoKey = JSON.stringify({\n'
    '\t\t\tc: params.config ?? null,\n'
    '\t\t\te: params.env === void 0 || params.env === process.env ? "$processEnv" : params.env,\n'
    '\t\t\tw: params.workspaceDir ?? null,\n'
    '\t\t\ts: params.stateDir ?? null,\n'
    '\t\t\tp: params.preferPersisted ?? null,\n'
    '\t\t\ti: params.index ?? null\n'
    '\t\t});\n'
    '\t} catch {\n'
    '\t\tmemoKey = void 0;\n'
    '\t}\n'
    '\tif (memoKey !== void 0 && memoSlot.key === memoKey) {\n'
    '\t\treturn memoSlot.value;\n'
    '\t}\n'
    '\tconst __snapshot_memo_result = measureDiagnosticsTimelineSpanSync("plugins.metadata.scan", () => loadPluginMetadataSnapshotImpl(params), {\n'
    '\t\tphase: getActiveDiagnosticsTimelineSpan()?.phase ?? "startup",\n'
    '\t\tconfig: params.config,\n'
    '\t\tenv: params.env,\n'
    '\t\tattributes: {\n'
    '\t\t\thasWorkspaceDir: params.workspaceDir !== void 0,\n'
    '\t\t\thasInstalledIndex: params.index !== void 0\n'
    '\t\t}\n'
    '\t});\n'
    '\tif (memoKey !== void 0) {\n'
    '\t\tmemoSlot.key = memoKey;\n'
    '\t\tmemoSlot.value = __snapshot_memo_result;\n'
    '\t}\n'
    '\treturn __snapshot_memo_result;\n'
    '}'
)

# Marker used to detect already-patched files (idempotence check)
APPLIED_MARKER = "__ocPluginMetadataSnapshotMemo"

# Upstream native memo signatures (added 2026.5.16-beta.2). When present,
# our patch is redundant — report no-op success instead of fail.
NATIVE_MEMO_SIGNATURE = "pluginMetadataSnapshotMemo"
NATIVE_MEMO_CLEAR_EXPORT = "clearLoadPluginMetadataSnapshotMemo"


def find_targets(dist_dir: str) -> list[str]:
    pattern = os.path.join(dist_dir, FILE_GLOB)
    matches = glob.glob(pattern)
    return matches


def patch_file(path: str, dry_run: bool) -> str:
    """Return one of: 'applied', 'already-applied', 'upstream-native-memo',
    'no-match', 'error:<reason>'."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return f"error:read:{e}"

    if APPLIED_MARKER in content:
        return "already-applied"

    # Upstream native memo present? (Both signatures required to avoid
    # false-positive on re-export proxy bundles like plugin-metadata-snapshot-CyffEZ4Z.js
    # which only contain the symbol re-export, not the implementation.)
    if (
        NATIVE_MEMO_SIGNATURE in content
        and NATIVE_MEMO_CLEAR_EXPORT in content
        and "function loadPluginMetadataSnapshot" in content
    ):
        return "upstream-native-memo"

    if OLD_FN_ANCHOR not in content:
        return "no-match"

    occurrences = content.count(OLD_FN_ANCHOR)
    if occurrences != 1:
        return f"error:expected 1 occurrence, found {occurrences}"

    new_content = content.replace(OLD_FN_ANCHOR, NEW_FN_REPLACEMENT, 1)

    if dry_run:
        return "applied"

    backup_path = path + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT)
    args = parser.parse_args()

    targets = find_targets(args.dist_dir)
    if not targets:
        print(
            f"FAIL: no files matched '{FILE_GLOB}' under {args.dist_dir}",
            file=sys.stderr,
        )
        return 2

    results = {p: patch_file(p, args.dry_run) for p in targets}

    applied = sum(1 for r in results.values() if r == "applied")
    already = sum(1 for r in results.values() if r == "already-applied")
    upstream = sum(1 for r in results.values() if r == "upstream-native-memo")
    no_match = sum(1 for r in results.values() if r == "no-match")
    errors = [(p, r) for p, r in results.items() if r.startswith("error")]

    for p, r in results.items():
        print(f"  {r:22s} {os.path.basename(p)}")

    print(
        f"\nSummary: applied={applied} already-applied={already} upstream-native-memo={upstream} no-match={no_match} errors={len(errors)} (dry-run={args.dry_run})"
    )

    if errors:
        for p, r in errors:
            print(f"  ERROR: {p}: {r}", file=sys.stderr)
        return 2

    # On v2026.5.16-beta.2+, the implementation bundle has native memo and the
    # proxy bundle (re-export only) has no `function loadPluginMetadataSnapshot`.
    # Both outcomes are valid no-op: if ANY bundle reports upstream-native-memo,
    # the patch is retired-correctly. Proxy `no-match` files are expected here.
    if upstream > 0:
        if applied == 0 and already == 0:
            print("OK: upstream native memo present, patch retired (no-op).")
        return 0

    if applied == 0 and already == 0:
        print(
            "FAIL: no target file matched the expected function body, "
            "and no upstream native memo detected. Upstream may have refactored "
            "loadPluginMetadataSnapshot in a way this script doesn't recognize. "
            "Re-validate by inspecting plugin-metadata-snapshot-*.js for the "
            "loadPluginMetadataSnapshot function shape.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
