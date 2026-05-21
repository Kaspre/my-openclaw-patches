#!/usr/bin/env python3
"""Patch: await agent_end hooks before one-shot local CLI exit.

Issue: local `openclaw agent --local` can finish the agent turn and tear down
the short-lived CLI process before fire-and-forget `agent_end` hooks settle.
That drops observation-only providers such as local OTEL spans.

This patch changes the installed generated bundles only:
  1. lifecycle-hook-helpers-*.js: make runAgentHarnessAgentEndHook async and
     await hookRunner.runAgentEnd(...) inside the existing catch/log boundary.
  2. cli-runner-*.js: await each local CLI terminal runAgentHarnessAgentEndHook
     call before returning from the one-shot command path.

Gateway/persistent runtime paths are not touched by this bundle patch.
"""

import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"

HELPER_BEFORE = """function runAgentHarnessAgentEndHook(params) {
\tconst hookRunner = params.hookRunner ?? getGlobalHookRunner();
\tif (!hookRunner?.hasHooks("agent_end") || typeof hookRunner.runAgentEnd !== "function") return;
\thookRunner.runAgentEnd(params.event, buildAgentHookContext(params.ctx)).catch((error) => {
\t\tlog.warn(`agent_end hook failed: ${String(error)}`);
\t});
}"""

HELPER_AFTER = """async function runAgentHarnessAgentEndHook(params) {
\tconst hookRunner = params.hookRunner ?? getGlobalHookRunner();
\tif (!hookRunner?.hasHooks("agent_end") || typeof hookRunner.runAgentEnd !== "function") return;
\ttry {
\t\tawait hookRunner.runAgentEnd(params.event, buildAgentHookContext(params.ctx));
\t} catch (error) {
\t\tlog.warn(`agent_end hook failed: ${String(error)}`);
\t}
}"""

HELPER_APPLIED_MARKER = "async function runAgentHarnessAgentEndHook(params)"
CLI_BARE_CALL_RE = re.compile(r"(?<!await )runAgentHarnessAgentEndHook\(")
CLI_AWAIT_CALL_RE = re.compile(r"await runAgentHarnessAgentEndHook\(")
EXPECTED_CLI_CALLS = 7


def find_candidates(dist: Path, pattern: str) -> list[Path]:
    matches = sorted(dist.glob(pattern))
    if not matches:
        raise RuntimeError(f"no {pattern} bundle found in {dist}")
    return matches


def patch_helper(path: Path, dry_run: bool) -> bool:
    content = path.read_text()
    if HELPER_AFTER in content or (
        HELPER_APPLIED_MARKER in content and "await hookRunner.runAgentEnd(" in content
    ):
        print(f"OK: {path.name}: agent_end helper already awaits runAgentEnd")
        return False

    count = content.count(HELPER_BEFORE)
    if count != 1:
        raise RuntimeError(f"{path.name}: expected one unpatched helper function, found {count}")

    if dry_run:
        print(f"DRY-RUN: would patch {path.name}: make runAgentHarnessAgentEndHook async")
        return True

    path.write_text(content.replace(HELPER_BEFORE, HELPER_AFTER, 1))
    print(f"APPLIED: {path.name}: make runAgentHarnessAgentEndHook async")
    return True


def patch_cli(path: Path, dry_run: bool) -> bool:
    content = path.read_text()
    bare = list(CLI_BARE_CALL_RE.finditer(content))
    awaited = list(CLI_AWAIT_CALL_RE.finditer(content))

    if not bare and len(awaited) == EXPECTED_CLI_CALLS:
        print(f"OK: {path.name}: all {EXPECTED_CLI_CALLS} local CLI agent_end calls already awaited")
        return False

    if len(bare) != EXPECTED_CLI_CALLS:
        raise RuntimeError(
            f"{path.name}: expected {EXPECTED_CLI_CALLS} bare local CLI agent_end calls, "
            f"found {len(bare)} bare and {len(awaited)} awaited"
        )

    if dry_run:
        print(f"DRY-RUN: would patch {path.name}: await {len(bare)} local CLI agent_end calls")
        return True

    patched = CLI_BARE_CALL_RE.sub("await runAgentHarnessAgentEndHook(", content)
    after_bare = len(CLI_BARE_CALL_RE.findall(patched))
    after_awaited = len(CLI_AWAIT_CALL_RE.findall(patched))
    if after_bare != 0 or after_awaited != EXPECTED_CLI_CALLS:
        raise RuntimeError(
            f"{path.name}: post-patch validation failed: bare={after_bare}, awaited={after_awaited}"
        )
    path.write_text(patched)
    print(f"APPLIED: {path.name}: await {EXPECTED_CLI_CALLS} local CLI agent_end calls")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply local CLI agent_end await patch")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    dist = args.dist_dir
    if not dist.exists():
        print(f"SKIP: dist dir not found: {dist}")
        return

    try:
        changed: list[bool] = []

        helper_count = 0
        for helper in find_candidates(dist, "lifecycle-hook-helpers-*.js"):
            content = helper.read_text()
            if HELPER_BEFORE in content or HELPER_APPLIED_MARKER in content:
                changed.append(patch_helper(helper, args.dry_run))
                helper_count += 1
            else:
                print(f"SKIP: {helper.name}: no agent_end helper function")
        if helper_count == 0:
            raise RuntimeError("no lifecycle-hook-helpers bundle with agent_end helper was patchable")

        cli_count = 0
        for cli in find_candidates(dist, "cli-runner-*.js"):
            if "runAgentHarnessAgentEndHook" not in cli.read_text():
                print(f"SKIP: {cli.name}: no local CLI agent_end helper call")
                continue
            changed.append(patch_cli(cli, args.dry_run))
            cli_count += 1
        if cli_count == 0:
            raise RuntimeError("no cli-runner bundle with local CLI agent_end calls was patchable")
    except RuntimeError as error:
        print(f"WARN: agent-local-agent-end-await structural drift: {error}")
        sys.exit(1)

    if not any(changed):
        print("OK: agent-local-agent-end-await already applied")


if __name__ == "__main__":
    main()
