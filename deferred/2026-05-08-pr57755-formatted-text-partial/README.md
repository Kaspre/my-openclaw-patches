# Deferred: PR #57755 — partial-send detection for sendFormattedText adapters

**Date:** 2026-05-08
**Source:** codex review during 2026-05-08 rebase of #57755 (feat/delivery-status-json)
**Status:** Deferred to follow-up PR (adjacent scope for #57755)
**Target PR:** Fresh adapter-contract PR; touches outbound deliver.ts + multi-chunk channel adapters

## Finding (verbatim from codex)

[P2] Report partial formatted-text sends incrementally —
`src/infra/outbound/deliver.ts:1497-1501`

When a channel uses `sendFormattedText` and sends multiple messages internally,
this only calls `recordDeliveryResults` after the whole adapter promise
resolves. For Signal, `sendFormattedSignalText` sends chunks in a loop;
if a later chunk throws after an earlier chunk was sent,
`onDeliveryResult` is never called, so `deliverAgentCommandResult` emits
`succeeded: false` instead of `"partial"` for strict `--json --deliver`
failures. That makes automation think nothing was delivered and can cause
duplicate retries of the already-sent chunk.

## Why deferred

Per `~/my-openclaw-patches/pr-context/57755.md`, #57755 owns:
- JSON envelope + emitJsonEnvelope + partial state only

The bug is in the **adapter contract**: `sendFormattedText`'s promise-based
return shape only surfaces results on resolve, not incrementally. Fixing it
requires either:
1. Threading an `onDeliveryResult` callback through `sendFormattedText`
   adapters so per-chunk results are recorded as they happen, or
2. Restructuring multi-chunk adapters (Signal, Telegram, etc.) to catch
   per-chunk errors and return a `{results, error}` shape.

Both are adapter-layer changes that affect every channel implementing
`sendFormattedText`. Pre-existing limitation: the same partial-loss
happens in main today (without this PR) for the `deliverySucceeded` boolean.
#57755 inherits the limitation; doesn't introduce it.

Per `feedback_codex-fractal-termination.md`: adjacent scope. Defer to keep
#57755 tight (envelope-shape only).

## Affected channels (need audit before fix)

Any adapter that loops sends inside `sendFormattedText`. Confirmed:
- `extensions/signal/src/channel.ts:226-240` — `sendFormattedSignalText`
  loops over `chunks`, calls `send` per chunk, throws on first failure.

Likely others: Telegram, Discord, Feishu — anywhere a single logical message
splits into multiple wire-level sends.

## Sketch of the fix (for the future PR)

Option A (callback through adapter):

```ts
// deliver.ts call site
recordDeliveryResults(
  await handler.sendFormattedText(
    payloadSummary.text,
    {
      ...applySendReplyToConsumption(sendOverrides),
      onChunkResult: recordDeliveryResult,
    },
  ),
);
```

Adapters call `onChunkResult(result)` per chunk before continuing the loop.
On throw, partial results have already been recorded.

Option B (return shape):

```ts
type FormattedTextResult =
  | DeliveryResult[]                      // all-success
  | { results: DeliveryResult[]; error: unknown }; // partial-then-error
```

Caller catches the error, records the partial results, then rethrows.

Option A is less disruptive to existing call sites; B is more explicit.

## Tests to add

1. Mock `sendFormattedText` adapter that records 2 results then throws on
   3rd → envelope reports `succeeded: "partial"`, not `false`.
2. Mock adapter that throws on 1st chunk (no results recorded) →
   `succeeded: false` preserved.
3. Mock adapter that completes all chunks → `succeeded: true`.

## Source references

- `src/infra/outbound/deliver.ts:1497-1501` — the call site
- `src/infra/outbound/deliver.ts` — `recordDeliveryResult` /
  `recordDeliveryResults` helpers (introduced by vincentkoc's a8fc4407e9)
- `extensions/signal/src/channel.ts:196-240` — example multi-chunk adapter

## Related

- PR #57755 (this PR) — surfaces but does not introduce the limitation
- PR #57843 (delivery-outcome-metadata) — also reads delivery results
- PR #53961 (delivery-status-tracking) — original `deliverySucceeded` boolean
