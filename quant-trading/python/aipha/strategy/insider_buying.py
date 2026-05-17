"""A 股内部人增持策略 (Insider Buying)

核心逻辑：公司内部人（高管/大股东）增持 = 最强看涨信号之一。
- Lakonishok & Lee (2001)："insiders' purchases predict future returns"
- A 股高管增持后 30-90 天平均超额收益 3-5%

代理方式（无实时增持数据）：
- 内部人增持的公司通常具有：低估值 + 业绩改善 + 低杠杆 + 小市值
- 用这 4 个特征预测"最可能被增持"的公司

与 buyback_signal 的差异：
- buyback_signal：高现金流 + 低估值（公司行为代理）
- **insider_buying**：盈利改善 + 低杠杆 + 小盘（高管行为代理）

Alpha 来源（4 因子）：
1. **估值** (30%) — 低估 → 内部人认为被低估
2. **盈利增速** (25%) — 业绩改善期内部人更倾向增持
3. **低杠杆** (25%) — 低负债，公司安全
4. **规模** (20%) — 小公司信息不对称更大

预期表现：AR 15-22%, MDD 25-35%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_debt_factor,
    compute_earnings_growth_factor,
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
    pass

logger = logging.getLogger(__name__)


@dataclass
class InsiderBuyingConfig(EquityRotationConfig):
    """内部人增持配置。"""

    top_k: int = 15
    rebalance_days: int = 20         # 偏长持有
    min_price: float = 5.0
    max_price: float = 150.0
    min_turnover: float = 300.0
    min_listed_days: int = 500
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.15
    buffer_k: int = 5
    max_single_pct: float = 0.08
    min_mv: float = 200_000.0       # 20 亿
    max_mv: float = 3_000_000.0     # 300 亿
    max_industry_pct: float = 0.25

    # 因子权重
    weight_value: float = 0.30
    weight_earnings: float = 0.25
    weight_debt: float = 0.25
    weight_size: float = 0.20

    # 基本面
    max_pe: float = 40.0
    min_roe: float = 3.0


@StrategyRegistry().register("insider_buying")
class InsiderBuyingStrategy(EquityRotationStrategy):
    """内部人增持代理：低估值 + 盈利改善 + 低杠杆 + 小盘。"""

    name = "insider_buying"

    def __init__(
        self, config: InsiderBuyingConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or InsiderBuyingConfig(**kwargs)

    @property
    def config(self) -> InsiderBuyingConfig:
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

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        f_earn = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_earn, cfg.weight_earnings, scores)

        f_debt = compute_debt_factor(candidates)
        self.rank_normalize(candidates, f_debt, cfg.weight_debt, scores)

        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
