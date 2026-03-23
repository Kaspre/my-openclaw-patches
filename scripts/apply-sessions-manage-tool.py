#!/usr/bin/env python3
"""
Patch: Add sessions_manage tool with semantic compaction support
Issue: openclaw/openclaw#10981
PR: openclaw/openclaw#52422

Injects the sessions_manage tool into the bundled dist files so agents can
call sessions.compact, sessions.compactSemantic, and sessions.reset gateway
RPC methods programmatically.

Changes:
  - Chunk files: createSessionsManageTool function + registrations
  - Gateway-cli files: sessions.compactSemantic RPC handler + allowed method
  - Auth-profiles file: post-run deferred semantic compaction handler

Usage:
  python3 apply-sessions-manage-tool.py [--dry-run] [--dist-dir PATH] [--restore]
"""

import argparse
import glob
import json
import os
import shutil
import sys

DIST_DIR_DEFAULT = os.path.expanduser(
    "~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist"
)

BACKUP_SUFFIX = ".bak-sessions-manage"

# === The tool function to inject ===
# This is a compiled version of sessions-manage-tool.ts that uses the same
# helpers as sessions_send (resolveSessionToolContext, resolveSessionReference,
# resolveVisibleSessionReference, createSessionVisibilityGuard, etc.)
SESSIONS_MANAGE_TOOL_FUNCTION = '''
function createSessionsManageTool(opts) {
\tconst ACTIONS = ["compact", "compactSemantic", "reset"];
\treturn {
\t\tlabel: "Session Manage",
\t\tname: "sessions_manage",
\t\tdescription: "Compact, semantically compact, or reset a session by key. " +
\t\t\t"Actions: 'compact' (JSONL truncation — fast, lossy fallback), " +
\t\t\t"'compactSemantic' (LLM-based summarization — preferred, deferred to post-run), " +
\t\t\t"'reset' (start fresh session). " +
\t\t\t"SELF-COMPACT PROCEDURE: " +
\t\t\t"(1) Write checkpoint to memory/YYYY-MM-DD.md with '## Self-Compact Checkpoint (HH:MM)' heading — include current task, findings, next steps, and concrete file/line references. " +
\t\t\t"(2) Write memory/.compaction-pending marker: {\\\"timestamp\\\":\\\"<ISO>\\\",\\\"sessionKey\\\":\\\"<YOUR-SESSION-KEY>\\\",\\\"messageCount\\\":-1,\\\"tokenCount\\\":\\\"self-compact\\\",\\\"toolCallsSinceCheckpoint\\\":0,\\\"trigger\\\":\\\"self-compact\\\"}. " +
\t\t\t"(3) Append breadcrumb to memory/.breadcrumbs-YYYY-MM-DD.log. " +
\t\t\t"(4) Call this tool with YOUR OWN sessionKey (use the key from your session context, NOT hardcoded 'main'), action 'compactSemantic'. Optionally pass instructions to guide the summarization. " +
\t\t\t"(5) End your turn — the semantic compaction runs after your session becomes idle, then Memory Guardian auto-injects the checkpoint on the next turn via .compaction-pending detection.",
\t\tparameters: {
\t\t\ttype: "object",
\t\t\tproperties: {
\t\t\t\tsessionKey: { type: "string", description: "Target session key. Use YOUR OWN session key for self-compaction." },
\t\t\t\taction: { type: "string", enum: ["compact", "compactSemantic", "reset"] },
\t\t\t\tinstructions: { type: "string", description: "Optional guidance for semantic compaction focus (e.g., 'Focus on decisions and open questions')" }
\t\t\t},
\t\t\trequired: ["sessionKey", "action"]
\t\t},
\t\texecute: async (_toolCallId, args) => {
\t\t\tconst params = args;
\t\t\tconst sessionKeyParam = typeof params?.sessionKey === "string" ? params.sessionKey.trim() : "";
\t\t\tconst action = typeof params?.action === "string" ? params.action.trim() : "";
\t\t\tconst instructions = typeof params?.instructions === "string" ? params.instructions.trim() : "";
\t\t\tif (!sessionKeyParam) return jsonResult({ status: "error", error: "sessionKey is required" });
\t\t\tif (!ACTIONS.includes(action)) return jsonResult({ status: "error", error: "action must be 'compact', 'compactSemantic', or 'reset'" });
\t\t\tconst { cfg, mainKey, alias, effectiveRequesterKey, restrictToSpawned } = resolveSessionToolContext(opts);
\t\t\tconst resolvedSession = await resolveSessionReference({
\t\t\t\tsessionKey: sessionKeyParam,
\t\t\t\talias,
\t\t\t\tmainKey,
\t\t\t\trequesterInternalKey: effectiveRequesterKey,
\t\t\t\trestrictToSpawned
\t\t\t});
\t\t\tif (!resolvedSession.ok) return jsonResult({ status: resolvedSession.status, error: resolvedSession.error });
\t\t\tconst visibleSession = await resolveVisibleSessionReference({
\t\t\t\tresolvedSession,
\t\t\t\trequesterSessionKey: effectiveRequesterKey,
\t\t\t\trestrictToSpawned,
\t\t\t\tvisibilitySessionKey: sessionKeyParam
\t\t\t});
\t\t\tif (!visibleSession.ok) return jsonResult({ status: visibleSession.status, error: visibleSession.error, sessionKey: visibleSession.displayKey });
\t\t\tconst resolvedKey = visibleSession.key;
\t\t\tconst displayKey = visibleSession.displayKey;
\t\t\tconst a2aPolicy = createAgentToAgentPolicy(cfg);
\t\t\tconst sessionVisibility = resolveEffectiveSessionToolsVisibility({ cfg, sandboxed: opts?.sandboxed === true });
\t\t\tconst visibilityGuard = await createSessionVisibilityGuard({
\t\t\t\taction: "send",
\t\t\t\trequesterSessionKey: effectiveRequesterKey,
\t\t\t\tvisibility: sessionVisibility,
\t\t\t\ta2aPolicy
\t\t\t});
\t\t\tconst access = visibilityGuard.check(resolvedKey);
\t\t\tif (!access.allowed) return jsonResult({ status: access.status, error: access.error, sessionKey: displayKey });
\t\t\tif (action === "compact") {
\t\t\t\ttry {
\t\t\t\t\tconst result = await callGateway({ method: "sessions.compact", params: { key: resolvedKey } });
\t\t\t\t\treturn jsonResult({ status: "ok", action, sessionKey: displayKey, compacted: result?.compacted === true, ...(typeof result?.reason === "string" ? { reason: result.reason } : {}), ...(typeof result?.kept === "number" ? { kept: result.kept } : {}) });
\t\t\t\t} catch (err) {
\t\t\t\t\treturn jsonResult({ status: "error", action, sessionKey: displayKey, error: err instanceof Error ? err.message : String(err) });
\t\t\t\t}
\t\t\t}
\t\t\tif (action === "compactSemantic") {
\t\t\t\ttry {
\t\t\t\t\tconst result = await callGateway({ method: "sessions.compactSemantic", params: { key: resolvedKey, instructions: instructions || void 0 } });
\t\t\t\t\treturn jsonResult({ status: "ok", action, sessionKey: displayKey, scheduled: result?.status === "scheduled", ...(typeof result?.error === "string" ? { error: result.error } : {}) });
\t\t\t\t} catch (err) {
\t\t\t\t\treturn jsonResult({ status: "error", action, sessionKey: displayKey, error: err instanceof Error ? err.message : String(err) });
\t\t\t\t}
\t\t\t}
\t\t\ttry {
\t\t\t\tconst result = await callGateway({ method: "sessions.reset", params: { key: resolvedKey } });
\t\t\t\treturn jsonResult({ status: "ok", action, sessionKey: displayKey, resetOk: result?.ok === true, ...(typeof result?.key === "string" ? { newKey: result.key } : {}) });
\t\t\t} catch (err) {
\t\t\t\treturn jsonResult({ status: "error", action, sessionKey: displayKey, error: err instanceof Error ? err.message : String(err) });
\t\t\t}
\t\t}
\t};
}
'''

# === Replacements for chunk files (contain createSessionsSendTool) ===
# Each tuple: (description, old_string, new_string)
# These are applied in order to each matching chunk file.

REPLACEMENTS = [
    # 1. Tool function: inject after createSessionsSendTool function definition
    (
        "inject createSessionsManageTool function",
        "function createSessionsSendTool(opts) {",
        SESSIONS_MANAGE_TOOL_FUNCTION.strip() + "\nfunction createSessionsSendTool(opts) {",
    ),

    # 2. Tool registration: add after createSessionsSendTool call
    (
        "register createSessionsManageTool in tool array",
        "\t\tcreateSessionsSendTool({\n"
        "\t\t\tagentSessionKey: options?.agentSessionKey,\n"
        "\t\t\tagentChannel: options?.agentChannel,\n"
        "\t\t\tsandboxed: options?.sandboxed,\n"
        "\t\t\tconfig: options?.config\n"
        "\t\t}),",

        "\t\tcreateSessionsSendTool({\n"
        "\t\t\tagentSessionKey: options?.agentSessionKey,\n"
        "\t\t\tagentChannel: options?.agentChannel,\n"
        "\t\t\tsandboxed: options?.sandboxed,\n"
        "\t\t\tconfig: options?.config\n"
        "\t\t}),\n"
        "\t\tcreateSessionsManageTool({\n"
        "\t\t\tagentSessionKey: options?.agentSessionKey,\n"
        "\t\t\tsandboxed: options?.sandboxed,\n"
        "\t\t\tconfig: options?.config\n"
        "\t\t}),",
    ),

    # 3. Sandbox constants: add sessions_manage to DEFAULT_TOOL_ALLOW
    (
        "add sessions_manage to sandbox constants",
        '\t"sessions_send",\n\t"sessions_spawn",\n\t"sessions_yield",',
        '\t"sessions_manage",\n\t"sessions_send",\n\t"sessions_spawn",\n\t"sessions_yield",',
    ),

    # 4. Tool catalog: add sessions_manage entry after sessions_send
    (
        "add sessions_manage to tool catalog",
        '\tid: "sessions_send",\n'
        '\t\tlabel: "sessions_send",\n'
        '\t\tdescription: "Send to session",\n'
        '\t\tsectionId: "sessions",\n'
        '\t\tprofiles: ["coding", "messaging"],\n'
        '\t\tincludeInOpenClawGroup: true\n'
        '\t},',

        '\tid: "sessions_send",\n'
        '\t\tlabel: "sessions_send",\n'
        '\t\tdescription: "Send to session",\n'
        '\t\tsectionId: "sessions",\n'
        '\t\tprofiles: ["coding", "messaging"],\n'
        '\t\tincludeInOpenClawGroup: true\n'
        '\t},\n'
        '\t{\n'
        '\t\tid: "sessions_manage",\n'
        '\t\tlabel: "sessions_manage",\n'
        '\t\tdescription: "Compact/reset session",\n'
        '\t\tsectionId: "sessions",\n'
        '\t\tprofiles: ["coding", "messaging"],\n'
        '\t\tincludeInOpenClawGroup: true\n'
        '\t},',
    ),

    # 5. System prompt descriptions: add sessions_manage after sessions_send
    (
        "add sessions_manage to system prompt descriptions",
        '\t\tsessions_send: "Send a message to another session/sub-agent",',
        '\t\tsessions_send: "Send a message to another session/sub-agent",\n'
        '\t\tsessions_manage: "Compact or reset a session (programmatic /compact and /new)",',
    ),

    # 6. System prompt tool order: add sessions_manage after sessions_send
    (
        "add sessions_manage to system prompt tool order",
        '\t\t"sessions_send",\n\t\t"subagents",',
        '\t\t"sessions_send",\n\t\t"sessions_manage",\n\t\t"subagents",',
    ),

    # 7. System prompt help text: add sessions_manage line
    (
        "add sessions_manage to help text",
        '\t\t\t"- sessions_send: send to another session",',
        '\t\t\t"- sessions_send: send to another session",\n'
        '\t\t\t"- sessions_manage: compact or reset a session",',
    ),

    # 8. Subagent deny list (SUBAGENT_TOOL_DENY_ALWAYS): add sessions_manage
    (
        "add sessions_manage to subagent deny list",
        '\t"sessions_send"\n];',
        '\t"sessions_send",\n\t"sessions_manage"\n];',
    ),

    # 9. MUTATING_TOOL_NAMES: add sessions_manage
    (
        "add sessions_manage to MUTATING_TOOL_NAMES",
        '\t"sessions_send",\n\t"cron",',
        '\t"sessions_send",\n\t"sessions_manage",\n\t"cron",',
    ),

    # 10. isMutatingToolCall switch: add sessions_manage case
    (
        "add sessions_manage to isMutatingToolCall switch",
        '\t\tcase "sessions_send": return true;',
        '\t\tcase "sessions_send":\n\t\tcase "sessions_manage": return true;',
    ),

    # 11. METHOD_SCOPE_GROUPS: add sessions.compactSemantic to ADMIN_SCOPE group
    #     Required for both client-side scope request (callGatewayLeastPrivilege)
    #     and server-side authorization (authorizeOperatorScopesForMethod)
    (
        "add sessions.compactSemantic to METHOD_SCOPE_GROUPS ADMIN_SCOPE",
        '\t\t"sessions.compact",',
        '\t\t"sessions.compact",\n\t\t"sessions.compactSemantic",',
    ),
]

# Tool display overrides - separate because the format is different (JSON)
DISPLAY_REPLACEMENT = (
    "add sessions_manage to tool display overrides",
    '\t\t"sessions_send": {\n'
    '\t\t\t"emoji": "📨",\n'
    '\t\t\t"title": "Session Send",\n'
    '\t\t\t"detailKeys": [\n'
    '\t\t\t\t"label",\n'
    '\t\t\t\t"sessionKey",\n'
    '\t\t\t\t"agentId",\n'
    '\t\t\t\t"timeoutSeconds"\n'
    '\t\t\t]\n'
    '\t\t},',

    '\t\t"sessions_send": {\n'
    '\t\t\t"emoji": "📨",\n'
    '\t\t\t"title": "Session Send",\n'
    '\t\t\t"detailKeys": [\n'
    '\t\t\t\t"label",\n'
    '\t\t\t\t"sessionKey",\n'
    '\t\t\t\t"agentId",\n'
    '\t\t\t\t"timeoutSeconds"\n'
    '\t\t\t]\n'
    '\t\t},\n'
    '\t\t"sessions_manage": {\n'
    '\t\t\t"emoji": "🧰",\n'
    '\t\t\t"title": "Session Manage",\n'
    '\t\t\t"detailKeys": [\n'
    '\t\t\t\t"sessionKey",\n'
    '\t\t\t\t"action"\n'
    '\t\t\t]\n'
    '\t\t},',
)

# Also patch dangerous-tools files (smaller, separate chunks)
DANGEROUS_TOOLS_REPLACEMENTS = [
    (
        "add sessions_manage to DEFAULT_GATEWAY_HTTP_TOOL_DENY",
        '\t"sessions_send",',
        '\t"sessions_send",\n\t"sessions_manage",',
    ),
    (
        "add sessions_manage to DANGEROUS_ACP_TOOLS",
        '\t"sessions_send",\n\t"gateway",',
        '\t"sessions_send",\n\t"sessions_manage",\n\t"gateway",',
    ),
]

# === Gateway-cli replacements ===
# Add sessions.compactSemantic RPC handler and allowed method

GATEWAY_COMPACT_SEMANTIC_HANDLER = '''\t,
\t"sessions.compactSemantic": async ({ params, respond }) => {
\t\tconst p = params ?? {};
\t\tconst key = typeof p.key === "string" ? p.key.trim() : "";
\t\tif (!key) { respond(false, void 0, { code: -32602, message: "missing required param: key" }); return; }
\t\tconst instructions = typeof p.instructions === "string" ? p.instructions.trim() : "";
\t\tlet cfg, target, storePath;
\t\ttry {
\t\t\tconst resolved = resolveGatewaySessionTargetFromKey(key);
\t\t\tcfg = resolved.cfg; target = resolved.target; storePath = resolved.storePath;
\t\t} catch (err) {
\t\t\trespond(false, void 0, { code: -32602, message: "invalid session key: " + String(err?.message || err) }); return;
\t\t}
\t\tconst workspaceDirRaw = resolveAgentWorkspaceDir(cfg, target.agentId);
\t\tconst workspaceDir = resolveUserPath(workspaceDirRaw);
\t\tconst store = loadSessionStore(storePath);
\t\tconst entry = target.storeKeys.map((k) => store[k]).find(Boolean);
\t\tconst sessionId = entry?.sessionId || "";
\t\tconst sessionFile = entry?.sessionFile || "";
\t\tconst flagDir = path.join(workspaceDir, "memory");
\t\tconst flagPath = path.join(flagDir, ".pending-semantic-compaction");
\t\ttry {
\t\t\tfs.mkdirSync(flagDir, { recursive: true });
\t\t\tfs.writeFileSync(flagPath, JSON.stringify({
\t\t\t\ttimestamp: new Date().toISOString(),
\t\t\t\tsessionKey: target.canonicalKey,
\t\t\t\tsessionId,
\t\t\t\tsessionFile,
\t\t\t\tinstructions
\t\t\t}), "utf-8");
\t\t} catch (err) {
\t\t\trespond(true, { ok: false, error: "Failed to write compaction flag: " + String(err) }, void 0); return;
\t\t}
\t\trespond(true, { ok: true, key: target.canonicalKey, status: "scheduled" }, void 0);
\t}'''

GATEWAY_REPLACEMENTS = [
    # 1. Add sessions.compactSemantic to allowed methods list
    (
        "add sessions.compactSemantic to allowed methods",
        '\t"sessions.compact",',
        '\t"sessions.compact",\n\t"sessions.compactSemantic",',
    ),

    # 2. Inject sessions.compactSemantic handler after sessions.compact handler
    #    The compact handler ends with:  \t}\n};\n//#endregion
    #    We insert the new handler before the closing };
    (
        "inject sessions.compactSemantic RPC handler",
        # Match the last few lines of sessions.compact handler + closing brace
        '\t\trespond(true, {\n'
        '\t\t\tok: true,\n'
        '\t\t\tkey: target.canonicalKey,\n'
        '\t\t\tcompacted: true,\n'
        '\t\t\tarchived,\n'
        '\t\t\tkept: keptLines.length\n'
        '\t\t}, void 0);\n'
        '\t}\n'
        '};\n'
        '//#endregion',

        '\t\trespond(true, {\n'
        '\t\t\tok: true,\n'
        '\t\t\tkey: target.canonicalKey,\n'
        '\t\t\tcompacted: true,\n'
        '\t\t\tarchived,\n'
        '\t\t\tkept: keptLines.length\n'
        '\t\t}, void 0);\n'
        '\t}' + GATEWAY_COMPACT_SEMANTIC_HANDLER + '\n'
        '};\n'
        '//#endregion',
    ),
]

# === Auth-profiles replacement: post-run deferred compaction handler ===
# Inserted after clearActiveEmbeddedRun in the finally block

POST_RUN_HANDLER = (
    '\t\t\t\ttry {\n'
    '\t\t\t\t\tconst _scFlagPath = (params.workspaceDir || process.cwd()) + "/memory/.pending-semantic-compaction";\n'
    '\t\t\t\t\tif (existsSync(_scFlagPath)) {\n'
    '\t\t\t\t\t\tlet _scMeta = {};\n'
    '\t\t\t\t\t\ttry { _scMeta = JSON.parse(readFileSync(_scFlagPath, "utf-8")); } catch (_e) {}\n'
    '\t\t\t\t\t\ttry { unlinkSync(_scFlagPath); } catch (_e) {}\n'
    '\t\t\t\t\t\tconst _scWs = params.workspaceDir || process.cwd();\n'
    '\t\t\t\t\t\tlog$15.info?.("[self-compact] flag detected, scheduling semantic compaction for session=" + (params.sessionKey || params.sessionId));\n'
    '\t\t\t\t\t\timport("./compact.runtime-DnLPxGfr.js").then(({ compactEmbeddedPiSessionDirect }) => {\n'
    '\t\t\t\t\t\t\treturn compactEmbeddedPiSessionDirect({\n'
    '\t\t\t\t\t\t\t\tsessionId: _scMeta.sessionId || params.sessionId,\n'
    '\t\t\t\t\t\t\t\tsessionKey: _scMeta.sessionKey || params.sessionKey,\n'
    '\t\t\t\t\t\t\t\tsessionFile: _scMeta.sessionFile || params.sessionFile,\n'
    '\t\t\t\t\t\t\t\tworkspaceDir: _scWs,\n'
    '\t\t\t\t\t\t\t\tconfig: params.config,\n'
    '\t\t\t\t\t\t\t\tprovider: params.provider,\n'
    '\t\t\t\t\t\t\t\tmodel: params.modelId,\n'
    '\t\t\t\t\t\t\t\tauthProfileId: params.authProfileId,\n'
    '\t\t\t\t\t\t\t\ttrigger: "self-compact",\n'
    '\t\t\t\t\t\t\t\tforce: true,\n'
    '\t\t\t\t\t\t\t\tcustomInstructions: _scMeta.instructions || void 0\n'
    '\t\t\t\t\t\t\t});\n'
    '\t\t\t\t\t\t}).then((result) => {\n'
    '\t\t\t\t\t\t\tlog$15.info?.("[self-compact] semantic compaction completed: ok=" + result?.ok + " compacted=" + result?.compacted + " reason=" + (result?.reason || "none"));\n'
    '\t\t\t\t\t\t\tif (result?.ok) {\n'
    '\t\t\t\t\t\t\t\ttry {\n'
    '\t\t\t\t\t\t\t\t\tconst _taskFile = _scWs + "/.current-task.json";\n'
    '\t\t\t\t\t\t\t\t\tif (!existsSync(_taskFile)) return;\n'
    '\t\t\t\t\t\t\t\t\tlet _chanFile = _scWs + "/.active-channel";\n'
    '\t\t\t\t\t\t\t\t\tlet _chan = "";\n'
    '\t\t\t\t\t\t\t\t\ttry { _chan = readFileSync(_chanFile, "utf-8").trim(); } catch (_e) {}\n'
    '\t\t\t\t\t\t\t\t\tif (!_chan) return;\n'
    '\t\t\t\t\t\t\t\t\tconst _agentId = _scMeta.sessionKey || params.sessionKey || "main";\n'
    '\t\t\t\t\t\t\t\t\tconst _ocBin = process.execPath.replace(/\\/node$/, "/openclaw");\n'
    '\t\t\t\t\t\t\t\t\tconst { execFile } = require("child_process");\n'
    '\t\t\t\t\t\t\t\t\texecFile(_ocBin, ["agent", "--agent", _agentId, "--channel", "discord", "--deliver", "--reply-to", "channel:" + _chan, "--timeout", "300", "-m", "Semantic compaction completed. Your checkpoint has been preserved — check .current-task.json and resume."], { timeout: 320000 }, (err) => {\n'
    '\t\t\t\t\t\t\t\t\t\tif (err) log$15.warn?.("[self-compact] post-compact nudge failed: " + String(err));\n'
    '\t\t\t\t\t\t\t\t\t\telse log$15.info?.("[self-compact] post-compact nudge sent to " + _agentId + " on channel " + _chan);\n'
    '\t\t\t\t\t\t\t\t\t});\n'
    '\t\t\t\t\t\t\t\t} catch (_nudgeErr) {\n'
    '\t\t\t\t\t\t\t\t\tlog$15.warn?.("[self-compact] post-compact nudge setup failed: " + String(_nudgeErr));\n'
    '\t\t\t\t\t\t\t\t}\n'
    '\t\t\t\t\t\t\t}\n'
    '\t\t\t\t\t\t}).catch((err) => {\n'
    '\t\t\t\t\t\t\tlog$15.warn?.("[self-compact] semantic compaction failed: " + String(err));\n'
    '\t\t\t\t\t\t});\n'
    '\t\t\t\t\t}\n'
    '\t\t\t\t} catch (_scErr) {\n'
    '\t\t\t\t\tlog$15.warn?.("[self-compact] flag check failed: " + String(_scErr));\n'
    '\t\t\t\t}\n'
)

AUTH_PROFILES_REPLACEMENTS = [
    (
        "inject post-run deferred semantic compaction handler",
        # Insert after clearActiveEmbeddedRun, before the abort signal cleanup
        '\t\t\t\tclearActiveEmbeddedRun(params.sessionId, queueHandle, params.sessionKey);\n'
        '\t\t\t\tparams.abortSignal?.removeEventListener?.("abort", onAbort);',

        '\t\t\t\tclearActiveEmbeddedRun(params.sessionId, queueHandle, params.sessionKey);\n'
        + POST_RUN_HANDLER +
        '\t\t\t\tparams.abortSignal?.removeEventListener?.("abort", onAbort);',
    ),
]


def find_chunk_files(dist_dir):
    """Find all chunk files that contain session tool registrations."""
    chunks = []
    for js_file in sorted(glob.glob(os.path.join(dist_dir, "*.js"))):
        if js_file.endswith(BACKUP_SUFFIX):
            continue
        basename = os.path.basename(js_file)
        # Skip backup files from other patches
        if ".bak-" in basename or ".bak." in basename:
            continue
        with open(js_file, "r") as f:
            content = f.read()
        if "createSessionsSendTool" in content:
            chunks.append(js_file)
    return chunks


def find_dangerous_tools_files(dist_dir):
    """Find dangerous-tools chunk files."""
    files = []
    for js_file in sorted(glob.glob(os.path.join(dist_dir, "dangerous-tools-*.js"))):
        if ".bak-" in js_file or ".bak." in js_file:
            continue
        files.append(js_file)
    return files


def find_gateway_cli_files(dist_dir):
    """Find gateway-cli chunk files containing session handlers."""
    files = []
    for js_file in sorted(glob.glob(os.path.join(dist_dir, "gateway-cli-*.js"))):
        if ".bak-" in js_file or ".bak." in js_file:
            continue
        with open(js_file, "r") as f:
            content = f.read()
        if '"sessions.compact"' in content and "resolveGatewaySessionTargetFromKey" in content:
            files.append(js_file)
    return files


def find_auth_profiles_files(dist_dir):
    """Find auth-profiles files containing the embedded run finally block."""
    files = []
    for js_file in sorted(glob.glob(os.path.join(dist_dir, "auth-profiles-*.js"))):
        if ".bak-" in js_file or ".bak." in js_file:
            continue
        with open(js_file, "r") as f:
            content = f.read()
        if "clearActiveEmbeddedRun" in content and "compactEmbeddedPiSessionDirect" in content:
            files.append(js_file)
    return files


def detect_compact_runtime_filename(dist_dir, auth_profiles_content):
    """Detect the compact.runtime filename from the auth-profiles import chain."""
    # Look for: import("./compact.runtime-XXXX.js")
    import re
    match = re.search(r'import\("\./(compact\.runtime-[^"]+\.js)"\)', auth_profiles_content)
    if match:
        return match.group(1)
    # Fallback: find the file
    candidates = glob.glob(os.path.join(dist_dir, "compact.runtime-*.js"))
    candidates = [c for c in candidates if ".bak-" not in c and "/plugin-sdk/" not in c]
    if candidates:
        return os.path.basename(candidates[0])
    return "compact.runtime-DnLPxGfr.js"  # Last resort fallback


def apply_replacement(content, desc, old, new):
    """Apply a single replacement, returning (new_content, success)."""
    if old not in content:
        return content, False
    if new in content:
        return content, None  # Already applied
    count = content.count(old)
    content = content.replace(old, new, 1)
    return content, True


def patch_file(filepath, replacements, dry_run=False, skip_already_check=False):
    """Apply all replacements to a file."""
    basename = os.path.basename(filepath)
    with open(filepath, "r") as f:
        content = f.read()

    if not skip_already_check and "sessions_manage" in content and "createSessionsManageTool" in content:
        print(f"  {basename}: already patched, skipping")
        return True

    backup = filepath + BACKUP_SUFFIX
    if not os.path.exists(backup) and not dry_run:
        shutil.copy2(filepath, backup)
        print(f"  {basename}: backed up")

    modified = False
    for desc, old, new in replacements:
        new_content, result = apply_replacement(content, desc, old, new)
        if result is True:
            content = new_content
            modified = True
            print(f"  {basename}: \u2705 {desc}")
        elif result is None:
            print(f"  {basename}: \u23ed\ufe0f  {desc} (already applied)")
        else:
            print(f"  {basename}: \u26a0\ufe0f  {desc} (pattern not found)")

    if modified and not dry_run:
        with open(filepath, "w") as f:
            f.write(content)

    return modified


def restore_backups(dist_dir):
    """Restore all backup files created by this patch."""
    restored = 0
    for bak_file in sorted(glob.glob(os.path.join(dist_dir, f"*{BACKUP_SUFFIX}"))):
        original = bak_file[: -len(BACKUP_SUFFIX)]
        if os.path.exists(bak_file):
            shutil.copy2(bak_file, original)
            os.remove(bak_file)
            print(f"  Restored: {os.path.basename(original)}")
            restored += 1
    return restored


def main():
    parser = argparse.ArgumentParser(description="Apply sessions_manage tool patch")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="Path to OC dist directory")
    parser.add_argument("--restore", action="store_true", help="Restore all files from backups and exit")
    args = parser.parse_args()

    dist_dir = args.dist_dir
    if not os.path.isdir(dist_dir):
        print(f"ERROR: dist directory not found: {dist_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Dist directory: {dist_dir}")

    if args.restore:
        print("Mode: RESTORE")
        print()
        count = restore_backups(dist_dir)
        print(f"\nRestored {count} files.")
        return

    print(f"Mode: {'DRY RUN' if args.dry_run else 'APPLY'}")
    print()

    # 1. Patch main chunk files (contain tool registration + function)
    chunks = find_chunk_files(dist_dir)
    print(f"Found {len(chunks)} chunk files with session tool registrations:")
    all_replacements = REPLACEMENTS + [DISPLAY_REPLACEMENT]
    for chunk in chunks:
        patch_file(chunk, all_replacements, dry_run=args.dry_run)
    print()

    # 2. Patch dangerous-tools files
    dangerous = find_dangerous_tools_files(dist_dir)
    print(f"Found {len(dangerous)} dangerous-tools files:")
    for dt_file in dangerous:
        patch_file(dt_file, DANGEROUS_TOOLS_REPLACEMENTS, dry_run=args.dry_run)
    print()

    # 3. Patch gateway-cli files (sessions.compactSemantic RPC handler)
    gateways = find_gateway_cli_files(dist_dir)
    print(f"Found {len(gateways)} gateway-cli files:")
    for gw_file in gateways:
        patch_file(gw_file, GATEWAY_REPLACEMENTS, dry_run=args.dry_run, skip_already_check=True)
    print()

    # 4. Patch auth-profiles files (post-run deferred compaction handler)
    auth_files = find_auth_profiles_files(dist_dir)
    print(f"Found {len(auth_files)} auth-profiles files:")
    for auth_file in auth_files:
        # Detect the correct compact.runtime filename for this file
        with open(auth_file, "r") as f:
            auth_content = f.read()
        runtime_file = detect_compact_runtime_filename(dist_dir, auth_content)
        # Build replacements with the correct runtime filename
        auth_replacements = []
        for desc, old, new in AUTH_PROFILES_REPLACEMENTS:
            # Replace the hardcoded compact.runtime filename with the detected one
            new_fixed = new.replace("compact.runtime-DnLPxGfr.js", runtime_file)
            auth_replacements.append((desc, old, new_fixed))
        print(f"  Using compact runtime: {runtime_file}")
        patch_file(auth_file, auth_replacements, dry_run=args.dry_run, skip_already_check=True)
    print()

    if args.dry_run:
        print("DRY RUN complete. No files were modified.")
    else:
        print("Patch applied. Restart gateway to activate:")
        print("  ~/my-openclaw-patches/scripts/graceful-restart.sh")
        print()
        print("To revert: python3 apply-sessions-manage-tool.py --restore")


if __name__ == "__main__":
    main()
