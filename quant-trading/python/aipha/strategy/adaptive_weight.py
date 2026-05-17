"""
自适应动态权重分配模块

解决不同策略在不同年份/市场环境下表现差异大的问题：
- 滚动窗口风险平价（Rolling Risk Parity）
- 逆波动率加权（Inverse Volatility）
- 动量追踪加权（Momentum Chasing）
- 滚动窗口最大夏普（Rolling Max Sharpe）
- Regime 自适应（基于市场状态切换权重）
- 回撤惩罚加权（Drawdown Penalty）
- 综合自适应（Composite Adaptive）

所有方法都以 NAV 序列为输入，滚动计算每日权重，避免前视偏差。

使用示例：
    allocator = AdaptiveWeightAllocator(method="rolling_risk_parity", lookback=60)
    result = allocator.compute_portfolio(nav_dict)
    # result.weights_history 包含每日权重快照
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

import numpy as np
from numpy.typing import NDArray

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


class WeightMethod(str, Enum):
    """权重方法枚举"""

    EQUAL = "equal"
    ROLLING_RISK_PARITY = "rolling_risk_parity"
    INVERSE_VOLATILITY = "inverse_volatility"
    MOMENTUM = "momentum"
    ROLLING_MAX_SHARPE = "rolling_max_sharpe"
    REGIME_ADAPTIVE = "regime_adaptive"
    DRAWDOWN_PENALTY = "drawdown_penalty"
    COMPOSITE_ADAPTIVE = "composite_adaptive"


# 方法中文名映射
WEIGHT_METHOD_LABELS: dict[str, str] = {
    WeightMethod.EQUAL: "等权",
    WeightMethod.ROLLING_RISK_PARITY: "滚动风险平价",
    WeightMethod.INVERSE_VOLATILITY: "逆波动率",
    WeightMethod.MOMENTUM: "动量追踪",
    WeightMethod.ROLLING_MAX_SHARPE: "滚动最大夏普",
    WeightMethod.REGIME_ADAPTIVE: "Regime 自适应",
    WeightMethod.DRAWDOWN_PENALTY: "回撤惩罚",
    WeightMethod.COMPOSITE_ADAPTIVE: "综合自适应",
}


@dataclass(frozen=True)
class WeightSnapshot:
    """某日的权重快照"""

    trade_date: date
    weights: dict[str, float]  # {strategy_name: weight}
    method: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdaptivePortfolioResult:
    """自适应组合结果"""

    # 组合 NAV 序列
    portfolio_nav: dict[date, float]

    # 每日权重历史
    weights_history: list[WeightSnapshot]

    # 方法名
    method: str

    # 参数
    params: dict[str, Any]

    # 各策略 NAV（归一化后的输入）
    strategy_navs: dict[str, dict[date, float]]

    # 组合指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0

    # 汇总：年度权重平均
    yearly_avg_weights: dict[int, dict[str, float]] = field(default_factory=dict)

    def get_weights_at(self, d: date) -> dict[str, float] | None:
        """获取指定日期的权重"""
        for snap in self.weights_history:
            if snap.trade_date == d:
                return snap.weights
        return None

    def get_weight_df_data(self) -> list[dict]:
        """转为可用于 DataFrame 的行列表"""
        rows = []
        for snap in self.weights_history:
            row: dict[str, Any] = {"日期": snap.trade_date}
            for name, w in snap.weights.items():
                row[name] = w
            rows.append(row)
        return rows


def _nav_to_returns(nav: dict[date, float], dates: list[date]) -> NDArray:
    """从 NAV 序列提取日收益率（对齐到 dates）"""
    vals = np.array([nav[d] for d in dates])
    rets = np.diff(vals) / vals[:-1]
    return rets


def _compute_metrics(
    nav_arr: NDArray,
    trading_days: int = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float = 0.02,
) -> dict[str, float]:
    """计算绩效指标"""
    if len(nav_arr) < 2:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }
    total_ret = nav_arr[-1] / nav_arr[0] - 1
    n_years = len(nav_arr) / trading_days
    ann_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 and total_ret > -1 else total_ret

    rets = np.diff(nav_arr) / nav_arr[:-1]
    vol = float(np.std(rets, ddof=1) * np.sqrt(trading_days)) if len(rets) > 1 else 0.0

    daily_rf = risk_free_rate / trading_days
    excess = rets - daily_rf
    sharpe = float(np.mean(excess) * np.sqrt(trading_days) / np.std(rets, ddof=1)) if np.std(rets, ddof=1) > 0 else 0.0

    cummax = np.maximum.accumulate(nav_arr)
    dd = (cummax - nav_arr) / cummax
    max_dd = float(np.max(dd))

    return {
        "total_return": total_ret,
        "annualized_return": ann_ret,
        "volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
    }


def _yearly_avg_weights(
    weights_history: list[WeightSnapshot],
) -> dict[int, dict[str, float]]:
    """计算每年的平均权重"""
    from collections import defaultdict

    yearly: dict[int, list[dict[str, float]]] = defaultdict(list)
    for snap in weights_history:
        yearly[snap.trade_date.year].append(snap.weights)

    result: dict[int, dict[str, float]] = {}
    for year, ws_list in sorted(yearly.items()):
        names = ws_list[0].keys()
        avg: dict[str, float] = {}
        for name in names:
            avg[name] = float(np.mean([w.get(name, 0.0) for w in ws_list]))
        result[year] = avg
    return result


class AdaptiveWeightAllocator:
    """自适应动态权重分配器

    以日频滚动计算策略间权重，避免前视偏差。
    """

    def __init__(
        self,
        method: str | WeightMethod = WeightMethod.ROLLING_RISK_PARITY,
        lookback: int = 60,
        rebalance_freq: int = 5,
        min_weight: float = 0.05,
        max_weight: float = 0.80,
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
        **kwargs: Any,
    ):
        """初始化

        Args:
            method: 权重方法
            lookback: 回看窗口（交易日数）
            rebalance_freq: 再平衡频率（每 N 个交易日重新计算权重）
            min_weight: 单策略最小权重
            max_weight: 单策略最大权重
            risk_free_rate: 无风险利率
            trading_days_per_year: 年交易日数
            **kwargs: 方法特定参数
        """
        self.method = WeightMethod(method) if isinstance(method, str) else method
        self.lookback = lookback
        self.rebalance_freq = rebalance_freq
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.risk_free_rate = risk_free_rate
        self.trading_days = trading_days_per_year
        self.extra_params = kwargs

    def _clip_weights(self, weights: dict[str, float]) -> dict[str, float]:
        """裁剪并归一化权重（迭代收敛，确保 min/max 约束严格满足）"""
        n = len(weights)
        if n == 0:
            return {}

        result = dict(weights)
        for _ in range(20):  # 最多迭代 20 次
            clipped = {
                name: max(self.min_weight, min(self.max_weight, w))
                for name, w in result.items()
            }
            total = sum(clipped.values())
            if total <= 0:
                return {name: 1.0 / n for name in weights}
            result = {name: w / total for name, w in clipped.items()}
            # 检查是否收敛
            if all(
                self.min_weight - 1e-10 <= w <= self.max_weight + 1e-10
                for w in result.values()
            ):
                break
        return result

    def _equal_weights(self, names: list[str]) -> dict[str, float]:
        """等权"""
        n = len(names)
        return {name: 1.0 / n for name in names}

    def _rolling_risk_parity(
        self,
        names: list[str],
        returns_matrix: NDArray,
    ) -> dict[str, float]:
        """滚动风险平价：权重与波动率成反比"""
        vols = np.std(returns_matrix, axis=1, ddof=1)

        if np.any(vols == 0) or np.any(np.isnan(vols)):
            return self._equal_weights(names)

        inv_vols = 1.0 / vols
        raw_weights = inv_vols / np.sum(inv_vols)
        return {name: float(raw_weights[i]) for i, name in enumerate(names)}

    def _inverse_volatility(
        self,
        names: list[str],
        returns_matrix: NDArray,
    ) -> dict[str, float]:
        """逆波动率加权（本质与风险平价相同，但用年化波动率）"""
        vols = np.std(returns_matrix, axis=1, ddof=1) * np.sqrt(self.trading_days)

        if np.any(vols == 0) or np.any(np.isnan(vols)):
            return self._equal_weights(names)

        inv_vols = 1.0 / vols
        raw_weights = inv_vols / np.sum(inv_vols)
        return {name: float(raw_weights[i]) for i, name in enumerate(names)}

    def _momentum_chasing(
        self,
        names: list[str],
        returns_matrix: NDArray,
    ) -> dict[str, float]:
        """动量追踪：最近表现好的策略获得更多权重

        基于滚动窗口内的累积收益率正比分配。
        对负收益率策略施加惩罚但保留最低权重。
        """
        # 每个策略在窗口内的累计收益率
        cum_rets = np.prod(1 + returns_matrix, axis=1) - 1

        # 对负值施加 softmax 以保证全正
        shifted = cum_rets - np.min(cum_rets)
        temperature = self.extra_params.get("momentum_temperature", 1.0)

        if np.max(shifted) == 0:
            return self._equal_weights(names)

        # 使用 softmax 确保全正且差异适中
        exp_vals = np.exp(shifted / (temperature * np.std(cum_rets) + 1e-8))
        raw_weights = exp_vals / np.sum(exp_vals)
        return {name: float(raw_weights[i]) for i, name in enumerate(names)}

    def _rolling_max_sharpe(
        self,
        names: list[str],
        returns_matrix: NDArray,
    ) -> dict[str, float]:
        """滚动最大夏普：基于窗口内夏普比率正比分配"""
        n = len(names)
        daily_rf = self.risk_free_rate / self.trading_days

        sharpes = np.zeros(n)
        for i in range(n):
            rets = returns_matrix[i]
            excess = rets - daily_rf
            std = np.std(rets, ddof=1)
            if std > 0:
                sharpes[i] = np.mean(excess) / std
            else:
                sharpes[i] = 0.0

        # Sharpe 可能为负，使用 softmax
        if np.all(sharpes == 0):
            return self._equal_weights(names)

        temperature = self.extra_params.get("sharpe_temperature", 1.0)
        shifted = sharpes - np.min(sharpes)
        exp_vals = np.exp(shifted / (temperature * np.std(sharpes) + 1e-8))
        raw_weights = exp_vals / np.sum(exp_vals)
        return {name: float(raw_weights[i]) for i, name in enumerate(names)}

    def _regime_adaptive(
        self,
        names: list[str],
        returns_matrix: NDArray,
        market_returns: NDArray | None = None,
    ) -> dict[str, float]:
        """Regime 自适应：基于市场状态切换权重

        Regime 判定：
        - 上涨（牛市）：近期收益 > 0 且波动率正常 → 给进攻型策略更多权重
        - 下跌（熊市）：近期收益 < 0 → 给防守型策略更多权重
        - 震荡（震荡市）：波动率高但无明确方向 → 风险平价

        用各策略在窗口内的表现来判断其在不同 regime 下的适应性。
        """
        # 使用策略收益的均值判断 regime
        if market_returns is not None:
            avg_ret = float(np.mean(market_returns))
            vol = float(np.std(market_returns, ddof=1))
        else:
            # 用所有策略的平均收益序列作为"市场"代理
            avg_market = np.mean(returns_matrix, axis=0)
            avg_ret = float(np.mean(avg_market))
            vol = float(np.std(avg_market, ddof=1))

        vol_threshold = self.extra_params.get("vol_threshold", 0.015)

        if avg_ret > 0 and vol < vol_threshold:
            regime = "bull"
        elif avg_ret < 0:
            regime = "bear"
        else:
            regime = "sideways"

        if regime == "sideways":
            return self._rolling_risk_parity(names, returns_matrix)

        # 在牛/熊市中，用夏普来判断策略适应性
        daily_rf = self.risk_free_rate / self.trading_days
        sharpes = np.zeros(len(names))
        for i in range(len(names)):
            rets = returns_matrix[i]
            excess = rets - daily_rf
            std = np.std(rets, ddof=1)
            if std > 0:
                sharpes[i] = np.mean(excess) / std

        # 牛市给高夏普更多权重，熊市给低波动（高夏普）更多权重
        # 使用 softmax
        temperature = 0.5 if regime == "bull" else 0.3  # 熊市更集中
        shifted = sharpes - np.min(sharpes)
        denom = temperature * np.std(sharpes) + 1e-8
        exp_vals = np.exp(shifted / denom)
        raw_weights = exp_vals / np.sum(exp_vals)
        return {name: float(raw_weights[i]) for i, name in enumerate(names)}

    def _drawdown_penalty(
        self,
        names: list[str],
        returns_matrix: NDArray,
        nav_matrix: NDArray,
    ) -> dict[str, float]:
        """回撤惩罚：正在大幅回撤的策略降权

        权重 ∝ 夏普 × (1 - 当前回撤比/最大容忍回撤)
        """
        n = len(names)
        max_tolerable_dd = self.extra_params.get("max_tolerable_dd", 0.15)
        daily_rf = self.risk_free_rate / self.trading_days

        scores = np.ones(n)
        for i in range(n):
            rets = returns_matrix[i]
            nav = nav_matrix[i]

            # 夏普分
            excess = rets - daily_rf
            std = np.std(rets, ddof=1)
            sharpe_score = np.mean(excess) / std if std > 0 else 0.0

            # 当前回撤
            peak = np.maximum.accumulate(nav)
            current_dd = (peak[-1] - nav[-1]) / peak[-1] if peak[-1] > 0 else 0.0

            # 回撤惩罚因子：回撤越大惩罚越重
            dd_factor = max(0.1, 1.0 - current_dd / max_tolerable_dd)

            scores[i] = max(0.01, (sharpe_score + 1.0) * dd_factor)

        raw_weights = scores / np.sum(scores)
        return {name: float(raw_weights[i]) for i, name in enumerate(names)}

    def _composite_adaptive(
        self,
        names: list[str],
        returns_matrix: NDArray,
        nav_matrix: NDArray,
    ) -> dict[str, float]:
        """综合自适应：融合多种信号

        加权平均：
        - 逆波动率     (30%)
        - 动量追踪     (25%)
        - 回撤惩罚     (25%)
        - 风险平价     (20%)
        """
        w_components = self.extra_params.get(
            "composite_weights",
            {"inv_vol": 0.30, "momentum": 0.25, "dd_penalty": 0.25, "risk_parity": 0.20},
        )

        w_inv = self._inverse_volatility(names, returns_matrix)
        w_mom = self._momentum_chasing(names, returns_matrix)
        w_dd = self._drawdown_penalty(names, returns_matrix, nav_matrix)
        w_rp = self._rolling_risk_parity(names, returns_matrix)

        merged: dict[str, float] = {name: 0.0 for name in names}
        for name in names:
            merged[name] = (
                w_components["inv_vol"] * w_inv[name]
                + w_components["momentum"] * w_mom[name]
                + w_components["dd_penalty"] * w_dd[name]
                + w_components["risk_parity"] * w_rp[name]
            )

        total = sum(merged.values())
        if total > 0:
            merged = {name: w / total for name, w in merged.items()}
        return merged

    def _compute_weights_for_window(
        self,
        names: list[str],
        returns_matrix: NDArray,
        nav_matrix: NDArray | None = None,
    ) -> dict[str, float]:
        """根据方法计算权重"""
        raw: dict[str, float]

        if self.method == WeightMethod.EQUAL:
            raw = self._equal_weights(names)
        elif self.method == WeightMethod.ROLLING_RISK_PARITY:
            raw = self._rolling_risk_parity(names, returns_matrix)
        elif self.method == WeightMethod.INVERSE_VOLATILITY:
            raw = self._inverse_volatility(names, returns_matrix)
        elif self.method == WeightMethod.MOMENTUM:
            raw = self._momentum_chasing(names, returns_matrix)
        elif self.method == WeightMethod.ROLLING_MAX_SHARPE:
            raw = self._rolling_max_sharpe(names, returns_matrix)
        elif self.method == WeightMethod.REGIME_ADAPTIVE:
            raw = self._regime_adaptive(names, returns_matrix)
        elif self.method == WeightMethod.DRAWDOWN_PENALTY:
            if nav_matrix is None:
                raw = self._equal_weights(names)
            else:
                raw = self._drawdown_penalty(names, returns_matrix, nav_matrix)
        elif self.method == WeightMethod.COMPOSITE_ADAPTIVE:
            if nav_matrix is None:
                raw = self._rolling_risk_parity(names, returns_matrix)
            else:
                raw = self._composite_adaptive(names, returns_matrix, nav_matrix)
        else:
            raw = self._equal_weights(names)

        return self._clip_weights(raw)

    def compute_portfolio(
        self,
        strategy_navs: dict[str, dict[date, float]],
    ) -> AdaptivePortfolioResult:
        """计算自适应组合

        Args:
            strategy_navs: {策略名: {日期: NAV}} - NAV 应已归一化到 1.0 起始

        Returns:
            自适应组合结果
        """
        if not strategy_navs:
            return AdaptivePortfolioResult(
                portfolio_nav={},
                weights_history=[],
                method=self.method.value,
                params=self._get_params(),
                strategy_navs={},
            )

        names = list(strategy_navs.keys())
        n = len(names)

        # 找公共日期
        all_dates_set: set[date] = set()
        for nav in strategy_navs.values():
            if not all_dates_set:
                all_dates_set = set(nav.keys())
            else:
                all_dates_set &= set(nav.keys())

        common_dates = sorted(all_dates_set)
        if len(common_dates) < 2:
            return AdaptivePortfolioResult(
                portfolio_nav={},
                weights_history=[],
                method=self.method.value,
                params=self._get_params(),
                strategy_navs=strategy_navs,
            )

        # 构建 NAV 矩阵 (n_strategies, n_dates)
        nav_matrix = np.zeros((n, len(common_dates)))
        for i, name in enumerate(names):
            for j, d in enumerate(common_dates):
                nav_matrix[i, j] = strategy_navs[name][d]

        # 归一化到 1.0 起始
        for i in range(n):
            if nav_matrix[i, 0] > 0:
                nav_matrix[i, :] = nav_matrix[i, :] / nav_matrix[i, 0]

        # 收益率矩阵 (n_strategies, n_dates-1)
        returns_matrix = np.diff(nav_matrix, axis=1) / nav_matrix[:, :-1]

        # 滚动计算权重
        weights_history: list[WeightSnapshot] = []
        portfolio_nav: dict[date, float] = {}

        # 初始等权
        current_weights = self._equal_weights(names)
        last_rebalance_idx = 0

        # 第一天：初始NAV = 1.0
        portfolio_nav[common_dates[0]] = 1.0
        weights_history.append(WeightSnapshot(
            trade_date=common_dates[0],
            weights=current_weights.copy(),
            method=self.method.value,
        ))

        portfolio_value = 1.0

        for t in range(1, len(common_dates)):
            # 使用当前权重计算今日组合收益
            daily_portfolio_ret = 0.0
            for i, name in enumerate(names):
                daily_portfolio_ret += current_weights[name] * returns_matrix[i, t - 1]

            portfolio_value *= (1 + daily_portfolio_ret)
            portfolio_nav[common_dates[t]] = portfolio_value

            # 判断是否需要 rebalance
            should_rebalance = (t - last_rebalance_idx) >= self.rebalance_freq
            has_enough_data = t >= self.lookback

            if should_rebalance and has_enough_data:
                # 取回看窗口
                window_start = max(0, t - self.lookback)
                window_returns = returns_matrix[:, window_start:t]
                window_nav = nav_matrix[:, window_start:t + 1]

                new_weights = self._compute_weights_for_window(
                    names, window_returns, window_nav
                )
                current_weights = new_weights
                last_rebalance_idx = t

            weights_history.append(WeightSnapshot(
                trade_date=common_dates[t],
                weights=current_weights.copy(),
                method=self.method.value,
            ))

        # 计算组合指标
        nav_arr = np.array([portfolio_nav[d] for d in common_dates])
        metrics = _compute_metrics(nav_arr, self.trading_days, self.risk_free_rate)

        # 年度平均权重
        y_avg = _yearly_avg_weights(weights_history)

        return AdaptivePortfolioResult(
            portfolio_nav=portfolio_nav,
            weights_history=weights_history,
            method=self.method.value,
            params=self._get_params(),
            strategy_navs={
                name: {d: float(nav_matrix[i, j]) for j, d in enumerate(common_dates)}
                for i, name in enumerate(names)
            },
            total_return=metrics["total_return"],
            annualized_return=metrics["annualized_return"],
            volatility=metrics["volatility"],
            sharpe=metrics["sharpe"],
            max_drawdown=metrics["max_drawdown"],
            yearly_avg_weights=y_avg,
        )

    def _get_params(self) -> dict[str, Any]:
        """获取参数快照"""
        return {
            "method": self.method.value,
            "lookback": self.lookback,
            "rebalance_freq": self.rebalance_freq,
            "min_weight": self.min_weight,
            "max_weight": self.max_weight,
            **self.extra_params,
        }


def compare_weight_methods(
    strategy_navs: dict[str, dict[date, float]],
    methods: list[str | WeightMethod] | None = None,
    lookback: int = 60,
    rebalance_freq: int = 5,
    **kwargs: Any,
) -> dict[str, AdaptivePortfolioResult]:
    """便捷函数：对比多种权重方法

    Args:
        strategy_navs: {策略名: {日期: NAV}}
        methods: 要对比的方法列表，None 则使用全部
        lookback: 回看窗口
        rebalance_freq: 再平衡频率
        **kwargs: 传递给 allocator 的额外参数

    Returns:
        {方法名: 组合结果}
    """
    if methods is None:
        methods = list(WeightMethod)

    results: dict[str, AdaptivePortfolioResult] = {}
    for method in methods:
        m = WeightMethod(method) if isinstance(method, str) else method
        allocator = AdaptiveWeightAllocator(
            method=m,
            lookback=lookback,
            rebalance_freq=rebalance_freq,
            **kwargs,
        )
        results[m.value] = allocator.compute_portfolio(strategy_navs)

    return results


def format_comparison_report(
    results: dict[str, AdaptivePortfolioResult],
) -> str:
    """格式化对比报告"""
    lines = [
        "=" * 70,
        "自适应权重方法对比报告",
        "=" * 70,
        "",
    ]

    # 表格头
    lines.append(
        f"{'方法':<20} {'总收益':>10} {'年化':>10} {'夏普':>8} {'最大回撤':>10} {'波动率':>10}"
    )
    lines.append("-" * 70)

    for method_name, result in sorted(
        results.items(), key=lambda x: x[1].sharpe, reverse=True
    ):
        label = WEIGHT_METHOD_LABELS.get(method_name, method_name)
        lines.append(
            f"{label:<20} "
            f"{result.total_return:>9.1%} "
            f"{result.annualized_return:>9.1%} "
            f"{result.sharpe:>7.3f} "
            f"{result.max_drawdown:>9.1%} "
            f"{result.volatility:>9.1%}"
        )

    # 年度权重分析（取综合自适应或第一个方法）
    if not results:
        lines.extend(["", "=" * 70])
        return "\n".join(lines)
    first_key = next(iter(results))
    first_result = results[first_key]
    if first_result.yearly_avg_weights:
        lines.extend(["", "", "【年度平均权重示例】", f"（方法: {WEIGHT_METHOD_LABELS.get(first_key, first_key)}）", ""])
        for year, weights in sorted(first_result.yearly_avg_weights.items()):
            w_str = ", ".join(f"{name}: {w:.1%}" for name, w in weights.items())
            lines.append(f"  {year}: {w_str}")

    lines.extend(["", "=" * 70])
    return "\n".join(lines)
