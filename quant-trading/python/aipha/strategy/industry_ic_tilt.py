"""A 股行业 IC 倾斜策略 (Industry IC Tilt)

核心逻辑：用因子 IC（信息系数）在行业间倾斜配置。
- 计算各因子在不同行业的截面 IC
- 在因子 IC 高的行业加大配置
- 自适应：行业 alpha 源不同，动态倾斜权重

学术基础：
- Kakushadze (2016) — "#101 Formulaic Alphas"，行业中性化因子
- Grinold & Kahn (1999) — IC 是因子预测能力的核心度量

与 ic_adaptive_multifactor 的差异：
- ic_adaptive_multifactor：因子层面 IC 加权（跨因子）
- **industry_ic_tilt**：行业层面 IC 倾斜（跨行业）

Alpha 来源（4 因子 + IC 行业倾斜）：
1. **质量 ROE** (25%)
2. **价值 EP** (25%)
3. **动量** (25%)
4. **盈利增速** (25%)
5. **行业倾斜**：在因子 IC 高的行业给予更多候选名额

预期表现：AR 15-22%, MDD 22-30%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
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
class IndustryIcTiltConfig(EquityRotationConfig):
    """行业 IC 倾斜配置。"""

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
    max_industry_pct: float = 0.25   # 稍高行业上限

    # 因子权重（基础等权）
    weight_quality: float = 0.25
    weight_value: float = 0.25
    weight_momentum: float = 0.25
    weight_growth: float = 0.25

    # 行业倾斜
    ic_lookback: int = 60            # IC 计算回看期
    tilt_strength: float = 0.30      # 倾斜强度（0=不倾斜，1=完全倾斜）

    max_pe: float = 80.0
    momentum_lookback: int = 60


@StrategyRegistry().register("industry_ic_tilt")
class IndustryIcTiltStrategy(EquityRotationStrategy):
    """行业 IC 倾斜：标准多因子 + 行业间 IC 动态配置。"""

    name = "industry_ic_tilt"

    def __init__(
        self, config: IndustryIcTiltConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or IndustryIcTiltConfig(**kwargs)

    @property
    def config(self) -> IndustryIcTiltConfig:
        return self._config

    def _compute_industry_tilt(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """计算行业倾斜系数：基于行业内动量的平均方向。

        简化版 IC — 用行业内候选股近期收益的一致性衡量
        （正 IC = 因子在该行业有效）。
        """
        cfg = self._config
        industry_returns: dict[str, list[float]] = defaultdict(list)

        for c in candidates:
            industry = c.metadata.get("industry", "unknown")
            hist = self._price_history.get(c.symbol, [])
            lb = cfg.ic_lookback
            if len(hist) >= lb + 1 and hist[-(lb + 1)] > 0:
                ret = (hist[-1] - hist[-(lb + 1)]) / hist[-(lb + 1)]
                industry_returns[industry].append(ret)

        tilt: dict[str, float] = {}
        for ind, rets in industry_returns.items():
            if len(rets) >= 3:
                avg = sum(rets) / len(rets)
                # 一致性 = 正收益比例
                pos_ratio = sum(1 for r in rets if r > 0) / len(rets)
                # 倾斜 = avg * 一致性 → 高且一致的行业获得高倾斜
                tilt[ind] = avg * pos_ratio
            else:
                tilt[ind] = 0.0
        return tilt

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
        """4 因子打分 + 行业倾斜加成。"""
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

        f_grow = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_grow, cfg.weight_growth, scores)

        # 行业倾斜加成
        tilt = self._compute_industry_tilt(candidates)
        if tilt:
            # 归一化倾斜值到 [0, 1]
            t_vals = [v for v in tilt.values() if not math.isnan(v)]
            if t_vals:
                t_min = min(t_vals)
                t_max = max(t_vals)
                t_range = t_max - t_min if t_max > t_min else 1.0

                for c in candidates:
                    ind = c.metadata.get("industry", "unknown")
                    t_val = tilt.get(ind, 0.0)
                    norm_tilt = (t_val - t_min) / t_range
                    bonus = cfg.tilt_strength * norm_tilt
                    scores[c.symbol] = scores.get(c.symbol, 0.0) + bonus

        return self.rank_sort(scores)
