"""A 股过度反应策略 (Overreaction Factor Strategy)

核心逻辑：利用散户行为偏差（注意力偏差、彩票偏好、过度反应）构建
纯因子排名选股模型，不依赖 LGB/ML。

学术基础：
- De Bondt & Thaler (1985) "Does the Stock Market Overreact?"
- Stambaugh, Yu, Yuan (2015) "Arbitrage Asymmetry"
- Lian & Shi (2021) SSRN 3928587 "A Composite Four-Factor Model in China"

Alpha 来源（3 因子，等权排名融合）：
1. **IVOL**（特质波动率，反向）— 低 IVOL 股票长期跑赢高 IVOL（IC=0.086）
2. **MAX**（最大日收益，反向）— 彩票效应修正（IC=0.086）
3. **ABT**（异常换手率，反向）— 注意力偏差修正（IC=0.081）

注：STR (短期反转) IC=0.028 不显著，不纳入。

验证数据（D-002, R120/R121 确认）：
- 组合 IC = 0.091（全量期），0.110（2022后）← 反衰减！
- ICIR = 0.614
- 半衰期 > 40 天

设计：
- 纯排名模型，无需训练/调参，天然符合原则 A（经济理论驱动）
- 与 ml_csi2000 (LGB) 预期低相关 → 组合分散化价值
- 容量：CSI2000 + top_k=50 → 估计容量 5000 万+

预期：AR 15-25%, MDD 30-40%, Sharpe 0.6-1.0
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_abnormal_turnover_factor,
    compute_ivol_factor,
    compute_max_daily_return_factor,
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
class OverreactionConfig(EquityRotationConfig):
    """过度反应策略配置。"""

    top_k: int = 50
    rebalance_days: int = 20          # 月频调仓（因子半衰期 > 40 天）
    min_price: float = 3.0
    max_price: float = 100.0
    min_turnover: float = 500.0
    min_listed_days: int = 120        # 排除次新
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.12
    buffer_k: int = 15                # 较大缓冲区降低换手
    max_single_pct: float = 0.03      # 单票 3%
    min_mv: float = 100_000.0         # 10 亿（CSI2000 范围）
    max_mv: float = 3_000_000.0       # 300 亿上限
    max_industry_pct: float = 0.15    # 单行业 15%
    cash_reserve_pct: float = 0.05

    # 因子权重（等权 — 验证阶段不做权重优化，避免过拟合）
    weight_ivol: float = 0.333
    weight_max: float = 0.333
    weight_abt: float = 0.334

    # 因子计算参数
    ivol_lookback: int = 20           # IVOL 计算窗口
    max_lookback: int = 20            # MAX 计算窗口
    abt_short_window: int = 5         # ABT 近期换手窗口
    abt_long_window: int = 60         # ABT 基线换手窗口

    # 基类历史窗口 — 需覆盖以容纳 ABT 60天 long window
    volatility_lookback: int = 65     # max(ivol 20, abt_long 60) + buffer


@StrategyRegistry().register("overreaction")
class OverreactionStrategy(EquityRotationStrategy):
    """过度反应策略：IVOL + MAX + ABT 三因子排名选股。"""

    name = "overreaction"

    def __init__(
        self, config: OverreactionConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or OverreactionConfig(**kwargs)

    @property
    def config(self) -> OverreactionConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """3 因子打分：IVOL + MAX + ABT（等权排名融合）。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}
        # 使用基类维护的价格/成交额历史 (self._price_history / self._amount_history)
        price_history = self._price_history

        # 1. IVOL 因子（低特质波动率 = 高分）
        f_ivol = compute_ivol_factor(
            price_history, candidates, lookback=cfg.ivol_lookback,
        )
        self.rank_normalize(candidates, f_ivol, cfg.weight_ivol, scores)

        # 2. MAX 因子（低最大日收益 = 高分，反彩票偏好）
        f_max = compute_max_daily_return_factor(
            price_history, candidates, lookback=cfg.max_lookback,
        )
        self.rank_normalize(candidates, f_max, cfg.weight_max, scores)

        # 3. ABT 因子（低异常换手 = 高分，反注意力偏差）
        # _amount_history 存储成交额（amount），ABT 用比值，成交额/成交量等效
        volume_history = self._amount_history
        f_abt = compute_abnormal_turnover_factor(
            volume_history, candidates,
            short_window=cfg.abt_short_window,
            long_window=cfg.abt_long_window,
        )
        self.rank_normalize(candidates, f_abt, cfg.weight_abt, scores)

        # 排序输出 top_k
        sorted_syms = sorted(
            scores.keys(), key=lambda s: scores.get(s, 0), reverse=True,
        )
        return sorted_syms[:cfg.top_k]
