#!/usr/bin/env python3
"""Patch: force activation.onStartup=true for bundled web-search plugins.

Issue: bundled web-search provider plugins (exa, firecrawl) declare
activation.onStartup=false in their manifests. The gateway loads them at
startup either way (verifiable via `openclaw plugins inspect exa`), but
`openclaw agent --local` forks load only plugins with hooks or onStartup:true.
Net effect: web-search providers register in the gateway registry but not in
the --local agent process registry, so `resolveRuntimeWebSearchProviders`
returns [] and the agent's web_search call fails with
"web_search is disabled or no provider is available."

Fix: flip onStartup → true for the providers we actually use (exa, firecrawl).
Skip tavily and others that are not enabled in user config.

This is a JSON-file patch on bundled plugin manifests, lost on every openclaw
update. apply-all.py runs this post-upgrade.

Captured 2026-05-12 during RC backlog burn investigation. Conan kept trying
web_search for claim verification and getting "disabled" errors despite full
EXA_API_KEY + plugins.entries.exa.enabled=true configuration.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".local/node-current/lib/node_modules/openclaw/dist/extensions"

TARGETS = ["exa", "firecrawl"]  # only the providers we have configured + API keys for


def main():
    parser = argparse.ArgumentParser(description="Force onStartup=true for web-search plugins")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    dist = args.dist_dir
    if not dist.exists():
        print(f"SKIP: dist extensions dir not found: {dist}")
        sys.exit(0)

    all_ok = True
    for plugin_id in TARGETS:
        manifest = dist / plugin_id / "openclaw.plugin.json"
        if not manifest.exists():
            print(f"WARN: {plugin_id}/openclaw.plugin.json not found — plugin may be uninstalled")
            all_ok = False
            continue

        try:
            m = json.load(open(manifest))
        except json.JSONDecodeError as e:
            print(f"ERROR: {plugin_id} manifest is not valid JSON: {e}")
            all_ok = False
            continue

        current = m.get("activation", {}).get("onStartup")
        if current is True:
            print(f"OK: {plugin_id} already onStartup=true")
            continue

        if args.dry_run:
            print(f"DRY-RUN: would patch {plugin_id} onStartup={current} → true")
            continue

        backup = manifest.with_suffix(".json.bak-onstartup-fix")
        if not backup.exists():
            shutil.copy(manifest, backup)
        m.setdefault("activation", {})["onStartup"] = True
        with open(manifest, "w") as f:
            json.dump(m, f, indent=2)
            f.write("\n")
        print(f"APPLIED: {plugin_id} onStartup={current} → true   (backup: {backup.name})")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
