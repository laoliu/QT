"""A 股动量加速策略

核心逻辑：捕获**动量加速**阶段 — 股价已上涨 + 盈利在改善 + 成交量在放大。

与纯动量(momentum.py)的差异：
- 不是简单 Ret(60d) 排序 → 结合盈利加速确认（避免无基本面支撑的炒作）
- 成交量确认：量能放大 = 机构在进场
- 风险调整动量 Ret/σ 替代原始收益率

Alpha 来源（4 因子）：
1. **风险调整动量** (35%) — Ret(60d)/σ(60d)，skip 5d
2. **盈利加速** (30%) — 利润增速的二阶导
3. **成交量变化** (20%) — 近20日成交额/60日均值
4. **质量 ROE** (15%) — 底线过滤

预期表现：AR 18-30%, MDD 30-45%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_quality_factor,
    compute_risk_adjusted_momentum,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class MomentumAccelerationConfig(EquityRotationConfig):
    """动量加速策略配置。"""

    top_k: int = 30
    rebalance_days: int = 10         # 较短周期，追动量
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 1000.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.20

    # 因子权重
    weight_momentum: float = 0.35
    weight_growth: float = 0.30
    weight_volume: float = 0.20
    weight_quality: float = 0.15

    # 因子参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    volatility_lookback: int = 60
    volume_short: int = 20
    volume_long: int = 60

    # 宽松过滤
    max_pe: float = 80.0
    min_roe: float = 3.0


@StrategyRegistry().register("momentum_acceleration")
class MomentumAccelerationStrategy(EquityRotationStrategy):
    """动量加速策略：动量 + 盈利改善 + 量能放大。"""

    name = "momentum_acceleration"

    def __init__(
        self, config: MomentumAccelerationConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or MomentumAccelerationConfig(**kwargs)

    @property
    def config(self) -> MomentumAccelerationConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        cfg = self._config
        meta = candidate.metadata
        pe = meta.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        return True

    def _compute_volume_factor(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """成交量变化因子：近期成交额 / 长期均值。"""
        cfg = self._config
        result: dict[str, float] = {}
        for c in candidates:
            hist = self._amount_history.get(c.symbol, [])
            if len(hist) < cfg.volume_long:
                result[c.symbol] = float("nan")
                continue
            short_avg = sum(hist[-cfg.volume_short:]) / cfg.volume_short
            long_avg = sum(hist[-cfg.volume_long:]) / cfg.volume_long
            if long_avg > 0:
                result[c.symbol] = short_avg / long_avg
            else:
                result[c.symbol] = float("nan")
        return result

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：风险调整动量 + 盈利加速 + 量能 + 质量。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 风险调整动量
        f_mom = compute_risk_adjusted_momentum(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            vol_lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        # 2. 盈利加速
        f_growth = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_growth, cfg.weight_growth, scores)

        # 3. 成交量变化
        f_vol = self._compute_volume_factor(candidates)
        self.rank_normalize(candidates, f_vol, cfg.weight_volume, scores)

        # 4. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        return self.rank_sort(scores)
