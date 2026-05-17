"""组合权重模拟器 (§Phase5 Portfolio Simulation)

给定 N 个组件的历史 NAV 曲线，模拟不同权重配置下的组合绩效，
用于识别最优/稳健权重、边际贡献分析、以及组合层约束验证。

功能:
- 单权重向量组合绩效计算
- 多权重网格搜索（Grid Search）
- Leave-One-Out 边际贡献
- 相关性矩阵 + 分散化指标
- Tier S / Tier L 约束下最优权重
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aipha.reporting.combo_nav_backtest import compute_metrics

logger = logging.getLogger(__name__)


@dataclass
class ComponentNav:
    """单个组件的 NAV 序列。"""

    name: str
    nav: pd.Series  # index=DatetimeIndex, values=float NAV

    @property
    def returns(self) -> pd.Series:
        return self.nav.pct_change().dropna()


@dataclass
class SimulationResult:
    """单次模拟结果。"""

    weights: dict[str, float]
    metrics: dict[str, Any]
    nav: pd.Series | None = None


@dataclass
class WeightSimulator:
    """组合权重模拟器。"""

    components: list[ComponentNav]
    initial_capital: float = 1_000_000.0

    def __post_init__(self) -> None:
        if len(self.components) < 2:
            logger.warning("组合权重模拟需要至少 2 个组件")

    def simulate(
        self,
        weights: dict[str, float],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> SimulationResult:
        """模拟给定权重下的组合绩效。"""
        rets_df = self._align_navs(start_date, end_date)
        combo_nav = self._build_combo_nav(rets_df, weights)
        metrics = compute_metrics(combo_nav)
        return SimulationResult(weights=weights, metrics=metrics, nav=combo_nav)

    def grid_search(
        self,
        weight_grid: dict[str, list[float]],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        constraint_fn: Any | None = None,
    ) -> list[SimulationResult]:
        """网格搜索不同权重配置。

        Args:
            weight_grid: {组件名: [候选权重列表]}
            constraint_fn: 可选约束函数 weights_dict -> bool
                           默认要求权重之和在 [0.95, 1.05]

        Returns:
            按 Sharpe 降序排列的 SimulationResult 列表
        """
        from itertools import product

        names = list(weight_grid.keys())
        value_lists = [weight_grid[n] for n in names]

        if constraint_fn is None:
            def constraint_fn(w: dict[str, float]) -> bool:
                total = sum(w.values())
                return 0.95 <= total <= 1.05

        rets_df = self._align_navs(start_date, end_date)
        results: list[SimulationResult] = []

        for combo in product(*value_lists):
            w = dict(zip(names, combo))
            if not constraint_fn(w):
                continue
            combo_nav = self._build_combo_nav(rets_df, w)
            metrics = compute_metrics(combo_nav)
            results.append(SimulationResult(weights=w, metrics=metrics, nav=None))

        # 按 Sharpe 降序排序
        results.sort(key=lambda r: r.metrics.get("sharpe_ratio", 0), reverse=True)
        return results

    def marginal_contribution(
        self,
        base_weights: dict[str, float],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, dict[str, float]]:
        """Leave-One-Out 边际贡献分析。

        Returns:
            {组件名: {"delta_sharpe": ..., "delta_ar": ..., "delta_mdd": ...}}
        """
        rets_df = self._align_navs(start_date, end_date)

        # 基线指标
        base_nav = self._build_combo_nav(rets_df, base_weights)
        base_metrics = compute_metrics(base_nav)

        contributions: dict[str, dict[str, float]] = {}
        for comp in self.components:
            if comp.name not in base_weights or base_weights[comp.name] == 0:
                continue
            # 移除该组件，重新归一化剩余权重
            remaining = {k: v for k, v in base_weights.items() if k != comp.name}
            total_remaining = sum(remaining.values())
            if total_remaining <= 0:
                continue
            normalized = {k: v / total_remaining for k, v in remaining.items()}

            excl_nav = self._build_combo_nav(rets_df, normalized)
            excl_metrics = compute_metrics(excl_nav)

            contributions[comp.name] = {
                "delta_sharpe": round(
                    base_metrics["sharpe_ratio"] - excl_metrics["sharpe_ratio"], 4
                ),
                "delta_ar": round(
                    base_metrics["annualized_return"] - excl_metrics["annualized_return"], 4
                ),
                "delta_mdd": round(
                    base_metrics["max_drawdown"] - excl_metrics["max_drawdown"], 4
                ),
            }

        return contributions

    def correlation_matrix(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """组件间日收益相关系数矩阵。"""
        rets_df = self._align_navs(start_date, end_date)
        return rets_df.corr()

    def _align_navs(
        self, start_date: str | None = None, end_date: str | None = None,
    ) -> pd.DataFrame:
        """对齐所有组件 NAV 到公共日期，返回日收益率 DataFrame。"""
        # 收集所有组件的收益率
        rets_dict: dict[str, pd.Series] = {}
        for comp in self.components:
            rets_dict[comp.name] = comp.returns

        # 合并为 DataFrame，取公共日期
        df = pd.DataFrame(rets_dict).dropna()

        if start_date:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df.index <= pd.Timestamp(end_date)]

        if df.empty:
            raise ValueError("对齐后无公共交易日数据")

        return df

    def _build_combo_nav(
        self, returns_df: pd.DataFrame, weights: dict[str, float],
    ) -> pd.Series:
        """从日收益率构建组合 NAV 曲线（加权求和日收益 → 累计）。"""
        # 组合日收益 = 加权平均各组件收益
        weight_arr = np.array([weights.get(c.name, 0.0) for c in self.components])
        col_order = [c.name for c in self.components]
        aligned_rets = returns_df[col_order].values  # (T, N)

        combo_rets = aligned_rets @ weight_arr  # (T,)
        # 累计 NAV
        nav_values = self.initial_capital * np.cumprod(1 + combo_rets)
        return pd.Series(nav_values, index=returns_df.index, name="combo_nav")
