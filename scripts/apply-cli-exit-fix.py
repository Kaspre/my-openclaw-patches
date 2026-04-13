#!/usr/bin/env python3
"""Patch: Add process.exit(0) after CLI command completion.

Issue: #63609 — CLI commands hang indefinitely after completing.
Fix: .then(() => { process.exit(0); }) on runCli promise resolution.
Files: dist/entry.js, dist/index.js
"""
import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v25.8.2/lib/node_modules/openclaw/dist"

PATCHES = [
    {
        "file": "entry.js",
        "description": "entry.js: process.exit after runCli resolves",
        "search": "=> runCli(argv)).catch(",
        "replace": "=> runCli(argv)).then(() => { process$1.exit(process$1.exitCode ?? 0); }).catch(",
    },
    {
        "file": "index.js",
        "description": "index.js: process.exit after runLegacyCliEntry resolves",
        "search": "runLegacyCliEntry(process.argv).catch(",
        "replace": "runLegacyCliEntry(process.argv).then(() => { process.exit(process.exitCode ?? 0); }).catch(",
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
