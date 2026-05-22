#!/usr/bin/env python3
"""Patch: await agent_end hooks before one-shot local CLI exit.

Issue: local `openclaw agent --local` can finish the agent turn and tear down
the short-lived CLI process before fire-and-forget `agent_end` hooks settle.
That drops observation-only providers such as local OTEL spans.

This patch changes the installed generated bundles only:
  1. hook-runner-global-*.js: allow awaited agent_end callers to keep hook
     timeout timers ref'ed while preserving unref'ed defaults.
  2. lifecycle-hook-helpers-*.js: keep the public runAgentHarnessAgentEndHook
     fire-and-forget, and add an internal awaited helper for one-shot CLI.
  3. cli-runner-*.js: await each local CLI terminal awaited helper call before
     returning from the one-shot command path.
  4. run-attempt-*.js: await agent_end for no-channel Codex app-server
     attempts, which are also one-shot for `openclaw agent --local`, while
     preserving fire-and-forget channel-backed gateway attempts.

Channel-backed gateway runtime paths are intentionally left fire-and-forget.
"""

import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"

HOOK_RUNNER_VOID_BEFORE = """async function runVoidHook(hookName, event, ctx) {
\t\tconst hooks = getHooksForName(registry, hookName);
\t\tif (hooks.length === 0) return;
\t\tlogger?.debug?.(`[hooks] running ${hookName} (${hooks.length} handlers)`);
\t\tconst promises = hooks.map(async (hook) => {
\t\t\ttry {
\t\t\t\tconst promise = Promise.resolve(hook.handler(event, ctx));
\t\t\t\tconst timeoutMs = getVoidHookTimeoutMs(hookName, hook);
\t\t\t\tif (timeoutMs) await withHookTimeout(promise, timeoutMs, { unref: true });
\t\t\t\telse await promise;
\t\t\t} catch (err) {"""

HOOK_RUNNER_VOID_AFTER = """async function runVoidHook(hookName, event, ctx, options = {}) {
\t\tconst hooks = getHooksForName(registry, hookName);
\t\tif (hooks.length === 0) return;
\t\tlogger?.debug?.(`[hooks] running ${hookName} (${hooks.length} handlers)`);
\t\tconst promises = hooks.map(async (hook) => {
\t\t\ttry {
\t\t\t\tconst promise = Promise.resolve(hook.handler(event, ctx));
\t\t\t\tconst timeoutMs = getVoidHookTimeoutMs(hookName, hook);
\t\t\t\tif (timeoutMs) await withHookTimeout(promise, timeoutMs, { unref: options.unrefTimeout ?? true });
\t\t\t\telse await promise;
\t\t\t} catch (err) {"""

HOOK_RUNNER_AGENT_END_BEFORE = """async function runAgentEnd(event, ctx) {
\t\treturn runVoidHook("agent_end", withAgentRunId(event, ctx), ctx);
\t}"""

HOOK_RUNNER_AGENT_END_AFTER = """async function runAgentEnd(event, ctx, options) {
\t\treturn runVoidHook("agent_end", withAgentRunId(event, ctx), ctx, options);
\t}"""

HOOK_RUNNER_APPLIED_MARKER = "options.unrefTimeout ?? true"

HELPER_ORIGINAL_BEFORE = """function runAgentHarnessAgentEndHook(params) {
\tconst hookRunner = params.hookRunner ?? getGlobalHookRunner();
\tif (!hookRunner?.hasHooks("agent_end") || typeof hookRunner.runAgentEnd !== "function") return;
\thookRunner.runAgentEnd(params.event, buildAgentHookContext(params.ctx)).catch((error) => {
\t\tlog.warn(`agent_end hook failed: ${String(error)}`);
\t});
}"""

HELPER_INTERMEDIATE_BEFORE = """async function runAgentHarnessAgentEndHook(params) {
\tconst hookRunner = params.hookRunner ?? getGlobalHookRunner();
\tif (!hookRunner?.hasHooks("agent_end") || typeof hookRunner.runAgentEnd !== "function") return;
\ttry {
\t\tawait hookRunner.runAgentEnd(params.event, buildAgentHookContext(params.ctx));
\t} catch (error) {
\t\tlog.warn(`agent_end hook failed: ${String(error)}`);
\t}
}"""

HELPER_AFTER = """async function executeAgentHarnessAgentEndHook(params) {
\tconst hookRunner = params.hookRunner ?? getGlobalHookRunner();
\tif (!hookRunner?.hasHooks("agent_end") || typeof hookRunner.runAgentEnd !== "function") return;
\ttry {
\t\tconst options = { unrefTimeout: params.unrefTimeout ?? false };
\t\tawait hookRunner.runAgentEnd(params.event, buildAgentHookContext(params.ctx), options);
\t} catch (error) {
\t\tlog.warn(`agent_end hook failed: ${String(error)}`);
\t}
}
function runAgentHarnessAgentEndHook(params) {
\tvoid executeAgentHarnessAgentEndHook({ ...params, unrefTimeout: true });
}
async function awaitAgentHarnessAgentEndHook(params) {
\tawait executeAgentHarnessAgentEndHook({ ...params, unrefTimeout: false });
}"""

HELPER_APPLIED_MARKER = "async function awaitAgentHarnessAgentEndHook(params)"
HELPER_EXPORT_BEFORE = "export { buildAgentHookContext as a, runAgentHarnessLlmOutputHook as i, runAgentHarnessBeforeAgentFinalizeHook as n, runAgentHarnessLlmInputHook as r, runAgentHarnessAgentEndHook as t };"
HELPER_EXPORT_AFTER = "export { buildAgentHookContext as a, runAgentHarnessLlmOutputHook as i, awaitAgentHarnessAgentEndHook as l, runAgentHarnessBeforeAgentFinalizeHook as n, runAgentHarnessLlmInputHook as r, runAgentHarnessAgentEndHook as t };"
# The lifecycle-hook-helpers filename hash rotates on every upstream build,
# so derive it from the bundle under patch instead of hard-coding it.
HELPER_HASH_RE = re.compile(r'"\./lifecycle-hook-helpers-([A-Za-z0-9_-]+)\.js"')
CLI_IMPORT_BEFORE_TMPL = 'import {{ a as buildAgentHookContext, i as runAgentHarnessLlmOutputHook, r as runAgentHarnessLlmInputHook, t as runAgentHarnessAgentEndHook }} from "./lifecycle-hook-helpers-{hash}.js";'
CLI_IMPORT_AFTER_TMPL = 'import {{ a as buildAgentHookContext, i as runAgentHarnessLlmOutputHook, l as awaitAgentHarnessAgentEndHook, r as runAgentHarnessLlmInputHook }} from "./lifecycle-hook-helpers-{hash}.js";'
CLI_BARE_CALL_RE = re.compile(r"(?<!await )runAgentHarnessAgentEndHook\(")
CLI_INTERMEDIATE_AWAIT_CALL_RE = re.compile(r"await runAgentHarnessAgentEndHook\(")
CLI_FINAL_AWAIT_CALL_RE = re.compile(r"await awaitAgentHarnessAgentEndHook\(")
EXPECTED_CLI_CALLS = 7
RUN_ATTEMPT_IMPORT_BEFORE_TMPL = 'import {{ i as runAgentHarnessLlmOutputHook, r as runAgentHarnessLlmInputHook, t as runAgentHarnessAgentEndHook }} from "./lifecycle-hook-helpers-{hash}.js";'
RUN_ATTEMPT_IMPORT_AFTER_TMPL = 'import {{ i as runAgentHarnessLlmOutputHook, l as awaitAgentHarnessAgentEndHook, r as runAgentHarnessLlmInputHook, t as runAgentHarnessAgentEndHook }} from "./lifecycle-hook-helpers-{hash}.js";'


def _resolve_helper_hash(path: Path, content: str) -> str:
    match = HELPER_HASH_RE.search(content)
    if not match:
        raise RuntimeError(f"{path.name}: could not locate lifecycle-hook-helpers import")
    return match.group(1)
RUN_ATTEMPT_HELPER_BEFORE = """async function runCodexAppServerAttempt(params, options = {}) {"""
RUN_ATTEMPT_HELPER_AFTER = """function shouldAwaitCodexAgentEndHook(params) {
\treturn !params.messageChannel && !params.messageProvider;
}
async function runCodexAgentEndHook(params, hookParams) {
\tif (shouldAwaitCodexAgentEndHook(params)) {
\t\tawait awaitAgentHarnessAgentEndHook(hookParams);
\t\treturn;
\t}
\trunAgentHarnessAgentEndHook(hookParams);
}
async function runCodexAppServerAttempt(params, options = {}) {"""
RUN_ATTEMPT_CALL_RE = re.compile(r"(?<!await )runAgentHarnessAgentEndHook\(\{")
RUN_ATTEMPT_FINAL_CALL_RE = re.compile(r"await runCodexAgentEndHook\(params, ")
EXPECTED_RUN_ATTEMPT_CALLS = 2


def find_candidates(dist: Path, pattern: str) -> list[Path]:
    matches = sorted(dist.glob(pattern))
    if not matches:
        raise RuntimeError(f"no {pattern} bundle found in {dist}")
    return matches


def patch_helper(path: Path, dry_run: bool) -> bool:
    content = path.read_text()
    if HELPER_AFTER in content and HELPER_EXPORT_AFTER in content:
        print(f"OK: {path.name}: agent_end helper has fire-and-forget and awaited variants")
        return False

    before = None
    if HELPER_INTERMEDIATE_BEFORE in content:
        before = HELPER_INTERMEDIATE_BEFORE
    elif HELPER_ORIGINAL_BEFORE in content:
        before = HELPER_ORIGINAL_BEFORE
    if before is None:
        raise RuntimeError(f"{path.name}: expected one known agent_end helper function")

    if dry_run:
        print(f"DRY-RUN: would patch {path.name}: add awaited agent_end helper")
        return True

    patched = content.replace(before, HELPER_AFTER, 1)
    if HELPER_EXPORT_AFTER not in patched:
        patched = patched.replace(HELPER_EXPORT_BEFORE, HELPER_EXPORT_AFTER, 1)
    if HELPER_AFTER not in patched or HELPER_EXPORT_AFTER not in patched:
        raise RuntimeError(f"{path.name}: post-patch helper validation failed")
    path.write_text(patched)
    print(f"APPLIED: {path.name}: add awaited agent_end helper")
    return True


def patch_cli(path: Path, dry_run: bool) -> bool:
    content = path.read_text()
    helper_hash = _resolve_helper_hash(path, content)
    cli_import_before = CLI_IMPORT_BEFORE_TMPL.format(hash=helper_hash)
    cli_import_after = CLI_IMPORT_AFTER_TMPL.format(hash=helper_hash)
    bare = list(CLI_BARE_CALL_RE.finditer(content))
    intermediate = list(CLI_INTERMEDIATE_AWAIT_CALL_RE.finditer(content))
    final = list(CLI_FINAL_AWAIT_CALL_RE.finditer(content))

    if not bare and not intermediate and len(final) == EXPECTED_CLI_CALLS and cli_import_after in content:
        print(f"OK: {path.name}: all {EXPECTED_CLI_CALLS} local CLI agent_end calls use awaited helper")
        return False

    if len(bare) != EXPECTED_CLI_CALLS and len(intermediate) != EXPECTED_CLI_CALLS:
        raise RuntimeError(
            f"{path.name}: expected {EXPECTED_CLI_CALLS} local CLI agent_end calls, "
            f"found {len(bare)} bare, {len(intermediate)} intermediate, and {len(final)} final"
        )

    if dry_run:
        print(f"DRY-RUN: would patch {path.name}: use awaited agent_end helper")
        return True

    patched = content.replace(cli_import_before, cli_import_after, 1)
    patched = CLI_BARE_CALL_RE.sub("await awaitAgentHarnessAgentEndHook(", patched)
    patched = CLI_INTERMEDIATE_AWAIT_CALL_RE.sub("await awaitAgentHarnessAgentEndHook(", patched)
    after_bare = len(CLI_BARE_CALL_RE.findall(patched))
    after_intermediate = len(CLI_INTERMEDIATE_AWAIT_CALL_RE.findall(patched))
    after_final = len(CLI_FINAL_AWAIT_CALL_RE.findall(patched))
    if cli_import_after not in patched or after_bare != 0 or after_intermediate != 0 or after_final != EXPECTED_CLI_CALLS:
        raise RuntimeError(
            f"{path.name}: post-patch validation failed: bare={after_bare}, "
            f"intermediate={after_intermediate}, final={after_final}"
        )
    path.write_text(patched)
    print(f"APPLIED: {path.name}: use awaited helper for {EXPECTED_CLI_CALLS} local CLI agent_end calls")
    return True


def patch_hook_runner(path: Path, dry_run: bool) -> bool:
    content = path.read_text()
    if HOOK_RUNNER_APPLIED_MARKER in content and HOOK_RUNNER_AGENT_END_AFTER in content:
        print(f"OK: {path.name}: agent_end timeout ref option already supported")
        return False

    if HOOK_RUNNER_VOID_BEFORE not in content or HOOK_RUNNER_AGENT_END_BEFORE not in content:
        raise RuntimeError(f"{path.name}: expected hook runner timeout structure")

    if dry_run:
        print(f"DRY-RUN: would patch {path.name}: add agent_end timeout ref option")
        return True

    patched = content.replace(HOOK_RUNNER_VOID_BEFORE, HOOK_RUNNER_VOID_AFTER, 1)
    patched = patched.replace(HOOK_RUNNER_AGENT_END_BEFORE, HOOK_RUNNER_AGENT_END_AFTER, 1)
    if HOOK_RUNNER_APPLIED_MARKER not in patched or HOOK_RUNNER_AGENT_END_AFTER not in patched:
        raise RuntimeError(f"{path.name}: post-patch hook runner validation failed")
    path.write_text(patched)
    print(f"APPLIED: {path.name}: add agent_end timeout ref option")
    return True


def patch_run_attempt(path: Path, dry_run: bool) -> bool:
    content = path.read_text()
    helper_hash = _resolve_helper_hash(path, content)
    run_attempt_import_before = RUN_ATTEMPT_IMPORT_BEFORE_TMPL.format(hash=helper_hash)
    run_attempt_import_after = RUN_ATTEMPT_IMPORT_AFTER_TMPL.format(hash=helper_hash)
    final = list(RUN_ATTEMPT_FINAL_CALL_RE.finditer(content))
    if (
        run_attempt_import_after in content
        and RUN_ATTEMPT_HELPER_AFTER in content
        and len(final) == EXPECTED_RUN_ATTEMPT_CALLS
    ):
        print(
            f"OK: {path.name}: Codex app-server no-channel agent_end calls use awaited helper"
        )
        return False

    calls = list(RUN_ATTEMPT_CALL_RE.finditer(content))
    if run_attempt_import_before not in content or RUN_ATTEMPT_HELPER_BEFORE not in content:
        raise RuntimeError(f"{path.name}: expected Codex app-server agent_end structure")
    if len(calls) != EXPECTED_RUN_ATTEMPT_CALLS:
        raise RuntimeError(
            f"{path.name}: expected {EXPECTED_RUN_ATTEMPT_CALLS} app-server agent_end calls, "
            f"found {len(calls)} bare and {len(final)} final"
        )

    if dry_run:
        print(
            f"DRY-RUN: would patch {path.name}: await no-channel Codex app-server agent_end"
        )
        return True

    patched = content.replace(run_attempt_import_before, run_attempt_import_after, 1)
    patched = patched.replace(RUN_ATTEMPT_HELPER_BEFORE, RUN_ATTEMPT_HELPER_AFTER, 1)
    patched = RUN_ATTEMPT_CALL_RE.sub("await runCodexAgentEndHook(params, {", patched)
    after_final = len(RUN_ATTEMPT_FINAL_CALL_RE.findall(patched))
    after_bare = len(RUN_ATTEMPT_CALL_RE.findall(patched))
    if (
        run_attempt_import_after not in patched
        or RUN_ATTEMPT_HELPER_AFTER not in patched
        or after_final != EXPECTED_RUN_ATTEMPT_CALLS
        or after_bare != 0
    ):
        raise RuntimeError(
            f"{path.name}: post-patch validation failed: bare={after_bare}, final={after_final}"
        )
    path.write_text(patched)
    print(
        f"APPLIED: {path.name}: await no-channel Codex app-server agent_end calls"
    )
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

        runner_count = 0
        for runner in find_candidates(dist, "hook-runner-global-*.js"):
            content = runner.read_text()
            if "async function runAgentEnd" in content and "async function runVoidHook" in content:
                changed.append(patch_hook_runner(runner, args.dry_run))
                runner_count += 1
            else:
                print(f"SKIP: {runner.name}: no hook runner agent_end structure")
        if runner_count == 0:
            raise RuntimeError("no hook-runner-global bundle with agent_end runner was patchable")

        helper_count = 0
        for helper in find_candidates(dist, "lifecycle-hook-helpers-*.js"):
            content = helper.read_text()
            if (
                HELPER_ORIGINAL_BEFORE in content
                or HELPER_INTERMEDIATE_BEFORE in content
                or HELPER_APPLIED_MARKER in content
            ):
                changed.append(patch_helper(helper, args.dry_run))
                helper_count += 1
            else:
                print(f"SKIP: {helper.name}: no agent_end helper function")
        if helper_count == 0:
            raise RuntimeError("no lifecycle-hook-helpers bundle with agent_end helper was patchable")

        cli_count = 0
        for cli in find_candidates(dist, "cli-runner-*.js"):
            content = cli.read_text()
            if (
                "runAgentHarnessAgentEndHook" not in content
                and "awaitAgentHarnessAgentEndHook" not in content
            ):
                print(f"SKIP: {cli.name}: no local CLI agent_end helper call")
                continue
            changed.append(patch_cli(cli, args.dry_run))
            cli_count += 1
        if cli_count == 0:
            raise RuntimeError("no cli-runner bundle with local CLI agent_end calls was patchable")

        run_attempt_count = 0
        for run_attempt in find_candidates(dist, "run-attempt-*.js"):
            content = run_attempt.read_text()
            if (
                "runAgentHarnessAgentEndHook" not in content
                and "awaitAgentHarnessAgentEndHook" not in content
            ):
                print(f"SKIP: {run_attempt.name}: no Codex app-server agent_end helper call")
                continue
            changed.append(patch_run_attempt(run_attempt, args.dry_run))
            run_attempt_count += 1
        if run_attempt_count == 0:
            raise RuntimeError(
                "no run-attempt bundle with Codex app-server agent_end calls was patchable"
            )
    except RuntimeError as error:
        print(f"WARN: agent-local-agent-end-await structural drift: {error}")
        sys.exit(1)

    if not any(changed):
        print("OK: agent-local-agent-end-await already applied")


if __name__ == "__main__":
    main()
