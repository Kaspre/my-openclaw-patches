#!/usr/bin/env python3
"""Patch Discord accepted typing cues for unmentioned guild messages.

Local issue
-----------
In OpenClaw 2026.5.18, the Discord extension sends the early accepted typing
cue only for DMs:

    if (!ctx.isDirectMessage || ctx.isGuildMessage || ctx.isGroupDm) return false;

That leaves allowlisted guild channels silent until the later reply pipeline
starts typing, which can be tens of seconds later on slow model starts.

This patch mirrors the important gate semantics from upstream PR #76091:
explicit `typingMode: "instant"` should allow accepted typing feedback in
guild/group contexts, while the default behavior still requires a mention for
guild/group messages.

Important: for unmentioned guild prompts, this patch requires either
`session.typingMode` or `agents.defaults.typingMode` to be `"instant"`.
Captain's local config sets `agents.defaults.typingMode = "instant"`.
"""

from __future__ import annotations

import argparse
import glob
import shutil
import sys
from pathlib import Path


PROFILE_DISCORD_DIST_DIR = (
    Path.home() / ".openclaw/npm/node_modules/@openclaw/discord/dist"
)
GLOBAL_OPENCLAW_DIST_DIR = (
    Path.home() / ".local/node-current/lib/node_modules/openclaw/dist"
)


def _live_discord_dist_from_installs() -> "Path | None":
    """Resolve the discord plugin's LIVE dist dir from the gateway's installs.json.

    OC 5.19+ installs plugins per-project under ~/.openclaw/npm/projects/<hash>/,
    so PROFILE_DISCORD_DIST_DIR (the node_modules symlink) goes stale after an
    upgrade — it keeps pointing at the prior version's .pnpm store. The gateway
    loads the path recorded in installs.json, so patch THAT. Returns None if it
    can't be resolved (callers still fall back to the node_modules/core dirs).
    See findings: 2026-06-03 5.28 upgrade — discord patches missed the live tree.
    """
    import json
    installs = Path.home() / ".openclaw/plugins/installs.json"
    try:
        for p in json.loads(installs.read_text()).get("plugins", []):
            if p.get("pluginId") == "discord" and p.get("enabled"):
                src = p.get("source", "")
                if src.endswith("index.js"):
                    d = Path(src).parent
                elif p.get("rootDir"):
                    d = Path(p["rootDir"]) / "dist"
                else:
                    continue
                if d.is_dir():
                    return d
    except Exception:
        pass
    return None


TARGET_GLOB = "message-handler-*.js"
BACKUP_SUFFIX = ".bak-discord-guild-accepted-typing"

OLD_CODE = """function shouldSendAcceptedDiscordTypingCue(ctx) {
\tif (ctx.abortSignal?.aborted) return false;
\tif (!ctx.isDirectMessage || ctx.isGuildMessage || ctx.isGroupDm) return false;
\tif (!ctx.messageText.trim()) return false;
\tconst configuredTypingMode = ctx.cfg.session?.typingMode ?? ctx.cfg.agents?.defaults?.typingMode;
\treturn configuredTypingMode === void 0 || configuredTypingMode === "instant";
}
function queueAcceptedDiscordTypingCue(ctx) {
\tif (!shouldSendAcceptedDiscordTypingCue(ctx)) return;
\tconst { rest } = createDiscordRestClient({
"""

NEW_CODE = """function shouldSendAcceptedDiscordTypingCue(ctx) {
\tif (ctx.abortSignal?.aborted) return false;
\tif (!ctx.messageText.trim()) return false;
\tconst configuredTypingMode = ctx.cfg.session?.typingMode ?? ctx.cfg.agents?.defaults?.typingMode;
\tif (configuredTypingMode !== void 0) return configuredTypingMode === "instant";
\tif (ctx.isGuildMessage || ctx.isGroupDm) return ctx.effectiveWasMentioned === true;
\treturn ctx.isDirectMessage;
}
function queueAcceptedDiscordTypingCue(ctx) {
\tif (!shouldSendAcceptedDiscordTypingCue(ctx)) return;
\tconst configuredTypingMode = ctx.cfg.session?.typingMode ?? ctx.cfg.agents?.defaults?.typingMode;
\tlogVerbose(`discord accepted typing cue queued for channel ${ctx.messageChannelId} mode=${configuredTypingMode ?? "default"}`);
\tconst { rest } = createDiscordRestClient({
"""

PATCHED_MARKER = "discord accepted typing cue queued for channel"
UPSTREAM_MARKERS = (
    "shouldStartAcceptedTypingFeedback",
    "DiscordReplyTypingFeedback",
    "replyTypingFeedback",
)


def resolve_candidate_dirs(args: argparse.Namespace) -> list[Path]:
    if args.discord_dist_dir is not None:
        return [args.discord_dist_dir]
    # Live projects-dir tree first (what the gateway loaded), then the legacy
    # node_modules symlink + core dist as fallbacks. Dedup below drops repeats.
    dirs = []
    live = _live_discord_dist_from_installs()
    if live is not None:
        dirs.append(live)
    dirs += [PROFILE_DISCORD_DIST_DIR, GLOBAL_OPENCLAW_DIST_DIR]
    if args.dist_dir is not None:
        dirs.append(args.dist_dir)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        resolved = str(path.expanduser().resolve()) if path.exists() else str(path.expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def candidate_files(discord_dist_dir: Path) -> list[Path]:
    return [
        Path(path)
        for path in sorted(glob.glob(str(discord_dist_dir / TARGET_GLOB)))
        if BACKUP_SUFFIX not in path and ".bak" not in Path(path).name
    ]


def patch_file(path: Path, dry_run: bool) -> tuple[str, str]:
    content = path.read_text()
    basename = path.name

    if PATCHED_MARKER in content:
        return "already_patched", basename

    if any(marker in content for marker in UPSTREAM_MARKERS):
        return "upstream_like", basename

    if OLD_CODE not in content:
        if "shouldSendAcceptedDiscordTypingCue" in content:
            return "pattern_not_found", basename
        return "not_target", basename

    count = content.count(OLD_CODE)
    if count != 1:
        return f"pattern_matched_{count}_times", basename

    if dry_run:
        return "would_patch", basename

    backup_path = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
    path.write_text(content.replace(OLD_CODE, NEW_CODE, 1))
    return "patched", basename


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply Discord guild accepted typing cue patch"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, help="Additional OpenClaw dist dir")
    parser.add_argument(
        "--discord-dist-dir",
        type=Path,
        help="Override @openclaw/discord/dist directory",
    )
    args = parser.parse_args()

    candidate_dirs = resolve_candidate_dirs(args)
    files: list[Path] = []
    for directory in candidate_dirs:
        if directory.is_dir():
            files.extend(candidate_files(directory))

    if not files:
        dirs = ", ".join(str(path) for path in candidate_dirs)
        print(f"ERROR: no {TARGET_GLOB} files found in candidate dirs: {dirs}", file=sys.stderr)
        sys.exit(1)

    target_seen = False
    errors: list[str] = []

    for path in files:
        status, basename = patch_file(path, args.dry_run)
        if status == "not_target":
            continue
        target_seen = True
        label = f"{path.parent}: {basename}"

        if status == "patched":
            print(f"OK: patched {label} (backup suffix: {BACKUP_SUFFIX})")
        elif status == "would_patch":
            print(f"DRY-RUN: would patch {label}")
        elif status == "already_patched":
            print(f"OK: {label} already patched")
        elif status == "upstream_like":
            print(f"OK: {label} appears to carry upstream typing feedback")
        else:
            msg = f"ERROR: {label}: {status}"
            print(msg, file=sys.stderr)
            errors.append(msg)

    if not target_seen:
        dirs = ", ".join(str(path) for path in candidate_dirs)
        print(
            "ERROR: no Discord message handler containing the accepted typing cue "
            f"function was found in candidate dirs: {dirs}",
            file=sys.stderr,
        )
        sys.exit(1)

    if errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
