# Patch: UI User Message Vanish Fix
- Date: 2026-03-10
- Version: v2026.3.8
- Issues: #14928, #9183
- PRs referenced: #15273 (closed, broken syntax), #16767 (open, has memory leak)

## Problem
User messages typed in the gateway dashboard (openclaw-control-ui) vanish after sending. The message is received by the agent but disappears from the chat UI. Root cause: loadChatHistory() replaces the entire chatMessages array with server data. If the server hasn't processed the user message yet, it gets wiped from local state.

## Fix
In loadChatHistory (minified as Qt), preserve any user messages from local state that aren't yet in the server response. Match by role + timestamp.

## File
~/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist/control-ui/assets/index-wxM3V0HM.js
Backup: same path with .bak extension

## Search/Replace

BEFORE:
e.chatMessages=n.filter(s=>!Ts(s)),e.chatThinkingLevel=t.thinkingLevel

AFTER:
const _fm=n.filter(s=>!Ts(s)),_om=e.chatMessages,_pend=_om.filter(s=>s.role==="user"&&s.timestamp&&!_fm.some(x=>x.role==="user"&&x.timestamp===s.timestamp));e.chatMessages=_pend.length>0?[..._fm,..._pend]:_fm,e.chatThinkingLevel=t.thinkingLevel

## Rollback
cp index-wxM3V0HM.js.bak index-wxM3V0HM.js

## Re-apply after upgrade
The UI file name will change on upgrade (hash in filename). Find the new file, verify the old search string exists, apply the sed replacement. The backup will be stale after upgrade.

## Notes
- UI-only patch, no gateway/agent runtime impact
- No new event listeners (no memory leak risk)
- Pending user messages auto-clear on next history refresh once server catches up
- steipete's commit 121851e74 fixed assistant message flicker separately (already in v2026.3.8)
