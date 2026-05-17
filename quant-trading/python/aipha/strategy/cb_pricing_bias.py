"""CB 定价偏差因子策略

基于**理论定价 vs 市场价格**的偏差捕捉被低估的可转债。

**理论基础**（对齐 copilot-instructions.md §11.2）：
- CB 理论价 = Bond Floor + Option Value
- Bond Floor ≈ 国债收益率折现的纯债价值
- Option Value ≈ f(正股波动率, 剩余年限, 转股溢价率)
- 市场价低于理论价 → 定价偏差（mispricing）→ 均值回归 alpha
- 参考：arXiv 2409.06496 (MC valuation of Chinese CB)
- 参考：EUDL (BS model, Binomial Tree, MC pricing biases)

**简化定价模型**（避免复杂 MC 需要的参数）：
- Bond_Floor ≈ close / (1 + ytm_approx)^years × (1 + coupon_proxy)
  简化为 100 / (1 + risk_free)^years 作为债底远近参考
- Option_Proxy ≈ cb_value × vol_proxy × sqrt(years)
  其中 vol_proxy 通过价格变动反推
- Theoretical_Price ≈ max(bond_floor, cb_value) + time_value
- Pricing_Bias = (Theoretical - Market) / Market

**因子矩阵（6 因子，3 层架构）**：
Layer 1 — 定价偏差因子（核心创新）：
  1. **定价偏差率** (pricing_bias): 理论估值比 → 越正越被低估（权重 0.30）
  2. **时间价值比** (time_value_ratio): 剩余时间价值/价格（权重 0.15）
Layer 2 — 传统因子：
  3. **价格因子** (close): 低价→安全边际（权重 0.20）
  4. **溢价率因子** (cb_over_rate): 低溢价→弹性（权重 0.15）
Layer 3 — 辅助因子：
  5. **信用评级** (rating_score): 高评级→低违约风险（权重 0.10）
  6. **发行规模** (issue_size): 小规模→定价低效（权重 0.10）

**与现有策略正交性**：
- 定价偏差捕捉的是市场对 CB **整体定价效率**的偏离
- 不同于双低（价格+溢价率排序）：本策略评估"应该值多少"
- 不同于信用利差（纯债性维度）：本策略考虑期权时间价值
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_credit_spread import rating_to_score
from aipha.strategy.cb_enhanced_factors import enhanced_ytm
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from datetime import date

    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 定价模型工具函数
# ---------------------------------------------------------------------------

# 中国国债收益率近似（10 年期，用作无风险利率 proxy）
DEFAULT_RISK_FREE_RATE: float = 0.02  # §13.1 全局统一 RISK_FREE_RATE_DEFAULT

# 隐含波动率 proxy（A 股/CB 平均年化波动率）
DEFAULT_VOL_PROXY: float = 0.30  # 30% annualized


def estimate_bond_floor(
    years_to_maturity: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    face_value: float = 100.0,
    coupon_rate: float = 0.003,  # 平均票面利率约 0.3%/年
) -> float:
    """估算纯债价值（Bond Floor）。

    使用简单折现模型：BF = sum(coupon / (1+r)^t) + face / (1+r)^T

    Args:
        years_to_maturity: 剩余年限
        risk_free_rate: 无风险利率
        face_value: 面值
        coupon_rate: 年票面利率（简化为固定）

    Returns:
        估算的纯债价值
    """
    if years_to_maturity <= 0:
        return face_value

    r = max(risk_free_rate, 0.001)
    bf = 0.0
    # 票息折现
    annual_coupon = face_value * coupon_rate
    years_int = max(1, int(math.ceil(years_to_maturity)))
    for t in range(1, years_int + 1):
        frac = min(t, years_to_maturity)
        bf += annual_coupon / (1 + r) ** frac

    # 面值折现
    bf += face_value / (1 + r) ** years_to_maturity

    return bf


def estimate_option_time_value(
    cb_value: float,
    years_to_maturity: float,
    vol_proxy: float = DEFAULT_VOL_PROXY,
) -> float:
    """估算期权时间价值（简化）。

    使用 ATM 期权近似：TimeValue ≈ S × vol × sqrt(T) × 0.4
    其中 0.4 ≈ N'(0)/sqrt(2π) 的近似系数

    Args:
        cb_value: 转股价值（正股价*100/转股价）
        years_to_maturity: 剩余年限
        vol_proxy: 年化波动率代理

    Returns:
        估算的期权时间价值
    """
    if cb_value <= 0 or years_to_maturity <= 0:
        return 0.0

    return cb_value * vol_proxy * math.sqrt(years_to_maturity) * 0.4


def estimate_theoretical_price(
    cb_value: float,
    years_to_maturity: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    vol_proxy: float = DEFAULT_VOL_PROXY,
) -> float:
    """估算 CB 理论价格。

    Theoretical ≈ max(Bond_Floor, Conversion_Value) + Time_Value

    Args:
        cb_value: 转股价值
        years_to_maturity: 剩余年限
        risk_free_rate: 无风险利率
        vol_proxy: 波动率代理

    Returns:
        理论价格估算值
    """
    bond_floor = estimate_bond_floor(years_to_maturity, risk_free_rate)
    time_value = estimate_option_time_value(cb_value, years_to_maturity, vol_proxy)

    # CB 价值 = max(债底, 转股价值) + 时间价值
    intrinsic = max(bond_floor, cb_value) if cb_value > 0 else bond_floor
    return intrinsic + time_value


def compute_pricing_bias(
    market_price: float,
    cb_value: float,
    years_to_maturity: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    vol_proxy: float = DEFAULT_VOL_PROXY,
) -> float:
    """计算定价偏差率。

    pricing_bias = (theoretical - market) / market
    正值 → 被低估，负值 → 被高估

    Args:
        market_price: 市场价格（close）
        cb_value: 转股价值
        years_to_maturity: 剩余年限
        risk_free_rate: 无风险利率
        vol_proxy: 波动率代理

    Returns:
        定价偏差率（正=低估，负=高估）
    """
    if market_price <= 0:
        return 0.0

    theoretical = estimate_theoretical_price(
        cb_value, years_to_maturity, risk_free_rate, vol_proxy
    )
    return (theoretical - market_price) / market_price


def compute_time_value_ratio(
    market_price: float,
    cb_value: float,
    years_to_maturity: float,
    vol_proxy: float = DEFAULT_VOL_PROXY,
) -> float:
    """计算时间价值占比。

    time_value_ratio = estimated_time_value / market_price

    Args:
        market_price: 市场价格
        cb_value: 转股价值
        years_to_maturity: 剩余年限
        vol_proxy: 波动率代理

    Returns:
        时间价值占市场价格的比例
    """
    if market_price <= 0:
        return 0.0

    tv = estimate_option_time_value(cb_value, years_to_maturity, vol_proxy)
    return tv / market_price


# ---------------------------------------------------------------------------
# 日期工具
# ---------------------------------------------------------------------------

def _years_to_maturity(maturity_date_str: str, now) -> float:
    """计算剩余年限。"""
    from datetime import datetime

    if not maturity_date_str:
        return 3.0  # 默认 3 年

    try:
        mat = datetime.strptime(maturity_date_str[:8], "%Y%m%d")
        if hasattr(now, "date"):
            now_dt = now
        else:
            now_dt = datetime.combine(now, datetime.min.time())
        days = (mat - now_dt).days
        return max(days / 365.25, 0.01)
    except (ValueError, TypeError):
        return 3.0


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class PricingBiasCBConfig(CBRotationConfig):
    """定价偏差因子策略配置。

    Attributes:
        weight_pricing_bias: 定价偏差率因子权重（正=被低估，越高越好）
        weight_time_value: 时间价值比因子权重（高=剩余期权价值丰富）
        weight_price: 价格因子权重
        weight_premium: 溢价率因子权重
        weight_rating: 信用评级因子权重
        weight_issue_size: 发行规模因子权重
        risk_free_rate: 无风险利率（用于定价模型）
        vol_proxy: 波动率代理值
        max_premium: 溢价率过滤上限 (%)
        use_age_filter: 是否启用次新过滤
        min_age_days: 最小上市天数
    """

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 160.0          # 更宽松，让定价模型来判断
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 60
    stop_loss_pct: float = 0.20
    lot_size: int = 10

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3

    # --- 溢价率过滤 ---
    max_premium: float = 100.0

    # --- 定价模型参数 ---
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    vol_proxy: float = DEFAULT_VOL_PROXY

    # --- 6 因子权重 ---
    # Layer 1: 定价偏差因子（核心）
    weight_pricing_bias: float = 0.30   # 定价偏差率
    weight_time_value: float = 0.15     # 时间价值比
    # Layer 2: 传统因子
    weight_price: float = 0.20          # 低价好
    weight_premium: float = 0.15        # 低溢价好
    # Layer 3: 辅助因子
    weight_rating: float = 0.10         # 高评级好
    weight_issue_size: float = 0.10     # 小规模好


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_pricing_bias")
class CBPricingBiasStrategy(CBRotationStrategy):
    """定价偏差因子可转债策略。

    核心逻辑：估算 CB 理论价值，找出市场价显著低于理论价的标的。

    score = w_bias       * rank(pricing_bias, ↓)      # 高偏差=更被低估
          + w_tv         * rank(time_value_ratio, ↓)   # 高时间价值好
          + w_price      * rank(close, ↑)              # 低价好
          + w_premium    * rank(cb_over_rate, ↑)       # 低溢价好
          + w_rating     * rank(rating_score, ↓)       # 高评级好
          + w_size       * rank(issue_size, ↑)         # 小规模好
    """

    name = "cb_pricing_bias"

    def __init__(
        self,
        config: PricingBiasCBConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or PricingBiasCBConfig(**kwargs)

    @property
    def config(self) -> PricingBiasCBConfig:
        return self._config

    # --- 过滤 ---

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率 + 次新过滤。"""
        cfg = self._config

        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        return True

    # --- 打分 ---

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """6 因子排名加权评分（定价偏差 + 传统 + 辅助）。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        from datetime import datetime as dt

        now = (
            context.current_time
            if isinstance(context.current_time, dt)
            else dt.combine(context.current_time, dt.min.time())
        )

        # ----- 提取因子原始值 -----

        # Layer 1: 定价偏差因子
        bias_vals: list[float] = []
        tv_vals: list[float] = []
        for c in candidates:
            cb_val = c.cb_value if c.cb_value is not None and c.cb_value > 0 else 0.0
            years = _years_to_maturity(c.maturity_date, now)

            bias = compute_pricing_bias(
                c.close, cb_val, years, cfg.risk_free_rate, cfg.vol_proxy,
            )
            bias_vals.append(bias)

            tv = compute_time_value_ratio(
                c.close, cb_val, years, cfg.vol_proxy,
            )
            tv_vals.append(tv)

        # Layer 2: 传统因子
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]

        # Layer 3: 辅助因子
        rating_scores = [rating_to_score(c.rating) for c in candidates]
        sizes = [c.issue_size for c in candidates]

        # ----- 排名归一化 -----
        rank_bias = _rank_scores(bias_vals, ascending=False)      # 高偏差（被低估）好
        rank_tv = _rank_scores(tv_vals, ascending=False)           # 高时间价值好
        rank_price = _rank_scores(prices, ascending=True)          # 低价好
        rank_premium = _rank_scores(premiums, ascending=True)      # 低溢价好
        rank_rating = _rank_scores(rating_scores, ascending=False) # 高评级好
        rank_size = _rank_scores(sizes, ascending=True)            # 小规模好

        # ----- 权重归一化 -----
        total_w = (
            cfg.weight_pricing_bias + cfg.weight_time_value
            + cfg.weight_price + cfg.weight_premium
            + cfg.weight_rating + cfg.weight_issue_size
        )
        if total_w <= 0:
            total_w = 1.0

        w_b = cfg.weight_pricing_bias / total_w
        w_t = cfg.weight_time_value / total_w
        w_p = cfg.weight_price / total_w
        w_m = cfg.weight_premium / total_w
        w_r = cfg.weight_rating / total_w
        w_s = cfg.weight_issue_size / total_w

        # ----- 综合打分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                w_b * rank_bias[i]
                + w_t * rank_tv[i]
                + w_p * rank_price[i]
                + w_m * rank_premium[i]
                + w_r * rank_rating[i]
                + w_s * rank_size[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]
