# Agent local `agent_end` await patch

## Root cause

`openclaw agent --local` is a short-lived one-shot path. The installed generated helper `runAgentHarnessAgentEndHook()` fired `hookRunner.runAgentEnd(...)` and immediately returned `void`, so terminal paths in `cli-runner-*.js` and no-channel Codex app-server attempts could finish and exit before observation-only `agent_end` providers settled.

The hook runner also used unref'ed timeout timers for all void hooks. That is correct for persistent fire-and-forget gateway/app-server callers, but it is not enough for an awaited one-shot CLI drain point: a never-settling hook with no ref'ed handles can let Node exit before the timeout fires.

That explains the local OTEL gap from `workspace/docs/findings/2026-05-21-agent-local-otel-hole-root-cause-attempt.md`: the agent turn could succeed while no `openclaw.agent.turn` span reached `workspace/logs/otel/traces.jsonl`.

## Local patch

`scripts/apply-agent-local-agent-end-await.py` patches the installed global OpenClaw dist bundle:

- `hook-runner-global-*.js`: let awaited `agent_end` callers request ref'ed timeout timers while preserving unref'ed defaults.
- `lifecycle-hook-helpers-*.js`: keep public `runAgentHarnessAgentEndHook()` fire-and-forget and add an internal awaited helper for one-shot CLI.
- `cli-runner-*.js`: await all seven local CLI terminal `agent_end` calls through the awaited helper before the command resolves.
- `run-attempt-*.js`: await `agent_end` for no-channel Codex app-server attempts, which are also used by `openclaw agent --local`, while preserving fire-and-forget channel-backed gateway attempts.

Channel-backed gateway and other persistent runtime paths are intentionally left fire-and-forget; the upstream PR keeps that split in source.

## Proof

Installed bundle probe before patch:

```text
isPromise=false
hookCompletedBeforeRelease=false
resultResolvedBeforeRelease=false
hookCompletedAfterRelease=true
resultResolvedAfterRelease=false
```

Installed bundle probe after first patch:

```text
isPromise=true
hookCompletedBeforeRelease=false
resultResolvedBeforeRelease=false
hookCompletedAfterRelease=true
resultResolvedAfterRelease=true
```

The current patch additionally matches upstream source shape: public SDK helper remains fire-and-forget, local CLI plus no-channel Codex app-server attempts use the awaited helper, and awaited hook timeouts stay ref'ed so the process remains alive until hook settlement or timeout.

Upstream PR:

- `openclaw/openclaw#85007`
- branch: `Kaspre:fix/agent-local-agent-end-hooks`
- commit: `322078452759e9cbe1a00b1a3b6c1000b978c193`

## Retire when

Retire this patch after an installed OpenClaw release proves equivalent behavior in the actual global bundle: local CLI terminal paths and no-channel Codex app-server attempts use the awaited `agent_end` helper before one-shot command exit, public/channel-backed persistent helper paths remain fire-and-forget, and awaited hook timeouts are ref'ed.
