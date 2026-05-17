"""可转债双低策略变种体系

基于经典双低策略（价格 + 转股溢价率），引入专业量化因子处理方法：

1. **双低Z (DoubleLowZScore)** — Z-Score 标准化后加权
   - 将价格和溢价率分别做横截面 Z-Score 标准化
   - 消除量纲差异（价格~100-150 vs 溢价率~0-50%），
     使两个因子在等权下真正"等权"
   - Z值 = (当前值 - 截面均值) / 截面标准差
   - 支持可配置的价格权重（price_weight）

2. **双低排名 (DoubleLowRank)** — 分别排名后加权
   - 将价格和溢价率分别在候选池内排名
   - 排名本身是一种非参数标准化，不假设正态分布
   - 对肥尾分布更鲁棒
   - 支持可配置的权重

3. **双低百分位 (DoubleLowPercentile)** — 百分位标准化后加权
   - 将因子值映射到 [0, 1] 百分位
   - 结合了 Z-Score 的连续性和排名的非参数特性
   - 对极端值更鲁棒

所有变种共享：
- CBRotationStrategy 基类（统一过滤管道、止损、强赎、调仓）
- CandidateInfo 数据结构
- 可配置的 price_weight / premium_weight（0~100%）
- 可选的第三因子（转股价值、YTM、规模等）

设计参考：
- 中证全指红利质量指数：Z(ROE)+Z(ΔROE)+Z(OPCFD)+Z(DP)
- 果仁网/禄得网：多因子分别排名后乘权重近似标准化
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
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ============================================================================
# 通用工具函数
# ============================================================================


def _zscore_normalize(values: list[float]) -> list[float]:
    """横截面 Z-Score 标准化。

    Z = (x - mean) / std

    如果标准差为 0（所有值相同），返回全 0。

    Args:
        values: 原始值列表

    Returns:
        Z 值列表（与输入等长）
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.0]

    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(var) if var > 0 else 0.0

    if std < 1e-10:
        return [0.0] * n

    return [(v - mean) / std for v in values]


def _rank_normalize(values: list[float], ascending: bool = True) -> list[float]:
    """排名归一化到 [0, 1]（委托给 rank_utils 共享实现 §13.2）。

    排名后映射到 [0, 1] 区间：rank / (n - 1)。
    ascending=True 时，最小值排名 0，最大值排名 1。

    Args:
        values: 原始值列表
        ascending: True=值越小排名越前（0），False=值越大排名越前（0）

    Returns:
        归一化排名列表（与输入等长）
    """
    from aipha.utils.rank_utils import rank_normalize_list
    return rank_normalize_list(values, ascending=ascending, handle_ties=False)


def _percentile_normalize(values: list[float]) -> list[float]:
    """百分位归一化到 [0, 1]。

    将每个值映射为其在分布中的百分位位置。
    等价于排名归一化，但语义更清晰。

    值越小百分位越低（越好/越低双低）。

    Args:
        values: 原始值列表

    Returns:
        百分位列表（与输入等长）
    """
    return _rank_normalize(values, ascending=True)


# ============================================================================
# 配置类
# ============================================================================


@dataclass
class DoubleLowZScoreConfig(CBRotationConfig):
    """双低 Z-Score 策略配置

    price_weight + premium_weight 会被自动归一化为和=1。
    """

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    max_premium: float = 50.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10

    # Z-Score 加权
    price_weight: float = 0.50       # 价格因子权重
    premium_weight: float = 0.50     # 溢价率因子权重

    # 可选：第三因子（转股价值）
    use_cb_value: bool = False       # 是否加入转股价值因子
    cb_value_weight: float = 0.0     # 转股价值因子权重


@dataclass
class DoubleLowRankConfig(CBRotationConfig):
    """双低排名加权策略配置"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    max_premium: float = 50.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10

    # 排名加权
    price_weight: float = 0.50       # 价格排名权重
    premium_weight: float = 0.50     # 溢价率排名权重

    # 可选：第三因子（转股价值）
    use_cb_value: bool = False
    cb_value_weight: float = 0.0


@dataclass
class DoubleLowPercentileConfig(CBRotationConfig):
    """双低百分位策略配置"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    max_premium: float = 50.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10

    # 百分位加权
    price_weight: float = 0.50
    premium_weight: float = 0.50

    # 可选：第三因子
    use_cb_value: bool = False
    cb_value_weight: float = 0.0


# ============================================================================
# 策略实现
# ============================================================================


@StrategyRegistry().register("double_low_zscore")
class DoubleLowZScoreStrategy(CBRotationStrategy):
    """双低 Z-Score 标准化策略

    核心改进：经典双低直接相加 (close + premium_rate%) 存在量纲不对等问题：
    - 可转债价格中位数 ~142 元
    - 转股溢价率中位数 ~36%
    - 直接相加时，价格因子主导了双低指标（~80% 权重）

    Z-Score 标准化：
        Z_价格 = (价格 - 均值_价格) / 标准差_价格
        Z_溢价 = (溢价率 - 均值_溢价) / 标准差_溢价
        双低Z = price_weight × Z_价格 + premium_weight × Z_溢价

    使两个因子在数值上可比，权重参数才真正反映配比意图。

    注意：Z-Score 假设近似正态分布。若数据呈肥尾分布，
    可使用 DoubleLowRankStrategy（排名法）替代。
    """

    name = "double_low_zscore"

    def __init__(self, config: DoubleLowZScoreConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DoubleLowZScoreConfig(**kwargs)

    @property
    def config(self) -> DoubleLowZScoreConfig:
        """返回双低 Z-Score 策略配置实例。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率上限 + 需要溢价率数据。"""
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """Z-Score 标准化后加权评分，升序取 top_k。"""
        cfg = self._config
        if not candidates:
            return []
        if len(candidates) == 1:
            return [candidates[0].symbol]

        # 溢价率缺失的标的排除（避免 0.0 虚假信号干扰排名）
        candidates = [c for c in candidates if c.cb_over_rate is not None]
        if not candidates:
            return []

        prices = [c.close for c in candidates]
        premiums = [c.cb_over_rate for c in candidates]

        z_prices = _zscore_normalize(prices)
        z_premiums = _zscore_normalize(premiums)

        # 权重归一化
        total_weight = cfg.price_weight + cfg.premium_weight
        if cfg.use_cb_value and cfg.cb_value_weight > 0:
            cb_values = [c.cb_value if c.cb_value is not None else c.close for c in candidates]
            z_cb_values = _zscore_normalize(cb_values)
            total_weight += cfg.cb_value_weight
        else:
            z_cb_values = [0.0] * len(candidates)

        if total_weight <= 0:
            total_weight = 1.0

        pw = cfg.price_weight / total_weight
        prw = cfg.premium_weight / total_weight
        cvw = (cfg.cb_value_weight / total_weight) if cfg.use_cb_value else 0.0

        # 综合 Z 值：越低越好
        scored = []
        for i, c in enumerate(candidates):
            # 价格和溢价率：Z 值越低越好（低价低溢价）
            # 转股价值：Z 值越低越好（低转股价值 = 偏债性，更安全）
            z_total = pw * z_prices[i] + prw * z_premiums[i] + cvw * z_cb_values[i]
            scored.append((c.symbol, z_total))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]


@StrategyRegistry().register("double_low_rank")
class DoubleLowRankStrategy(CBRotationStrategy):
    """双低排名加权策略

    核心改进：分别对价格和溢价率排名后加权，避免 Z-Score 的正态假设。

    排名法特点：
    - 非参数方法，不假设数据分布形态
    - 对肥尾分布、极端值更鲁棒
    - 等价于果仁网/禄得网的"多因子分别排名后乘权重"方法
    - 严格意义上，排名法与 Z-Score 法有微小差异，但实践中接近

    评分公式：
        score = price_weight × rank_价格 + premium_weight × rank_溢价率
    其中 rank 归一化到 [0, 1]，0 = 最优（最低价/最低溢价）。
    """

    name = "double_low_rank"

    def __init__(self, config: DoubleLowRankConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DoubleLowRankConfig(**kwargs)

    @property
    def config(self) -> DoubleLowRankConfig:
        """返回双低排名加权策略配置实例。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """分别排名后加权，升序取 top_k。"""
        cfg = self._config
        if not candidates:
            return []
        if len(candidates) == 1:
            return [candidates[0].symbol]

        # 溢价率缺失的标的排除（避免 0.0 虚假信号干扰排名）
        candidates = [c for c in candidates if c.cb_over_rate is not None]
        if not candidates:
            return []

        prices = [c.close for c in candidates]
        premiums = [c.cb_over_rate for c in candidates]

        # 排名归一化（ascending=True: 值越小排名越前=分越低=越优）
        rank_prices = _rank_normalize(prices, ascending=True)
        rank_premiums = _rank_normalize(premiums, ascending=True)

        # 权重归一化
        total_weight = cfg.price_weight + cfg.premium_weight
        if cfg.use_cb_value and cfg.cb_value_weight > 0:
            cb_values = [c.cb_value if c.cb_value is not None else c.close for c in candidates]
            rank_cb_values = _rank_normalize(cb_values, ascending=True)
            total_weight += cfg.cb_value_weight
        else:
            rank_cb_values = [0.0] * len(candidates)

        if total_weight <= 0:
            total_weight = 1.0

        pw = cfg.price_weight / total_weight
        prw = cfg.premium_weight / total_weight
        cvw = (cfg.cb_value_weight / total_weight) if cfg.use_cb_value else 0.0

        # 综合排名分：越低越好
        scored = []
        for i, c in enumerate(candidates):
            rank_total = pw * rank_prices[i] + prw * rank_premiums[i] + cvw * rank_cb_values[i]
            scored.append((c.symbol, rank_total))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]


@StrategyRegistry().register("double_low_percentile")
class DoubleLowPercentileStrategy(CBRotationStrategy):
    """双低百分位标准化策略

    核心改进：将因子值映射到 [0, 1] 百分位后加权。

    百分位法特点：
    - 结合排名法的非参数鲁棒性
    - 保持值的连续性（较排名法区分度更好）
    - 适合可转债这类样本量中等（200-500只）的场景

    评分公式：
        score = price_weight × pct_价格 + premium_weight × pct_溢价率
    其中 pct 为百分位（0 = 最低值），score 越低越好。
    """

    name = "double_low_percentile"

    def __init__(self, config: DoubleLowPercentileConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DoubleLowPercentileConfig(**kwargs)

    @property
    def config(self) -> DoubleLowPercentileConfig:
        """返回双低百分位策略配置实例。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """百分位标准化后加权，升序取 top_k。"""
        cfg = self._config
        if not candidates:
            return []
        if len(candidates) == 1:
            return [candidates[0].symbol]

        # 溢价率缺失的标的排除（避免 0.0 虚假信号干扰排名）
        candidates = [c for c in candidates if c.cb_over_rate is not None]
        if not candidates:
            return []

        prices = [c.close for c in candidates]
        premiums = [c.cb_over_rate for c in candidates]

        pct_prices = _percentile_normalize(prices)
        pct_premiums = _percentile_normalize(premiums)

        total_weight = cfg.price_weight + cfg.premium_weight
        if cfg.use_cb_value and cfg.cb_value_weight > 0:
            cb_values = [c.cb_value if c.cb_value is not None else c.close for c in candidates]
            pct_cb_values = _percentile_normalize(cb_values)
            total_weight += cfg.cb_value_weight
        else:
            pct_cb_values = [0.0] * len(candidates)

        if total_weight <= 0:
            total_weight = 1.0

        pw = cfg.price_weight / total_weight
        prw = cfg.premium_weight / total_weight
        cvw = (cfg.cb_value_weight / total_weight) if cfg.use_cb_value else 0.0

        scored = []
        for i, c in enumerate(candidates):
            pct_total = pw * pct_prices[i] + prw * pct_premiums[i] + cvw * pct_cb_values[i]
            scored.append((c.symbol, pct_total))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]


# ============================================================================
# 原始双低值策略（集思录经典方法）
# ============================================================================


@dataclass
class DoubleLowRawConfig(CBRotationConfig):
    """原始双低值策略配置

    经典集思录方法: 双低值 = 转债价格 + 转股溢价率(%)
    直接按原始值排序，不做标准化。
    """

    top_k: int = 15
    rebalance_days: int = 10
    max_price: float = 130.0
    max_premium: float = 50.0
    min_turnover: float = 100.0
    min_days_to_maturity: int = 365
    max_days_to_maturity: int = 1825
    min_issue_size: float = 30000.0
    min_rating: str = "AA-"
    exclude_st: bool = True
    exclude_called: bool = True
    stop_loss_pct: float = 0.0
    lot_size: int = 10
    buffer_k: int = 3

    # 双低温度计（市场估值自适应仓位控制）
    temperature_enabled: bool = False
    temperature_cold: float = 100.0  # 双低中位数低于此值 → 满仓
    temperature_hot: float = 130.0   # 双低中位数高于此值 → 最低仓位
    temperature_min_scale: float = 0.3  # 最低仓位比例（对 top_k 的缩放下限）
    temperature_min_positions: int = 3  # 最少持仓数量


@StrategyRegistry().register("double_low_raw")
class DoubleLowRawStrategy(CBRotationStrategy):
    """原始双低值策略（集思录经典方法）

    评分公式：
        dual_low = close + cb_over_rate
    其中 cb_over_rate 为百分比形式（如10表示10%溢价率）。
    按 dual_low 升序排列，取最低的 top_k 只。

    这是集思录/持有封基等大V使用的经典方法，不做任何标准化。

    可选温度计：根据全市场双低中位数动态调整有效持仓数量。
    """

    name = "double_low_raw"

    def __init__(self, config: DoubleLowRawConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or DoubleLowRawConfig(**kwargs)
        self._temperature_scale: float = 1.0  # 当前温度缩放因子

    @property
    def config(self) -> DoubleLowRawConfig:
        return self._config

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新双低温度计。"""
        if not self._config.temperature_enabled:
            return

        # 计算全市场双低中位数（仅需 close + cb_over_rate 有效的标的）
        dl_values: list[float] = []
        for bar in bars.values():
            meta = bar.metadata or {}
            cb_over_rate = meta.get("cb_over_rate")
            if cb_over_rate is not None and bar.close > 0:
                dl_values.append(bar.close + cb_over_rate)

        if len(dl_values) < 10:
            self._temperature_scale = 1.0
            return

        dl_values.sort()
        median_dl = dl_values[len(dl_values) // 2]

        # 线性映射: cold → 1.0, hot → min_scale
        cold = self._config.temperature_cold
        hot = self._config.temperature_hot
        if median_dl <= cold:
            self._temperature_scale = 1.0
        elif median_dl >= hot:
            self._temperature_scale = self._config.temperature_min_scale
        else:
            ratio = (median_dl - cold) / (hot - cold)
            self._temperature_scale = 1.0 - ratio * (1.0 - self._config.temperature_min_scale)

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """原始双低值排序，升序取 effective_top_k（温度计缩放后）。"""
        cfg = self._config
        if not candidates:
            return []

        candidates = [c for c in candidates if c.cb_over_rate is not None]
        if not candidates:
            return []

        # 温度计调整有效持仓数
        effective_k = cfg.top_k
        if cfg.temperature_enabled and self._temperature_scale < 1.0:
            effective_k = max(
                cfg.temperature_min_positions,
                int(cfg.top_k * self._temperature_scale),
            )

        # 经典双低值 = 转债价格 + 转股溢价率(%)
        scored = [(c.symbol, c.close + c.cb_over_rate) for c in candidates]
        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:effective_k]]


# =============================================================================
# CB 等权基准策略 (Phase 3 A1)
# =============================================================================


class CBEqualWeightConfig(CBRotationConfig):
    """CB 等权基准配置 — 不做评分, 等权持有所有合格标的."""

    rebalance_days: int = 10
    max_price: float = 125.0
    max_premium: float = 50.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 365
    max_days_to_maturity: int = 1825
    min_issue_size: float = 20000.0
    min_rating: str = "A-"
    exclude_st: bool = True
    exclude_called: bool = True
    stop_loss_pct: float = 0.0
    lot_size: int = 10
    buffer_k: int = 0


@StrategyRegistry().register("cb_equal_weight")
class CBEqualWeightStrategy(CBRotationStrategy):
    """CB 等权基准 — 对所有通过过滤的 CB 等权持有.

    用于度量选股策略的超额收益 (alpha = 策略收益 - 等权基准收益).
    """

    name = "cb_equal_weight"

    def __init__(self, config: CBEqualWeightConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBEqualWeightConfig(**kwargs)

    @property
    def config(self) -> CBEqualWeightConfig:
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """等权: 返回所有通过过滤的候选标的."""
        return [c.symbol for c in candidates]
