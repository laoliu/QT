"""A 股现金牛精选策略

核心逻辑：以**自由现金流**(FCF)为核心选股标准。

与其他防御策略的差异：
- 不用股息率/PE/PB 等传统估值 → 用 FCF/市值 和现金流质量
- FCF 比净利润更难操纵（Sloan 1996: CFO 优于 Earnings）
- 高 FCF yield + 高 ROE + 低换手 = "隐形冠军"
- 大中盘定位（80亿+），避免小盘流动性问题

Alpha 来源（4 因子）：
1. **现金流质量** (35%) — CFO/净利润，越高越真实
2. **质量 ROE** (25%) — 高盈利能力
3. **价值 EP** (25%) — 低市盈率确认便宜
4. **低换手率** (15%) — 低关注度溢价

硬性门槛：
- ROE ≥ 8%
- PE_TTM ∈ (0, 35)
- 资产负债率 < 65%
- 流通市值 ≥ 80 亿

预期表现：AR 8-14%, MDD 15-22%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
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
    pass

logger = logging.getLogger(__name__)


@dataclass
class CashCowSelectConfig(EquityRotationConfig):
    """现金牛精选策略配置。"""

    top_k: int = 25
    rebalance_days: int = 20
    min_price: float = 5.0
    max_price: float = 500.0
    min_turnover: float = 1500.0
    min_listed_days: int = 250
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10
    buffer_k: int = 8
    max_single_pct: float = 0.06
    min_mv: float = 800_000.0       # 80 亿以上
    max_mv: float = float("inf")
    inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20

    # 因子权重
    weight_cash_quality: float = 0.35   # 现金流质量（应计质量的反面=好）
    weight_roe: float = 0.25
    weight_value: float = 0.25
    weight_turnover: float = 0.15

    # 硬性过滤
    min_roe: float = 8.0
    max_pe: float = 35.0
    max_debt_to_assets: float = 65.0


@StrategyRegistry().register("cash_cow_select")
class CashCowSelectStrategy(EquityRotationStrategy):
    """A 股现金牛精选策略。

    以自由现金流质量为核心，筛选盈利真实、估值合理的"隐形冠军"。
    """

    name = "cash_cow_select"

    def __init__(
        self, config: CashCowSelectConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or CashCowSelectConfig(**kwargs)

    @property
    def config(self) -> CashCowSelectConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """现金牛硬性过滤。"""
        cfg = self._config
        meta = candidate.metadata

        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        pe = meta.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False

        dta = meta.get("debt_to_assets")
        if dta is not None and dta > cfg.max_debt_to_assets:
            return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：现金流质量 + ROE + EP价值 + 低换手。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 现金流质量（应计质量因子，高=盈利真实）
        # nan_handling="median": 缺失 fina 数据的股票给中位排名分，避免 NaN 污染
        f_cash = compute_accruals_quality_factor(candidates)
        self.rank_normalize(
            candidates, f_cash, cfg.weight_cash_quality, scores,
            nan_handling="median",
        )

        # 2. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(
            candidates, f_roe, cfg.weight_roe, scores,
            nan_handling="median",
        )

        # 3. 价值因子（低 PE）
        f_value = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(
            candidates, f_value, cfg.weight_value, scores,
            nan_handling="median",
        )

        # 4. 低换手率
        f_turn = compute_turnover_factor(candidates)
        self.rank_normalize(
            candidates, f_turn, cfg.weight_turnover, scores,
            nan_handling="median",
        )

        return self.rank_sort(scores)
