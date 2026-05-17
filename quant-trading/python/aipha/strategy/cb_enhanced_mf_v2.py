"""可转债增强多因子策略 V2

Alpha-Plus 路线图 Phase 1 产出 — 在现有 7 因子基础上叠加 5 个正交新因子。

因子体系 (12 因子):
  基础因子 (来自 v1 MultiFactorStrategy):
    1. price       — 低价因子 (升序: 越低越优)
    2. premium     — 溢价率因子 (升序)
    3. cb_value    — 转股价值因子 (降序: 越高越优)
    4. issue_size  — 小规模因子 (升序)
    5. delta       — Delta 暴露因子 (降序)
    6. rating      — 信用评级因子 (降序)
    7. ytm         — 到期收益率因子 (降序)

  新增因子 (来自 factors/cb_factors.py):
    8.  iv_bias           — 隐含波动率偏差 (升序: 低 IV = 便宜期权)
    9.  gamma_convexity   — Gamma 凸性价值 (降序: 高凸性 = 好)
    10. credit_spread     — 信用利差 (降序: 高利差 = 高补偿)
    11. liquidity_activity — 流动性活跃度 (降序: 高活跃 = 好)
    12. maturity_convexity — 到期凸性 (降序: 接近最优区间)

权重方案:
  - 默认等权 12 因子 (每个 ~8.3%)
  - 支持 IC 加权模式: 按历史 IC 绝对值动态调整权重

策略逻辑:
  1. 对每只 CB 计算 12 个因子值
  2. 逐因子独立横截面排名并归一化到 [0, 1]
  3. 加权合并: total_score = Σ(w_i * rank_i)
  4. 持有 top_k (score 最低/最优) 的 CB
  5. 定期轮动 (rebalance_days)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.cb_factors import (
    compute_credit_spread,
    compute_gamma_convexity,
    compute_iv_bias,
    compute_liquidity_activity,
    compute_maturity_convexity,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_enhanced_factors import (
    delta_exposure,
    enhanced_ytm,
    rating_score,
)
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry
from aipha.utils.rank_utils import rank_normalize_list

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class EnhancedMFV2Config(CBRotationConfig):
    """增强多因子 V2 策略配置"""

    top_k: int = 20
    rebalance_days: int = 3

    # --- 基础因子权重 (v1 继承) ---
    price_weight: float = 0.10
    premium_weight: float = 0.10
    cb_value_weight: float = 0.08
    size_weight: float = 0.10

    # --- v1 增强因子权重 ---
    delta_weight: float = 0.08
    rating_weight: float = 0.06
    ytm_weight: float = 0.06

    # --- v2 新增因子权重 ---
    iv_bias_weight: float = 0.10
    gamma_convexity_weight: float = 0.10
    credit_spread_weight: float = 0.08
    liquidity_activity_weight: float = 0.06
    maturity_convexity_weight: float = 0.08

    # --- 筛选 ---
    max_price: float = 130.0
    max_premium: float = 40.0
    min_turnover: float = 50.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True

    # --- 风控 ---
    stop_loss_pct: float = 0.15
    buffer_k: int = 0

    # --- 新因子参数 ---
    risk_free_rate: float = 0.02  # §13.1 全局统一 RISK_FREE_RATE_DEFAULT
    maturity_sweet_spot: float = 3.0
    maturity_half_range: float = 3.0

    lot_size: int = 10


@StrategyRegistry().register("cb_enhanced_mf_v2")
class CBEnhancedMultifactorV2Strategy(CBRotationStrategy):
    """可转债增强多因子策略 V2 — 12 因子融合。

    在 v1 的 7 因子基础上新增 5 个正交因子:
    iv_bias, gamma_convexity, credit_spread,
    liquidity_activity, maturity_convexity。
    """

    name = "cb_enhanced_mf_v2"

    def __init__(self, config: EnhancedMFV2Config | None = None, **kwargs):
        super().__init__()
        self._config = config or EnhancedMFV2Config(**kwargs)

    @property
    def config(self) -> EnhancedMFV2Config:
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率上限 + 需要溢价率数据。"""
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """12 因子排名归一化加权。"""
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []
        if n == 1:
            return [candidates[0].symbol]

        now = context.current_time

        # --- 计算因子值 ---
        data: list[dict] = []
        for c in candidates:
            data.append({
                "symbol": c.symbol,
                # 基础因子
                "close": c.close,
                "cb_over_rate": float(c.cb_over_rate),  # guaranteed non-None by _extra_filter
                "cb_value": c.cb_value if c.cb_value is not None else c.close,
                "issue_size": c.issue_size,
                # v1 增强因子
                "delta": delta_exposure(c),
                "rating": rating_score(c.rating),
                "ytm": enhanced_ytm(c.close, c.maturity_date, now),
                # v2 新因子
                "iv_bias": compute_iv_bias(
                    c.close, c.cb_value, c.maturity_date, now,
                ),
                "gamma_convexity": compute_gamma_convexity(
                    c.cb_value, c.maturity_date, now,
                ),
                "credit_spread": compute_credit_spread(
                    c.close, c.maturity_date, now, cfg.risk_free_rate,
                ),
                "liquidity_activity": compute_liquidity_activity(
                    c.amount, c.issue_size,
                ),
                "maturity_convexity": compute_maturity_convexity(
                    c.maturity_date, now,
                    cfg.maturity_sweet_spot, cfg.maturity_half_range,
                ),
            })

        # --- 升序因子 (越小越优) --- rank 0 = 最小 ---
        asc_factors = ("close", "cb_over_rate", "issue_size", "iv_bias")
        for key in asc_factors:
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=True, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # --- 降序因子 (越大越优) --- rank 0 = 最大 ---
        desc_factors = (
            "cb_value", "delta", "rating", "ytm",
            "gamma_convexity", "credit_spread",
            "liquidity_activity", "maturity_convexity",
        )
        for key in desc_factors:
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=False, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # --- 加权合并 ---
        weight_map = {
            "close": cfg.price_weight,
            "cb_over_rate": cfg.premium_weight,
            "cb_value": cfg.cb_value_weight,
            "issue_size": cfg.size_weight,
            "delta": cfg.delta_weight,
            "rating": cfg.rating_weight,
            "ytm": cfg.ytm_weight,
            "iv_bias": cfg.iv_bias_weight,
            "gamma_convexity": cfg.gamma_convexity_weight,
            "credit_spread": cfg.credit_spread_weight,
            "liquidity_activity": cfg.liquidity_activity_weight,
            "maturity_convexity": cfg.maturity_convexity_weight,
        }

        for d in data:
            d["score"] = sum(
                w * d[f"{k}_rank"] for k, w in weight_map.items()
            )

        # 按 score 升序 (越低越优)
        data.sort(key=lambda x: x["score"])

        return [d["symbol"] for d in data[: cfg.top_k]]
