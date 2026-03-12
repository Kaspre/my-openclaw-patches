# Patch: memoryFlush skip-every-other compaction fix

**Bug:** GitHub #12590 (open, stale since Feb 28)
**Version:** Applied on v2026.3.8
**Date:** 2026-03-09
**Risk:** Low — single line removal, no behavioral change to compaction itself

## Problem

`memoryFlush` only fires on every other auto-compaction cycle (~50% miss rate). The dedup guard `hasAlreadyFlushedForCurrentCompaction` compares `memoryFlushCompactionCount === compactionCount`. After a flush, `runMemoryFlushIfNeeded` increments `compactionCount` (because compaction ran inside the flush), then reassigns `memoryFlushCompactionCount` to the new value. Both counters sync, causing the next cycle's flush to be skipped.

Pattern: flush → skip → flush → skip → ...

## File

`/home/captain/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/compact-D3emcZgv.js`

Backup: `compact-D3emcZgv.js.bak`

## Change

**Line 57061** — Remove the reassignment of `memoryFlushCompactionCount` to the post-increment value.

### Before (lines 57054-57062):
```js
if (memoryCompactionCompleted) {
    const nextCount = await incrementCompactionCount({
        sessionEntry: activeSessionEntry,
        sessionStore: activeSessionStore,
        sessionKey: params.sessionKey,
        storePath: params.storePath
    });
    if (typeof nextCount === "number") memoryFlushCompactionCount = nextCount;
}
```

### After:
```js
if (memoryCompactionCompleted) {
    await incrementCompactionCount({
        sessionEntry: activeSessionEntry,
        sessionStore: activeSessionStore,
        sessionKey: params.sessionKey,
        storePath: params.storePath
    });
    // FIX #12590: Do NOT reassign memoryFlushCompactionCount to post-increment value.
    // Keep it at pre-increment (N) so next cycle's compactionCount (N+1) won't match,
    // allowing flush to fire on every compaction instead of every other.
}
```

## Why This Is Safe

- `memoryFlushCompactionCount` stays at `N` (pre-increment value)
- After increment: `compactionCount = N+1`
- Next cycle check: `N !== N+1` → flush allowed (correct)
- Same-turn double-flush risk: mitigated by token threshold — compaction just cleared tokens, so the threshold won't be met again immediately

## Re-application After Upgrade

1. Find the bundled compact chunk: `ls ~/.nvm/versions/node/*/lib/node_modules/openclaw/dist/compact-*.js`
2. Search for: `if (typeof nextCount === "number") memoryFlushCompactionCount = nextCount`
3. Remove that line (and optionally `const nextCount =` → plain `await`)
4. Restart gateway

## Verification

After applying, trigger 3+ consecutive auto-compactions in a long session. Check gateway logs for memory flush activity on every cycle, not alternating. Compare against `.bak` file behavior if needed.
