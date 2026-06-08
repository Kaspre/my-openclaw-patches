# OpenClaw Upgrade Procedure

## Pre-Upgrade

1. **Backup**: Run `~/my-openclaw-backup/scripts/backup.sh` to create a pre-upgrade restore point (git commit pushed to GitHub — all prior backups are retained as separate commits). The default skips the slow Tier 1 database mirror to I: drive; the daily 3:15 AM cron handles that separately. If you need the mirror too (e.g., DR test), pass `--with-mirror`.
2. **Check release notes**: `https://github.com/openclaw/openclaw/releases/tag/v<VERSION>` — review for breaking changes, merged patches, and migration notes

## Upgrade Steps

1. **Stop watchdog timer** — prevents it from restarting the gateway mid-patch
   ```bash
   systemctl --user stop openclaw-watchdog.timer
   ```

2. **Run update without restart**
   ```bash
   openclaw update --tag v<VERSION> --no-restart --yes
   ```

3. **Update systemd unit** — bump version in Description and OPENCLAW_SERVICE_VERSION
   ```bash
   # Edit ~/.config/systemd/user/openclaw-gateway.service
   # Then:
   systemctl --user daemon-reload
   ```

4. **Restart gateway on UNPATCHED code**
   ```bash
   systemctl --user restart openclaw-gateway
   ```

5. **Baseline test (no patches)** — verify the new release WITHOUT local patches:
   - `openclaw --version` — confirm new version
   - `systemctl --user status openclaw-gateway --no-pager` — confirm running
   - Test each issue our patches fix to confirm it's still broken:
     - **Exec host override** (#11150): Does `host: "sandbox"` still throw an error?
     - **Heartbeat sessionKey** (#21682): Does exec completion still fail to notify on Discord?
   - If a patch's issue is now fixed upstream, **remove that patch from the list** and update its patch doc
   - Also check for NEW regressions introduced by the upgrade

6. **Stop gateway for patching**
   ```bash
   systemctl --user stop openclaw-gateway
   ```

7. **Dry-run gate — catch pattern drift BEFORE touching the dist**
   ```bash
   python3 ~/my-openclaw-patches/scripts/apply-all.py --dry-run
   ```
   - All entries must report `OK`. Exit code is non-zero on any miss.
   - A `WARN: ... pattern not found` means the patch's anchor no longer matches —
     review whether (a) upstream has merged the fix (retire the patch), (b) the
     anchor needs hardening per PATCHING-GUIDE.md §5b (cosmetic drift), or (c)
     real structural change requires rewriting the patch (do NOT proceed past
     this step until resolved).
   - At this point the gateway is still running on UNPATCHED code from step 4,
     so any decision here has a clean rollback path (just re-start the watchdog).
   - Security-critical Codex PreToolUse patches must fail loud on missing/stale
     artifacts. Do not continue if `codex-codemode-pretooluse-binary` or
     `openclaw-codex-native-pretool-delivery` reports artifact, version, or marker
     drift.

8. **Apply only still-needed patches** — filenames change every version, search by code patterns not filenames
   ```bash
   python3 ~/my-openclaw-patches/scripts/apply-all.py
   ```
   - Skips already-applied patches via per-script markers
   - Skip any patches confirmed fixed upstream in step 5 by commenting them out of `PATCHES` in `apply-all.py`

9. **Restart gateway on patched code**
   ```bash
   systemctl --user restart openclaw-gateway
   ```

10. **Re-enable watchdog timer**
    ```bash
    systemctl --user start openclaw-watchdog.timer
    ```

## Post-Upgrade Verification (config regressions)

Upgrades have reset non-openclaw.json config files. Check these EVERY time:

1. **`tools.exec.ask`** — must be `"off"` (OC Firewall is sole enforcer). Upgrades have reset it to `"on-miss"`.
   ```bash
   openclaw config get tools.exec.ask   # expect: off
   ```

2. **`exec-approvals.json` main agent security** — must be `"full"`. Upgrades have reset it to `"allowlist"`, which creates a redundant gateway allowlist that blocks tools not in the list (e.g. pandoc) even though OC Firewall allows them.
   ```bash
   grep -A1 '"main"' ~/.openclaw/exec-approvals.json | grep security   # expect: "full"
   ```

## Post-Patch Testing

- Verify: `openclaw --version && systemctl --user status openclaw-gateway --no-pager`
- Verify the Codex PreToolUse patch stack:
  ```bash
  python3 ~/my-openclaw-patches/scripts/check-codex-pretooluse-stack.py
  ~/.local/node-current/bin/node ~/my-openclaw-patches/scripts/prove-codex-code-mode-pretooluse.mjs
  ```
  Once the fail-closed hardening patch is promoted into the rotation, run the
  static gate with `--require-fail-closed`.
- Test Captain on Discord — confirm exec approval, heartbeat notifications, and basic functionality work
- Confirm each applied patch is working as expected
- Update patch docs with new filenames
- Update MEMORY.md with new version number

## Rollback

If something goes wrong:
1. `git log` the backup repo (`~/my-openclaw-backup`) to find the pre-upgrade commit
2. Run `~/my-openclaw-backup/scripts/restore.sh` (or manually copy files back)
3. Downgrade: `openclaw update --tag v<OLD_VERSION> --no-restart --yes`
4. Re-apply patches for the old version, restart gateway

## Why This Order

- Watchdog checks port 18789 every 5 minutes — disable it first to prevent interference
- `--no-restart` lets us control when the gateway starts on new code
- **Baseline test before patching** catches: (1) patches merged upstream that are no longer needed, (2) patches that now conflict with upstream changes, (3) new regressions unrelated to our patches
- **Dry-run gate before apply** catches pattern drift while the gateway is still running on the unpatched dist, so a half-applied state can never leave the gateway broken between steps. A `WARN: pattern not found` here is the signal to evaluate whether the patch is even still needed — see PATCHING-GUIDE.md §5b on cosmetic vs structural drift
- Gateway is stopped again for patching so it comes up clean on fully patched code
- Re-enabling the watchdog last avoids any race condition

## History

| Date | From | To | Notes |
|------|------|----|-------|
| 2026-03-09 | 2026.3.7 | 2026.3.8 | First attempt: all 3 patches applied, approval prefix-match caused auto-expire bug |
| 2026-03-09 | 2026.3.8 | 2026.3.7 | Rolled back to isolate cause |
| 2026-03-09 | 2026.3.7 | 2026.3.8 | Second attempt: 2 patches only (dropped prefix-match), working |
| 2026-03-13 | 2026.3.8 | 2026.3.12 | 8 patches applied (3 scripts updated). Regressions: tools.exec.ask reset to "on-miss", exec-approvals.json main security reset to "allowlist" |
