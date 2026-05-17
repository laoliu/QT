"""CB 信用利差因子策略

信用利差（Credit Spread）是可转债定价中**债性维度**的核心指标，
与传统的价格/溢价率/规模因子在理论基础和信息来源上正交：

**理论基础**（对齐 copilot-instructions.md §11.2）：
- CB = Bond Floor + Option Value + Credit Spread Premium
- Bond Floor = PV(coupons + par @ risk-free rate)
- Credit Spread = YTM_CB - Risk_Free_Rate
- 高信用利差 → 市场高估信用风险 → 均值回归 alpha（Robeco 2024）
- 信用评级越高，利差越低 → rating 是利差的离散代理变量

**因子矩阵（6 因子，3 层架构）**：
Layer 1 — 债性因子（本策略核心创新）：
  1. **近似 YTM** (approx_ytm): 到期收益率，越高→越便宜（权重 0.25）
  2. **信用评级分** (rating_score): 评级映射，AAA=8,AA+=7,...A=3（权重 0.15）
  3. **债底距离** (bond_floor_ratio): close/100 的倒数，越近面值→债底保护越强（权重 0.15）
Layer 2 — 传统因子（已验证基线）：
  4. **价格因子** (close): 低价→安全边际（权重 0.20）
  5. **溢价率因子** (cb_over_rate): 低溢价→股性弹性（权重 0.15）
Layer 3 — 流动性/规模弱因子：
  6. **发行规模** (issue_size): 小规模→定价偏差（权重 0.10）

**与现有策略的正交性分析**：
- triple_low: 价格+溢价率+规模 → 纯股性/价值维度
- sq_v4b: 正股 ROE+EP+Vol → 正股基本面维度
- small_size: 流通盘+成交额 → 流动性维度
- **本策略**: YTM+评级+债底 → **债性/信用风险维度** ← NEW
⇒ 预期与 triple_low/small_size 相关性 < 0.5

**经济学解释**（§1.4 原则 A）：
- 信用利差反映债券发行人违约概率的市场定价
- 中国 CB 发行人实际违约极少（面值保底 + 下修条款），但市场仍对低评级CB过度折价
- 利差均值回归的经济动力：投资者恐惧消退、评级上调预期、转股成功预期
- 这是"信息不对称+行为偏差"的经典 alpha 来源

数据来源：
- 近似 YTM：cb_enhanced_factors.enhanced_ytm()
- 信用评级：CandidateInfo.rating
- 其他因子：CandidateInfo 标准字段
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aipha.strategy.base import StrategyContext
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
# 信用评级映射
# ---------------------------------------------------------------------------

# 中国 CB 信用评级到数值映射（越高越好）
RATING_SCORE_MAP: dict[str, float] = {
    "AAA": 8.0,
    "AA+": 7.0,
    "AA": 6.0,
    "AA-": 5.0,
    "A+": 4.0,
    "A": 3.0,
    "A-": 2.0,
    "BBB+": 1.5,
    "BBB": 1.0,
    "BB+": 0.5,
    "BB": 0.0,
}

# 无评级时的默认分（中性偏低，等同 A- ）
DEFAULT_RATING_SCORE: float = 2.0


def rating_to_score(rating: str) -> float:
    """将信用评级字符串转换为数值分数。

    Args:
        rating: 评级字符串，如 "AAA", "AA+", "AA" 等

    Returns:
        对应的数值分数，无法识别则返回默认值
    """
    if not rating:
        return DEFAULT_RATING_SCORE
    rating_clean = rating.strip().upper()
    return RATING_SCORE_MAP.get(rating_clean, DEFAULT_RATING_SCORE)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CreditSpreadCBConfig(CBRotationConfig):
    """信用利差因子策略配置。

    Attributes:
        weight_ytm: 近似 YTM 因子权重（越高越便宜 → 越好）
        weight_rating: 信用评级因子权重（越高 → 信用越好）
        weight_bond_floor: 债底距离因子权重（越近面值 → 越安全）
        weight_price: 价格因子权重（低价好）
        weight_premium: 溢价率因子权重（低溢价好）
        weight_issue_size: 发行规模因子权重（小规模好）
        max_premium: 溢价率过滤上限 (%)
        min_rating: 最低信用评级（低于此评级的 CB 被排除）
        use_age_filter: 是否启用次新过滤
        min_age_days: 最小上市天数
        ytm_mode: YTM 计算模式 ("simple"=简单近似, "enhanced"=增强)
    """

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 150.0          # 债性策略可适当放宽价格上限
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0        # 万元
    min_days_to_maturity: int = 60    # 到期日太近的排除（避免赎回风险）
    stop_loss_pct: float = 0.20
    lot_size: int = 10

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3

    # --- 溢价率过滤 ---
    max_premium: float = 100.0        # 比双低更宽松

    # --- 信用评级过滤 ---
    min_rating: str = ""              # 空=不过滤；"AA-"=排除 A+及以下

    # --- 6 因子权重 ---
    # Layer 1: 债性因子（核心）
    weight_ytm: float = 0.25          # 近似 YTM（越高越好）
    weight_rating: float = 0.15       # 信用评级（越高越好）
    weight_bond_floor: float = 0.15   # 债底距离（越近面值越好）
    # Layer 2: 传统因子
    weight_price: float = 0.20        # 低价好
    weight_premium: float = 0.15      # 低溢价好
    # Layer 3: 弱因子
    weight_issue_size: float = 0.10   # 小规模好

    # --- YTM 模式 ---
    ytm_mode: str = "enhanced"        # "simple" or "enhanced"


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_credit_spread")
class CBCreditSpreadStrategy(CBRotationStrategy):
    """信用利差因子可转债策略。

    6 因子排名加权选股，核心创新是引入**债性维度**因子（YTM + 评级 + 债底距离），
    与现有策略的股性/基本面/流动性维度正交。

    score = w_ytm        * rank(approx_ytm, ↓)       # 高 YTM = 便宜
          + w_rating     * rank(rating_score, ↓)      # 高评级 = 安全
          + w_bond_floor * rank(100/close, ↓)         # 越近面值 = 债底保护
          + w_price      * rank(close, ↑)             # 低价好
          + w_premium    * rank(cb_over_rate, ↑)      # 低溢价好
          + w_size       * rank(issue_size, ↑)        # 小规模好
    """

    name = "cb_credit_spread"

    def __init__(
        self,
        config: CreditSpreadCBConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or CreditSpreadCBConfig(**kwargs)

    @property
    def config(self) -> CreditSpreadCBConfig:
        return self._config

    # --- 过滤 ---

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率 + 次新 + 评级过滤。"""
        cfg = self._config

        # 溢价率过滤
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        # 次新过滤
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        # 评级过滤
        if cfg.min_rating:
            min_score = rating_to_score(cfg.min_rating)
            cand_score = rating_to_score(candidate.rating)
            if cand_score < min_score:
                return False

        return True

    # --- 打分 ---

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """6 因子排名加权评分（债性 + 传统 + 弱因子）。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # 获取当前时间用于 YTM 计算
        from datetime import datetime as dt

        now = (
            context.current_time
            if isinstance(context.current_time, dt)
            else dt.combine(context.current_time, dt.min.time())
        )

        # ----- 提取因子原始值 -----

        # Layer 1: 债性因子
        ytm_vals: list[float] = []
        for c in candidates:
            if cfg.ytm_mode == "enhanced" and c.maturity_date:
                ytm_val = enhanced_ytm(c.close, c.maturity_date, now)
            else:
                # 简单近似: YTM ≈ (100/close)^(1/years) - 1
                ytm_val = (100.0 / c.close - 1.0) if c.close > 0 else 0.0
            ytm_vals.append(ytm_val)

        rating_scores = [rating_to_score(c.rating) for c in candidates]

        bond_floor_ratios = [
            100.0 / c.close if c.close > 0 else 0.0
            for c in candidates
        ]

        # Layer 2: 传统因子
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]

        # Layer 3: 弱因子
        sizes = [c.issue_size for c in candidates]

        # ----- 排名归一化 -----
        rank_ytm = _rank_scores(ytm_vals, ascending=False)         # 高 YTM 好
        rank_rating = _rank_scores(rating_scores, ascending=False)  # 高评级好
        rank_floor = _rank_scores(bond_floor_ratios, ascending=False)  # 近面值好
        rank_price = _rank_scores(prices, ascending=True)           # 低价好
        rank_premium = _rank_scores(premiums, ascending=True)       # 低溢价好
        rank_size = _rank_scores(sizes, ascending=True)             # 小规模好

        # ----- 权重归一化 -----
        total_w = (
            cfg.weight_ytm + cfg.weight_rating + cfg.weight_bond_floor
            + cfg.weight_price + cfg.weight_premium + cfg.weight_issue_size
        )
        if total_w <= 0:
            total_w = 1.0

        w_ytm = cfg.weight_ytm / total_w
        w_rat = cfg.weight_rating / total_w
        w_flr = cfg.weight_bond_floor / total_w
        w_pri = cfg.weight_price / total_w
        w_prm = cfg.weight_premium / total_w
        w_sz = cfg.weight_issue_size / total_w

        # ----- 综合打分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                w_ytm * rank_ytm[i]
                + w_rat * rank_rating[i]
                + w_flr * rank_floor[i]
                + w_pri * rank_price[i]
                + w_prm * rank_premium[i]
                + w_sz * rank_size[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]
