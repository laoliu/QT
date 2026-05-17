"""增强型小规模轮动策略

在原始 small_size 策略基础上增强：
1. 多因子打分：issue_size + premium + momentum 综合排名
2. 平滑截断：使用 buffer_k 缓冲避免参数悬崖（top_k 边缘频繁进出）
3. 可配置权重分配

Alpha 来源：
- 小发行规模 → 关注度低、定价偏差大
- 低溢价率 → 债底保护 + 转股性价比高
- 价格动量 → 趋势延续（小盘转债动量效应显著）

消除参数悬崖的方法：
- buffer_k 缓冲（已在 CBRotationBase 中实现）
- 连续评分替代硬截断（非 issue_size 单一排序）
- 多因子加权分散单因子集中风险

参考：
    集思录"小盘+低溢价"组合策略
    Qlib 多因子打分范式
"""

from __future__ import annotations

import logging
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
    pass

logger = logging.getLogger(__name__)


@dataclass
class SmallSizeEnhancedConfig(CBRotationConfig):
    """增强型小规模策略配置"""

    top_k: int = 20
    rebalance_days: int = 1
    max_price: float = 115.0
    max_issue_size: float = 30000.0   # 万元（小盘alpha源，放宽会稀释收益）
    min_issue_size: float = 3000.0    # 万元
    min_turnover: float = 50.0
    min_days_to_maturity: int = 60
    exclude_st: bool = True
    stop_loss_pct: float = 0.15
    lot_size: int = 10

    # --- buffer 缓冲 ---
    buffer_k: int = 5                 # 已持仓标的在 top_k+5 内保留

    # --- 多因子权重（归一化后使用）---
    size_weight: float = 0.50         # 发行规模因子权重（越小越好）
    premium_weight: float = 0.30      # 溢价率因子权重（越低越好）
    momentum_weight: float = 0.20     # 价格因子权重（越低越好→防追高）

    # --- 额外过滤 ---
    max_premium: float = 50.0         # 溢价率上限（%）


@StrategyRegistry().register("small_size_enhanced")
class SmallSizeEnhancedStrategy(CBRotationStrategy):
    """增强型小规模轮动策略

    核心改进（相比原始 small_size）：
    1. 多因子加权评分：size(50%) + premium(30%) + price(20%)
    2. buffer_k=5 缓冲：减少边缘排名波动导致的换手
    3. 溢价率上限过滤：防止选入纯债无弹性品种

    min-max 归一化打分方式（参考 triple_low）：
    - 各因子在候选池内做 min-max 归一化到 [0, 1]
    - 总分 = Σ weight_i × score_i（越低越好）
    - 按总分升序取 top_k
    """

    name = "small_size_enhanced"

    def __init__(
        self,
        config: SmallSizeEnhancedConfig | None = None,
        **kwargs,
    ):
        super().__init__()
        self._config = config or SmallSizeEnhancedConfig(**kwargs)

    @property
    def config(self) -> SmallSizeEnhancedConfig:
        """当前策略配置对象。"""
        return self._config

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """溢价率上限过滤。"""
        cfg = self._config
        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """多因子 min-max 归一化打分，按总分升序取 top_k。

        因子说明（均为越小越好）：
        - size_score: issue_size 归一化
        - premium_score: cb_over_rate 归一化
        - price_score: close 价格归一化
        """
        cfg = self._config
        if not candidates:
            return []

        # 提取原始值
        sizes = [c.issue_size for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        prices = [c.close for c in candidates]

        # min-max 归一化
        def normalize(vals: list[float]) -> list[float]:
            lo, hi = min(vals), max(vals)
            if hi - lo < 1e-9:
                return [0.5] * len(vals)
            return [(v - lo) / (hi - lo) for v in vals]

        size_scores = normalize(sizes)
        prem_scores = normalize(premiums)
        price_scores = normalize(prices)

        # 加权总分
        w_total = cfg.size_weight + cfg.premium_weight + cfg.momentum_weight
        ws = cfg.size_weight / w_total
        wp = cfg.premium_weight / w_total
        wm = cfg.momentum_weight / w_total

        scored = []
        for i, c in enumerate(candidates):
            total = ws * size_scores[i] + wp * prem_scores[i] + wm * price_scores[i]
            scored.append((c.symbol, total))

        # 按总分升序（越小越好）→ 取 top_k + buffer_k（buffer 由基类处理）
        scored.sort(key=lambda x: x[1])
        # 返回 top_k + buffer_k 个，让基类 _generate_rebalance 做 buffer 逻辑
        n = cfg.top_k + cfg.buffer_k
        return [s for s, _ in scored[:n]]
