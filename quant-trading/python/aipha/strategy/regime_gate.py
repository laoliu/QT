"""Regime 感知策略门控 — Strategy-level Regime Gate。

在策略截面评分阶段，根据当前市场 Regime 对候选分数进行缩放，
实现"震荡期降权、趋势期正常"的自适应信号强度调整。

与 portfolio/regime_weight.py（组合层多策略配置）的区别：
- 本模块作用于**单策略候选列表**的评分缩放
- portfolio/regime_weight.py 作用于**多策略间**的权重分配

理论基础：
- Hamilton (1989) — Regime-Switching 状态机
- 知识库 P 章节 — A股/CB 历史 Regime 划分（2015-2025）
- Asness et al. (2013) — 市场择时对策略容量的影响

使用示例::

    gate = RegimeStrategyGate()
    score_map = {"000001.SZ": 0.8, "000002.SZ": -0.3}
    scaled = gate.scale_scores(score_map, regime=MarketRegime.NEUTRAL)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import NamedTuple

from aipha.portfolio.regime_weight import MarketRegime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class RegimeGateConfig:
    """Regime 门控配置。

    每种 Regime 都有对应的分数缩放系数：
    - score_scale > 1.0 → 放大信号（牛市激进策略）
    - score_scale = 1.0 → 不变
    - score_scale < 1.0 → 降低信号强度（震荡期）
    - score_scale = 0.0 → 完全静默（极度熊市可将激进策略归零）

    Attributes:
        bull_scale: 牛市信号强度系数
        neutral_scale: 震荡市（NEUTRAL）信号强度系数（降权）
        bear_scale: 熊市信号强度系数
        min_scale: 最小允许系数（防止过度压制）
        max_scale: 最大允许系数（防止信号爆炸）
    """
    bull_scale: float = 1.0
    neutral_scale: float = 0.7      # 震荡期降权 30%
    bear_scale: float = 0.5         # 熊市降权 50%
    min_scale: float = 0.1
    max_scale: float = 2.0


# CB 策略专用配置：
# - 震荡期：CB面值保护良好，信号保留 80%
# - 牛市：  动量信号可适当放大 10%
# - 熊市：  债性保护，信号降至 60%
CB_GATE_CONFIG = RegimeGateConfig(
    bull_scale=1.1,
    neutral_scale=0.8,
    bear_scale=0.6,
)

# A股策略专用配置：
# - 震荡期：横盘信号不可靠，降至 70%
# - 牛市：  趋势明确，正常100%
# - 熊市：  熊市信号不稳定，降至 40%
EQUITY_GATE_CONFIG = RegimeGateConfig(
    bull_scale=1.0,
    neutral_scale=0.7,
    bear_scale=0.4,
)


# ---------------------------------------------------------------------------
# 门控结果
# ---------------------------------------------------------------------------

class GateResult(NamedTuple):
    """Regime 门控执行结果。"""
    regime: MarketRegime
    scale_applied: float
    n_candidates: int
    is_gated: bool          # True = 实际缩放了（scale != 1.0）


# ---------------------------------------------------------------------------
# 核心门控类
# ---------------------------------------------------------------------------

class RegimeStrategyGate:
    """Regime 感知策略截面评分门控。

    在策略 `_score_candidates()` 调用后、持仓筛选前，
    根据当前 Regime 对每支候选股/债的评分进行全局缩放。

    Usage::

        gate = RegimeStrategyGate(CB_GATE_CONFIG)
        # 在策略中:
        scores = self._compute_raw_scores(candidates)
        current_regime = self._regime_detector.detect()
        scores, result = gate.apply(scores, current_regime)
    """

    def __init__(self, config: RegimeGateConfig | None = None) -> None:
        self._config = config or RegimeGateConfig()

    @property
    def config(self) -> RegimeGateConfig:
        return self._config

    def get_scale(self, regime: MarketRegime) -> float:
        """获取当前 Regime 对应的缩放系数。"""
        cfg = self._config
        if regime == MarketRegime.BULL:
            scale = cfg.bull_scale
        elif regime == MarketRegime.BEAR:
            scale = cfg.bear_scale
        else:  # NEUTRAL / unknown
            scale = cfg.neutral_scale
        return max(cfg.min_scale, min(cfg.max_scale, scale))

    def scale_scores(
        self,
        scores: dict[str, float],
        regime: MarketRegime,
    ) -> dict[str, float]:
        """对 score_map 按 Regime 进行全局缩放。

        Args:
            scores: 符号 → 原始评分字典
            regime: 当前市场 Regime

        Returns:
            缩放后的评分字典（键不变，值乘以缩放系数）
        """
        if not scores:
            return {}
        scale = self.get_scale(regime)
        return {sym: v * scale for sym, v in scores.items()}

    def apply(
        self,
        scores: dict[str, float],
        regime: MarketRegime,
    ) -> tuple[dict[str, float], GateResult]:
        """应用 Regime 门控，返回缩放后分数和门控结果。

        Args:
            scores: 原始评分字典
            regime: 当前市场 Regime

        Returns:
            (scaled_scores, GateResult)
        """
        scale = self.get_scale(regime)
        scaled = self.scale_scores(scores, regime)
        result = GateResult(
            regime=regime,
            scale_applied=scale,
            n_candidates=len(scores),
            is_gated=(abs(scale - 1.0) > 1e-9),
        )
        if result.is_gated:
            logger.debug(
                "RegimeStrategyGate: regime=%s, scale=%.2f, n=%d",
                regime, scale, len(scores),
            )
        return scaled, result

    def get_regime_summary(self) -> dict[str, float]:
        """返回各 Regime 对应缩放系数（用于监控/报告）。"""
        return {
            "BULL": self.get_scale(MarketRegime.BULL),
            "NEUTRAL": self.get_scale(MarketRegime.NEUTRAL),
            "BEAR": self.get_scale(MarketRegime.BEAR),
        }


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------

def make_cb_gate(
    neutral_scale: float = 0.8,
    bear_scale: float = 0.6,
) -> RegimeStrategyGate:
    """创建 CB 策略专用 Regime 门控（可覆盖关键参数）。"""
    cfg = RegimeGateConfig(
        bull_scale=1.1,
        neutral_scale=neutral_scale,
        bear_scale=bear_scale,
    )
    return RegimeStrategyGate(cfg)


def make_equity_gate(
    neutral_scale: float = 0.7,
    bear_scale: float = 0.4,
) -> RegimeStrategyGate:
    """创建 A股策略专用 Regime 门控（可覆盖关键参数）。"""
    cfg = RegimeGateConfig(
        bull_scale=1.0,
        neutral_scale=neutral_scale,
        bear_scale=bear_scale,
    )
    return RegimeStrategyGate(cfg)
