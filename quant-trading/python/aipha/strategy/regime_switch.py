"""A 股牛熊切换策略 (Regime Switch)

核心逻辑：根据市场状态在**动量**与**价值**之间切换。
- 牛市 → 动量因子为主（追趋势）
- 熊市 → 价值因子为主（守安全边际）
- 震荡 → 均配

学术基础：
- Asness (2019) — "Value and Momentum Everywhere"，动量与价值长期负相关
- 在市场状态转换时，两类因子有互补特性
- A 股"牛短熊长"特征 → 状态切换能显著改善组合表现

与 quality_momentum_combo 的差异：
- quality_momentum_combo：固定权重混合动量+质量
- **regime_switch**：根据市场状态**动态调整**因子权重

Alpha 来源（4 因子 + 状态切换）：
1. **动量** (牛市 50% / 熊市 10%)
2. **价值 EP** (牛市 10% / 熊市 50%)
3. **质量 ROE** (25%)
4. **规模** (15%)
5. **状态信号**：MA + 动量综合判断

预期表现：AR 18-25%, MDD 20-30%, Sharpe 0.6-0.9
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
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
    from aipha.backtest.engine import Bar

logger = logging.getLogger(__name__)


@dataclass
class RegimeSwitchConfig(EquityRotationConfig):
    """牛熊切换配置。"""

    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0       # 30 亿
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 牛市因子权重
    bull_weight_momentum: float = 0.50
    bull_weight_value: float = 0.10
    bull_weight_quality: float = 0.25
    bull_weight_size: float = 0.15

    # 熊市因子权重
    bear_weight_momentum: float = 0.10
    bear_weight_value: float = 0.50
    bear_weight_quality: float = 0.25
    bear_weight_size: float = 0.15

    # 震荡因子权重
    neutral_weight_momentum: float = 0.25
    neutral_weight_value: float = 0.30
    neutral_weight_quality: float = 0.25
    neutral_weight_size: float = 0.20

    # 状态检测参数
    ma_short: int = 20
    ma_long: int = 60
    momentum_lookback: int = 60
    bull_momentum_threshold: float = 0.08
    bear_momentum_threshold: float = -0.06

    max_pe: float = 80.0

    # 内部状态
    _index_history: list[float] = field(default_factory=list)
    _current_regime: str = "NEUTRAL"


@StrategyRegistry().register("regime_switch")
class RegimeSwitchStrategy(EquityRotationStrategy):
    """牛熊切换：动态调整动量/价值因子权重。"""

    name = "regime_switch"

    def __init__(
        self, config: RegimeSwitchConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or RegimeSwitchConfig(**kwargs)

    @property
    def config(self) -> RegimeSwitchConfig:
        return self._config

    def _detect_regime(self) -> str:
        """基于 MA + 动量的状态检测。"""
        cfg = self._config
        hist = cfg._index_history
        if len(hist) < cfg.ma_long:
            return "NEUTRAL"

        # 信号 1: MA 交叉
        ma_s = sum(hist[-cfg.ma_short:]) / cfg.ma_short
        ma_l = sum(hist[-cfg.ma_long:]) / cfg.ma_long
        ma_signal = "BULL" if ma_s > ma_l else ("BEAR" if ma_s < ma_l * 0.98 else "NEUTRAL")

        # 信号 2: 动量
        if len(hist) >= cfg.momentum_lookback + 1:
            ret = (hist[-1] - hist[-(cfg.momentum_lookback + 1)]) / hist[-(cfg.momentum_lookback + 1)]
            if ret > cfg.bull_momentum_threshold:
                mom_signal = "BULL"
            elif ret < cfg.bear_momentum_threshold:
                mom_signal = "BEAR"
            else:
                mom_signal = "NEUTRAL"
        else:
            mom_signal = "NEUTRAL"

        # 投票
        if ma_signal == mom_signal:
            return ma_signal
        if ma_signal == "NEUTRAL":
            return mom_signal
        if mom_signal == "NEUTRAL":
            return ma_signal
        return "NEUTRAL"  # 矛盾 → 中性

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """更新指数序列 + 检测市场状态。"""
        cfg = self._config
        bench_price = context.metadata.get("benchmark_close")
        if bench_price is None:
            prices = [b.close for b in bars.values() if b.close > 0]
            bench_price = sum(prices) / len(prices) if prices else 0.0

        cfg._index_history.append(bench_price)
        max_len = max(cfg.ma_long, cfg.momentum_lookback) + 10
        if len(cfg._index_history) > max_len:
            del cfg._index_history[:-max_len]

        cfg._current_regime = self._detect_regime()
        context.metadata["market_regime"] = cfg._current_regime

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
        """根据市场状态动态调整因子权重。"""
        cfg = self._config
        if not candidates:
            return []

        regime = cfg._current_regime
        if regime == "BULL":
            w_mom = cfg.bull_weight_momentum
            w_val = cfg.bull_weight_value
            w_qual = cfg.bull_weight_quality
            w_size = cfg.bull_weight_size
        elif regime == "BEAR":
            w_mom = cfg.bear_weight_momentum
            w_val = cfg.bear_weight_value
            w_qual = cfg.bear_weight_quality
            w_size = cfg.bear_weight_size
        else:
            w_mom = cfg.neutral_weight_momentum
            w_val = cfg.neutral_weight_value
            w_qual = cfg.neutral_weight_quality
            w_size = cfg.neutral_weight_size

        scores: dict[str, float] = {}

        f_mom = compute_momentum_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.momentum_lookback, risk_adjusted=True,
        )
        self.rank_normalize(candidates, f_mom, w_mom, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, w_val, scores)

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, w_qual, scores)

        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, w_size, scores)

        return self.rank_sort(scores)
