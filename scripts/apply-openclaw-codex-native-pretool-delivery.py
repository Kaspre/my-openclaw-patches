#!/usr/bin/env python3
"""Patch: install the OpenClaw #90994 native PreToolUse delivery dist artifact.

This is the OpenClaw-side positive-delivery half of the Codex Code Mode
PreToolUse stack. It pairs with the local patched Codex binary installed by
apply-codex-codemode-pretooluse-binary.py.

The artifact is a full OpenClaw `dist/` build from:
  openclaw/openclaw#90994
  head 8e22ba40f04816459e90ac34c441becbad21215d
  OpenClaw package version 2026.6.1

It is intentionally version guarded. If OpenClaw moves past 2026.6.1, this
script fails rather than applying an old dist bundle to a new runtime.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path


HOME = Path.home()
ARTIFACT = (
    HOME
    / "my-openclaw-patches/artifacts/"
    "openclaw-2026.6.1-codex-native-pretool-delivery-90994-8e22ba40f0-dist.tar.gz"
)
ARTIFACT_SHA256 = "2bd104064e310e0e7e031b100b75449dfbc5d585bc5248a1bdacf29cdfa8c122"
EXPECTED_OPENCLAW_VERSION = "2026.6.1"
EXPECTED_PR = "openclaw/openclaw#90994"
EXPECTED_HEAD = "8e22ba40f04816459e90ac34c441becbad21215d"

CORE_MARKERS = [
    "preToolUsePolicyActive",
    'params.relay.preToolUsePolicyActive === true',
]
PLUGIN_MARKERS = [
    "preToolUsePolicyActive",
    'params.relay.preToolUsePolicyActive === true',
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def package_version_for_dist(dist_dir: Path) -> str | None:
    package_json = dist_dir.parent / "package.json"
    if not package_json.exists():
        return None
    try:
        return str(read_json(package_json).get("version", ""))
    except Exception:
        return None


def unique_existing(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        out.append(path)
    return out


def discover_core_dist_dirs(dist_dir: Path | None) -> list[Path]:
    if dist_dir is not None:
        return unique_existing([dist_dir])
    candidates = [
        HOME / ".local/node-current/lib/node_modules/openclaw/dist",
        HOME / ".openclaw/npm/node_modules/openclaw/dist",
    ]
    candidates.extend(
        Path(path)
        for path in glob.glob(str(HOME / ".nvm/versions/node/*/lib/node_modules/openclaw/dist"))
    )
    return unique_existing(candidates)


def select_expected_version_targets(targets: list[Path], label: str) -> tuple[list[Path], bool]:
    selected: list[Path] = []
    ok = True
    required = HOME / ".local/node-current/lib/node_modules/openclaw/dist"
    for target in targets:
        version = package_version_for_dist(target)
        if version == EXPECTED_OPENCLAW_VERSION:
            selected.append(target)
            continue
        if target == required:
            print(
                f"ERROR: required {label} target {target} is version "
                f"{version or '<unknown>'}; this artifact is for {EXPECTED_OPENCLAW_VERSION}.",
                file=sys.stderr,
            )
            ok = False
            continue
        print(
            f"[{EXPECTED_PR}] skip stale {label} target version "
            f"{version or '<unknown>'}: {target}"
        )
    return selected, ok


def discover_codex_plugin_dist_dirs(codex_dist_dir: Path | None) -> list[Path]:
    if codex_dist_dir is not None:
        return unique_existing([codex_dist_dir])
    return unique_existing(
        [
            Path(path)
            for path in glob.glob(
                str(
                    HOME
                    / ".openclaw/npm/projects/openclaw-codex-*/"
                    "node_modules/@openclaw/codex/dist"
                )
            )
        ]
    )


def file_contains_any(path: Path, markers: list[str]) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(marker in text for marker in markers)


def has_markers(dist_dir: Path, markers: list[str]) -> bool:
    js_files = list(dist_dir.glob("run-attempt-*.js")) + list(dist_dir.glob("native-hook-relay-*.js"))
    if not js_files:
        return False
    found = {marker: False for marker in markers}
    for path in js_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for marker in markers:
            if marker in text:
                found[marker] = True
    return all(found.values())


def validate_artifact() -> bool:
    if not ARTIFACT.exists():
        print(f"ERROR: missing artifact: {ARTIFACT}", file=sys.stderr)
        print(
            "Rebuild/copy the #90994 dist artifact before running apply-all.",
            file=sys.stderr,
        )
        return False
    got = sha256(ARTIFACT)
    if got != ARTIFACT_SHA256:
        print(
            f"ERROR: artifact sha256 mismatch: got {got}, expected {ARTIFACT_SHA256}",
            file=sys.stderr,
        )
        return False
    with tarfile.open(ARTIFACT, "r:gz") as tar:
        names = set(tar.getnames())
    required_prefixes = {
        "dist/": False,
        "extensions/codex/dist/": False,
    }
    for name in names:
        normalized = name.rstrip("/") + "/"
        for prefix in required_prefixes:
            if normalized == prefix or name.startswith(prefix):
                required_prefixes[prefix] = True
    missing = [prefix for prefix, present in required_prefixes.items() if not present]
    if missing:
        print(f"ERROR: artifact missing entries under: {', '.join(missing)}", file=sys.stderr)
        return False
    return True


def backup_dir(target: Path, suffix: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = target.parent / f"{target.name}.bak-{suffix}-{stamp}"
    candidate = base
    i = 1
    while candidate.exists():
        i += 1
        candidate = target.parent / f"{base.name}.{i}"
    shutil.copytree(target, candidate, symlinks=True)
    return candidate


def copy_tree_contents(src: Path, dst: Path) -> None:
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(child, target)


def install_artifact(core_targets: list[Path], plugin_targets: list[Path], dry_run: bool) -> bool:
    if dry_run:
        for target in core_targets:
            status = "already has markers" if has_markers(target, CORE_MARKERS) else "would install"
            print(f"[{EXPECTED_PR}] core {status}: {target}")
        for target in plugin_targets:
            status = "already has markers" if has_markers(target, PLUGIN_MARKERS) else "would install"
            print(f"[{EXPECTED_PR}] codex plugin {status}: {target}")
        return True

    with tempfile.TemporaryDirectory(prefix="openclaw-90994-dist-") as tmp:
        tmpdir = Path(tmp)
        with tarfile.open(ARTIFACT, "r:gz") as tar:
            tar.extractall(tmpdir)
        artifact_core = tmpdir / "dist"
        artifact_plugin = tmpdir / "extensions/codex/dist"

        for target in core_targets:
            bak = backup_dir(target, "codex-pretool-delivery")
            copy_tree_contents(artifact_core, target)
            if not has_markers(target, CORE_MARKERS):
                print(f"ERROR: core target missing markers after install: {target}", file=sys.stderr)
                return False
            print(f"[{EXPECTED_PR}] installed core dist: {target} (backup: {bak})")

        for target in plugin_targets:
            bak = backup_dir(target, "codex-pretool-delivery")
            copy_tree_contents(artifact_plugin, target)
            if not has_markers(target, PLUGIN_MARKERS):
                print(f"ERROR: codex plugin target missing markers after install: {target}", file=sys.stderr)
                return False
            print(f"[{EXPECTED_PR}] installed codex plugin dist: {target} (backup: {bak})")

    print("NOTE: graceful gateway restart required after applying this patch.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, help="single OpenClaw core dist dir")
    parser.add_argument("--codex-dist-dir", type=Path, help="single @openclaw/codex plugin dist dir")
    args = parser.parse_args()

    print(
        f"OpenClaw Codex native PreToolUse delivery patch ({EXPECTED_PR} {EXPECTED_HEAD[:12]})"
    )

    if not validate_artifact():
        return 1

    discovered_core_targets = discover_core_dist_dirs(args.dist_dir)
    discovered_plugin_targets = discover_codex_plugin_dist_dirs(args.codex_dist_dir)
    if not discovered_core_targets:
        print("ERROR: no OpenClaw core dist dirs found", file=sys.stderr)
        return 1
    if not discovered_plugin_targets:
        print("ERROR: no @openclaw/codex plugin dist dirs found", file=sys.stderr)
        return 1

    core_targets, core_ok = select_expected_version_targets(discovered_core_targets, "core dist")
    plugin_targets, plugin_ok = select_expected_version_targets(
        discovered_plugin_targets,
        "codex plugin dist",
    )
    if not core_ok or not plugin_ok:
        return 1
    if not core_targets:
        print(
            f"ERROR: no OpenClaw core dist dirs match {EXPECTED_OPENCLAW_VERSION}",
            file=sys.stderr,
        )
        return 1
    if not plugin_targets:
        print(
            f"ERROR: no @openclaw/codex plugin dist dirs match {EXPECTED_OPENCLAW_VERSION}",
            file=sys.stderr,
        )
        return 1

    if not install_artifact(core_targets, plugin_targets, args.dry_run):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
