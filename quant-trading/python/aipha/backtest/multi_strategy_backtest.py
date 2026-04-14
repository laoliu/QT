"""多策略组合回测"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ALLOCATION_MODE_MAP = {
    "equal": "等权",
    "risk_parity": "风险平价",
    "inverse_vol": "波动率倒数",
    "max_sharpe": "最大夏普",
    "min_variance": "最小方差",
    "target_vol": "目标波动率",
    "max_diversification": "最大分散化",
    "custom": "自定义权重",
}


@dataclass
class SubStrategyResult:
    name: str
    weight: float
    nav_series: pd.Series
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    total_trades: int = 0
    total_fees: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": round(self.weight, 4),
            "annual_return": round(self.annual_return, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "sharpe": round(self.sharpe, 4),
            "total_trades": self.total_trades,
            "total_fees": round(self.total_fees, 2),
        }


@dataclass
class MultiStrategyResult:
    sub_results: list[SubStrategyResult]
    combined_nav: pd.Series
    combined_annual_return: float = 0.0
    combined_max_drawdown: float = 0.0
    combined_sharpe: float = 0.0
    correlation_matrix: pd.DataFrame | None = None
    allocation_mode: str = "equal"
    rebalance_freq: str = "monthly"

    def to_dict(self) -> dict:
        d = {
            "allocation_mode": self.allocation_mode,
            "rebalance_freq": self.rebalance_freq,
            "combined_annual_return": round(self.combined_annual_return, 6),
            "combined_max_drawdown": round(self.combined_max_drawdown, 6),
            "combined_sharpe": round(self.combined_sharpe, 4),
            "sub_results": [s.to_dict() for s in self.sub_results],
        }
        if self.correlation_matrix is not None:
            d["correlation_matrix"] = self.correlation_matrix.round(4).to_dict()
        return d


def _combine_nav_series(
    nav_list: list[pd.Series],
    weights: list[float],
    rebalance_freq: str = "monthly",
) -> pd.Series:
    """合并多条净值曲线为组合净值"""
    if not nav_list:
        return pd.Series(dtype=float)
    # 对齐日期
    aligned = pd.DataFrame({f"s{i}": s for i, s in enumerate(nav_list)}).ffill()
    if aligned.empty:
        return pd.Series(dtype=float)

    ret_df = aligned.pct_change().fillna(0)
    w = np.array(weights) / np.sum(weights)

    combo_ret = ret_df.values @ w
    combo_nav = pd.Series((1 + combo_ret).cumprod(), index=aligned.index)
    return combo_nav


def _compute_portfolio_metrics(nav: pd.Series) -> dict:
    if nav.empty or len(nav) < 2:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = (nav.index[-1] - nav.index[0]).days or 1
    ann_ret = (1 + total_ret) ** (365 / days) - 1

    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    max_dd = dd.min()

    daily_ret = nav.pct_change().dropna()
    if daily_ret.std() > 0:
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
    else:
        sharpe = 0.0

    return {"annual_return": ann_ret, "max_drawdown": abs(max_dd), "sharpe": sharpe}


def _compute_correlation(nav_list: list[pd.Series], names: list[str]) -> pd.DataFrame:
    if len(nav_list) < 2:
        return pd.DataFrame()
    aligned = pd.DataFrame({n: s for n, s in zip(names, nav_list)}).ffill()
    return aligned.pct_change().dropna().corr()


def parse_multi_strategy_config(config: dict) -> tuple[list[dict], list[float], str, str]:
    """解析多策略配置

    Returns: (strategy_configs, weights, allocation_mode, rebalance_freq)
    """
    strategies = config.get("strategies", [])
    mode = config.get("allocation_mode", "equal")
    freq = config.get("rebalance_freq", "monthly")

    cfgs = []
    weights = []
    for s in strategies:
        cfgs.append(s.get("config", {}))
        weights.append(float(s.get("weight", 1.0)))

    if mode == "equal":
        weights = [1.0 / len(cfgs)] * len(cfgs) if cfgs else []

    return cfgs, weights, mode, freq


def build_sub_strategy_config(base_config: dict, override: dict) -> dict:
    merged = {**base_config}
    merged.update(override)
    return merged


def run_multi_strategy_backtest(
    sub_results: list[SubStrategyResult],
    allocation_mode: str = "equal",
    rebalance_freq: str = "monthly",
) -> MultiStrategyResult:
    """汇总多个子策略回测结果为组合

    Parameters
    ----------
    sub_results : 各子策略独立回测结果
    allocation_mode : 权重分配模式
    rebalance_freq : 再平衡频率
    """
    names = [s.name for s in sub_results]
    navs = [s.nav_series for s in sub_results]
    weights = [s.weight for s in sub_results]

    combined = _combine_nav_series(navs, weights, rebalance_freq)
    metrics = _compute_portfolio_metrics(combined)
    corr = _compute_correlation(navs, names)

    return MultiStrategyResult(
        sub_results=sub_results,
        combined_nav=combined,
        combined_annual_return=metrics["annual_return"],
        combined_max_drawdown=metrics["max_drawdown"],
        combined_sharpe=metrics["sharpe"],
        correlation_matrix=corr if not corr.empty else None,
        allocation_mode=allocation_mode,
        rebalance_freq=rebalance_freq,
    )
