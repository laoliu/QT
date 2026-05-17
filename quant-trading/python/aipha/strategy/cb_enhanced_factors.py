"""可转债增强因子模块

在基础的 价格/规模/溢价率/次新 因子之外，提供更丰富的 CB Alpha 因子：

1. **Delta 暴露因子** — cb_value / close，衡量转债对正股的弹性
2. **溢价率动量** — 溢价率变化速度，捕捉市场情绪变化
3. **价值-规模复合因子** — 低价+小规模+低溢价融合
4. **到期收益率增强** — 基于价格和剩余年限估算 YTM
5. **成交活跃度因子** — 成交额相对发行规模的比值
6. **信用评级评分** — 将评级映射为可排序数值

所有因子设计遵循：
- 可在 CBRotationStrategy._score_candidates() 中直接调用
- 输入为 CandidateInfo 或 metadata dict，无需额外数据依赖
- 值越大表示越优（统一方向），方便排名融合
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aipha.strategy.cb_rotation_base import CandidateInfo


# ---------------------------------------------------------------------------
# 信用评级评分
# ---------------------------------------------------------------------------

_RATING_SCORES: dict[str, float] = {
    "AAA": 1.00,
    "AA+": 0.90,
    "AA": 0.80,
    "AA-": 0.70,
    "A+": 0.60,
    "A": 0.50,
    "A-": 0.40,
    "BBB+": 0.35,
    "BBB": 0.30,
    "BBB-": 0.25,
    "BB+": 0.20,
    "BB": 0.15,
    "BB-": 0.10,
    "B+": 0.08,
    "B": 0.05,
    "B-": 0.03,
    "CCC": 0.02,
    "CC": 0.01,
    "C": 0.00,
}


def rating_score(rating: str) -> float:
    """将信用评级映射为 [0, 1] 归一化分数，适合多因子排名融合。

    评级越高分数越高。无评级返回 0.5（中性）。

    注意：cb_rotation_base.rating_score() 返回 0-100 整数刻度，
    适合阈值过滤场景。两者保持相同的排序语义。
    """
    if not rating:
        return 0.5
    return _RATING_SCORES.get(rating.strip().upper(), 0.5)


# ---------------------------------------------------------------------------
# Delta 暴露因子
# ---------------------------------------------------------------------------

def delta_exposure(candidate: CandidateInfo) -> float:
    """Delta 暴露 = cb_value / close

    衡量转债对正股变动的敏感度：
    - > 1.0: 折价状态，转债价格低于转股价值（套利机会）
    - ≈ 1.0: 平价，正常状态
    - < 1.0: 溢价状态，转债含时间价值/债性保护

    **因子方向**：值越大越好（高 Delta = 高弹性 + 可能的折价机会）
    """
    cv = candidate.cb_value
    if cv is None or cv <= 0 or candidate.close <= 0:
        return 0.0
    return cv / candidate.close


# ---------------------------------------------------------------------------
# 溢价率动量
# ---------------------------------------------------------------------------

class PremiumMomentumTracker:
    """跟踪溢价率的变化动量

    需要逐 bar 调用 update()，积累历史后可计算动量。
    """

    def __init__(self, lookback: int = 5):
        self.lookback = lookback
        # symbol -> list of recent cb_over_rate values
        self._history: dict[str, list[float]] = {}

    def update(self, symbol: str, cb_over_rate: float | None) -> None:
        """记录最新溢价率"""
        if cb_over_rate is None:
            return
        hist = self._history.setdefault(symbol, [])
        hist.append(cb_over_rate)
        if len(hist) > self.lookback + 5:
            del hist[:-self.lookback - 1]

    def momentum(self, symbol: str) -> float | None:
        """计算溢价率动量 = 当前溢价率 - N天前溢价率

        **因子方向**：值越负越好（溢价率在下降 → 转债变便宜/正股在涨）
        返回的是负值表示溢价率在收敛，这是正面信号。
        为了统一"越大越好"方向，返回 -momentum。
        """
        hist = self._history.get(symbol, [])
        if len(hist) < self.lookback + 1:
            return None
        current = hist[-1]
        past = hist[-self.lookback - 1]
        raw_momentum = current - past
        return -raw_momentum  # 取反：溢价率下降 → 正分


# ---------------------------------------------------------------------------
# 到期收益率增强
# ---------------------------------------------------------------------------

def enhanced_ytm(close: float, maturity_date: str, now: datetime) -> float:
    """增强版近似到期收益率

    假设到期按面值 100 兑付（忽略票息），计算年化隐含收益率：
    YTM ≈ (100 / close) ^ (1 / years_to_maturity) - 1

    **因子方向**：值越大越好（到期收益率高 = 安全边际大）

    Args:
        close: 当前价格
        maturity_date: 到期日 YYYYMMDD
        now: 当前时间
    """
    if close <= 0 or close >= 200:
        return 0.0

    try:
        mat = datetime.strptime(maturity_date[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return 0.0

    days = max((mat - now).days, 1)
    years = days / 365.0
    if years < 0.1:
        return 0.0

    # (100/close)^(1/years) - 1
    try:
        ytm = (100.0 / close) ** (1.0 / years) - 1.0
    except (OverflowError, ZeroDivisionError):
        return 0.0

    return ytm


# ---------------------------------------------------------------------------
# 价值-规模复合因子
# ---------------------------------------------------------------------------

def value_size_composite(
    candidate: CandidateInfo,
    price_weight: float = 0.35,
    premium_weight: float = 0.30,
    size_weight: float = 0.20,
    delta_weight: float = 0.15,
    *,
    pool_stats: dict[str, tuple[float, float]] | None = None,
) -> float:
    """价值-规模复合因子

    融合多个维度为单一评分：
    - 价格越低越好
    - 溢价率越低越好
    - 规模越小越好（小盘效应）
    - Delta 暴露越高越好

    返回值越大越好。各分量先做 0-1 归一化再加权。

    Args:
        candidate: 转债候选信息
        price_weight: 价格因子权重
        premium_weight: 溢价率因子权重
        size_weight: 规模因子权重
        delta_weight: Delta 暴露因子权重
        pool_stats: 可选的自适应归一化参数，格式为
            {"price": (min, max), "premium": (min, max),
             "size": (min, max), "delta": (min, max)}。
            传入后使用实际数据范围替代硬编码区间。
            由 ``compute_pool_stats`` 自动计算。

    学术参考：
    - 自适应百分位标准化替代固定区间，避免因行情阶段不同
      导致因子区分度丧失（Barra 风格因子构建方法论）。
    """
    if pool_stats:
        p_lo, p_hi = pool_stats.get("price", (80.0, 150.0))
        pr_lo, pr_hi = pool_stats.get("premium", (-10.0, 50.0))
        sz_lo, sz_hi = pool_stats.get("size", (1000.0, 100000.0))
        d_lo, d_hi = pool_stats.get("delta", (0.5, 1.5))
    else:
        # 回退默认值（仍然保留，但建议传入 pool_stats）
        p_lo, p_hi = 80.0, 150.0
        pr_lo, pr_hi = -10.0, 50.0
        sz_lo, sz_hi = 1000.0, 100000.0
        d_lo, d_hi = 0.5, 1.5

    p_range = p_hi - p_lo if p_hi > p_lo else 1.0
    pr_range = pr_hi - pr_lo if pr_hi > pr_lo else 1.0
    sz_range = sz_hi - sz_lo if sz_hi > sz_lo else 1.0
    d_range = d_hi - d_lo if d_hi > d_lo else 1.0

    # 价格分（越低越好）
    price_score = max(0.0, min(1.0, (p_hi - candidate.close) / p_range))

    # 溢价率分（越低越好）
    premium = candidate.cb_over_rate
    if premium is not None:
        premium_score = max(0.0, min(1.0, (pr_hi - premium) / pr_range))
    else:
        premium_score = 0.5

    # 规模分（越小越好）
    size_score = max(0.0, min(1.0, (sz_hi - candidate.issue_size) / sz_range))

    # Delta 分（越高越好）
    d = delta_exposure(candidate)
    delta_score = max(0.0, min(1.0, (d - d_lo) / d_range))

    return (
        price_weight * price_score
        + premium_weight * premium_score
        + size_weight * size_score
        + delta_weight * delta_score
    )


def compute_pool_stats(
    candidates: list[CandidateInfo],
    percentile_low: float = 5.0,
    percentile_high: float = 95.0,
) -> dict[str, tuple[float, float]]:
    """从候选池计算各因子的自适应归一化区间。

    使用百分位数（默认 5th–95th）而非 min/max，对极端值更鲁棒。

    Args:
        candidates: 候选转债列表
        percentile_low: 下界百分位
        percentile_high: 上界百分位

    Returns:
        {"price": (lo, hi), "premium": (lo, hi), "size": (lo, hi), "delta": (lo, hi)}
    """
    if not candidates:
        return {}

    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = pct / 100.0 * (len(s) - 1)
        lo_idx = int(idx)
        hi_idx = min(lo_idx + 1, len(s) - 1)
        frac = idx - lo_idx
        return s[lo_idx] * (1 - frac) + s[hi_idx] * frac

    prices = [c.close for c in candidates if c.close > 0]
    premiums = [c.cb_over_rate for c in candidates if c.cb_over_rate is not None]
    sizes = [c.issue_size for c in candidates if c.issue_size > 0]
    deltas = [delta_exposure(c) for c in candidates]

    stats: dict[str, tuple[float, float]] = {}
    if prices:
        stats["price"] = (_percentile(prices, percentile_low), _percentile(prices, percentile_high))
    if premiums:
        stats["premium"] = (_percentile(premiums, percentile_low), _percentile(premiums, percentile_high))
    if sizes:
        stats["size"] = (_percentile(sizes, percentile_low), _percentile(sizes, percentile_high))
    if deltas:
        stats["delta"] = (_percentile(deltas, percentile_low), _percentile(deltas, percentile_high))

    return stats


# ---------------------------------------------------------------------------
# 成交活跃度因子
# ---------------------------------------------------------------------------

def turnover_activity(candidate: CandidateInfo) -> float:
    """成交活跃度 = 成交额 / 发行规模

    衡量转债的相对流动性：
    - 高值 = 交易活跃，市场关注度高
    - 低值 = 交易清淡，可能存在流动性风险

    **因子方向**：值越大越好（适度活跃，但非极端投机）
    """
    if candidate.issue_size <= 0:
        return 0.0
    return candidate.amount / candidate.issue_size


# ---------------------------------------------------------------------------
# 多因子排名融合工具
# ---------------------------------------------------------------------------

@dataclass
class FactorWeight:
    """因子及其权重定义"""
    name: str
    weight: float
    ascending: bool = True  # True=值越大排名越前

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError(f"因子权重不能为负: {self.name}={self.weight}")


def rank_fusion(
    candidates: list[CandidateInfo],
    factor_values: dict[str, dict[str, float]],
    factor_weights: list[FactorWeight],
    top_k: int = 20,
) -> list[str]:
    """多因子排名融合

    对每个因子独立排名后按权重加权求和，返回综合排名的 top_k 个 symbol。

    Args:
        candidates: 候选列表
        factor_values: {因子名: {symbol: 值}}
        factor_weights: 因子权重定义列表
        top_k: 返回前 K 个

    Returns:
        按综合得分降序排列的 symbol 列表
    """
    if not candidates:
        return []

    symbols = [c.symbol for c in candidates]
    n = len(symbols)

    # 对每个因子排名
    composite: dict[str, float] = {s: 0.0 for s in symbols}

    for fw in factor_weights:
        fv = factor_values.get(fw.name, {})
        # 获取每个 symbol 的因子值
        scored = [(s, fv.get(s, 0.0)) for s in symbols]
        # 排序（ascending=True 则值越大排名越前/分越高）
        scored.sort(key=lambda x: x[1], reverse=fw.ascending)

        # 排名分（第1名得 n 分，最后一名得 1 分）
        for rank, (sym, _) in enumerate(scored):
            rank_score = (n - rank) / n  # 归一化到 [1/n, 1]
            composite[sym] += fw.weight * rank_score

    # 按综合分降序排
    result = sorted(composite.keys(), key=lambda s: composite[s], reverse=True)
    return result[:top_k]


# ---------------------------------------------------------------------------
# 因子批量计算
# ---------------------------------------------------------------------------

def compute_factor_values(
    candidates: list[CandidateInfo],
    now: datetime,
    premium_tracker: PremiumMomentumTracker | None = None,
) -> dict[str, dict[str, float]]:
    """批量计算所有 CB 增强因子

    Args:
        candidates: 候选列表
        now: 当前时间
        premium_tracker: 溢价率动量跟踪器（可选）

    Returns:
        {因子名: {symbol: 值}} 字典
    """
    result: dict[str, dict[str, float]] = {
        "price": {},
        "premium": {},
        "size": {},
        "delta": {},
        "ytm": {},
        "activity": {},
        "composite": {},
        "rating": {},
    }

    # 自适应归一化参数（基于当期候选池百分位）
    pool_stats = compute_pool_stats(candidates) if candidates else {}

    for c in candidates:
        sym = c.symbol

        # 价格（越低越好 → 取负）
        result["price"][sym] = -c.close

        # 溢价率（越低越好 → 取负）
        result["premium"][sym] = -(c.cb_over_rate if c.cb_over_rate is not None else 50.0)

        # 规模（越小越好 → 取负）
        result["size"][sym] = -c.issue_size

        # Delta 暴露
        result["delta"][sym] = delta_exposure(c)

        # 到期收益率
        result["ytm"][sym] = enhanced_ytm(c.close, c.maturity_date, now)

        # 成交活跃度
        result["activity"][sym] = turnover_activity(c)

        # 价值-规模复合（使用自适应归一化区间）
        result["composite"][sym] = value_size_composite(c, pool_stats=pool_stats)

        # 信用评级
        result["rating"][sym] = rating_score(c.rating)

    # 溢价率动量（需要历史数据）
    if premium_tracker is not None:
        result["premium_momentum"] = {}
        for c in candidates:
            mom = premium_tracker.momentum(c.symbol)
            if mom is not None:
                result["premium_momentum"][c.symbol] = mom

    return result
