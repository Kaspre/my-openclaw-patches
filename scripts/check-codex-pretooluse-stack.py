#!/usr/bin/env python3
"""Static post-install check for the local Codex PreToolUse patch stack."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path


HOME = Path.home()
CODEX_ARTIFACT = HOME / "my-openclaw-patches/artifacts/codex-0.135-codemode-pretooluse-fwfix"
CODEX_SHA = "a2df3cff587102968ce402cc070ff1411f4265510b085a25b695208d6ac37438"
CODEX_TARGET_GLOB = str(
    HOME
    / ".openclaw/npm/projects/openclaw-codex-*/node_modules/@openclaw/codex/"
    "node_modules/@openai/codex-linux-x64/vendor/*/bin/codex"
)
DELIVERY_ARTIFACT = (
    HOME
    / "my-openclaw-patches/artifacts/"
    "openclaw-2026.6.1-codex-native-pretool-delivery-90994-8e22ba40f0-dist.tar.gz"
)
DELIVERY_SHA = "2bd104064e310e0e7e031b100b75449dfbc5d585bc5248a1bdacf29cdfa8c122"
OPENCLAW_VERSION = "2026.6.1"
DELIVERY_MARKERS = [
    "preToolUsePolicyActive",
    'params.relay.preToolUsePolicyActive === true',
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_version_for_dist(dist_dir: Path) -> str | None:
    package_json = dist_dir.parent / "package.json"
    if not package_json.exists():
        return None
    try:
        with package_json.open("r", encoding="utf-8") as f:
            return str(json.load(f).get("version", ""))
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


def core_dist_dirs() -> list[Path]:
    candidates = [
        HOME / ".local/node-current/lib/node_modules/openclaw/dist",
        HOME / ".openclaw/npm/node_modules/openclaw/dist",
    ]
    candidates.extend(
        Path(path)
        for path in glob.glob(str(HOME / ".nvm/versions/node/*/lib/node_modules/openclaw/dist"))
    )
    return unique_existing(candidates)


def codex_plugin_dist_dirs() -> list[Path]:
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


def has_delivery_markers(dist_dir: Path) -> bool:
    files = list(dist_dir.glob("run-attempt-*.js")) + list(dist_dir.glob("native-hook-relay-*.js"))
    found = {marker: False for marker in DELIVERY_MARKERS}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for marker in DELIVERY_MARKERS:
            if marker in text:
                found[marker] = True
    return all(found.values())


def select_expected_version_targets(targets: list[Path], label: str) -> tuple[list[Path], bool]:
    selected: list[Path] = []
    ok = True
    required = HOME / ".local/node-current/lib/node_modules/openclaw/dist"
    for dist_dir in targets:
        version = package_version_for_dist(dist_dir)
        if version == OPENCLAW_VERSION:
            selected.append(dist_dir)
            continue
        if dist_dir == required:
            print(
                f"FAIL openclaw-90994-delivery: required {label} target "
                f"{dist_dir} version {version or '<unknown>'} != {OPENCLAW_VERSION}"
            )
            ok = False
        else:
            print(
                f"SKIP openclaw-90994-delivery: stale {label} target "
                f"version {version or '<unknown>'}: {dist_dir}"
            )
    return selected, ok


def check_artifact(path: Path, expected_sha: str, label: str) -> bool:
    if not path.exists():
        print(f"FAIL {label}: artifact missing: {path}")
        return False
    got = sha256(path)
    if got != expected_sha:
        print(f"FAIL {label}: artifact sha256 mismatch got={got} expected={expected_sha}")
        return False
    print(f"OK   {label}: artifact hash matches")
    return True


def check_codex_binary() -> bool:
    ok = check_artifact(CODEX_ARTIFACT, CODEX_SHA, "codex-binary")
    targets = [Path(path) for path in sorted(glob.glob(CODEX_TARGET_GLOB))]
    if not targets:
        print("FAIL codex-binary: no installed bundled codex binary found")
        return False
    for target in targets:
        if sha256(target) == CODEX_SHA:
            print(f"OK   codex-binary: installed target hash matches: {target}")
        else:
            print(f"FAIL codex-binary: target is not patched: {target}")
            ok = False
    return ok


def check_delivery() -> bool:
    ok = check_artifact(DELIVERY_ARTIFACT, DELIVERY_SHA, "openclaw-90994-delivery")
    for label, discovered in [("core", core_dist_dirs()), ("codex-plugin", codex_plugin_dist_dirs())]:
        dirs, selected_ok = select_expected_version_targets(discovered, label)
        ok = selected_ok and ok
        if not dirs:
            print(f"FAIL openclaw-90994-delivery: no {label} dist dirs found for {OPENCLAW_VERSION}")
            ok = False
            continue
        for dist_dir in dirs:
            if has_delivery_markers(dist_dir):
                print(f"OK   openclaw-90994-delivery: {label} markers present: {dist_dir}")
            else:
                print(f"FAIL openclaw-90994-delivery: {label} markers missing: {dist_dir}")
                ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-fail-closed", action="store_true")
    args = parser.parse_args()

    ok = check_codex_binary()
    ok = check_delivery() and ok
    if args.require_fail_closed:
        print("FAIL fail-closed: no promoted local patch artifact is registered yet")
        ok = False
    else:
        print("WARN fail-closed: not checked; promote #90805 or narrower equivalent first")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
