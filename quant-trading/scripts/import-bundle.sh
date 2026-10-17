#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────────
# quant-trading import — 从 export-bundle.sh 导出的 tar.gz 恢复配置和数据
#
# 用法:
#   ./extensions/quant-trading/scripts/import-bundle.sh <bundle.tar.gz> [选项]
#
# 选项:
#   --config-only     只恢复配置，不覆盖运行时数据
#   --data-only       只恢复运行时数据，不覆盖配置
#   --python-only     只恢复 Python 策略代码
#   --no-backup       不备份现有文件
#   --dry-run         只显示会做什么，不实际操作
#   --force           不询问确认，直接覆盖
#
# 恢复内容:
#   1. config/openclaw.json → config-custom/openclaw.json (合并 quant-trading 配置)
#   2. data/*              → ~/data/
#   3. cache/*             → ~/data/cache/
#   4. python/*            → extensions/quant-trading/python/
#   5. meta/*              → extensions/quant-trading/ (元信息 + src)
# ────────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXT_DIR/../.." && pwd)"

# ── 参数解析 ─────────────────────────────────────────────────────────────────
BUNDLE_FILE=""
CONFIG_ONLY=false
DATA_ONLY=false
PYTHON_ONLY=false
NO_BACKUP=false
DRY_RUN=false
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --config-only)  CONFIG_ONLY=true ;;
    --data-only)    DATA_ONLY=true ;;
    --python-only)  PYTHON_ONLY=true ;;
    --no-backup)    NO_BACKUP=true ;;
    --dry-run)      DRY_RUN=true ;;
    --force)        FORCE=true ;;
    -*)             echo "未知选项: $arg"; exit 1 ;;
    *)              BUNDLE_FILE="$arg" ;;
  esac
done

if [[ -z "$BUNDLE_FILE" ]]; then
  echo "用法: $0 <bundle.tar.gz> [选项]"
  echo ""
  echo "选项:"
  echo "  --config-only   只恢复配置"
  echo "  --data-only     只恢复运行时数据"
  echo "  --python-only   只恢复 Python 策略代码"
  echo "  --no-backup     不备份现有文件"
  echo "  --dry-run       只显示操作，不实际执行"
  echo "  --force         不询问确认"
  exit 1
fi

if [[ ! -f "$BUNDLE_FILE" ]]; then
  echo "❌ 文件不存在: $BUNDLE_FILE"
  exit 1
fi

# ── 解包 ─────────────────────────────────────────────────────────────────────
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

tar xzf "$BUNDLE_FILE" -C "$STAGING"
BUNDLE="$STAGING/quant-trading-bundle"

if [[ ! -d "$BUNDLE" ]]; then
  echo "❌ 包格式不正确，缺少 quant-trading-bundle 目录"
  exit 1
fi

# 显示 manifest
if [[ -f "$BUNDLE/MANIFEST.json" ]]; then
  echo "📦 Quant Trading Import"
  echo "───────────────────────"
  python3 -c "
import json, sys
m = json.load(open('$BUNDLE/MANIFEST.json'))
print(f\"导出时间: {m.get('exportDate', '?')}\")
print(f\"导出主机: {m.get('exportHost', '?')}\")
print(f\"导出用户: {m.get('exportUser', '?')}\")
print('包含:')
for k, v in m.get('contents', {}).items():
    print(f'  {k}: {v}')
" 2>/dev/null || true
  echo "───────────────────────"
fi

DATE_TAG=$(date +%Y%m%d-%H%M%S)
DATA_DIR="${QUANT_DATA_DIR:-$HOME/data}"

# 确定要恢复哪些部分
RESTORE_CONFIG=true
RESTORE_DATA=true
RESTORE_PYTHON=true
RESTORE_META=true

if $CONFIG_ONLY; then
  RESTORE_DATA=false; RESTORE_PYTHON=false; RESTORE_META=false
elif $DATA_ONLY; then
  RESTORE_CONFIG=false; RESTORE_PYTHON=false; RESTORE_META=false
elif $PYTHON_ONLY; then
  RESTORE_CONFIG=false; RESTORE_DATA=false; RESTORE_META=false
fi

# ── 确认 ─────────────────────────────────────────────────────────────────────
if ! $FORCE && ! $DRY_RUN; then
  echo ""
  echo "将要恢复:"
  $RESTORE_CONFIG && echo "  • 配置文件 → config-custom/openclaw.json"
  $RESTORE_DATA   && echo "  • 运行时数据 → $DATA_DIR"
  $RESTORE_DATA   && echo "  • 缓存数据库 → ~/data/cache/"
  $RESTORE_DATA   && echo "  • ~/.openclaw 目录 (workspace, identity, cron, feishu)"
  $RESTORE_PYTHON && echo "  • Python 策略 → extensions/quant-trading/python/"
  $RESTORE_META   && echo "  • 插件元信息 → extensions/quant-trading/"
  echo ""
  read -rp "确认导入? (y/N) " answer
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "取消导入"
    exit 0
  fi
fi

backup_file() {
  local target="$1"
  if $NO_BACKUP || $DRY_RUN; then return; fi
  if [[ -e "$target" ]]; then
    local bak="${target}.bak-${DATE_TAG}"
    cp -a "$target" "$bak"
    echo "  📂 已备份: $(basename "$target") → $(basename "$bak")"
  fi
}

# ── 1. 恢复配置 ──────────────────────────────────────────────────────────────
if $RESTORE_CONFIG && [[ -d "$BUNDLE/config" ]]; then
  echo ""
  echo "🔧 恢复配置..."

  # 尝试合并 quant-trading 配置到现有 openclaw.json
  if [[ -f "$BUNDLE/config/openclaw.json" ]]; then
    TARGET_CONFIG="$REPO_ROOT/config-custom/openclaw.json"

    if $DRY_RUN; then
      echo "  [dry-run] 会合并配置到 $TARGET_CONFIG"
    else
      mkdir -p "$REPO_ROOT/config-custom"
      if [[ -f "$TARGET_CONFIG" ]]; then
        backup_file "$TARGET_CONFIG"
        # 用 Python 合并: 只替换 plugins.entries.quant-trading 部分
        python3 -c "
import json, re, sys

def load_jsonc(path):
    \"\"\"Load JSON with comments.\"\"\"
    with open(path, 'r') as f:
        text = f.read()
    return text

# 从源包 JSON 中提取 quant-trading 配置
src_text = load_jsonc('$BUNDLE/config/openclaw.json')
# 去掉注释用于解析
src_clean = re.sub(r'//.*$', '', src_text, flags=re.MULTILINE)
src_clean = re.sub(r'/\*.*?\*/', '', src_clean, flags=re.DOTALL)
try:
    src = json.loads(src_clean)
except json.JSONDecodeError:
    print('  ⚠ 源配置解析失败，直接复制整个文件')
    import shutil
    shutil.copy('$BUNDLE/config/openclaw.json', '$TARGET_CONFIG')
    sys.exit(0)

qt_config = src.get('plugins', {}).get('entries', {}).get('quant-trading')
if qt_config:
    # 如果目标也有配置，尝试合并 (带注释的 JSONC 直接覆盖更安全)
    print('  ✓ 检测到 quant-trading 配置，复制完整配置文件')
    import shutil
    shutil.copy('$BUNDLE/config/openclaw.json', '$TARGET_CONFIG')
else:
    print('  ⚠ 源配置中未找到 quant-trading 配置段')
    import shutil
    shutil.copy('$BUNDLE/config/openclaw.json', '$TARGET_CONFIG')
" 2>/dev/null || {
          # Python 解析失败时直接复制
          backup_file "$TARGET_CONFIG"
          cp "$BUNDLE/config/openclaw.json" "$TARGET_CONFIG"
          echo "  ✓ 直接复制配置文件"
        }
      else
        cp "$BUNDLE/config/openclaw.json" "$TARGET_CONFIG"
        echo "  ✓ 配置文件已创建"
      fi
    fi
  fi

  if [[ -f "$BUNDLE/config/openclaw-home.json" ]]; then
    HOME_CONFIG="$HOME/.openclaw/openclaw.json"
    if $DRY_RUN; then
      echo "  [dry-run] 会复制到 $HOME_CONFIG"
    else
      mkdir -p "$HOME/.openclaw"
      backup_file "$HOME_CONFIG"
      cp "$BUNDLE/config/openclaw-home.json" "$HOME_CONFIG"
      echo "  ✓ ~/.openclaw/openclaw.json"
    fi
  fi
fi

# ── 2. 恢复运行时数据 ────────────────────────────────────────────────────────
if $RESTORE_DATA && [[ -d "$BUNDLE/data" ]] && [[ "$(ls -A "$BUNDLE/data" 2>/dev/null)" ]]; then
  echo ""
  echo "📊 恢复运行时数据..."

  if $DRY_RUN; then
    echo "  [dry-run] 会复制到 $DATA_DIR"
    find "$BUNDLE/data" -type f -exec echo "  [dry-run]  {}" \;
  else
    mkdir -p "$DATA_DIR"
    # 逐个文件备份+复制，避免覆盖导入包中没有的文件
    for src_file in "$BUNDLE"/data/*; do
      [[ -f "$src_file" ]] || continue
      fname=$(basename "$src_file")
      target="$DATA_DIR/$fname"
      backup_file "$target"
      cp "$src_file" "$target"
      echo "  ✓ $fname"
    done
    # 递归复制子目录
    for src_dir in "$BUNDLE"/data/*/; do
      [[ -d "$src_dir" ]] || continue
      dname=$(basename "$src_dir")
      mkdir -p "$DATA_DIR/$dname"
      rsync -a "$src_dir" "$DATA_DIR/$dname/"
      echo "  ✓ $dname/"
    done
  fi
fi

# ── 3. 恢复缓存数据库 ─────────────────────────────────────────────────────────
CACHE_TARGET="$HOME/data/cache"
if $RESTORE_DATA && [[ -d "$BUNDLE/cache" ]] && [[ "$(ls -A "$BUNDLE/cache" 2>/dev/null)" ]]; then
  echo ""
  echo "💾 恢复缓存数据库..."

  if $DRY_RUN; then
    echo "  [dry-run] 会复制到 $CACHE_TARGET"
    find "$BUNDLE/cache" -type f -exec echo "  [dry-run]  {}" \;
  else
    mkdir -p "$CACHE_TARGET"
    for src_file in "$BUNDLE"/cache/*; do
      [[ -f "$src_file" ]] || continue
      fname=$(basename "$src_file")
      target="$CACHE_TARGET/$fname"
      backup_file "$target"
      cp "$src_file" "$target"
      fsize=$(du -h "$src_file" | cut -f1)
      echo "  ✓ $fname ($fsize)"
    done
  fi
fi

# ── 3.5 恢复 ~/.openclaw 其他目录 ────────────────────────────────────────────
if $RESTORE_DATA && [[ -d "$BUNDLE/openclaw-home" ]] && [[ "$(ls -A "$BUNDLE/openclaw-home" 2>/dev/null)" ]]; then
  echo ""
  echo "🏠 恢复 ~/.openclaw 数据..."

  OPENCLAW_HOME="$HOME/.openclaw"

  # workspace/ — AI 人设
  if [[ -d "$BUNDLE/openclaw-home/workspace" ]]; then
    if $DRY_RUN; then
      echo "  [dry-run] workspace/ → $OPENCLAW_HOME/workspace/"
    else
      mkdir -p "$OPENCLAW_HOME/workspace"
      if [[ -d "$OPENCLAW_HOME/workspace" ]] && ! $NO_BACKUP; then
        for f in "$OPENCLAW_HOME/workspace"/*.md; do
          [[ -f "$f" ]] && backup_file "$f"
        done
      fi
      rsync -a --exclude='.DS_Store' "$BUNDLE/openclaw-home/workspace/" "$OPENCLAW_HOME/workspace/"
      WS_COUNT=$(find "$OPENCLAW_HOME/workspace" -type f -name "*.md" | wc -l | tr -d ' ')
      echo "  ✓ workspace/ ($WS_COUNT 个文件)"
    fi
  fi

  # identity/ — 设备密钥
  if [[ -d "$BUNDLE/openclaw-home/identity" ]]; then
    if $DRY_RUN; then
      echo "  [dry-run] identity/ → $OPENCLAW_HOME/identity/"
    else
      mkdir -p "$OPENCLAW_HOME/identity"
      backup_file "$OPENCLAW_HOME/identity/device.json"
      cp -R "$BUNDLE/openclaw-home/identity"/. "$OPENCLAW_HOME/identity/"
      echo "  ✓ identity/ (设备密钥)"
    fi
  fi

  # cron/ — 定时任务
  if [[ -d "$BUNDLE/openclaw-home/cron" ]]; then
    if $DRY_RUN; then
      echo "  [dry-run] cron/ → $OPENCLAW_HOME/cron/"
    else
      mkdir -p "$OPENCLAW_HOME/cron"
      backup_file "$OPENCLAW_HOME/cron/jobs.json"
      cp -R "$BUNDLE/openclaw-home/cron"/. "$OPENCLAW_HOME/cron/"
      echo "  ✓ cron/ (定时任务)"
    fi
  fi

  # feishu/ — 飞书去重
  if [[ -d "$BUNDLE/openclaw-home/feishu" ]]; then
    if $DRY_RUN; then
      echo "  [dry-run] feishu/ → $OPENCLAW_HOME/feishu/"
    else
      mkdir -p "$OPENCLAW_HOME/feishu"
      rsync -a --exclude='.DS_Store' "$BUNDLE/openclaw-home/feishu/" "$OPENCLAW_HOME/feishu/"
      echo "  ✓ feishu/ (去重状态)"
    fi
  fi
fi

# ── 4. 恢复 Python 策略 ──────────────────────────────────────────────────────
if $RESTORE_PYTHON && [[ -d "$BUNDLE/python" ]] && [[ "$(ls -A "$BUNDLE/python" 2>/dev/null)" ]]; then
  echo ""
  echo "🐍 恢复 Python 策略..."

  PYTHON_TARGET="$EXT_DIR/python"
  if $DRY_RUN; then
    echo "  [dry-run] 会同步到 $PYTHON_TARGET"
    find "$BUNDLE/python" -name "*.py" -exec echo "  [dry-run]  {}" \;
  else
    if [[ -d "$PYTHON_TARGET" ]] && ! $NO_BACKUP; then
      # 备份整个 python 目录
      PYTHON_BAK="${PYTHON_TARGET}.bak-${DATE_TAG}"
      cp -aR "$PYTHON_TARGET" "$PYTHON_BAK"
      echo "  📂 已备份: python/ → python.bak-${DATE_TAG}/"
    fi
    rsync -a \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      "$BUNDLE/python/" "$PYTHON_TARGET/"
    PY_COUNT=$(find "$PYTHON_TARGET" -name "*.py" | wc -l | tr -d ' ')
    echo "  ✓ 已恢复 $PY_COUNT 个 .py 文件"
  fi
fi

# ── 5. 恢复元信息 + TypeScript 源码 ──────────────────────────────────────────
if $RESTORE_META && [[ -d "$BUNDLE/meta" ]] && [[ "$(ls -A "$BUNDLE/meta" 2>/dev/null)" ]]; then
  echo ""
  echo "📋 恢复插件元信息..."

  if $DRY_RUN; then
    echo "  [dry-run] 会复制到 $EXT_DIR"
  else
    for f in openclaw.plugin.json package.json README.md index.ts; do
      if [[ -f "$BUNDLE/meta/$f" ]]; then
        backup_file "$EXT_DIR/$f"
        cp "$BUNDLE/meta/$f" "$EXT_DIR/$f"
        echo "  ✓ $f"
      fi
    done

    if [[ -d "$BUNDLE/meta/src" ]]; then
      if [[ -d "$EXT_DIR/src" ]] && ! $NO_BACKUP; then
        SRC_BAK="${EXT_DIR}/src.bak-${DATE_TAG}"
        cp -aR "$EXT_DIR/src" "$SRC_BAK"
        echo "  📂 已备份: src/ → src.bak-${DATE_TAG}/"
      fi
      rsync -a "$BUNDLE/meta/src/" "$EXT_DIR/src/"
      echo "  ✓ TypeScript 源码"
    fi
  fi
fi

# ── 完成 ─────────────────────────────────────────────────────────────────────
echo ""
if $DRY_RUN; then
  echo "🔍 Dry-run 完成，未做任何更改"
else
  echo "✅ 导入完成！"
  echo ""
  echo "后续步骤:"
  echo "  1. 检查 config-custom/openclaw.json 中的配置（API密钥等可能需要更新）"
  echo "  2. cd extensions/quant-trading/python && pip install -r requirements.txt"
  echo "  3. pnpm install  (安装 Node 依赖)"
  echo "  4. pnpm build    (重新构建)"
fi
