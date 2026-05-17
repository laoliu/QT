"""A 股价值成长切换策略 (Value-Growth Switch)

核心逻辑：根据市场利率/估值环境在价值股和成长股之间切换。
- 低利率/高估值环境 → 成长股跑赢（DCF 贴现率低 → 长久期资产受益）
- 高利率/低估值环境 → 价值股跑赢（安全边际高）
- A 股价值/成长风格切换周期约 1-3 年

代理信号（无利率数据）：
- 用全市场 PE 中位数代理"贵/便宜"
- PE 中位数高 → 成长模式；PE 中位数低 → 价值模式

与 regime_switch 的差异：
- regime_switch：牛/熊切动量/价值权重
- **value_growth_switch**：价值/成长风格切换（不同维度）

Alpha 来源：
- 价值模式（4 因子）：EP(35%) + BP(25%) + ROE(25%) + 低波(15%)
- 成长模式（4 因子）：盈利增速(35%) + 动量(25%) + ROE(25%) + 规模(15%)

预期表现：AR 15-22%, MDD 22-30%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_value_factor,
    compute_volatility_factor,
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
class ValueGrowthSwitchConfig(EquityRotationConfig):
    """价值成长切换配置。"""

    top_k: int = 30
    rebalance_days: int = 20         # 月频
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 价值模式权重
    value_weight_ep: float = 0.35
    value_weight_bp: float = 0.25
    value_weight_quality: float = 0.25
    value_weight_vol: float = 0.15

    # 成长模式权重
    growth_weight_earnings: float = 0.35
    growth_weight_momentum: float = 0.25
    growth_weight_quality: float = 0.25
    growth_weight_size: float = 0.15

    # 风格信号
    pe_median_high: float = 35.0     # PE 中位数 > 此 → 成长模式
    pe_median_low: float = 20.0      # PE 中位数 < 此 → 价值模式

    max_pe: float = 100.0
    momentum_lookback: int = 60

    # 内部状态
    _style_mode: str = "VALUE"


@StrategyRegistry().register("value_growth_switch")
class ValueGrowthSwitchStrategy(EquityRotationStrategy):
    """价值成长切换：根据市场估值水平动态切换风格。"""

    name = "value_growth_switch"

    def __init__(
        self, config: ValueGrowthSwitchConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or ValueGrowthSwitchConfig(**kwargs)

    @property
    def config(self) -> ValueGrowthSwitchConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """用候选池 PE 中位数判断风格。"""
        cfg = self._config
        pe_list = []
        for sym, bar in bars.items():
            meta = getattr(bar, "metadata", {}) or {}
            pe = meta.get("pe_ttm")
            if pe is not None and 0 < pe < 500:
                pe_list.append(pe)

        if pe_list:
            pe_list.sort()
            n = len(pe_list)
            median_pe = pe_list[n // 2] if n % 2 == 1 else (pe_list[n // 2 - 1] + pe_list[n // 2]) / 2
            if median_pe > cfg.pe_median_high:
                cfg._style_mode = "GROWTH"
            elif median_pe < cfg.pe_median_low:
                cfg._style_mode = "VALUE"
            # 否则保持上次状态

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
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        if cfg._style_mode == "VALUE":
            f_ep = compute_value_factor(candidates, metric="ep")
            self.rank_normalize(candidates, f_ep, cfg.value_weight_ep, scores)
            f_bp = compute_value_factor(candidates, metric="bp")
            self.rank_normalize(candidates, f_bp, cfg.value_weight_bp, scores)
            f_roe = compute_quality_factor(candidates, metric="roe")
            self.rank_normalize(candidates, f_roe, cfg.value_weight_quality, scores)
            f_vol = compute_volatility_factor(
                candidates, price_history=self._price_history,
                lookback=cfg.volatility_lookback, invert=True,
            )
            self.rank_normalize(candidates, f_vol, cfg.value_weight_vol, scores)
        else:
            f_grow = compute_earnings_growth_factor(candidates)
            self.rank_normalize(candidates, f_grow, cfg.growth_weight_earnings, scores)
            f_mom = compute_momentum_factor(
                candidates, price_history=self._price_history,
                lookback=cfg.momentum_lookback, risk_adjusted=True,
            )
            self.rank_normalize(candidates, f_mom, cfg.growth_weight_momentum, scores)
            f_roe = compute_quality_factor(candidates, metric="roe")
            self.rank_normalize(candidates, f_roe, cfg.growth_weight_quality, scores)
            f_size = compute_size_factor(candidates)
            self.rank_normalize(candidates, f_size, cfg.growth_weight_size, scores)

        return self.rank_sort(scores)
