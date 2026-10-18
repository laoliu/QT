#!/usr/bin/env bash
# 模拟盘容器入口：导出环境 → 安装 crontab → 前台跑 cron
#
# ── 为什么第一件事是导出环境 ──────────────────────────────────
# cron 的任务不继承容器的环境变量（PID 1 的 env 只属于 PID 1），也不读
# login shell 的 profile。docker-compose 里配的 TUSHARE_TOKEN /
# FEISHU_WEBHOOK_URL 之类对 cron 任务来说等于不存在：数据拉不到、告警发
# 不出，而且失败是静默的 —— 日志里只有一行 ImportError 或「token 为空」。
#
# 标准解法是把容器环境写进 /etc/environment，cron 的 PAM 会读它。
# 顺序很重要：**必须在 `cron -f` 之前**写完，否则第一批任务跑在空环境里。
set -euo pipefail

log() { echo "[entrypoint] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

APP_DIR="${APP_DIR:-/app}"
CRONTAB_FILE="${CRONTAB_FILE:-${APP_DIR}/docker/paper/crontab}"

# ---------------------------------------------------------------------------
# 1. 导出容器环境给 cron
# ---------------------------------------------------------------------------
# 白名单式导出：只导出任务真正需要的变量。全量 `env` 倒进
# /etc/environment 会把 HOSTNAME / PWD / 各种 _ 开头的临时变量也写进去，
# 还可能把带换行的值写坏整个文件。
ENV_KEYS=(
  PYTHONPATH
  PYTHONUNBUFFERED
  TZ
  AIPHA_PROJECT_ROOT
  TUSHARE_TOKEN
  AIPHA_PIT_SNAPSHOT_KEY
  AKSHARE_TOKEN
  FEISHU_WEBHOOK_URL
  FEISHU_CHAT_ID
  FEISHU_APP_ID
  FEISHU_APP_SECRET
  PAPER_PROFILE
  AIPHA_ENV
)

log "导出容器环境到 /etc/environment（cron 任务靠它拿 TUSHARE_TOKEN 等）"
: > /etc/environment
for key in "${ENV_KEYS[@]}"; do
  value="${!key-}"
  if [[ -n "${value}" ]]; then
    # 值里若含换行会写坏 /etc/environment，直接跳过并告警
    if [[ "${value}" == *$'\n'* ]]; then
      log "警告: ${key} 含换行，跳过导出"
      continue
    fi
    printf '%s="%s"\n' "${key}" "${value}" >> /etc/environment
    if [[ "${key}" == *TOKEN* || "${key}" == *SECRET* || "${key}" == *WEBHOOK* || "${key}" == *KEY* ]]; then
      log "  ${key}=<已设置，长度 ${#value}>"   # 凭据不进日志
    else
      log "  ${key}=${value}"
    fi
  else
    log "  ${key}=<未设置>"
  fi
done
chmod 600 /etc/environment

# ---------------------------------------------------------------------------
# 2. 准备日志目录
# ---------------------------------------------------------------------------
# crontab 里每条任务都重定向到 /app/logs/cron/*.log；目录不存在的话
# cron 会把整条命令判为失败，而且失败信息只留在容器 stderr 里。
mkdir -p "${APP_DIR}/logs/cron" "${APP_DIR}/logs/audit" "${APP_DIR}/logs/ops"

# ---------------------------------------------------------------------------
# 3. 安装 crontab
# ---------------------------------------------------------------------------
if [[ ! -f "${CRONTAB_FILE}" ]]; then
  log "错误: crontab 文件不存在: ${CRONTAB_FILE}"
  exit 1
fi
log "安装 crontab: ${CRONTAB_FILE}"
crontab "${CRONTAB_FILE}"
crontab -l | sed 's/^/[crontab] /'

# ---------------------------------------------------------------------------
# 4. 自检（不阻塞启动，只把结论写进日志）
# ---------------------------------------------------------------------------
log "自检: python -c 'import aipha'"
if python -c "import aipha, sys; print('aipha ok', sys.version.split()[0])"; then
  log "自检通过"
else
  log "警告: aipha 导入失败 —— cron 任务大概率也会失败，请检查 PYTHONPATH 与挂载"
fi

# 传了命令就执行它（调试用：docker compose run paper bash）
if [[ $# -gt 0 ]]; then
  log "执行传入命令: $*"
  exec "$@"
fi

# ---------------------------------------------------------------------------
# 5. 前台运行 cron（PID 1，容器随之存活）
# ---------------------------------------------------------------------------
log "启动 cron -f"
exec cron -f
