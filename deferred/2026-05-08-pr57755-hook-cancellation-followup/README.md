# Deferred: PR #57755 — terminal hook cancellation should clear pendingFinalDelivery

**Date:** 2026-05-08
**Source:** codex review during 2026-05-08 rebase of #57755 (feat/delivery-status-json)
**Status:** Deferred to follow-up PR (out-of-scope for #57755)
**Target PR:** Either #57843 follow-up, or a fresh hook-cancellation PR

## Finding (verbatim from codex)

[P2] Preserve terminal hook cancellation as completed delivery —
`src/agents/command/delivery.ts:454`

When a `message_sending` hook cancels delivery or blanks a text-only payload,
`deliverOutboundPayloads` resolves to `[]` without calling `onError` (this is
documented as terminal cancellation). The success check
`results.length > 0 ? (hadPartialFailure ? "partial" : true) : false`
makes those intentional no-send outcomes return `deliverySucceeded: false`,
so `src/agents/agent-command.ts` leaves `pendingFinalDelivery` set for main
sessions and heartbeat/recovery can keep replaying a reply that a plugin
deliberately suppressed.

Codex's recommendation: keep `deliveryStatus.succeeded = false` if needed,
but the backward-compat `deliverySucceeded` / pending-marker clearing needs
to treat zero-result/no-error terminal delivery as completed.

## Why deferred

Per `~/my-openclaw-patches/pr-context/57755.md`:

> **NOT in scope:**
> - Hook-cancellation false-positive suppression — same as #53961's follow-up scope.

This bug also exists in #53961 today (the success check there is the same
`results.length > 0` boolean before #57755 narrows it to 3-state). It is
**not introduced by #57755** — #57755 inherits and re-shapes the existing
bug. Fixing it is the job of a hook-cancellation-aware follow-up,
soft-blocked behind #57843 per PR-context doc.

Per `feedback_codex-fractal-termination.md`: this is adjacent scope. Defer
to keep #57755's scope tight (JSON envelope shape only).

## Sketch of the fix (for the future PR)

Distinguish "delivery returned [] with no onError" from "delivery returned
[] and onError fired" in `delivery.ts`:

```ts
const terminalNoSend = results.length === 0 && !hadPartialFailure;
deliverySucceeded = results.length > 0
  ? (hadPartialFailure ? "partial" : true)
  : terminalNoSend
    ? true   // hook cancelled / payload blanked — count as completed
    : false; // delivery actually failed
```

But: needs to be careful that the "no delivery channel resolved" /
"no delivery target resolved" / "channel resolved to internal" cases at
:488-:497 still log as not-completed. Those paths skip the `try` block
entirely so they don't hit this branch — but verify in tests.

## Tests to add

1. Hook cancels delivery (`deliverOutboundPayloads` returns `[]`, no
   `onError` fired) → `deliverySucceeded: true`, `pendingFinalDelivery`
   cleared.
2. Hook blanks text-only payload → same.
3. Genuine delivery failure (`onError` fired, results empty) →
   `deliverySucceeded: false` preserved.

## Source references

- `src/agents/command/delivery.ts:454` — the success check at issue
- `src/agents/agent-command.ts` — reads `deliveryResult?.deliverySucceeded === true`
  to clear `pendingFinalDelivery`
- `src/infra/outbound/deliver.ts` — `deliverOutboundPayloads` documents
  terminal cancellation behaviour

## Related

- PR #57843 (delivery-outcome-metadata) — likely owner of the follow-up
- PR #53961 (delivery-status-tracking) — also carries the bug
