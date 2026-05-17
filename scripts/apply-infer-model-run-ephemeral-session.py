#!/usr/bin/env python3
"""
Patch: infer model run --gateway ephemeral session

`openclaw infer model run --gateway` is documented as "Run a one-shot model
turn" but the dist code (capability-cli-*.js: runModelRun) attaches every
invocation to the default agent's persistent session. That session is the
"main" / Heartbeat lane, which accumulates context every minute. For
openai/* models routed through the codex harness, the harness then triggers
a "remote compact task" that ships the whole accumulated transcript back to
the model for summarization. Once that transcript exceeds the model's input
window (gpt-5.5 on ChatGPT-Plus OAuth), every dispatch fails with
context_length_exceeded — including model-health monitor probes.

Symptom (2026-05-16): model-health-monitor.py flipped openai/gpt-5.5 to
`down` at 19:10:02Z purely because of probe-side compact overflow; the
model itself was healthy (passive harvest of real agent turns confirms
24-32s clean turns through 18:39Z).

Fix: generate a fresh ephemeral sessionId per `infer model run --gateway`
invocation and pass it (with derived sessionKey) into the gateway "agent"
call. The gateway honors per-request sessionId+sessionKey (see
register.agent-*.js), and a fresh OC sessionId causes the codex harness to
spin up a fresh rollout — no inherited compact-overflow context.

Files: dist/capability-cli-*.js
PR: openclaw#82861 — MERGED 2026-05-17, ships in 2026.5.17. Retire this local
patch once we upgrade past 2026.5.16-beta.4.

Usage:
  python3 apply-infer-model-run-ephemeral-session.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import re
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-infer-ephemeral"

# Edit 1: anchor on the existing client-info import line so we can insert
# two new imports right after it. This anchor is unique enough.
OLD_IMPORTS_ANCHOR = (
    'import { i as GATEWAY_CLIENT_NAMES, r as GATEWAY_CLIENT_MODES } from "./client-info-CUFg6Tbz.js";'
)


def find_model_fallback_chunk(dist_dir):
    """Locate the active model-fallback-<hash>.js chunk, excluding bak and
    auth.runtime sibling files. Hash drifts on every release."""
    candidates = [
        os.path.basename(f)
        for f in glob.glob(os.path.join(dist_dir, "model-fallback-*.js"))
        if ".bak" not in f and "auth.runtime" not in os.path.basename(f)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly 1 model-fallback chunk in {dist_dir}, found {len(candidates)}: {candidates}"
        )
    return candidates[0]


def find_build_explicit_session_alias(dist_dir, chunk_filename):
    """Find the minified short alias for buildExplicitSessionIdSessionKey in
    the model-fallback chunk. The chunk exports as `buildExplicitSessionIdSessionKey as <X>`;
    we then import that short `<X>` and rename it back. `<X>` drifts per release."""
    path = os.path.join(dist_dir, chunk_filename)
    with open(path, "r") as f:
        content = f.read()
    match = re.search(r"buildExplicitSessionIdSessionKey\s+as\s+(\w+)", content)
    if not match:
        raise RuntimeError(
            f"buildExplicitSessionIdSessionKey export alias not found in {chunk_filename}"
        )
    return match.group(1)


def build_new_imports_block(dist_dir):
    chunk = find_model_fallback_chunk(dist_dir)
    alias = find_build_explicit_session_alias(dist_dir, chunk)
    return (
        OLD_IMPORTS_ANCHOR
        + f'\nimport {{ {alias} as buildExplicitSessionIdSessionKey }} from "./{chunk}";'
        + '\nimport { randomUUID as __inferEphemeralRandomUUID } from "node:crypto";'
    )

# Edit 2: inject sessionId+sessionKey into the gateway "agent" params object.
# Anchor on a unique line inside runModelRun's gateway branch — the
# idempotencyKey: randomIdempotencyKey() line is unique in this file.
# We add three lines BEFORE the call so the constants are in scope, and
# two new keys to the params object. To keep the anchor tight, we replace
# the small block that opens the params object.
OLD_PARAMS_OPEN = (
    "\tconst response = await callGateway({\n"
    "\t\tmethod: \"agent\",\n"
    "\t\tparams: {\n"
    "\t\t\tagentId,\n"
)
NEW_PARAMS_OPEN = (
    "\tconst __inferEphemeralSessionId = `model-run-${__inferEphemeralRandomUUID()}`;\n"
    "\tconst __inferEphemeralSessionKey = buildExplicitSessionIdSessionKey({\n"
    "\t\tagentId,\n"
    "\t\tsessionId: __inferEphemeralSessionId\n"
    "\t});\n"
    "\tconst response = await callGateway({\n"
    "\t\tmethod: \"agent\",\n"
    "\t\tparams: {\n"
    "\t\t\tagentId,\n"
    "\t\t\tsessionId: __inferEphemeralSessionId,\n"
    "\t\t\tsessionKey: __inferEphemeralSessionKey,\n"
)

MARKER = "__inferEphemeralSessionId"


def build_replacements(dist_dir):
    """REPLACEMENTS depend on dist_dir because the model-fallback chunk hash
    drifts on every release. Build at apply time, not module load time."""
    return [
        ("imports for ephemeral session helpers", OLD_IMPORTS_ANCHOR, build_new_imports_block(dist_dir)),
        ("inject ephemeral sessionId+sessionKey", OLD_PARAMS_OPEN, NEW_PARAMS_OPEN),
    ]


def find_capability_cli_files(dist_dir):
    pattern = os.path.join(dist_dir, "capability-cli-*.js")
    files = [f for f in glob.glob(pattern) if ".bak" not in f]
    return sorted(files)


def apply_patch(filepath, replacements, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    if MARKER in content:
        print(f"  SKIP (already patched): {os.path.basename(filepath)}")
        return False

    for name, old, new in replacements:
        count = content.count(old)
        if count == 0:
            print(f"  ERROR: pattern not found for '{name}' in {os.path.basename(filepath)}")
            return False
        if count > 1:
            print(f"  ERROR: pattern for '{name}' matched {count} times (expected 1) in {os.path.basename(filepath)}")
            return False
        content = content.replace(old, new)

    if dry_run:
        print(f"  DRY RUN OK: {os.path.basename(filepath)} — all {len(replacements)} replacements matched")
        return True

    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"  Backup: {os.path.basename(backup)}")

    with open(filepath, "w") as f:
        f.write(content)

    print(f"  PATCHED: {os.path.basename(filepath)} — {len(replacements)} replacements applied")
    return True


def main():
    parser = argparse.ArgumentParser(description="Patch infer model run --gateway to use ephemeral session")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="OpenClaw dist directory")
    args = parser.parse_args()

    files = find_capability_cli_files(args.dist_dir)
    if not files:
        print(f"ERROR: No capability-cli-*.js files found in {args.dist_dir}")
        sys.exit(1)

    try:
        replacements = build_replacements(args.dist_dir)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Infer Model Run Ephemeral Session Patch {'(DRY RUN)' if args.dry_run else ''}")
    print(f"Found {len(files)} capability-cli file(s)")
    print()

    patched = 0
    for f in files:
        if apply_patch(f, replacements, dry_run=args.dry_run):
            patched += 1

    print()
    if args.dry_run:
        print(f"Dry run complete: {patched}/{len(files)} files would be patched")
    else:
        print(f"Done: {patched}/{len(files)} files patched")
        if patched > 0:
            print("Restart gateway: systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
