"""A 股困境反转策略 (Turnaround Play)

核心逻辑：买入基本面触底回升的"落难贵族"。
- 学术基础：Piotroski (2000) "Value Investing" — F-Score 在低 BP 股票中
  区分真正翻转 vs 持续恶化，年化 alpha 7-10%
- A 股增强：结合**盈利拐点** + **低估值** + **资产负债表改善**

与 fscore_value_enhanced 的差异：
- fscore_value_enhanced：从整体 F-Score 出发，买"高 F-Score + 低 PB"
- **turnaround_play**：关注**利润增速由负转正**（拐点），低估值是
  安全边际，F-Score 仅做辅助验证

Alpha 来源（4 因子）：
1. **盈利增速** (35%) — 核心：利润同比增速（负→正 = 拐点）
2. **估值** (30%) — 低 PB 提供安全边际
3. **F-Score** (20%) — 资产负债表健康度确认
4. **规模** (15%) — 中小盘流动性溢价

预期表现：AR 20-30%, MDD 30-40%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_piotroski_factor,
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
class TurnaroundPlayConfig(EquityRotationConfig):
    """困境反转配置。"""

    top_k: int = 30
    rebalance_days: int = 15         # 偏长持有
    min_price: float = 3.0
    max_price: float = 150.0
    min_turnover: float = 300.0
    min_listed_days: int = 500       # 至少 2 年上市（历史可查）
    stop_loss_pct: float = 0.22
    trailing_stop_pct: float = 0.18
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 200_000.0       # 20 亿
    max_mv: float = 5_000_000.0     # 500 亿
    max_industry_pct: float = 0.20

    # 因子权重
    weight_earnings: float = 0.35
    weight_value: float = 0.30
    weight_fscore: float = 0.20
    weight_size: float = 0.15

    # 估值安全边际
    max_pb: float = 5.0             # PB > 5 排除
    max_pe: float = 100.0           # 宽松 PE 上限（困境反转 PE 可能较高）
    min_fscore: float = 4.0         # F-Score ≥ 4（确认不是持续恶化）


@StrategyRegistry().register("turnaround_play")
class TurnaroundPlayStrategy(EquityRotationStrategy):
    """困境反转：盈利拐点 + 低估值 + F-Score 确认。"""

    name = "turnaround_play"

    def __init__(
        self, config: TurnaroundPlayConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or TurnaroundPlayConfig(**kwargs)

    @property
    def config(self) -> TurnaroundPlayConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """过滤：PB ≤ 上限，PE 正数且 ≤ 上限。"""
        cfg = self._config
        pb = candidate.metadata.get("pb")
        if pb is not None and (pb <= 0 or pb > cfg.max_pb):
            return False
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：盈利增速 + 估值 + F-Score + 规模。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 盈利增速（核心 — 拐点信号）
        f_earn = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_earn, cfg.weight_earnings, scores)

        # 2. 估值（低 PB = 安全边际）
        f_val = compute_value_factor(candidates, metric="bp")
        self.rank_normalize(candidates, f_val, cfg.weight_value, scores)

        # 3. F-Score（资产负债表确认）
        f_fsc = compute_piotroski_factor(candidates)
        # 额外过滤：F-Score 过低的剔除
        for c in candidates:
            sym = c.symbol
            raw = f_fsc.get(sym, float("nan"))
            if not math.isnan(raw) and raw < cfg.min_fscore:
                f_fsc[sym] = float("nan")  # 标记为无效
        self.rank_normalize(candidates, f_fsc, cfg.weight_fscore, scores)

        # 4. 规模（偏中小盘）
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
