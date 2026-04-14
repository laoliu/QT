#!/usr/bin/env bash
# Start the QT gateway in the background
# Usage: ./gateway-up.sh [--lobster] [--port PORT] [--py-port PORT]
#   Default    Start the Python FastAPI gateway (uvicorn) on py-port
#   --lobster  Start the OpenClaw (龙虾) Node.js gateway instead
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${QT_GATEWAY_PORT:-18889}"
PY_PORT="${QT_PY_GATEWAY_PORT:-8001}"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON_DIR="$PROJECT_DIR/quant-trading/python"
LOBSTER=false

for arg in "$@"; do
  case "$arg" in
    --lobster) LOBSTER=true ;;
    --port=*) PORT="${arg#*=}" ;;
    --py-port=*) PY_PORT="${arg#*=}" ;;
  esac
done

mkdir -p "$LOG_DIR"

# ── OpenClaw (龙虾) mode ──────────────────────────────────────
if [[ "$LOBSTER" == true ]]; then
    existing="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$existing" ]]; then
        echo "⚠️  Port $PORT is already in use (PID: $existing)"
        echo "   Run ./scripts/gateway-down.sh --lobster first, or set QT_GATEWAY_PORT to use a different port."
        exit 1
    fi

    LOG_FILE="$LOG_DIR/gateway-${PORT}.log"

    echo "Starting OpenClaw (龙虾) gateway on port $PORT ..."
    cd "$PROJECT_DIR"
    nohup pnpm openclaw gateway run --bind loopback --port "$PORT" --force \
        > "$LOG_FILE" 2>&1 &

    GW_PID=$!
    echo "$GW_PID" > "$LOG_DIR/gateway-${PORT}.pid"

    sleep 1
    if kill -0 "$GW_PID" 2>/dev/null; then
        echo "✅ OpenClaw gateway started (PID: $GW_PID, port: $PORT)"
        echo "   Log: $LOG_FILE"
    else
        echo "❌ OpenClaw gateway failed to start. Check log: $LOG_FILE"
        exit 1
    fi
    exit 0
fi

# ── Python FastAPI mode (default) ──────────────────────────────
existing="$(lsof -tiTCP:"$PY_PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$existing" ]]; then
    echo "⚠️  Port $PY_PORT is already in use (PID: $existing)"
    echo "   Run ./scripts/gateway-down.sh first, or set QT_PY_GATEWAY_PORT to use a different port."
    exit 1
fi

LOG_FILE="$LOG_DIR/py-gateway-${PY_PORT}.log"

echo "Starting Python FastAPI gateway on port $PY_PORT ..."
cd "$PYTHON_DIR"
nohup python -m uvicorn aipha.api.app:app --host 127.0.0.1 --port "$PY_PORT" \
    > "$LOG_FILE" 2>&1 &

GW_PID=$!
echo "$GW_PID" > "$LOG_DIR/py-gateway-${PY_PORT}.pid"

sleep 1
if kill -0 "$GW_PID" 2>/dev/null; then
    echo "✅ Python gateway started (PID: $GW_PID, port: $PY_PORT)"
    echo "   Log: $LOG_FILE"
    echo "   API docs: http://127.0.0.1:${PY_PORT}/api/docs"
else
    echo "❌ Python gateway failed to start. Check log: $LOG_FILE"
    exit 1
fi
else
    echo "❌ Gateway failed to start. Check log: $LOG_FILE"
    exit 1
fi
