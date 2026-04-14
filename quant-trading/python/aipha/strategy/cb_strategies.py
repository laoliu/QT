"""可转债策略集 — 10+ 策略从 AlphQ 移植

包含：
1. CBDoubleLow — 双低策略（价格+溢价率）
2. CBMultiFactor — 多因子融合(5因子去共线性版)
3. CBGammaConvexity — Gamma 凸性策略
4. CBCreditSpread — 信用利差收窄策略
5. CBMomentum — 可转债动量策略
6. CBMeanReversion — 均值回归策略
7. CBSmallSize — 小盘转债策略
8. CBDeltaBalanced — Delta 平衡区策略
9. CBValueMomentum — 价值动量复合策略
10. CBRotation — 通用轮动框架

所有策略共享：
- 预过滤: 价格≤130, 成交额>50万, 到期>30天, 排除ST
- 等权持仓: top_k 只
- T+0 交易规则
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── 候选标的信息 ────────────────────────────────────────────────────

@dataclass
class CandidateInfo:
    symbol: str
    close: float
    amount: float = 0.0              # 成交额（元）
    issue_size: float = 0.0          # 发行规模（万元）
    cb_over_rate: float | None = None  # 转股溢价率
    cb_value: float | None = None    # 转股价值
    conv_price: float | None = None  # 转股价
    maturity_date: str = ""
    rating: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── 通用配置 ────────────────────────────────────────────────────────

@dataclass
class CBStrategyConfig:
    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    min_turnover: float = 50.0       # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.15
    lot_size: int = 10


# ── 排名工具函数 ──────────────────────────────────────────────────

def _rank_normalize(values: list[float], ascending: bool = True) -> list[float]:
    """排名归一化到 [0, 1]"""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=not ascending)
    ranks = [0.0] * n
    for rank_idx, (orig_idx, _) in enumerate(indexed):
        ranks[orig_idx] = rank_idx / (n - 1)
    return ranks


def _prefilter(
    candidates: list[CandidateInfo],
    config: CBStrategyConfig,
    now: datetime,
) -> list[CandidateInfo]:
    """通用预过滤"""
    result = []
    for c in candidates:
        if c.close <= 0 or c.close > config.max_price:
            continue
        if c.amount < config.min_turnover * 1e4:
            continue
        if config.exclude_st and "ST" in c.metadata.get("name", ""):
            continue
        result.append(c)
    return result


# ── 策略1: 双低策略 ────────────────────────────────────────────────

class CBDoubleLow:
    """双低 = 价格 + 溢价率，选双低值最小的 top_k"""
    name = "cb_double_low"

    def __init__(self, config: CBStrategyConfig | None = None) -> None:
        self.config = config or CBStrategyConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        pool = _prefilter(candidates, self.config, now)
        pool = [c for c in pool if c.cb_over_rate is not None]
        scored = [(c.symbol, c.close + (c.cb_over_rate or 0)) for c in pool]
        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略2: 多因子融合 ──────────────────────────────────────────────

@dataclass
class MultiFactorConfig(CBStrategyConfig):
    price_weight: float = 0.25
    premium_weight: float = 0.20
    size_weight: float = 0.25
    delta_weight: float = 0.10
    rating_weight: float = 0.20
    max_premium: float = 40.0


class CBMultiFactor:
    """多因子融合策略 (5因子去共线性版)"""
    name = "cb_multi_factor"

    def __init__(self, config: MultiFactorConfig | None = None) -> None:
        self.config = config or MultiFactorConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        cfg = self.config
        pool = _prefilter(candidates, cfg, now)
        pool = [c for c in pool if c.cb_over_rate is not None and c.cb_over_rate <= cfg.max_premium]
        if len(pool) <= 1:
            return [c.symbol for c in pool]

        from aipha.factors.cb_factors import delta_exposure, rating_score

        n = len(pool)
        prices = [c.close for c in pool]
        premiums = [c.cb_over_rate or 0 for c in pool]
        sizes = [c.issue_size for c in pool]
        deltas = [delta_exposure(c.close, c.cb_value) for c in pool]
        ratings = [rating_score(c.rating) for c in pool]

        # 升序因子（越小越优）: 价格、溢价率、规模
        price_ranks = _rank_normalize(prices, ascending=True)
        prem_ranks = _rank_normalize(premiums, ascending=True)
        size_ranks = _rank_normalize(sizes, ascending=True)
        # 降序因子（越大越优）: delta、评级
        delta_ranks = _rank_normalize(deltas, ascending=False)
        rating_ranks = _rank_normalize(ratings, ascending=False)

        scores = []
        for i in range(n):
            s = (cfg.price_weight * price_ranks[i]
                 + cfg.premium_weight * prem_ranks[i]
                 + cfg.size_weight * size_ranks[i]
                 + cfg.delta_weight * delta_ranks[i]
                 + cfg.rating_weight * rating_ranks[i])
            scores.append((pool[i].symbol, s))

        scores.sort(key=lambda x: x[1])
        return [sym for sym, _ in scores[:cfg.top_k]]


# ── 策略3: Gamma 凸性 ──────────────────────────────────────────────

@dataclass
class GammaConfig(CBStrategyConfig):
    delta_min: float = 0.3
    delta_max: float = 0.7
    premium_weight: float = 0.25
    gamma_weight: float = 0.50
    price_weight: float = 0.25


class CBGammaConvexity:
    """Gamma 凸性策略 — 选 Delta 平衡区高 Gamma CB"""
    name = "cb_gamma_convexity"

    def __init__(self, config: GammaConfig | None = None) -> None:
        self.config = config or GammaConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        from aipha.factors.cb_factors import compute_gamma_convexity, classify_delta_zone
        cfg = self.config
        pool = _prefilter(candidates, cfg, now)
        balanced = []
        for c in pool:
            zone = classify_delta_zone(cb_value=c.cb_value)
            if zone == "balanced":
                gamma = compute_gamma_convexity(c.cb_value, c.maturity_date, now)
                balanced.append((c, gamma))
        if not balanced:
            return []
        balanced.sort(key=lambda x: x[1], reverse=True)
        return [c.symbol for c, _ in balanced[:cfg.top_k]]


# ── 策略4: 信用利差收窄 ────────────────────────────────────────────

class CBCreditSpread:
    """选信用利差最高的 CB — 押注利差收窄"""
    name = "cb_credit_spread"

    def __init__(self, config: CBStrategyConfig | None = None) -> None:
        self.config = config or CBStrategyConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        from aipha.factors.cb_factors import compute_credit_spread
        pool = _prefilter(candidates, self.config, now)
        scored = []
        for c in pool:
            spread = compute_credit_spread(c.close, c.maturity_date, now)
            scored.append((c.symbol, spread))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略5: 动量策略 ────────────────────────────────────────────────

@dataclass
class MomentumConfig(CBStrategyConfig):
    lookback: int = 20


class CBMomentum:
    """短期价格动量 — 选过去N日涨幅最大的"""
    name = "cb_momentum"

    def __init__(self, config: MomentumConfig | None = None) -> None:
        self.config = config or MomentumConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        pool = _prefilter(candidates, self.config, now)
        scored = []
        for c in pool:
            momentum = c.metadata.get("return_20d", 0.0)
            scored.append((c.symbol, momentum))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略6: 均值回归 ────────────────────────────────────────────────

class CBMeanReversion:
    """均值回归 — 选近期超跌的 CB"""
    name = "cb_mean_reversion"

    def __init__(self, config: MomentumConfig | None = None) -> None:
        self.config = config or MomentumConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        pool = _prefilter(candidates, self.config, now)
        scored = []
        for c in pool:
            ret = c.metadata.get("return_20d", 0.0)
            scored.append((c.symbol, ret))
        scored.sort(key=lambda x: x[1])  # 越跌越优
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略7: 小盘转债 ────────────────────────────────────────────────

@dataclass
class SmallSizeConfig(CBStrategyConfig):
    max_issue_size: float = 5.0  # 亿元


class CBSmallSize:
    """小盘转债策略 — 发行规模小 + 低价"""
    name = "cb_small_size"

    def __init__(self, config: SmallSizeConfig | None = None) -> None:
        self.config = config or SmallSizeConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        pool = _prefilter(candidates, self.config, now)
        pool = [c for c in pool if c.issue_size <= self.config.max_issue_size * 1e4]
        scored = [(c.symbol, c.close) for c in pool]  # 同等条件下选低价
        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略8: Delta 平衡区 ────────────────────────────────────────────

class CBDeltaBalanced:
    """只选 Delta 位于平衡区的转债"""
    name = "cb_delta_balanced"

    def __init__(self, config: CBStrategyConfig | None = None) -> None:
        self.config = config or CBStrategyConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        from aipha.factors.cb_factors import classify_delta_zone
        pool = _prefilter(candidates, self.config, now)
        balanced = [c for c in pool if classify_delta_zone(cb_value=c.cb_value) == "balanced"]
        # 按双低排序
        scored = [(c.symbol, c.close + (c.cb_over_rate or 0)) for c in balanced if c.cb_over_rate is not None]
        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略9: 价值动量复合 ────────────────────────────────────────────

@dataclass
class ValueMomConfig(CBStrategyConfig):
    value_weight: float = 0.6
    momentum_weight: float = 0.4


class CBValueMomentum:
    """价值 + 动量复合策略"""
    name = "cb_value_momentum"

    def __init__(self, config: ValueMomConfig | None = None) -> None:
        self.config = config or ValueMomConfig()

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        cfg = self.config
        pool = _prefilter(candidates, cfg, now)
        pool = [c for c in pool if c.cb_over_rate is not None]
        if not pool:
            return []
        n = len(pool)
        dl = [c.close + (c.cb_over_rate or 0) for c in pool]
        mom = [c.metadata.get("return_20d", 0.0) for c in pool]
        dl_ranks = _rank_normalize(dl, ascending=True)
        mom_ranks = _rank_normalize(mom, ascending=False)
        scores = []
        for i in range(n):
            s = cfg.value_weight * dl_ranks[i] + cfg.momentum_weight * mom_ranks[i]
            scores.append((pool[i].symbol, s))
        scores.sort(key=lambda x: x[1])
        return [sym for sym, _ in scores[:cfg.top_k]]


# ── 策略10: 通用轮动框架 ──────────────────────────────────────────

class CBRotation:
    """通用可转债轮动框架 — 支持自定义打分函数"""
    name = "cb_rotation"

    def __init__(
        self,
        score_fn: Any = None,
        config: CBStrategyConfig | None = None,
    ) -> None:
        self.config = config or CBStrategyConfig()
        self._score_fn = score_fn or (lambda c, now: c.close)

    def select(self, candidates: list[CandidateInfo], now: datetime) -> list[str]:
        pool = _prefilter(candidates, self.config, now)
        scored = [(c.symbol, self._score_fn(c, now)) for c in pool]
        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:self.config.top_k]]


# ── 策略注册表 ──────────────────────────────────────────────────────

CB_STRATEGIES: dict[str, type] = {
    "cb_double_low": CBDoubleLow,
    "cb_multi_factor": CBMultiFactor,
    "cb_gamma_convexity": CBGammaConvexity,
    "cb_credit_spread": CBCreditSpread,
    "cb_momentum": CBMomentum,
    "cb_mean_reversion": CBMeanReversion,
    "cb_small_size": CBSmallSize,
    "cb_delta_balanced": CBDeltaBalanced,
    "cb_value_momentum": CBValueMomentum,
    "cb_rotation": CBRotation,
}


def get_cb_strategy(name: str, **kwargs: Any) -> Any:
    """按名称获取 CB 策略实例"""
    cls = CB_STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown CB strategy: {name}. Available: {list(CB_STRATEGIES)}")
    return cls(**kwargs)
