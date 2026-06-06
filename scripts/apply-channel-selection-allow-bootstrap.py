#!/usr/bin/env python3
"""Patch: pass allowBootstrap to resolveOutboundChannelPlugin from channel-selection.

Issue: `openclaw agent --local` only eagerly loads 6 hook-only plugins
(memory-guardian, oc-firewall, otel, mem-rc, knostic-shield, web-sanitizer).
Channel/tool plugins — discord in particular — are absent from the eager load.
When the model invokes the in-agent `openclaw_message` tool, the tool path
resolves the outbound channel via `resolveAvailableKnownChannel`
(`dist/channel-selection-*.js`), which calls `resolveOutboundChannelPlugin`
(`dist/channel-resolution-*.js`) WITHOUT `allowBootstrap: true`. The resolver
falls through to `getChannelPlugin` (metadata-only) and returns undefined for
the caller's purposes; the caller throws "Channel is unavailable: discord".

This recurred 6 times in 14 days (2026-05-07, -09, -10, -11, -14, -20, -21) all
in ~04:00-05:00 UTC heartbeat windows, where `--local` agent dispatches run
their Phase 3 message tool. CLI invocations work because they route through
the running gateway via HTTP RPC; the gateway has the discord adapter loaded.

Fix: pass `allowBootstrap: true` in `resolveAvailableKnownChannel`'s call to
`resolveOutboundChannelPlugin`. When the plugin is already loaded (gateway
context), this is a no-op fast path. When it's not (--local context), the
bootstrap branch runs once to lazy-load the channel adapter.

Diagnostic chain (current dist):
  channel-selection-DHX6ygME.js:23-26 → resolveOutboundChannelPlugin({channel, cfg})
  channel-resolution-jR5Cj7fx.js:32  → if (params.allowBootstrap !== true) return resolve()
                                                                ^^^^^^^^^^
                                                              this short-circuit
                                                              is the bug

L3 friction triage diagnosis (2026-05-21T09:15Z, FRI-2026-05-21-a3b4c5d6e7f89012)
confirmed this exact code path on `dist/channel-selection-DHX6ygME.js:138`.
Findings doc: `workspace/docs/findings/2026-05-20-discord-tool-path-channel-unavailable.md`.

Local-only fix to be promoted upstream. Cross-reference: #77254 (which fixed
the cron-announce / final-reply paths but did NOT touch the in-agent tool
path's caller). Our patch closes the remaining hole in the same family.

RETIREMENT: when upstream lands an equivalent (channel-selection caller passes
allowBootstrap, OR resolver bootstraps by default on first miss). Dry-run will
report "already-patched" or "pattern not found".
"""
import argparse
import re
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".local/node-current/lib/node_modules/openclaw/dist"

# Hash-suffix-tolerant glob for the bundled file (the suffix changes across
# OC releases; the function name + structure does not).
FILE_GLOB = "channel-selection-*.js"

# Anchor: the exact call shape we need to extend. We require the absence of
# `allowBootstrap` to avoid double-patching.
ANCHOR_RE = re.compile(
    r"return resolveOutboundChannelPlugin\(\{\s*"
    r"channel:\s*normalized,\s*"
    r"cfg:\s*params\.cfg\s*"
    r"\}\)",
)

REPLACEMENT = (
    "return resolveOutboundChannelPlugin({"
    "channel: normalized,"
    "cfg: params.cfg,"
    "allowBootstrap: true"  # local-patch: unblock agent-runtime tool path
    "})"
)

# Used to detect already-applied state.
APPLIED_MARKER = "allowBootstrap: true"


def find_target(dist: Path) -> Path | None:
    matches = sorted(dist.glob(FILE_GLOB))
    # Exclude shim files (re-exports that just import + export a symbol).
    candidates: list[Path] = []
    for m in matches:
        size = m.stat().st_size
        if size < 500:  # shim files are tiny re-export stubs
            continue
        candidates.append(m)
    if not candidates:
        return None
    if len(candidates) > 1:
        print(
            f"WARN: multiple non-shim {FILE_GLOB} found: {[m.name for m in candidates]}",
            file=sys.stderr,
        )
        return None
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pass allowBootstrap:true through channel-selection to resolveOutboundChannelPlugin"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    dist = args.dist_dir
    if not dist.exists():
        print(f"SKIP: dist dir not found: {dist}")
        return 0

    target = find_target(dist)
    if target is None:
        print(f"WARN: no {FILE_GLOB} bundle found")
        return 1

    content = target.read_text()
    if APPLIED_MARKER in content and "resolveOutboundChannelPlugin({channel: normalized,cfg: params.cfg,allowBootstrap: true" in content:
        print(f"OK: {target.name} (already applied)")
        return 0

    matches = list(ANCHOR_RE.finditer(content))
    if not matches:
        print(f"WARN: anchor pattern not found in {target.name} (structural drift; review patch)")
        return 1
    if len(matches) > 1:
        print(f"WARN: anchor matched {len(matches)} times (expected 1); aborting")
        return 1

    if args.dry_run:
        print(f"would patch {target.name}: pass allowBootstrap:true at offset {matches[0].start()}")
        return 0

    new_content = ANCHOR_RE.sub(REPLACEMENT, content, count=1)
    target.write_text(new_content)
    print(f"PATCHED: {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
