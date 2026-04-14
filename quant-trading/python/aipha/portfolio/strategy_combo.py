"""多策略 NAV 组合 — 8 种方法。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ComboResult:
    """组合结果"""
    combo_nav: np.ndarray
    weights_history: dict[str, list[float]]
    metrics: dict[str, float]

    def summary(self) -> str:
        lines = ["=== 策略组合结果 ==="]
        for key, val in self.metrics.items():
            if isinstance(val, float):
                lines.append(f"  {key}: {val:.4f}")
            else:
                lines.append(f"  {key}: {val}")
        return "\n".join(lines)


class StrategyCombiner:
    """多策略 NAV 组合器

    支持方法:
    - equal_weight: 等权
    - risk_parity: 风险平价（逆波动率）
    - rolling_risk_parity: 滚动窗口风险平价
    - momentum_tilt: 动量倾斜
    - performance_weighted: 绩效加权
    """

    def __init__(self, method: str = "equal_weight", **kwargs):
        self._method = method
        self._kwargs = kwargs

    def combine(self, strategy_navs: dict[str, pd.Series]) -> ComboResult:
        """组合多策略 NAV。

        Args:
            strategy_navs: {strategy_name: daily_nav_series}
        """
        if not strategy_navs:
            return ComboResult(combo_nav=np.array([]), weights_history={}, metrics={})

        df = pd.DataFrame(strategy_navs)
        returns = df.pct_change().fillna(0)

        if self._method == "risk_parity":
            weights = self._risk_parity_weights(returns)
        elif self._method == "rolling_risk_parity":
            weights = self._rolling_risk_parity(returns)
        elif self._method == "momentum_tilt":
            weights = self._momentum_tilt(returns)
        elif self._method == "performance_weighted":
            weights = self._performance_weighted(returns)
        else:
            weights = pd.DataFrame(
                1.0 / len(df.columns),
                index=returns.index,
                columns=returns.columns,
            )

        combo_returns = (returns * weights).sum(axis=1)
        combo_nav = (1 + combo_returns).cumprod().values

        metrics = {
            "total_return": float(combo_nav[-1] - 1) if len(combo_nav) > 0 else 0,
            "volatility": float(combo_returns.std() * np.sqrt(252)),
            "sharpe_ratio": float(combo_returns.mean() / combo_returns.std() * np.sqrt(252)) if combo_returns.std() > 0 else 0,
            "max_drawdown": float(self._max_dd(combo_nav)),
        }
        metrics["calmar_ratio"] = abs(metrics["total_return"] / metrics["max_drawdown"]) if metrics["max_drawdown"] > 0 else 0

        weights_history = {col: weights[col].tolist() for col in weights.columns}
        return ComboResult(combo_nav=combo_nav, weights_history=weights_history, metrics=metrics)

    def _risk_parity_weights(self, returns: pd.DataFrame) -> pd.DataFrame:
        vols = returns.std()
        vols = vols.where(vols > 0, 1e-8)
        inv_vol = 1.0 / vols
        w = inv_vol / inv_vol.sum()
        return pd.DataFrame({col: w[col] for col in returns.columns}, index=returns.index)

    def _rolling_risk_parity(self, returns: pd.DataFrame, window: int = 60) -> pd.DataFrame:
        weights = pd.DataFrame(index=returns.index, columns=returns.columns, dtype=float)
        n = len(returns.columns)
        for i in range(len(returns)):
            if i < window:
                weights.iloc[i] = 1.0 / n
            else:
                window_ret = returns.iloc[i - window:i]
                vols = window_ret.std()
                vols = vols.where(vols > 0, 1e-8)
                inv_vol = 1.0 / vols
                weights.iloc[i] = (inv_vol / inv_vol.sum()).values
        return weights

    def _momentum_tilt(self, returns: pd.DataFrame, lookback: int = 20, tilt: float = 0.5) -> pd.DataFrame:
        weights = pd.DataFrame(index=returns.index, columns=returns.columns, dtype=float)
        n = len(returns.columns)
        base = 1.0 / n
        for i in range(len(returns)):
            if i < lookback:
                weights.iloc[i] = base
            else:
                cum = (1 + returns.iloc[i - lookback:i]).prod() - 1
                tilt_w = base + tilt * (cum - cum.mean())
                tilt_w = tilt_w.clip(lower=0)
                tilt_w /= tilt_w.sum()
                weights.iloc[i] = tilt_w.values
        return weights

    def _performance_weighted(self, returns: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
        weights = pd.DataFrame(index=returns.index, columns=returns.columns, dtype=float)
        n = len(returns.columns)
        for i in range(len(returns)):
            if i < lookback:
                weights.iloc[i] = 1.0 / n
            else:
                perf = (1 + returns.iloc[i - lookback:i]).prod()
                perf = perf.clip(lower=0.01)
                weights.iloc[i] = (perf / perf.sum()).values
        return weights

    @staticmethod
    def _max_dd(nav: np.ndarray) -> float:
        peak = np.maximum.accumulate(nav)
        dd = (peak - nav) / np.where(peak > 0, peak, 1)
        return float(dd.max())
