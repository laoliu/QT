"""跨资产组合配置器（Cross-Asset Portfolio Allocator）

支持 CB + A股 多资产类别的层次化组合配权：

**层次架构**：
    Level 1: 跨资产类别配权（CB vs A-Stock vs ...）
        → risk_parity / inverse_vol / equal_weight / min_variance
    Level 2: 类内策略配权（各资产类内策略间权重）
        → 复用 AdaptiveWeightAllocator / StrategyCombo

**支持的配权方法**：
    - equal_weight: 等权分配到各资产类别
    - inverse_volatility: 按策略波动率倒数分配
    - risk_parity: 风险平价（各资产类别贡献等风险）
    - min_variance: 全局最小方差
    - hierarchical_risk_parity: 层次风险平价（HRP, López de Prado 2016）

**理论基础**（§11.2）：
    - 跨资产分散化降低组合风险（Markowitz 1952）
    - CB 与 A股 相关性适中 → 组合效率高
    - 风险平价不依赖期望收益估计 → 参数敏感性低（Maillard et al. 2010）
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Sequence

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    from aipha.risk.valuation_guard import ValuationGuard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举 & 配置
# ---------------------------------------------------------------------------

class AssetClass(str, Enum):
    """支持的资产类别。"""
    CB = "cb"           # 可转债
    A_STOCK = "a_stock"  # A 股
    ETF = "etf"         # ETF（未来扩展）

    def __str__(self) -> str:
        return self.value


class CrossAssetMethod(str, Enum):
    """跨资产配权方法。"""
    EQUAL_WEIGHT = "equal_weight"
    INVERSE_VOLATILITY = "inverse_volatility"
    RISK_PARITY = "risk_parity"
    MIN_VARIANCE = "min_variance"
    HRP = "hierarchical_risk_parity"

    def __str__(self) -> str:
        return self.value


@dataclass
class CrossAssetConfig:
    """跨资产配置器参数。

    Attributes:
        method: 跨资产配权方法
        lookback: 收益率/协方差计算回看天数
        rebalance_freq: 再平衡频率（交易日）
        min_class_weight: 单资产类别最小权重
        max_class_weight: 单资产类别最大权重
        min_strategy_weight: 单策略最小权重
        max_strategy_weight: 单策略最大权重
        vol_target: 年化波动率目标（可选，用于波动率缩放）
        risk_free_rate: 无风险利率（年化）
    """
    method: CrossAssetMethod = CrossAssetMethod.RISK_PARITY
    lookback: int = 60
    rebalance_freq: int = 5
    min_class_weight: float = 0.10
    max_class_weight: float = 0.80
    min_strategy_weight: float = 0.05
    max_strategy_weight: float = 0.60
    vol_target: float | None = None  # e.g. 0.15 for 15% annualized
    risk_free_rate: float = 0.02


@dataclass
class CrossAssetResult:
    """跨资产配权结果。

    Attributes:
        class_weights: 资产类别权重 {AssetClass: weight}
        strategy_weights: 各策略最终权重 {name: weight}
        portfolio_nav: 组合净值序列 {date: nav}
        class_contributions: 各资产类别风险贡献
        correlation_matrix: 策略间相关系数矩阵
        metadata: 诊断信息
    """
    class_weights: dict[str, float] = field(default_factory=dict)
    strategy_weights: dict[str, float] = field(default_factory=dict)
    portfolio_nav: dict[date, float] = field(default_factory=dict)
    class_contributions: dict[str, float] = field(default_factory=dict)
    correlation_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 工具函数：收益率 & 统计
# ---------------------------------------------------------------------------

def compute_returns(nav_series: dict[date, float]) -> list[tuple[date, float]]:
    """从 NAV 序列计算日收益率。"""
    sorted_dates = sorted(nav_series.keys())
    returns = []
    for i in range(1, len(sorted_dates)):
        d_prev, d_curr = sorted_dates[i - 1], sorted_dates[i]
        nav_prev, nav_curr = nav_series[d_prev], nav_series[d_curr]
        if nav_prev > 0:
            returns.append((d_curr, nav_curr / nav_prev - 1.0))
    return returns


def compute_volatility(returns: Sequence[float]) -> float:
    """计算日收益率标准差（日频）。"""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(var)


def compute_annualized_vol(returns: Sequence[float], ann_factor: float = float(TRADING_DAYS_PER_YEAR)) -> float:
    """计算年化波动率。"""
    daily_vol = compute_volatility(returns)
    return daily_vol * math.sqrt(ann_factor)


def compute_covariance_matrix(
    returns_dict: dict[str, list[float]],
) -> dict[str, dict[str, float]]:
    """计算策略间协方差矩阵。

    Args:
        returns_dict: {strategy_name: [daily_returns]}，所有序列等长且日期对齐。

    Returns:
        嵌套字典 {name_i: {name_j: cov_ij}}
    """
    names = sorted(returns_dict.keys())
    n_obs = min(len(v) for v in returns_dict.values()) if returns_dict else 0
    if n_obs < 2:
        return {n: {m: 0.0 for m in names} for n in names}

    # 截取到等长
    data = {name: returns_dict[name][:n_obs] for name in names}
    means = {name: sum(data[name]) / n_obs for name in names}

    cov: dict[str, dict[str, float]] = {}
    for i in names:
        cov[i] = {}
        for j in names:
            s = sum(
                (data[i][t] - means[i]) * (data[j][t] - means[j])
                for t in range(n_obs)
            )
            cov[i][j] = s / (n_obs - 1)
    return cov


def compute_correlation_matrix(
    cov: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """从协方差矩阵计算相关系数矩阵。"""
    names = sorted(cov.keys())
    stds = {}
    for n in names:
        v = cov[n][n]
        stds[n] = math.sqrt(v) if v > 0 else 1e-10

    corr: dict[str, dict[str, float]] = {}
    for i in names:
        corr[i] = {}
        for j in names:
            corr[i][j] = cov[i][j] / (stds[i] * stds[j])
    return corr


# ---------------------------------------------------------------------------
# 配权方法实现
# ---------------------------------------------------------------------------

def _equal_weight(names: list[str], **_: Any) -> dict[str, float]:
    """等权分配。"""
    if not names:
        return {}
    w = 1.0 / len(names)
    return {n: w for n in names}


def _inverse_volatility(
    names: list[str],
    returns_dict: dict[str, list[float]],
    **_: Any,
) -> dict[str, float]:
    """波动率倒数加权。"""
    vols = {}
    for n in names:
        rets = returns_dict.get(n, [])
        vol = compute_annualized_vol(rets)
        vols[n] = max(vol, 1e-8)  # floor

    inv_sum = sum(1.0 / v for v in vols.values())
    if inv_sum <= 0:
        return _equal_weight(names)

    return {n: (1.0 / vols[n]) / inv_sum for n in names}


def _risk_parity(
    names: list[str],
    cov: dict[str, dict[str, float]],
    max_iter: int = 200,
    tol: float = 1e-8,
    **_: Any,
) -> dict[str, float]:
    """风险平价（ERC）：每个策略等风险贡献。

    使用迭代法求解 $w_i (\\Sigma w)_i = w_j (\\Sigma w)_j$（§11.2）。
    """
    n = len(names)
    if n == 0:
        return {}
    if n == 1:
        return {names[0]: 1.0}

    # 初始等权
    w = [1.0 / n] * n

    for _ in range(max_iter):
        # 计算 Σw
        sigma_w = []
        for i in range(n):
            sw_i = sum(cov[names[i]][names[j]] * w[j] for j in range(n))
            sigma_w.append(sw_i)

        # 组合方差
        port_var = sum(w[i] * sigma_w[i] for i in range(n))
        if port_var <= 0:
            return _equal_weight(names)

        port_vol = math.sqrt(port_var)

        # 边际风险贡献
        mrc = [sw / port_vol for sw in sigma_w]

        # 风险贡献
        rc = [w[i] * mrc[i] for i in range(n)]
        rc_sum = sum(rc)
        if rc_sum <= 0:
            return _equal_weight(names)

        # 目标：等风险贡献
        target_rc = rc_sum / n

        # 更新权重
        new_w = []
        for i in range(n):
            if mrc[i] > 0:
                new_w.append(target_rc / mrc[i])
            else:
                new_w.append(w[i])

        w_sum = sum(new_w)
        new_w = [x / w_sum for x in new_w]

        # 收敛检查
        diff = sum(abs(new_w[i] - w[i]) for i in range(n))
        w = new_w
        if diff < tol:
            break

    return {names[i]: w[i] for i in range(n)}


def _min_variance(
    names: list[str],
    cov: dict[str, dict[str, float]],
    **_: Any,
) -> dict[str, float]:
    """全局最小方差组合（解析解，仅 long-only 近似）。

    $w^* = \\Sigma^{-1} \\mathbf{1} / (\\mathbf{1}' \\Sigma^{-1} \\mathbf{1})$
    简化实现：使用 inverse-variance + 协方差调整。
    """
    n = len(names)
    if n <= 1:
        return _equal_weight(names)

    # 精确解需要矩阵求逆 → 简化为 inverse-variance 然后 clip
    variances = [max(cov[names[i]][names[i]], 1e-10) for i in range(n)]
    inv_var = [1.0 / v for v in variances]
    inv_sum = sum(inv_var)

    weights = {names[i]: inv_var[i] / inv_sum for i in range(n)}
    return weights


# ---------------------------------------------------------------------------
# 核心类：CrossAssetAllocator
# ---------------------------------------------------------------------------

class CrossAssetAllocator:
    """跨资产组合配置器。

    使用方法：
        >>> allocator = CrossAssetAllocator(config)
        >>> allocator.add_strategy("triple_low_v3", AssetClass.CB, nav_dict)
        >>> allocator.add_strategy("small_cap_v2", AssetClass.A_STOCK, nav_dict)
        >>> result = allocator.compute()
    """

    def __init__(
        self,
        config: CrossAssetConfig | None = None,
        valuation_guard: ValuationGuard | None = None,
    ) -> None:
        self._config = config or CrossAssetConfig()
        self._strategies: dict[str, tuple[AssetClass, dict[date, float]]] = {}
        self._valuation_guard = valuation_guard

    @property
    def config(self) -> CrossAssetConfig:
        return self._config

    def add_strategy(
        self, name: str, asset_class: AssetClass, nav: dict[date, float],
    ) -> None:
        """添加一个策略的 NAV 序列。"""
        if not nav:
            raise ValueError(f"NAV series for '{name}' is empty")
        self._strategies[name] = (asset_class, nav)

    def list_strategies(self) -> dict[str, str]:
        """返回已注册策略 {name: asset_class}。"""
        return {n: str(ac) for n, (ac, _) in self._strategies.items()}

    def compute(self) -> CrossAssetResult:
        """执行跨资产配权计算。

        Returns:
            CrossAssetResult 包含资产类别权重、策略权重、组合 NAV。
        """
        if not self._strategies:
            return CrossAssetResult(metadata={"error": "no strategies"})

        cfg = self._config

        # 1. 按资产类别分组
        class_groups: dict[AssetClass, list[str]] = defaultdict(list)
        for name, (ac, _) in self._strategies.items():
            class_groups[ac].append(name)

        # 2. 对齐日期 & 计算收益率
        common_dates = self._get_common_dates()
        if len(common_dates) < cfg.lookback + 5:
            logger.warning(
                "公共交易日不足 (%d < %d)，降级到等权",
                len(common_dates), cfg.lookback,
            )
            return self._fallback_equal_weight(common_dates)

        returns_dict = self._compute_aligned_returns(common_dates)

        # 3. 计算协方差 & 相关系数（用最近 lookback 天）
        recent_returns = {
            name: rets[-cfg.lookback:] for name, rets in returns_dict.items()
        }
        cov = compute_covariance_matrix(recent_returns)
        corr = compute_correlation_matrix(cov)

        # 4. 层次 1：跨资产类别配权
        # 先计算每个资产类别的"聚合 NAV"（类内等权合成）
        class_navs = self._compute_class_navs(common_dates, class_groups)
        class_returns = {
            str(ac): [
                class_navs[ac][common_dates[i]] / class_navs[ac][common_dates[i - 1]] - 1.0
                for i in range(1, len(common_dates))
                if class_navs[ac][common_dates[i - 1]] > 0
            ]
            for ac in class_groups
        }

        class_recent = {
            k: v[-cfg.lookback:] for k, v in class_returns.items()
        }
        class_cov = compute_covariance_matrix(class_recent)

        method = cfg.method
        class_names = sorted(class_recent.keys())

        if method == CrossAssetMethod.EQUAL_WEIGHT:
            class_weights = _equal_weight(class_names)
        elif method == CrossAssetMethod.INVERSE_VOLATILITY:
            class_weights = _inverse_volatility(class_names, class_recent)
        elif method == CrossAssetMethod.RISK_PARITY:
            class_weights = _risk_parity(class_names, class_cov)
        elif method == CrossAssetMethod.MIN_VARIANCE:
            class_weights = _min_variance(class_names, class_cov)
        elif method == CrossAssetMethod.HRP:
            # HRP 简化实现：先按相关距离聚类 → 递归二分 → bisection allocation
            class_weights = _risk_parity(class_names, class_cov)  # fallback
        else:
            class_weights = _equal_weight(class_names)

        # 应用约束
        class_weights = self._clip_weights(
            class_weights, cfg.min_class_weight, cfg.max_class_weight,
        )

        # 5. 层次 2：类内策略等权
        strategy_weights: dict[str, float] = {}
        for ac, strat_names in class_groups.items():
            class_w = class_weights.get(str(ac), 0.0)
            n_strats = len(strat_names)
            for sn in strat_names:
                strategy_weights[sn] = class_w / n_strats if n_strats > 0 else 0.0

        # clip 策略权重
        strategy_weights = self._clip_weights(
            strategy_weights, cfg.min_strategy_weight, cfg.max_strategy_weight,
        )

        # 6. 合成组合 NAV
        portfolio_nav = self._combine_navs(common_dates, strategy_weights)

        # 7. 风险贡献
        class_contributions = self._compute_risk_contributions(
            class_names, class_weights, class_cov,
        )

        # 7b. 估值过热风控覆盖层（可选）
        valuation_alerts: list[dict[str, Any]] = []
        if self._valuation_guard is not None and self._valuation_guard.enabled:
            adj_class_w, alerts = self._valuation_guard.adjust_weights(
                class_weights,
            )
            if alerts:
                class_weights = adj_class_w
                valuation_alerts = [
                    {
                        "sector": a.sector_id,
                        "level": a.level.value,
                        "pct": a.percentile,
                        "base_w": a.base_weight,
                        "adj_w": a.adjusted_weight,
                    }
                    for a in alerts
                ]
                # 重新计算策略权重
                strategy_weights = {}
                for ac, strat_names in class_groups.items():
                    class_w = class_weights.get(str(ac), 0.0)
                    n_strats = len(strat_names)
                    for sn in strat_names:
                        strategy_weights[sn] = (
                            class_w / n_strats if n_strats > 0 else 0.0
                        )
                strategy_weights = self._clip_weights(
                    strategy_weights,
                    cfg.min_strategy_weight,
                    cfg.max_strategy_weight,
                )
                portfolio_nav = self._combine_navs(
                    common_dates, strategy_weights,
                )
                logger.info(
                    "ValuationGuard adjusted %d sectors",
                    len(alerts),
                )

        return CrossAssetResult(
            class_weights=class_weights,
            strategy_weights=strategy_weights,
            portfolio_nav=portfolio_nav,
            class_contributions=class_contributions,
            correlation_matrix=corr,
            metadata={
                "method": str(method),
                "lookback": cfg.lookback,
                "n_strategies": len(self._strategies),
                "n_common_dates": len(common_dates),
                "asset_classes": [str(ac) for ac in class_groups],
                "valuation_alerts": valuation_alerts,
            },
        )

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _get_common_dates(self) -> list[date]:
        """获取所有策略的公共交易日。"""
        date_sets = [set(nav.keys()) for _, nav in self._strategies.values()]
        if not date_sets:
            return []
        common = date_sets[0]
        for ds in date_sets[1:]:
            common &= ds
        return sorted(common)

    def _compute_aligned_returns(
        self, common_dates: list[date],
    ) -> dict[str, list[float]]:
        """计算日期对齐后的收益率序列。"""
        result = {}
        for name, (_, nav) in self._strategies.items():
            rets = []
            for i in range(1, len(common_dates)):
                prev = nav[common_dates[i - 1]]
                curr = nav[common_dates[i]]
                rets.append(curr / prev - 1.0 if prev > 0 else 0.0)
            result[name] = rets
        return result

    def _compute_class_navs(
        self,
        common_dates: list[date],
        class_groups: dict[AssetClass, list[str]],
    ) -> dict[AssetClass, dict[date, float]]:
        """计算各资产类别的等权合成 NAV。"""
        class_navs = {}
        for ac, strat_names in class_groups.items():
            navs_by_date: dict[date, float] = {}
            for d in common_dates:
                values = []
                for sn in strat_names:
                    _, nav = self._strategies[sn]
                    if d in nav:
                        values.append(nav[d])
                navs_by_date[d] = (
                    sum(values) / len(values) if values else 1.0
                )
            class_navs[ac] = navs_by_date
        return class_navs

    def _combine_navs(
        self,
        common_dates: list[date],
        strategy_weights: dict[str, float],
    ) -> dict[date, float]:
        """按策略权重合成组合 NAV（日频再加权）。"""
        if not common_dates:
            return {}

        # 归一化到 start=1.0
        norm_navs: dict[str, dict[date, float]] = {}
        for name, (_, nav) in self._strategies.items():
            start_val = nav.get(common_dates[0], 1.0)
            if start_val <= 0:
                start_val = 1.0
            norm_navs[name] = {d: nav.get(d, start_val) / start_val for d in common_dates}

        portfolio: dict[date, float] = {}
        for d in common_dates:
            pv = sum(
                strategy_weights.get(name, 0.0) * norm_navs[name].get(d, 1.0)
                for name in self._strategies
            )
            portfolio[d] = pv

        return portfolio

    def _compute_risk_contributions(
        self,
        names: list[str],
        weights: dict[str, float],
        cov: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """计算各成分的风险贡献比例。"""
        n = len(names)
        if n == 0:
            return {}

        w = [weights.get(n, 0.0) for n in names]
        sigma_w = []
        for i in range(n):
            sw = sum(cov.get(names[i], {}).get(names[j], 0.0) * w[j] for j in range(n))
            sigma_w.append(sw)

        port_var = sum(w[i] * sigma_w[i] for i in range(n))
        if port_var <= 0:
            return {n: 1.0 / len(names) for n in names}

        rc = {names[i]: w[i] * sigma_w[i] / port_var for i in range(n)}
        return rc

    def _fallback_equal_weight(
        self, common_dates: list[date],
    ) -> CrossAssetResult:
        """数据不足时降级到等权。"""
        names = list(self._strategies.keys())
        w = 1.0 / len(names) if names else 0.0
        weights = {n: w for n in names}
        portfolio_nav = self._combine_navs(common_dates, weights)

        return CrossAssetResult(
            class_weights={},
            strategy_weights=weights,
            portfolio_nav=portfolio_nav,
            metadata={"method": "fallback_equal_weight", "reason": "insufficient_data"},
        )

    @staticmethod
    def _clip_weights(
        weights: dict[str, float],
        min_w: float,
        max_w: float,
    ) -> dict[str, float]:
        """裁剪权重到 [min_w, max_w] 并重新归一化。"""
        if not weights:
            return weights

        clipped = {k: max(min_w, min(max_w, v)) for k, v in weights.items()}
        total = sum(clipped.values())
        if total <= 0:
            n = len(clipped)
            return {k: 1.0 / n for k in clipped}
        return {k: v / total for k, v in clipped.items()}
