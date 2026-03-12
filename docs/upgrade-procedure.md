# OpenClaw Upgrade Procedure

## Pre-Upgrade

1. **Backup**: Run `~/my-openclaw-backup/scripts/backup.sh` to create a pre-upgrade restore point (git commit pushed to GitHub — all prior backups are retained as separate commits)
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

7. **Apply only still-needed patches** — filenames change every version, search by code patterns not filenames
   - Exec host enforcement override (`patches/exec-host-enforcement-override.md`)
   - Heartbeat sessionKey fix, Changes 2-5 (`patches/heartbeat-sessionkey-fix.md`)
   - ~~Approval prefix-match~~ — DROPPED: caused auto-expire bug on v2026.3.8, not needed with Discord buttons
   - Skip any patches confirmed fixed upstream in step 5

8. **Restart gateway on patched code**
   ```bash
   systemctl --user restart openclaw-gateway
   ```

9. **Re-enable watchdog timer**
   ```bash
   systemctl --user start openclaw-watchdog.timer
   ```

## Post-Patch Testing

- Verify: `openclaw --version && systemctl --user status openclaw-gateway --no-pager`
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
- Gateway is stopped again for patching so it comes up clean on fully patched code
- Re-enabling the watchdog last avoids any race condition

## History

| Date | From | To | Notes |
|------|------|----|-------|
| 2026-03-09 | 2026.3.7 | 2026.3.8 | First attempt: all 3 patches applied, approval prefix-match caused auto-expire bug |
| 2026-03-09 | 2026.3.8 | 2026.3.7 | Rolled back to isolate cause |
| 2026-03-09 | 2026.3.7 | 2026.3.8 | Second attempt: 2 patches only (dropped prefix-match), working |
