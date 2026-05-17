"""A 股市场宽度控制策略 (Breadth-Controlled)

核心逻辑：多因子选股 + 市场宽度（上涨家数比例）控制仓位。
- 基础选股：综合多因子（质量+价值+动量+规模）
- 仓位叠加：上涨家数占比高 → 满仓（广泛参与）；
  占比低 → 缩仓（市场参与度差，仅少数股票上涨 = 风险）

学术基础：
- Zweig (1986) — breadth-thrust indicator
- Fosback (1976) — advance-decline breadth as market timing
- A 股增强：用候选池内上涨比例（不需要全市场数据）

与 composite_rank_equal 的差异：
- composite_rank_equal：纯排名，无择时
- **breadth_controlled**：同样多因子排名，但叠加市场宽度动态仓位

Alpha 来源（4 因子 + 1 宽度信号）：
1. **质量 ROE** (25%)
2. **估值 EP** (25%)
3. **动量** (25%)
4. **规模** (25%)
5. **宽度信号**：候选池上涨比例 > 60% → 满仓；< 40% → 半仓

预期表现：AR 15-22%, MDD 18-28%, Sharpe 0.6-1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.backtest.engine import Bar

logger = logging.getLogger(__name__)


@dataclass
class BreadthControlledConfig(EquityRotationConfig):
    """市场宽度控制配置。"""

    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0       # 30 亿
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 因子权重（等配 4 因子）
    weight_quality: float = 0.25
    weight_value: float = 0.25
    weight_momentum: float = 0.25
    weight_size: float = 0.25

    # 宽度控制
    breadth_bullish: float = 0.60    # 上涨比例 > 此 → 满仓
    breadth_bearish: float = 0.40    # 上涨比例 < 此 → 缩仓
    bear_scale: float = 0.50         # 缩仓系数
    breadth_lookback: int = 5        # 近 N 天做平滑

    max_pe: float = 80.0
    momentum_lookback: int = 60

    # 内部状态：近 N 天宽度
    _breadth_history: list[float] = field(default_factory=list)


@StrategyRegistry().register("breadth_controlled")
class BreadthControlledStrategy(EquityRotationStrategy):
    """市场宽度控制：多因子选股 + 宽度动态仓位。"""

    name = "breadth_controlled"

    def __init__(
        self, config: BreadthControlledConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or BreadthControlledConfig(**kwargs)

    @property
    def config(self) -> BreadthControlledConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """计算候选池上涨比例 → 设置 position_scale。"""
        cfg = self._config
        # 计算当日上涨比例
        up_count = 0
        total = 0
        for sym, bar in bars.items():
            if bar.close > 0 and bar.open > 0:
                total += 1
                if bar.close >= bar.open:
                    up_count += 1

        breadth = up_count / total if total > 0 else 0.5
        cfg._breadth_history.append(breadth)
        if len(cfg._breadth_history) > cfg.breadth_lookback + 5:
            del cfg._breadth_history[:-(cfg.breadth_lookback + 5)]

        # 平滑处理
        recent = cfg._breadth_history[-cfg.breadth_lookback:]
        avg_breadth = sum(recent) / len(recent) if recent else 0.5

        if avg_breadth >= cfg.breadth_bullish:
            context.metadata["position_scale"] = 1.0
        elif avg_breadth <= cfg.breadth_bearish:
            context.metadata["position_scale"] = cfg.bear_scale
        else:
            # 线性插值
            ratio = (avg_breadth - cfg.breadth_bearish) / (cfg.breadth_bullish - cfg.breadth_bearish)
            context.metadata["position_scale"] = cfg.bear_scale + ratio * (1.0 - cfg.bear_scale)

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > self._config.max_pe):
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """等权 4 因子。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        f_mom = compute_momentum_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.momentum_lookback, risk_adjusted=True,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
