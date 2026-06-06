#!/usr/bin/env python3
"""Patch: Mirror of openclaw#82403 — release Codex raw assistant completions
when `turn/completed` is missing, preventing agent --local zombies.

Upstream: openclaw#82403 (merged 2026-05-16T11:13:55Z, commit f50c65f12454).
Source: extensions/codex/src/app-server/run-attempt.ts + event-projector.ts.
In our bundled dist both files are merged into run-attempt-*.js (tree-shaken).

Why local mirror: the fix merged AFTER v2026.5.16-beta.2 was cut (16 minutes
after the tag was published; tag's source commit predates the merge). The
next OC beta should bundle it natively — retire this script then.

Five hunks (all in run-attempt-*.js):
  1. Add `let turnCrossedToolHandoff = false;` declaration in main run scope
  2. Set `turnCrossedToolHandoff = true` at the tool-call site
  3. Inject the tool-handoff tracking + assistantCompletionCanRelease in the
     notification handler, replacing the bare isCompletedAssistantNotification
     check
  4. Add three new helper functions: isAssistantCompletionReleaseNotification,
     isNativeToolProgressNotification, isRawAssistantCompletionNotification
  5. In handleRawResponseItemCompleted, capture item phase and emit commentary
     progress when phase === "commentary"

Idempotent (each hunk skips if already applied).
"""
import argparse
import sys
from pathlib import Path

DIST_DIR = Path.home() / ".local/node-current/lib/node_modules/openclaw/dist"


def find_bundle() -> Path | None:
    """Find the run-attempt-*.js bundle (content-hashed name)."""
    candidates = sorted(DIST_DIR.glob("run-attempt-*.js"))
    candidates = [p for p in candidates if not p.name.endswith(".map")]
    return candidates[0] if candidates else None


# === HUNK 1: declare turnCrossedToolHandoff ===
HUNK_1_SEARCH = "\tconst activeTurnItemIds = /* @__PURE__ */ new Set();\n"
HUNK_1_REPLACE = (
    "\tconst activeTurnItemIds = /* @__PURE__ */ new Set();\n"
    "\tlet turnCrossedToolHandoff = false;\n"
)

# === HUNK 2: set turnCrossedToolHandoff = true at the dynamic-tool-call site ===
# The tool-call site is unique because it's the one that also adds to activeOpenClawDynamicToolCallIds.
HUNK_2_SEARCH = (
    "\t\t\tarmCompletionWatchOnResponse = true;\n"
    "\t\t\tactiveOpenClawDynamicToolCallIds.add(call.callId);\n"
)
HUNK_2_REPLACE = (
    "\t\t\tarmCompletionWatchOnResponse = true;\n"
    "\t\t\tturnCrossedToolHandoff = true;\n"
    "\t\t\tactiveOpenClawDynamicToolCallIds.add(call.callId);\n"
)

# === HUNK 3: notification handler — tool-handoff tracking + new release fn use ===
# The bundled form is a long single-line statement. We anchor on the exact text.
HUNK_3_SEARCH = (
    "\t\tconst rawToolOutputCompletion = isRawToolOutputCompletionNotification(notification);\n"
    "\t\tconst shouldRearmCompletionIdleWatchAfterLastCurrentTurnItem = isCurrentTurnNotification && notification.method === \"item/completed\" && activeTurnItemIds.size === 0 && !trackedDynamicToolCompletion && !isCompletedAssistantNotification(notification);\n"
)
HUNK_3_REPLACE = (
    "\t\tconst rawToolOutputCompletion = isRawToolOutputCompletionNotification(notification);\n"
    "\t\tif (isCurrentTurnNotification && (rawToolOutputCompletion || isNativeToolProgressNotification(notification))) turnCrossedToolHandoff = true;\n"
    "\t\tconst assistantCompletionCanRelease = isAssistantCompletionReleaseNotification(notification, turnCrossedToolHandoff);\n"
    "\t\tconst shouldRearmCompletionIdleWatchAfterLastCurrentTurnItem = isCurrentTurnNotification && notification.method === \"item/completed\" && activeTurnItemIds.size === 0 && !trackedDynamicToolCompletion && !assistantCompletionCanRelease;\n"
)

# === HUNK 4: replace isCompletedAssistantNotification with assistantCompletionCanRelease in arm-watch path ===
HUNK_4_SEARCH = (
    "\t\telse if (isCurrentTurnNotification && isCompletedAssistantNotification(notification)) "
    "armTurnAssistantCompletionIdleWatch(describeNotificationActivity(notification));\n"
)
HUNK_4_REPLACE = (
    "\t\telse if (isCurrentTurnNotification && assistantCompletionCanRelease) "
    "armTurnAssistantCompletionIdleWatch(describeNotificationActivity(notification));\n"
)

# === HUNK 5: add 3 new helper functions after isCompletedAssistantNotification ===
# Anchor: end of isCompletedAssistantNotification function body. Source code:
#   function isCompletedAssistantNotification(notification) {
#     ...
#     return Boolean(item && readString(item, "type") === "agentMessage" && readString(item, "phase") !== "commentary");
#   }
# We append 3 new functions after that closing brace.
HUNK_5_SEARCH = (
    "function isCompletedAssistantNotification(notification) {\n"
    "\tif (!isJsonObject(notification.params)) return false;\n"
    "\tif (notification.method !== \"item/completed\") return false;\n"
    "\tconst item = isJsonObject(notification.params.item) ? notification.params.item : void 0;\n"
    "\treturn Boolean(item && readString(item, \"type\") === \"agentMessage\" && readString(item, \"phase\") !== \"commentary\");\n"
    "}\n"
)
HUNK_5_REPLACE = (
    "function isCompletedAssistantNotification(notification) {\n"
    "\tif (!isJsonObject(notification.params)) return false;\n"
    "\tif (notification.method !== \"item/completed\") return false;\n"
    "\tconst item = isJsonObject(notification.params.item) ? notification.params.item : void 0;\n"
    "\treturn Boolean(item && readString(item, \"type\") === \"agentMessage\" && readString(item, \"phase\") !== \"commentary\");\n"
    "}\n"
    "function isAssistantCompletionReleaseNotification(notification, turnCrossedToolHandoff) {\n"
    "\tif (isCompletedAssistantNotification(notification)) return true;\n"
    "\treturn !turnCrossedToolHandoff && isRawAssistantCompletionNotification(notification);\n"
    "}\n"
    "function isNativeToolProgressNotification(notification) {\n"
    "\tif (notification.method !== \"item/started\" && notification.method !== \"item/completed\" && notification.method !== \"item/updated\") return false;\n"
    "\tif (!isJsonObject(notification.params)) return false;\n"
    "\tconst item = isJsonObject(notification.params.item) ? notification.params.item : void 0;\n"
    "\tswitch (item ? readString(item, \"type\") : void 0) {\n"
    "\t\tcase \"commandExecution\":\n"
    "\t\tcase \"fileChange\":\n"
    "\t\tcase \"mcpToolCall\":\n"
    "\t\tcase \"webSearch\":\n"
    "\t\t\treturn true;\n"
    "\t\tdefault:\n"
    "\t\t\treturn false;\n"
    "\t}\n"
    "}\n"
    "function isRawAssistantCompletionNotification(notification) {\n"
    "\tif (notification.method !== \"rawResponseItem/completed\" || !isJsonObject(notification.params)) return false;\n"
    "\tconst item = isJsonObject(notification.params.item) ? notification.params.item : void 0;\n"
    "\treturn Boolean(item && readString(item, \"type\") === \"message\" && readString(item, \"role\") === \"assistant\" && readString(item, \"phase\") !== \"commentary\" && readRawAssistantTextPreview(item));\n"
    "}\n"
)

# === HUNK 6: event-projector half — handleRawResponseItemCompleted ===
# This is in the same bundle file (the projector class is tree-shaken alongside
# run-attempt). The bundle uses `readString$2` (renamed for dedupe).
HUNK_6_SEARCH = (
    "\thandleRawResponseItemCompleted(params) {\n"
    "\t\tconst item = isJsonObject(params.item) ? params.item : void 0;\n"
    "\t\tif (!item || readString$2(item, \"role\") !== \"assistant\") return;\n"
    "\t\tconst text = extractRawAssistantText(item);\n"
    "\t\tif (!text) return;\n"
    "\t\tconst itemId = readString$2(item, \"id\") ?? `raw-assistant-${this.assistantItemOrder.length + 1}`;\n"
    "\t\tthis.rememberAssistantItem(itemId);\n"
    "\t\tthis.assistantTextByItem.set(itemId, text);\n"
    "\t}\n"
)
HUNK_6_REPLACE = (
    "\thandleRawResponseItemCompleted(params) {\n"
    "\t\tconst item = isJsonObject(params.item) ? params.item : void 0;\n"
    "\t\tif (!item || readString$2(item, \"role\") !== \"assistant\") return;\n"
    "\t\tconst text = extractRawAssistantText(item);\n"
    "\t\tif (!text) return;\n"
    "\t\tconst itemId = readString$2(item, \"id\") ?? `raw-assistant-${this.assistantItemOrder.length + 1}`;\n"
    "\t\tconst phase = readString$2(item, \"phase\");\n"
    "\t\tif (phase) this.assistantPhaseByItem.set(itemId, phase);\n"
    "\t\tthis.rememberAssistantItem(itemId);\n"
    "\t\tthis.assistantTextByItem.set(itemId, text);\n"
    "\t\tif (phase === \"commentary\") this.emitCommentaryProgress({ itemId, text });\n"
    "\t}\n"
)


HUNKS = [
    ("turnCrossedToolHandoff declaration", HUNK_1_SEARCH, HUNK_1_REPLACE),
    ("set turnCrossedToolHandoff at tool-call site", HUNK_2_SEARCH, HUNK_2_REPLACE),
    ("inject tool-handoff tracking + assistantCompletionCanRelease", HUNK_3_SEARCH, HUNK_3_REPLACE),
    ("replace isCompletedAssistantNotification with assistantCompletionCanRelease in arm-watch", HUNK_4_SEARCH, HUNK_4_REPLACE),
    ("add isAssistantCompletionReleaseNotification + 2 helpers", HUNK_5_SEARCH, HUNK_5_REPLACE),
    ("handleRawResponseItemCompleted: capture phase + commentary emit", HUNK_6_SEARCH, HUNK_6_REPLACE),
]


def main():
    parser = argparse.ArgumentParser(description="Mirror of openclaw#82403")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    args = parser.parse_args()

    if not args.dist_dir.exists():
        print(f"SKIP: dist dir not found: {args.dist_dir}")
        sys.exit(0)

    bundle = find_bundle()
    if not bundle:
        print("SKIP: no run-attempt-*.js bundle found in dist")
        sys.exit(0)
    print(f"Target: {bundle.name}")

    content = bundle.read_text()
    applied = 0
    already = 0
    failed = 0

    for desc, search, replace in HUNKS:
        if replace in content and search not in content:
            print(f"  OK (already applied): {desc}")
            already += 1
            continue
        if search not in content:
            print(f"  FAIL: {desc} — search pattern not found")
            failed += 1
            continue
        if args.dry_run:
            print(f"  DRY-RUN: would apply: {desc}")
            applied += 1
            continue
        content = content.replace(search, replace, 1)
        print(f"  APPLIED: {desc}")
        applied += 1

    if failed > 0:
        print(f"\n{failed} hunk(s) failed. NOT writing changes.")
        sys.exit(1)

    if args.dry_run:
        print(f"\nDry-run complete: would apply {applied} hunk(s), {already} already applied.")
        return

    if applied > 0:
        # Backup before writing
        backup = bundle.with_suffix(bundle.suffix + ".bak-codex-raw-completion")
        if not backup.exists():
            backup.write_text(bundle.read_text())
            print(f"Backup: {backup.name}")
        bundle.write_text(content)
        print(f"\nDone: {applied} applied, {already} already applied. Restart gateway to load.")
    else:
        print(f"\nNo changes. {already} hunk(s) already applied.")


if __name__ == "__main__":
    main()
