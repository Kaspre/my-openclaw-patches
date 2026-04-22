# Deferred scope from PR #57843 — 2026-04-21

Four fixes that got built during one #57843 iteration session but are being
pulled out of the PR to keep it minimum-scope. Each one is legitimate work that
belongs somewhere — just not in #57843. Park them here so they can be revived
when the right home is ready.

## Quick index

| Patch | Concern | Natural home | Priority |
|-------|---------|--------------|----------|
| `01-sentinel-retry-guard.patch` | `server-restart-sentinel.ts` retry loop duplicates prefix when `deliverOutboundPayloads` throws `DeliveryError` (chunked restart notice, bestEffort:false, mid-stream failure) | **New PR** (`fix/sentinel-delivery-error-retry-guard` or similar) — orthogonal pre-existing bug | Low-medium |
| `02-wal-ack-on-deliveryerror.patch` | Outer queue wrapper in `deliver.ts` calls `failDelivery()` on `DeliveryError`; entry stays pending, `recoverPendingDeliveries()` replays on restart → user sees duplicate prefix | **#53961** (`fix/delivery-status-tracking`) — fits its "silent delivery failure" lens; WAL replay-dup is exactly the class of bug #53961 tracks | Medium |
| `03-gateway-response-shape.patch` | `gateway/server-methods/send.ts` returns `ok: true` without `messageId` when `allCancelledByHook`; callers (`message.ts:322`, `commands/message-format.ts:304-314`) expect `{ messageId: string }` | **#57755** (`feat/delivery-status-json`) — its scope is surfacing delivery status through to JSON output, includes naturally | Medium |
| `04-helper-extraction-and-guard.patch` | Pull `isDeliveryError` into a shared module (`src/infra/outbound/delivery-error-guard.ts`) so second consumer (sentinel patch 01) can import it without each site re-implementing the name+shape check | Apply when **01 lands** — premature extraction otherwise | Low |

`full-branch-diff.patch` is the whole `upstream/main..preserve/57843-pre-revert-2026-04-21` diff for reference.

## Preserved git state

- **Tag:** `preserve/57843-pre-revert-2026-04-21` (in `~/openclaw-source`)
- **Branch:** `preserve/57843-scope-creep-97bb8a58` (same repo, same SHA)
- **Commit SHA:** `97bb8a58415841c6a86d8d80cc063d6e5f36914b`

If the patch files drift or get lost, the full over-grown state is reconstructable from either ref.

## How the patches were generated

```bash
cd ~/openclaw-source
git diff upstream/main preserve/57843-pre-revert-2026-04-21 -- <paths...> > <patch>
```

So each patch contains the **full delta** for its listed paths, against the `upstream/main` at the time of the session (2026-04-21, `upstream/main` at `67719b3c28`). That means each patch file includes:

1. The core #57766 changes that live in those files (DeliveryOutcome return type, DeliveryError class, etc.) — this portion will already be in place on the downstream branch.
2. The scope-creep hunks we actually want to revive.

When applying, `git apply --3way` should let the already-present hunks merge cleanly and highlight just the scope-creep additions. Cherry-picking hunks by hand is also fine — the scope-creep sections are identifiable by their inline comments (search for `#57766` in the added lines, or the concern-specific comment keywords noted below).

## Per-patch application notes

### 01-sentinel-retry-guard.patch

Adds to `src/gateway/server-restart-sentinel.ts`:
- Import `isDeliveryError` (assumes patch 04 is also applied, OR inline the name-check locally).
- In the retry loop's `catch (err)` block: before the existing retry logic, branch on `isDeliveryError(err) && err.sentBeforeError.length > 0` → log, `ackDelivery(queueId)`, return.

Adds to `src/gateway/server-restart-sentinel.test.ts`:
- `"does not retry a partial DeliveryError and acks the queued notice"` test.
- (Also contains mock-shape updates on lines 41, 159, 211 — those are **required for rebase** and will already be in the #57843 minimum-scope commit. Ignore those hunks on apply.)

**Target commit message:**
```
fix(gateway): do not retry DeliveryError in restart-sentinel retry loop

scheduleRestartSentinelWake's retry loop caught any error and retried
up to 45 times. For chunked restart notices where chunk 1 landed before
chunk 2 failed, each retry would duplicate the already-sent prefix.

Branch on isDeliveryError(err) && err.sentBeforeError.length > 0 →
log, ackDelivery, return. Same rationale as the cron delivery retry
guard introduced in #57766.
```

### 02-wal-ack-on-deliveryerror.patch (→ #53961)

Adds to `src/infra/outbound/deliver.ts`:
- In `deliverOutboundPayloads` outer catch (queue wrapper), change `if (isAbortError(err))` → `if (isAbortError(err) || err instanceof DeliveryError)`. Adds a comment explaining that partial-send entries must not be left for `recoverPendingDeliveries()` to replay.

Adds to `src/infra/outbound/deliver.test.ts`:
- `"acks the queue entry on partial DeliveryError to avoid recovery replay"` test.
- (Also contains the WhatsApp→Matrix conversion — those are **required for rebase** and will already be in #57843 minimum-scope. Ignore those hunks on apply.)

Uses `instanceof DeliveryError` (not the name-check helper) because the code lives in `deliver.ts` where the class is defined locally — no lazy-load concern.

**Target PR:** #53961 is about tracking silent delivery failures. WAL replay-duplicating-prefix-on-restart IS a silent failure: user gets duplicate, no log ties cause to effect. Natural fit.

After #57843 merges, when rebasing #53961:
1. Apply this hunk to `deliver.ts`.
2. Apply the test.
3. Update #53961's commit message to reference the WAL coverage.

### 03-gateway-response-shape.patch (→ #57755)

Updates to `src/infra/outbound/message.ts`:
- `callMessageGateway<{ messageId: string }>` → `callMessageGateway<{ messageId?: string; runId?: string; channel?: string; cancelledByHook?: boolean }>` (acknowledging that gateway can now return a hook-cancelled variant).
- `MessageSendResult.result` type union gains the hook-cancelled shape.

Does **not** update downstream renderers (`commands/message-format.ts:304-314`) — that's the substantive change #57755 should own. The type fix here is the enabling piece; the actual JSON-output semantics (how "cancelled_by_hook" appears alongside "delivered" vs "failed") is #57755's scope.

**Target PR:** #57755 is "surface deliveryStatus in --json output" — it should naturally surface `cancelled_by_hook` as a third status, which requires this type plumbing.

### 04-helper-extraction-and-guard.patch

New file `src/infra/outbound/delivery-error-guard.ts`:
- Exports `isDeliveryError(err): err is DeliveryError` — name + `Array.isArray(sentBeforeError)` check.
- Uses `import type { DeliveryError }` so no runtime dependency on `deliver.js`.

Modifies `src/cron/isolated-agent/delivery-dispatch.ts`:
- Removes inline `isDeliveryError` helper (which is what #57843 minimum-scope will ship with).
- Imports from the new shared module.

Apply this patch **when a second consumer appears** (the sentinel patch from 01 is the obvious trigger). Until then, keeping `isDeliveryError` inline in `delivery-dispatch.ts` is simpler and has zero cost.

## Minimum-scope #57843 (what's kept in the PR)

Not in these patches — it's what the PR keeps:

- `DeliveryOutcome` return type, `DeliveryError` class, five callers updated.
- Inline `isDeliveryError` helper in `delivery-dispatch.ts` using name + shape check.
- Type-only `import type { DeliveryError }` in `delivery-dispatch.ts`.
- Cron dispatch retry guard at the two existing sites.
- Rebase-forced test fixes: WhatsApp→Matrix in `deliver.test.ts`, mock-shape in `double-announce.test.ts:612`, sentinel test mocks (lines 41/159/211), route-reply mock, message.test.ts mock.
- Narrow mock in `double-announce.test.ts` with inline `DeliveryError` stub class (avoids broad `importOriginal()` per AGENTS.md:134, also cuts test runtime ~10s→3s).

## See also

- `~/.openclaw/workspace/docs/findings/2026-04-21-pr-57843-scope-reconsideration.md` — first-principles analysis of how the session over-grew the PR and why these four fixes are better off elsewhere.
- KNOWLEDGE-INDEX.md "Upstream PRs" section for current state of the delivery PR cluster (#57843, #57755, #53961, plus #51421, #50818, #52422).
