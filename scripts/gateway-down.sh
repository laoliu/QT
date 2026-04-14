#!/usr/bin/env bash
# Stop the QT gateway
# Usage: ./gateway-down.sh [--lobster] [--port PORT] [--py-port PORT]
#   Default    Stop the Python FastAPI gateway
#   --lobster  Stop the OpenClaw (龙虾) Node.js gateway instead
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${QT_GATEWAY_PORT:-18889}"
PY_PORT="${QT_PY_GATEWAY_PORT:-8001}"
LOG_DIR="$PROJECT_DIR/logs"
LOBSTER=false

for arg in "$@"; do
  case "$arg" in
    --lobster) LOBSTER=true ;;
    --port=*) PORT="${arg#*=}" ;;
    --py-port=*) PY_PORT="${arg#*=}" ;;
  esac
done

# ── OpenClaw (龙虾) mode ──────────────────────────────────────
if [[ "$LOBSTER" == true ]]; then
    PID_FILE="$LOG_DIR/gateway-${PORT}.pid"

    echo "Stopping OpenClaw (龙虾) gateway on port $PORT ..."

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
        echo "✅ OpenClaw gateway stopped."
    else
        echo "ℹ️  No OpenClaw gateway was running on port $PORT."
    fi
    exit 0
fi

# ── Python FastAPI mode (default) ──────────────────────────────
PID_FILE="$LOG_DIR/py-gateway-${PY_PORT}.pid"

echo "Stopping Python FastAPI gateway on port $PY_PORT ..."

stopped=false

# 1) Kill by PID file
if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        stopped=true
    fi
    rm -f "$PID_FILE"
fi

# 2) Kill anything still listening on the port
pids="$(lsof -tiTCP:"$PY_PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
    stopped=true
fi

if $stopped; then
    echo "✅ Python gateway stopped."
else
    echo "ℹ️  No Python gateway was running on port $PY_PORT."
fi
