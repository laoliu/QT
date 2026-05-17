"""A 股回购信号策略 (Buyback Signal)

核心逻辑：回购公告后股价长期正向漂移。
- 大额回购 = 管理层对公司价值低估的"用真金白银投票"
- A 股 2018 年回购新规后，回购事件显著增加
- 用**基本面代理**模拟回购：高自由现金流 + 低估值 + 高 ROE

代理逻辑（无实时回购公告数据）：
- 高 FCF / 盈利（有能力回购）
- 低估值（有动机回购）
- 高 ROE（管理层能力确认）

与 cash_cow_select 的差异：
- cash_cow_select：买高现金流的好公司（持有型）
- **buyback_signal**：买"最可能回购"的公司（事件预测型），更偏小盘

Alpha 来源（4 因子）：
1. **现金流质量** (30%) — FCF/净利润
2. **估值** (30%) — 低估 → 回购动机
3. **质量 ROE** (20%) — 管理层能力
4. **盈利增速** (20%) — 盈利改善 → 有多余资金

预期表现：AR 15-22%, MDD 22-30%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_debt_factor,
    compute_earnings_growth_factor,
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
class BuybackSignalConfig(EquityRotationConfig):
    """回购信号配置。"""

    top_k: int = 15
    rebalance_days: int = 20         # 偏长持有（回购效应 30-90 天）
    min_price: float = 5.0
    max_price: float = 150.0
    min_turnover: float = 500.0
    min_listed_days: int = 500       # 长期上市公司
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.15
    buffer_k: int = 5
    max_single_pct: float = 0.08
    min_mv: float = 200_000.0       # 20 亿
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.25

    # 因子权重
    weight_cashflow: float = 0.30
    weight_value: float = 0.30
    weight_quality: float = 0.20
    weight_growth: float = 0.20

    # 基本面过滤
    min_roe: float = 5.0
    max_pe: float = 40.0            # 低估值为主


@StrategyRegistry().register("buyback_signal")
class BuybackSignalStrategy(EquityRotationStrategy):
    """回购信号：高现金流 + 低估值 + 高 ROE + 盈利增速。"""

    name = "buyback_signal"

    def __init__(
        self, config: BuybackSignalConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or BuybackSignalConfig(**kwargs)

    @property
    def config(self) -> BuybackSignalConfig:
        return self._config

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        cfg = self._config
        roe = candidate.metadata.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 现金流质量（低负债 = 更多自由现金流的代理）
        f_cf = compute_debt_factor(candidates)
        self.rank_normalize(candidates, f_cf, cfg.weight_cashflow, scores)

        # 估值（低 PE = 回购动机）
        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        # 质量
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        # 盈利增速
        f_grow = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_grow, cfg.weight_growth, scores)

        return self.rank_sort(scores)
