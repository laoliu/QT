"""可转债 IVOL-价值策略 — 正交因子维度融合

Alpha 来源与学术基础：
1. **特异波动率 (IVOL)** — Ang, Hodrick, Xing, Zhang (2006)
   低 IVOL 标的具有异常正超额收益（低波异象）。
   在 CB 市场中，低 IVOL 通常对应：信息透明度高、
   发行人质量好、流动性稳定的标的。

2. **52 周高点距离** — George & Hwang (2004) 锚定效应
   接近 52 周高点的标的有动量延续效应。
   投资者对历史高点有心理锚定，突破后惜售导致动量持续。

3. **Theta 代理** — CB 特有因子
   标准化的期权时间价值衰减速率。
   高 Theta + 远到期 → 期权成分有价值且衰减可控。

4. **传统价值因子** — 保留已验证的价格/溢价率因子作为基底
   确保与纯因子策略的兼容性，但降低传统因子权重。

设计理念：
- 传统维度 (price+premium) 权重控制在 30% 以下
- 新维度 (IVOL + high_distance + theta) 权重 ≥ 50%
- 尺度因子 (size) 保留 ~20%，与小盘效应互补
- 面向与 triple_low / sq_v4b / small_size 的**低相关性**组合构建
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.cb_orthogonal_factors import (
    HighDistanceTracker,
    IVOLTracker,
    SkewnessTracker,
    theta_proxy,
)
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.registry import StrategyRegistry
from aipha.utils.rank_utils import rank_normalize_list

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 策略配置
# ---------------------------------------------------------------------------


@dataclass
class IVOLValueConfig(CBRotationConfig):
    """IVOL-价值策略配置

    因子权重说明：
    - 传统价值因子 (price + premium) ≤ 30%
    - 正交新因子 (IVOL + high_distance + theta + skewness) ≥ 50%
    - 尺度因子 (size) ~20%
    """

    top_k: int = 20
    rebalance_days: int = 5

    # 传统因子权重（基底，但降权）
    price_weight: float = 0.15       # 低价格更优
    premium_weight: float = 0.10     # 低溢价率更优

    # 正交因子权重（核心差异化来源）
    ivol_weight: float = 0.20        # 低特异波动率更优
    high_distance_weight: float = 0.15  # 接近 52w 高点更优
    theta_weight: float = 0.10       # 高 Theta 代理更优
    skewness_weight: float = 0.10    # 正偏度更优

    # 尺度因子
    size_weight: float = 0.20        # 小规模更优

    # IVOL Tracker 参数
    ivol_lookback: int = 20
    ivol_min_obs: int = 10

    # High Distance Tracker 参数
    high_distance_lookback: int = 252

    # Skewness Tracker 参数
    skewness_lookback: int = 20
    skewness_min_obs: int = 10

    # 筛选
    max_price: float = 135.0
    min_turnover: float = 50.0
    min_days_to_maturity: int = 60
    exclude_st: bool = True

    # 风控
    stop_loss_pct: float = 0.12
    buffer_k: int = 3

    lot_size: int = 10


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


@StrategyRegistry().register("cb_ivol_value")
class CBIVOLValueStrategy(CBRotationStrategy):
    """可转债 IVOL-价值策略

    融合特异波动率、52 周高点距离、Theta 代理等正交因子，
    与传统价格/溢价率因子加权融合。目标：产出与 triple_low/
    small_size 等现有策略低相关的 alpha 信号。

    数据要求：
    - 需要 ≥20 个 bar 的历史数据来计算 IVOL（冷启动期）
    - 需要 ≥20 个 bar 来计算偏度
    - 52 周高点距离需要 ≥20 个 bar 的最低先验数据

    适用场景：
    - 作为组合中的正交成分（低相关性维度贡献者）
    - 适合与传统 triple_low/double_low/small_size 等价值型策略搭配
    """

    name = "cb_ivol_value"

    def __init__(self, config: IVOLValueConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or IVOLValueConfig(**kwargs)
        self._ivol_tracker = IVOLTracker(
            lookback=self._config.ivol_lookback,
            min_obs=self._config.ivol_min_obs,
        )
        self._high_tracker = HighDistanceTracker(
            lookback=self._config.high_distance_lookback,
        )
        self._skewness_tracker = SkewnessTracker(
            lookback=self._config.skewness_lookback,
            min_obs=self._config.skewness_min_obs,
        )

    @property
    def config(self) -> IVOLValueConfig:
        """当前策略配置对象。"""
        return self._config

    def _on_start_extra(self) -> None:
        """策略启动时重置 Trackers。"""
        self._ivol_tracker.reset()
        self._high_tracker.reset()
        self._skewness_tracker.reset()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext
    ) -> None:
        """每 bar 更新所有 Tracker 的状态。"""
        self._ivol_tracker.update(bars)
        self._high_tracker.update(bars)
        self._skewness_tracker.update(bars)

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """七因子排名归一化加权。

        因子方向（排名方式）：
        - 升序（越小越优）：price, premium, size, ivol(已取负)
        - 降序（越大越优）：high_distance, theta, skewness

        流程：
        1. 为每个候选计算所有因子值（缺失因子用中位数填充）
        2. 逐因子排名归一化到 [0, 1]
        3. 加权求和得 composite score
        4. 返回 score 最低的 top_k 个标的
        """
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []
        if n == 1:
            return [candidates[0].symbol]

        now = context.current_time

        # 步骤 1: 构建因子数据
        data: list[dict] = []
        for c in candidates:
            ivol = self._ivol_tracker.get_ivol(c.symbol)
            high_dist = self._high_tracker.get_distance(c.symbol, c.close)
            skew = self._skewness_tracker.get_skewness(c.symbol)
            theta = theta_proxy(
                close=c.close,
                maturity_date=c.maturity_date,
                now=now,
            )

            data.append({
                "symbol": c.symbol,
                "close": c.close,
                "premium": c.cb_over_rate if c.cb_over_rate is not None else 50.0,
                "size": c.issue_size,
                "ivol": ivol,          # 已取负（IVOL 低 → 值高）
                "high_dist": high_dist,
                "theta": theta,
                "skewness": skew,
            })

        # 步骤 1.5: 中位数填充缺失值
        for key in ("ivol", "high_dist", "skewness"):
            valid = sorted(d[key] for d in data if d[key] is not None)
            if valid:
                median_val = valid[len(valid) // 2]
            else:
                median_val = 0.0
            for d in data:
                if d[key] is None:
                    d[key] = median_val

        # 步骤 2: 逐因子排名归一化 — 委托 rank_normalize_list (§13.2)

        # 升序因子（越小越优 → rank 低 = 分数低 = 更优）
        for key in ("close", "premium", "size"):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=True, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # IVOL: 已取负（ivol 值越大 → IVOL 越低 → 越优）→ 降序
        # high_dist, theta, skewness: 越大越优 → 降序
        for key in ("ivol", "high_dist", "theta", "skewness"):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=False, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # 步骤 3: 加权求和
        for d in data:
            d["score"] = (
                cfg.price_weight * d["close_rank"]
                + cfg.premium_weight * d["premium_rank"]
                + cfg.size_weight * d["size_rank"]
                + cfg.ivol_weight * d["ivol_rank"]
                + cfg.high_distance_weight * d["high_dist_rank"]
                + cfg.theta_weight * d["theta_rank"]
                + cfg.skewness_weight * d["skewness_rank"]
            )

        # 步骤 4: 按 score 升序（最低 = 最优）
        data.sort(key=lambda x: x["score"])
        return [d["symbol"] for d in data[: cfg.top_k]]
