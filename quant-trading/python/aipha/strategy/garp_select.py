"""A 股 GARP（Growth at Reasonable Price）选股策略

Peter Lynch 经典理念：以合理价格买入成长股。
核心指标 PEG = PE / 盈利增速，PEG < 1 为"便宜的成长"。

与 quality_growth 的差异：
- **PEG 思路**：不是分两阶段筛+排，而是直接用 PEG 排名作为核心因子
- **成长+价值平衡**：避免 quality_growth 的纯成长偏向（2016/2018 崩溃）
- **更宽 PE 容忍度**：允许 PE 到 60（成长股估值较高但 PEG 合理）

Alpha 来源（4 因子）：
1. **PEG 倒数** (35%) — 低 PEG = 成长性价比高
2. **质量 ROE** (25%) — 高盈利能力锚定
3. **盈利加速** (25%) — 二阶导：增速在提升的更好
4. **动量确认** (15%) — 排除"估值合理但趋势下行"

硬性门槛：
- PE_TTM ∈ (0, 60)
- 营收/利润增速 > 0（必须有正增长才有 PEG 意义）
- ROE ≥ 5%
- 流通市值 ≥ 30 亿

预期表现：AR 12-18%, MDD 22-30%, Sharpe 0.5-0.9
"""

from __future__ import annotations

import logging
import math
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
class GARPSelectConfig(EquityRotationConfig):
    """GARP 选股配置。"""

    top_k: int = 30
    rebalance_days: int = 15
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.20

    # 因子权重
    weight_peg: float = 0.35         # PEG 倒数（核心）
    weight_quality: float = 0.25
    weight_growth: float = 0.25      # 盈利加速
    weight_momentum: float = 0.15

    # 硬性过滤
    max_pe: float = 60.0
    min_roe: float = 5.0
    min_profit_growth: float = 0.0   # 利润增速 > 0%

    # 因子参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True


@StrategyRegistry().register("garp_select")
class GARPSelectStrategy(EquityRotationStrategy):
    """GARP 选股策略：以合理价格买入成长股。"""

    name = "garp_select"

    def __init__(
        self, config: GARPSelectConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or GARPSelectConfig(**kwargs)

    @property
    def config(self) -> GARPSelectConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """GARP 硬性过滤：有正增长 + 合理估值。"""
        cfg = self._config
        meta = candidate.metadata

        pe = meta.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False

        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # 利润增速必须为正（PEG 才有意义）
        # dt_netprofit_yoy: 扣非净利润同比增长率（TuShare fina_indicator 标准字段）
        growth = meta.get("dt_netprofit_yoy")
        if growth is not None and growth < cfg.min_profit_growth:
            return False

        return True

    def _compute_peg_factor(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """计算 PEG 倒数因子：1/PEG = Growth/PE，越高越好。"""
        result: dict[str, float] = {}
        for c in candidates:
            pe = c.metadata.get("pe_ttm")
            growth = c.metadata.get("dt_netprofit_yoy")
            if (
                pe is not None
                and growth is not None
                and math.isfinite(pe)
                and math.isfinite(growth)
                and pe > 0
                and growth > 0
            ):
                # PEG = PE / Growth%, 我们要 1/PEG = Growth% / PE
                result[c.symbol] = growth / pe
            else:
                result[c.symbol] = float("nan")
        return result

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：PEG倒数 + ROE + 盈利加速 + 动量。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. PEG 倒数（核心）
        # nan_handling="median": 缺失 PE 或增速的股票给中位排名分
        f_peg = self._compute_peg_factor(candidates)
        self.rank_normalize(
            candidates, f_peg, cfg.weight_peg, scores,
            nan_handling="median",
        )

        # 2. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(
            candidates, f_roe, cfg.weight_quality, scores,
            nan_handling="median",
        )

        # 3. 盈利加速
        f_growth = compute_earnings_growth_factor(candidates)
        self.rank_normalize(
            candidates, f_growth, cfg.weight_growth, scores,
            nan_handling="median",
        )

        # 4. 动量确认
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(
            candidates, f_mom, cfg.weight_momentum, scores,
            nan_handling="median",
        )

        return self.rank_sort(scores)
