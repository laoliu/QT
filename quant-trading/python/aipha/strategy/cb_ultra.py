"""可转债超级复合因子策略 (CB Ultra Alpha)

目标: 年化 30%+，通过叠加多个高 Alpha 因子实现

Alpha 来源叠加:
1. **小规模效应** (issue_size): 小发行规模 → 市场关注度低 → 定价偏差大
2. **次新效应** (list_age): 新上市 → IPO 定价偏差 + 上市初期抛压结束后估值修复
3. **低溢价效应** (cb_over_rate): 低转股溢价 → 高股性敞口 → 牛市弹性
4. **动量效应** (price_change): 短期价格趋势延续

策略核心创新:
- 分别对每个因子做排名归一化(非参数方法，无正态假设)
- 可配置各因子权重
- 候选不足时自动放宽次新约束(降级机制)
- 完全配置驱动，所有参数可在 YAML 中调整

与现有策略的区别:
- `small_size`: 单因子(仅 issue_size) → 本策略多因子
- `new_small`: 双因子(age + size) 但无溢价率/动量因子 → 本策略四因子
- `small_size_enhanced`: 三因子但无 age 因子 → 本策略四因子
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数 — §13.2 委托给共享实现
# ---------------------------------------------------------------------------

def _rank_scores(values: list[float], ascending: bool = True) -> list[float]:
    """排名归一化到 [0, 1]（委托给 rank_utils 共享实现）。

    ascending=True: 值越小 → 排名分越低(0 = 最优)。
    对并列值取平均排名。

    Args:
        values: 因子原始值
        ascending: True=越小越好, False=越大越好

    Returns:
        归一化排名 [0, 1]
    """
    from aipha.utils.rank_utils import rank_normalize_list
    return rank_normalize_list(values, ascending=ascending, handle_ties=True)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBUltraConfig(CBRotationConfig):
    """超级复合因子策略配置。

    所有因子权重之和无需为 1，内部自动归一化。
    """

    top_k: int = 5
    rebalance_days: int = 1
    max_price: float = 150.0
    max_issue_size: float = 20000.0   # 万元（2亿以下）
    min_issue_size: float = 2000.0    # 万元
    min_turnover: float = 10.0        # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.50       # 宽松止损：债底保护
    lot_size: int = 10

    # --- 次新因子 ---
    use_age_filter: bool = True
    min_age_days: int = 3             # 避免首日异常
    max_age_days: int = 90            # 次新上限
    fallback_max_age_days: int = 365  # 候选不足时放宽到此值

    # --- 溢价率过滤 ---
    max_premium: float = 80.0         # 溢价率超此值过滤

    # --- 因子权重（排名法加权） ---
    weight_size: float = 0.40         # 发行规模(越小越好)
    weight_premium: float = 0.20      # 溢价率(越低越好)
    weight_price: float = 0.20        # 价格(越低越好)
    weight_age: float = 0.20          # 上市天数(越小=越新越好)

    # --- 额外增强 ---
    prefer_low_premium: bool = True   # 启用低溢价偏好
    min_candidates: int = 3           # 最少候选数(不足则放宽 age)


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_ultra")
class CBUltraStrategy(CBRotationStrategy):
    """可转债超级复合因子策略。

    四因子排名加权选股:
        score = w_size * rank(issue_size, ↑)
              + w_prem * rank(cb_over_rate, ↑)
              + w_price * rank(close, ↑)
              + w_age * rank(age_days, ↑)

    其中 rank 归一化到 [0,1]，↑ 表示越小因子值排名越优(分越低)。
    总分越低越好，取 top_k。

    自适应降级:
    - 当次新候选不足 min_candidates 时，自动放宽 max_age_days
    - 每次放宽 30 天，上限为 fallback_max_age_days
    - 候选充裕时自动恢复
    """

    name = "cb_ultra"

    def __init__(self, config: CBUltraConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBUltraConfig(**kwargs)
        self._effective_max_age: int = self._config.max_age_days
        self._effective_max_premium: float = self._config.max_premium

    @property
    def config(self) -> CBUltraConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._effective_max_age = self._config.max_age_days
        self._effective_max_premium = self._config.max_premium

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """次新 + 溢价率过滤。"""
        cfg = self._config

        # 溢价率过滤（使用 effective 值支持降级）
        effective_premium = getattr(self, "_effective_max_premium", cfg.max_premium)
        if cfg.prefer_low_premium and candidate.cb_over_rate is not None:
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

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """四因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)

        # 注意：自适应降级已移至基类 _filter_with_fallback()，此处不再重复

        if n == 1:
            return [candidates[0].symbol]

        # 提取因子值
        sizes = [c.issue_size for c in candidates]
        premiums = [c.cb_over_rate if c.cb_over_rate is not None else 50.0 for c in candidates]
        prices = [c.close for c in candidates]
        ages = []
        for c in candidates:
            if c.list_date:
                ages.append(float(days_since_listed(c.list_date, context.current_time)))
            else:
                ages.append(365.0)  # 无上市日期视为老债

        # 排名归一化(均为越小越好)
        rank_size = _rank_scores(sizes, ascending=True)
        rank_premium = _rank_scores(premiums, ascending=True)
        rank_price = _rank_scores(prices, ascending=True)
        rank_age = _rank_scores(ages, ascending=True)

        # 权重归一化
        total_w = cfg.weight_size + cfg.weight_premium + cfg.weight_price + cfg.weight_age
        if total_w <= 0:
            total_w = 1.0
        ws = cfg.weight_size / total_w
        wp = cfg.weight_premium / total_w
        wpr = cfg.weight_price / total_w
        wa = cfg.weight_age / total_w

        # 综合打分
        scored = []
        for i, c in enumerate(candidates):
            score = (ws * rank_size[i]
                     + wp * rank_premium[i]
                     + wpr * rank_price[i]
                     + wa * rank_age[i])
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]


# ---------------------------------------------------------------------------
# 变种: 无年龄限制的纯多因子小盘策略
# ---------------------------------------------------------------------------

@dataclass
class CBUltraNoAgeConfig(CBRotationConfig):
    """纯多因子小盘策略配置（无次新限制）。"""

    top_k: int = 5
    rebalance_days: int = 1
    max_price: float = 200.0          # 很宽松，让高价小盘也能入选
    max_issue_size: float = 20000.0
    min_issue_size: float = 2000.0
    min_turnover: float = 5.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.50
    lot_size: int = 10

    max_premium: float = 100.0

    # 因子权重
    weight_size: float = 0.60         # 规模主导
    weight_premium: float = 0.20
    weight_price: float = 0.20


@StrategyRegistry().register("cb_ultra_noage")
class CBUltraNoAgeStrategy(CBRotationStrategy):
    """纯多因子小盘策略（无次新限制）。

    三因子排名加权:
        score = w_size * rank(issue_size) + w_prem * rank(premium) + w_price * rank(price)

    适合候选池更大时使用，不受次新债供给波动影响。
    """

    name = "cb_ultra_noage"

    def __init__(self, config: CBUltraNoAgeConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBUltraNoAgeConfig(**kwargs)

    @property
    def config(self) -> CBUltraNoAgeConfig:
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        cfg = self._config
        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self._config
        if not candidates:
            return []
        if len(candidates) == 1:
            return [candidates[0].symbol]

        sizes = [c.issue_size for c in candidates]
        premiums = [c.cb_over_rate if c.cb_over_rate is not None else 50.0 for c in candidates]
        prices = [c.close for c in candidates]

        rank_size = _rank_scores(sizes, ascending=True)
        rank_premium = _rank_scores(premiums, ascending=True)
        rank_price = _rank_scores(prices, ascending=True)

        total_w = cfg.weight_size + cfg.weight_premium + cfg.weight_price
        if total_w <= 0:
            total_w = 1.0
        ws = cfg.weight_size / total_w
        wp = cfg.weight_premium / total_w
        wpr = cfg.weight_price / total_w

        scored = []
        for i, c in enumerate(candidates):
            score = ws * rank_size[i] + wp * rank_premium[i] + wpr * rank_price[i]
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]
