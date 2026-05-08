# Deferred from PR #57843 — codex round 2 P2 (2026-05-08 rebase)

During the 2026-05-08 rebase of #57843 onto upstream/main, codex flagged
two P2 findings. One was fixed in-place; one is deferred here as adjacent
scope to prevent fractal scope growth (per the codex termination criterion
documented in `~/.claude/projects/-home-captain--openclaw/memory/feedback_codex-fractal-termination.md`).

## Fixed in #57843 rebase commit (ea79009920 → final HEAD)

1. **Run commit hooks for `sentBeforeError` prefix before treating the
   delivery as terminal** — both the live-send wrapper in `deliver.ts`
   and the recovery loop in `delivery-queue-recovery.ts`. Without this,
   the WAL ack would silently drop afterCommit-style message-lifecycle
   side effects for messages that were already delivered.
2. **Move partial-send entry to `failed/` if ack fails** during recovery
   replay (non-ENOENT). Without this, a transient ack failure left the
   entry in `pending/` and the next drain would replay and duplicate
   the prefix.

## Deferred — adjacent scope

### Adapter-internal multi-chunk partial sends (Signal `sendFormattedSignalText`)

**Codex finding (#57843 round 2, 2026-05-08):**

> The patch only wraps failures when the outer `results` array already
> contains sent messages, but the `sendFormattedText` branch above appends
> results only after the adapter promise resolves. Signal's
> `sendFormattedSignalText` sends multiple chunks internally and returns
> the array at the end, so if a later chunk throws, this rethrows the raw
> error with no `sentBeforeError`; the new cron/restart/queue guards then
> retry the whole delivery and can duplicate chunks that were already sent.

**Why deferred:** fixing this requires changing the adapter contract for
`sendFormattedText`-style adapters so they can surface intra-call partial
results when they throw. That touches at minimum:

- `src/channels/signal/outbound/sendFormattedSignalText.ts` (and any
  other `sendFormattedText` adapter that batches chunks internally)
- `src/infra/outbound/deliver.ts` to reshape how `results` accumulates
  across the formatted-text branch — likely an `onChunkDelivered` callback
  threaded into the adapter, or a new `PartialSendError` thrown by the
  adapter that wraps both the underlying error and the chunks already
  delivered.
- Tests for each adapter that gains the new contract.

**Scope decision:** #57843's stated scope is "DeliveryOutcome type +
DeliveryError class + WAL ack-on-DeliveryError." Adapter-internal
partial-send tracking is a sibling concern — it interacts with the same
DeliveryError plumbing but lives entirely below the deliver.ts boundary.
Bundling it into this rebase would push the diff well past the size that
killed the 2026-04-21 round of this PR (200 → 530 lines across 8 codex
rounds, ending in revert).

**Follow-up PR title (suggested):** `fix(delivery): surface partial sends
from formatted-text adapters as DeliveryError`

**Acceptance criteria for follow-up:**

- Signal multi-chunk send with chunk-N throw → caller sees `DeliveryError`
  with chunks 1..N-1 in `sentBeforeError`.
- Existing `sendFormattedSignalText` callers that don't read
  `sentBeforeError` still see the error and a non-empty results array if
  they want it.
- Same audit applied to any other `sendFormattedText`-shaped adapter
  (Telegram, Discord, WhatsApp where applicable).
- Tests: per-adapter chunk-N-throws unit test asserting `DeliveryError`
  shape.

**Not blocking #57843:** without this, a Signal multi-chunk partial-send
that crosses the new DeliveryError boundary still rethrows raw — which is
exactly the pre-#57843 behavior. So this PR doesn't make Signal worse;
it just doesn't make Signal as good as Matrix/Discord/Slack where the
chunk loop is in deliver.ts itself.
