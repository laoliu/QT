"""
Multi-Factor Stock Selection Strategy (多因子选股).

Category: Composite / Factor-Based
Markets: A-shares (best), adaptable to other markets

A systematic factor-based strategy that combines fundamental and technical
factors to score and rank stocks, entering positions in top-ranked names.

Implemented factors:
  1. Momentum (动量): N-day return rate — higher is better
  2. Volatility (波动率): N-day annualized volatility — lower is better
  3. Volume trend (量价趋势): recent volume vs. average — breakout confirmation
  4. Mean reversion (均值乖离): BIAS = (close - MA) / MA — extreme negative = buy

Scoring:
  - Each factor produces a z-score (standard deviation from mean)
  - Factors are weighted and combined into a composite score
  - A threshold composite score triggers buy/sell

In single-asset backtest mode (one data feed), the strategy applies all four
factors to the single symbol and generates buy/sell signals based on the
composite score crossing entry/exit thresholds.

Parameters:
  - momentum_period:     Momentum lookback window (default 20)
  - vol_period:          Volatility calculation window (default 20)
  - ma_period:           Moving average period for BIAS (default 60)
  - score_buy_threshold: Composite score above this → buy (default 0.5)
  - score_sell_threshold: Composite score below this → sell (default -0.3)
  - w_momentum:          Momentum factor weight (default 0.3)
  - w_volatility:        Volatility factor weight (default 0.2)
  - w_volume:            Volume factor weight (default 0.2)
  - w_bias:              Mean-reversion (BIAS) factor weight (default 0.3)
"""

from __future__ import annotations

import math

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MultiFactorStrategy(BaseStrategy):
    """Multi-factor composite scoring strategy."""

    STRATEGY_META = StrategyMeta(
        id="multi-factor",
        name="多因子选股 (Multi-Factor)",
        description=(
            "多因子策略：综合动量、波动率、量价趋势、均值乖离四大因子，"
            "对个股打分排序，得分超过阈值时买入，低于阈值时卖出。"
            "适用于A股选股和轮动，可灵活调整因子权重。"
            "单标的模式下，对单只股票进行因子评分并产生交易信号。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("momentum_period", "number", 20, "动量回看周期", min=5, max=60),
            StrategyParameter("vol_period", "number", 20, "波动率计算周期", min=10, max=60),
            StrategyParameter("ma_period", "number", 60, "BIAS均线周期", min=20, max=120),
            StrategyParameter(
                "score_buy_threshold", "number", 0.5, "买入分数阈值", min=-1.0, max=2.0, step=0.1
            ),
            StrategyParameter(
                "score_sell_threshold", "number", -0.3, "卖出分数阈值", min=-2.0, max=1.0, step=0.1
            ),
            StrategyParameter("w_momentum", "number", 0.3, "动量权重", min=0.0, max=1.0, step=0.05),
            StrategyParameter("w_volatility", "number", 0.2, "波动率权重", min=0.0, max=1.0, step=0.05),
            StrategyParameter("w_volume", "number", 0.2, "量价权重", min=0.0, max=1.0, step=0.05),
            StrategyParameter("w_bias", "number", 0.3, "BIAS权重", min=0.0, max=1.0, step=0.05),
        ],
    )

    params = (
        ("momentum_period", 20),
        ("vol_period", 20),
        ("ma_period", 60),
        ("score_buy_threshold", 0.5),
        ("score_sell_threshold", -0.3),
        ("w_momentum", 0.3),
        ("w_volatility", 0.2),
        ("w_volume", 0.2),
        ("w_bias", 0.3),
    )

    def __init__(self):
        super().__init__()
        # Momentum: N-day rate of change
        self.roc = bt.indicators.RateOfChange(
            self.data.close,
            period=self.p.momentum_period,
        )
        # Volatility: standard deviation of returns
        self.stddev = bt.indicators.StdDev(
            self.data.close,
            period=self.p.vol_period,
        )
        # Moving average for BIAS
        self.sma = bt.indicators.SMA(
            self.data.close,
            period=self.p.ma_period,
        )
        # Volume SMA for volume factor
        self.vol_sma = bt.indicators.SMA(
            self.data.volume,
            period=self.p.momentum_period,
        )

        # Rolling statistics for z-score normalization
        self._roc_history: list[float] = []
        self._std_history: list[float] = []
        self._vol_ratio_history: list[float] = []
        self._bias_history: list[float] = []

    # ── factor calculations ───────────────────────────────────────

    def _zscore(self, value: float, history: list[float]) -> float:
        """Compute z-score of value given its history."""
        if len(history) < 2:
            return 0.0
        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        std = math.sqrt(variance) if variance > 0 else 1e-9
        return (value - mean) / std

    def _compute_composite_score(self) -> tuple[float, dict[str, float]]:
        """Compute the weighted composite factor score.

        Returns:
            (composite_score, {factor_name: z_score})
        """
        close = self.data.close[0]
        sma_val = self.sma[0]

        # Factor 1: Momentum (higher ROC → higher score)
        momentum = self.roc[0]
        self._roc_history.append(momentum)

        # Factor 2: Volatility (lower → higher score, so we negate)
        vol = self.stddev[0]
        self._std_history.append(vol)

        # Factor 3: Volume trend (current volume / avg volume)
        avg_vol = self.vol_sma[0]
        vol_ratio = (self.data.volume[0] / avg_vol) if avg_vol > 0 else 1.0
        self._vol_ratio_history.append(vol_ratio)

        # Factor 4: BIAS = (close - MA) / MA
        bias = (close - sma_val) / sma_val if sma_val > 0 else 0.0
        self._bias_history.append(bias)

        # Trim histories to a rolling window
        max_len = self.p.ma_period * 3
        for hist in (
            self._roc_history,
            self._std_history,
            self._vol_ratio_history,
            self._bias_history,
        ):
            if len(hist) > max_len:
                del hist[: len(hist) - max_len]

        # Z-scores
        z_momentum = self._zscore(momentum, self._roc_history)
        z_vol = -self._zscore(vol, self._std_history)  # negate: lower vol is better
        z_volume = self._zscore(vol_ratio, self._vol_ratio_history)
        z_bias = -self._zscore(bias, self._bias_history)  # negate: negative bias → buy

        factors = {
            "momentum": z_momentum,
            "volatility": z_vol,
            "volume": z_volume,
            "bias": z_bias,
        }

        composite = (
            self.p.w_momentum * z_momentum
            + self.p.w_volatility * z_vol
            + self.p.w_volume * z_volume
            + self.p.w_bias * z_bias
        )

        return composite, factors

    # ── core logic ────────────────────────────────────────────────

    def next(self):
        # Need enough data for the longest lookback
        min_bars = max(self.p.momentum_period, self.p.vol_period, self.p.ma_period)
        if len(self) < min_bars + 1:
            return

        score, factors = self._compute_composite_score()
        close = self.data.close[0]

        if not self.position:
            if score >= self.p.score_buy_threshold:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    detail = ", ".join(f"{k}={v:.2f}" for k, v in factors.items())
                    self.record_signal(
                        "buy",
                        close,
                        f"多因子买入: score={score:.2f} (阈值{self.p.score_buy_threshold}), "
                        f"[{detail}]",
                    )
        else:
            if score <= self.p.score_sell_threshold:
                self.close()
                detail = ", ".join(f"{k}={v:.2f}" for k, v in factors.items())
                self.record_signal(
                    "sell",
                    close,
                    f"多因子卖出: score={score:.2f} (阈值{self.p.score_sell_threshold}), "
                    f"[{detail}]",
                )
