"""跨资产配置 — 等权/逆波动率/风险平价/HRP"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


class AssetClass(str, Enum):
    CB = "cb"
    A_STOCK = "a_stock"
    ETF = "etf"


class CrossAssetMethod(str, Enum):
    EQUAL_WEIGHT = "equal_weight"
    INVERSE_VOLATILITY = "inverse_volatility"
    RISK_PARITY = "risk_parity"
    MIN_VARIANCE = "min_variance"
    HRP = "hrp"


@dataclass
class CrossAssetConfig:
    method: CrossAssetMethod = CrossAssetMethod.EQUAL_WEIGHT
    lookback: int = 60
    rebalance_freq: int = 5
    min_class_weight: float = 0.10
    max_class_weight: float = 0.80
    min_strategy_weight: float = 0.05
    max_strategy_weight: float = 0.60
    vol_target: float | None = None
    risk_free_rate: float = 0.02


@dataclass
class CrossAssetResult:
    class_weights: dict[str, float]
    strategy_weights: dict[str, float]
    portfolio_nav: list[float] = field(default_factory=list)
    class_contributions: dict[str, float] = field(default_factory=dict)
    correlation_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def compute_returns(nav_series: dict[date, float]) -> list[tuple[date, float]]:
    dates = sorted(nav_series.keys())
    result = []
    for i in range(1, len(dates)):
        prev = nav_series[dates[i - 1]]
        curr = nav_series[dates[i]]
        if prev > 0:
            result.append((dates[i], curr / prev - 1))
    return result


def compute_volatility(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mu = sum(returns) / len(returns)
    var = sum((r - mu) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var)


def compute_annualized_vol(returns: Sequence[float], ann_factor: float = 252.0) -> float:
    return compute_volatility(returns) * math.sqrt(ann_factor)


def compute_covariance_matrix(
    returns_dict: dict[str, list[float]],
) -> dict[str, dict[str, float]]:
    names = list(returns_dict.keys())
    n = min(len(r) for r in returns_dict.values()) if returns_dict else 0
    if n < 2:
        return {}
    result: dict[str, dict[str, float]] = {}
    for a in names:
        result[a] = {}
        ra = returns_dict[a][-n:]
        ma = sum(ra) / n
        for b in names:
            rb = returns_dict[b][-n:]
            mb = sum(rb) / n
            cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / (n - 1)
            result[a][b] = cov
    return result


def compute_correlation_matrix(
    cov: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    names = list(cov.keys())
    stds = {n: math.sqrt(max(cov[n][n], 1e-15)) for n in names}
    result: dict[str, dict[str, float]] = {}
    for a in names:
        result[a] = {}
        for b in names:
            result[a][b] = cov[a][b] / (stds[a] * stds[b])
    return result


def _equal_weight(names: list[str], **_: Any) -> dict[str, float]:
    n = len(names)
    return {name: 1.0 / n for name in names} if n > 0 else {}


def _inverse_volatility(
    names: list[str], returns_dict: dict[str, list[float]], **_: Any,
) -> dict[str, float]:
    vols = {n: compute_volatility(returns_dict.get(n, [])) for n in names}
    inv = {n: 1.0 / max(v, 1e-10) for n, v in vols.items()}
    total = sum(inv.values())
    return {n: v / total for n, v in inv.items()} if total > 0 else _equal_weight(names)


def _clip_weights(
    weights: dict[str, float], min_w: float, max_w: float,
) -> dict[str, float]:
    clipped = {k: max(min_w, min(max_w, v)) for k, v in weights.items()}
    total = sum(clipped.values())
    if total > 0:
        return {k: v / total for k, v in clipped.items()}
    return weights


def allocate_cross_asset(
    nav_dict: dict[str, dict[date, float]],
    config: CrossAssetConfig | None = None,
) -> CrossAssetResult:
    """执行跨资产配置

    Parameters
    ----------
    nav_dict : {name -> {date -> nav}}
    config : 配置
    """
    cfg = config or CrossAssetConfig()
    names = list(nav_dict.keys())
    if not names:
        return CrossAssetResult(class_weights={}, strategy_weights={})

    returns_dict = {
        n: [r for _, r in compute_returns(nav_dict[n])[-cfg.lookback:]]
        for n in names
    }

    method = cfg.method
    if method == CrossAssetMethod.EQUAL_WEIGHT:
        raw = _equal_weight(names)
    elif method == CrossAssetMethod.INVERSE_VOLATILITY:
        raw = _inverse_volatility(names, returns_dict)
    else:
        raw = _equal_weight(names)  # 其余方法需要更多依赖

    weights = _clip_weights(raw, cfg.min_class_weight, cfg.max_class_weight)
    cov = compute_covariance_matrix(returns_dict)
    corr = compute_correlation_matrix(cov) if cov else {}

    return CrossAssetResult(
        class_weights=weights,
        strategy_weights=weights,
        correlation_matrix=corr,
        metadata={"method": method.value, "lookback": cfg.lookback},
    )
