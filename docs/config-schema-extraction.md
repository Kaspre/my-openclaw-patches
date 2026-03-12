# Config Schema Extraction & VS Code Setup

## Overview

OpenClaw has an internal config schema (Zod definitions compiled into `daemon-cli.js`) but does not publish a JSON Schema file. We extract it locally and host it as a GitHub Gist so VS Code can provide autocompletion and validation when editing `openclaw.json`.

- Upstream issue requesting official schema: openclaw/openclaw#22278
- Gist: https://gist.github.com/Kaspre/f8857f5b650378ae900103f11154111e

## How the Schema Is Extracted

The extraction script (`scripts/extract-schema.mjs`) does the following:

1. Reads `daemon-cli.js` from the OpenClaw install (`~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/`)
2. Extracts lines ~10343-14038 containing all Zod schema definitions (sub-schemas + the root `OpenClawSchema`)
3. Strips `.superRefine()` calls (~15 instances) — these are runtime cross-field validators, not structural schema
4. Strips `.transform()` calls — runtime value transformers
5. Stubs runtime dependencies (`path`, `parseByteSize`, `parseDurationMs`, `isValidFileSecretRefId`, etc.)
6. Evals the cleaned code with `zod` in scope to reconstruct the `OpenClawSchema` object
7. Converts to JSON Schema using Zod v4's built-in `toJSONSchema()` (not the external `zod-to-json-schema` library, which is incompatible with Zod v4)
8. Writes output to `~/.openclaw/config.schema.json`

Result: ~705 KB JSON Schema with 36 top-level properties covering all config sections (agents, channels, tools, models, hooks, cron, etc.) with full enum values and nested structure.

## Running the Extraction

```bash
# Extract schema from current OpenClaw install
node ~/my-openclaw-patches/scripts/extract-schema.mjs
```

Output: `~/.openclaw/config.schema.json`

### After an OpenClaw Upgrade

The schema is version-specific. After upgrading OpenClaw:

1. Re-run the extraction:
   ```bash
   node ~/my-openclaw-patches/scripts/extract-schema.mjs
   ```
2. If it fails, the line numbers in the script may need updating for the new version's `daemon-cli.js`. Search for `const OpenClawSchema = z.object({` to find the new location.
3. Update the gist:
   ```bash
   gh gist edit f8857f5b650378ae900103f11154111e ~/.openclaw/config.schema.json
   ```

## VS Code Setup (Windows + WSL)

### Prerequisites
- VS Code installed on Windows
- WSL extension installed (`ms-vscode-remote.remote-wsl`)
- `code` command available in WSL terminal

### openclaw.json — $schema reference

The `$schema` field in `openclaw.json` points to the gist raw URL:

```json
{
  "$schema": "https://gist.githubusercontent.com/Kaspre/f8857f5b650378ae900103f11154111e/raw/config.schema.json",
  "meta": { ... },
  ...
}
```

Note: A relative path like `"./config.schema.json"` does NOT work from VS Code over WSL due to UNC path security restrictions.

### VS Code settings.json

Location: `C:\Users\admbm\AppData\Roaming\Code\User\settings.json`

Required settings:

```json
{
    "[json]": {
        "editor.suggest.showWords": false
    },
    "http.schemaDownload.allowedUrls": [
        "https://gist.githubusercontent.com/**"
    ],
    "json.schemas": [
        {
            "fileMatch": ["**/openclaw.json"],
            "url": "https://gist.githubusercontent.com/Kaspre/f8857f5b650378ae900103f11154111e/raw/config.schema.json"
        }
    ]
}
```

**What each setting does:**

- `editor.suggest.showWords: false` (scoped to JSON) — Disables word-based suggestions so only schema-valid values appear in dropdowns
- `http.schemaDownload.allowedUrls` — Whitelists gist URLs for schema downloads (VS Code blocks untrusted URLs by default in WSL workspaces)
- `json.schemas` — Associates `openclaw.json` files with our schema, providing a fallback in case the `$schema` field in the file itself doesn't load

### Workspace Trust

VS Code may prompt about workspace trust when opening WSL files. You need to either:
- Trust the workspace: `Ctrl+Shift+P` > "Manage Workspace Trust" > Trust
- Or add `"security.workspace.trust.untrustedFiles": "open"` to settings.json

### Opening the config file

From WSL terminal:
```bash
code ~/.openclaw/openclaw.json
```

### What You Get

- Enum dropdowns for fields like `logging.level` (silent/fatal/error/warn/info/debug/trace), `logging.consoleStyle` (pretty/compact/json), etc.
- Type validation (string vs number vs boolean vs array)
- Required/optional field hints
- Nested structure autocompletion for all 36 top-level sections

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Unable to load schema... UNC host access not allowed" | Use the gist URL instead of a relative path |
| "Downloading schemas is disabled in untrusted workspaces" | Trust the workspace or add `http.schemaDownload.allowedUrls` |
| "Location ... is untrusted" | Add `http.schemaDownload.allowedUrls` with the gist pattern |
| Dropdowns show word-based suggestions | Add `"editor.suggest.showWords": false` scoped to `[json]` |
| Schema out of date after upgrade | Re-run `extract-schema.mjs` and update the gist |
| Extraction script fails on new version | Line numbers shifted — search for `OpenClawSchema` in new `daemon-cli.js` and update the script |

## Files

- `scripts/extract-schema.mjs` — Extraction script
- `~/.openclaw/config.schema.json` — Extracted schema (local copy)
- Gist `f8857f5b650378ae900103f11154111e` — Published schema (VS Code reads from here)
