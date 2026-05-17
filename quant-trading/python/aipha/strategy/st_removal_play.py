"""A 股摘帽博弈策略 (ST Removal Play)

核心逻辑：ST 公司业绩改善 → 摘帽预期 → 短期大涨。
- **代理方式**：不直接交易 ST 票（排除在 Universe 之外），
  而是买入**刚摘帽的、基本面确认改善的**公司
- 摘帽 Window: 刚摘帽 30-90 天内仍有估值修复红利

实现方式（无 ST 事件数据的代理）：
- 盈利增速极高（由亏转盈，利润增速 > 100%）
- 低估值（市场尚未充分定价）
- Piotroski F-Score 高（资产负债表确认改善）
- 低市值（小公司摘帽效应更强）

与 turnaround_play 的差异：
- turnaround_play：广义困境反转（ROE 拐点 + 低 PB）
- **st_removal_play**：极端困境反转（利润由负转正），更集中、更高波动

Alpha 来源（4 因子）：
1. **盈利增速** (40%) — 由亏转盈信号
2. **F-Score** (25%) — 财务健康确认
3. **估值** (20%) — 低估安全垫
4. **规模** (15%) — 小盘效应

预期表现：AR 20-35%, MDD 35-45%, Sharpe 0.4-0.7
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
class StRemovalPlayConfig(EquityRotationConfig):
    """摘帽博弈配置。"""

    top_k: int = 10                  # 集中持仓
    rebalance_days: int = 15
    min_price: float = 3.0
    max_price: float = 80.0          # 低价为主
    min_turnover: float = 300.0
    min_listed_days: int = 500       # 老公司
    stop_loss_pct: float = 0.25      # 宽松止损（高波动策略）
    trailing_stop_pct: float = 0.20
    buffer_k: int = 3
    max_single_pct: float = 0.12     # 集中持仓允许更高单票
    min_mv: float = 100_000.0        # 10 亿
    max_mv: float = 1_000_000.0      # 100 亿
    max_industry_pct: float = 0.30

    # 因子权重
    weight_earnings: float = 0.40
    weight_fscore: float = 0.25
    weight_value: float = 0.20
    weight_size: float = 0.15

    min_fscore: float = 5.0          # F-Score ≥ 5（健康确认更严格）
    max_pe: float = 80.0             # 宽松（转盈初期 PE 可能较高）


@StrategyRegistry().register("st_removal_play")
class StRemovalPlayStrategy(EquityRotationStrategy):
    """摘帽博弈：极端盈利改善 + F-Score 确认 + 低估值。"""

    name = "st_removal_play"

    def __init__(
        self, config: StRemovalPlayConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or StRemovalPlayConfig(**kwargs)

    @property
    def config(self) -> StRemovalPlayConfig:
        return self._config

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
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 盈利增速（核心信号，越高=越可能是由亏转盈）
        f_earn = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_earn, cfg.weight_earnings, scores)

        # 2. F-Score（健康确认，过滤虚假改善）
        f_fsc = compute_piotroski_factor(candidates)
        for c in candidates:
            raw = f_fsc.get(c.symbol, float("nan"))
            if not math.isnan(raw) and raw < cfg.min_fscore:
                f_fsc[c.symbol] = float("nan")
        self.rank_normalize(candidates, f_fsc, cfg.weight_fscore, scores)

        # 3. 估值
        f_bp = compute_value_factor(candidates, metric="bp")
        self.rank_normalize(candidates, f_bp, cfg.weight_value, scores)

        # 4. 规模（偏小）
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
