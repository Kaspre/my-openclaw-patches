#!/usr/bin/env python3
"""Patch: install the patched codex binary that emits PreToolUse hooks for
Code Mode `exec` (openai/codex#23411 / "Bug A").

Background
----------
Upstream codex's `CodeModeExecuteHandler` does NOT implement
`pre_tool_use_payload`, so Code Mode `exec` (freeform JS) dispatches are invisible
to the PreToolUse hook chain -- the OpenClaw firewall never sees them and cannot
deny. Our fix (emit `pre_tool_use_payload` + `with_updated_hook_input`, modeled on
the merged apply_patch precedent openai/codex#18391) lives on the fork branch
`Kaspre/codex:rebase/code-mode-pretooluse-0.135` and is compiled into the artifact
referenced below.

IMPORTANT (2026-06-05): this patched binary is NECESSARY BUT NOT SUFFICIENT to
close the bypass. The OC-side codex PreToolUse hook DELIVERY ("Bug B") is still
dark on 6.1, so enforcement is NOT yet restored even with this binary. We keep it
deployed so the Bug-A side is in place for when Bug B is fixed, and tracked here so
an OpenClaw/codex upgrade (which restores the vendored musl binary) RE-APPLIES it
instead of silently reverting -- with a loud version-mismatch warning if the
artifact has gone stale.

Docs: workspace/docs/findings/2026-05-18-codex-code-mode-pretooluse-bypass.md ,
      workspace/patches/codex-codemode-pretooluse-binary.md . Beads: beads-workspace-8yp.

Behavior (idempotent)
---------------------
- Discovers the live codex plugin's bundled binary (npm/projects glob; the
  projects-hash changes across upgrades, so this is NOT hardcoded).
- already == our artifact (sha256) -> no-op.
- bundled `--version` != artifact version -> LOUD WARNING (codex upgraded; 0.135
  artifact is STALE, rebuild from the fork) + skip; exit 0 (does not break apply-all).
- same version, vendored binary restored -> rename-aside backup + install artifact
  (rename works on in-use binaries; ETXTBSY-safe). Gateway restart required after
  so running codex app-servers respawn onto the new binary.

The 204MB artifact is gitignored; rebuild from the fork via the GCP remote-build
config ~/.local/share/remote-build/codex-fwfix.conf if it is missing.
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
ARTIFACT = os.path.join(HOME, "my-openclaw-patches/artifacts/codex-0.135-codemode-pretooluse-fwfix")
ARTIFACT_SHA = "a2df3cff587102968ce402cc070ff1411f4265510b085a25b695208d6ac37438"
EXPECTED_VERSION = "0.135.0"  # codex-cli version this artifact was built against
TARGET_GLOB = os.path.join(
    HOME,
    ".openclaw/npm/projects/openclaw-codex-*/node_modules/@openclaw/codex/"
    "node_modules/@openai/codex-linux-x64/vendor/*/bin/codex",
)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def codex_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
        toks = (out.stdout + " " + out.stderr).split()
        return toks[-1] if toks else "<empty>"
    except Exception as e:  # noqa: BLE001
        return f"<error: {e}>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    tag = "[codex-codemode-pretooluse-binary]"

    if not os.path.exists(ARTIFACT):
        print(f"{tag} ⚠️  artifact MISSING: {ARTIFACT}")
        print(f"{tag}    Rebuild: ~/.local/bin/remote-build --config "
              f"~/.local/share/remote-build/codex-fwfix.conf  (from Kaspre/codex "
              f"rebase/code-mode-pretooluse-0.135). Skipping (exit 0).")
        return 0

    art_sha = sha256(ARTIFACT)
    if art_sha != ARTIFACT_SHA:
        print(f"{tag} ⚠️  artifact sha256 unexpected (got {art_sha[:12]}…, want "
              f"{ARTIFACT_SHA[:12]}…); refusing to install. Skipping.")
        return 0

    targets = sorted(glob.glob(TARGET_GLOB))
    if not targets:
        print(f"{tag} ⚠️  no codex bundled binary found (plugin not installed?). Skipping.")
        return 0

    changed = 0
    for tgt in targets:
        try:
            tgt_sha = sha256(tgt)
        except Exception as e:  # noqa: BLE001
            print(f"{tag}   ERROR reading {tgt}: {e}")
            return 1
        if tgt_sha == ARTIFACT_SHA:
            print(f"{tag}   OK (already patched): …{tgt[-60:]}")
            continue
        ver = codex_version(tgt)
        if ver != EXPECTED_VERSION:
            print(f"{tag}   ⚠️ ============================================================")
            print(f"{tag}   ⚠️ codex version changed: bundled={ver}, our patch={EXPECTED_VERSION}")
            print(f"{tag}   ⚠️ Code Mode PreToolUse fix is STALE for: …{tgt[-60:]}")
            print(f"{tag}   ⚠️ REBUILD from Kaspre/codex (rebase onto the new tag) + GCP build,")
            print(f"{tag}   ⚠️ then refresh ARTIFACT/ARTIFACT_SHA/EXPECTED_VERSION here.")
            print(f"{tag}   ⚠️ Skipping install (avoids a version-mismatched binary).")
            print(f"{tag}   ⚠️ ============================================================")
            continue
        if args.dry_run:
            print(f"{tag}   WOULD install (same-version vendored binary restored): …{tgt[-60:]}")
            changed += 1
            continue
        bak = f"{tgt}.bak-pre-fwfix-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        os.rename(tgt, bak)  # move in-use binary aside (rename works on running binary; serves as backup)
        try:
            shutil.copy(ARTIFACT, tgt)
            os.chmod(tgt, 0o755)
        except Exception:  # noqa: BLE001
            os.rename(bak, tgt)  # restore on failure
            raise
        print(f"{tag}   INSTALLED patched binary -> …{tgt[-60:]} (backup: {os.path.basename(bak)})")
        print(f"{tag}   NOTE: gateway restart required so running codex app-servers respawn onto it.")
        changed += 1

    if args.dry_run:
        print(f"{tag} [dry-run] {changed} target(s) would be patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
