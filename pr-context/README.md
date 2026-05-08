# PR context — local notes for in-flight openclaw/openclaw PRs

**Purpose:** prevent scope bleed between related PRs by documenting each PR's
intended scope, its hard/soft boundaries, and its relationships to other PRs.
Read the relevant `<NNNNN>.md` BEFORE rebasing or amending a PR — especially
when conflicts touch surface that another PR also modifies.

## Convention

- One file per PR: `<PR-number>.md`.
- Each file states: stated purpose, scope (what's IN), boundary (what's NOT),
  related PRs, dependency direction, sequencing notes, and any "this is what
  I learned not to do" notes from prior sessions.
- Keep files terse but specific. If a fact would change the conflict
  resolution choice, write it down.

## When to update

- After a session where a PR's scope changed (slim, expand, redirect).
- After a maintainer comment that clarifies what the PR should/shouldn't be.
- When discovering a relationship to another PR (dependency, supersedes,
  alternative implementation).
- When merging or closing a PR — move file to `closed/<PR-number>.md`.

## When to read

- Start of every PR-touching task — read the relevant file before reading
  the diff.
- When resolving a merge conflict — read the file for any PR that owns
  surface in the conflicted hunk.
- When deciding what to include in a rebase / fold-in / amend.

## Index

- [`57843.md`](57843.md) — fix(delivery): disambiguate hook cancellations from delivery failures
- [`53961.md`](53961.md) — fix(delivery): track and log silent delivery failures
- [`57755.md`](57755.md) — feat(delivery): surface deliveryStatus in --json output
