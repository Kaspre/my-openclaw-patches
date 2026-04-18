# web-search-activate-on-empty

**Applies to:** OpenClaw 2026.4.14 (confirmed); **NOT needed on v2026.4.15** (fixed upstream — see retirement note)
**Target file:** `dist/web-search-providers.runtime-*.js`
**Upstream:** open issue [#68249](https://github.com/openclaw/openclaw/issues/68249) (Brave reporter says bug persists on v4.15, but our v4.15 Exa config works unpatched — the upstream fix appears partial)

## Problem

Native `web_search` tool never registers on v4.14 despite correct config (`tools.web.search.provider: "exa"`, `plugins.entries.exa.enabled: true`, `EXA_API_KEY` valid). Affects all bundled **provider-only** search plugins: Exa, Brave, Perplexity, Tavily. Firecrawl is unaffected because it registers its own plugin tools (`firecrawl_search`, `firecrawl_scrape`) alongside its provider.

Root cause trace:
1. `createWebSearchTool()` calls `resolveWebSearchDefinition()` which calls `resolvePluginWebSearchProviders()`.
2. That delegates to `resolvePluginWebProviders()` in the patched file.
3. `resolvePluginWebProviders` calls `resolveCompatibleRuntimePluginRegistry(loadOptions)` which returns the **startup-time active registry**.
4. The startup registry has **zero `webSearchProviders`** because bundled provider-only plugins never had their `register()` executed — they have no standalone agent tool to trigger activation via the tool-registration path.
5. `mapRegistryProviders` returns `[]`, `resolveWebSearchDefinition` returns `null`, `createWebSearchTool` returns `null`, `web_search` is never registered.

Full findings: `workspace/docs/findings/2026-04-17-native-web-search-activation.md`.

## Fix

Two small semantic changes inside `resolvePluginWebProviders`:

1. Treat an empty compatible registry as "fall through" instead of a valid result.
2. On the fall-through, force a fresh activating load with `activate: true, cache: false` so bundled provider-only plugins actually run `register()`.

The "force activate" vector uses `activate: true` (the option name `buildPluginRuntimeLoadOptionsFromValues` reads to derive `shouldActivate`). Setting `shouldActivate: true` directly on loadOptions is silently ignored because `resolvePluginLoadCacheContext` computes `shouldActivate` from `options.activate !== false`. Confirmed empirically during v1 of this patch.

## Prerequisites alongside the patch

- `tools.web.search.enabled: true`
- `tools.web.search.provider: "exa"` (or any other supported provider)
- `plugins.allow` includes the target provider plugin id
- `plugins.entries.<provider>.enabled: true` (with optional `config.webSearch` — empty `{}` is fine when API key comes from env)
- Provider's API key in gateway env (e.g. `EXA_API_KEY` in `~/.openclaw/.env`)
- OC Firewall allows `tool.<provider>_search` AND `http.fetch` on `**` for the calling agent (web_search passes the search query string as the http.fetch resource — not a URL — so URL-glob allow rules won't match)

## Risks

- **Extra plugin load on cold `web_search` calls.** Result is cached via the existing `memoizeSnapshot` mechanism, so cost is one-time per config snapshot per process lifetime.
- **Zero impact when the active registry has providers** — `resolved.length > 0` short-circuits before the fallback.
- **No impact on Firecrawl users** — their active registry already has providers; short-circuit path runs.
- **Upgrade breakage risk** — the pattern match is specific to the current Rollup output. If OC refactors this file in a future release, the patch will skip with "pattern_not_found" (non-destructive).

## Verification

1. Apply: `python3 ~/my-openclaw-patches/scripts/apply-web-search-activate-on-empty.py`
2. Restart gateway: `bash ~/.openclaw/workspace/scripts/graceful-restart.sh`
3. Dispatch an agent and ask for its tool list — `web_search` should appear alongside `firecrawl_search`.
4. Smoke-test the call: ask the agent to invoke `web_search` with a real query; response should include `provider: "exa"` and non-empty `results` array.

## Retirement

**Effectively retired on v2026.4.15.** Upstream added a new plugin-load pathway (`resolveBundledPluginCompatibleLoadValues` in `activation-context-*.js`) that plumbs `applyPluginAutoEnable` + `applyPluginCompatibilityOverrides` before the registry is loaded. Result: `resolvePluginWebSearchProviders` returns 12 providers on v4.15 unpatched (vs 0 on v4.14). Verified via standalone probe + Einstein dispatch returning `provider: "exa"` results.

**We're keeping the patch script** on disk and registered in `apply-all.py` as a rollback hedge — if a future OC version regresses on this seam we can re-apply without re-deriving the fix. The `apply-all.py --dry-run` still matches the OLD pattern in v4.15 dist (meaning the file we patch is structurally unchanged), so the script would apply cleanly but do nothing useful. That's why we don't run it.

**Remove permanently when:** either (a) confidence in upstream stability across ≥2 further releases, or (b) the target file signature moves and the patch pattern stops matching, whichever comes first.
