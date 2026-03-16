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

## Current Patches (v2026.3.13)

| # | Script | Issue | Files | Description |
|---|--------|-------|-------|-------------|
| 1 | `apply-exec-host-override.py` | #11150 / PR #11185 | ~10 | Silently override model's `host: "sandbox"` with configured host |
| 2 | `apply-approval-auto-expire-fix.py` | (no issue) | 2 | Recognize Discord native approvals in `hasExecApprovalClients` |
| 3 | `apply-approval-prefix-match.py` | #9591 / PR #9641 | 2 | Allow 8-char slugs for `/approve` (TUI/SSH/Telegram) |
| 4 | `apply-approval-desc-routing.py` | #28753 | ~10 | Keep approval embeds in originating channel |
| 5 | `apply-heartbeat-sessionkey-fix.py` | #14191 / PR #21682 | ~16 | Fix exec notification delivery (Changes 2-5) |
| 6 | `apply-memoryflush-fix.py` | #12590 | 1 | Fix flush skipping every other compaction |
| 7 | `apply-session-key-cli.py` | PR #35241 | 2 | Add `--session-key` flag to `openclaw agent` |
| 8 | `apply-ui-message-vanish-fix.py` | #14928 / #9183 | 1 | Fix user messages vanishing in dashboard |
| 9 | `ws-handshake-timeout.sh` | #44718 / PRs #44784 #44849 | 29 | Increase WS handshake timeouts (server 3s→15s, client 2s→10s) |
| 10 | `apply-loglevel-fix.py` | #29448 / PR #44646 | 9 | Fix inverted levelToMinLevel mapping (debug logging broken) |

## Usage

Each script supports:
- `--dry-run` — check patterns without modifying files
- `--dist-dir PATH` — override the OpenClaw dist directory

The master script also supports:
- `--only name1 name2` — apply only specific patches
- `--skip name1 name2` — skip specific patches

```bash
# Apply only approval-related patches
python3 scripts/apply-all.py --only approval-auto-expire approval-prefix-match approval-desc-routing

# Apply everything except UI patch
python3 scripts/apply-all.py --skip ui-message-vanish
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
