#!/usr/bin/env python3
"""
Patch: Add sessions_manage tool for programmatic session compact/reset
Issue: openclaw/openclaw#10981
PR: openclaw/openclaw#51415

Injects the sessions_manage tool into the bundled dist files so agents can
call sessions.compact and sessions.reset gateway RPC methods programmatically.

Usage:
  python3 apply-sessions-manage-tool.py [--dry-run] [--dist-dir PATH]
"""

import argparse
import glob
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
\tconst ACTIONS = ["compact", "reset"];
\treturn {
\t\tlabel: "Session Manage",
\t\tname: "sessions_manage",
\t\tdescription: "Compact or reset a session by key. Use action 'compact' to compress context or 'reset' to start fresh.",
\t\tparameters: {
\t\t\ttype: "object",
\t\t\tproperties: {
\t\t\t\tsessionKey: { type: "string" },
\t\t\t\taction: { type: "string", enum: ["compact", "reset"] }
\t\t\t},
\t\t\trequired: ["sessionKey", "action"]
\t\t},
\t\texecute: async (_toolCallId, args) => {
\t\t\tconst params = args;
\t\t\tconst sessionKeyParam = typeof params?.sessionKey === "string" ? params.sessionKey.trim() : "";
\t\t\tconst action = typeof params?.action === "string" ? params.action.trim() : "";
\t\t\tif (!sessionKeyParam) return jsonResult({ status: "error", error: "sessionKey is required" });
\t\t\tif (!ACTIONS.includes(action)) return jsonResult({ status: "error", error: "action must be 'compact' or 'reset'" });
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
\t\t\tif (action === "reset" && resolvedKey === effectiveRequesterKey) {
\t\t\t\treturn jsonResult({ status: "error", action, sessionKey: displayKey, error: "Cannot reset own active session — use /new or target from another session" });
\t\t\t}
\t\t\tif (action === "compact") {
\t\t\t\ttry {
\t\t\t\t\tconst result = await callGateway({ method: "sessions.compact", params: { key: resolvedKey } });
\t\t\t\t\treturn jsonResult({ status: "ok", action, sessionKey: displayKey, compacted: result?.compacted === true, ...(typeof result?.reason === "string" ? { reason: result.reason } : {}), ...(typeof result?.kept === "number" ? { kept: result.kept } : {}) });
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

# === Replacements ===
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


def apply_replacement(content, desc, old, new):
    """Apply a single replacement, returning (new_content, success)."""
    if old not in content:
        return content, False
    if new in content:
        return content, None  # Already applied
    count = content.count(old)
    content = content.replace(old, new, 1)
    return content, True


def patch_file(filepath, replacements, dry_run=False):
    """Apply all replacements to a file."""
    basename = os.path.basename(filepath)
    with open(filepath, "r") as f:
        content = f.read()

    if "sessions_manage" in content and "createSessionsManageTool" in content:
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
            print(f"  {basename}: ✅ {desc}")
        elif result is None:
            print(f"  {basename}: ⏭️  {desc} (already applied)")
        else:
            print(f"  {basename}: ⚠️  {desc} (pattern not found)")

    if modified and not dry_run:
        with open(filepath, "w") as f:
            f.write(content)

    return modified


def main():
    parser = argparse.ArgumentParser(description="Apply sessions_manage tool patch")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--dist-dir", default=DIST_DIR_DEFAULT, help="Path to OC dist directory")
    args = parser.parse_args()

    dist_dir = args.dist_dir
    if not os.path.isdir(dist_dir):
        print(f"ERROR: dist directory not found: {dist_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Dist directory: {dist_dir}")
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

    if args.dry_run:
        print("DRY RUN complete. No files were modified.")
    else:
        print("Patch applied. Restart gateway to activate.")
        print("To revert: restore .bak-sessions-manage files")


if __name__ == "__main__":
    main()
