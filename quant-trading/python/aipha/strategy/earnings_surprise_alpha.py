"""A 股盈利超预期 Alpha 策略

核心逻辑：PEAD (Post-Earnings Announcement Drift) 异象。
- 业绩公告后，超预期的股票在 20-60 天内持续漂移
- A 股 PEAD 效应在学术和实战中均被证实（年化 alpha 5-10%）

与 pead_event 的差异：
- pead_event 用复杂事件检测 → 本策略用**基本面代理**（盈利增速排名）
- 更简单：不需要实时事件数据，用季报增速代替
- 结合短期反转（5-20d 反转在 A 股极强）→ 买入回调中的超预期股

Alpha 来源（4 因子）：
1. **盈利增速** (35%) — 利润增速排名（PEAD 代理）
2. **短期反转** (25%) — 近5-20日下跌的反弹（逆向）
3. **质量确认** (20%) — ROE 底线
4. **规模** (20%) — 偏中小盘

预期表现：AR 15-25%, MDD 25-35%, Sharpe 0.5-0.8
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
    compute_size_factor,
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
class EarningsSurpriseConfig(EquityRotationConfig):
    """盈利超预期 Alpha 配置。"""

    top_k: int = 30
    rebalance_days: int = 10         # 短周期捕获 PEAD
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 250       # 有财报历史
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 200_000.0
    max_mv: float = 3_000_000.0
    max_industry_pct: float = 0.20

    # 因子权重
    weight_earnings: float = 0.35
    weight_reversal: float = 0.25
    weight_quality: float = 0.20
    weight_size: float = 0.20

    # 短期反转参数
    reversal_lookback: int = 20
    reversal_skip: int = 0   # 不跳过

    # 宽松过滤
    max_pe: float = 80.0
    momentum_lookback: int = 20


@StrategyRegistry().register("earnings_surprise_alpha")
class EarningsSurpriseAlphaStrategy(EquityRotationStrategy):
    """盈利超预期 Alpha：盈利增速 + 短期反转 + 质量 + 规模。"""

    name = "earnings_surprise_alpha"

    def __init__(
        self, config: EarningsSurpriseConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or EarningsSurpriseConfig(**kwargs)

    @property
    def config(self) -> EarningsSurpriseConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > self._config.max_pe):
            return False
        return True

    def _compute_reversal_factor(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """短期反转因子：近 N 日收益率的负数（下跌 = 高分）。"""
        cfg = self._config
        result: dict[str, float] = {}
        for c in candidates:
            hist = self._price_history.get(c.symbol, [])
            lb = cfg.reversal_lookback
            if len(hist) < lb + 1:
                result[c.symbol] = float("nan")
                continue
            old_price = hist[-(lb + 1)]
            new_price = hist[-1]
            if old_price > 0:
                ret = (new_price - old_price) / old_price
                result[c.symbol] = -ret  # 反转：跌多 = 高分
            else:
                result[c.symbol] = float("nan")
        return result

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：盈利增速 + 短期反转 + 质量 + 规模。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 盈利增速（PEAD 代理）
        f_earn = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_earn, cfg.weight_earnings, scores)

        # 2. 短期反转
        f_rev = self._compute_reversal_factor(candidates)
        self.rank_normalize(candidates, f_rev, cfg.weight_reversal, scores)

        # 3. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        # 4. 规模
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
