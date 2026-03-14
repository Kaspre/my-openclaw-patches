#!/usr/bin/env bash
set -euo pipefail

DIST_DIR="/home/captain/.nvm/versions/node/v25.6.1/lib/node_modules/openclaw/dist"

echo "=== WS Handshake Timeout Patch (#44718) ==="
echo "Dist dir: $DIST_DIR"
echo

# --- Server-side: gateway bundles ---
echo "--- Server-side: DEFAULT_HANDSHAKE_TIMEOUT_MS 3e3 -> 15e3 ---"
server_count=0
for f in "$DIST_DIR"/gateway-cli-*.js; do
  [[ "$f" == *.bak* ]] && continue
  if grep -q 'DEFAULT_HANDSHAKE_TIMEOUT_MS\s*=\s*3e3' "$f"; then
    cp "$f" "$f.bak-ws-timeout"
    sed -i 's/DEFAULT_HANDSHAKE_TIMEOUT_MS\s*=\s*3e3/DEFAULT_HANDSHAKE_TIMEOUT_MS = 15e3/g' "$f"
    echo "  Patched: $(basename "$f")"
    ((server_count++))
  fi
done
echo "  Server files patched: $server_count"
echo

# --- Client-side: connectChallengeTimeoutMs ---
echo "--- Client-side: connectChallengeTimeoutMs 2e3 -> 10e3 ---"
client_count=0
for f in "$DIST_DIR"/*.js; do
  [[ "$f" == *.bak* ]] && continue
  if grep -q 'connectChallengeTimeoutMs' "$f"; then
    cp "$f" "$f.bak-ws-timeout"
    sed -i 's/connectChallengeTimeoutMs.*2e3/connectChallengeTimeoutMs=typeof rawConnectDelayMs=="number"\&\&Number.isFinite(rawConnectDelayMs)?Math.max(250,Math.min(1e4,rawConnectDelayMs)):10e3/' "$f"
    echo "  Patched: $(basename "$f")"
    ((client_count++))
  fi
done
echo "  Client files patched: $client_count"
echo

total=$((server_count + client_count))
echo "=== Total files patched: $total ==="
echo

# --- Verify no old values remain ---
echo "--- Verification ---"
errors=0

remaining_server=$(grep -rl 'DEFAULT_HANDSHAKE_TIMEOUT_MS\s*=\s*3e3' "$DIST_DIR"/gateway-cli-*.js 2>/dev/null | grep -v '\.bak' || true)
if [[ -n "$remaining_server" ]]; then
  echo "  ERROR: Old server timeout still found in:"
  echo "$remaining_server"
  errors=1
else
  echo "  OK: No gateway files contain old 3e3 timeout"
fi

remaining_client=$(grep -rl 'connectChallengeTimeoutMs.*2e3' "$DIST_DIR"/*.js 2>/dev/null | grep -v '\.bak' || true)
if [[ -n "$remaining_client" ]]; then
  echo "  ERROR: Old client timeout still found in:"
  echo "$remaining_client"
  errors=1
else
  echo "  OK: No dist files contain old 2e3 timeout"
fi

echo
if [[ $errors -eq 0 ]]; then
  echo "Patch applied successfully."
else
  echo "WARNING: Some files may not have been patched correctly."
fi

echo
echo "Restart gateway to apply:"
echo "  systemctl --user restart openclaw-gateway"
