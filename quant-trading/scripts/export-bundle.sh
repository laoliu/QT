#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────────
# quant-trading export — 把配置 + 数据 + Python 策略打包成一个可迁移的 tar.gz
#
# 用法:
#   ./extensions/quant-trading/scripts/export-bundle.sh [输出文件路径]
#
# 默认输出: ~/quant-trading-bundle-<日期>.tar.gz
#
# 打包内容:
#   1. openclaw.json 中的 quant-trading 插件配置
#   2. ~/.openclaw/quant-trading/ 运行时数据 (signals, stock-list, portfolio, orders …)
#   3. ~/.openclaw/extensions/quant-trading/cache/ 缓存数据库 (stock_data.db, financial_data.db)
#   4. ~/.openclaw/workspace/ 人设文件 (IDENTITY, USER, SOUL, TOOLS …)
#   5. ~/.openclaw/identity/ 设备密钥
#   6. ~/.openclaw/cron/ 定时任务
#   7. Python 策略代码 (strategies/, screeners/, engines/, data_providers/, brokers/, cache/)
#   8. Python 依赖清单 (requirements.txt)
#   9. 插件元信息 (openclaw.plugin.json, package.json)
# ────────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXT_DIR/../.." && pwd)"

DATE_TAG=$(date +%Y%m%d-%H%M%S)
OUTPUT="${1:-$HOME/quant-trading-bundle-${DATE_TAG}.tar.gz}"

STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

BUNDLE="$STAGING/quant-trading-bundle"
mkdir -p "$BUNDLE"/{config,data,cache,openclaw-home,python,meta}

echo "📦 Quant Trading Export"
echo "───────────────────────"

# ── 1. 插件配置 ──────────────────────────────────────────────────────────────
CONFIG_FILE="$REPO_ROOT/config-custom/openclaw.json"
if [[ -f "$CONFIG_FILE" ]]; then
  cp "$CONFIG_FILE" "$BUNDLE/config/openclaw.json"
  echo "✓ 配置文件  config-custom/openclaw.json"
else
  echo "⚠ 未找到 config-custom/openclaw.json，跳过"
fi

# 也检查 ~/.openclaw/openclaw.json (部署模式)
HOME_CONFIG="$HOME/.openclaw/openclaw.json"
if [[ -f "$HOME_CONFIG" ]]; then
  cp "$HOME_CONFIG" "$BUNDLE/config/openclaw-home.json"
  echo "✓ 配置文件  ~/.openclaw/openclaw.json"
fi

# ── 2. 运行时数据 ────────────────────────────────────────────────────────────
DATA_DIR="${QUANT_DATA_DIR:-$HOME/.openclaw/quant-trading}"
if [[ -d "$DATA_DIR" ]]; then
  cp -R "$DATA_DIR"/. "$BUNDLE/data/"
  FILE_COUNT=$(find "$BUNDLE/data" -type f | wc -l | tr -d ' ')
  echo "✓ 运行时数据  $DATA_DIR  ($FILE_COUNT 个文件)"
else
  echo "⚠ 数据目录 $DATA_DIR 不存在，跳过"
fi

# ── 3. 缓存数据库 (SQLite) ───────────────────────────────────────────────────
CACHE_DIR="$HOME/.openclaw/extensions/quant-trading/cache"
if [[ -d "$CACHE_DIR" ]]; then
  # 先做 WAL checkpoint 以确保数据完整性
  for db in "$CACHE_DIR"/*.db; do
    [[ -f "$db" ]] || continue
    sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
  done
  # 只复制 .db 文件（不需要 -shm/-wal，checkpoint 后数据已合并）
  for db in "$CACHE_DIR"/*.db; do
    [[ -f "$db" ]] || continue
    cp "$db" "$BUNDLE/cache/"
  done
  # 也复制其他非 SQLite 缓存文件 (如 JSON 缓存)
  for f in "$CACHE_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    cp "$f" "$BUNDLE/cache/"
  done
  CACHE_SIZE=$(du -sh "$BUNDLE/cache" | cut -f1)
  CACHE_COUNT=$(find "$BUNDLE/cache" -type f | wc -l | tr -d ' ')
  echo "✓ 缓存数据库  $CACHE_DIR  ($CACHE_COUNT 个文件, $CACHE_SIZE)"
else
  echo "⚠ 缓存目录 $CACHE_DIR 不存在，跳过"
fi

# ── 4. ~/.openclaw 其他有用目录 ──────────────────────────────────────────────
# workspace/ — AI 人设文件 (IDENTITY.md, USER.md, SOUL.md, TOOLS.md …)
WORKSPACE_DIR="$HOME/.openclaw/workspace"
if [[ -d "$WORKSPACE_DIR" ]]; then
  mkdir -p "$BUNDLE/openclaw-home/workspace"
  rsync -a --exclude='.DS_Store' "$WORKSPACE_DIR/" "$BUNDLE/openclaw-home/workspace/"
  WS_COUNT=$(find "$BUNDLE/openclaw-home/workspace" -type f | wc -l | tr -d ' ')
  echo "✓ Workspace 人设  $WORKSPACE_DIR  ($WS_COUNT 个文件)"
fi

# identity/ — 设备身份密钥
IDENTITY_DIR="$HOME/.openclaw/identity"
if [[ -d "$IDENTITY_DIR" ]]; then
  mkdir -p "$BUNDLE/openclaw-home/identity"
  cp -R "$IDENTITY_DIR"/. "$BUNDLE/openclaw-home/identity/"
  echo "✓ 设备身份  $IDENTITY_DIR"
fi

# cron/ — 定时任务
CRON_DIR="$HOME/.openclaw/cron"
if [[ -d "$CRON_DIR" ]]; then
  mkdir -p "$BUNDLE/openclaw-home/cron"
  for f in "$CRON_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    cp "$f" "$BUNDLE/openclaw-home/cron/"
  done
  echo "✓ 定时任务  $CRON_DIR"
fi

# feishu/ — 飞书去重状态
FEISHU_DIR="$HOME/.openclaw/feishu"
if [[ -d "$FEISHU_DIR" ]]; then
  mkdir -p "$BUNDLE/openclaw-home/feishu"
  rsync -a --exclude='.DS_Store' "$FEISHU_DIR/" "$BUNDLE/openclaw-home/feishu/"
  echo "✓ 飞书状态  $FEISHU_DIR"
fi

# ── 5. Python 策略代码 ───────────────────────────────────────────────────────
PYTHON_DIR="$EXT_DIR/python"
if [[ -d "$PYTHON_DIR" ]]; then
  # 复制所有 Python 源码，排除 __pycache__ 和 .pyc
  rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    "$PYTHON_DIR/" "$BUNDLE/python/"
  PY_COUNT=$(find "$BUNDLE/python" -name "*.py" | wc -l | tr -d ' ')
  echo "✓ Python 策略  $PY_COUNT 个 .py 文件"
else
  echo "⚠ Python 目录 $PYTHON_DIR 不存在，跳过"
fi

# ── 6. 插件元信息 ────────────────────────────────────────────────────────────
for f in openclaw.plugin.json package.json README.md index.ts; do
  if [[ -f "$EXT_DIR/$f" ]]; then
    cp "$EXT_DIR/$f" "$BUNDLE/meta/$f"
  fi
done

# TypeScript 源码也打包（方便完整恢复）
if [[ -d "$EXT_DIR/src" ]]; then
  rsync -a \
    --exclude='*.test.ts' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    "$EXT_DIR/src/" "$BUNDLE/meta/src/"
  echo "✓ TypeScript 源码  meta/src/"
fi

# ── 7. 生成 manifest ────────────────────────────────────────────────────────
cat > "$BUNDLE/MANIFEST.json" <<MANIFEST_EOF
{
  "bundleVersion": 1,
  "exportDate": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "exportHost": "$(hostname -s)",
  "exportUser": "$(whoami)",
  "contents": {
    "config": "插件配置 (openclaw.json)",
    "data": "运行时数据 (signals, stock-list, portfolio, orders …)",
    "cache": "SQLite 缓存数据库 (stock_data.db, financial_data.db)",
    "openclaw-home": "~/.openclaw 目录 (workspace 人设, identity 密钥, cron 定时任务, feishu 状态)",
    "python": "Python 策略/选股/引擎代码",
    "meta": "插件元信息 + TypeScript 源码"
  },
  "importScript": "使用 import-bundle.sh 导入"
}
MANIFEST_EOF

# ── 打包 ─────────────────────────────────────────────────────────────────────
(cd "$STAGING" && tar czf "$OUTPUT" quant-trading-bundle)

SIZE=$(du -h "$OUTPUT" | cut -f1)
echo ""
echo "✅ 导出完成: $OUTPUT ($SIZE)"
echo ""
echo "在目标机器上导入:"
echo "  ./extensions/quant-trading/scripts/import-bundle.sh $OUTPUT"
