# Codex PreToolUse Patch Stack

Status: active positive-delivery stack, fail-closed hardening pending promotion.

## Purpose

This stack keeps predicate-claw / OC Firewall enforcement visible for Codex native
shell/code execution, including Code Mode `exec`.

The local install should treat this as a long-lived downstream patch stack. Do not
assume upstream will merge or release these fixes soon.

## Required Pieces

1. `codex-codemode-pretooluse-binary`
   - Codex-side Bug A fix.
   - Artifact: `artifacts/codex-0.135-codemode-pretooluse-fwfix`
   - Source: `Kaspre/codex:rebase/code-mode-pretooluse-0.135`
   - Reason: upstream Codex 0.135 does not emit `PreToolUse` for outer Code Mode
     `exec` unless this binary patch is installed.

2. `openclaw-codex-native-pretool-delivery`
   - OpenClaw-side positive-delivery fix.
   - Artifact: `artifacts/openclaw-2026.6.1-codex-native-pretool-delivery-90994-8e22ba40f0-dist.tar.gz`
   - Source: `openclaw/openclaw#90994`, head `8e22ba40f04816459e90ac34c441becbad21215d`
   - Reason: policy-active Codex turns must route native shell/code execution
     through the hookable unified-exec path, keep native hook relay stdout
     protocol-clean, and refresh stale native-hook config fingerprints.

3. Fail-closed hardening
   - Status: not yet promoted to this local patch rotation.
   - Candidate: draft `openclaw/openclaw#90805` or a narrower equivalent.
   - Reason: this does not make `PreToolUse` work. It makes a future delivery
     regression fail in the expected OpenClaw way: block instead of silently
     allowing native execution around policy.

## Apply

```bash
python3 ~/my-openclaw-patches/scripts/apply-all.py --dry-run
python3 ~/my-openclaw-patches/scripts/apply-all.py
```

Then graceful-restart the gateway. Do not use a generic gateway restart on this
host; follow the local OpenClaw graceful restart rule.

## Post-Install Gates

Static gate:

```bash
python3 ~/my-openclaw-patches/scripts/check-codex-pretooluse-stack.py
```

This fails if the patched Codex binary is not installed or if the #90994 delivery
markers are missing from any detected OpenClaw core or `@openclaw/codex` plugin
dist root. Once the fail-closed patch is promoted, run:

```bash
python3 ~/my-openclaw-patches/scripts/check-codex-pretooluse-stack.py --require-fail-closed
```

Executable Code Mode proof:

```bash
~/.local/node-current/bin/node ~/my-openclaw-patches/scripts/prove-codex-code-mode-pretooluse.mjs
```

This starts the installed Codex app-server against a local mock Responses API,
emits an outer Code Mode `exec` call, routes it through the installed OpenClaw
native hook relay, and expects:

- `before_tool_call` runs exactly once.
- The hook tool name is `code_mode_exec`.
- Codex reports `Tool call blocked by PreToolUse hook`.
- The marker file written by the blocked command does not exist.

It does not call a remote model and should be safe as a post-install canary.

Live firewall sentinel proof:

Run the real sentinel read path after an upgrade or patch rebuild, preferably
after local host load is low or on Crabbox/GCP if broader validation is needed.
Expected result: the output must not contain `FWSENTINELREAD`, and it must contain
the deny signal for `deny-fw-regression-sentinel-read`.

## Version Drift Rules

- If Codex moves past `0.135.0`, rebuild the Codex binary from the fork rebased to
  the new tag, then update the artifact path, hash, and expected version in
  `apply-codex-codemode-pretooluse-binary.py`.
- If OpenClaw moves past `2026.6.1`, rebuild or rework the #90994 delivery
  artifact against the new OpenClaw version. Do not apply the 2026.6.1 dist
  artifact to a newer runtime.
- After any OpenClaw or Codex update, run the static gate and executable Code
  Mode proof before calling the install safe.
