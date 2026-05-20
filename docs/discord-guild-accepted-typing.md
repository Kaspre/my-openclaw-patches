# Discord Guild Accepted Typing Patch

## Summary

Local OpenClaw 2026.5.18 had a Discord regression where allowlisted guild
channels did not receive the immediate "accepted your prompt" typing cue.
Users saw a long silent gap, then the normal reply-pipeline typing indicator
started much later.

The live runtime patch was tested on 2026-05-19 in `#captain-verbose` and
restored the early typing indicator.

## Root Cause

The installed `@openclaw/discord` bundle only allowed the early accepted typing
cue for DMs:

```js
if (!ctx.isDirectMessage || ctx.isGuildMessage || ctx.isGroupDm) return false;
```

That excludes guild channels even when the channel is explicitly allowlisted
and `requireMention:false`.

## Patch

`scripts/apply-discord-guild-accepted-typing.py` patches Discord
`message-handler-*.js` bundles in both the profile-installed
`@openclaw/discord/dist` path and the global OpenClaw `dist` path so:

- explicit `typingMode:"instant"` sends accepted typing cues for guild/group
  contexts;
- default/unconfigured guild/group behavior still requires a mention;
- DMs keep the old default instant behavior;
- a verbose proof log is emitted:
  `discord accepted typing cue queued for channel ... mode=instant`.

This is a focused local backport of the key gate behavior from upstream
PR #76091 / issue #79104. It does not backport the whole PR's later
`replyTypingFeedback` lifecycle plumbing.

## Config Dependency

For unmentioned guild prompts, this patch requires one of:

```json
"agents": {
  "defaults": {
    "typingMode": "instant"
  }
}
```

or a session-level `typingMode:"instant"`.

Captain's local `openclaw.json` currently sets
`agents.defaults.typingMode = "instant"`.

## Apply

```bash
python3 scripts/apply-all.py --dry-run
python3 scripts/apply-all.py
```

Use the host's graceful gateway restart path after applying.

## Verify

After restart, send an unmentioned prompt in `#captain-verbose` and check:

```bash
rg -n "discord accepted typing cue|early typing cue failed" /tmp/openclaw/openclaw-$(date +%F).log
```

Expected:

- Discord shows the typing indicator shortly after the prompt is accepted.
- Logs include `discord accepted typing cue queued ... mode=instant`.
- Logs do not include `discord early typing cue failed ...`.

## Retire Criteria

Retire this patch when the installed `@openclaw/discord` runtime includes
upstream accepted typing feedback for guild channels, likely via PR #76091 or
an equivalent implementation. Do not retire based only on release notes; prove
the installed runtime path and an unmentioned guild-channel prompt.
