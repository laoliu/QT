"""DoubleLow+ 可转债多因子增强策略

在经典「双低」（价格 + 转股溢价率）基础上，融合 4 个额外 Alpha 因子，
通过排名归一化加权打分选股。

Alpha 因子矩阵（6 因子）:
1. **价格因子** (close): 低价 → 债底保护 + 上行弹性
2. **溢价率因子** (cb_over_rate): 低溢价 → 高股性弹性
3. **规模因子** (issue_size): 小规模 → 低关注度 → 定价偏差
4. **次新因子** (age): 新债 → IPO 偏差 + 抛压结束后修复
5. **均值回归因子** (bollinger_deviation): 偏离布林带下轨 → 超卖
6. **信用评级因子** (rating): 高评级 → 偿付安全 + 机构偏好

与现有策略对比:
- `double_low`: 2 因子(价格+溢价率)，简单加总 → 本策略 6 因子排名加权
- `cb_ultra`: 4 因子(规模+溢价+价格+次新) → 本策略增加均值回归+评级
- `cb_mean_reversion`: 单一均值回归 → 本策略融合均值回归为子因子

核心设计:
- 排名归一化(rank normalization)：消除因子量纲差异
- 权重可配置：可通过 YAML 调整各因子权重
- 均值回归因子需要维护价格历史(lookback bar)
- 自适应降级：候选不足时放宽次新约束
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 信用评级 → 数值映射（复用 cb_rotation_base 的排序语义）
# ---------------------------------------------------------------------------

_RATING_SCORE: dict[str, int] = {
    "AAA": 100,
    "AA+": 90,
    "AA": 80,
    "AA-": 70,
    "A+": 60,
    "A": 50,
    "A-": 40,
    "BBB+": 35,
    "BBB": 30,
    "BBB-": 25,
    "BB+": 20,
    "BB": 15,
    "BB-": 10,
    "B+": 8,
    "B": 5,
    "B-": 3,
    "CCC": 2,
    "CC": 1,
    "C": 0,
}


def _get_rating_score(rating: str) -> int:
    """将信用评级字符串转为可比较的数值。越高越好。"""
    return _RATING_SCORE.get(rating.strip().upper(), -1) if rating else -1


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class DoubleLowPlusConfig(CBRotationConfig):
    """DoubleLow+ 多因子增强策略配置。

    6 因子权重之和无需为 1，内部自动归一化。
    将某因子权重设为 0 即可关闭该因子。
    """

    top_k: int = 10
    rebalance_days: int = 3
    max_price: float = 145.0
    max_issue_size: float = 30000.0   # 万元（3亿以下）
    min_issue_size: float = 1000.0    # 万元（0.1亿以上）
    min_turnover: float = 15.0        # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.30       # 适中止损
    lot_size: int = 10

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3             # 避免首日异常
    max_age_days: int = 180           # 比 cb_ultra 宽松：兼顾双低策略的广覆盖
    fallback_max_age_days: int = 730  # 候选不足时放宽到 2 年

    # --- 溢价率过滤 ---
    max_premium: float = 70.0         # 溢价率上限

    # --- 均值回归参数 ---
    lookback: int = 20                # 布林带回看天数
    bb_k: float = 2.0                 # 布林带标准差倍数

    # --- 6 因子权重（排名法加权）---
    weight_price: float = 0.20        # 价格因子(越低越好)
    weight_premium: float = 0.20      # 溢价率因子(越低越好)
    weight_size: float = 0.15         # 规模因子(越小越好)
    weight_age: float = 0.10          # 次新因子(越新越好)
    weight_mean_reversion: float = 0.20  # 均值回归因子(偏离越大越好)
    weight_rating: float = 0.15       # 信用评级因子(越高越好)

    # --- 自适应降级 ---
    min_candidates: int = 5           # 最少候选数(不足则放宽 age)


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("double_low_plus")
class DoubleLowPlusStrategy(CBRotationStrategy):
    """DoubleLow+ 可转债多因子增强策略。

    6 因子排名加权选股:
        score = w_price   * rank(close, ↑)
              + w_premium * rank(cb_over_rate, ↑)
              + w_size    * rank(issue_size, ↑)
              + w_age     * rank(age_days, ↑)
              + w_mr      * rank(bb_deviation, ↓)   # 偏离越大排名越优
              + w_rating  * rank(rating_score, ↓)    # 评级越高排名越优

    rank 归一化到 [0,1]，↑=越小越好(ascending)，↓=越大越好(descending→反转)。
    总分越低越好，取 top_k。

    均值回归因子:
    - 内部维护每只转债的 N 日价格历史
    - 计算布林带偏离: (MA - k*std - close) / std
    - 偏离 > 0 表示价格低于下轨(超卖)，偏离越大越好
    - 若历史不足或 std≈0，给予中性排名

    自适应降级:
    - 候选不足 min_candidates 时，自动放宽 max_age_days
    - 候选充裕时恢复原始约束
    """

    name = "double_low_plus"

    def __init__(self, config: DoubleLowPlusConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DoubleLowPlusConfig(**kwargs)
        self._price_history: dict[str, deque] = {}
        self._effective_max_age: int = self._config.max_age_days
        self._effective_max_premium: float = self._config.max_premium

    @property
    def config(self) -> DoubleLowPlusConfig:
        """策略配置。"""
        return self._config

    def _on_start_extra(self) -> None:
        """策略启动时重置状态。"""
        self._price_history.clear()
        self._effective_max_age = self._config.max_age_days
        self._effective_max_premium = self._config.max_premium

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """逐 bar 更新价格历史（用于均值回归因子）。"""
        for symbol, bar in bars.items():
            if bar.close > 0:
                if symbol not in self._price_history:
                    self._price_history[symbol] = deque(
                        maxlen=self._config.lookback
                    )
                self._price_history[symbol].append(bar.close)

    def _should_rebalance(self) -> bool:
        """是否触发再平衡。"""
        if self._bar_count < 2:
            return False
        return (self._bar_count - 1) % self._config.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """次新 + 溢价率预过滤。"""
        cfg = self._config

        # 溢价率过滤（使用 effective 值支持降级）
        effective_premium = getattr(self, "_effective_max_premium", cfg.max_premium)
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > effective_premium:
                return False

        # 次新过滤
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False
            if age > self._effective_max_age:
                return False

        return True

    # ----- 均值回归辅助 -----

    def _compute_bb_deviation(self, symbol: str, close: float) -> float | None:
        """计算布林带偏离度。

        返回 (lower - close) / std，正值表示超卖。
        若历史不足或 std 过小，返回 None（中性）。
        """
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < self._config.lookback:
            return None
        prices = list(hist)
        ma = sum(prices) / len(prices)
        variance = sum((p - ma) ** 2 for p in prices) / len(prices)
        std = variance ** 0.5
        if std < 0.01:
            return None
        lower = ma - self._config.bb_k * std
        return (lower - close) / std

    # ----- 核心打分 -----

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """6 因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)

        # 注意：自适应降级已移至基类 _filter_with_fallback()，此处不再重复

        if n == 1:
            return [candidates[0].symbol]

        # ----- 提取因子原始值 -----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size for c in candidates]

        # 次新：上市天数
        ages: list[float] = []
        for c in candidates:
            if c.list_date:
                ages.append(float(days_since_listed(c.list_date, context.current_time)))
            else:
                ages.append(365.0)

        # 均值回归：布林带偏离度
        bb_devs: list[float] = []
        for c in candidates:
            dev = self._compute_bb_deviation(c.symbol, c.close)
            bb_devs.append(dev if dev is not None else 0.0)

        # 信用评级分数
        ratings: list[float] = []
        for c in candidates:
            ratings.append(float(_get_rating_score(c.rating)))

        # ----- 排名归一化 -----
        rank_price = _rank_scores(prices, ascending=True)       # 越低越好
        rank_premium = _rank_scores(premiums, ascending=True)    # 越低越好
        rank_size = _rank_scores(sizes, ascending=True)          # 越小越好
        rank_age = _rank_scores(ages, ascending=True)            # 越新越好
        rank_mr = _rank_scores(bb_devs, ascending=False)         # 偏离越大越好(反转排名)
        rank_rating = _rank_scores(ratings, ascending=False)     # 评级越高越好(反转排名)

        # ----- 权重归一化 -----
        total_w = (
            cfg.weight_price
            + cfg.weight_premium
            + cfg.weight_size
            + cfg.weight_age
            + cfg.weight_mean_reversion
            + cfg.weight_rating
        )
        if total_w <= 0:
            total_w = 1.0

        w_pr = cfg.weight_price / total_w
        w_pm = cfg.weight_premium / total_w
        w_sz = cfg.weight_size / total_w
        w_ag = cfg.weight_age / total_w
        w_mr = cfg.weight_mean_reversion / total_w
        w_rt = cfg.weight_rating / total_w

        # ----- 综合打分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                w_pr * rank_price[i]
                + w_pm * rank_premium[i]
                + w_sz * rank_size[i]
                + w_ag * rank_age[i]
                + w_mr * rank_mr[i]
                + w_rt * rank_rating[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]
