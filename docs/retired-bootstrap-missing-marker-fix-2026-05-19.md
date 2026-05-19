# Retired Patch: bootstrap-missing-marker-fix

Date: 2026-05-19
Retired from: `scripts/apply-all.py`
Patch script kept for archaeology: `scripts/apply-bootstrap-missing-marker-fix.py`
Original upstream reference: `openclaw/openclaw#42542`
Installed version checked: `OpenClaw 2026.5.18-beta.1 (4eebca1)`

## Original Behavior

Completed workspaces had two bad runtime outcomes around root `BOOTSTRAP.md`:

- If `BOOTSTRAP.md` was absent, the agent context could include a missing-file entry for `BOOTSTRAP.md`.
- If `BOOTSTRAP.md` was present as a placeholder or stale file, the agent context included it and could trigger the hardcoded `Reminder: commit your changes in this workspace after edits.` workspace note.

The local patch changed the compiled `workspace-*.js` bundle so `loadWorkspaceBootstrapFiles()` omitted missing root `BOOTSTRAP.md` once workspace setup was complete.

## Current Upstream State

`v2026.5.18-beta.1` fixes the LLM-facing runtime path at the resolver layer, not by changing the raw loader:

- `src/agents/bootstrap-files.ts` contains `filterCompletedWorkspaceBootstrapFile(...)`.
- `resolveBootstrapFilesForRun(...)` checks workspace setup completion and filters root `BOOTSTRAP.md` before context-mode filtering and again after bootstrap hooks.
- `resolveBootstrapContextForRun(...)` builds injected context from the filtered resolver output.
- Regression tests in `src/agents/bootstrap-files.test.ts` cover completed workspaces, hook re-add attempts, home-relative paths, unreadable state fallback, and nested `BOOTSTRAP.md` preservation.

The raw `loadWorkspaceBootstrapFiles()` function in upstream source can still return root `BOOTSTRAP.md` entries. That is no longer the runtime contract for agent context because installed runtime callers use `resolveBootstrapFilesForRun()` / `resolveBootstrapContextForRun()`.

## Installed Bundle Proof

Installed package:

```text
OpenClaw 2026.5.18-beta.1 (4eebca1)
```

Relevant installed caller search:

```text
agent attempts, compaction, doctor bootstrap-size, and view-system-prompt call resolveBootstrapContextForRun() or resolveBootstrapFilesForRun().
bootstrap-cache calls loadWorkspaceBootstrapFiles(), but its cached output is passed through resolveBootstrapFilesForRun() before reaching agent context.
```

Small installed-bundle proof results:

```json
{"case":"completed+missing-root-bootstrap","loaderHasBootstrap":false,"resolverHasBootstrap":false}
{"case":"completed+present-root-bootstrap","loaderHasBootstrap":true,"resolverHasBootstrap":false}
```

Interpretation:

- Missing root `BOOTSTRAP.md` is not exposed by the installed resolver.
- Present/stale root `BOOTSTRAP.md` is not exposed by the installed resolver.
- The runtime path that builds agent context no longer reintroduces the original nag or commit-reminder behavior.

## Retirement Decision

Retire `bootstrap-missing-marker-fix` from `scripts/apply-all.py`.

Expected impact after retirement:

- Normal agent runtime context stays fixed.
- `/context`, system prompt reports, compaction context, and doctor bootstrap-size continue to use the upstream resolver-level filter.
- A direct developer/test call to raw `loadWorkspaceBootstrapFiles()` may again show upstream raw-loader behavior, but that is not the LLM-facing runtime path.

If the original user-visible behavior returns after a future upgrade, inspect `resolveBootstrapFilesForRun()` and its call sites first. Do not re-enable the raw-loader patch unless a real runtime path bypasses the resolver.
