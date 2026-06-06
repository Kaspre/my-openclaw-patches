#!/usr/bin/env python3
"""
Patch: allow untracked global TS-source plugins to load (PR #80557).
Upstream: https://github.com/openclaw/openclaw/pull/80557 (filed by Kaspre,
          open at the time of writing). Fixes #80503.

Problem
-------
On OC 5.10.x, global discovery treats every extension dir under
`~/.openclaw/extensions/` that ships both `package.json` and
`openclaw.plugin.json` as a "managed package install" and demands
compiled JavaScript output. Local source-checkout plugins (the typical
shape for hand-installed extensions) are TypeScript-only and get
silently dropped with a "requires compiled runtime output for
TypeScript entry index.ts" warning.

Our environment hit this on `otel-observability/` (henrikrexed plugin
applied locally) — the plugin stopped loading after upgrade to 5.10,
even though it had been working on prior OC versions.

Fix (PR #80557)
---------------
Distinguish managed package installs (those named in install records)
from untracked source-checkout dirs. Untracked dirs may load TS source;
managed installs keep the compiled-output requirement.

Implementation: add a `requireBuiltRuntimeEntry` param threaded through
the discovery dispatch (default to current strict behavior when the
dir is in the managed set; explicit `false` when it isn't).

Targets
-------
1. `dist/discovery-*.js` — add `collectManagedPluginRecordPaths` and
   3 helper functions; thread `requireBuiltRuntimeEntry`,
   `managedPluginDirs`, `skipRootDirKeys` through `discoverInDirectory`,
   `discoverFromPath`, and `discoverOpenClawPlugins`.
2. `dist/package-entry-resolution-*.js` — honor the override in the
   dispatch check; add `sourceEntryLabel` to diagnostics; pass through
   wrappers.

Both bundles use hashed file names that change per OC release; the
script globs by prefix and filters by unique content markers.

Usage
-----
  python3 apply-plugin-ts-source-discovery-fix.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.local/node-current/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-80557"
DISCOVERY_GLOB = "discovery-*.js"
PER_GLOB = "package-entry-resolution-*.js"

ALREADY_PATCHED_MARKER = "LOCAL PATCH (PR #80557)"

# ---------------------------------------------------------------------------
# discovery-*.js replacements
# ---------------------------------------------------------------------------

DISCOVERY_REPLACEMENTS = [
    # 1. Add 4 helper functions after collectInstalledPluginRecordPaths.
    (
        "function collectInstalledPluginRecordPaths(installRecords, env) {\n"
        "\tconst paths = [];\n"
        "\tconst seen = /* @__PURE__ */ new Set();\n"
        "\tfor (const record of Object.values(installRecords ?? {})) {\n"
        "\t\tconst rawPath = typeof record.installPath === \"string\" && record.installPath.trim() ? record.installPath : typeof record.sourcePath === \"string\" && record.sourcePath.trim() ? record.sourcePath : void 0;\n"
        "\t\tif (!rawPath) continue;\n"
        "\t\tconst resolved = resolveUserPath(rawPath, env);\n"
        "\t\tif (seen.has(resolved) || !fs.existsSync(resolved)) continue;\n"
        "\t\tseen.add(resolved);\n"
        "\t\tpaths.push(resolved);\n"
        "\t}\n"
        "\treturn paths;\n"
        "}\n"
        "function readPackageManifest(dir, rejectHardlinks = true, rootRealPath) {",
        "function collectInstalledPluginRecordPaths(installRecords, env) {\n"
        "\tconst paths = [];\n"
        "\tconst seen = /* @__PURE__ */ new Set();\n"
        "\tfor (const record of Object.values(installRecords ?? {})) {\n"
        "\t\tconst rawPath = typeof record.installPath === \"string\" && record.installPath.trim() ? record.installPath : typeof record.sourcePath === \"string\" && record.sourcePath.trim() ? record.sourcePath : void 0;\n"
        "\t\tif (!rawPath) continue;\n"
        "\t\tconst resolved = resolveUserPath(rawPath, env);\n"
        "\t\tif (seen.has(resolved) || !fs.existsSync(resolved)) continue;\n"
        "\t\tseen.add(resolved);\n"
        "\t\tpaths.push(resolved);\n"
        "\t}\n"
        "\treturn paths;\n"
        "}\n"
        "// LOCAL PATCH (PR #80557): managed-dir classification for plugin discovery.\n"
        "// Untracked global extension dirs may load TS source; only dirs named by an\n"
        "// install record (installPath OR sourcePath) stay strict.\n"
        "function collectManagedPluginRecordPaths(installRecords, env) {\n"
        "\tconst paths = [];\n"
        "\tconst seen = /* @__PURE__ */ new Set();\n"
        "\tfor (const record of Object.values(installRecords ?? {})) {\n"
        "\t\tfor (const rawPath of [record.installPath, record.sourcePath]) {\n"
        "\t\t\tif (typeof rawPath !== \"string\" || !rawPath.trim()) continue;\n"
        "\t\t\tconst resolved = resolveUserPath(rawPath, env);\n"
        "\t\t\tif (seen.has(resolved) || !fs.existsSync(resolved)) continue;\n"
        "\t\t\tseen.add(resolved);\n"
        "\t\t\tpaths.push(resolved);\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn paths;\n"
        "}\n"
        "function resolveManagedPluginDirKey(installedPath, realpathCache) {\n"
        "\tconst stat = safeStatSync(installedPath);\n"
        "\tif (!stat) return null;\n"
        "\tconst pluginDir = stat.isFile() ? path.dirname(installedPath) : installedPath;\n"
        "\treturn safeRealpathSync(pluginDir, realpathCache) ?? path.resolve(pluginDir);\n"
        "}\n"
        "function collectManagedPluginDirKeys(installedPaths, realpathCache) {\n"
        "\tconst dirs = /* @__PURE__ */ new Set();\n"
        "\tfor (const installedPath of installedPaths) {\n"
        "\t\tconst key = resolveManagedPluginDirKey(installedPath, realpathCache);\n"
        "\t\tif (key) dirs.add(key);\n"
        "\t}\n"
        "\treturn dirs;\n"
        "}\n"
        "function isManagedPluginDir(params) {\n"
        "\tif (!params.managedPluginDirs || params.managedPluginDirs.size === 0) return false;\n"
        "\tconst key = params.realpath ?? safeRealpathSync(params.dir, params.realpathCache) ?? path.resolve(params.dir);\n"
        "\treturn params.managedPluginDirs.has(key);\n"
        "}\n"
        "function readPackageManifest(dir, rejectHardlinks = true, rootRealPath) {",
    ),
    # 2. discoverInDirectory: add requireBuiltRuntimeEntry computation,
    #    skip-root-dir-keys check, thread into setup/runtime calls.
    (
        "\t\tconst fullPathRealPath = safeRealpathSync(fullPath, params.realpathCache) ?? void 0;\n"
        "\t\tconst rejectHardlinks = shouldRejectHardlinkedPluginFiles({\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trootDir: fullPath,\n"
        "\t\t\tenv: params.env,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\tconst manifest = readCandidatePackageManifest({\n"
        "\t\t\tdir: fullPath,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trejectHardlinks,\n"
        "\t\t\t...fullPathRealPath !== void 0 ? { rootRealPath: fullPathRealPath } : {}\n"
        "\t\t});\n"
        "\t\tconst extensionResolution = resolvePackageExtensionEntries(manifest ?? void 0);\n"
        "\t\tconst extensions = extensionResolution.status === \"ok\" ? extensionResolution.entries : [];\n"
        "\t\tconst manifestId = resolveIdHintManifestId(fullPath, rejectHardlinks, fullPathRealPath);\n"
        "\t\tconst setupSource = resolvePackageSetupSource({\n"
        "\t\t\tpackageDir: fullPath,\n"
        "\t\t\t...fullPathRealPath !== void 0 ? { packageRootRealPath: fullPathRealPath } : {},\n"
        "\t\t\tmanifest,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\tsourceLabel: fullPath,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks\n"
        "\t\t});\n"
        "\t\tif (extensions.length > 0) {\n"
        "\t\t\tconst resolvedRuntimeSources = resolvePackageRuntimeExtensionSources({\n"
        "\t\t\t\tpackageDir: fullPath,\n"
        "\t\t\t\t...fullPathRealPath !== void 0 ? { packageRootRealPath: fullPathRealPath } : {},\n"
        "\t\t\t\tmanifest,\n"
        "\t\t\t\textensions,\n"
        "\t\t\t\torigin: params.origin,\n"
        "\t\t\t\tpluginIdHint: derivePackagePluginIdHint({\n"
        "\t\t\t\t\tmanifestId,\n"
        "\t\t\t\t\tpackageName: manifest?.name\n"
        "\t\t\t\t}),\n"
        "\t\t\t\tsourceLabel: fullPath,\n"
        "\t\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\t\trejectHardlinks\n"
        "\t\t\t});",
        "\t\tconst fullPathRealPath = safeRealpathSync(fullPath, params.realpathCache) ?? void 0;\n"
        "\t\t// LOCAL PATCH (PR #80557): skip dirs already covered by install-record discovery,\n"
        "\t\t// and compute requireBuiltRuntimeEntry from managed-dir classification.\n"
        "\t\tconst fullPathDirKey = fullPathRealPath ?? path.resolve(fullPath);\n"
        "\t\tif (params.skipRootDirKeys?.has(fullPathDirKey)) continue;\n"
        "\t\tconst requireBuiltRuntimeEntry = params.requireBuiltRuntimeEntry ?? isManagedPluginDir({\n"
        "\t\t\tdir: fullPath,\n"
        "\t\t\trealpath: fullPathRealPath,\n"
        "\t\t\tmanagedPluginDirs: params.managedPluginDirs,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\tconst rejectHardlinks = shouldRejectHardlinkedPluginFiles({\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trootDir: fullPath,\n"
        "\t\t\tenv: params.env,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\tconst manifest = readCandidatePackageManifest({\n"
        "\t\t\tdir: fullPath,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trejectHardlinks,\n"
        "\t\t\t...fullPathRealPath !== void 0 ? { rootRealPath: fullPathRealPath } : {}\n"
        "\t\t});\n"
        "\t\tconst extensionResolution = resolvePackageExtensionEntries(manifest ?? void 0);\n"
        "\t\tconst extensions = extensionResolution.status === \"ok\" ? extensionResolution.entries : [];\n"
        "\t\tconst manifestId = resolveIdHintManifestId(fullPath, rejectHardlinks, fullPathRealPath);\n"
        "\t\tconst setupSource = resolvePackageSetupSource({\n"
        "\t\t\tpackageDir: fullPath,\n"
        "\t\t\t...fullPathRealPath !== void 0 ? { packageRootRealPath: fullPathRealPath } : {},\n"
        "\t\t\tmanifest,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trequireBuiltRuntimeEntry,\n"
        "\t\t\tsourceLabel: fullPath,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks\n"
        "\t\t});\n"
        "\t\tif (extensions.length > 0) {\n"
        "\t\t\tconst resolvedRuntimeSources = resolvePackageRuntimeExtensionSources({\n"
        "\t\t\t\tpackageDir: fullPath,\n"
        "\t\t\t\t...fullPathRealPath !== void 0 ? { packageRootRealPath: fullPathRealPath } : {},\n"
        "\t\t\t\tmanifest,\n"
        "\t\t\t\textensions,\n"
        "\t\t\t\torigin: params.origin,\n"
        "\t\t\t\tpluginIdHint: derivePackagePluginIdHint({\n"
        "\t\t\t\t\tmanifestId,\n"
        "\t\t\t\t\tpackageName: manifest?.name\n"
        "\t\t\t\t}),\n"
        "\t\t\t\trequireBuiltRuntimeEntry,\n"
        "\t\t\t\tsourceLabel: fullPath,\n"
        "\t\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\t\trejectHardlinks\n"
        "\t\t\t});",
    ),
    # 3. discoverFromPath: same threading for the single-path scan.
    (
        "\tif (stat.isDirectory()) {\n"
        "\t\tconst resolvedRealPath = safeRealpathSync(resolved, params.realpathCache) ?? void 0;\n"
        "\t\tconst rejectHardlinks = shouldRejectHardlinkedPluginFiles({\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trootDir: resolved,\n"
        "\t\t\tenv: params.env,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\tconst manifest = readCandidatePackageManifest({\n"
        "\t\t\tdir: resolved,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trejectHardlinks,\n"
        "\t\t\t...resolvedRealPath !== void 0 ? { rootRealPath: resolvedRealPath } : {}\n"
        "\t\t});\n"
        "\t\tconst extensionResolution = resolvePackageExtensionEntries(manifest ?? void 0);\n"
        "\t\tconst extensions = extensionResolution.status === \"ok\" ? extensionResolution.entries : [];\n"
        "\t\tconst manifestId = resolveIdHintManifestId(resolved, rejectHardlinks, resolvedRealPath);\n"
        "\t\tconst setupSource = resolvePackageSetupSource({\n"
        "\t\t\tpackageDir: resolved,\n"
        "\t\t\t...resolvedRealPath !== void 0 ? { packageRootRealPath: resolvedRealPath } : {},\n"
        "\t\t\tmanifest,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\tsourceLabel: resolved,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks\n"
        "\t\t});\n"
        "\t\tif (extensions.length > 0) {\n"
        "\t\t\tconst resolvedRuntimeSources = resolvePackageRuntimeExtensionSources({\n"
        "\t\t\t\tpackageDir: resolved,\n"
        "\t\t\t\t...resolvedRealPath !== void 0 ? { packageRootRealPath: resolvedRealPath } : {},\n"
        "\t\t\t\tmanifest,\n"
        "\t\t\t\textensions,\n"
        "\t\t\t\torigin: params.origin,\n"
        "\t\t\t\tpluginIdHint: derivePackagePluginIdHint({\n"
        "\t\t\t\t\tmanifestId,\n"
        "\t\t\t\t\tpackageName: manifest?.name\n"
        "\t\t\t\t}),\n"
        "\t\t\t\tsourceLabel: resolved,\n"
        "\t\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\t\trejectHardlinks\n"
        "\t\t\t});",
        "\tif (stat.isDirectory()) {\n"
        "\t\tconst resolvedRealPath = safeRealpathSync(resolved, params.realpathCache) ?? void 0;\n"
        "\t\t// LOCAL PATCH (PR #80557): compute requireBuiltRuntimeEntry from managed-dir classification.\n"
        "\t\tconst requireBuiltRuntimeEntry = params.requireBuiltRuntimeEntry ?? isManagedPluginDir({\n"
        "\t\t\tdir: resolved,\n"
        "\t\t\trealpath: resolvedRealPath,\n"
        "\t\t\tmanagedPluginDirs: params.managedPluginDirs,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\tconst rejectHardlinks = shouldRejectHardlinkedPluginFiles({\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trootDir: resolved,\n"
        "\t\t\tenv: params.env,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\tconst manifest = readCandidatePackageManifest({\n"
        "\t\t\tdir: resolved,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trejectHardlinks,\n"
        "\t\t\t...resolvedRealPath !== void 0 ? { rootRealPath: resolvedRealPath } : {}\n"
        "\t\t});\n"
        "\t\tconst extensionResolution = resolvePackageExtensionEntries(manifest ?? void 0);\n"
        "\t\tconst extensions = extensionResolution.status === \"ok\" ? extensionResolution.entries : [];\n"
        "\t\tconst manifestId = resolveIdHintManifestId(resolved, rejectHardlinks, resolvedRealPath);\n"
        "\t\tconst setupSource = resolvePackageSetupSource({\n"
        "\t\t\tpackageDir: resolved,\n"
        "\t\t\t...resolvedRealPath !== void 0 ? { packageRootRealPath: resolvedRealPath } : {},\n"
        "\t\t\tmanifest,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\trequireBuiltRuntimeEntry,\n"
        "\t\t\tsourceLabel: resolved,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks\n"
        "\t\t});\n"
        "\t\tif (extensions.length > 0) {\n"
        "\t\t\tconst resolvedRuntimeSources = resolvePackageRuntimeExtensionSources({\n"
        "\t\t\t\tpackageDir: resolved,\n"
        "\t\t\t\t...resolvedRealPath !== void 0 ? { packageRootRealPath: resolvedRealPath } : {},\n"
        "\t\t\t\tmanifest,\n"
        "\t\t\t\textensions,\n"
        "\t\t\t\torigin: params.origin,\n"
        "\t\t\t\tpluginIdHint: derivePackagePluginIdHint({\n"
        "\t\t\t\t\tmanifestId,\n"
        "\t\t\t\t\tpackageName: manifest?.name\n"
        "\t\t\t\t}),\n"
        "\t\t\t\trequireBuiltRuntimeEntry,\n"
        "\t\t\t\tsourceLabel: resolved,\n"
        "\t\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\t\trejectHardlinks\n"
        "\t\t\t});",
    ),
    # 4. discoverFromPath: forward new params on the trailing discoverInDirectory recursion.
    (
        "\t\tdiscoverInDirectory({\n"
        "\t\t\tdir: resolved,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\tenv: params.env,\n"
        "\t\t\townershipUid: params.ownershipUid,\n"
        "\t\t\tworkspaceDir: params.workspaceDir,\n"
        "\t\t\tcandidates: params.candidates,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\tseen: params.seen,\n"
        "\t\t\trealpathCache: params.realpathCache\n"
        "\t\t});\n"
        "\t\treturn;\n"
        "\t}\n"
        "}\n"
        "function discoverOpenClawPlugins(params) {",
        "\t\tdiscoverInDirectory({\n"
        "\t\t\tdir: resolved,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\tenv: params.env,\n"
        "\t\t\townershipUid: params.ownershipUid,\n"
        "\t\t\tworkspaceDir: params.workspaceDir,\n"
        "\t\t\tcandidates: params.candidates,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\tseen: params.seen,\n"
        "\t\t\trealpathCache: params.realpathCache,\n"
        "\t\t\t// LOCAL PATCH (PR #80557): forward managed-dir context to nested scans.\n"
        "\t\t\t...params.requireBuiltRuntimeEntry !== void 0 ? { requireBuiltRuntimeEntry: params.requireBuiltRuntimeEntry } : {},\n"
        "\t\t\t...params.managedPluginDirs ? { managedPluginDirs: params.managedPluginDirs } : {},\n"
        "\t\t\t...params.skipRootDirKeys ? { skipRootDirKeys: params.skipRootDirKeys } : {}\n"
        "\t\t});\n"
        "\t\treturn;\n"
        "\t}\n"
        "}\n"
        "function discoverOpenClawPlugins(params) {",
    ),
    # 5. discoverOpenClawPlugins: compute managed-dir set and pass through.
    (
        "\t\tfor (const installedPath of collectInstalledPluginRecordPaths(params.installRecords, env)) discoverFromPath({\n"
        "\t\t\trawPath: installedPath,\n"
        "\t\t\torigin: \"global\",\n"
        "\t\t\townershipUid: params.ownershipUid,\n"
        "\t\t\tworkspaceDir,\n"
        "\t\t\tenv,\n"
        "\t\t\tcandidates: result.candidates,\n"
        "\t\t\tdiagnostics: result.diagnostics,\n"
        "\t\t\tseen,\n"
        "\t\t\trealpathCache\n"
        "\t\t});\n"
        "\t\tdiscoverInDirectory({\n"
        "\t\t\tdir: roots.global,\n"
        "\t\t\torigin: \"global\",\n"
        "\t\t\tenv,\n"
        "\t\t\townershipUid: params.ownershipUid,\n"
        "\t\t\tcandidates: result.candidates,\n"
        "\t\t\tdiagnostics: result.diagnostics,\n"
        "\t\t\tseen,\n"
        "\t\t\trealpathCache\n"
        "\t\t});",
        "\t\t// LOCAL PATCH (PR #80557): compute managed-dir set so global scan distinguishes\n"
        "\t\t// untracked source-checkout plugins from managed installs.\n"
        "\t\tconst installedPaths = collectInstalledPluginRecordPaths(params.installRecords, env);\n"
        "\t\tconst installedPluginDirKeys = collectManagedPluginDirKeys(installedPaths, realpathCache);\n"
        "\t\tconst managedPluginDirs = collectManagedPluginDirKeys(\n"
        "\t\t\tcollectManagedPluginRecordPaths(params.installRecords, env),\n"
        "\t\t\trealpathCache\n"
        "\t\t);\n"
        "\t\tfor (const installedPath of installedPaths) discoverFromPath({\n"
        "\t\t\trawPath: installedPath,\n"
        "\t\t\torigin: \"global\",\n"
        "\t\t\townershipUid: params.ownershipUid,\n"
        "\t\t\tworkspaceDir,\n"
        "\t\t\trequireBuiltRuntimeEntry: true,\n"
        "\t\t\tmanagedPluginDirs,\n"
        "\t\t\tenv,\n"
        "\t\t\tcandidates: result.candidates,\n"
        "\t\t\tdiagnostics: result.diagnostics,\n"
        "\t\t\tseen,\n"
        "\t\t\trealpathCache\n"
        "\t\t});\n"
        "\t\tdiscoverInDirectory({\n"
        "\t\t\tdir: roots.global,\n"
        "\t\t\torigin: \"global\",\n"
        "\t\t\tenv,\n"
        "\t\t\townershipUid: params.ownershipUid,\n"
        "\t\t\tmanagedPluginDirs,\n"
        "\t\t\tskipRootDirKeys: installedPluginDirKeys,\n"
        "\t\t\tcandidates: result.candidates,\n"
        "\t\t\tdiagnostics: result.diagnostics,\n"
        "\t\t\tseen,\n"
        "\t\t\trealpathCache\n"
        "\t\t});",
    ),
]

# ---------------------------------------------------------------------------
# package-entry-resolution-*.js replacements
# ---------------------------------------------------------------------------

PER_REPLACEMENTS = [
    # 1. Dispatch check: honor params.requireBuiltRuntimeEntry override.
    (
        "\t\tif (shouldRequireBuiltRuntimeEntry(params.origin) && isTypeScriptPackageEntry(safeEntry.relativePath)) {",
        "\t\tif ((params.requireBuiltRuntimeEntry ?? shouldRequireBuiltRuntimeEntry(params.origin)) && isTypeScriptPackageEntry(safeEntry.relativePath)) {",
    ),
    # 2. Trailing fallback: only attempt resolvePackageEntrySource for trusted callers; emit
    #    an explicit not-found diagnostic otherwise.
    # NOTE: beta.5 added `pluginIdHint` to the resolvePackageEntrySource call here.
    # We preserve it in the trusted branch.
    (
        "\tif (safeEntry.existingSource) return safeEntry.existingSource;\n"
        "\treturn resolvePackageEntrySource({\n"
        "\t\tpackageDir: params.packageDir,\n"
        "\t\t...params.packageRootRealPath !== void 0 ? { packageRootRealPath: params.packageRootRealPath } : {},\n"
        "\t\tentryPath: params.entryPath,\n"
        "\t\tpluginIdHint: params.pluginIdHint,\n"
        "\t\tsourceLabel: params.sourceLabel,\n"
        "\t\tdiagnostics: params.diagnostics,\n"
        "\t\trejectHardlinks: params.rejectHardlinks\n"
        "\t});\n"
        "}\n"
        "function resolvePackageSetupSource(params) {",
        "\tif (safeEntry.existingSource) return safeEntry.existingSource;\n"
        "\tif (params.rejectHardlinks === false) {\n"
        "\t\tconst trustedFallbackSource = resolvePackageEntrySource({\n"
        "\t\t\tpackageDir: params.packageDir,\n"
        "\t\t\t...params.packageRootRealPath !== void 0 ? { packageRootRealPath: params.packageRootRealPath } : {},\n"
        "\t\t\tentryPath: params.entryPath,\n"
        "\t\t\tpluginIdHint: params.pluginIdHint,\n"
        "\t\t\tsourceLabel: params.sourceLabel,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks: params.rejectHardlinks\n"
        "\t\t});\n"
        "\t\tif (trustedFallbackSource) return trustedFallbackSource;\n"
        "\t}\n"
        "\tparams.diagnostics.push({\n"
        "\t\tlevel: \"error\",\n"
        "\t\t...params.pluginIdHint ? { pluginId: params.pluginIdHint } : {},\n"
        "\t\tmessage: `${params.sourceEntryLabel ?? \"extension entry\"} not found: ${safeEntry.relativePath}`,\n"
        "\t\tsource: params.sourceLabel\n"
        "\t});\n"
        "\treturn null;\n"
        "}\n"
        "function resolvePackageSetupSource(params) {",
    ),
    # 3. resolvePackageSetupSource: pass sourceEntryLabel + requireBuiltRuntimeEntry through.
    (
        "function resolvePackageSetupSource(params) {\n"
        "\tconst packageManifest = getPackageManifestMetadata(params.manifest ?? void 0);\n"
        "\tconst setupEntryPath = normalizeOptionalString(packageManifest?.setupEntry);\n"
        "\tif (!setupEntryPath) return null;\n"
        "\treturn resolvePackageRuntimeEntrySource({\n"
        "\t\tpackageDir: params.packageDir,\n"
        "\t\t...params.packageRootRealPath !== void 0 ? { packageRootRealPath: params.packageRootRealPath } : {},\n"
        "\t\tentryPath: setupEntryPath,\n"
        "\t\truntimeEntryPath: normalizeOptionalString(packageManifest?.runtimeSetupEntry),\n"
        "\t\truntimeEntryLabel: \"runtime setup entry\",\n"
        "\t\tpluginIdHint: packageManifest?.plugin?.id ?? packageManifest?.channel?.id,\n"
        "\t\torigin: params.origin,\n"
        "\t\tsourceLabel: params.sourceLabel,\n"
        "\t\tdiagnostics: params.diagnostics,\n"
        "\t\trejectHardlinks: params.rejectHardlinks\n"
        "\t});\n"
        "}",
        "function resolvePackageSetupSource(params) {\n"
        "\tconst packageManifest = getPackageManifestMetadata(params.manifest ?? void 0);\n"
        "\tconst setupEntryPath = normalizeOptionalString(packageManifest?.setupEntry);\n"
        "\tif (!setupEntryPath) return null;\n"
        "\treturn resolvePackageRuntimeEntrySource({\n"
        "\t\tpackageDir: params.packageDir,\n"
        "\t\t...params.packageRootRealPath !== void 0 ? { packageRootRealPath: params.packageRootRealPath } : {},\n"
        "\t\tentryPath: setupEntryPath,\n"
        "\t\tsourceEntryLabel: \"setup entry\",\n"
        "\t\truntimeEntryPath: normalizeOptionalString(packageManifest?.runtimeSetupEntry),\n"
        "\t\truntimeEntryLabel: \"runtime setup entry\",\n"
        "\t\tpluginIdHint: packageManifest?.plugin?.id ?? packageManifest?.channel?.id,\n"
        "\t\torigin: params.origin,\n"
        "\t\t...params.requireBuiltRuntimeEntry !== void 0 ? { requireBuiltRuntimeEntry: params.requireBuiltRuntimeEntry } : {},\n"
        "\t\tsourceLabel: params.sourceLabel,\n"
        "\t\tdiagnostics: params.diagnostics,\n"
        "\t\trejectHardlinks: params.rejectHardlinks\n"
        "\t});\n"
        "}",
    ),
    # 4. resolvePackageRuntimeExtensionSources: thread requireBuiltRuntimeEntry through to each runtime resolve.
    (
        "\treturn params.extensions.flatMap((entryPath, index) => {\n"
        "\t\tconst source = resolvePackageRuntimeEntrySource({\n"
        "\t\t\tpackageDir: params.packageDir,\n"
        "\t\t\t...params.packageRootRealPath !== void 0 ? { packageRootRealPath: params.packageRootRealPath } : {},\n"
        "\t\t\tentryPath,\n"
        "\t\t\truntimeEntryPath: runtimeResolution.runtimeExtensions[index],\n"
        "\t\t\truntimeEntryLabel: \"runtime extension entry\",\n"
        "\t\t\tpluginIdHint: params.pluginIdHint,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\tsourceLabel: params.sourceLabel,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks: params.rejectHardlinks\n"
        "\t\t});\n"
        "\t\treturn source ? [source] : [];\n"
        "\t});",
        "\treturn params.extensions.flatMap((entryPath, index) => {\n"
        "\t\tconst source = resolvePackageRuntimeEntrySource({\n"
        "\t\t\tpackageDir: params.packageDir,\n"
        "\t\t\t...params.packageRootRealPath !== void 0 ? { packageRootRealPath: params.packageRootRealPath } : {},\n"
        "\t\t\tentryPath,\n"
        "\t\t\truntimeEntryPath: runtimeResolution.runtimeExtensions[index],\n"
        "\t\t\truntimeEntryLabel: \"runtime extension entry\",\n"
        "\t\t\tpluginIdHint: params.pluginIdHint,\n"
        "\t\t\torigin: params.origin,\n"
        "\t\t\t...params.requireBuiltRuntimeEntry !== void 0 ? { requireBuiltRuntimeEntry: params.requireBuiltRuntimeEntry } : {},\n"
        "\t\t\tsourceLabel: params.sourceLabel,\n"
        "\t\t\tdiagnostics: params.diagnostics,\n"
        "\t\t\trejectHardlinks: params.rejectHardlinks\n"
        "\t\t});\n"
        "\t\treturn source ? [source] : [];\n"
        "\t});",
    ),
]


def find_bundle(dist_dir, glob_pattern, anchor_text):
    """Find the (sole) dist file matching `glob_pattern` and containing `anchor_text`.
    Returns (path, content) or (None, None)."""
    candidates = [
        p for p in glob.glob(os.path.join(dist_dir, glob_pattern))
        if not p.endswith(BACKUP_SUFFIX)
    ]
    hits = []
    for c in candidates:
        try:
            with open(c, "r") as f:
                content = f.read()
            if anchor_text in content:
                hits.append((c, content))
        except OSError:
            pass
    if len(hits) == 1:
        return hits[0]
    return (None, None)


def apply_replacements(filepath, content, replacements, dry_run=False, already_patched_token=None):
    """Apply each (old, new) pair to content. Returns (status, basename, applied_count).

    `already_patched_token` is a string present only AFTER the file has been patched.
    Used for idempotent detection when no inline marker comment was added (the
    package-entry-resolution edits are purely semantic).
    """
    basename = os.path.basename(filepath)
    if ALREADY_PATCHED_MARKER in content:
        return ("already_patched", basename, 0)
    if already_patched_token and already_patched_token in content:
        return ("already_patched", basename, 0)

    new_content = content
    for i, (old, new) in enumerate(replacements):
        if old not in new_content:
            return (f"pattern_not_found:step_{i+1}", basename, 0)
        if new_content.count(old) != 1:
            return (f"pattern_matched_{new_content.count(old)}_times:step_{i+1}", basename, 0)
        new_content = new_content.replace(old, new, 1)

    if dry_run:
        return ("would_patch", basename, len(replacements))

    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)
    with open(filepath, "w") as f:
        f.write(new_content)
    return ("patched", basename, len(replacements))


def main():
    parser = argparse.ArgumentParser(
        description="Apply local equivalent of OC PR #80557 (untracked TS-source plugin loading)"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT)
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir does not exist: {args.dist_dir}", file=sys.stderr)
        sys.exit(1)

    discovery_path, discovery_content = find_bundle(
        args.dist_dir, DISCOVERY_GLOB, "collectInstalledPluginRecordPaths"
    )
    if not discovery_path:
        print(f"ERROR: no discovery-*.js bundle with collectInstalledPluginRecordPaths "
              f"found in {args.dist_dir}", file=sys.stderr)
        sys.exit(2)

    per_path, per_content = find_bundle(
        args.dist_dir, PER_GLOB, "shouldRequireBuiltRuntimeEntry"
    )
    if not per_path:
        print(f"ERROR: no package-entry-resolution-*.js bundle with "
              f"shouldRequireBuiltRuntimeEntry found in {args.dist_dir}", file=sys.stderr)
        sys.exit(2)

    overall_ok = True

    PER_PATCHED_TOKEN = "params.requireBuiltRuntimeEntry ?? shouldRequireBuiltRuntimeEntry"
    for path, content, replacements, label, token in (
        (discovery_path, discovery_content, DISCOVERY_REPLACEMENTS, "discovery", None),
        (per_path, per_content, PER_REPLACEMENTS, "package-entry-resolution", PER_PATCHED_TOKEN),
    ):
        status, basename, applied = apply_replacements(
            path, content, replacements, args.dry_run, already_patched_token=token
        )
        if status == "patched":
            print(f"OK [{label}]: patched {basename} ({applied} replacement(s); backup at {basename}{BACKUP_SUFFIX})")
        elif status == "would_patch":
            print(f"DRY-RUN [{label}]: would patch {basename} ({applied} replacement(s))")
        elif status == "already_patched":
            print(f"SKIP [{label}]: {basename} already patched")
        else:
            print(f"ERROR [{label}]: status='{status}' for {basename}", file=sys.stderr)
            print(f"  The bundle shape may have changed in this OC version.",
                  file=sys.stderr)
            print(f"  Review {basename} manually and update the matching block.",
                  file=sys.stderr)
            overall_ok = False

    sys.exit(0 if overall_ok else 3)


if __name__ == "__main__":
    main()
