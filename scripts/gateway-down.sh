#!/usr/bin/env bash
# Stop the QT gateway
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${QT_GATEWAY_PORT:-18889}"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/gateway-${PORT}.pid"

echo "Stopping QT gateway on port $PORT ..."

stopped=false

# 1) Try graceful stop via openclaw CLI
cd "$PROJECT_DIR"
pnpm openclaw gateway stop 2>/dev/null && stopped=true || true

# 2) Kill by PID file
if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        stopped=true
    fi
    rm -f "$PID_FILE"
fi

# 3) Kill anything still listening on the port
pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
    stopped=true
fi

if $stopped; then
    echo "✅ Gateway stopped."
else
    echo "ℹ️  No gateway was running on port $PORT."
fi
