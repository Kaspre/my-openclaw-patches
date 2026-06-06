#!/usr/bin/env python3
"""
apply-snapshot-memo-multislot.py — durable mitigation for OC 2026.5.16-beta.2
CLI bootstrap plugin-walk loop hang (TECH-2026-05-16-beta2-plugin-walk).

PROBLEM (see workspace/docs/findings/2026-05-16-oc-beta2-cli-bootstrap-plugin-walk-loop.md):

  On beta.2, every CLI subcommand that loads the plugin registry hangs in a
  tight loop that re-reads every plugin.json file at ~50 calls/sec. Root cause:
  the snapshot memo in dist/plugin-metadata-snapshot-<hash>.js is single-slot
  and the snapshot is called with two distinct memoKeys (workspaceDir set vs
  null) by different bootstrap callers, so they overwrite each other and miss
  on every call. Compounding bug: canMemoizePluginMetadataSnapshotResult only
  allows memoization of derived snapshots whose registry diagnostics are
  exclusively 'persisted-registry-stale-policy', rejecting our common
  'persisted-registry-stale-source' case.

FIX (this patch):

  Three edits, all in plugin-metadata-snapshot-<hash>.js:

    (1) STORAGE: replace single-slot `let pluginMetadataSnapshotMemo;` with a
        bounded LRU Map (8 entries) + a separate single-slot registry-state
        hint. Update clearLoadPluginMetadataSnapshotMemo() accordingly.

    (2) LOOKUP/STORE: rewrite loadPluginMetadataSnapshot() to look up via
        Map.get(), LRU-touch on hit, and Map.set() with eviction on store.
        Hint registry state is passed separately into
        resolvePersistedRegistryMemoStateForLookup().

    (3) ELIGIBILITY: extend canMemoizePluginMetadataSnapshotResult() to allow
        cacheable derived snapshots whose diagnostics are in
        {persisted-registry-stale-policy, persisted-registry-stale-source}.

Verified on OC 2026.5.16-beta.2 (dba00cb) by another session: 100 snapshot
calls in 8s went from 0 hits / 99 misses to ~179 hits / 3 misses (98.4% hit
rate). plugins list --json went from 25s timeout to 7.5-8.4s clean exit. All
five smoke-matrix CLI subcommands recovered.

RETIREMENT CRITERION (this patch + apply-clone-storm-fix.py are a
PAIRED set; retire together):

  This patch fixes the SOURCE of cache thrashing (single-slot memo can't
  hold alternating workspaceDir keys → 100% miss rate → rebuild loop).
  The paired clone-storm-fix patch fixes the COST of the cache hit
  (structuredClone of the cached snapshot on every hit, amplified by
  buildModelAliasIndex iterating N model aliases per agent dispatch).

  Both are load-bearing on beta.2 until upstream covers both layers.
  Maintainer Shakker (2026-05-16) is targeting the hot-path / caller layer
  rather than the cache layer — when his fix ships, both of our patches
  may become unnecessary together.

  Retirement steps: see RETIREMENT CRITERION in apply-clone-storm-fix.py
  — same process for both.

Usage:
  python3 apply-snapshot-memo-multislot.py [--dry-run] [--dist-dir PATH]

Idempotent: if the multi-slot marker is already present, exits 0 with
'already-applied'. If neither old nor patched form is found, fails loud.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.local/node-current/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-snapshot-memo-multislot"

# Content-addressed glob — survives bundle-hash churn across OC releases.
FILE_GLOB = "plugin-metadata-snapshot-*.js"

# Marker: presence in file means this patch (or an equivalent upstream fix)
# has already been applied. Skip-with-success in that case.
APPLIED_MARKER = "PLUGIN_METADATA_SNAPSHOT_MEMO_MAX_ENTRIES"

# ===== EDIT 1: STORAGE =====
# Replaces the single-slot let + simple clear() with bounded LRU Map + clear.

EDIT1_OLD = (
    "let pluginMetadataSnapshotMemo;\n"
    "function clearLoadPluginMetadataSnapshotMemo() {\n"
    "\tpluginMetadataSnapshotMemo = void 0;\n"
    "}\n"
)

EDIT1_NEW = (
    "const PLUGIN_METADATA_SNAPSHOT_MEMO_MAX_ENTRIES = 8;\n"
    "const pluginMetadataSnapshotMemo = /* @__PURE__ */ new Map();\n"
    "let pluginMetadataSnapshotRegistryStateMemo;\n"
    "function clearLoadPluginMetadataSnapshotMemo() {\n"
    "\tpluginMetadataSnapshotMemo.clear();\n"
    "\tpluginMetadataSnapshotRegistryStateMemo = void 0;\n"
    "}\n"
)

# ===== EDIT 2: LOOKUP + STORE =====
# Replaces the full loadPluginMetadataSnapshot function body.

EDIT2_OLD = (
    "function loadPluginMetadataSnapshot(params) {\n"
    "\tconst activeTimelineSpan = getActiveDiagnosticsTimelineSpan();\n"
    "\tconst memo = pluginMetadataSnapshotMemo;\n"
    "\tconst env = params.env ?? process.env;\n"
    "\tconst registryState = resolvePersistedRegistryMemoStateForLookup({\n"
    "\t\tenv,\n"
    "\t\t...params.stateDir ? { stateDir: resolveUserPath(params.stateDir, env) } : {},\n"
    "\t\t...params.preferPersisted !== void 0 ? { preferPersisted: params.preferPersisted } : {}\n"
    "\t}, memo);\n"
    "\tconst memoKey = computePluginMetadataSnapshotMemoKey({\n"
    "\t\tparams,\n"
    "\t\tregistryState\n"
    "\t});\n"
    "\tif (memo?.key === memoKey) return measureDiagnosticsTimelineSpanSync(\"plugins.metadata.scan\", () => clonePluginMetadataSnapshot(memo.snapshot), {\n"
    "\t\tphase: activeTimelineSpan?.phase ?? \"startup\",\n"
    "\t\tconfig: params.config,\n"
    "\t\tenv: params.env,\n"
    "\t\tattributes: {\n"
    "\t\t\tcacheHit: true,\n"
    "\t\t\thasWorkspaceDir: params.workspaceDir !== void 0,\n"
    "\t\t\thasInstalledIndex: params.index !== void 0\n"
    "\t\t}\n"
    "\t});\n"
    "\tconst result = measureDiagnosticsTimelineSpanSync(\"plugins.metadata.scan\", () => loadPluginMetadataSnapshotImpl(params), {\n"
    "\t\tphase: activeTimelineSpan?.phase ?? \"startup\",\n"
    "\t\tconfig: params.config,\n"
    "\t\tenv: params.env,\n"
    "\t\tattributes: {\n"
    "\t\t\thasWorkspaceDir: params.workspaceDir !== void 0,\n"
    "\t\t\thasInstalledIndex: params.index !== void 0\n"
    "\t\t}\n"
    "\t});\n"
    "\tif (canMemoizePluginMetadataSnapshotResult(result)) {\n"
    "\t\tconst cachedRegistryState = result.registrySource === \"derived\" ? resolvePersistedRegistryMemoState({\n"
    "\t\t\tenv,\n"
    "\t\t\tindex: result.snapshot.index,\n"
    "\t\t\t...params.stateDir ? { stateDir: resolveUserPath(params.stateDir, env) } : {},\n"
    "\t\t\t...params.preferPersisted !== void 0 ? { preferPersisted: params.preferPersisted } : {}\n"
    "\t\t}) : registryState;\n"
    "\t\tpluginMetadataSnapshotMemo = {\n"
    "\t\t\tkey: computePluginMetadataSnapshotMemoKey({\n"
    "\t\t\t\tparams,\n"
    "\t\t\t\tregistryState: cachedRegistryState\n"
    "\t\t\t}),\n"
    "\t\t\tregistryState: cachedRegistryState,\n"
    "\t\t\tsnapshot: clonePluginMetadataSnapshot(result.snapshot)\n"
    "\t\t};\n"
    "\t}\n"
    "\treturn result.snapshot;\n"
    "}\n"
)

EDIT2_NEW = (
    "function loadPluginMetadataSnapshot(params) {\n"
    "\tconst activeTimelineSpan = getActiveDiagnosticsTimelineSpan();\n"
    "\tconst env = params.env ?? process.env;\n"
    "\tconst registryState = resolvePersistedRegistryMemoStateForLookup({\n"
    "\t\tenv,\n"
    "\t\t...params.stateDir ? { stateDir: resolveUserPath(params.stateDir, env) } : {},\n"
    "\t\t...params.preferPersisted !== void 0 ? { preferPersisted: params.preferPersisted } : {}\n"
    "\t}, pluginMetadataSnapshotRegistryStateMemo);\n"
    "\tconst memoKey = computePluginMetadataSnapshotMemoKey({\n"
    "\t\tparams,\n"
    "\t\tregistryState\n"
    "\t});\n"
    "\tconst memo = pluginMetadataSnapshotMemo.get(memoKey);\n"
    "\tif (memo) {\n"
    "\t\tpluginMetadataSnapshotMemo.delete(memoKey);\n"
    "\t\tpluginMetadataSnapshotMemo.set(memoKey, memo);\n"
    "\t\treturn measureDiagnosticsTimelineSpanSync(\"plugins.metadata.scan\", () => clonePluginMetadataSnapshot(memo.snapshot), {\n"
    "\t\t\tphase: activeTimelineSpan?.phase ?? \"startup\",\n"
    "\t\t\tconfig: params.config,\n"
    "\t\t\tenv: params.env,\n"
    "\t\t\tattributes: {\n"
    "\t\t\t\tcacheHit: true,\n"
    "\t\t\t\thasWorkspaceDir: params.workspaceDir !== void 0,\n"
    "\t\t\t\thasInstalledIndex: params.index !== void 0\n"
    "\t\t\t}\n"
    "\t\t});\n"
    "\t}\n"
    "\tconst result = measureDiagnosticsTimelineSpanSync(\"plugins.metadata.scan\", () => loadPluginMetadataSnapshotImpl(params), {\n"
    "\t\tphase: activeTimelineSpan?.phase ?? \"startup\",\n"
    "\t\tconfig: params.config,\n"
    "\t\tenv: params.env,\n"
    "\t\tattributes: {\n"
    "\t\t\thasWorkspaceDir: params.workspaceDir !== void 0,\n"
    "\t\t\thasInstalledIndex: params.index !== void 0\n"
    "\t\t}\n"
    "\t});\n"
    "\tif (canMemoizePluginMetadataSnapshotResult(result)) {\n"
    "\t\tconst cachedRegistryState = result.registrySource === \"derived\" ? resolvePersistedRegistryMemoState({\n"
    "\t\t\tenv,\n"
    "\t\t\tindex: result.snapshot.index,\n"
    "\t\t\t...params.stateDir ? { stateDir: resolveUserPath(params.stateDir, env) } : {},\n"
    "\t\t\t...params.preferPersisted !== void 0 ? { preferPersisted: params.preferPersisted } : {}\n"
    "\t\t}) : registryState;\n"
    "\t\tconst cachedMemoKey = computePluginMetadataSnapshotMemoKey({\n"
    "\t\t\tparams,\n"
    "\t\t\tregistryState: cachedRegistryState\n"
    "\t\t});\n"
    "\t\tpluginMetadataSnapshotRegistryStateMemo = { registryState: cachedRegistryState };\n"
    "\t\tpluginMetadataSnapshotMemo.set(cachedMemoKey, {\n"
    "\t\t\tregistryState: cachedRegistryState,\n"
    "\t\t\tsnapshot: clonePluginMetadataSnapshot(result.snapshot)\n"
    "\t\t});\n"
    "\t\tif (pluginMetadataSnapshotMemo.size > PLUGIN_METADATA_SNAPSHOT_MEMO_MAX_ENTRIES) {\n"
    "\t\t\tconst oldestKey = pluginMetadataSnapshotMemo.keys().next().value;\n"
    "\t\t\tif (oldestKey !== void 0) pluginMetadataSnapshotMemo.delete(oldestKey);\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn result.snapshot;\n"
    "}\n"
)

# ===== EDIT 3: ELIGIBILITY =====

EDIT3_OLD = (
    "function canMemoizePluginMetadataSnapshotResult(result) {\n"
    "\tif (result.snapshot.index.plugins.length === 0) return false;\n"
    "\tif (result.registrySource !== \"derived\") return true;\n"
    "\treturn result.snapshot.registryDiagnostics.length > 0 && result.snapshot.registryDiagnostics.every((diagnostic) => diagnostic.code === \"persisted-registry-stale-policy\");\n"
    "}\n"
)

EDIT3_NEW = (
    "function canMemoizePluginMetadataSnapshotResult(result) {\n"
    "\tif (result.snapshot.index.plugins.length === 0) return false;\n"
    "\tif (result.registrySource !== \"derived\") return true;\n"
    "\tconst cacheableDerivedRegistryDiagnostics = /* @__PURE__ */ new Set([\n"
    "\t\t\"persisted-registry-stale-policy\",\n"
    "\t\t\"persisted-registry-stale-source\"\n"
    "\t]);\n"
    "\treturn result.snapshot.registryDiagnostics.length > 0 && result.snapshot.registryDiagnostics.every((diagnostic) => cacheableDerivedRegistryDiagnostics.has(diagnostic.code));\n"
    "}\n"
)

EDITS = [
    ("storage", EDIT1_OLD, EDIT1_NEW),
    ("lookup-store", EDIT2_OLD, EDIT2_NEW),
    ("eligibility", EDIT3_OLD, EDIT3_NEW),
]


def find_snapshot_file(dist_dir: str) -> str:
    candidates = []
    for path in glob.glob(os.path.join(dist_dir, FILE_GLOB)):
        # Skip the thin re-export shim (~< 1 KB, just imports/re-exports).
        if os.path.getsize(path) < 2048:
            continue
        candidates.append(path)
    if len(candidates) == 0:
        sys.exit(f"ERROR: no snapshot module matching {FILE_GLOB} (>=2 KB) under {dist_dir}")
    if len(candidates) > 1:
        joined = "\n  ".join(candidates)
        sys.exit(f"ERROR: multiple candidate snapshot modules:\n  {joined}\nNarrow FILE_GLOB or inspect manually.")
    return candidates[0]


def apply(path: str, dry_run: bool) -> int:
    with open(path, "r", encoding="utf-8") as fp:
        original = fp.read()

    if APPLIED_MARKER in original:
        print(f"already-applied: {os.path.basename(path)} contains {APPLIED_MARKER!r}; no-op success")
        return 0

    patched = original
    misses = []
    for name, old, new in EDITS:
        if old not in patched:
            misses.append(name)
            continue
        patched = patched.replace(old, new, 1)

    if misses:
        print(f"ERROR: anchor(s) not found in {os.path.basename(path)}: {', '.join(misses)}", file=sys.stderr)
        print("       bundle layout may have changed; re-derive anchors from the current file.", file=sys.stderr)
        return 2

    if patched == original:
        print(f"ERROR: replacement produced no change in {os.path.basename(path)} despite anchor match", file=sys.stderr)
        return 3

    if dry_run:
        print(f"dry-run: would patch {os.path.basename(path)} ({len(original)} -> {len(patched)} bytes)")
        return 0

    backup = path + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        print(f"backup -> {backup}")
    else:
        print(f"backup already exists -> {backup} (not overwriting)")

    with open(path, "w", encoding="utf-8") as fp:
        fp.write(patched)
    print(f"patched: {os.path.basename(path)} ({len(original)} -> {len(patched)} bytes)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--dist-dir", default=DIST_DIR_DEFAULT)
    args = p.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist-dir does not exist: {args.dist_dir}", file=sys.stderr)
        return 4

    path = find_snapshot_file(args.dist_dir)
    return apply(path, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
