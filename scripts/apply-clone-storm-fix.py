#!/usr/bin/env python3
"""
apply-clone-storm-fix.py — durable mitigation for OC 2026.5.16-beta.2
"clone-storm": N×structuredClone of the cached plugin-metadata snapshot
per agent dispatch.

PROBLEM (companion to apply-snapshot-memo-multislot.py and PR #82619):

  Even with the snapshot Map memo working (#82619 / multislot patch), the
  cache-hit path in loadPluginMetadataSnapshot still calls
  clonePluginMetadataSnapshot → cloneSnapshotValue → structuredClone — a
  deep clone of the entire plugin-metadata graph (100+ plugins worth of
  manifest data on an active install).

  On the agent --local dispatch path, agentCommandInternal calls
  resolveDefaultModelForAgent → buildModelAliasIndex, which iterates every
  configured model alias. Each iteration ultimately calls
  loadManifestModelIdNormalizationPolicies(params), which calls
  resolveMetadataSnapshotForPolicies(params), which calls
  loadPluginMetadataSnapshot — triggering a full structuredClone per
  iteration. That's the "clone-storm" — one lookup amplifies to N JS
  deep-clones. CPU sampling confirms ~70% of agent-dispatch wall time is
  in clonePluginManifestRecord/cloneSnapshotValue.

  Before-patch: agent --local hangs to Layer 2 SIGKILL (timeout + 30s).
  After-patch: agent --local completes (PONG response observed in
  ~52s wall on a heavily-loaded test system; expected ~10-20s on idle).

FIX (this patch):

  Add a params-keyed cache in loadManifestModelIdNormalizationPolicies
  that short-circuits BEFORE resolveMetadataSnapshotForPolicies is called.
  The cache key is a JSON.stringify of the relevant params fields
  (config/env/workspaceDir/stateDir/preferPersisted). First call pays the
  snapshot fetch + clone cost; subsequent calls with identical params
  return cached policies without any snapshot work.

  Single-slot cache, last-key-wins. For buildModelAliasIndex's tight loop
  (same params each iteration), this collapses N structuredClones to 1.
  Worst case (alternating params): degenerate to unpatched behavior.

UPSTREAM:
  - Maintainer Shakker (2026-05-16 12:41 PM Discord): "My approach is not
    to introduce another cache but resolve hot paths that caused this
    regression in the first place." So his fix targets the CALLER (likely
    buildModelAliasIndex doing one snapshot fetch instead of N), not the
    cache layer. The two approaches are compatible — if Shakker reduces
    the call count, our cache simply never gets hit beyond the first call.
  - Our companion findings doc:
    workspace/docs/findings/2026-05-16-oc-beta2-cli-bootstrap-plugin-walk-loop.md

RETIREMENT CRITERION (this patch + apply-snapshot-memo-multislot.py are a
PAIRED set; retire together):

  1. Install the OC release that bundles Shakker's hot-path fix.
  2. Run regression smoke matrix WITH both patches still applied:
       /home/captain/.openclaw/workspace/scripts/regression-test.sh (or
       equivalent — agent --local + 5 CLI subcommands).
     All must complete cleanly. If anything regresses, KEEP the patches
     and investigate.
  3. With both patches still applied + clean smoke matrix, dry-run remove:
       python3 apply-snapshot-memo-multislot.py --dry-run
       python3 apply-clone-storm-fix.py --dry-run
     Confirm both still recognize their APPLIED_MARKER.
  4. Move BOTH patch scripts from apply-all.py active list to the
     "Retired" section in the apply-all.py docstring.
  5. Restore each file from its .bak-* backup, OR re-run `openclaw update`
     to pull a clean upstream dist.

  Do NOT retire piecemeal. snapshot-memo-multislot fixes the SOURCE of
  cache thrashing; this patch fixes the COST of the cache hit. Both are
  load-bearing on beta.2 until upstream covers both layers.

Usage:
  python3 apply-clone-storm-fix.py [--dry-run] [--dist-dir PATH]
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

BACKUP_SUFFIX = ".bak-clone-storm-fix"

# Content-addressed glob — survives bundle-hash churn across OC releases.
FILE_GLOB = "model-ref-shared-*.js"

# Marker: presence means this patch (or an equivalent upstream fix at a
# higher level) is already in place — exit 0 no-op.
APPLIED_MARKER = "policiesByParamsKeyCache"

OLD = (
    "function loadManifestModelIdNormalizationPolicies(params = {}) {\n"
    "\tif (params.plugins) return collectManifestModelIdNormalizationPolicies(params.plugins);\n"
    "\tconst { snapshot, cacheable } = resolveMetadataSnapshotForPolicies(params);\n"
    "\tconst configFingerprint = snapshot.configFingerprint;\n"
    "\tif (cacheable && configFingerprint && cachedPolicies?.configFingerprint === configFingerprint) return cachedPolicies.policies;\n"
    "\tconst policies = collectManifestModelIdNormalizationPolicies(snapshot.plugins);\n"
    "\tif (cacheable && configFingerprint) cachedPolicies = {\n"
    "\t\tconfigFingerprint,\n"
    "\t\tpolicies\n"
    "\t};\n"
    "\treturn policies;\n"
    "}\n"
)

NEW = (
    "// LOCAL PATCH (clone-storm-fix): cache the resolved policies on a\n"
    "// params-derived key BEFORE calling resolveMetadataSnapshotForPolicies (which is what\n"
    "// triggers clonePluginMetadataSnapshot / structuredClone on every call). On agent --local\n"
    "// dispatch, buildModelAliasIndex iterates over all configured models — each iteration\n"
    "// passing the same {provider, plugins, context} params object shape. Without this cache,\n"
    "// every iteration triggers a full structuredClone of the snapshot (~70% of agent --local\n"
    "// wall time on a 100-plugin install per CPU sampling). With this cache, only the first\n"
    "// iteration pays the clone cost; subsequent iterations hit the params-keyed cache and\n"
    "// return cached policies directly.\n"
    "let policiesByParamsKeyCache;\n"
    "function __policiesParamsKey(params) {\n"
    "\ttry {\n"
    "\t\treturn JSON.stringify({\n"
    "\t\t\tc: params.config ?? null,\n"
    "\t\t\te: params.env === void 0 || params.env === process.env ? \"$processEnv\" : \"$nondefault\",\n"
    "\t\t\tw: params.workspaceDir ?? null,\n"
    "\t\t\ts: params.stateDir ?? null,\n"
    "\t\t\tp: params.preferPersisted ?? null\n"
    "\t\t});\n"
    "\t} catch {\n"
    "\t\treturn undefined;\n"
    "\t}\n"
    "}\n"
    "function loadManifestModelIdNormalizationPolicies(params = {}) {\n"
    "\tif (params.plugins) return collectManifestModelIdNormalizationPolicies(params.plugins);\n"
    "\t// FAST PATH: params-keyed cache hit short-circuits BEFORE any snapshot fetch/clone.\n"
    "\tconst fastKey = __policiesParamsKey(params);\n"
    "\tif (fastKey !== undefined && policiesByParamsKeyCache?.key === fastKey) return policiesByParamsKeyCache.policies;\n"
    "\tconst { snapshot, cacheable } = resolveMetadataSnapshotForPolicies(params);\n"
    "\tconst configFingerprint = snapshot.configFingerprint;\n"
    "\tif (cacheable && configFingerprint && cachedPolicies?.configFingerprint === configFingerprint) {\n"
    "\t\tif (fastKey !== undefined) policiesByParamsKeyCache = { key: fastKey, policies: cachedPolicies.policies };\n"
    "\t\treturn cachedPolicies.policies;\n"
    "\t}\n"
    "\tconst policies = collectManifestModelIdNormalizationPolicies(snapshot.plugins);\n"
    "\tif (cacheable && configFingerprint) cachedPolicies = {\n"
    "\t\tconfigFingerprint,\n"
    "\t\tpolicies\n"
    "\t};\n"
    "\tif (fastKey !== undefined) policiesByParamsKeyCache = { key: fastKey, policies };\n"
    "\treturn policies;\n"
    "}\n"
)


def find_target(dist_dir: str) -> str:
    matches = glob.glob(os.path.join(dist_dir, FILE_GLOB))
    if not matches:
        sys.exit(f"ERROR: no file matching {FILE_GLOB} under {dist_dir}")
    if len(matches) > 1:
        sys.exit(f"ERROR: multiple matches: {matches}")
    return matches[0]


def apply(path: str, dry_run: bool) -> int:
    with open(path, "r", encoding="utf-8") as fp:
        original = fp.read()

    if APPLIED_MARKER in original:
        print(f"already-applied: {os.path.basename(path)} contains {APPLIED_MARKER!r}; no-op success")
        return 0

    if OLD not in original:
        print(f"ERROR: OLD anchor not found in {os.path.basename(path)}", file=sys.stderr)
        print("       bundle layout may have changed; re-derive anchor from current file.", file=sys.stderr)
        return 2

    patched = original.replace(OLD, NEW, 1)
    if patched == original:
        print("ERROR: replacement produced no change despite anchor match", file=sys.stderr)
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
    path = find_target(args.dist_dir)
    return apply(path, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
