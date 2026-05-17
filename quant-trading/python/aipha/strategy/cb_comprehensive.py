"""CBComprehensive 可转债全因子综合策略（10 因子）

在 DoubleLow+ 的 6 因子基础上再融合 4 个 Alpha 因子，构建最全面的可转债
多因子评分体系。

Alpha 因子矩阵（10 因子）:
1. **价格因子** (close): 低价 → 债底保护 + 上行弹性
2. **溢价率因子** (cb_over_rate): 低溢价 → 高股性弹性
3. **规模因子** (issue_size): 小规模 → 低关注度 → 定价偏差
4. **次新因子** (age): 新债 → IPO 偏差 + 抛压修复
5. **均值回归因子** (bollinger_deviation): 偏离布林带下轨 → 超卖
6. **信用评级因子** (rating): 高评级 → 偿付安全 + 机构偏好
7. **Delta暴露因子** (cb_value/close): 高 Delta → 高弹性 + 折价机会
8. **到期收益率因子** (pseudo_ytm): 高 YTM → 债底安全边际
9. **流动性因子** (amount/issue_size): 适度活跃 → 可交易性好
10. **动量因子** (N日涨幅): 正动量 → 趋势延续

设计原则:
- 可关闭任意因子（权重=0）
- 排名归一化消除量纲差异
- 权重通过 YAML 配置可调
- 继承 DoubleLowPlus 的自适应降级
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_enhanced_factors import (
    delta_exposure,
    enhanced_ytm,
    turnover_activity,
)
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.double_low_plus import _get_rating_score
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBComprehensiveConfig(CBRotationConfig):
    """CBComprehensive 10 因子综合策略配置。

    所有因子权重之和无需为 1，内部自动归一化。
    将某因子权重设为 0 即可关闭该因子。
    """

    top_k: int = 10
    rebalance_days: int = 3
    max_price: float = 145.0
    max_issue_size: float = 30000.0   # 万元
    min_issue_size: float = 1000.0    # 万元
    min_turnover: float = 15.0        # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.30
    lot_size: int = 10

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3
    max_age_days: int = 180
    fallback_max_age_days: int = 730

    # --- 溢价率过滤 ---
    max_premium: float = 70.0

    # --- 均值回归/动量参数 ---
    lookback: int = 20                # 布林带/动量回看天数
    bb_k: float = 2.0                 # 布林带标准差倍数
    momentum_lookback: int = 10       # 动量因子回看天数

    # --- 10 因子权重 ---
    weight_price: float = 0.12
    weight_premium: float = 0.12
    weight_size: float = 0.10
    weight_age: float = 0.06
    weight_mean_reversion: float = 0.15
    weight_rating: float = 0.10
    weight_delta: float = 0.10        # Delta暴露
    weight_ytm: float = 0.10          # 到期收益率
    weight_turnover: float = 0.05     # 流动性
    weight_momentum: float = 0.10     # 动量

    # --- C1 新增因子权重（默认=0，不影响现有策略） ---
    weight_premium_percentile: float = 0.0   # 溢价率历史百分位（ValuationPercentile）
    weight_putback: float = 0.0              # 回售接近度（PutbackProximity via cb_value）

    # --- 新因子参数 ---
    premium_pct_lookback: int = 40    # 溢价率历史百分位回看天数
    putback_trigger_ratio: float = 0.85  # 回售触发比例（正股/转股价）

    # --- 自适应降级 ---
    min_candidates: int = 5


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_comprehensive")
class CBComprehensiveStrategy(CBRotationStrategy):
    """CBComprehensive 可转债 10 因子综合策略。

    10 因子排名加权选股:
        score = w_price     * rank(close, ↑)          # 越低越好
              + w_premium   * rank(cb_over_rate, ↑)    # 越低越好
              + w_size      * rank(issue_size, ↑)      # 越小越好
              + w_age       * rank(age_days, ↑)        # 越新越好
              + w_mr        * rank(bb_deviation, ↓)    # 偏离越大越好
              + w_rating    * rank(rating_score, ↓)    # 评级越高越好
              + w_delta     * rank(delta, ↓)           # Delta越高越好
              + w_ytm       * rank(ytm, ↓)             # YTM越高越好
              + w_turnover  * rank(activity, ↓)        # 活跃度越高越好
              + w_momentum  * rank(momentum, ↓)        # 动量越大越好
    """

    name = "cb_comprehensive"

    def __init__(self, config: CBComprehensiveConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBComprehensiveConfig(**kwargs)
        self._price_history: dict[str, deque] = {}
        self._premium_history: dict[str, deque] = {}   # C1: 溢价率历史（ValuationPercentile）
        self._effective_max_age: int = self._config.max_age_days
        self._effective_max_premium: float = self._config.max_premium

    @property
    def config(self) -> CBComprehensiveConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._price_history.clear()
        self._premium_history.clear()   # C1
        self._effective_max_age = self._config.max_age_days
        self._effective_max_premium = self._config.max_premium

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """逐 bar 更新价格历史（用于均值回归、动量及溢价率百分位因子）。"""
        max_len = max(self._config.lookback, self._config.momentum_lookback) + 5
        pct_len = self._config.premium_pct_lookback + 5
        for symbol, bar in bars.items():
            if bar.close > 0:
                if symbol not in self._price_history:
                    self._price_history[symbol] = deque(maxlen=max_len)
                self._price_history[symbol].append(bar.close)
            # C1: 跟踪溢价率历史（用于 ValuationPercentile 因子）
            if self._config.weight_premium_percentile > 0:
                meta = bar.metadata or {}
                rate = meta.get("cb_over_rate")
                if rate is not None and math.isfinite(rate):
                    if symbol not in self._premium_history:
                        self._premium_history[symbol] = deque(maxlen=pct_len)
                    self._premium_history[symbol].append(float(rate))

    def _should_rebalance(self) -> bool:
        if self._bar_count < 2:
            return False
        return (self._bar_count - 1) % self._config.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率 + 次新预过滤。"""
        cfg = self._config
        effective_premium = getattr(self, "_effective_max_premium", cfg.max_premium)
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > effective_premium:
                return False
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False
            if age > self._effective_max_age:
                return False
        return True

    # ----- 辅助因子计算 -----

    def _compute_bb_deviation(self, symbol: str, close: float) -> float | None:
        """布林带偏离度。(lower - close) / std，正值=超卖。"""
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < self._config.lookback:
            return None
        prices = list(hist)[-self._config.lookback :]
        ma = sum(prices) / len(prices)
        variance = sum((p - ma) ** 2 for p in prices) / len(prices)
        std = variance ** 0.5
        if std < 0.01:
            return None
        lower = ma - self._config.bb_k * std
        return (lower - close) / std

    def _compute_momentum(self, symbol: str) -> float | None:
        """N 日价格涨幅 (%)。"""
        hist = self._price_history.get(symbol)
        n = self._config.momentum_lookback
        if hist is None or len(hist) < n + 1:
            return None
        current = hist[-1]
        past = list(hist)[-n - 1]
        if past <= 0:
            return None
        return (current - past) / past * 100.0

    def _compute_premium_percentile(self, symbol: str, current_rate: float) -> float:
        """C1: 溢价率历史百分位因子（ValuationPercentile）。

        返回 1.0 - 历史 percentile，即：
        - 值接近 1.0：当前溢价率处于历史低位（相对便宜）
        - 值接近 0.0：当前溢价率处于历史高位（相对昂贵）

        历史数据不足时返回 0.5（中性）。
        """
        hist = self._premium_history.get(symbol)
        min_hist = max(10, self._config.premium_pct_lookback // 2)
        if hist is None or len(hist) < min_hist:
            return 0.5  # 数据不足：中性
        data = list(hist)
        # Percentile rank of current_rate among historical values
        pct_rank = sum(1.0 for x in data if x <= current_rate) / len(data)
        return 1.0 - pct_rank  # Invert: 低溢价率历史分位 → 高得分

    def _compute_putback_proximity(self, cb_value: float | None) -> float:
        """C1: 回售接近度因子（PutbackProximity）。

        使用转股价值(cb_value/100)作为正股/转股价比率的代理:
          proximity = 1 - (cb_value/100) / trigger_ratio

        - 值 > 0：parity 低于触发线（正股跌破下修触发→回售保护激活）
        - 值 ≈ 0：parity 正在触发线附近
        - 值 < 0：parity 高于触发线（正常转债）

        越高越好：更强的回售保底安全边际。
        """
        if cb_value is None or cb_value <= 0:
            return 0.0
        parity = cb_value / 100.0
        trigger = self._config.putback_trigger_ratio
        return 1.0 - parity / trigger  # 正值=接近/低于触发线

    # ----- 核心打分 -----

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """10 因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)

        # 注意：自适应降级已移至基类 _filter_with_fallback()，此处不再重复

        if n == 1:
            return [candidates[0].symbol]

        # ----- 各因子原始值 -----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size for c in candidates]

        # 次新
        ages: list[float] = []
        for c in candidates:
            if c.list_date:
                ages.append(float(days_since_listed(c.list_date, context.current_time)))
            else:
                ages.append(365.0)

        # 均值回归
        bb_devs = [
            self._compute_bb_deviation(c.symbol, c.close) or 0.0
            for c in candidates
        ]

        # 信用评级
        ratings = [float(_get_rating_score(c.rating)) for c in candidates]

        # Delta暴露
        deltas = [delta_exposure(c) for c in candidates]

        # 到期收益率
        ytms = [
            enhanced_ytm(c.close, c.maturity_date, context.current_time)
            for c in candidates
        ]

        # 流动性
        turnovers = [turnover_activity(c) for c in candidates]

        # 动量
        momentums = [
            self._compute_momentum(c.symbol) or 0.0
            for c in candidates
        ]

        # C1: 溢价率历史百分位（ValuationPercentile）
        premium_pcts = [
            self._compute_premium_percentile(
                c.symbol,
                c.cb_over_rate if c.cb_over_rate is not None else 50.0,
            )
            for c in candidates
        ]

        # C1: 回售接近度（PutbackProximity via cb_value）
        putback_proxs = [
            self._compute_putback_proximity(c.cb_value)
            for c in candidates
        ]

        # ----- 排名归一化 -----
        rank_price = _rank_scores(prices, ascending=True)
        rank_premium = _rank_scores(premiums, ascending=True)
        rank_size = _rank_scores(sizes, ascending=True)
        rank_age = _rank_scores(ages, ascending=True)
        rank_mr = _rank_scores(bb_devs, ascending=False)
        rank_rating = _rank_scores(ratings, ascending=False)
        rank_delta = _rank_scores(deltas, ascending=False)
        rank_ytm = _rank_scores(ytms, ascending=False)
        rank_turnover = _rank_scores(turnovers, ascending=False)
        rank_momentum = _rank_scores(momentums, ascending=False)
        rank_pct = _rank_scores(premium_pcts, ascending=False)   # C1
        rank_putback = _rank_scores(putback_proxs, ascending=False)  # C1

        # ----- 权重归一化 -----
        total_w = (
            cfg.weight_price + cfg.weight_premium + cfg.weight_size
            + cfg.weight_age + cfg.weight_mean_reversion + cfg.weight_rating
            + cfg.weight_delta + cfg.weight_ytm + cfg.weight_turnover
            + cfg.weight_momentum
            + cfg.weight_premium_percentile + cfg.weight_putback  # C1
        )
        if total_w <= 0:
            total_w = 1.0

        w = {
            "pr": cfg.weight_price / total_w,
            "pm": cfg.weight_premium / total_w,
            "sz": cfg.weight_size / total_w,
            "ag": cfg.weight_age / total_w,
            "mr": cfg.weight_mean_reversion / total_w,
            "rt": cfg.weight_rating / total_w,
            "dl": cfg.weight_delta / total_w,
            "yt": cfg.weight_ytm / total_w,
            "to": cfg.weight_turnover / total_w,
            "mo": cfg.weight_momentum / total_w,
            "pct": cfg.weight_premium_percentile / total_w,   # C1
            "pb": cfg.weight_putback / total_w,               # C1
        }

        # ----- 综合打分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                w["pr"] * rank_price[i]
                + w["pm"] * rank_premium[i]
                + w["sz"] * rank_size[i]
                + w["ag"] * rank_age[i]
                + w["mr"] * rank_mr[i]
                + w["rt"] * rank_rating[i]
                + w["dl"] * rank_delta[i]
                + w["yt"] * rank_ytm[i]
                + w["to"] * rank_turnover[i]
                + w["mo"] * rank_momentum[i]
                + w["pct"] * rank_pct[i]        # C1
                + w["pb"] * rank_putback[i]     # C1
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]
