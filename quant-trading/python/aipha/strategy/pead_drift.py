"""A 股业绩漂移策略 (Post-Earnings Announcement Drift)

核心逻辑：PEAD 效应 — 业绩超预期后股价持续漂移 20-60 天。
- 用**盈利增速排名**代理"超预期"（无实时公告 feed 的替代方案）
- 叠加短期动量确认 + 低估值安全垫

与 earnings_surprise_alpha 的差异：
- earnings_surprise_alpha：盈利增速 + 短期反转（逆向）
- **pead_drift**：盈利增速 + **短期动量（顺向）**+ 低估值
  回调买入 vs 趋势跟随的区别

学术基础：
- Ball & Brown (1968) — PEAD 效应
- Bernard & Thomas (1989) — A 股 PEAD 延续约 60 天

Alpha 来源（4 因子）：
1. **盈利增速** (40%) — PEAD 核心
2. **动量** (25%) — 短期趋势确认（已在漂移中）
3. **估值** (20%) — EP 安全垫
4. **质量** (15%) — ROE 排除垃圾股

预期表现：AR 18-25%, MDD 25-35%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_quality_factor,
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
    pass

logger = logging.getLogger(__name__)


@dataclass
class PeadDriftConfig(EquityRotationConfig):
    """业绩漂移配置。"""

    top_k: int = 20
    rebalance_days: int = 10         # 短周期跟踪漂移
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 800.0      # 高流动性（PEAD 在高关注度票更明显）
    min_listed_days: int = 250       # 有财报历史
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 5
    max_single_pct: float = 0.06
    min_mv: float = 300_000.0       # 30 亿
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.25

    # 因子权重
    weight_earnings: float = 0.40
    weight_momentum: float = 0.25
    weight_value: float = 0.20
    weight_quality: float = 0.15

    momentum_lookback: int = 20      # 短期动量（捕捉漂移中的趋势）
    max_pe: float = 60.0


@StrategyRegistry().register("pead_drift")
class PeadDriftStrategy(EquityRotationStrategy):
    """业绩漂移：盈利增速 + 动量确认 + 估值安全垫。"""

    name = "pead_drift"

    def __init__(
        self, config: PeadDriftConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or PeadDriftConfig(**kwargs)

    @property
    def config(self) -> PeadDriftConfig:
        return self._config

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

        f_earn = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_earn, cfg.weight_earnings, scores)

        f_mom = compute_momentum_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.momentum_lookback, risk_adjusted=False,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        return self.rank_sort(scores)
