"""可转债多因子融合策略（增强版 — R94 去共线性优化）

Alpha 来源：
1. low_price 排名信号 — 面值回归 & 债底保护
2. premium 排名信号 — 转股溢价率（跨维度交叉因子）
3. 小规模因子 — 发行人促转股动力（使用 issue_size 替代 remain_size 避免前视偏差）
4. **Delta 暴露因子** — cb_value/close，衡量正股弹性
5. **信用评级因子** — AAA~C 映射为可排名数值

R94 去共线性说明（2026-03-15）：
  - 移除 YTM 因子：与 price 因子近单调函数关系（ρ ≈ -0.9），信息完全冗余
  - 移除 cb_value 因子：与 delta 高度共线（ρ ≈ 0.8），delta = cb_value/close 更优
  - 保留 5 个有效独立维度：price / premium / issue_size / delta / rating
  - 提权真正独立的维度：issue_size (0.15→0.25)、rating (0.10→0.20)
  - 旧 7 因子配置仍可通过 YAML 显式设置 ytm_weight/cb_value_weight 启用

策略逻辑：
1. 对每只转债计算多因子值
2. 逐因子独立排名并归一化（0~1）
3. 加权合并: total_score = Σ (w_i * rank_i)
4. 持有 top_k（score最低/最优）的转债
5. 定期轮动
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

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
class MultiFactorConfig(CBRotationConfig):
    """多因子融合策略配置"""

    top_k: int = 20
    rebalance_days: int = 3

    # 基础因子权重（R94 去共线性后的默认值）
    price_weight: float = 0.25        # 价格因子
    premium_weight: float = 0.20      # 溢价率因子
    cb_value_weight: float = 0.00     # 转股价值因子（R94: 与delta共线，默认关闭）
    size_weight: float = 0.25         # 小规模因子（使用 issue_size）— 提权

    # 增强因子权重
    delta_weight: float = 0.10        # Delta 暴露因子（保留，替代 cb_value）
    rating_weight: float = 0.20       # 信用评级因子 — 提权
    ytm_weight: float = 0.00          # 近似 YTM 因子（R94: 与price共线，默认关闭）

    # 筛选
    max_price: float = 130.0
    max_premium: float = 40.0
    min_turnover: float = 50.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True

    # 风控
    stop_loss_pct: float = 0.15
    buffer_k: int = 0   # 缓冲带

    lot_size: int = 10


@StrategyRegistry().register("multi_factor")
class MultiFactorStrategy(CBRotationStrategy):
    """可转债多因子融合策略

    将价格、溢价率、转股价值、发行规模四个因子排名加权，
    取综合评分最低的 top_k 只持有。
    使用 issue_size（静态字段）替代 remain_size，消除前视偏差。
    """

    name = "multi_factor"

    def __init__(self, config: MultiFactorConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or MultiFactorConfig(**kwargs)

    @property
    def config(self) -> MultiFactorConfig:
        """当前策略配置对象。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率上限 + 需要溢价率数据。"""
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """多因子排名归一化加权（R94 去共线性版）。

        默认 5 因子：价格、溢价率、发行规模、Delta 暴露、信用评级
        （cb_value 与 YTM 因与已有因子高度共线，默认权重为 0）

        注：权重为 0 的因子仍参与计算但不影响最终排名。
        若需启用旧版 7 因子，在 YAML 中设置对应 weight > 0 即可。
        """
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []
        if n == 1:
            return [candidates[0].symbol]

        # 构建排名数据 —— 增加新因子值
        now = context.current_time
        data: list[dict] = []
        for c in candidates:
            data.append({
                "symbol": c.symbol,
                "close": c.close,
                "cb_over_rate": float(c.cb_over_rate),  # guaranteed non-None by _extra_filter
                "cb_value": c.cb_value if c.cb_value is not None else c.close,
                "issue_size": c.issue_size,
                "delta": delta_exposure(c),
                "rating": rating_score(c.rating),
                "ytm": enhanced_ytm(c.close, c.maturity_date, now),
            })

        # 升序因子（越小越优）：价格、溢价率、发行规模
        for key in ("close", "cb_over_rate", "issue_size"):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=True, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # 降序因子（越大越优）：转股价值、Delta、评级、YTM
        for key in ("cb_value", "delta", "rating", "ytm"):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=False, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # 加权合并
        for d in data:
            d["score"] = (
                cfg.price_weight * d["close_rank"]
                + cfg.premium_weight * d["cb_over_rate_rank"]
                + cfg.cb_value_weight * d["cb_value_rank"]
                + cfg.size_weight * d["issue_size_rank"]
                + cfg.delta_weight * d["delta_rank"]
                + cfg.rating_weight * d["rating_rank"]
                + cfg.ytm_weight * d["ytm_rank"]
            )

        # 按 score 升序（越低越优）
        data.sort(key=lambda x: x["score"])

        # 注意：buffer_k 逻辑已由基类 _generate_rebalance 统一处理，
        # 此处仅返回排序后的 top_k 列表，避免双重缓冲。
        return [d["symbol"] for d in data[: cfg.top_k]]
