#!/usr/bin/env python3
"""
Patch: fall through to activating load when runtime registry has zero web search providers
Upstream: open issue #68249 (filed 2026-04-17 for Brave, identical pattern).
          Draft of this patch is the basis for our own upstream PR.

Problem
-------
Native `web_search` tool never registers in OpenClaw 2026.4.14+ even with
correct config (provider set, API key valid, plugin enabled in plugins.allow).
Root cause: `resolvePluginWebProviders()` reads from the gateway-startup
"active" plugin registry. Bundled provider-only plugins (Exa, Brave,
Perplexity, Tavily, ...) never had their `register()` run during startup
because they expose no standalone agent tool — only an
`api.registerWebSearchProvider()` call. So the active registry has zero
web-search providers, `resolvePluginWebProviders` returns `[]`,
`resolveWebSearchDefinition` returns `null`, and `createWebSearchTool`
produces `null` (= not registered).

Firecrawl works because it ALSO calls `api.registerTool(...)` for its own
`firecrawl_search` / `firecrawl_scrape`. The tool-registration path activates
Firecrawl at startup and also wires up its provider. Provider-only plugins
don't trigger that path.

Full trace + empirical confirmation:
  ~/.openclaw/workspace/docs/findings/2026-04-17-native-web-search-activation.md

Fix
---
Two small semantic changes in `resolvePluginWebProviders`:

  1. Don't trust an empty compatible registry. When the active registry
     returns zero providers, fall through to a fresh load instead of
     returning `[]`.
  2. On the fall-through load, force `shouldActivate: true` so bundled
     provider-only plugins actually run their `register()` and wire up
     providers.

Cost: at most one extra plugin-load per process lifetime. Result is cached
via the existing `memoizeSnapshot` mechanism. Workloads whose active
registry already has providers (e.g., firecrawl-only) pay zero cost — the
non-empty `resolved.length > 0` check short-circuits before the fallback.

Target file
-----------
`dist/web-search-providers.runtime-*.js` (hash varies; script globs and
filters by the unique pattern at the `if (compatible) { ... }` block).

Related upstream reports
------------------------
  - #68249  [Bug]: Brave plugin enabled in config but not loaded at runtime
            — web_search tool unavailable (2026-04-17, same pattern)
  - #53857  web_search always reports "API key not configured" despite
            Perplexity/Brave keys set (persists in v2026.4.5)
  - #51937  web_search tool not available despite correct configuration
            (closed, reporter reports persistence)
  - #52677  web_search/web_fetch tools not available with built-in Gemini
            (closed — same symptom)
  - PR #53020  Agents: fix runtime web_search provider selection (merged
               but did not resolve the class of bug)

Usage
-----
  python3 apply-web-search-activate-on-empty.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-web-search-activate-on-empty"
TARGET_GLOB = "web-search-providers.runtime-*.js"

# Exact unpatched block. Tabs match Rollup/esbuild output.
OLD_CODE = """\tconst loadOptions = resolveWebProviderLoadOptions(params, deps);
\tconst compatible = resolveCompatibleRuntimePluginRegistry(loadOptions);
\tif (compatible) {
\t\tconst resolved = deps.mapRegistryProviders({
\t\t\tregistry: compatible,
\t\t\tonlyPluginIds: params.onlyPluginIds
\t\t});
\t\tmemoizeSnapshot(resolved);
\t\treturn resolved;
\t}
\tif (isPluginRegistryLoadInFlight(loadOptions)) return [];
\tconst resolved = deps.mapRegistryProviders({
\t\tregistry: loadOpenClawPlugins(loadOptions),
\t\tonlyPluginIds: params.onlyPluginIds
\t});"""

NEW_CODE = """\tconst loadOptions = resolveWebProviderLoadOptions(params, deps);
\tconst compatible = resolveCompatibleRuntimePluginRegistry(loadOptions);
\tif (compatible) {
\t\tconst resolved = deps.mapRegistryProviders({
\t\t\tregistry: compatible,
\t\t\tonlyPluginIds: params.onlyPluginIds
\t\t});
\t\t// LOCAL PATCH (web-search-activate-on-empty): the startup-time active
\t\t// registry doesn't activate bundled provider-only plugins (Exa, Brave,
\t\t// Perplexity, Tavily), so their api.registerWebSearchProvider() never
\t\t// fires and the registry has zero web-search providers. Falling through
\t\t// to a fresh activating load fixes native web_search registration.
\t\t// See: workspace/docs/findings/2026-04-17-native-web-search-activation.md
\t\tif (resolved.length > 0) {
\t\t\tmemoizeSnapshot(resolved);
\t\t\treturn resolved;
\t\t}
\t}
\tif (isPluginRegistryLoadInFlight(loadOptions)) return [];
\t// LOCAL PATCH (web-search-activate-on-empty): force activation AND bypass
\t// the plugin registry cache. The compatible registry sits in the cache with
\t// the same cacheKey we'd otherwise hit — reading it back would just return
\t// the same empty providers list. `cache: false` forces a fresh load;
\t// `activate: true` makes loadOpenClawPlugins run register() on bundled
\t// provider-only plugins (which the startup-time registry skipped). Note:
\t// loadOpenClawPlugins reads `options.activate` (not `shouldActivate`) —
\t// getting this wrong silently yields 0 providers because `activate: false`
\t// flows through to `shouldActivate = false`.
\tconst activatingLoadOptions = { ...loadOptions, activate: true, cache: false };
\tconst resolved = deps.mapRegistryProviders({
\t\tregistry: loadOpenClawPlugins(activatingLoadOptions),
\t\tonlyPluginIds: params.onlyPluginIds
\t});"""

ALREADY_PATCHED_MARKER = "LOCAL PATCH (web-search-activate-on-empty)"


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
        description="Apply web-search activate-on-empty patch"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Check pattern without modifying file")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT,
                        help=f"OpenClaw dist directory (default: {DIST_DIR_DEFAULT})")
    args = parser.parse_args()

    target = find_target(args.dist_dir)
    if not target:
        print(f"FAIL: could not find target file matching {TARGET_GLOB} "
              f"in {args.dist_dir}", file=sys.stderr)
        print("      (pattern may have moved in a new OC release; "
              "inspect dist/ for web-search-providers.runtime-*.js)", file=sys.stderr)
        sys.exit(1)

    status, basename = patch_file(target, dry_run=args.dry_run)

    if status == "already_patched":
        print(f"OK (already patched): {basename}")
        sys.exit(0)
    if status == "would_patch":
        print(f"DRY-RUN would patch: {basename}")
        sys.exit(0)
    if status == "patched":
        print(f"PATCHED: {basename} (backup at {basename}{BACKUP_SUFFIX})")
        print("        Gateway restart required to pick up changes.")
        sys.exit(0)
    if status == "pattern_not_found":
        print(f"FAIL: exact pattern not found in {basename}", file=sys.stderr)
        print("      OpenClaw dist/ layout may have changed; re-check the patch.",
              file=sys.stderr)
        sys.exit(1)
    if status.startswith("pattern_matched_"):
        n = status.split("_")[-1]
        print(f"FAIL: pattern matched {n} times in {basename}; expected 1. "
              f"Pattern is no longer unique — re-check the patch.", file=sys.stderr)
        sys.exit(1)

    print(f"FAIL: unknown status '{status}' for {basename}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
