"""A 股波动管理价值策略 (Volatility-Managed Value)

核心逻辑：价值选股 + 波动率倒数仓位管理。
- 基础选股：低估值(EP+BP) + 质量(ROE) + 低换手
- 仓位叠加：历史波动率(HV)高 → 降仓；HV低 → 满仓
  （vol-targeting 思想：Moreira & Muir, 2017）

与 low_vol_value 的差异：
- low_vol_value：选低波动的票，但不择时
- **vol_managed_value**：选价值票，但根据**市场波动率**动态调仓

Alpha 来源（4 因子 + 1 仓位管理信号）：
1. **估值 EP** (30%)
2. **估值 BP** (20%)
3. **质量 ROE** (25%)
4. **低换手** (25%)
5. **仓位管理**：20 日 HV > 阈值 → 按 target_vol/HV 缩放

预期表现：AR 15-22%, MDD 15-22%, Sharpe 0.7-1.1
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_quality_factor,
    compute_turnover_factor,
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
class VolManagedValueConfig(EquityRotationConfig):
    """波动管理价值配置。"""

    top_k: int = 30
    rebalance_days: int = 15
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 250
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 500_000.0       # 50 亿
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 因子权重
    weight_ep: float = 0.30
    weight_bp: float = 0.20
    weight_quality: float = 0.25
    weight_turnover: float = 0.25

    # 波动率目标仓位管理
    vol_lookback: int = 20
    target_vol: float = 0.015          # 日波动率目标 1.5%
    min_position_scale: float = 0.30   # 最低仓位比例

    # 基本面
    max_pe: float = 50.0

    # 内部状态
    _index_history: list[float] = field(default_factory=list)


@StrategyRegistry().register("vol_managed_value")
class VolManagedValueStrategy(EquityRotationStrategy):
    """波动管理价值：价值选股 + HV 倒数动态调仓。"""

    name = "vol_managed_value"

    def __init__(
        self, config: VolManagedValueConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or VolManagedValueConfig(**kwargs)

    @property
    def config(self) -> VolManagedValueConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """每 bar 更新指数历史 → HV → position_scale。"""
        cfg = self._config
        bench_price = context.metadata.get("benchmark_close")
        if bench_price is None:
            prices = [b.close for b in bars.values() if b.close > 0]
            bench_price = sum(prices) / len(prices) if prices else 0.0

        cfg._index_history.append(bench_price)
        max_len = cfg.vol_lookback + 10
        if len(cfg._index_history) > max_len:
            del cfg._index_history[:-max_len]

        # 计算已实现波动率
        if len(cfg._index_history) >= cfg.vol_lookback + 1:
            prices = cfg._index_history[-(cfg.vol_lookback + 1):]
            rets = []
            for i in range(1, len(prices)):
                if prices[i - 1] > 0:
                    rets.append((prices[i] - prices[i - 1]) / prices[i - 1])
            if len(rets) >= 2:
                hv = statistics.stdev(rets)
                if hv > 0:
                    scale = cfg.target_vol / hv
                    scale = max(cfg.min_position_scale, min(1.0, scale))
                    context.metadata["position_scale"] = scale
                    return
        # 数据不足时满仓
        context.metadata["position_scale"] = 1.0

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
        """EP + BP + ROE + 低换手。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_ep, scores)

        f_bp = compute_value_factor(candidates, metric="bp")
        self.rank_normalize(candidates, f_bp, cfg.weight_bp, scores)

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_turn = compute_turnover_factor(candidates)
        self.rank_normalize(candidates, f_turn, cfg.weight_turnover, scores)

        return self.rank_sort(scores)
