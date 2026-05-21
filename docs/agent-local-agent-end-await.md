# Agent local `agent_end` await patch

## Root cause

`openclaw agent --local` is a short-lived one-shot CLI path. The installed generated helper `runAgentHarnessAgentEndHook()` fired `hookRunner.runAgentEnd(...)` and immediately returned `void`, so terminal paths in `cli-runner-*.js` could finish and exit before observation-only `agent_end` providers settled.

That explains the local OTEL gap from `workspace/docs/findings/2026-05-21-agent-local-otel-hole-root-cause-attempt.md`: the agent turn could succeed while no `openclaw.agent.turn` span reached `workspace/logs/otel/traces.jsonl`.

## Local patch

`scripts/apply-agent-local-agent-end-await.py` patches the installed global OpenClaw dist bundle:

- `lifecycle-hook-helpers-*.js`: make `runAgentHarnessAgentEndHook()` async and await `runAgentEnd()` inside the existing catch/log boundary.
- `cli-runner-*.js`: await all seven local CLI terminal `agent_end` calls before the command resolves.

Gateway and other persistent runtime paths are intentionally left fire-and-forget; the upstream PR keeps that split in source.

## Proof

Installed bundle probe before patch:

```text
isPromise=false
hookCompletedBeforeRelease=false
resultResolvedBeforeRelease=false
hookCompletedAfterRelease=true
resultResolvedAfterRelease=false
```

Installed bundle probe after patch:

```text
isPromise=true
hookCompletedBeforeRelease=false
resultResolvedBeforeRelease=false
hookCompletedAfterRelease=true
resultResolvedAfterRelease=true
```

Upstream draft PR:

- `openclaw/openclaw#85007`
- branch: `Kaspre:fix/agent-local-agent-end-hooks`
- commit: `bed0a8d2d85bfc69e2910f220093488914e590bd`

## Retire when

Retire this patch after an installed OpenClaw release proves equivalent behavior in the actual global bundle: `runAgentHarnessAgentEndHook()` returns a promise and the local CLI terminal paths await it before one-shot command exit.
