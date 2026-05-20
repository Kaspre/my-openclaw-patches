# OpenClaw Local Patches

Executable patch scripts for self-hosted OpenClaw. These fix bugs in compiled JS bundles that don't yet have upstream fixes (or have unmerged PRs).

## Quick Start

```bash
# Dry-run all patches (shows what would change)
python3 scripts/apply-all.py --dry-run

# Apply all patches
python3 scripts/apply-all.py

# Restart gateway to activate
systemctl --user restart openclaw-gateway
```

## Active Patches (v2026.4.12)

| # | Script | Issue | Files | Description |
|---|--------|-------|-------|-------------|
| 1 | `apply-heartbeat-sessionkey-fix.py` | #14191 / PR #50818 | 2 | Fix exec notification delivery (Changes 2+4 of PR #21682; Changes 3+5 refactored upstream) |
| 2 | `apply-memoryflush-fix.py` | #12590 / PR #51421 | 1 | Fix flush skipping every other compaction |
| 3 | `apply-plugin-register-skip-on-inspection.py` | #56522 | 1 | Skip register() during plugin inspection (startup perf) |
| 4 | `apply-cli-exit-fix.py` | #63609 | 2 | process.exit after CLI completes (partial — #64072 covers Windows) |
| 5 | `apply-discord-guild-accepted-typing.py` | #79104 / PR #76091 | 1 | Restore early Discord typing cue in allowlisted guild channels when `typingMode=instant` |

### On Hold

| Script | Issue | Description |
|--------|-------|-------------|
| `apply-sessions-manage-tool.py` | #10981 / PR #52422 | Add `sessions_manage` tool with semantic compaction. Apply on demand: `python3 scripts/apply-all.py --only sessions-manage-tool` |

### Retired Patches

| Script | Retired | Reason |
|--------|---------|--------|
| `apply-bootstrap-missing-marker-fix.py` | v2026.5.18-beta.1 | Runtime path fixed upstream via resolver-level completed-workspace root `BOOTSTRAP.md` filtering. Findings: `docs/retired-bootstrap-missing-marker-fix-2026-05-19.md` |
| `apply-channels-before-ws-handlers.py` | v2026.4.12 | Merged upstream (#63480 in v4.10) |
| `apply-cron-duplicate-fix.py` | v2026.4.12 | Superseded upstream (`previousRunAtMs` guard + #63507) |
| `apply-loglevel-fix.py` | v2026.4.9 | Merged upstream (PR #44646) |
| `apply-cache-trace-systemprompt-fix.py` | v2026.4.9 | Merged upstream (PR #58928) |
| `apply-ui-message-vanish-fix.py` | v2026.3.13 | Fixed upstream |
| `apply-plugin-cache-global.py` | v2026.3.22 | Fixed upstream (bundle refactor) |
| `apply-cache-trace-redact-apikey.py` | v2026.3.24 | No unredacted files remain |
| `apply-exec-host-override.py` | v2026.3.31 | Fixed upstream (#57689) |
| `apply-approval-auto-expire-fix.py` | v2026.3.31 | Tabled — OC Firewall handles security |
| `apply-approval-prefix-match.py` | v2026.3.31 | Tabled — OC Firewall handles security |
| `apply-approval-desc-routing.py` | v2026.3.31 | Tabled — OC Firewall handles security |
| `apply-session-key-cli.py` | v2026.3.31 | Superseded by native `--session-id` flag (v2026.3.22) |
| `ws-handshake-timeout.sh` | v2026.3.28 | Fixed upstream (bumped to 10s + env var) |

### Notes for v2026.4.12
- Patch 1 (heartbeat): Changes 3+5 no longer match — code refactored upstream. Changes 2+4 still apply cleanly.
- Patch 2 (memoryflush): Buggy pattern exists in 1 of 4 files with the counter variable.
- Patch 5 (cli-exit): v4.10 #64072 fixes the Windows variant; our patch covers the Linux/WSL case.

## Usage

Each script supports:
- `--dry-run` — check patterns without modifying files
- `--dist-dir PATH` — override the OpenClaw dist directory

The master script also supports:
- `--only name1 name2` — apply only specific patches
- `--skip name1 name2` — skip specific patches

```bash
# Apply sessions-manage-tool on demand
python3 scripts/apply-all.py --only sessions-manage-tool

# Dry-run a specific patch
python3 scripts/apply-heartbeat-sessionkey-fix.py --dry-run
```

## MG Extension Workarounds

These are not source patches — they live in `~/.openclaw/extensions/memory-guardian/index.ts` and survive upgrades.

| Workaround | Issue | Description |
|-----------|-------|-------------|
| Pre-compaction context dump | #19488 | Mechanically dumps conversation to `memory/pre-compaction-*.md` before compaction; MG injects pointer post-compaction. Docs: `~/.openclaw/workspace/patches/pre-compaction-context-dump.md` |

## After OpenClaw Upgrades

1. Run `python3 scripts/apply-all.py --dry-run` to check which patches still apply
2. Patterns that don't match may need updating (filenames and code change per version)
3. Check upstream release notes — some patches may have been merged
4. Apply, restart gateway, test
5. Verify MG extension workarounds still function (check `before_compaction` hook output)

## How It Works

Each script uses exact string replacement on OpenClaw's Vite/Rollup bundles. Bundle filenames contain content hashes that change every version, so scripts search by glob pattern (e.g., `gateway-cli-*.js`) rather than exact filenames.

Key conventions:
- Backups use descriptive suffixes (`.bak-exec-host`, `.bak-heartbeat`, etc.)
- Already-patched files are detected and skipped (idempotent)
- Each replacement is verified to occur exactly once per file
- All scripts use Python 3 with no external dependencies

## Documentation

Detailed patch docs (root cause analysis, before/after code, re-application notes) are in `docs/`.
