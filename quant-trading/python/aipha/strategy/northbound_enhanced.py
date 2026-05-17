"""A 股北向增强策略 (Northbound Enhanced)

核心逻辑：跟踪外资偏好 + 基本面确认。
- 北向资金（沪港通/深港通）是 A 股重要的边际定价力量
- 外资偏好：高 ROE + 低波动 + 大市值 + 知名品牌
- 用**外资偏好因子画像**代理（无实时北向持仓数据）

代理方式（无实时北向数据）：
- 高 ROE（外资核心偏好）
- 高股息率（外资长期持有型）
- 大市值（流动性好，纳入 MSCI）
- 低波动（外资厌恶高波动 A 股）

与 dividend_quality_select 的差异：
- dividend_quality_select：纯红利+质量
- **northbound_enhanced**：模拟外资审美 → ROE+分红+大盘+低波

Alpha 来源（4 因子）：
1. **质量 ROE** (30%) — 外资核心偏好
2. **股息率** (25%) — 长期持有型
3. **低波动** (25%) — 外资厌波动
4. **估值 EP** (20%) — 安全边际

预期表现：AR 12-18%, MDD 18-25%, Sharpe 0.6-0.9
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_dividend_factor,
    compute_quality_factor,
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
    pass

logger = logging.getLogger(__name__)


@dataclass
class NorthboundEnhancedConfig(EquityRotationConfig):
    """北向增强配置。"""

    top_k: int = 25
    rebalance_days: int = 10         # 周频
    min_price: float = 10.0          # 外资不爱低价股
    max_price: float = 500.0
    min_turnover: float = 1000.0     # 高流动性
    min_listed_days: int = 500       # 成熟公司
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 6
    max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0     # 100 亿 — 大盘为主
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 因子权重
    weight_quality: float = 0.30
    weight_dividend: float = 0.25
    weight_vol: float = 0.25
    weight_value: float = 0.20

    # 基本面
    min_roe: float = 8.0             # 外资门槛较高
    min_div_yield: float = 0.5       # 最低股息率 0.5%
    max_pe: float = 40.0


@StrategyRegistry().register("northbound_enhanced")
class NorthboundEnhancedStrategy(EquityRotationStrategy):
    """北向增强：高 ROE + 股息 + 低波 + 估值。"""

    name = "northbound_enhanced"

    def __init__(
        self, config: NorthboundEnhancedConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or NorthboundEnhancedConfig(**kwargs)

    @property
    def config(self) -> NorthboundEnhancedConfig:
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

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_div = compute_dividend_factor(candidates)
        self.rank_normalize(candidates, f_div, cfg.weight_dividend, scores)

        f_vol = compute_volatility_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.volatility_lookback, invert=True,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_vol, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        return self.rank_sort(scores)
