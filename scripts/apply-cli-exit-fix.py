#!/usr/bin/env python3
"""Patch: Add process.exit(0) after CLI command completion.

Issue: #63609 — CLI commands hang indefinitely after completing.
Fix: .then(() => { process.exit(0); }) on runLegacyCliEntry promise resolution.
Files: dist/index.js

Re-enabled on v2026.5.12-beta.2 (2026-05-12): retired patch was validated only
against `openclaw config get` (upstream stopAndWait via PR #70691 covers that
path). The `openclaw agent --local` path still hangs post-session-end (matches
TECH-2026-2946/2947), causing RC backlog burn iterations to consume the full
60min wall-clock budget when real work completes in ~20min. This patch's
forced process.exit on runLegacyCliEntry's success path overrides whatever
plugin background handle is keeping the event loop alive.

The original entry.js half was dropped on re-enable: upstream moved `runCli`
to `await runCli(argv)` form (entry.js:470), so the search pattern is stale.
Additionally the original entry.js `replace` had a `process$1` typo (regex
backreference inside a plain str.replace — meaningless). If that path needs
re-patching, write a fresh search against the await-form.
"""
import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"

PATCHES = [
    {
        "file": "index.js",
        "description": "index.js: process.exit + SIGKILL fallback after runLegacyCliEntry resolves",
        # The .then() fires when the agent session ends, but process.exit() can be preempted
        # by ref'd handles from LCM compaction / otel exporter / plugin background loops,
        # causing agent --local to hang 60min until external SIGKILL. The setTimeout fallback
        # signals SIGKILL to self if process.exit hasn't taken effect within 3s.
        "search": "runLegacyCliEntry(process.argv).catch(",
        "replace": "runLegacyCliEntry(process.argv).then(() => { setTimeout(() => { try { process.kill(process.pid, 'SIGKILL'); } catch {} }, 3000); process.exit(process.exitCode ?? 0); }).catch(",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Apply CLI exit fix")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    dist = args.dist_dir
    if not dist.exists():
        print(f"SKIP: dist dir not found: {dist}")
        sys.exit(0)

    all_ok = True
    for patch in PATCHES:
        fpath = dist / patch["file"]
        if not fpath.exists():
            print(f"SKIP: {patch['file']} not found")
            continue

        content = fpath.read_text()

        if patch["replace"] in content:
            print(f"OK: {patch['description']} (already applied)")
            continue

        if patch["search"] not in content:
            print(f"WARN: {patch['description']} — search pattern not found (file may have changed)")
            all_ok = False
            continue

        if args.dry_run:
            print(f"DRY-RUN: would apply {patch['description']}")
            continue

        new_content = content.replace(patch["search"], patch["replace"], 1)
        fpath.write_text(new_content)
        print(f"APPLIED: {patch['description']}")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
