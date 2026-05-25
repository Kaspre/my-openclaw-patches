#!/usr/bin/env python3
"""Patch: Bound local CLI agent lifetime + cancel deferred CLI-exit work.

Issue: #63609 — CLI commands hang indefinitely after completing.
Files: dist/index.js, dist/entry.js, dist/cli/run-main.js,
       dist/context-engine-maintenance-*.js

Three conceptual layers:

  Layer 1 (post-completion cleanup, updated 2026-05-25 from #86264): when a
  routed or full Commander CLI command finishes, cancel queued/active deferred
  context engine turn-maintenance work, fence late transcript rewrites,
  clear/reset the dedicated maintenance lanes, clear progress timers, and drop
  reruns. This replaces the older post-resolve process.exit/SIGKILL fallback
  on the actual dist/entry.js CLI path.

  Layer 2 (wall-clock safety net, added 2026-05-15, updated 2026-05-25 from
  #86276): startup hard timeout for `agent --local` only. Handles the case
  where CLI setup or the agent runtime promise NEVER resolves. Honors
  `--timeout N` and `--timeout=N` with +30s grace so the agent's own --timeout
  has first crack at clean shutdown. When --timeout is omitted, reads
  agents.defaults.timeoutSeconds from config and falls back to 600s only if the
  config value is absent or invalid; timeout 0 disables the timer. Exits 124
  with the upstream-style message instead of SIGKILLing the process. The timer
  is armed before importing the full CLI so plugin registration / discovery
  hangs are also bounded.

  Layer 3 (legacy dist/index.js fallback): dist/index.js is not the normal
  openclaw.mjs CLI entry, but it keeps the older post-resolve forced-exit
  fallback because that legacy entry path does not load dist/cli/run-main.js.

Combined-patch history: layers 2 and the old forced-exit fallback were originally two sequential
search/replace patches on the same `runLegacyCliEntry(process.argv).catch(`
line — layer 2's search anchor was the patched output of layer 1. That made
--dry-run report a false-negative for layer 2 (the anchor only exists in
real-apply mode, where each iteration re-reads the file). Folded into a
single replace 2026-05-16 (beta.3 upgrade) — same byte output, no order
dependency, dry-run is accurate.

The original entry.js half was dropped on re-enable: upstream moved `runCli`
to `await runCli(argv)` form (entry.js:470), so the search pattern was
stale. Restored 2026-05-15 against the new `runMainOrRootHelp` shape, and
combined with Layer 2's IIFE in the same single replace. Updated 2026-05-25
to remove the actual-entry forced exit once the #86264 cleanup is installed.
"""
import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"

# Layer 2 — #86276 startup hard timeout for `agent --local` invocations.
# Inlined as a single-line IIFE for clean string-replace insertion.
# Two variants: one for files using `process`, one for files using `process$1` (entry.js bundled name).
def _startup_hard_timer_iife(proc_var: str, config_import: str) -> str:
    return (
        "await (async()=>{try{const a=" + proc_var + ".argv;"
        "let primary;"
        "for(let i=2;i<a.length;i++){const v=a[i];if(!v)continue;if(v==='--')break;if(!v.startsWith('-')){primary=v;break;}}"
        "if(primary!=='agent')return;"
        "let hasLocal=false;let s;"
        "for(let i=2;i<a.length;i++){const v=a[i];if(!v)continue;if(v==='--')break;"
        "if(v==='-h'||v==='--help'||v==='--version'||v==='-V')return;"
        "if(v==='--local'||v.startsWith('--local='))hasLocal=true;"
        "if(v==='--timeout')s=a[i+1];else if(v.startsWith('--timeout='))s=v.slice(10);}"
        "if(!hasLocal)return;"
        "let n;let explicitTimeout=s!==undefined;"
        "if(explicitTimeout){const p=parseInt(s,10);if(Number.isFinite(p)&&p>=0)n=p;}"
        "if(n===undefined&&!explicitTimeout){try{const{readBestEffortConfig}=await import(\"" + config_import + "\");"
        "const cfg=await readBestEffortConfig();const c=cfg?.agents?.defaults?.timeoutSeconds;"
        "if(typeof c==='number'&&Number.isFinite(c)&&c>=0)n=Math.floor(c);}catch{}}"
        "if(n===undefined)n=600;"
        "if(n===0)return;"
        "const ms=Math.min(2147000000,Math.max(1,(n+30)*1000));"
        "const t=setTimeout(()=>{try{" + proc_var + ".stderr.write(`local agent command timed out after ${n}s plus 30s grace\\n`);}catch{}"
        "try{" + proc_var + ".exit(124);}catch{}},ms);"
        "t.unref&&t.unref();return()=>clearTimeout(t);}catch{}})()"
    )

# Stable substring inside the IIFE — used to detect "already applied" without
# rebuilding the full replacement literal (which depends on the captured alias).
HARD_TIMEOUT_MARKER = "local agent command timed out after"
CONFIG_BACKED_TIMEOUT_MARKER = "agents?.defaults?.timeoutSeconds"
APPLIED_MARKER = HARD_TIMEOUT_MARKER
DEFERRED_MAINTENANCE_MARKER = "short-lived CLI command completed before deferred maintenance"
CLI_RUN_MAIN_CLEANUP_MARKER = "cancelCliDeferredContextEngineMaintenance"
CLI_RUN_MAIN_ROUTED_CLEANUP_MARKER = "shouldCancelDeferredMaintenanceOnExit"
CLI_EXIT_HOOK_MARKER = "contextEngineTurnMaintenanceCliExitHook"
OLD_APPLIED_MARKER = "cli-exit-fix: hard wall-clock SIGKILL"


def _replace_once(content: str, search: str, replacement: str, description: str) -> tuple[str, bool]:
    if search not in content:
        print(f"WARN: {description} — pattern not found (structural drift; review patch)")
        return content, False
    return content.replace(search, replacement, 1), True


def _replace_once_regex(
    content: str,
    pattern: re.Pattern[str],
    replacement: str,
    description: str,
) -> tuple[str, bool]:
    matches = list(pattern.finditer(content))
    if not matches:
        print(f"WARN: {description} — pattern not found (structural drift; review patch)")
        return content, False
    if len(matches) > 1:
        print(f"WARN: {description} — pattern matched {len(matches)} times (expected 1); aborting")
        return content, False
    return pattern.sub(replacement, content, count=1), True


def _find_context_engine_maintenance_bundle(dist: Path) -> Path | None:
    candidates = sorted(dist.glob("context-engine-maintenance-*.js"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        print("WARN: context-engine-maintenance bundle not found")
        return None
    print(
        "WARN: multiple context-engine-maintenance bundles found: "
        + ", ".join(candidate.name for candidate in candidates)
    )
    return None


def _find_read_best_effort_config_bundle(dist: Path) -> Path | None:
    candidates = []
    for candidate in sorted(dist.glob("io-*.js")):
        try:
            if "export { readBestEffortConfig };" in candidate.read_text():
                candidates.append(candidate)
        except UnicodeDecodeError:
            continue
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        print("WARN: readBestEffortConfig bundle not found")
        return None
    print(
        "WARN: multiple readBestEffortConfig bundles found: "
        + ", ".join(candidate.name for candidate in candidates)
    )
    return None


def _apply_cli_run_main_cleanup_patch(dist: Path, dry_run: bool) -> bool:
    fpath = dist / "cli" / "run-main.js"
    if not fpath.exists():
        print("SKIP: cli/run-main.js not found")
        return True
    content = fpath.read_text()
    if CLI_RUN_MAIN_ROUTED_CLEANUP_MARKER in content and CLI_EXIT_HOOK_MARKER in content:
        print("OK: cli/run-main.js #86264 routed deferred maintenance hook cleanup (already applied)")
        return True

    maintenance_bundle = _find_context_engine_maintenance_bundle(dist)
    if maintenance_bundle is None:
        return False
    maintenance_import = f"../{maintenance_bundle.name}"
    new_content = content
    all_ok = True

    cleanup_function = (
        'const DEFERRED_TURN_MAINTENANCE_CLI_EXIT_HOOK_KEY = Symbol.for("openclaw.contextEngineTurnMaintenanceCliExitHook");\n'
        "async function cancelCliDeferredContextEngineMaintenance() {\n"
        "\tconst hook = globalThis[DEFERRED_TURN_MAINTENANCE_CLI_EXIT_HOOK_KEY]?.cancelForCliExit;\n"
        "\tif (!hook) return;\n"
        "\ttry {\n"
        "\t\tawait hook();\n"
        "\t} catch {}\n"
        "}\n"
    )
    if CLI_EXIT_HOOK_MARKER not in new_content:
        if CLI_RUN_MAIN_CLEANUP_MARKER in new_content:
            new_content, ok = _replace_once_regex(
                new_content,
                re.compile(
                    r'async function cancelCliDeferredContextEngineMaintenance\(\) \{\n'
                    r'\ttry \{\n'
                    r'\t\tconst \{ cancelActiveDeferredTurnMaintenanceRunsForCliExit \} = await import\("[^"]+"\);\n'
                    r'\t\tawait cancelActiveDeferredTurnMaintenanceRunsForCliExit\(\);\n'
                    r'\t\} catch \{\}\n'
                    r'\}\n'
                ),
                cleanup_function,
                "cli/run-main.js: switch cleanup helper to global hook",
            )
        else:
            new_content, ok = _replace_once(
                new_content,
                "function pauseNonTtyStdinForCliExit() {",
                cleanup_function + "function pauseNonTtyStdinForCliExit() {",
                "cli/run-main.js: insert #86264 cleanup helper",
            )
        all_ok = all_ok and ok
    if CLI_RUN_MAIN_ROUTED_CLEANUP_MARKER in new_content:
        ok = True
    elif "let parsedFullCliCommand = false;" in new_content:
        new_content, ok = _replace_once(
            new_content,
            "\tlet parsedFullCliCommand = false;",
            "\tlet shouldCancelDeferredMaintenanceOnExit = false;",
            "cli/run-main.js: rename cleanup trigger for routed commands",
        )
    else:
        new_content, ok = _replace_once(
            new_content,
            "\tlet onExit = null;\n\tif (proxyHandle) {",
            "\tlet onExit = null;\n\tlet shouldCancelDeferredMaintenanceOnExit = false;\n\tif (proxyHandle) {",
            "cli/run-main.js: track CLI cleanup trigger",
        )
    all_ok = all_ok and ok
    if "tryRouteCli(normalizedArgv))) {" in new_content and (
        "shouldCancelDeferredMaintenanceOnExit = true" in new_content
    ):
        ok = True
    else:
        new_content, ok = _replace_once_regex(
            new_content,
            re.compile(
                r'\t\tconst \{ tryRouteCli \} = await startupTrace\.measure\("route-import", \(\) => import\("([^"]+)"\)\);\n'
                r'\t\tif \(await startupTrace\.measure\("route", \(\) => tryRouteCli\(normalizedArgv\)\)\) return;'
            ),
            '\t\tconst { tryRouteCli } = await startupTrace.measure("route-import", () => import("\\1"));\n'
            "\t\ttry {\n"
            '\t\t\tif (await startupTrace.measure("route", () => tryRouteCli(normalizedArgv))) {\n'
            "\t\t\t\tshouldCancelDeferredMaintenanceOnExit = true;\n"
            "\t\t\t\treturn;\n"
            "\t\t\t}\n"
            "\t\t} catch (error) {\n"
            "\t\t\tshouldCancelDeferredMaintenanceOnExit = true;\n"
            "\t\t\tthrow error;\n"
            "\t\t}",
            "cli/run-main.js: cancel deferred maintenance after routed commands",
        )
    all_ok = all_ok and ok
    if "shouldCancelDeferredMaintenanceOnExit = true;\n\t\t\t\tawait startupTrace.measure(\"parse\"" in new_content:
        ok = True
    elif "parsedFullCliCommand = true;" in new_content:
        new_content, ok = _replace_once(
            new_content,
            "\t\t\t\tparsedFullCliCommand = true;\n\t\t\t\tawait startupTrace.measure(\"parse\", () => program.parseAsync(parseArgv));",
            "\t\t\t\tshouldCancelDeferredMaintenanceOnExit = true;\n\t\t\t\tawait startupTrace.measure(\"parse\", () => program.parseAsync(parseArgv));",
            "cli/run-main.js: mark full CLI command before parse",
        )
    else:
        new_content, ok = _replace_once(
            new_content,
            "\t\t\t\tawait startupTrace.measure(\"parse\", () => program.parseAsync(parseArgv));",
            "\t\t\t\tshouldCancelDeferredMaintenanceOnExit = true;\n\t\t\t\tawait startupTrace.measure(\"parse\", () => program.parseAsync(parseArgv));",
            "cli/run-main.js: mark full CLI command before parse",
        )
    all_ok = all_ok and ok
    if "if (shouldCancelDeferredMaintenanceOnExit) await cancelCliDeferredContextEngineMaintenance();" in new_content:
        ok = True
    elif "if (parsedFullCliCommand) await cancelCliDeferredContextEngineMaintenance();" in new_content:
        new_content, ok = _replace_once(
            new_content,
            "\t\tif (parsedFullCliCommand) await cancelCliDeferredContextEngineMaintenance();",
            "\t\tif (shouldCancelDeferredMaintenanceOnExit) await cancelCliDeferredContextEngineMaintenance();",
            "cli/run-main.js: use routed cleanup trigger",
        )
    else:
        new_content, ok = _replace_once(
            new_content,
            "\t\tawait stopStartedProxy();\n\t\tawait disposeCliAgentHarnesses();",
            "\t\tawait stopStartedProxy();\n\t\tif (shouldCancelDeferredMaintenanceOnExit) await cancelCliDeferredContextEngineMaintenance();\n\t\tawait disposeCliAgentHarnesses();",
            "cli/run-main.js: cancel deferred maintenance before remaining cleanup",
        )
    all_ok = all_ok and ok

    if not all_ok:
        return False
    if dry_run:
        print("DRY-RUN: would apply cli/run-main.js #86264 routed deferred maintenance hook cleanup")
        return True
    fpath.write_text(new_content)
    print("APPLIED: cli/run-main.js #86264 routed deferred maintenance hook cleanup")
    return True


def _apply_context_engine_maintenance_patch(dist: Path, dry_run: bool) -> bool:
    fpath = _find_context_engine_maintenance_bundle(dist)
    if fpath is None:
        return False
    content = fpath.read_text()
    if DEFERRED_MAINTENANCE_MARKER in content and CLI_EXIT_HOOK_MARKER in content:
        print(f"OK: {fpath.name} #86264 deferred maintenance hook cancellation (already applied)")
        return True

    new_content = content
    all_ok = True
    full_replacements = [
        (
            'import { c as getQueueSize, i as enqueueCommandInLane } from "./command-queue-Bu19cj-7.js";',
            'import { c as getQueueSize, i as enqueueCommandInLane, p as resetCommandLane, r as clearCommandLane } from "./command-queue-Bu19cj-7.js";',
            "context-engine-maintenance: import lane clear/reset helpers",
        ),
        (
            "const TURN_MAINTENANCE_LONG_WAIT_MS = 1e4;\nconst DEFERRED_TURN_MAINTENANCE_ABORT_STATE_KEY",
            "const TURN_MAINTENANCE_LONG_WAIT_MS = 1e4;\nconst TURN_MAINTENANCE_CLI_EXIT_DRAIN_MS = 1e3;\nconst DEFERRED_TURN_MAINTENANCE_ABORT_STATE_KEY",
            "context-engine-maintenance: add CLI-exit drain budget",
        ),
        (
            "function unregisterDeferredTurnMaintenanceAbortSignalHandlers(processLike, state) {\n\tif (!state.registered) return;\n\tfor (const [signal, handler] of state.cleanupHandlers) processLike.off(signal, handler);\n\tstate.cleanupHandlers.clear();\n\tstate.registered = false;\n}\n",
            "function unregisterDeferredTurnMaintenanceAbortSignalHandlers(processLike, state) {\n\tif (!state.registered) return;\n\tfor (const [signal, handler] of state.cleanupHandlers) processLike.off(signal, handler);\n\tstate.cleanupHandlers.clear();\n\tstate.registered = false;\n}\nfunction abortDeferredTurnMaintenanceControllers(params) {\n\tconst state = params.processLike[DEFERRED_TURN_MAINTENANCE_ABORT_STATE_KEY];\n\tif (!state) return;\n\tfor (const activeController of state.controllers) if (!activeController.signal.aborted) activeController.abort(params.reason);\n\tstate.controllers.clear();\n\tunregisterDeferredTurnMaintenanceAbortSignalHandlers(params.processLike, state);\n}\n",
            "context-engine-maintenance: add shared abort-controller cleanup",
        ),
        (
            "\tconst handleTerminationSignal = (signalName) => {\n\t\tconst shouldReraise = typeof processLike.listenerCount === \"function\" ? processLike.listenerCount(signalName) === 1 : false;\n\t\tfor (const activeController of state.controllers) if (!activeController.signal.aborted) activeController.abort(/* @__PURE__ */ new Error(`received ${signalName} while waiting for deferred maintenance`));\n\t\tstate.controllers.clear();\n\t\tunregisterDeferredTurnMaintenanceAbortSignalHandlers(processLike, state);\n\t\tif (shouldReraise && typeof processLike.kill === \"function\") try {",
            "\tconst handleTerminationSignal = (signalName) => {\n\t\tconst shouldReraise = typeof processLike.listenerCount === \"function\" ? processLike.listenerCount(signalName) === 1 : false;\n\t\tabortDeferredTurnMaintenanceControllers({\n\t\t\tprocessLike,\n\t\t\treason: /* @__PURE__ */ new Error(`received ${signalName} while waiting for deferred maintenance`)\n\t\t});\n\t\tif (shouldReraise && typeof processLike.kill === \"function\") try {",
            "context-engine-maintenance: route signal aborts through helper",
        ),
        (
            "function markDeferredTurnMaintenanceTaskScheduleFailure(params) {",
            "function cancelDeferredTurnMaintenanceTask(params) {\n\tconst task = findTaskByRunIdForOwner({\n\t\trunId: params.runId,\n\t\tcallerOwnerKey: params.sessionKey\n\t});\n\tif (!task) return;\n\tif ([\"succeeded\", \"failed\", \"timed_out\", \"cancelled\", \"lost\"].includes(task.status)) return;\n\tcancelTaskByIdForOwner({\n\t\ttaskId: task.taskId,\n\t\tcallerOwnerKey: params.sessionKey,\n\t\tendedAt: Date.now(),\n\t\tterminalSummary: params.terminalSummary\n\t});\n}\nasync function cancelActiveDeferredTurnMaintenanceRunsForCliExit(params) {\n\tconst activeEntries = Array.from(activeDeferredTurnMaintenanceRuns.entries());\n\tif (activeEntries.length === 0) return;\n\tabortDeferredTurnMaintenanceControllers({\n\t\tprocessLike: process,\n\t\treason: /* @__PURE__ */ new Error(\"short-lived CLI command completed before deferred maintenance\")\n\t});\n\tfor (const [activeSessionKey, state] of activeEntries) {\n\t\tstate.rerunRequested = false;\n\t\tactiveDeferredTurnMaintenanceRuns.delete(activeSessionKey);\n\t\tcancelDeferredTurnMaintenanceTask({\n\t\t\tsessionKey: activeSessionKey,\n\t\t\trunId: state.runId,\n\t\t\tterminalSummary: \"Deferred maintenance cancellation requested because the CLI command completed.\"\n\t\t});\n\t\tconst lane = resolveDeferredTurnMaintenanceLane(activeSessionKey);\n\t\tclearCommandLane(lane);\n\t\tresetCommandLane(lane);\n\t}\n\tconst drainMs = Math.max(0, Math.floor(params?.drainMs ?? TURN_MAINTENANCE_CLI_EXIT_DRAIN_MS));\n\tif (drainMs === 0) return;\n\tawait Promise.race([\n\t\tPromise.allSettled(activeEntries.map(([, state]) => state.promise)),\n\t\tnew Promise((resolve) => {\n\t\t\tconst timeout = setTimeout(resolve, drainMs);\n\t\t\ttimeout.unref?.();\n\t\t})\n\t]);\n}\nfunction markDeferredTurnMaintenanceTaskScheduleFailure(params) {",
            "context-engine-maintenance: add CLI-exit cancellation function",
        ),
        (
            "function buildContextEngineMaintenanceRuntimeContext(params) {\n\treturn {",
            "function buildContextEngineMaintenanceRuntimeContext(params) {\n\tconst abortSignal = params.runtimeContext?.abortSignal;\n\tconst throwIfMaintenanceAborted = () => {\n\t\tif (!abortSignal?.aborted) return;\n\t\tconst reason = abortSignal.reason;\n\t\tif (reason instanceof Error) throw reason;\n\t\tconst error = /* @__PURE__ */ new Error(\"Deferred maintenance cancelled before transcript rewrite.\");\n\t\terror.name = \"AbortError\";\n\t\tthrow error;\n\t};\n\treturn {",
            "context-engine-maintenance: create abort-aware runtime context",
        ),
        (
            "\t\trewriteTranscriptEntries: async (request) => {\n\t\t\tif (params.sessionManager) {",
            "\t\trewriteTranscriptEntries: async (request) => {\n\t\t\tthrowIfMaintenanceAborted();\n\t\t\tif (params.sessionManager) {",
            "context-engine-maintenance: check abort before transcript rewrite",
        ),
        (
            "\t\t\t\tconst rewriteSessionManagerEntries = () => rewriteTranscriptEntriesInSessionManager({\n\t\t\t\t\tsessionManager,\n\t\t\t\t\treplacements: request.replacements\n\t\t\t\t});",
            "\t\t\t\tconst rewriteSessionManagerEntries = () => {\n\t\t\t\t\tthrowIfMaintenanceAborted();\n\t\t\t\t\treturn rewriteTranscriptEntriesInSessionManager({\n\t\t\t\t\t\tsessionManager,\n\t\t\t\t\t\treplacements: request.replacements\n\t\t\t\t\t});\n\t\t\t\t};",
            "context-engine-maintenance: fence session-manager rewrites",
        ),
        (
            "\t\t\tconst rewriteTranscriptEntriesInFile = async () => await rewriteTranscriptEntriesInSessionFile({\n\t\t\t\tsessionFile: params.sessionFile,\n\t\t\t\tsessionId: params.sessionId,\n\t\t\t\tsessionKey: params.sessionKey,\n\t\t\t\tconfig: params.config,\n\t\t\t\trequest\n\t\t\t});",
            "\t\t\tconst rewriteTranscriptEntriesInFile = async () => {\n\t\t\t\tthrowIfMaintenanceAborted();\n\t\t\t\treturn await rewriteTranscriptEntriesInSessionFile({\n\t\t\t\t\tsessionFile: params.sessionFile,\n\t\t\t\t\tsessionId: params.sessionId,\n\t\t\t\t\tsessionKey: params.sessionKey,\n\t\t\t\t\tconfig: params.config,\n\t\t\t\t\trequest\n\t\t\t\t});\n\t\t\t};",
            "context-engine-maintenance: fence file rewrites",
        ),
        (
            "\t\t\tif (params.deferTranscriptRewriteToSessionLane && rewriteSessionKey) return await enqueueCommandInLane(resolveSessionLane(rewriteSessionKey), async () => await rewriteTranscriptEntriesInFile());",
            "\t\t\tif (params.deferTranscriptRewriteToSessionLane && rewriteSessionKey) {\n\t\t\t\tthrowIfMaintenanceAborted();\n\t\t\t\treturn await enqueueCommandInLane(resolveSessionLane(rewriteSessionKey), async () => await rewriteTranscriptEntriesInFile());\n\t\t\t}",
            "context-engine-maintenance: fence queued rewrites",
        ),
        (
            "\t\t\truntimeContext: params.runtimeContext,\n\t\t\tagentId: params.agentId,",
            "\t\t\truntimeContext: {\n\t\t\t\t...params.runtimeContext,\n\t\t\t\t...params.abortSignal ? { abortSignal: params.abortSignal } : {}\n\t\t\t},\n\t\t\tagentId: params.agentId,",
            "context-engine-maintenance: thread abortSignal into maintain runtime context",
        ),
        (
            "async function runDeferredTurnMaintenanceWorker(params) {\n\tlet surfacedUserNotice = false;\n\tlet longRunningTimer = null;\n\tconst shutdownAbort = createDeferredTurnMaintenanceAbortSignal();",
            "async function runDeferredTurnMaintenanceWorker(params) {\n\tif (params.scheduledAbortSignal?.aborted) {\n\t\tcancelDeferredTurnMaintenanceTask({\n\t\t\tsessionKey: params.sessionKey,\n\t\t\trunId: params.runId,\n\t\t\tterminalSummary: \"Deferred maintenance cancelled during shutdown.\"\n\t\t});\n\t\treturn;\n\t}\n\tlet surfacedUserNotice = false;\n\tlet longRunningTimer = null;\n\tconst shutdownAbort = createDeferredTurnMaintenanceAbortSignal();\n\tconst clearLongRunningTimer = () => {\n\t\tif (!longRunningTimer) return;\n\t\tclearTimeout(longRunningTimer);\n\t\tlongRunningTimer = null;\n\t};\n\tshutdownAbort.abortSignal?.addEventListener(\"abort\", clearLongRunningTimer, { once: true });\n\tparams.scheduledAbortSignal?.addEventListener(\"abort\", clearLongRunningTimer, { once: true });",
            "context-engine-maintenance: initialize worker abort cleanup",
        ),
        (
            "\t\t\t\tawait sleepWithAbort(TURN_MAINTENANCE_WAIT_POLL_MS, shutdownAbort.abortSignal);\n\t\t\t}\n\t\t\tawait Promise.resolve();\n\t\t\tif (getQueueSize(sessionLane) === 0) break;",
            "\t\t\t\tawait sleepWithAbort(TURN_MAINTENANCE_WAIT_POLL_MS, shutdownAbort.abortSignal);\n\t\t\t\tif (params.scheduledAbortSignal?.aborted) throw params.scheduledAbortSignal.reason ?? new Error(\"deferred maintenance cancelled\");\n\t\t\t}\n\t\t\tawait Promise.resolve();\n\t\t\tif (params.scheduledAbortSignal?.aborted) throw params.scheduledAbortSignal.reason ?? new Error(\"deferred maintenance cancelled\");\n\t\t\tif (getQueueSize(sessionLane) === 0) break;",
            "context-engine-maintenance: abort while waiting for session lane",
        ),
        (
            "\t\tlongRunningTimer = setTimeout(() => {",
            "\t\tlongRunningTimer = setTimeout(() => {",
            "context-engine-maintenance: locate long-running timer",
        ),
        (
            "\t\t}, TURN_MAINTENANCE_LONG_WAIT_MS);\n\t\tconst result = await executeContextEngineMaintenance({",
            "\t\t}, TURN_MAINTENANCE_LONG_WAIT_MS);\n\t\tlongRunningTimer.unref?.();\n\t\tconst result = await executeContextEngineMaintenance({",
            "context-engine-maintenance: unref long-running timer",
        ),
        (
            "\t\t\tconfig: params.config,\n\t\t\texecutionMode: \"background\"\n\t\t});\n\t\tif (longRunningTimer) {\n\t\t\tclearTimeout(longRunningTimer);\n\t\t\tlongRunningTimer = null;\n\t\t}",
            "\t\t\tconfig: params.config,\n\t\t\texecutionMode: \"background\",\n\t\t\tabortSignal: shutdownAbort.abortSignal\n\t\t});\n\t\tif (shutdownAbort.abortSignal?.aborted || params.scheduledAbortSignal?.aborted) {\n\t\t\tclearLongRunningTimer();\n\t\t\tcancelDeferredTurnMaintenanceTask({\n\t\t\t\tsessionKey: params.sessionKey,\n\t\t\t\trunId: params.runId,\n\t\t\t\tterminalSummary: \"Deferred maintenance cancelled during shutdown.\"\n\t\t\t});\n\t\t\treturn;\n\t\t}\n\t\tclearLongRunningTimer();",
            "context-engine-maintenance: abort after maintain resolves",
        ),
        (
            "\t} catch (err) {\n\t\tif (shutdownAbort.abortSignal?.aborted) {\n\t\t\tif (longRunningTimer) {\n\t\t\t\tclearTimeout(longRunningTimer);\n\t\t\t\tlongRunningTimer = null;\n\t\t\t}\n\t\t\tconst task = findTaskByRunIdForOwner({\n\t\t\t\trunId: params.runId,\n\t\t\t\tcallerOwnerKey: params.sessionKey\n\t\t\t});\n\t\t\tif (task) cancelTaskByIdForOwner({\n\t\t\t\ttaskId: task.taskId,\n\t\t\t\tcallerOwnerKey: params.sessionKey,\n\t\t\t\tendedAt: Date.now(),\n\t\t\t\tterminalSummary: \"Deferred maintenance cancelled during shutdown.\"\n\t\t\t});\n\t\t\treturn;\n\t\t}\n\t\tif (longRunningTimer) {\n\t\t\tclearTimeout(longRunningTimer);\n\t\t\tlongRunningTimer = null;\n\t\t}",
            "\t} catch (err) {\n\t\tif (shutdownAbort.abortSignal?.aborted || params.scheduledAbortSignal?.aborted) {\n\t\t\tclearLongRunningTimer();\n\t\t\tcancelDeferredTurnMaintenanceTask({\n\t\t\t\tsessionKey: params.sessionKey,\n\t\t\t\trunId: params.runId,\n\t\t\t\tterminalSummary: \"Deferred maintenance cancelled during shutdown.\"\n\t\t\t});\n\t\t\treturn;\n\t\t}\n\t\tclearLongRunningTimer();",
            "context-engine-maintenance: use shared cancellation helper in catch",
        ),
        (
            "\t} finally {\n\t\tshutdownAbort.dispose();\n\t}",
            "\t} finally {\n\t\tshutdownAbort.abortSignal?.removeEventListener(\"abort\", clearLongRunningTimer);\n\t\tparams.scheduledAbortSignal?.removeEventListener(\"abort\", clearLongRunningTimer);\n\t\tshutdownAbort.dispose();\n\t}",
            "context-engine-maintenance: unregister timer abort listeners",
        ),
        (
            "\t\trunPromise = enqueueCommandInLane(resolveDeferredTurnMaintenanceLane(sessionKey), async () => runDeferredTurnMaintenanceWorker({\n\t\t\tcontextEngine: params.contextEngine,\n\t\t\tsessionId: params.sessionId,\n\t\t\tsessionKey,\n\t\t\tsessionFile: params.sessionFile,\n\t\t\tsessionManager: params.sessionManager,\n\t\t\truntimeContext: params.runtimeContext,\n\t\t\tagentId: params.agentId,\n\t\t\tconfig: params.config,\n\t\t\trunId: task.runId\n\t\t}));",
            "\t\trunPromise = enqueueCommandInLane(resolveDeferredTurnMaintenanceLane(sessionKey), async () => runDeferredTurnMaintenanceWorker({\n\t\t\tcontextEngine: params.contextEngine,\n\t\t\tsessionId: params.sessionId,\n\t\t\tsessionKey,\n\t\t\tsessionFile: params.sessionFile,\n\t\t\tsessionManager: params.sessionManager,\n\t\t\truntimeContext: params.runtimeContext,\n\t\t\tagentId: params.agentId,\n\t\t\tconfig: params.config,\n\t\t\trunId: task.runId,\n\t\t\tscheduledAbortSignal: schedulerAbort.abortSignal\n\t\t}));",
            "context-engine-maintenance: pass scheduler abort to worker",
        ),
        (
            "\tconst trackedPromise = runPromise.catch((err) => {\n\t\tmarkDeferredTurnMaintenanceTaskScheduleFailure({",
            "\tconst trackedPromise = runPromise.catch((err) => {\n\t\tif (schedulerAbort.abortSignal?.aborted) return;\n\t\tmarkDeferredTurnMaintenanceTaskScheduleFailure({",
            "context-engine-maintenance: suppress expected abort schedule failure",
        ),
        (
            "\tstate = {\n\t\tpromise: trackedPromise,\n\t\trerunRequested: false,\n\t\tlatestParams: {\n\t\t\t...params,\n\t\t\tsessionKey\n\t\t}\n\t};",
            "\tstate = {\n\t\tpromise: trackedPromise,\n\t\trerunRequested: false,\n\t\tlatestParams: {\n\t\t\t...params,\n\t\t\tsessionKey\n\t\t},\n\t\ttaskId: task.taskId,\n\t\trunId: task.runId\n\t};",
            "context-engine-maintenance: track task/run ids",
        ),
        (
            "export { getRawSessionAppendMessage as a, persistTranscriptStateMutation as c, rewriteTranscriptEntriesInState as i, readTranscriptFileState as l, rewriteTranscriptEntriesInSessionFile as n, setRawSessionAppendMessage as o, rewriteTranscriptEntriesInSessionManager as r, TranscriptFileState as s, runContextEngineMaintenance as t, writeTranscriptFileAtomic as u };",
            "export { getRawSessionAppendMessage as a, persistTranscriptStateMutation as c, rewriteTranscriptEntriesInState as i, readTranscriptFileState as l, rewriteTranscriptEntriesInSessionFile as n, setRawSessionAppendMessage as o, rewriteTranscriptEntriesInSessionManager as r, TranscriptFileState as s, runContextEngineMaintenance as t, writeTranscriptFileAtomic as u, cancelActiveDeferredTurnMaintenanceRunsForCliExit };",
            "context-engine-maintenance: export CLI-exit cancellation",
        ),
    ]
    hook_replacements = [
        (
            'const DEFERRED_TURN_MAINTENANCE_ABORT_STATE_KEY = Symbol.for("openclaw.contextEngineTurnMaintenanceAbortState");',
            'const DEFERRED_TURN_MAINTENANCE_ABORT_STATE_KEY = Symbol.for("openclaw.contextEngineTurnMaintenanceAbortState");\nconst DEFERRED_TURN_MAINTENANCE_CLI_EXIT_HOOK_KEY = Symbol.for("openclaw.contextEngineTurnMaintenanceCliExitHook");',
            "context-engine-maintenance: add CLI-exit hook key",
        ),
        (
            "\tawait Promise.race([\n\t\tPromise.allSettled(activeEntries.map(([, state]) => state.promise)),\n\t\tnew Promise((resolve) => {\n\t\t\tconst timeout = setTimeout(resolve, drainMs);\n\t\t\ttimeout.unref?.();\n\t\t})\n\t]);\n}\nfunction markDeferredTurnMaintenanceTaskScheduleFailure(params) {",
            "\tawait Promise.race([\n\t\tPromise.allSettled(activeEntries.map(([, state]) => state.promise)),\n\t\tnew Promise((resolve) => {\n\t\t\tconst timeout = setTimeout(resolve, drainMs);\n\t\t\ttimeout.unref?.();\n\t\t})\n\t]);\n}\nfunction registerDeferredTurnMaintenanceCliExitHook() {\n\tconst globalState = globalThis;\n\tconst state = globalState[DEFERRED_TURN_MAINTENANCE_CLI_EXIT_HOOK_KEY] ?? {};\n\tstate.cancelForCliExit = cancelActiveDeferredTurnMaintenanceRunsForCliExit;\n\tglobalState[DEFERRED_TURN_MAINTENANCE_CLI_EXIT_HOOK_KEY] = state;\n}\nregisterDeferredTurnMaintenanceCliExitHook();\nfunction markDeferredTurnMaintenanceTaskScheduleFailure(params) {",
            "context-engine-maintenance: register CLI-exit hook",
        ),
    ]

    replacements = []
    if DEFERRED_MAINTENANCE_MARKER not in content:
        replacements.extend(full_replacements)
    if CLI_EXIT_HOOK_MARKER not in content:
        replacements.extend(hook_replacements)

    for search, replacement, description in replacements:
        new_content, ok = _replace_once(new_content, search, replacement, description)
        all_ok = all_ok and ok

    if not all_ok:
        return False
    if dry_run:
        print(f"DRY-RUN: would apply {fpath.name} #86264 deferred maintenance hook cancellation")
        return True
    fpath.write_text(new_content)
    print(f"APPLIED: {fpath.name} #86264 deferred maintenance hook cancellation")
    return True

# Each recipe has a REGEX anchor that captures the process-alias (group 1).
# The bundler emits `process` in some files and `process$1` (or potentially
# `process$2` in future builds) in others — we discover it at apply time
# instead of hardcoding. See PATCHING-GUIDE.md §5b.
PATCH_RECIPES = [
    # === dist/index.js (legacy library-mode CLI; harmless if never main) ===
    {
        "file": "index.js",
        "description": "index.js: Layer 1 (post-resolve exit) + Layer 2 (#86276 startup hard timeout) for `agent --local` invocations",
        "anchor_re": re.compile(
            r"runLegacyCliEntry\((process(?:\$\d+)?)\.argv\)\.catch\("
        ),
        "old_patched_anchor_re": re.compile(
            r"\(\(\)=>\{try\{const a=(process(?:\$\d+)?)\.argv;[\s\S]*?cli-exit-fix: hard wall-clock SIGKILL[\s\S]*?\}\)\(\);"
            r"runLegacyCliEntry\(\1\.argv\)\.then\(\(\) => \{ setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}\)\.catch\("
        ),
        "current_hard_timeout_anchor_re": re.compile(
            r"const __ocCliHardTimeout=(?:await )?[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
            r"runLegacyCliEntry\((process(?:\$\d+)?)\.argv\)\.then\(\(\) => \{ try \{ __ocCliHardTimeout&&__ocCliHardTimeout\(\); \} catch \{\} setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}\)\.catch\("
        ),
        "build_replacement": lambda alias, config_import: (
            f"const __ocCliHardTimeout={_startup_hard_timer_iife(alias, config_import)};"
            + f"runLegacyCliEntry({alias}.argv)"
            + f".then(() => {{ try {{ __ocCliHardTimeout&&__ocCliHardTimeout(); }} catch {{}} setTimeout(() => {{ try {{ {alias}.kill({alias}.pid, 'SIGKILL'); }} catch {{}} }}, 3000); {alias}.exit({alias}.exitCode ?? 0); }})"
            + ".catch("
        ),
    },
    # === dist/entry.js (the ACTUAL CLI entry per openclaw.mjs `tryImport("./dist/entry.js")`) ===
    # The \1 backreference forces both occurrences of process$N to match the SAME alias.
    {
        "file": "entry.js",
        "description": "entry.js: Layer 1 (post-resolve exit) + Layer 2 (#86276 startup hard timeout) around runMainOrRootHelp",
        "anchor_re": re.compile(
            r"if \(!tryHandleRootVersionFastPath\((process(?:\$\d+)?)\.argv\)\) "
            r"await runMainOrRootHelp\(\1\.argv\);"
        ),
        "old_patched_anchor_re": re.compile(
            r"\(\(\)=>\{try\{const a=(process(?:\$\d+)?)\.argv;[\s\S]*?cli-exit-fix: hard wall-clock SIGKILL[\s\S]*?\}\)\(\);"
            r"if \(!tryHandleRootVersionFastPath\(\1\.argv\)\) \{ await runMainOrRootHelp\(\1\.argv\); setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}"
        ),
        "current_forced_exit_anchor_re": re.compile(
            r"const __ocCliHardTimeout=\(\(\)=>\{try\{const a=(process(?:\$\d+)?)\.argv;[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
            r"if \(!tryHandleRootVersionFastPath\(\1\.argv\)\) \{ try \{ await runMainOrRootHelp\(\1\.argv\); \} finally \{ try \{ __ocCliHardTimeout&&__ocCliHardTimeout\(\); \} catch \{\} \} setTimeout\(\(\) => \{ try \{ \1\.kill\(\1\.pid, 'SIGKILL'\); \} catch \{\} \}, 3000\); \1\.exit\(\1\.exitCode \?\? 0\); \}"
        ),
        "current_hard_timeout_anchor_re": re.compile(
            r"const __ocCliHardTimeout=(?:await )?[\s\S]*?local agent command timed out after[\s\S]*?\}\)\(\);"
            r"if \(!tryHandleRootVersionFastPath\((process(?:\$\d+)?)\.argv\)\) \{ try \{ await runMainOrRootHelp\(\1\.argv\); \} finally \{ try \{ __ocCliHardTimeout&&__ocCliHardTimeout\(\); \} catch \{\} \} \}"
        ),
        "build_replacement": lambda alias, config_import: (
            f"const __ocCliHardTimeout={_startup_hard_timer_iife(alias, config_import)};"
            + f"if (!tryHandleRootVersionFastPath({alias}.argv)) {{ "
            + f"try {{ await runMainOrRootHelp({alias}.argv); }} finally {{ try {{ __ocCliHardTimeout&&__ocCliHardTimeout(); }} catch {{}} }} "
            + f"}}"
        ),
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

    config_bundle = _find_read_best_effort_config_bundle(dist)
    if config_bundle is None:
        sys.exit(1)
    config_import = f"./{config_bundle.name}"

    all_ok = True
    all_ok = _apply_context_engine_maintenance_patch(dist, args.dry_run) and all_ok
    all_ok = _apply_cli_run_main_cleanup_patch(dist, args.dry_run) and all_ok
    for recipe in PATCH_RECIPES:
        fpath = dist / recipe["file"]
        if not fpath.exists():
            print(f"SKIP: {recipe['file']} not found")
            continue

        content = fpath.read_text()

        current_hard_timeout_anchor_re = recipe.get("current_hard_timeout_anchor_re")
        current_hard_timeout_matches = (
            list(current_hard_timeout_anchor_re.finditer(content))
            if current_hard_timeout_anchor_re is not None
            and APPLIED_MARKER in content
            and CONFIG_BACKED_TIMEOUT_MARKER not in content
            else []
        )
        current_forced_exit_anchor_re = recipe.get("current_forced_exit_anchor_re")
        current_forced_exit_matches = (
            list(current_forced_exit_anchor_re.finditer(content))
            if current_forced_exit_anchor_re is not None and APPLIED_MARKER in content
            else []
        )
        if (
            APPLIED_MARKER in content
            and CONFIG_BACKED_TIMEOUT_MARKER in content
            and not current_forced_exit_matches
        ):
            print(f"OK: {recipe['description']} (already applied)")
            continue

        anchor_re = (
            current_forced_exit_anchor_re
            if current_forced_exit_matches
            else current_hard_timeout_anchor_re
            if current_hard_timeout_matches
            else recipe["old_patched_anchor_re"] if OLD_APPLIED_MARKER in content else recipe["anchor_re"]
        )
        matches = list(anchor_re.finditer(content))
        if not matches:
            print(f"WARN: {recipe['description']} — anchor pattern not found (structural drift; review patch)")
            all_ok = False
            continue
        if len(matches) > 1:
            print(f"WARN: {recipe['description']} — anchor matched {len(matches)} times (expected 1); aborting")
            all_ok = False
            continue

        match = matches[0]
        alias = match.group(1)
        search_literal = match.group(0)
        replace_literal = recipe["build_replacement"](alias, config_import)

        if args.dry_run:
            print(f"DRY-RUN: would apply {recipe['description']} (alias={alias})")
            continue

        new_content = content.replace(search_literal, replace_literal, 1)
        fpath.write_text(new_content)
        print(f"APPLIED: {recipe['description']} (alias={alias})")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
