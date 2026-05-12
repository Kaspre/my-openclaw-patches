#!/usr/bin/env python3
"""
Patch: in-process memoization of loadPluginMetadataSnapshot.

Why:
  V8 cpu-prof of `openclaw plugins list` (91s wall, 100% CPU) shows
  loadPluginMetadataSnapshot is called 5 times per CLI invocation,
  each doing ~16-17s of work building the same snapshot. Lower-level
  caches (manifestMetadataCache in manifest-metadata-scan) don't dedup
  these because the 5 callers reach loadPluginMetadataSnapshot via
  different paths that each rebuild the upper-layer snapshot wrapper
  (registry, owner-maps, fingerprints) before the lower cache helps.

  This patch memoizes at the snapshot level. Single-slot cache, keyed
  by a JSON fingerprint of (config, env, workspaceDir, stateDir,
  preferPersisted, index). process.env is normalized to a sentinel
  to avoid expensive Object.entries on every call.

  Uses a globalThis singleton (matching apply-plugin-cache-global.py
  precedent) so duplicated bundler chunks share the cache.

Expected impact:
  5 calls × ~16s -> 1 call × ~16s + 4 trivial cache hits.
  Wall-clock target: 91s -> ~30-40s on `openclaw plugins list`.

Idempotent. Re-runnable. Fail-loud if anchors don't match.

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


def find_targets(dist_dir: str) -> list[str]:
    pattern = os.path.join(dist_dir, FILE_GLOB)
    matches = glob.glob(pattern)
    return matches


def patch_file(path: str, dry_run: bool) -> str:
    """Return one of: 'applied', 'already-applied', 'no-match', 'error:<reason>'."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return f"error:read:{e}"

    if APPLIED_MARKER in content:
        return "already-applied"

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
    no_match = sum(1 for r in results.values() if r == "no-match")
    errors = [(p, r) for p, r in results.items() if r.startswith("error")]

    for p, r in results.items():
        print(f"  {r:20s} {os.path.basename(p)}")

    print(
        f"\nSummary: applied={applied} already-applied={already} no-match={no_match} errors={len(errors)} (dry-run={args.dry_run})"
    )

    if errors:
        for p, r in errors:
            print(f"  ERROR: {p}: {r}", file=sys.stderr)
        return 2

    if applied == 0 and already == 0:
        print(
            "FAIL: no target file matched the expected function body. Upstream may have refactored loadPluginMetadataSnapshot.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
