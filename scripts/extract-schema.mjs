#!/usr/bin/env node
/**
 * Extract OpenClawSchema from OpenClaw GitHub source and convert to JSON Schema.
 *
 * Usage:
 *   node extract-schema.mjs [--output /path/to/schema.json] [--diff /path/to/old-schema.json] [--ref main]
 *
 * Environment variables:
 *   SCHEMA_OUTPUT   — Output path (default: ~/.openclaw/workspace/docs/config-schema.json)
 *   OPENCLAW_DIR    — OpenClaw install dir for Zod dependency (default: auto-detect)
 *
 * Strategy:
 *   1. Download zod-schema source files + utility deps from GitHub
 *   2. Generate a harness that imports OpenClawSchema and calls toJSONSchema()
 *   3. Run via tsx (TypeScript execute) using OpenClaw's bundled Zod
 *   4. Optionally diff against a previous schema
 *
 * Requirements:
 *   - gh CLI (authenticated)
 *   - tsx (npx tsx)
 *   - OpenClaw installed (for Zod dependency)
 *
 * Caveats:
 *   - Depends on OpenClaw's internal source structure. If files are renamed or
 *     imports change, the download list may need updating.
 *   - .superRefine() calls contain runtime validation that can't be represented
 *     in JSON Schema — they are stripped automatically.
 *   - Tested against v2026.3.8 and v2026.3.13. YMMV on future versions.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { execSync } from "node:child_process";
import { resolve, join, dirname } from "node:path";
import { homedir } from "node:os";
import { tmpdir } from "node:os";

// ── CLI args ───────────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = { ref: "main" };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--output" && args[i + 1]) opts.output = args[++i];
    else if (args[i] === "--diff" && args[i + 1]) opts.diff = args[++i];
    else if (args[i] === "--ref" && args[i + 1]) opts.ref = args[++i];
    else if (args[i] === "--openclaw-dir" && args[i + 1]) opts.openclawDir = args[++i];
    else if (args[i] === "--keep-tmp") opts.keepTmp = true;
    else if (args[i] === "--help" || args[i] === "-h") {
      console.log(`Usage: node extract-schema.mjs [options]
  --output FILE       Output path (default: ~/.openclaw/workspace/docs/config-schema.json)
  --diff OLD_SCHEMA   Compare against previous schema and show changes
  --ref REF           Git ref to fetch from (default: main). Use a tag like v2026.3.13.
  --openclaw-dir DIR  OpenClaw install dir (for Zod). Auto-detected if omitted.
  --keep-tmp          Don't delete temp directory after extraction
  --help              Show this help`);
      process.exit(0);
    }
  }
  return opts;
}

const opts = parseArgs();
const OUTPUT_FILE = opts.output || process.env.SCHEMA_OUTPUT || join(homedir(), ".openclaw/workspace/docs/config-schema.json");

// ── Locate OpenClaw (for Zod) ──────────────────────────────────────────────

function findOpenClawDir() {
  if (opts.openclawDir) return opts.openclawDir;
  if (process.env.OPENCLAW_DIR) return process.env.OPENCLAW_DIR;
  try {
    const bin = execSync("which openclaw", { encoding: "utf8" }).trim();
    const resolved = execSync(`readlink -f "${bin}"`, { encoding: "utf8" }).trim();
    let dir = dirname(resolved);
    for (let i = 0; i < 5; i++) {
      if (existsSync(join(dir, "package.json"))) {
        const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
        if (pkg.name === "openclaw") return dir;
      }
      dir = dirname(dir);
    }
  } catch { /* ignore */ }
  console.error("ERROR: Could not find OpenClaw. Use --openclaw-dir or set OPENCLAW_DIR.");
  process.exit(1);
}

const OPENCLAW_DIR = findOpenClawDir();
let ocVersion = "unknown";
try {
  ocVersion = JSON.parse(readFileSync(join(OPENCLAW_DIR, "package.json"), "utf8")).version || "unknown";
} catch { /* ignore */ }

console.log(`OpenClaw dir: ${OPENCLAW_DIR}`);
console.log(`OpenClaw version: ${ocVersion}`);
console.log(`Git ref: ${opts.ref}`);

// ── Files to download ──────────────────────────────────────────────────────

// Schema files (src/config/)
const SCHEMA_FILES = [
  "zod-schema.ts",
  "zod-schema.core.ts",
  "zod-schema.agents.ts",
  "zod-schema.agent-runtime.ts",
  "zod-schema.agent-defaults.ts",
  "zod-schema.agent-model.ts",
  "zod-schema.allowdeny.ts",
  "zod-schema.approvals.ts",
  "zod-schema.channels.ts",
  "zod-schema.hooks.ts",
  "zod-schema.installs.ts",
  "zod-schema.providers.ts",
  "zod-schema.providers-core.ts",
  "zod-schema.providers-whatsapp.ts",
  "zod-schema.secret-input-validation.ts",
  "zod-schema.sensitive.ts",
  "zod-schema.session.ts",
];

// Utility dependencies (need stubs or actual files)
const UTIL_FILES = [
  { src: "src/config/types.secrets.ts", dest: "config/types.secrets.ts" },
  { src: "src/config/types.models.ts", dest: "config/types.models.ts" },
  { src: "src/config/byte-size.ts", dest: "config/byte-size.ts" },
  { src: "src/config/discord-preview-streaming.ts", dest: "config/discord-preview-streaming.ts" },
  { src: "src/config/telegram-custom-commands.ts", dest: "config/telegram-custom-commands.ts" },
  { src: "src/cli/parse-bytes.ts", dest: "cli/parse-bytes.ts" },
  { src: "src/cli/parse-duration.ts", dest: "cli/parse-duration.ts" },
  { src: "src/infra/exec-safety.ts", dest: "infra/exec-safety.ts" },
  { src: "src/infra/scp-host.ts", dest: "infra/scp-host.ts" },
  { src: "src/secrets/ref-contract.ts", dest: "secrets/ref-contract.ts" },
  { src: "src/agents/sandbox/network-mode.ts", dest: "agents/sandbox/network-mode.ts" },
  { src: "src/media/inbound-path-policy.ts", dest: "media/inbound-path-policy.ts" },
];

// ── Download from GitHub ───────────────────────────────────────────────────

const WORK_DIR = join(tmpdir(), `openclaw-schema-extract-${Date.now()}`);
mkdirSync(join(WORK_DIR, "config"), { recursive: true });
mkdirSync(join(WORK_DIR, "cli"), { recursive: true });
mkdirSync(join(WORK_DIR, "infra"), { recursive: true });
mkdirSync(join(WORK_DIR, "agents/sandbox"), { recursive: true });
mkdirSync(join(WORK_DIR, "media"), { recursive: true });
mkdirSync(join(WORK_DIR, "secrets"), { recursive: true });

console.log(`\nWork dir: ${WORK_DIR}`);
console.log("Downloading source files...");

function downloadFile(repoPath, localPath) {
  const fullLocal = join(WORK_DIR, localPath);
  try {
    const content = execSync(
      `gh api "repos/openclaw/openclaw/contents/${repoPath}?ref=${opts.ref}" -H "Accept: application/vnd.github.raw+json"`,
      { encoding: "utf8", maxBuffer: 1024 * 1024 }
    );
    writeFileSync(fullLocal, content);
    return true;
  } catch (e) {
    console.warn(`  WARN: Could not download ${repoPath}: ${e.message?.split("\n")[0]}`);
    return false;
  }
}

// Download schema files
let downloaded = 0;
for (const f of SCHEMA_FILES) {
  if (downloadFile(`src/config/${f}`, `config/${f}`)) downloaded++;
}
console.log(`  Schema files: ${downloaded}/${SCHEMA_FILES.length}`);

// Download utility files
let utilDownloaded = 0;
for (const { src, dest } of UTIL_FILES) {
  if (downloadFile(src, dest)) utilDownloaded++;
}
console.log(`  Utility files: ${utilDownloaded}/${UTIL_FILES.length}`);

// ── Post-process: strip superRefine and transform ──────────────────────────

function stripBalancedCall(code, methodName) {
  let result = "";
  let i = 0;
  const marker = "." + methodName + "(";
  while (i < code.length) {
    const idx = code.indexOf(marker, i);
    if (idx === -1) { result += code.slice(i); break; }
    result += code.slice(i, idx);
    let depth = 1;
    let j = idx + marker.length;
    while (j < code.length && depth > 0) {
      const ch = code[j];
      if (ch === "(") depth++;
      else if (ch === ")") depth--;
      else if (ch === '"' || ch === "'" || ch === "`") {
        const q = ch; j++;
        while (j < code.length) {
          if (code[j] === "\\") j++;
          else if (code[j] === q) break;
          j++;
        }
      }
      j++;
    }
    i = j;
  }
  return result;
}

// Strip superRefine and transform from all schema files
for (const f of SCHEMA_FILES) {
  const fp = join(WORK_DIR, "config", f);
  if (!existsSync(fp)) continue;
  let code = readFileSync(fp, "utf8");
  code = stripBalancedCall(code, "superRefine");
  code = stripBalancedCall(code, "transform");
  writeFileSync(fp, code);
}

// ── Create stub files for missing dependencies ─────────────────────────────

// Some utility files may import things we don't have. Create minimal stubs
// for any that failed to download.
for (const { dest } of UTIL_FILES) {
  const fp = join(WORK_DIR, dest);
  if (!existsSync(fp)) {
    // Write a stub that exports empty objects/functions
    console.warn(`  Creating stub for missing: ${dest}`);
    writeFileSync(fp, `// Auto-generated stub\nexport default {};\n`);
  }
}

// ── Fix import paths ───────────────────────────────────────────────────────

// The schema files use .js extensions in imports but we have .ts files
// tsx handles this automatically, but we need to ensure paths resolve correctly

// ── Generate harness ───────────────────────────────────────────────────────

const harnessCode = `
import { OpenClawSchema } from "./config/zod-schema.js";
import { writeFileSync } from "node:fs";

const OUTPUT = ${JSON.stringify(OUTPUT_FILE)};
const OC_VERSION = ${JSON.stringify(ocVersion)};

try {
  let jsonSchema;
  try {
    jsonSchema = OpenClawSchema.toJSONSchema({ target: "draft-2020-12" });
  } catch {
    jsonSchema = OpenClawSchema.toJSONSchema({});
  }

  jsonSchema.title = "OpenClaw Configuration";
  jsonSchema.description = "JSON Schema for openclaw.json — extracted from OpenClaw v" + OC_VERSION;

  const output = JSON.stringify(jsonSchema, null, 2);
  writeFileSync(OUTPUT, output);

  const topProps = jsonSchema.properties ? Object.keys(jsonSchema.properties) : [];
  const sizeKB = (Buffer.byteLength(output) / 1024).toFixed(1);

  console.log("Schema extracted successfully!");
  console.log("Output: " + OUTPUT);
  console.log("File size: " + sizeKB + " KB");
  console.log("Top-level properties (" + topProps.length + "): " + topProps.join(", "));

  // Output property count as machine-readable for diffing
  console.log("__PROPS_COUNT__:" + topProps.length);
} catch (err) {
  console.error("Extraction failed:", err.message);
  if (err.stack) console.error(err.stack.split("\\n").slice(0, 8).join("\\n"));
  process.exit(1);
}
`;

writeFileSync(join(WORK_DIR, "harness.ts"), harnessCode);

// ── Run extraction via tsx ─────────────────────────────────────────────────

console.log("\nRunning extraction via tsx...");

// tsx needs to find zod — use NODE_PATH to point to OpenClaw's node_modules
const zodPath = join(OPENCLAW_DIR, "node_modules");
const env = { ...process.env, NODE_PATH: zodPath };

try {
  const result = execSync(`npx tsx "${join(WORK_DIR, "harness.ts")}"`, {
    encoding: "utf8",
    env,
    cwd: WORK_DIR,
    maxBuffer: 10 * 1024 * 1024,
    timeout: 60000,
  });
  console.log(result);
} catch (err) {
  console.error("\nExtraction FAILED.");
  if (err.stderr) console.error(err.stderr.slice(0, 2000));
  if (err.stdout) console.log(err.stdout.slice(0, 1000));
  console.error("\nThis may mean the source structure has changed.");
  console.error("Check the downloaded files in:", WORK_DIR);
  if (!opts.keepTmp) {
    console.error("(Re-run with --keep-tmp to preserve temp files for debugging)");
  }
  if (!opts.keepTmp) rmSync(WORK_DIR, { recursive: true, force: true });
  process.exit(1);
}

// ── Diff against previous schema ───────────────────────────────────────────

if (opts.diff && existsSync(opts.diff) && existsSync(OUTPUT_FILE)) {
  console.log(`\n--- Schema diff vs ${opts.diff} ---`);
  try {
    const oldSchema = JSON.parse(readFileSync(opts.diff, "utf8"));
    const newSchema = JSON.parse(readFileSync(OUTPUT_FILE, "utf8"));

    const oldProps = new Set(Object.keys(oldSchema.properties || {}));
    const newProps = new Set(Object.keys(newSchema.properties || {}));
    const added = [...newProps].filter(p => !oldProps.has(p));
    const removed = [...oldProps].filter(p => !newProps.has(p));

    if (added.length) console.log(`  ADDED top-level: ${added.join(", ")}`);
    if (removed.length) console.log(`  REMOVED top-level: ${removed.join(", ")}`);
    if (!added.length && !removed.length) console.log("  No top-level property changes.");

    // Deep path count
    function countPaths(obj, prefix = "") {
      const paths = new Set();
      if (!obj || typeof obj !== "object") return paths;
      if (obj.properties) {
        for (const [k, v] of Object.entries(obj.properties)) {
          const p = prefix ? `${prefix}.${k}` : k;
          paths.add(p);
          for (const n of countPaths(v, p)) paths.add(n);
        }
      }
      if (obj.items) for (const n of countPaths(obj.items, `${prefix}[]`)) paths.add(n);
      if (obj.additionalProperties && typeof obj.additionalProperties === "object") {
        for (const n of countPaths(obj.additionalProperties, `${prefix}[*]`)) paths.add(n);
      }
      return paths;
    }

    const oldPaths = countPaths(oldSchema);
    const newPaths = countPaths(newSchema);
    const addedPaths = [...newPaths].filter(p => !oldPaths.has(p));
    const removedPaths = [...oldPaths].filter(p => !newPaths.has(p));
    const delta = newPaths.size - oldPaths.size;

    console.log(`  Total schema paths: ${oldPaths.size} → ${newPaths.size} (${delta >= 0 ? "+" : ""}${delta})`);

    if (addedPaths.length > 0 && addedPaths.length <= 30) {
      for (const p of addedPaths) console.log(`    + ${p}`);
    } else if (addedPaths.length > 30) {
      console.log(`    ${addedPaths.length} paths added`);
    }
    if (removedPaths.length > 0 && removedPaths.length <= 30) {
      for (const p of removedPaths) console.log(`    - ${p}`);
    } else if (removedPaths.length > 30) {
      console.log(`    ${removedPaths.length} paths removed`);
    }
  } catch (e) {
    console.warn(`  Diff failed: ${e.message}`);
  }
}

// ── Cleanup ────────────────────────────────────────────────────────────────

if (!opts.keepTmp) {
  rmSync(WORK_DIR, { recursive: true, force: true });
} else {
  console.log(`\nTemp files preserved at: ${WORK_DIR}`);
}

console.log("\nDone.");
