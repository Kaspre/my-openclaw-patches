#!/usr/bin/env python3
"""
Patch: start channels + sidecars before attaching WebSocket handlers
Upstream: openclaw/openclaw PR #63480 (OPEN, not yet merged)
Closes: openclaw/openclaw #63450

Problem
-------
`startGatewayServer()` in `src/gateway/server.impl.ts` attaches the WebSocket
handler stack (`attachGatewayWsHandlers`) before starting channel sidecars
(`startGatewaySidecars`). Any WebSocket RPC that touches the session store or
transcript history on the main thread — in particular `chat.history`, which
performs large synchronous session-store and transcript reads — runs on a live
event loop that the sidecar startup path is trying to share. The result: a
pending `chat.history` call can block sidecar startup for tens of seconds to
several minutes, during which Telegram/Discord/browser-control/plugin channels
sit inert with nothing logged.

Measured on our environment (v2026.4.8): `chat.history` blocked channel startup
for 41–157 seconds per restart before this patch. After the patch, the same
call resolves in ~11 seconds and does not serialize against sidecar startup.

Fix
---
Move the `if (!minimalTestGateway) { ... startGatewaySidecars ... }` block up
to run before `attachGatewayWsHandlers(...)`. Channels come up during the
quiet window before WebSocket RPC is live, so any subsequent `chat.history`
call lands on an already-running event loop with nothing to contend against.

This is PR #63480's exact fix, mechanically applied to the compiled bundle.

Target file
-----------
`dist/server.impl-*.js` (hash varies; script globs and filters by the unique
pattern at the `attachGatewayWsHandlers(` call site).

Usage
-----
  python3 apply-channels-before-ws-handlers.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.local/node-current/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-channels-before-ws"
TARGET_GLOB = "server.impl-*.js"

# Step 1: inject the channel-startup block BEFORE attachGatewayWsHandlers
INSERT_ANCHOR = """\t\tsetFallbackGatewayContextResolver(() => gatewayRequestContext);
\t\tattachGatewayWsHandlers({"""

INSERT_REPLACEMENT = """\t\tsetFallbackGatewayContextResolver(() => gatewayRequestContext);
\t\t// LOCAL PATCH (PR #63480): start channels + sidecars BEFORE attaching WS handlers
\t\t// so slow chat.history reads can't block channel startup. Block moved from below.
\t\tif (!minimalTestGateway) {
\t\t\tif (deferredConfiguredChannelPluginIds.length > 0) ({pluginRegistry} = reloadDeferredGatewayPlugins({
\t\t\t\tcfg: gatewayPluginConfigAtStart,
\t\t\t\tworkspaceDir: defaultWorkspaceDir,
\t\t\t\tlog,
\t\t\t\tcoreGatewayHandlers,
\t\t\t\tbaseMethods,
\t\t\t\tpluginIds: startupPluginIds,
\t\t\t\tlogDiagnostics: false
\t\t\t}));
\t\t\tlog.info("starting channels and sidecars...");
\t\t\t({pluginServices} = await startGatewaySidecars({
\t\t\t\tcfg: gatewayPluginConfigAtStart,
\t\t\t\tpluginRegistry,
\t\t\t\tdefaultWorkspaceDir,
\t\t\t\tdeps,
\t\t\t\tstartChannels,
\t\t\t\tlog,
\t\t\t\tlogHooks,
\t\t\t\tlogChannels
\t\t\t}));
\t\t}
\t\tattachGatewayWsHandlers({"""

# Step 2: delete the original (now duplicate) block that sits between the
# startGatewayTailscaleExposure call and the gateway_start hook block
DELETE_ANCHOR = """\t\tif (!minimalTestGateway) {
\t\t\tif (deferredConfiguredChannelPluginIds.length > 0) ({pluginRegistry} = reloadDeferredGatewayPlugins({
\t\t\t\tcfg: gatewayPluginConfigAtStart,
\t\t\t\tworkspaceDir: defaultWorkspaceDir,
\t\t\t\tlog,
\t\t\t\tcoreGatewayHandlers,
\t\t\t\tbaseMethods,
\t\t\t\tpluginIds: startupPluginIds,
\t\t\t\tlogDiagnostics: false
\t\t\t}));
\t\t\tlog.info("starting channels and sidecars...");
\t\t\t({pluginServices} = await startGatewaySidecars({
\t\t\t\tcfg: gatewayPluginConfigAtStart,
\t\t\t\tpluginRegistry,
\t\t\t\tdefaultWorkspaceDir,
\t\t\t\tdeps,
\t\t\t\tstartChannels,
\t\t\t\tlog,
\t\t\t\tlogHooks,
\t\t\t\tlogChannels
\t\t\t}));
\t\t}
\t\tif (!minimalTestGateway) {
\t\t\tconst hookRunner = getGlobalHookRunner();"""

DELETE_REPLACEMENT = """\t\t// LOCAL PATCH (PR #63480): block moved up (before attachGatewayWsHandlers)
\t\tif (!minimalTestGateway) {
\t\t\tconst hookRunner = getGlobalHookRunner();"""

ALREADY_PATCHED_MARKER = "LOCAL PATCH (PR #63480)"


def find_target(dist_dir):
    candidates = [
        p for p in glob.glob(os.path.join(dist_dir, TARGET_GLOB))
        if not p.endswith(BACKUP_SUFFIX)
    ]
    hits = []
    for c in candidates:
        try:
            with open(c, "r") as f:
                content = f.read()
            if ALREADY_PATCHED_MARKER in content or INSERT_ANCHOR in content:
                hits.append(c)
        except OSError:
            pass
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    for c in hits:
        with open(c, "r") as f:
            if INSERT_ANCHOR in f.read():
                return c
    return hits[0]


def patch_file(filepath, dry_run=False):
    with open(filepath, "r") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    if ALREADY_PATCHED_MARKER in content:
        return ("already_patched", basename)

    if INSERT_ANCHOR not in content:
        return ("insert_anchor_not_found", basename)
    if DELETE_ANCHOR not in content:
        return ("delete_anchor_not_found", basename)
    if content.count(INSERT_ANCHOR) != 1:
        return (f"insert_anchor_matched_{content.count(INSERT_ANCHOR)}_times", basename)
    if content.count(DELETE_ANCHOR) != 1:
        return (f"delete_anchor_matched_{content.count(DELETE_ANCHOR)}_times", basename)

    new_content = content.replace(INSERT_ANCHOR, INSERT_REPLACEMENT)
    new_content = new_content.replace(DELETE_ANCHOR, DELETE_REPLACEMENT)

    # Sanity: after the two replacements, "starting channels and sidecars..."
    # should appear exactly once (it was once; we added one and deleted one).
    if new_content.count('starting channels and sidecars...') != 1:
        return (f"post_patch_sanity_failed_channel_log_count_{new_content.count('starting channels and sidecars...')}", basename)

    if dry_run:
        return ("would_patch", basename)

    backup_path = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)

    with open(filepath, "w") as f:
        f.write(new_content)

    return ("patched", basename)


def main():
    parser = argparse.ArgumentParser(
        description="Apply channels-before-ws-handlers patch (PR #63480)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Check pattern without modifying file")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT,
                        help="OpenClaw dist directory")
    args = parser.parse_args()

    if not os.path.isdir(args.dist_dir):
        print(f"ERROR: dist dir does not exist: {args.dist_dir}", file=sys.stderr)
        sys.exit(1)

    target = find_target(args.dist_dir)
    if not target:
        print(f"ERROR: no server.impl-*.js bundle containing target pattern found in {args.dist_dir}",
              file=sys.stderr)
        sys.exit(1)

    status, basename = patch_file(target, dry_run=args.dry_run)

    if status == "patched":
        print(f"OK: patched {basename} (backup at {basename}{BACKUP_SUFFIX})")
        sys.exit(0)
    elif status == "would_patch":
        print(f"DRY-RUN: would patch {basename}")
        sys.exit(0)
    elif status == "already_patched":
        print(f"SKIP: {basename} already patched")
        sys.exit(0)
    elif status == "insert_anchor_not_found":
        print(f"ERROR: insert anchor not found in {basename} (bundle shape changed?)",
              file=sys.stderr)
        sys.exit(2)
    elif status == "delete_anchor_not_found":
        print(f"ERROR: delete anchor not found in {basename} (bundle shape changed?)",
              file=sys.stderr)
        sys.exit(2)
    else:
        print(f"ERROR: unexpected status '{status}' for {basename}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
