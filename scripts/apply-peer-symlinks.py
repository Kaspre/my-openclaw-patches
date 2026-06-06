#!/usr/bin/env python3
"""Patch: recreate peer-package symlinks destroyed by `pnpm install --force`.

The 5.19 stable upgrade exposed a pnpm CAS contract change: with
`package-import-method=copy` (now required by the new
`@openclaw/fs-safe.openPinnedFileSync(rejectHardlinks=true)` manifest
validator), pnpm no longer hardlinks into the CAS — but it also does NOT
automatically re-create the per-package peer-symlinks under
`.pnpm/<scope>+<pkg>@<ver>*/node_modules/<peer>` when a `pnpm install --force`
runs. Three local plugins import from the global `openclaw` SDK package and
need this symlink for their `import "openclaw"` resolves:

  - @openclaw/codex
  - @openclaw/discord
  - @martian-engineering/lossless-claw

The symlinks survive normal `pnpm install`, but are destroyed by `--force`
(used by our upgrade.sh / pnpm post-extraction step). This script restores
them idempotently: if the target symlink already exists and resolves to the
right global SDK path, it leaves it alone; if it's missing or stale, it
creates / replaces it.

Target structure:
  ~/.openclaw/npm/node_modules/.pnpm/<scope+pkg>@<ver>_*/node_modules/openclaw
    → /home/captain/.local/node-current/lib/node_modules/openclaw

The script auto-discovers the active Node prefix via `node -p
"path.join(process.execPath, '..', '..', 'lib/node_modules/openclaw')"` so
it survives Node version bumps without manual updating.

Findings: workspace/docs/findings/2026-05-20-oc-5.19-upgrade-and-agent-local-observability.md §1
(plugin-loader changes in 5.19) + session recap 2026-05-21T03-56-41
(this manual recreation step listed as resume item #2).

This patch is idempotent and read-only when nothing needs changing. Safe to
include in the apply-all.py rotation.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# pnpm peer-package directory roots: ~/.openclaw/npm/node_modules/.pnpm/
PNPM_DIR = Path.home() / ".openclaw" / "npm" / "node_modules" / ".pnpm"

# Plugin packages that need an `openclaw` peer-symlink. The patterns match
# pnpm's CAS folder naming: `<scope>+<pkg>@<version>_<peer-hash>` (the
# `_<peer-hash>` suffix varies on every install). We glob for any matching
# version since plugins are version-pinned in ~/.openclaw/npm/package.json.
PLUGIN_GLOBS = [
    "@openclaw+codex@*",
    "@openclaw+discord@*",
    "@martian-engineering+lossless-claw@*",
]


def resolve_global_sdk_path() -> Path:
    """Find the active node's global `openclaw` SDK package directory.

    Uses `node -e` rather than hard-coding the nvm path so the patch survives
    Node version bumps. Falls back to a sensible default if `node` is unavailable.
    """
    try:
        result = subprocess.run(
            [
                "node",
                "-e",
                "console.log(require('path').join(process.execPath, '..', '..', 'lib/node_modules/openclaw'))",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return Path(result.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        # Fallback for environments without node on PATH.
        return Path.home() / ".local/node-current/lib/node_modules/openclaw"


def desired_link_targets(global_sdk: Path) -> list[tuple[Path, Path]]:
    """Yield (link_path, target_path) for every plugin that needs a peer-symlink.

    A plugin can have multiple matching .pnpm dirs across versions; we only
    create symlinks for those that lack one.
    """
    targets: list[tuple[Path, Path]] = []
    if not PNPM_DIR.exists():
        return targets
    for pattern in PLUGIN_GLOBS:
        for pkg_dir in sorted(PNPM_DIR.glob(pattern)):
            link = pkg_dir / "node_modules" / "openclaw"
            targets.append((link, global_sdk))
    return targets


def ensure_link(link: Path, target: Path, dry_run: bool) -> str:
    """Idempotently ensure `link` is a symlink to `target`.

    Returns a short status word: "ok", "created", "replaced", or "skip".
    """
    if link.is_symlink():
        current = os.readlink(link)
        if Path(current) == target:
            return "ok"
        # Stale symlink — replace.
        if dry_run:
            return "would-replace"
        link.unlink()
        link.symlink_to(target)
        return "replaced"
    if link.exists():
        # Real directory or file blocking us — refuse to clobber.
        return "skip (non-symlink in the way)"
    # Missing — create.
    if dry_run:
        return "would-create"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recreate peer-package symlinks under ~/.openclaw/npm/node_modules/.pnpm/"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without modifying anything")
    parser.add_argument(
        "--dist-dir",
        help=argparse.SUPPRESS,  # accepted but unused (apply-all.py passes it to every script)
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON summary instead of text")
    args = parser.parse_args()

    global_sdk = resolve_global_sdk_path()
    if not global_sdk.exists():
        print(f"SKIP: global SDK path not found: {global_sdk}")
        return 0

    targets = desired_link_targets(global_sdk)
    if not targets:
        print(f"SKIP: no plugin .pnpm dirs found under {PNPM_DIR}")
        return 0

    results: list[dict] = []
    overall_status = "OK"
    for link, target in targets:
        try:
            status = ensure_link(link, target, args.dry_run)
        except OSError as e:
            status = f"error: {e}"
            overall_status = "ERROR"
        rel_link = link.relative_to(PNPM_DIR.parent.parent)  # display under ~/.openclaw/...
        results.append({"link": str(rel_link), "target": str(target), "status": status})
        if not args.json:
            print(f"{status:>16}  {rel_link}")

    if args.json:
        print(json.dumps({"global_sdk": str(global_sdk), "results": results, "status": overall_status}, indent=2))

    return 0 if overall_status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
