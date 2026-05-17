"""A 股回撤卫士策略 (Max Drawdown Guard)

核心逻辑：多因子选股 + 组合回撤动态减仓 + 恢复加仓。
- 基础选股：质量 + 价值 + 动量 + 低波动
- 仓位叠加：当前回撤 > 阈值 → 线性减仓；回撤恢复 → 逐步加仓
- 目标：在大幅回撤（2015/2018/2022）时自动保护，避免进一步损失

学术基础：
- CPPI (Constant Proportion Portfolio Insurance) 思想
- Grossman & Zhou (1996) — 最优止损策略
- Risk parity 中的 drawdown control overlay

与其他 G4 策略的差异：
- trend_quality_managed 用 MA 择时
- vol_managed_value 用波动率择时
- **max_drawdown_guard** 用**自身回撤**择时（直接风控反馈）

Alpha 来源（4 因子 + 1 回撤控制信号）：
1. **质量 ROE** (25%)
2. **价值 EP** (25%)
3. **动量** (25%)
4. **低波动** (25%)
5. **仓位管理**：drawdown > 10% → 开始线性减仓；> 20% → 最低仓位

预期表现：AR 13-20%, MDD 12-18%, Sharpe 0.7-1.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
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
    from aipha.backtest.engine import Bar

logger = logging.getLogger(__name__)


@dataclass
class MaxDrawdownGuardConfig(EquityRotationConfig):
    """回撤卫士配置。"""

    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0       # 30 亿
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 因子权重
    weight_quality: float = 0.25
    weight_value: float = 0.25
    weight_momentum: float = 0.25
    weight_vol: float = 0.25

    # 回撤控制参数
    dd_start_threshold: float = 0.10    # 回撤 > 10% 开始减仓
    dd_max_threshold: float = 0.20      # 回撤 > 20% 达到最低仓位
    min_position_scale: float = 0.30    # 最低仓位比例

    max_pe: float = 80.0
    momentum_lookback: int = 60

    # 内部状态
    _peak_nav: float = 0.0


@StrategyRegistry().register("max_drawdown_guard")
class MaxDrawdownGuardStrategy(EquityRotationStrategy):
    """回撤卫士：多因子选股 + 回撤动态减仓。"""

    name = "max_drawdown_guard"

    def __init__(
        self, config: MaxDrawdownGuardConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or MaxDrawdownGuardConfig(**kwargs)

    @property
    def config(self) -> MaxDrawdownGuardConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """每 bar 更新 NAV peak → 计算当前回撤 → 设置 position_scale。"""
        cfg = self._config
        nav = context.nav
        if nav > cfg._peak_nav:
            cfg._peak_nav = nav

        if cfg._peak_nav > 0:
            current_dd = (cfg._peak_nav - nav) / cfg._peak_nav
        else:
            current_dd = 0.0

        if current_dd <= cfg.dd_start_threshold:
            scale = 1.0
        elif current_dd >= cfg.dd_max_threshold:
            scale = cfg.min_position_scale
        else:
            # 线性插值
            ratio = (current_dd - cfg.dd_start_threshold) / (
                cfg.dd_max_threshold - cfg.dd_start_threshold
            )
            scale = 1.0 - ratio * (1.0 - cfg.min_position_scale)

        context.metadata["position_scale"] = scale

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
        """等权 4 因子。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        f_mom = compute_momentum_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.momentum_lookback, risk_adjusted=True,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        f_vol = compute_volatility_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.volatility_lookback, invert=True,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_vol, scores)

        return self.rank_sort(scores)
