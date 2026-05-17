"""条款博弈可转债策略（Clause Play CB Strategy）

利用可转债回售条款和到期催化效应的"条款博弈"策略。

**核心投资逻辑**：
可转债的回售条款和到期日为持有人提供了确定性的期权价值：
1. 回售触发接近时，发行人被迫下修转股价（概率 70-80%），带来正收益
2. 临近到期（1-3年）时，发行人行动紧迫性骤增，催化事件频率上升
3. 低价叠加条款保护 = 高安全边际 + alpha 弹性

**因子矩阵（4+1 因子，2 层架构）**：
Layer 1 — CB 本体因子：
  1. **价格因子** (close): 低价→债底保护（权重 0.20）
  2. **溢价率因子** (cb_over_rate): 低溢价→股性弹性（权重 0.15）
Layer 3 — 条款博弈因子：
  3. **回售接近度** (putback_proximity): 正股价/转股价→触发线距离（权重 0.30）
  4. **到期催化** (maturity_catalyst): 非单调催化曲线，1-3年高峰（权重 0.25）
Weak：
  5. **发行规模** (issue_size): 小规模→定价偏差（权重 ≤ 0.10）

**经济学理论基础**：
- 回售保护：Tsiveriotis-Fernandes (1998) 将 CB 分解为债权+股权，
  回售条款提供硬性债底 → V = max(V_hold, P_put)
- 下修博弈：发行人理性选择 → 下修成本 < 回售成本时必然下修
- 到期催化：Campbell et al. (2008) 实证 — CB 到期前 2 年 alpha 显著

**与 F1 策略的关系**：
- cb_stock_quality (F1c): Layer 1 + Layer 2 (正股传导)
- cb_gamma_convexity (F1d): Layer 1 + Delta 结构
- cb_clause_play (F2d): Layer 1 + Layer 3 (条款博弈) ← 本策略
- 未来 cb_multi_layer: Layer 1 + Layer 2 + Layer 3 全整合
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.factors.enhanced_putback import (
    compute_clause_play_score,
    compute_putback_from_cb,
)
from aipha.factors.maturity_catalyst import compute_maturity_catalyst
from aipha.monitoring.downward_revision_tracker import DownwardRevisionTracker
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_to_maturity,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class ClausePlayCBConfig(CBRotationConfig):
    """条款博弈策略配置。

    继承 CBRotationConfig 的通用过滤参数（max_price, min_days_to_maturity 等），
    新增条款博弈特有的因子权重和阈值。
    """

    # --- 持仓 ---
    top_k: int = 15
    rebalance_days: int = 5

    # --- 过滤 ---
    max_price: float = 130.0
    max_premium: float = 50.0        # 溢价率上限（%）
    min_age_days: int = 30           # 最少上市天数（排除次新波动）
    max_issue_size: float = float("inf")  # 不做硬性规模截断

    # --- 因子权重 ---
    weight_price: float = 0.20       # Layer 1: 低价保护
    weight_premium: float = 0.15     # Layer 1: 低溢价弹性
    weight_putback: float = 0.30     # Layer 3: 回售接近度
    weight_maturity: float = 0.25    # Layer 3: 到期催化
    weight_issue_size: float = 0.10  # Weak: 小规模偏差

    # --- 回售参数 ---
    putback_trigger_ratio: float = 0.70  # 回售典型触发：正股 ≤ 70% 转股价

    # --- 到期催化增强 ---
    # 允许催化分数低于此阈值的标的被降权（但不直接剔除）
    min_catalyst_score: float = 0.0

    # --- 下修事件因子 ---
    weight_downward_revision: float = 0.15  # 下修事件信号权重（0=禁用）
    revision_lookback_days: int = 180        # 事件有效回望窗口（天）
    revision_recency_halflife: float = 60.0  # 近期衰减半衰期（天）


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_clause_play")
class CBClausePlayStrategy(CBRotationStrategy):
    """条款博弈可转债轮动策略。

    核心逻辑：在低价+低溢价标的中，优选
    (1) 正股价格接近回售触发线（下修概率高）
    (2) 距到期日 1-3 年的催化高峰期标的
    """

    name = "cb_clause_play"

    # --- 初始化 ---

    def __init__(
        self,
        config: ClausePlayCBConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or ClausePlayCBConfig(**kwargs)

    @property
    def config(self) -> ClausePlayCBConfig:
        return self._config

    # --- 额外过滤 ---

    def _extra_filter(self, ci: CandidateInfo, context: StrategyContext) -> bool:
        """过滤高溢价率和次新债。"""
        cfg = self._config

        # 溢价率上限
        if ci.cb_over_rate is not None and ci.cb_over_rate > cfg.max_premium:
            return False

        # 次新排除
        if ci.list_date and cfg.min_age_days > 0:
            now = datetime(
                context.current_time.year,
                context.current_time.month,
                context.current_time.day,
            )
            try:
                ld = datetime.strptime(ci.list_date[:8], "%Y%m%d")
                if (now - ld).days < cfg.min_age_days:
                    return False
            except (ValueError, TypeError):
                pass

        return True

    # --- 核心打分 ---

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """4+1 因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []

        now = datetime(
            context.current_time.year,
            context.current_time.month,
            context.current_time.day,
        )

        # 提取各维度原始值
        prices: list[float] = []
        premiums: list[float] = []
        putbacks: list[float] = []
        catalysts: list[float] = []
        sizes: list[float] = []
        revision_scores: list[float] = []

        # 下修事件查询表（由回测引擎或 paper 引擎注入，key=ts_code）
        revision_lookup: dict[str, dict] = context.metadata.get("downward_revision_lookup", {})
        trade_date_str: str = now.strftime("%Y%m%d")

        for c in candidates:
            prices.append(c.close)
            premiums.append(c.cb_over_rate if c.cb_over_rate is not None else 999.0)

            # 回售接近度：用 cb_value 作为 parity 代理
            pb = compute_putback_from_cb(
                c.cb_value, trigger_ratio=cfg.putback_trigger_ratio,
            )
            putbacks.append(pb if pb is not None else -1.0)

            # 到期催化分数
            dtm = days_to_maturity(c.maturity_date, now) if c.maturity_date else 9999
            cat = compute_maturity_catalyst(dtm)
            catalysts.append(cat)

            sizes.append(c.issue_size if c.issue_size < float("inf") else 1e9)

            # 下修事件分数（§9.1/§9.2）
            rev_score = _compute_revision_score(
                ts_code=c.symbol,
                revision_lookup=revision_lookup,
                trade_date_str=trade_date_str,
                lookback_days=cfg.revision_lookback_days,
                recency_halflife=cfg.revision_recency_halflife,
            )
            revision_scores.append(rev_score)

        # 排名归一化
        # ascending=True: 值越小 → rank 越低(0.0) → 综合分低 → 排序在前 = 最优
        # 所以: "越小越好" 用 ascending=True, "越大越好" 用 ascending=False
        rank_price = _rank_scores(prices, ascending=True)              # 低价好
        rank_premium = _rank_scores(premiums, ascending=True)          # 低溢价好
        rank_putback = _rank_scores(putbacks, ascending=False)         # 高接近度好
        rank_catalyst = _rank_scores(catalysts, ascending=False)       # 高催化好
        rank_size = _rank_scores(sizes, ascending=True)                # 小规模好
        rank_revision = _rank_scores(revision_scores, ascending=False) # 有下修事件好

        # 权重归一化（含下修权重，若 weight=0 则不影响结果）
        total_w = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_putback + cfg.weight_maturity
            + cfg.weight_issue_size + cfg.weight_downward_revision
        )
        if total_w <= 0:
            total_w = 1.0

        w = {
            "pr": cfg.weight_price / total_w,
            "pm": cfg.weight_premium / total_w,
            "pb": cfg.weight_putback / total_w,
            "mc": cfg.weight_maturity / total_w,
            "sz": cfg.weight_issue_size / total_w,
            "rv": cfg.weight_downward_revision / total_w,
        }

        # 计算综合分数
        scored: list[tuple[float, str]] = []
        for i, c in enumerate(candidates):
            score = (
                w["pr"] * rank_price[i]
                + w["pm"] * rank_premium[i]
                + w["pb"] * rank_putback[i]
                + w["mc"] * rank_catalyst[i]
                + w["sz"] * rank_size[i]
                + w["rv"] * rank_revision[i]
            )
            scored.append((score, c.symbol))

        # 按分数升序排列（分数越低越好），取 top_k
        scored.sort(key=lambda x: x[0])
        selected = [sym for _, sym in scored[: cfg.top_k]]

        if selected:
            logger.info(
                f"[{self.name}] 条款博弈选股: "
                f"best={scored[0][0]:.3f} worst={scored[-1][0]:.3f} "
                f"选中={len(selected)}/{len(candidates)}"
            )

        return selected


# ---------------------------------------------------------------------------
# 辅助函数 — §9.2 事件窗口评分
# ---------------------------------------------------------------------------

def _compute_revision_score(
    ts_code: str,
    revision_lookup: dict[str, dict],
    trade_date_str: str,
    lookback_days: int = 180,
    recency_halflife: float = 60.0,
) -> float:
    """计算单只转债的下修事件分数（§9.2 事件窗口统计）。

    评分逻辑（0.0 ~ 1.0）：
    - 无下修事件 → 0.0
    - 有事件：基础分 0.5 + 近期衰减加权（recency）+ 幅度加权（magnitude）
    - 近期性：exp(-elapsed_days / halflife)，越近期越高
    - 幅度：abs(revision_pct)，下修比例越大说明发行人压力越大

    Args:
        ts_code: 转债代码（如 "113556.SH"）
        revision_lookup: 由 DownwardRevisionTracker.build_lookup() 生成
        trade_date_str: 当前交易日 "YYYYMMDD"
        lookback_days: 有效事件回望窗口（天）
        recency_halflife: 近期性指数衰减半衰期（天）

    Returns:
        float in [0.0, 1.0]
    """
    import math
    from datetime import datetime as _dt

    info = revision_lookup.get(ts_code)
    if info is None:
        return 0.0

    pub_str = info.get("publish_date", "")
    if len(pub_str) < 8:
        return 0.0

    try:
        pub_dt = _dt.strptime(pub_str[:8], "%Y%m%d")
        trade_dt = _dt.strptime(trade_date_str[:8], "%Y%m%d")
    except ValueError:
        return 0.0

    elapsed = (trade_dt - pub_dt).days
    if elapsed < 0 or elapsed > lookback_days:
        # 事件在未来或超出回望窗口 → 不算
        return 0.0

    # 近期性衰减因子 [0, 1]
    halflife = max(recency_halflife, 1.0)
    recency = math.exp(-elapsed * math.log(2.0) / halflife)

    # 幅度因子：abs(revision_pct) 通常 0.0 ~ 0.5+，归一化到 [0, 1]
    magnitude = min(abs(info.get("revision_pct", 0.0)), 0.5) / 0.5

    # 综合分：近期性 × (0.6) + 幅度 × (0.4)，基础分 0.5 确保有事件就高于无事件
    score = 0.5 + 0.5 * (0.6 * recency + 0.4 * magnitude)
    return min(score, 1.0)

