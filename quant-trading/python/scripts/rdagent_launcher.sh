#!/bin/bash
# =============================================================================
# RD-Agent 一键启动脚本（在 WSL2 Ubuntu-24.04 内运行）
# 用法:
#   从 Windows: wsl -d Ubuntu-24.04 -- bash /mnt/j/work/Aipha-Q/scripts/rdagent_launcher.sh [csi300|csi1000]
#   从 WSL2:    bash /mnt/j/work/Aipha-Q/scripts/rdagent_launcher.sh [csi300|csi1000]
# =============================================================================
set -euo pipefail

MARKET="${1:-csi300}"
WORKSPACE="/root/rdagent_workspace"
ENV_FILE="${WORKSPACE}/.env"
OUTPUT_DIR="${WORKSPACE}/output_${MARKET}_$(date +%Y%m%d_%H%M%S)"
AIPHA_Q="/mnt/j/work/Aipha-Q"

echo "========================================"
echo "  RD-Agent Factor Mining Launcher"
echo "  Market: ${MARKET}"
echo "  Output: ${OUTPUT_DIR}"
echo "========================================"

# ── 前置检查 ──────────────────────────────────────────
check_prereqs() {
    echo "[1/5] 检查前置条件..."

    # Conda
    if [ ! -f /opt/miniconda3/etc/profile.d/conda.sh ]; then
        echo "ERROR: Miniconda 未安装. 请先运行环境搭建."
        exit 1
    fi
    source /opt/miniconda3/etc/profile.d/conda.sh
    conda activate rdagent

    # rdagent
    if ! command -v rdagent &>/dev/null; then
        echo "ERROR: rdagent 未安装. 请在 rdagent conda 环境中安装."
        exit 1
    fi
    echo "  rdagent $(pip show rdagent 2>/dev/null | grep Version | awk '{print $2}')"

    # Docker
    service docker start 2>/dev/null || true
    if ! docker info &>/dev/null; then
        echo "ERROR: Docker 不可用. 请检查 Docker Engine."
        exit 1
    fi
    echo "  Docker $(docker --version | awk '{print $3}')"

    # .env
    if [ ! -f "${ENV_FILE}" ]; then
        echo "ERROR: ${ENV_FILE} 不存在. 请先创建配置."
        exit 1
    fi
    if grep -q "YOUR_DEEPSEEK_API_KEY_HERE" "${ENV_FILE}"; then
        echo "ERROR: .env 中 DEEPSEEK_API_KEY 未填写."
        echo "  请编辑 ${ENV_FILE} 填入有效的 API Key."
        exit 1
    fi
    echo "  .env 配置已加载"
}

# ── 更新 .env 市场配置 ────────────────────────────────
update_market_config() {
    echo "[2/5] 配置市场: ${MARKET}..."
    local bench
    case "${MARKET}" in
        csi300)  bench="SH000300" ;;
        csi1000) bench="SH000852" ;;
        csi500)  bench="SH000905" ;;
        *)
            echo "ERROR: 不支持的市场 ${MARKET}. 支持: csi300, csi1000, csi500"
            exit 1
            ;;
    esac
    # 使用 sed 更新 .env
    sed -i "s/^QLIB_QUANT_market=.*/QLIB_QUANT_market=${MARKET}/" "${ENV_FILE}"
    sed -i "s/^QLIB_QUANT_benchmark=.*/QLIB_QUANT_benchmark=${bench}/" "${ENV_FILE}"
    echo "  market=${MARKET}, benchmark=${bench}"
}

# ── 创建输出目录 ──────────────────────────────────────
prepare_output() {
    echo "[3/5] 准备输出目录..."
    mkdir -p "${OUTPUT_DIR}"
    echo "  ${OUTPUT_DIR}"
}

# ── 运行 RD-Agent ─────────────────────────────────────
run_rdagent() {
    echo "[4/5] 启动 RD-Agent fin_factor..."
    echo "  evolving_n=10 (默认10轮进化)"
    echo "  开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    cd "${WORKSPACE}"
    # dotenv 加载 .env 然后运行 rdagent
    python -c "
from dotenv import load_dotenv
load_dotenv('${ENV_FILE}')
" 2>/dev/null || true

    # 使用 dotenv 方式运行
    set -a
    source "${ENV_FILE}"
    set +a

    export LOG_TRACE_PATH="${OUTPUT_DIR}/logs"
    mkdir -p "${LOG_TRACE_PATH}"

    rdagent fin_factor 2>&1 | tee "${OUTPUT_DIR}/rdagent_run.log"

    echo ""
    echo "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
}

# ── 后处理：复制结果到 Aipha-Q ────────────────────────
post_process() {
    echo "[5/5] 后处理..."

    # 查找最新的 session 输出
    local latest_session
    latest_session=$(find "${LOG_TRACE_PATH}" -name "__session__" -type d 2>/dev/null | head -1)

    if [ -n "${latest_session}" ]; then
        local dest="${AIPHA_Q}/output/rdagent_${MARKET}_$(date +%Y%m%d)"
        mkdir -p "${dest}"
        cp -r "${latest_session}" "${dest}/" 2>/dev/null || true
        echo "  结果已复制到: ${dest}"
    fi

    echo ""
    echo "========================================"
    echo "  RD-Agent 运行完成!"
    echo "  输出目录: ${OUTPUT_DIR}"
    echo "  下一步: 运行管道对接脚本"
    echo "    python scripts/rdagent_pipeline.py --input ${OUTPUT_DIR}"
    echo "========================================"
}

# ── 主流程 ────────────────────────────────────────────
check_prereqs
update_market_config
prepare_output
run_rdagent
post_process
