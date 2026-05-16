#!/usr/bin/env python3
"""Patch: Bound CLI agent lifetime + force exit on completion.

Issue: #63609 — CLI commands hang indefinitely after completing.
Files: dist/index.js

Two layers (both target `openclaw agent` invocations):

  Layer 1 (post-resolve fast exit, original patch): when runLegacyCliEntry's
  promise resolves, force process.exit(0) + 3s SIGKILL self-kill. Handles the
  case where the agent's logical work completes but ref'd handles (LCM
  compaction, otel exporter, plugin background loops) keep the event loop
  alive past natural exit. Re-enabled 2026-05-12 on v2026.5.12-beta.2 after
  the retired patch's "agent --local" regression (matches TECH-2026-2946/2947).

  Layer 2 (wall-clock safety net, added 2026-05-15): hard SIGKILL timer
  registered before runLegacyCliEntry. Handles the case where the agent
  runtime's promise NEVER resolves (e.g., codex app-server crashes mid-call
  leaving an awaited promise pending; LCM/gateway-WS handles where Layer 1's
  .then() never fires). Defaults to 600s for `agent` subcommand invocations;
  honors `--timeout N` (seconds) from argv with +30s grace so the agent's
  own --timeout has first crack at clean shutdown; `--timeout 0` disables
  (matches OC's "0 means no timeout" semantics in resolveAgentTimeoutMs).
  Uses .unref() so the timer never prevents natural exit.

The original entry.js half was dropped on re-enable: upstream moved `runCli`
to `await runCli(argv)` form (entry.js:470), so the search pattern is stale.
"""
import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".nvm/versions/node/v26.1.0/lib/node_modules/openclaw/dist"

# Layer 2 — hard wall-clock SIGKILL timer for `agent` invocations.
# Inlined as a single-line IIFE for clean string-replace insertion.
# Two variants: one for files using `process`, one for files using `process$1` (entry.js bundled name).
def _hard_timer_iife(proc_var: str) -> str:
    return (
        "(()=>{try{const a=" + proc_var + ".argv;"
        "const sub=a.find((v,i)=>i>=2&&!v.startsWith('-'));"
        "if(sub!=='agent')return;"
        "const ti=a.indexOf('--timeout');"
        "let s=600;"
        "if(ti>=0&&a[ti+1]){const p=parseInt(a[ti+1],10);"
        "if(Number.isFinite(p)&&p>=0)s=p;}"
        "if(s<=0)return;"
        "const ms=s*1000+30000;"
        "const t=setTimeout(()=>{try{console.error(`[openclaw] cli-exit-fix: hard wall-clock SIGKILL after ${ms/1000}s (timeout=${s}s + 30s grace)`);}catch{}"
        "try{" + proc_var + ".kill(" + proc_var + ".pid,'SIGKILL');}catch{}},ms);"
        "t.unref&&t.unref();}catch{}})();"
    )

HARD_TIMER_IIFE = _hard_timer_iife("process")
HARD_TIMER_IIFE_P1 = _hard_timer_iife("process$1")  # entry.js bundles process as process$1

PATCHES = [
    # === dist/index.js (legacy library-mode CLI; harmless if never main) ===
    {
        "file": "index.js",
        "description": "index.js: process.exit + SIGKILL fallback after runLegacyCliEntry resolves",
        "search": "runLegacyCliEntry(process.argv).catch(",
        "replace": "runLegacyCliEntry(process.argv).then(() => { setTimeout(() => { try { process.kill(process.pid, 'SIGKILL'); } catch {} }, 3000); process.exit(process.exitCode ?? 0); }).catch(",
    },
    {
        "file": "index.js",
        "description": "index.js: hard wall-clock SIGKILL timer for `agent` invocations (--timeout aware)",
        "search": "runLegacyCliEntry(process.argv).then(() =>",
        "replace": HARD_TIMER_IIFE + "runLegacyCliEntry(process.argv).then(() =>",
    },
    # === dist/entry.js (the ACTUAL CLI entry per openclaw.mjs `tryImport("./dist/entry.js")`) ===
    # This is where `openclaw agent --local` really runs. Patches on index.js are dormant
    # for the CLI hot path; entry.js is what matters.
    {
        "file": "entry.js",
        "description": "entry.js: hard wall-clock SIGKILL timer + post-resolve SIGKILL around runMainOrRootHelp",
        # The whole inline `if (...) await runMainOrRootHelp(...)` is replaced. We:
        #   (1) Run the IIFE BEFORE the dispatch so Layer 2 timer is scheduled early
        #   (2) Convert the bare-statement form to a block + post-resolve cleanup (Layer 1)
        "search": "if (!tryHandleRootVersionFastPath(process$1.argv)) await runMainOrRootHelp(process$1.argv);",
        "replace": (
            HARD_TIMER_IIFE_P1
            + "if (!tryHandleRootVersionFastPath(process$1.argv)) { "
            + "await runMainOrRootHelp(process$1.argv); "
            + "setTimeout(() => { try { process$1.kill(process$1.pid, 'SIGKILL'); } catch {} }, 3000); "
            + "process$1.exit(process$1.exitCode ?? 0); "
            + "}"
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
