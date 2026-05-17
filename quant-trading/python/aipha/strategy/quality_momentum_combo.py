"""A 股质量动量组合策略

核心逻辑：A 股最经典的正交双因子 — ROE质量 + 中期动量。

学术基础：
- Novy-Marx (2013): Quality 和 Momentum 在美股负相关但各自有alpha
- 中金/华泰 A 股实证: ROE(IC 4-6%) + 动量(IC 2-3%)，组合 IC 5-7%
- 关键洞察：质量因子全周期有效，动量因子牛市强/熊市弱 → 天然互补

与 quality_growth 的差异：
- **无硬性基本面门槛**（不用 min_roe/min_growth 做门限筛选）
- **纯排名打分** → 候选池更大 → 持仓更充分
- 动量用**风险调整动量**(Ret/σ) 替代原始收益率 → 更稳健
- 无成长因子 → 避免景气下行期崩溃

Alpha 来源（4 因子）：
1. **质量 ROE** (30%) — 全周期最稳 alpha
2. **风险调整动量** (30%) — skip_recent=5, lookback=60
3. **价值 EP** (20%) — 防止追高
4. **规模倒数** (20%) — 中小盘溢价（行业中性化后）

预期表现：AR 12-18%, MDD 20-28%, Sharpe 0.6-1.0
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_quality_factor,
    compute_risk_adjusted_momentum,
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
class QualityMomentumConfig(EquityRotationConfig):
    """质量动量组合策略配置。"""

    top_k: int = 30
    rebalance_days: int = 15          # 双周调仓
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0        # 30 亿以上
    max_mv: float = 5_000_000.0      # 500 亿以下（中小盘甜蜜区）
    max_industry_pct: float = 0.20

    # 因子权重
    weight_quality: float = 0.30
    weight_momentum: float = 0.30
    weight_value: float = 0.20
    weight_size: float = 0.20

    # 动量参数（风险调整动量）
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    volatility_lookback: int = 60

    # 无硬性基本面门槛（只靠排名）
    max_pe: float = 80.0             # 仅排除极端泡沫


@StrategyRegistry().register("quality_momentum_combo")
class QualityMomentumComboStrategy(EquityRotationStrategy):
    """A 股质量动量组合策略。

    经典正交双因子组合：ROE质量 + 风险调整动量 + 价值确认 + 规模暴露。
    无硬性基本面门槛，纯排名打分，候选池更大。
    """

    name = "quality_momentum_combo"

    def __init__(
        self, config: QualityMomentumConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or QualityMomentumConfig(**kwargs)

    @property
    def config(self) -> QualityMomentumConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """仅排除极端高估值，门槛极宽松。"""
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > self._config.max_pe):
            return False
        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：质量 + 风险调整动量 + 价值 + 规模。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        # 2. 风险调整动量 (Ret/σ)
        f_mom = compute_risk_adjusted_momentum(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            vol_lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        # 3. 价值因子（低 PE）
        f_value = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(candidates, f_value, cfg.weight_value, scores)

        # 4. 规模因子（小市值优先）
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
