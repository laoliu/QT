"""A 股量价突破策略 (Volume-Price Breakout)

核心逻辑：趋势跟随 + 量价确认。
- 在 A 股中，新高突破+放量确认的股票在短期（20-40 天）有显著动量
- 结合基本面质量过滤排除纯题材炒作
- 较高换手率 → 需更严格的成本控制

学术基础：
- Jegadeesh & Titman (1993) — 中期动量
- Gervais & Kaniel (2001) — 成交量与预期收益
- A 股增强：动量 + 放量 + 质量过滤

与 momentum_acceleration 的差异：
- momentum_acceleration：盈利加速 + 动量（基本面驱动）
- **volume_price_breakout**：价格新高 + 放量（技术面驱动）+ 质量保底

Alpha 来源（4 因子）：
1. **动量** (35%) — 60 天风险调整动量
2. **放量** (25%) — 近期成交量 vs 长期均量
3. **质量** (20%) — ROE 底线确认
4. **估值** (20%) — 不追过贵的票

预期表现：AR 20-30%, MDD 30-40%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
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
class VolumePriceBreakoutConfig(EquityRotationConfig):
    """量价突破配置。"""

    top_k: int = 25
    rebalance_days: int = 10
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 800.0      # 高流动性要求
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15      # 偏紧止损（趋势策略）
    trailing_stop_pct: float = 0.12
    buffer_k: int = 6
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0        # 30 亿
    max_mv: float = 5_000_000.0      # 500 亿
    max_industry_pct: float = 0.20

    # 因子权重
    weight_momentum: float = 0.35
    weight_volume: float = 0.25
    weight_quality: float = 0.20
    weight_value: float = 0.20

    # 动量参数
    momentum_lookback: int = 60

    # 放量参数
    volume_short_window: int = 5
    volume_long_window: int = 60
    min_volume_ratio: float = 1.2    # 短期均量 / 长期均量 ≥ 1.2

    # 估值
    max_pe: float = 60.0


@StrategyRegistry().register("volume_price_breakout")
class VolumePriceBreakoutStrategy(EquityRotationStrategy):
    """量价突破：动量 + 放量 + 质量 + 估值。"""

    name = "volume_price_breakout"

    def __init__(
        self, config: VolumePriceBreakoutConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or VolumePriceBreakoutConfig(**kwargs)

    @property
    def config(self) -> VolumePriceBreakoutConfig:
        return self._config

    def _compute_volume_factor(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """放量因子：近 N 天成交量均值 / 长期成交量均值。"""
        cfg = self._config
        result: dict[str, float] = {}
        for c in candidates:
            vol_hist = self._volume_history.get(c.symbol, [])
            short_n = cfg.volume_short_window
            long_n = cfg.volume_long_window
            if len(vol_hist) < long_n:
                result[c.symbol] = float("nan")
                continue
            short_avg = sum(vol_hist[-short_n:]) / short_n
            long_avg = sum(vol_hist[-long_n:]) / long_n
            if long_avg > 0:
                ratio = short_avg / long_avg
                result[c.symbol] = ratio
            else:
                result[c.symbol] = float("nan")
        return result

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """过滤：PE 正且 ≤ 上限 + 放量比 ≥ 阈值。"""
        cfg = self._config
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        # 放量过滤通过 scoring 中进行（因为需要 volume history）
        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：动量 + 放量 + 质量 + 估值。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 动量（风险调整）
        f_mom = compute_momentum_factor(
            candidates,
            price_history=self._price_history,
            lookback=cfg.momentum_lookback,
            risk_adjusted=True,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        # 2. 放量因子
        f_vol = self._compute_volume_factor(candidates)
        # 额外过滤：放量比低于阈值的标记为 NaN
        for c in candidates:
            sym = c.symbol
            raw = f_vol.get(sym, float("nan"))
            if not math.isnan(raw) and raw < cfg.min_volume_ratio:
                f_vol[sym] = float("nan")
        self.rank_normalize(candidates, f_vol, cfg.weight_volume, scores)

        # 3. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        # 4. 估值 EP
        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        return self.rank_sort(scores)
