"""可转债热度动量策略 (Heat / Attention Momentum)

灵感来源：国金证券 2025 年报告中的「热度策略」——换手差因子，
以及 IF 资产量化的轮动策略思路（成交活跃度追踪）。

Alpha 原理：
    - 「换手差」= 转债近期换手 vs 历史均值的加速度
    - 转债成交额开始加速增长（不是绝对高位，而是增速提升）
      表明市场关注度从低水平快速升温
    - 配合低溢价率 + 价格安全垫，捕捉"关注度即将爆发但估值仍低"的标的
    - 不同于 turnover_surge（绝对高换手水平筛选），
      热度动量关注的是 **换手率的变化趋势**（加速 vs 减速）

因子矩阵（4 因子）：
1. **换手加速度 (turnover_accel)**: MA(amount, 5) / MA(amount, 20)
   → 短期均值/长期均值 > 1 表示成交加速，越高越好
2. **转股溢价率 (premium)**: cb_over_rate
   → 越低越好（正股联动性强）
3. **价格安全垫 (price)**: close
   → 越低越好（估值保护）
4. **信用评级 (rating)**: 高评级优先
   → 排除低资质"雷券"

区别于 turnover_surge：
- turnover_surge: "当日成交 / 20日均值 ≥ 2倍" → 单日爆发式筛选
- heat_momentum: "5日均值 / 20日均值" → 趋势性加速度筛选
- 本策略不要求单日极端放量，而是持续升温的温和加速
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    _RATING_ORDER,
)
from aipha.strategy.registry import StrategyRegistry
from aipha.utils.rank_utils import rank_normalize_list

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class CBHeatMomentumConfig(CBRotationConfig):
    """热度动量策略配置。"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 135.0
    min_turnover: float = 20.0          # 万元
    min_issue_size: float = 0.0         # 万元
    max_issue_size: float = float("inf")  # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    buffer_k: int = 2
    force_redeem_threshold: float = 130.0
    force_redeem_days: int = 8
    exclude_called: bool = True

    # --- 信用筛选 ---
    min_rating: str = ""
    exclude_unrated: bool = False

    # --- 溢价率上限 ---
    max_premium: float = 40.0

    # --- 换手加速窗口 ---
    accel_short_window: int = 5         # 短期成交均值窗口
    accel_long_window: int = 20         # 长期成交均值窗口
    min_history: int = 22               # 至少需要的历史数据量

    # --- 溢价率上限 ---
    max_premium: float = 50.0

    # --- 四因子权重（自动归一化）---
    w_accel: float = 0.15               # 换手加速度（反向信号, 保持低权重）
    w_premium: float = 0.35             # 溢价率（主因子）
    w_price: float = 0.35               # 价格（主因子）
    w_rating: float = 0.15              # 评级


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


@StrategyRegistry().register("cb_heat_momentum")
class CBHeatMomentumStrategy(CBRotationStrategy):
    """热度动量策略。

    捕捉成交额从低位加速回升的转债：
    - 5 日成交均值 / 20 日成交均值 → 加速度
    - 配合低溢价 + 低价格 + 高评级做安全保护
    """

    name = "cb_heat_momentum"

    def __init__(self, config: CBHeatMomentumConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBHeatMomentumConfig(**kwargs)
        self._amount_history: dict[str, deque] = {}

    @property
    def config(self) -> CBHeatMomentumConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._amount_history.clear()

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """溢价率上限过滤。"""
        cfg = self._config
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False
        return True

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """维护每只转债的成交额历史。"""
        maxlen = self._config.accel_long_window + 10
        for symbol, bar in bars.items():
            amt = (bar.metadata or {}).get("amount", 0)
            if amt is None:
                amt = 0
            if symbol not in self._amount_history:
                self._amount_history[symbol] = deque(maxlen=maxlen)
            self._amount_history[symbol].append(float(amt))

    def _should_rebalance(self) -> bool:
        cfg = self._config
        if self._bar_count < cfg.min_history:
            return False
        return (self._bar_count - 1) % cfg.rebalance_days == 0

    def _compute_accel(self, symbol: str) -> float | None:
        """计算换手加速度 = MA(short) / MA(long)。

        Returns:
            加速度比值。None 表示数据不足。
            > 1.0 → 成交加速；< 1.0 → 成交减速
        """
        cfg = self._config
        hist = self._amount_history.get(symbol)
        if hist is None or len(hist) < cfg.accel_long_window:
            return None

        recent = list(hist)
        short_slice = recent[-cfg.accel_short_window:]
        long_slice = recent[-cfg.accel_long_window:]

        ma_short = sum(short_slice) / len(short_slice) if short_slice else 0
        ma_long = sum(long_slice) / len(long_slice) if long_slice else 0

        if ma_long < 1e-8:
            return None

        return ma_short / ma_long

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """四因子排名打分。

        加速度：正向（越高越好 → 成交升温）
        溢价率、价格：反向（越低越好）
        评级：正向（越高越好）
        """
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []

        # --- 采集因子值 ---
        data: list[dict] = []
        for c in candidates:
            premium = c.cb_over_rate if c.cb_over_rate is not None else 50.0

            accel = self._compute_accel(c.symbol)
            if accel is None:
                accel = 1.0  # 中性填充

            rating_num = _RATING_ORDER.get(c.rating, 30)
            data.append({
                "symbol": c.symbol,
                "accel": accel,
                "premium": premium,
                "close": c.close,
                "rating": rating_num,
            })

        n = len(data)
        if n < 2:
            return [d["symbol"] for d in data[:cfg.top_k]]

        # --- 排名归一化 — 委托 rank_normalize_list (§13.2) ---
        # 正向因子（越大越好 → rank 0 = 最优）
        for key in ("rating",):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=False, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # 反向因子（越小越好 → rank 0 = 最优）
        # accel: 低加速度 = 被遗忘标的 = 反向信号（经诊断验证）
        for key in ("accel", "premium", "close"):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=True, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # --- 加权综合得分 ---
        total_w = cfg.w_accel + cfg.w_premium + cfg.w_price + cfg.w_rating
        if total_w < 1e-8:
            total_w = 1.0

        for d in data:
            d["score"] = (
                cfg.w_accel * d["accel_rank"]
                + cfg.w_premium * d["premium_rank"]
                + cfg.w_price * d["close_rank"]
                + cfg.w_rating * d["rating_rank"]
            ) / total_w

        data.sort(key=lambda x: x["score"])
        return [d["symbol"] for d in data[:cfg.top_k]]
