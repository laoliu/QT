"""可转债短期反转 + 小盘策略

Alpha 来源：
1. 短期反转效应 — 转债市场散户主导，过度反应带来 3-5 日均值回归
2. 小规模因子 — issue_size 越小，弹性越大，反转空间更大

逻辑：
1. 基础筛选：小规模 CB（issue_size < max_issue_size）
2. 计算 reversal_lookback 天内收益率
3. 取收益率最低（跌幅最大）的 top_k 只
4. 可选：乘以规模排名权重，使小盘+大跌标的排名更高

与 cb_mean_reversion 的区别：
- cb_mean_reversion 使用 z-score 判断偏离度，回望较长
- cb_reversal_small 专注极短期（3-5 日）反转，更高频换手
- 叠加小规模约束，alpha 衰减更慢
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
class CBReversalSmallConfig(CBRotationConfig):
    """短期反转 + 小盘策略配置。"""

    # --- 持仓 ---
    top_k: int = 8
    rebalance_days: int = 3           # 高频换手
    buffer_k: int = 2

    # --- 筛选 ---
    max_price: float = 150.0
    max_issue_size: float = 30000.0   # 3 亿（偏宽松）
    min_issue_size: float = 0.0
    min_turnover: float = 10.0
    min_days_to_maturity: int = 60
    exclude_st: bool = True
    exclude_called: bool = True
    max_premium: float = 80.0

    # --- 反转因子 ---
    reversal_lookback: int = 5        # 反转回望天数
    warmup_bars: int = 10             # 预热期
    size_weight: float = 0.30         # 小规模权重（0=纯反转，1=纯小盘）
    reversal_weight: float = 0.70     # 反转权重

    # --- 风控 ---
    stop_loss_pct: float = 0.12
    min_positions: int = 3
    max_single_drop: float = -0.20    # 跌幅超过此值不参与反转（避免踩雷）

    lot_size: int = 10


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_reversal_small")
class CBReversalSmallStrategy(CBRotationStrategy):
    """可转债短期反转 + 小盘策略。

    买入近 N 日跌幅最大的小规模转债，捕捉过度反应后的均值回归。
    """

    name = "cb_reversal_small"

    def __init__(
        self,
        config: CBReversalSmallConfig | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__()
        self._config = config or CBReversalSmallConfig(**kwargs)
        self._price_history: dict[str, deque[float]] = {}

    @property
    def config(self) -> CBReversalSmallConfig:
        return self._config

    # ------------------------------------------------------------------
    # 生命周期钩子
    # ------------------------------------------------------------------

    def _on_start_extra(self) -> None:
        self._price_history.clear()

    def _on_bar_update_state(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> None:
        """每 bar 追加收盘价到历史队列。"""
        max_len = self._config.reversal_lookback + 5
        for symbol, bar in bars.items():
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=max_len)
            self._price_history[symbol].append(bar.close)

    def _should_rebalance(self) -> bool:
        """预热期后按 rebalance_days 轮动。"""
        cfg = self._config
        effective = self._bar_count - cfg.warmup_bars
        if effective < 1:
            return False
        return (effective - 1) % cfg.rebalance_days == 0

    # ------------------------------------------------------------------
    # 筛选
    # ------------------------------------------------------------------

    def _extra_filter(
        self,
        candidate: CandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """溢价率上限 + 需要足够的价格历史。"""
        cfg = self._config

        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False

        price_hist = self._price_history.get(candidate.symbol)
        if not price_hist or len(price_hist) < cfg.reversal_lookback + 1:
            return False

        # 计算 N 日收益率
        past_price = price_hist[-(cfg.reversal_lookback + 1)]
        if past_price <= 0:
            return False

        ret = (candidate.close - past_price) / past_price

        # 排除极端暴跌（可能是基本面真正变差，非过度反应）
        if ret < cfg.max_single_drop:
            return False

        return True

    # ------------------------------------------------------------------
    # 打分
    # ------------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """反转因子（N 日负收益率）+ 小规模因子复合排名。

        反转分：跌幅越大 rank 越低 → 越优
        规模分：issue_size 越小 rank 越低 → 越优
        """
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []
        if n == 1:
            return [candidates[0].symbol]

        # --- 计算因子值 ---
        factor_data: list[dict] = []
        for c in candidates:
            price_hist = self._price_history[c.symbol]
            past_price = price_hist[-(cfg.reversal_lookback + 1)]
            ret = (c.close - past_price) / past_price if past_price > 0 else 0.0

            factor_data.append({
                "symbol": c.symbol,
                "return": ret,             # 越小（负）越优
                "size": c.issue_size,      # 越小越优
            })

        # --- 排名归一化 — 委托 rank_normalize_list (§13.2) ---
        # 反转：收益率升序（最负的 rank=0 → 最优）
        ret_scores = rank_normalize_list(
            [factor_data[i]["return"] for i in range(n)],
            ascending=True, handle_ties=False,
        )
        for i, s in enumerate(ret_scores):
            factor_data[i]["ret_rank"] = s

        # 规模：升序（最小的 rank=0 → 最优）
        size_scores = rank_normalize_list(
            [factor_data[i]["size"] for i in range(n)],
            ascending=True, handle_ties=False,
        )
        for i, s in enumerate(size_scores):
            factor_data[i]["size_rank"] = s

        # --- 加权合并 ---
        w_total = cfg.reversal_weight + cfg.size_weight
        for d in factor_data:
            d["score"] = (
                cfg.reversal_weight * d["ret_rank"]
                + cfg.size_weight * d["size_rank"]
            ) / w_total

        # 按 score 升序（越低越优）
        factor_data.sort(key=lambda x: x["score"])

        top = cfg.top_k + cfg.buffer_k
        result = [d["symbol"] for d in factor_data[:top]]

        if logger.isEnabledFor(logging.DEBUG) and factor_data:
            best = factor_data[0]
            logger.debug(
                f"[{self.name}] 最优: {best['symbol']} "
                f"score={best['score']:.4f} "
                f"ret={best['return']:.4f} ret_r={best.get('ret_rank', 0):.2f} "
                f"size={best['size']:.0f} size_r={best.get('size_rank', 0):.2f}"
            )

        return result
