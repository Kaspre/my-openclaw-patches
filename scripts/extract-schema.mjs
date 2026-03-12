#!/usr/bin/env node
/**
 * Extract OpenClawSchema from daemon-cli.js and convert to JSON Schema.
 *
 * Strategy:
 *  1. Read lines 10266–14038 (schema definitions + dependencies)
 *  2. Read earlier utility functions needed by the schemas
 *  3. Strip .superRefine() calls (runtime validation, not structural)
 *  4. Stub out runtime dependencies (path, os, etc.)
 *  5. Eval the code with zod in scope
 *  6. Convert OpenClawSchema to JSON Schema via zod-to-json-schema
 */

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const OPENCLAW_DIR = "/home/captain/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw";
const SOURCE_FILE = `${OPENCLAW_DIR}/dist/daemon-cli.js`;
const OUTPUT_FILE = "/home/captain/.openclaw/config.schema.json";

// Read the full source
const lines = readFileSync(SOURCE_FILE, "utf8").split("\n");

// Helper to extract line range (1-indexed)
function getLines(start, end) {
  return lines.slice(start - 1, end).join("\n");
}

// --- Collect the code pieces ---

// 1. Utility functions from early in the file
const utilityCode = `
// --- Stubs for runtime dependencies ---
const path = {
  isAbsolute(v) { return v.startsWith('/') || /^[A-Za-z]:[\\\\/]/.test(v); },
  posix: {
    normalize(v) { return v.replace(/\\/+/g, '/').replace(/\\/\\.\\//g, '/').replace(/(?!^\\/)\\/$/,''); }
  }
};

// coerceSecretRef and normalizeSecretInputString stubs
function normalizeSecretInputString(value) {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}
function coerceSecretRef(value, defaults) {
  // Stub: just check if it looks like a ref object
  if (value && typeof value === 'object' && value.source && value.provider && value.id) return value;
  return null;
}
function hasConfiguredSecretInput(value, defaults) {
  if (normalizeSecretInputString(value)) return true;
  return coerceSecretRef(value, defaults) !== null;
}

// FILE_SECRET_REF_SEGMENT_PATTERN and isValidFileSecretRefId
const FILE_SECRET_REF_SEGMENT_PATTERN = /^(?:[^~]|~0|~1)*$/;
function isValidFileSecretRefId(value) {
  if (value === "value") return true;
  if (!value.startsWith("/")) return false;
  return value.slice(1).split("/").every((segment) => FILE_SECRET_REF_SEGMENT_PATTERN.test(segment));
}

// exec-safety
const SHELL_METACHARS = /[;&|${'`'}$<>]/;
const CONTROL_CHARS = /[\\r\\n]/;
const QUOTE_CHARS = /["']/;
const BARE_NAME_PATTERN = /^[A-Za-z0-9._+-]+$/;
function isLikelyPath(value) {
  if (value.startsWith(".") || value.startsWith("~")) return true;
  if (value.includes("/") || value.includes("\\\\")) return true;
  return /^[A-Za-z]:[\\\\/]/.test(value);
}
function isSafeExecutableValue(value) {
  if (!value) return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.includes("\\0")) return false;
  if (CONTROL_CHARS.test(trimmed)) return false;
  if (SHELL_METACHARS.test(trimmed)) return false;
  if (QUOTE_CHARS.test(trimmed)) return false;
  if (isLikelyPath(trimmed)) return true;
  if (trimmed.startsWith("-")) return false;
  return BARE_NAME_PATTERN.test(trimmed);
}

// streaming mode helpers (stubs - only used in superRefine which we strip)
function parseStreamingMode(v) { return typeof v === 'string' && ['off','partial','block','progress'].includes(v) ? v : undefined; }
function parseDiscordPreviewStreamMode(v) { return typeof v === 'string' && ['off','partial','block'].includes(v) ? v : undefined; }
function parseSlackLegacyDraftStreamMode(v) { return typeof v === 'string' ? v : undefined; }
function mapSlackLegacyDraftStreamModeToStreaming(v) { return v || 'partial'; }
function resolveTelegramPreviewStreamMode(params) { return 'partial'; }
function resolveDiscordPreviewStreamMode(params) { return 'off'; }
function resolveSlackStreamingMode(params) { return 'partial'; }
function resolveSlackNativeStreaming(params) { return true; }

// network mode (used in superRefine but also in SandboxDockerSchema superRefine)
function normalizeNetworkMode(network) {
  return network?.trim().toLowerCase() || undefined;
}
function getBlockedNetworkModeReason(params) {
  const normalized = normalizeNetworkMode(params.network);
  if (!normalized) return null;
  if (normalized === "host") return "host";
  if (normalized.startsWith("container:") && params.allowContainerNamespaceJoin !== true) return "container_namespace_join";
  return null;
}

// byte size / duration parsers
const UNIT_MULTIPLIERS = { b: 1, kb: 1024, k: 1024, mb: 1024**2, m: 1024**2, gb: 1024**3, g: 1024**3, tb: 1024**4, t: 1024**4 };
function parseByteSize(raw, opts) {
  const trimmed = String(raw ?? "").trim().toLowerCase();
  if (!trimmed) throw new Error("invalid byte size (empty)");
  const m = /^(\\d+(?:\\.\\d+)?)([a-z]+)?$/.exec(trimmed);
  if (!m) throw new Error("invalid byte size: " + raw);
  const value = Number(m[1]);
  const multiplier = UNIT_MULTIPLIERS[(m[2] ?? opts?.defaultUnit ?? "b").toLowerCase()];
  if (!multiplier) throw new Error("invalid byte size unit: " + raw);
  return Math.round(value * multiplier);
}
function isValidNonNegativeByteSizeString(value) {
  try {
    const bytes = parseByteSize(String(value).trim(), { defaultUnit: "b" });
    return bytes >= 0;
  } catch { return false; }
}

const DURATION_MULTIPLIERS = { ms: 1, s: 1e3, m: 6e4, h: 36e5, d: 864e5 };
function parseDurationMs(raw, opts) {
  const trimmed = String(raw ?? "").trim().toLowerCase();
  if (!trimmed) throw new Error("invalid duration (empty)");
  const single = /^(\\d+(?:\\.\\d+)?)(ms|s|m|h|d)?$/.exec(trimmed);
  if (single) {
    const value = Number(single[1]);
    const unit = single[2] ?? opts?.defaultUnit ?? "ms";
    return Math.round(value * DURATION_MULTIPLIERS[unit]);
  }
  throw new Error("invalid duration: " + raw);
}

// telegram custom commands, validation helpers — defined within schema code block
// forEachEnabledAccount, validateTelegramWebhookSecretRequirements,
// validateSlackSigningSecretRequirements — defined within schema code block (lines 11917-11964)
// Note: normalizeAllowFrom, requireOpenAllowFrom, requireAllowlistAllowFrom,
// addAllowAlsoAllowConflictIssue, normalizeTelegramStreamingConfig,
// normalizeDiscordStreamingConfig, normalizeSlackStreamingConfig,
// validateTelegramCustomCommands, isSafeScpRemoteHost, isValidInboundPathRootPattern,
// isSafeRelativeModulePath, enforceOpenDmPolicyAllowFromStar,
// enforceAllowlistDmPolicyAllowFrom, refineIrcAllowFromAndNickserv
// are all defined within the schema code block (10343-14038), no need to stub them.
`;

// 2. Extract the schema code block (lines 10343 to 14038)
let schemaCode = getLines(10343, 14038);

// 3. Strip ALL .superRefine(...) calls
// These are complex - they can span many lines with nested parens
// We need a balanced-paren stripper
function stripSuperRefine(code) {
  let result = "";
  let i = 0;
  while (i < code.length) {
    // Look for .superRefine(
    const marker = ".superRefine(";
    const idx = code.indexOf(marker, i);
    if (idx === -1) {
      result += code.slice(i);
      break;
    }
    // Include everything before .superRefine
    result += code.slice(i, idx);

    // Now skip from the opening paren to the matching close paren
    let parenStart = idx + marker.length - 1; // position of the '('
    let depth = 1;
    let j = parenStart + 1;
    while (j < code.length && depth > 0) {
      if (code[j] === "(") depth++;
      else if (code[j] === ")") depth--;
      // Handle string literals to avoid counting parens inside strings
      else if (code[j] === '"' || code[j] === "'" || code[j] === "`") {
        const quote = code[j];
        j++;
        while (j < code.length) {
          if (code[j] === "\\") { j++; } // skip escaped char
          else if (code[j] === quote) break;
          j++;
        }
      }
      j++;
    }
    i = j; // skip past the closing paren
  }
  return result;
}

schemaCode = stripSuperRefine(schemaCode);

// Also strip .transform() calls (same balanced-paren approach)
function stripMethod(code, methodName) {
  let result = "";
  let i = 0;
  const marker = "." + methodName + "(";
  while (i < code.length) {
    const idx = code.indexOf(marker, i);
    if (idx === -1) {
      result += code.slice(i);
      break;
    }
    result += code.slice(i, idx);
    let parenStart = idx + marker.length - 1;
    let depth = 1;
    let j = parenStart + 1;
    while (j < code.length && depth > 0) {
      if (code[j] === "(") depth++;
      else if (code[j] === ")") depth--;
      else if (code[j] === '"' || code[j] === "'" || code[j] === "`") {
        const quote = code[j];
        j++;
        while (j < code.length) {
          if (code[j] === "\\") { j++; }
          else if (code[j] === quote) break;
          j++;
        }
      }
      j++;
    }
    i = j;
  }
  return result;
}

schemaCode = stripMethod(schemaCode, "transform");

// 4. Remove the trailing `.strict()` that was before `.superRefine()` on the root OpenClawSchema
// The root schema ends with `}).strict()` now (since we stripped superRefine)
// We need to ensure it ends properly

// 5. Strip import.meta references (shouldn't be in this block but just in case)
schemaCode = schemaCode.replace(/import\.meta\.[^\s;,)]+/g, "undefined");

// 6. Handle the `sensitive` registry - z.registry() and .register(sensitive)
// .register(sensitive) returns the schema itself, so it's a no-op for structure purposes
// But z.registry() might not exist in the bundled zod. Let's handle it.

// Build the final evaluation code
const fullCode = `
${utilityCode}

// --- Schema definitions ---
${schemaCode}

// Export the main schema
return OpenClawSchema;
`;

// Load zod
const { createRequire } = await import("node:module");
const require = createRequire(import.meta.url);
const z = require(`${OPENCLAW_DIR}/node_modules/zod`);

// Check if z.registry exists
if (typeof z.registry !== "function") {
  console.log("Note: z.registry not found, will monkey-patch it");
}

// Create the function and execute it
try {
  const factory = new Function("z", fullCode);
  const OpenClawSchema = factory(z);

  console.log("Schema extracted successfully!");
  console.log("Type:", OpenClawSchema?._def?.typeName || OpenClawSchema?.constructor?.name);

  // Convert to JSON Schema using Zod v4's built-in toJSONSchema
  let jsonSchema;
  try {
    jsonSchema = OpenClawSchema.toJSONSchema({ target: "draft-2020-12" });
  } catch (e) {
    console.error("toJSONSchema failed:", e.message);
    // Try without options
    jsonSchema = OpenClawSchema.toJSONSchema({});
  }

  // Add metadata
  jsonSchema.title = "OpenClaw Configuration";
  jsonSchema.description = "JSON Schema for openclaw.json configuration file";

  const output = JSON.stringify(jsonSchema, null, 2);
  writeFileSync(OUTPUT_FILE, output);

  // Count top-level properties
  const topProps = jsonSchema.properties
    ? Object.keys(jsonSchema.properties)
    : [];

  console.log(`\nOutput: ${OUTPUT_FILE}`);
  console.log(`File size: ${(Buffer.byteLength(output) / 1024).toFixed(1)} KB`);
  console.log(`Top-level properties (${topProps.length}): ${topProps.join(", ")}`);
} catch (err) {
  console.error("Extraction failed:", err.message);
  // Try to find the error location
  if (err.message.includes("Unexpected")) {
    const match = err.message.match(/position (\d+)/);
    if (match) {
      const pos = parseInt(match[1]);
      const context = fullCode.slice(Math.max(0, pos - 100), pos + 100);
      console.error("\nCode around error position:\n", context);
    }
  }
  console.error("\nStack:", err.stack?.split("\n").slice(0, 5).join("\n"));
  process.exit(1);
}
