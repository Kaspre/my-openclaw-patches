#!/usr/bin/env python3
"""Patch: inject a no-op `api.on(...)` hook into passive provider plugins
(exa, firecrawl) so they get activated in `openclaw agent --local` forks.

Root cause: `--local` agent forks filter the plugin registry by activation
trigger. The `manifest-hook-owner` trigger only fires for plugins that
called `api.on(...)` during their `register(api)` callback. Passive plugins
that only register provider contracts (exa, firecrawl) declare no hooks,
so they get no activation trigger, so they're not loaded in the --local
fork — even when enabled in user config and onStartup:true in their
manifest.

Filter source (per investigation 2026-05-12):
  dist/configured-Ce35-Dfp.js:37-38 — `if (hookNames.length === 0 && installId.trim()) return null;`
  dist/activation-planner-_rPyZ9dw.js:82 — manifest-hook-owner activation trigger
  dist/loader-CwgM3XmX.js:2192,3577 — populates hookNames during register()

Workaround: insert one no-op `api.on("before_agent_start", () => {})` call
into the `register(api)` body. That populates hookNames → triggers the
manifest-hook-owner activation → plugin loads in --local forks → its
registered provider becomes a web_search candidate.

This is a JSON+JS patch on bundled extension files, lost on every openclaw
update. apply-all.py runs this post-upgrade.

Captured 2026-05-12 during RC backlog burn investigation: Conan was failing
web_search calls despite full EXA_API_KEY + plugins.entries.exa.enabled=true
configuration, because exa wasn't loading in his --local agent fork.
"""
import argparse
import shutil
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist/extensions"

NOOP_HOOK = 'api.on("before_agent_start", () => {});'

# Exact strings we expect to match. Each plugin has a `register(api) { ... }`
# function inside `definePluginEntry({...})`. We append the no-op hook call
# at the start of the body, right after the opening brace, so it runs before
# the existing provider-registration calls.
TARGETS = {
    "exa": {
        "search": 'register(api) {\n\t\tapi.registerWebSearchProvider(createExaWebSearchProvider());\n\t}',
        "replace": 'register(api) {\n\t\tapi.on("before_agent_start", () => {});\n\t\tapi.registerWebSearchProvider(createExaWebSearchProvider());\n\t}',
    },
    "firecrawl": {
        # firecrawl's register body is multi-line; we anchor on the first registration call.
        # The patch script needs to verify the entry point before mutating.
        "marker": "register(api) {",
    },
}


def patch_exa(plugin_dir: Path, dry_run: bool) -> tuple[bool, str]:
    """Patch dist/extensions/exa/index.js."""
    index_js = plugin_dir / "index.js"
    if not index_js.exists():
        return False, f"index.js not found at {index_js}"

    content = index_js.read_text()
    spec = TARGETS["exa"]

    if NOOP_HOOK in content:
        return True, f"exa: hook already injected"

    if spec["search"] not in content:
        # Try a more lenient search — just find the register block opener
        if "register(api) {" not in content:
            return False, f"exa: register(api) opener not found — bundle shape may have changed"
        return False, f"exa: exact register body shape not found — manual review needed"

    if dry_run:
        return True, f"exa: would inject no-op hook"

    backup = index_js.with_suffix(".js.bak-passive-hook")
    if not backup.exists():
        shutil.copy(index_js, backup)
    index_js.write_text(content.replace(spec["search"], spec["replace"], 1))
    return True, f"exa: hook injected (backup: {backup.name})"


def patch_firecrawl(plugin_dir: Path, dry_run: bool) -> tuple[bool, str]:
    """Patch dist/extensions/firecrawl/index.js — append no-op hook at start of register body."""
    index_js = plugin_dir / "index.js"
    if not index_js.exists():
        return False, f"firecrawl: index.js not found at {index_js}"

    content = index_js.read_text()

    if NOOP_HOOK in content:
        return True, f"firecrawl: hook already injected"

    marker = "register(api) {"
    if marker not in content:
        return False, f"firecrawl: register(api) opener not found — bundle shape may have changed"

    # Insert NOOP_HOOK on its own line right after register(api) {
    # Inspect indentation by reading the line after marker
    idx = content.index(marker) + len(marker)
    # Detect the existing indentation of the first body line
    rest = content[idx:]
    # Skip leading newline
    if rest.startswith("\n"):
        # Look for indentation pattern (whitespace before first non-ws char)
        i = 1
        while i < len(rest) and rest[i] in (" ", "\t"):
            i += 1
        indent = rest[1:i]
        injection = f"\n{indent}{NOOP_HOOK}"
    else:
        injection = f" {NOOP_HOOK}"

    if dry_run:
        return True, f"firecrawl: would inject no-op hook (indent={len(injection)} chars)"

    backup = index_js.with_suffix(".js.bak-passive-hook")
    if not backup.exists():
        shutil.copy(index_js, backup)
    new_content = content[:idx] + injection + content[idx:]
    index_js.write_text(new_content)
    return True, f"firecrawl: hook injected (backup: {backup.name})"


def main():
    parser = argparse.ArgumentParser(description="Inject no-op hook into passive provider plugins")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    dist = args.dist_dir
    if not dist.exists():
        print(f"SKIP: dist extensions dir not found: {dist}")
        sys.exit(0)

    all_ok = True
    for plugin_id, patch_fn in [("exa", patch_exa), ("firecrawl", patch_firecrawl)]:
        plugin_dir = dist / plugin_id
        if not plugin_dir.is_dir():
            print(f"WARN: {plugin_id} plugin dir not found — may be uninstalled")
            all_ok = False
            continue
        ok, msg = patch_fn(plugin_dir, args.dry_run)
        prefix = "OK" if ok else "ERROR"
        if args.dry_run and "would inject" in msg:
            prefix = "DRY-RUN"
        print(f"{prefix}: {msg}")
        if not ok:
            all_ok = False

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
