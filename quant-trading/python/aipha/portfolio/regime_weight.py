"""Regime 自适应策略权重分配器

根据市场状态（牛市/震荡/熊市）动态调整策略权重：

**核心思路**：
- 牛市（BULL）：增配动量/攻击型策略，减配防御型
- 震荡（NEUTRAL）：均衡配置，偏向价值/低波策略
- 熊市（BEAR）：增配防御/债性策略，减配动量型
- 高波动：全面降低仓位（波动率缩放）

**策略分类**：
- AGGRESSIVE: 动量、趋势、热度类（如 momentum, heat_momentum, vol_breakout）
- BALANCED: 多因子、轮动类（如 multi_factor, four_factor）
- DEFENSIVE: 价值、低价、债性类（如 triple_low, credit_spread, ytm_plus）

**理论基础**（§11.2）：
- 趋势跟踪 + 波动率目标（Moskowitz, Ooi, Pedersen 2012 — Time Series Momentum）
- 风险平价在不同 regime 下的表现差异（Roncalli 2013）
- 条件期望收益模型：E[r|regime] 显著不同 → 配权应随之调整

**与现有架构关系**：
- 复用 MarketRegimeDetector（src/aipha/risk/constraints.py）做 regime 判断
- 输出权重字典可直接对接 AdaptiveWeightAllocator / multi_strategy_backtest
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 策略类型枚举
# ---------------------------------------------------------------------------

class StrategyStyle(str, Enum):
    """策略风格分类。"""
    AGGRESSIVE = "aggressive"     # 攻击型（动量/趋势/热度）
    BALANCED = "balanced"         # 均衡型（多因子/轮动）
    DEFENSIVE = "defensive"       # 防御型（价值/债性/低波）

    def __str__(self) -> str:
        return self.value


class MarketRegime(str, Enum):
    """市场状态。"""
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class RegimeWeightConfig:
    """Regime 自适应权重配置。

    Attributes:
        bull_weights: 牛市时各风格的目标权重倍数
        neutral_weights: 震荡时各风格的目标权重倍数
        bear_weights: 熊市时各风格的目标权重倍数
        high_vol_scale: 高波动环境下的仓位缩放系数
        transition_speed: 状态切换平滑系数 (0=不切换, 1=立即切换)
        min_weight: 单策略最小权重
        max_weight: 单策略最大权重
    """
    bull_weights: dict[str, float] = field(default_factory=lambda: {
        "aggressive": 1.5,
        "balanced": 1.0,
        "defensive": 0.5,
    })
    neutral_weights: dict[str, float] = field(default_factory=lambda: {
        "aggressive": 0.8,
        "balanced": 1.2,
        "defensive": 1.0,
    })
    bear_weights: dict[str, float] = field(default_factory=lambda: {
        "aggressive": 0.3,
        "balanced": 0.8,
        "defensive": 1.5,
    })
    high_vol_scale: float = 0.7
    transition_speed: float = 0.3   # EMA 平滑
    min_weight: float = 0.05
    max_weight: float = 0.60


# ---------------------------------------------------------------------------
# 预定义策略→风格映射
# ---------------------------------------------------------------------------

DEFAULT_STYLE_MAP: dict[str, StrategyStyle] = {
    # Aggressive
    "cb_momentum": StrategyStyle.AGGRESSIVE,
    "cb_heat_momentum": StrategyStyle.AGGRESSIVE,
    "vol_breakout": StrategyStyle.AGGRESSIVE,
    "turnover_surge": StrategyStyle.AGGRESSIVE,
    "cb_regime_rotation": StrategyStyle.AGGRESSIVE,
    "earnings_momentum": StrategyStyle.AGGRESSIVE,
    # Balanced
    "multi_factor": StrategyStyle.BALANCED,
    "cb_four_factor": StrategyStyle.BALANCED,
    "small_size_enhanced": StrategyStyle.BALANCED,
    "small_size_ensemble": StrategyStyle.BALANCED,
    "new_small": StrategyStyle.BALANCED,
    "new_small_ensemble": StrategyStyle.BALANCED,
    "new_bond_ensemble": StrategyStyle.BALANCED,
    "cb_stock_quality": StrategyStyle.BALANCED,
    "cb_stock_quality_v2": StrategyStyle.BALANCED,
    "cb_ivol_value": StrategyStyle.BALANCED,
    # Defensive
    "triple_low": StrategyStyle.DEFENSIVE,
    "double_low_percentile": StrategyStyle.DEFENSIVE,
    "double_low_rank": StrategyStyle.DEFENSIVE,
    "double_low_zscore": StrategyStyle.DEFENSIVE,
    "low_price": StrategyStyle.DEFENSIVE,
    "low_premium": StrategyStyle.DEFENSIVE,
    "cb_credit_spread": StrategyStyle.DEFENSIVE,
    "cb_pricing_bias": StrategyStyle.DEFENSIVE,
    "pseudo_ytm": StrategyStyle.DEFENSIVE,
    "ytm_plus": StrategyStyle.DEFENSIVE,
    "defensive_value": StrategyStyle.DEFENSIVE,
    "cb_mean_reversion": StrategyStyle.DEFENSIVE,
    # Balanced (multi-factor adaptive)
    "cb_adaptive_factor": StrategyStyle.BALANCED,
}


# ---------------------------------------------------------------------------
# 核心类
# ---------------------------------------------------------------------------

@dataclass
class RegimeWeightResult:
    """Regime 自适应权重结果。

    Attributes:
        regime: 当前市场状态
        raw_weights: 调整前的基础权重
        adjusted_weights: regime 调整后权重
        is_high_vol: 是否处于高波动状态
        regime_history: 历史 regime 记录
    """
    regime: MarketRegime = MarketRegime.NEUTRAL
    raw_weights: dict[str, float] = field(default_factory=dict)
    adjusted_weights: dict[str, float] = field(default_factory=dict)
    is_high_vol: bool = False
    regime_history: list[tuple[date, str]] = field(default_factory=list)


class RegimeAdaptiveAllocator:
    """Regime 自适应策略权重分配器。

    用法：
        >>> allocator = RegimeAdaptiveAllocator(config)
        >>> allocator.register_strategy("triple_low_v3", StrategyStyle.DEFENSIVE)
        >>> adjusted = allocator.compute_weights(
        ...     base_weights={"triple_low_v3": 0.33, "momentum_v1": 0.33, "multi_factor": 0.34},
        ...     regime=MarketRegime.BEAR,
        ...     is_high_vol=False,
        ... )
    """

    def __init__(
        self,
        config: RegimeWeightConfig | None = None,
        style_map: dict[str, StrategyStyle] | None = None,
    ) -> None:
        self._config = config or RegimeWeightConfig()
        self._style_map: dict[str, StrategyStyle] = dict(DEFAULT_STYLE_MAP)
        if style_map:
            self._style_map.update(style_map)
        self._prev_weights: dict[str, float] | None = None

    @property
    def config(self) -> RegimeWeightConfig:
        return self._config

    def register_strategy(self, name: str, style: StrategyStyle) -> None:
        """注册或更新策略的风格分类。"""
        self._style_map[name] = style

    def get_style(self, name: str) -> StrategyStyle:
        """获取策略风格。默认 BALANCED。"""
        return self._style_map.get(name, StrategyStyle.BALANCED)

    def compute_weights(
        self,
        base_weights: dict[str, float],
        regime: MarketRegime,
        is_high_vol: bool = False,
    ) -> dict[str, float]:
        """根据 regime 调整策略权重。

        Args:
            base_weights: 基础策略权重（不依赖regime的初始配权）
            regime: 当前市场状态
            is_high_vol: 是否高波动环境

        Returns:
            调整后的归一化权重
        """
        cfg = self._config

        # 选择 regime 对应的倍数表
        if regime == MarketRegime.BULL:
            multipliers = cfg.bull_weights
        elif regime == MarketRegime.BEAR:
            multipliers = cfg.bear_weights
        else:
            multipliers = cfg.neutral_weights

        # 应用倍数
        adjusted: dict[str, float] = {}
        for name, w in base_weights.items():
            style = self.get_style(name)
            mult = multipliers.get(str(style), 1.0)
            adjusted[name] = w * mult

        # 高波动缩放
        if is_high_vol:
            adjusted = {k: v * cfg.high_vol_scale for k, v in adjusted.items()}

        # EMA 平滑（与上次权重混合，避免剧烈切换）
        if self._prev_weights is not None and cfg.transition_speed < 1.0:
            alpha = cfg.transition_speed
            smoothed = {}
            for name in adjusted:
                prev = self._prev_weights.get(name, adjusted[name])
                smoothed[name] = alpha * adjusted[name] + (1 - alpha) * prev
            adjusted = smoothed

        # 归一化
        adjusted = self._normalize_and_clip(adjusted, cfg.min_weight, cfg.max_weight)

        # 记录
        self._prev_weights = dict(adjusted)

        return adjusted

    def compute_full(
        self,
        base_weights: dict[str, float],
        regime: MarketRegime,
        is_high_vol: bool = False,
        current_date: date | None = None,
    ) -> RegimeWeightResult:
        """完整计算，返回 RegimeWeightResult。"""
        adjusted = self.compute_weights(base_weights, regime, is_high_vol)
        return RegimeWeightResult(
            regime=regime,
            raw_weights=dict(base_weights),
            adjusted_weights=adjusted,
            is_high_vol=is_high_vol,
            regime_history=(
                [(current_date, str(regime))] if current_date else []
            ),
        )

    @staticmethod
    def _normalize_and_clip(
        weights: dict[str, float],
        min_w: float,
        max_w: float,
    ) -> dict[str, float]:
        """归一化 + 裁剪。"""
        if not weights:
            return weights

        # Clip
        clipped = {k: max(min_w, min(max_w, v)) for k, v in weights.items()}
        total = sum(clipped.values())
        if total <= 0:
            n = len(clipped)
            return {k: 1.0 / n for k in clipped}
        return {k: v / total for k, v in clipped.items()}

    def reset(self) -> None:
        """重置内部 EMA 状态。"""
        self._prev_weights = None
