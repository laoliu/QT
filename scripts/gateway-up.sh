#!/usr/bin/env bash
# Start the QT gateway in the background
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${QT_GATEWAY_PORT:-18889}"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

# Check if something is already listening on the port
existing="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$existing" ]]; then
    echo "⚠️  Port $PORT is already in use (PID: $existing)"
    echo "   Run ./scripts/gateway-down.sh first, or set QT_GATEWAY_PORT to use a different port."
    exit 1
fi

LOG_FILE="$LOG_DIR/gateway-${PORT}.log"

echo "Starting QT gateway on port $PORT ..."
cd "$PROJECT_DIR"
nohup pnpm openclaw gateway run --bind loopback --port "$PORT" --force \
    > "$LOG_FILE" 2>&1 &

GW_PID=$!
echo "$GW_PID" > "$LOG_DIR/gateway-${PORT}.pid"

# Wait briefly and verify the process is still alive
sleep 1
if kill -0 "$GW_PID" 2>/dev/null; then
    echo "✅ Gateway started (PID: $GW_PID, port: $PORT)"
    echo "   Log: $LOG_FILE"
else
    echo "❌ Gateway failed to start. Check log: $LOG_FILE"
    exit 1
fi
