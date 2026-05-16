#!/usr/bin/env python3
"""
Apply all OpenClaw patches in the correct order.

Usage:
  python3 apply-all.py [--dry-run] [--dist-dir PATH]

Active patches (re-verified 2026.5.12-beta.2):
  1. heartbeat-sessionkey              — partial upstream coverage; PR #80214 covers Change 4 only (Changes 1/2/3 still apply to 3 files)
  2. bootstrap-missing-marker-fix      — suppress BOOTSTRAP.md marker (third-party PR #42542 still OPEN)
  3. plugin-register-skip-on-inspection — partial upstream coverage; issue #56522 fix covers config schema path only (different loader surface still applies)
  4. cli-exit-fix                      — process.exit + SIGKILL-fallback on runLegacyCliEntry resolve; fixes `agent --local` post-session hang (TECH-2026-2946); plain process.exit gets preempted by ref'd handles from LCM/otel/plugin background loops
  5. plugin-metadata-snapshot-memo     — CLI bootstrap memo of loadPluginMetadataSnapshot (no upstream PR)
  6. web-search-onstartup              — flip exa/firecrawl plugin manifests to activation.onStartup=true (kept but proved insufficient on its own; see #7)
  7. passive-plugin-hook-injection     — inject no-op `api.on("before_agent_start", () => {})` into exa/firecrawl register(api) bodies so manifest-hook-owner activation trigger fires in --local forks (the real fix for web_search providers not loading)

Wrapper workarounds (~/my-openclaw-backup/scripts/upgrade.sh) — UPSTREAM-FIXED in 5.10-beta.4+:
  - v-prefix verify rollback (#74069 → PR #80480)
  - TimeoutStartSec clobber by doctor (#80462 → PR #80485)
  - plugins.deny stale-id fatal (#77802 → PR #80471)
  All three behaviors landed in beta.4+. Wrapper logic kept; idempotent re-runs are safe.

On hold:
  - sessions-manage-tool    — programmatic session compact/reset (PR #52422, apply on demand)

Retired on v2026.5.12-beta.2:
  - memoryflush-fix                    — our PR #51421 merged 2026-05-08 by Kaspre (dry-run finds no targets)
  - plugin-ts-source-discovery-fix     — our PR #80557 merged 2026-05-12 by Kaspre (dry-run: pattern_not_found + already-patched)

Partially upstream-merged in v2026.5.12-beta.2 (still load-bearing):
  - heartbeat-sessionkey               — PR #80214 (merged 2026-05-11) covers Change 4 only; Changes 1/2/3 still apply
  - plugin-register-skip-on-inspection — issue #56522 closed 2026-04-25 addressed config.get/config.schema; loader.*.js surface still applies

Retired on v2026.4.12:
  - cron-duplicate-fix      — superseded upstream (previousRunAtMs guard + #63507)
  - channels-before-ws-handlers — merged upstream (#63480 in v4.10)

Retired on v2026.4.9:
  - loglevel-fix            — merged upstream (PR #44646)
  - cache-trace-systemprompt-fix — merged upstream (PR #58928)

Retired on v2026.3.31:
  - exec-host-override      — fixed upstream (#57689)
  - approval-auto-expire    — tabled (OC Firewall handles security)
  - approval-prefix-match   — tabled (OC Firewall handles security)
  - approval-desc-routing   — tabled (OC Firewall handles security)
  - session-key-cli         — superseded by native --session-id (v2026.3.22)
  - cache-trace-redact      — no unredacted files remain
"""

import argparse
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

PATCHES = [
    # heartbeat-sessionkey — partially upstream-merged in v2026.5.12-beta.2.
    # PR #21682 → #50818 → #80214 (merged 2026-05-11 by Kaspre, commit
    # 7eefb26bc8d8) covers Change 4 (exec: prefix in resolveHeartbeatReasonKind)
    # which dry-runs as "no matching files found". Changes 1/2/3 still apply
    # (3 files patched on beta.2). Revisit once remaining changes upstream.
    ("heartbeat-sessionkey", "apply-heartbeat-sessionkey-fix.py"),
    # Retired on v2026.5.12-beta.2: PR #51421 merged 2026-05-08 by Kaspre.
    # Script kept on disk for archaeology.
    # ("memoryflush-fix", "apply-memoryflush-fix.py"),
    # Retired in v2026.4.9: PR #44646 landed upstream. All 5 loglevel issues
    # (inverted mapping, <= file/console comparisons, child logger minLevel
    # inheritance, sub-logger minLevel propagation) are fixed in v4.9 bundles.
    # Verified 2026-04-09 in logger--Y4fLUmQ.js + subsystem-C1arrdPy.js.
    # Script kept on disk for archaeology.
    # ("loglevel-fix", "apply-loglevel-fix.py"),
    # Retired in v2026.4.12: upstream isRunnableJob now uses previousRunAtMs > lastRunAtMs
    # guard for cron jobs + #63507 fixes nextRunAtMs <= 0. Script kept for archaeology.
    # ("cron-duplicate-fix", "apply-cron-duplicate-fix.py"),
    ("bootstrap-missing-marker-fix", "apply-bootstrap-missing-marker-fix.py"),
    # Retired in v2026.4.9: merged upstream (PR #58928 effectively landed as a
    # fallback read `context.systemPrompt ?? context.system` at the recordStage
    # call site in pi-embedded-*.js). Script kept on disk for archaeology.
    # ("cache-trace-systemprompt-fix", "apply-cache-trace-systemprompt-fix.py"),
    # plugin-register-skip-on-inspection — partially upstream-fixed in
    # v2026.5.12-beta.2. Issue #56522 closed 2026-04-25 by steipete (commit
    # fc5920fb5134) addresses the config.get / config.schema schema-loading
    # paths, but our patch targets a different loader.*.js surface that
    # still dry-runs as "would patch loader-*.js". Keep applying until the
    # remaining surface lands upstream.
    ("plugin-register-skip-on-inspection", "apply-plugin-register-skip-on-inspection.py"),
    # Retired on v2026.5.12-beta.2: PR #80557 merged 2026-05-12 by Kaspre.
    # Was local equivalent fixing #80503 (untracked global TS-source plugins —
    # otel-observability, lossless-claw source checkouts, hand-installed
    # dual-manifest extensions — silently dropped on OC 5.10+). Script kept
    # on disk; re-enable here if dry-run shows the fix didn't land.
    # ("plugin-ts-source-discovery-fix", "apply-plugin-ts-source-discovery-fix.py"),
    # plugin-metadata-snapshot-memo (2026-05-11): in-process memo of
    # loadPluginMetadataSnapshot. CLI bootstrap calls this ~5x per invocation,
    # each rebuilding the same ~16s snapshot. With memo: openclaw plugins list
    # drops from ~91s → ~7s (~13× speedup); gateway status drops from ~77s → ~19s.
    # No-op for already-fast paths (--version/--help). Findings doc:
    # workspace/docs/findings/2026-05-11-cli-startup-perf-investigation.md
    ("plugin-metadata-snapshot-memo", "apply-plugin-metadata-snapshot-memo.py"),
    # web-search-onstartup (2026-05-12): flip exa/firecrawl plugin manifests
    # to activation.onStartup=true. Originally hypothesized to fix the
    # web_search-disabled issue but later proved INSUFFICIENT — see
    # passive-plugin-hook-injection below. Kept because it costs nothing
    # and may help other downstream consumers of the manifest flag.
    ("web-search-onstartup", "apply-web-search-onstartup.py"),
    # passive-plugin-hook-injection (2026-05-12): inject a no-op
    # `api.on("before_agent_start", () => {})` into the register(api) body of
    # exa/firecrawl. `agent --local` forks filter plugins by activation
    # trigger; the "manifest-hook-owner" trigger requires the plugin to have
    # registered at least one hook at runtime. Passive providers (only
    # api.registerWebSearchProvider) declare no hooks → no trigger → not
    # loaded in --local. The no-op hook gives them an activation trigger
    # without changing behavior. See investigation notes in the script.
    ("passive-plugin-hook-injection", "apply-passive-plugin-hook-injection.py"),
    # sessions-manage-tool — NEEDS REVIEW on v2026.5.10-beta.2 (2026-05-10).
    # Patch script exists (apply-sessions-manage-tool.py) and was originally
    # for PR #52422 (closed by Kaspre 2026-04-26 as superseded). Dry-run on
    # beta.2 shows extensive pattern drift: gateway-cli + auth-profiles
    # target file categories now have 0 matches; multiple within-file
    # patterns ("pattern not found") in openclaw-tools, docker, method-scopes,
    # dangerous-tools. The wrapper's Step 9 grep for `sessions_manage` returns
    # 0 files on beta.2 — tool was not upstream-merged under that name.
    # Decide: rewrite the patch against current dist structure, OR retire if
    # the underlying functionality is now provided by a different mechanism.
    # ("sessions-manage-tool", "apply-sessions-manage-tool.py"),
    # Retired in v2026.4.12: merged upstream (#63480 in v4.10 release notes).
    # Script kept for archaeology.
    # ("channels-before-ws-handlers", "apply-channels-before-ws-handlers.py"),
    # cli-exit-fix — RE-ENABLED on v2026.5.12-beta.2 (2026-05-12). Retired
    # 2026-05-06 validation only covered `openclaw config get` (upstream
    # stopAndWait via PR #70691 fixed that path). `openclaw agent --local`
    # still hangs post-session-end (TECH-2026-2946/2947), causing RC backlog
    # burn iterations to consume the full 60min ITER_TIMEOUT when real work
    # completes in ~20min. This patch's forced process.exit on the success
    # path overrides whatever plugin background handle is pinning the loop.
    # Original entry.js half was dropped — upstream refactored to await form
    # (and the original replace had a `process$1` regex typo anyway).
    ("cli-exit-fix", "apply-cli-exit-fix.py"),
    # Local mirror of openclaw#82403 (merged 2026-05-16T11:13:55Z, commit
    # f50c65f12454, NOT in v2026.5.16-beta.2 which was cut from a pre-merge
    # commit). Releases Codex raw assistant completions when turn/completed is
    # missing — closes the dominant cause of `openclaw agent --local` zombie
    # processes that #71710 tracks. Retire this entry once an OC release that
    # bundles #82403 (likely 5.16-beta.3 or later) is installed.
    ("codex-raw-completion-fix", "apply-codex-raw-completion-fix.py"),
    # Retired on v2026.4.15: upstream added resolveBundledPluginCompatibleLoadValues
    # in activation-context-*.js which plumbs applyPluginAutoEnable + overrides
    # before the plugin registry loads. Our v4.15 probe returns 12 providers
    # unpatched. Script kept on disk; re-enable here if the regression returns.
    # ("web-search-activate-on-empty", "apply-web-search-activate-on-empty.py"),
    # On hold — apply with: --only sessions-manage-tool
    # ("sessions-manage-tool", "apply-sessions-manage-tool.py"),
]


def main():
    parser = argparse.ArgumentParser(description="Apply all OpenClaw patches")
    parser.add_argument("--dry-run", action="store_true", help="Check patterns without modifying files")
    parser.add_argument("--dist-dir", help="OpenClaw dist directory (passed to each script)")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="Only apply specific patches by name")
    parser.add_argument("--skip", nargs="+", metavar="NAME", help="Skip specific patches by name")
    args = parser.parse_args()

    patches = PATCHES
    if args.only:
        patches = [(n, s) for n, s in patches if n in args.only]
        if not patches:
            print(f"ERROR: No patches matched --only {args.only}")
            print(f"Available: {', '.join(n for n, _ in PATCHES)}")
            sys.exit(1)
    if args.skip:
        patches = [(n, s) for n, s in patches if n not in args.skip]

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"OpenClaw Patch Suite — {mode}")
    print(f"Patches: {len(patches)}")
    print("=" * 60)
    print()

    results = {}
    for name, script in patches:
        print(f"--- {name} ---")
        cmd = [sys.executable, os.path.join(SCRIPTS_DIR, script)]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.dist_dir:
            cmd.extend(["--dist-dir", args.dist_dir])

        result = subprocess.run(cmd, capture_output=False)
        results[name] = result.returncode
        print()

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, code in results.items():
        status = "OK" if code == 0 else f"FAILED (exit {code})"
        print(f"  {name:30s} {status}")

    failed = sum(1 for c in results.values() if c != 0)
    if failed:
        print(f"\n{failed} patch(es) failed!")
        sys.exit(1)
    else:
        print(f"\nAll {len(results)} patches completed successfully.")
        if not args.dry_run:
            print("Restart gateway: systemctl --user restart openclaw-gateway")


if __name__ == "__main__":
    main()
