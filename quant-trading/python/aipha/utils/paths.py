"""项目根目录与关键路径的唯一来源。

## 为什么需要这个模块

树里曾散着 45 处 ``Path(__file__).resolve().parents[3]`` / ``.parent.parent
.parent.parent`` 之类的「数层数」写法，**全部偏了一层**。原因有迹可循：
``aipha/runner/daily_utils.py:19`` 一度留着注释 ``# src/aipha/runner → Aipha-Q``
—— 包原先在 ``src/`` 下面，那时 ``parents[3]`` 确实是项目根；后来 ``src/``
被拿掉，包上移一层，所有数层数的常量就都指到了 ``python/`` 的**上一级**
``quant-trading/``。那一级没有 ``configs/``、也没有 ``data/``、没有 ``docker/``。

数层数的写法只要目录结构一动就会静默指错 —— 不报错，只是从此读到空目录。
所以这里不数层数，而是**认路标**：先定位 ``aipha`` 包自己，再往上找第一个
带 ``configs/`` 的目录。

## 影响过什么

最严重的是 Kill Switch：``live_runner`` 检查的是
``quant-trading/configs/kill_switch.flag``，而这个目录根本不存在，于是
**紧急停止开关永远检测不到** —— 运维按文档在项目根建了标志文件，
引擎照常下单。``combo_orchestrator._persist_kill_switch()`` 反过来要往
同一个不存在的目录里**写**这个文件，会直接 FileNotFoundError。

同一个偏移还让整个 ``aipha/governance/`` 包（准入检查、容量闸、上线门禁、
统计门禁、策略分层……）读的是不存在的 ``configs/governance/`` 与
``configs/strategy_catalog.json``；``p4_factors_ml.ALT_CACHE``、
``trading_ops`` 的 §13.9 缓存新鲜度检查、``calendar`` 的交易日历回退
同理。它们不报错，只是永远走「文件不存在」那条分支。

只有 ``logs/`` 和 ``output/`` 在错误的那一层**恰好存在**（Node 侧在用），
所以这两类路径不是读空目录，而是把文件写到了另一棵树里 ——
回测主库 ``output/backtest_index.db`` 就是这么落在 ``quant-trading/`` 下的，
``result_store._resolve_db_path()`` 为此留了一个迁移期回退分支。

## 现状

上述 45 处已全部改为引用本模块。新代码请一律用 ``PROJECT_ROOT`` /
``project_path()``，不要再数层数。
"""

from __future__ import annotations

import os
from pathlib import Path


def _discover_project_root() -> Path:
    """定位项目根目录（含 ``configs/`` 的那一层）。

    优先级：
    1. 环境变量 ``AIPHA_PROJECT_ROOT``（容器里把树挂到 /app 之类的场景）；
    2. 从 ``aipha`` 包的上一级开始向上找第一个含 ``configs/`` 的目录；
    3. 都没找到就退回 ``aipha`` 包的上一级 —— 即「包旁边」，
       这也是仓库当前的真实布局。
    """
    override = os.environ.get("AIPHA_PROJECT_ROOT")
    if override:
        return Path(override).resolve()

    # 本文件位于 <root>/aipha/utils/paths.py → parents[2] 即 aipha 的上一级
    package_parent = Path(__file__).resolve().parents[2]
    for candidate in (package_parent, *package_parent.parents):
        if (candidate / "configs").is_dir():
            return candidate
    return package_parent


PROJECT_ROOT: Path = _discover_project_root()


def project_path(*parts: str) -> Path:
    """拼一个项目根下的路径。``project_path("configs", "x.yml")``。"""
    return PROJECT_ROOT.joinpath(*parts)


# ── 关键路径常量 ──────────────────────────────────────────────
# Kill Switch 物理标志文件（§14.2 L3）。存在即停止一切交易。
# 写方（combo_orchestrator）与读方（live_runner / live_engine_eod /
# deadman_checker / go_live_gate）必须用同一个常量 —— 之前四个地方
# 各算一遍路径，算出两个不同的结果，等于开关根本没接上。
KILL_SWITCH_PATH: Path = PROJECT_ROOT / "configs" / "kill_switch.flag"

CONFIGS_DIR: Path = PROJECT_ROOT / "configs"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
DATA_DIR: Path = PROJECT_ROOT / "data"
