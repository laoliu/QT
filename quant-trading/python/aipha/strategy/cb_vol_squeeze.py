"""可转债波动压缩策略 (Volatility Squeeze)

灵感来源：国金证券 2025 年《可转债量化择时、轮动、选券策略》——期权策略。
核心因子：振幅差（Amplitude Gap）的 CB-only 版本。

Alpha 原理：
    当可转债的近期振幅（amplitude = (high-low)/close）显著低于其
    历史振幅均值时，说明波动率受到压缩（类似 Bollinger Band Squeeze），
    隐含期权价值被低估。波动率压缩后大概率会迎来扩张，
    此时以低价+低溢价率筛选，可捕获"波动率回归+估值修复"双重收益。

因子矩阵：
1. **振幅压缩比 (amplitude_squeeze)**: 近期振幅 / 长期振幅
   → 越低说明波动压缩越严重 → 期望反弹越大（反向因子）
2. **价格因子 (close)**: 低价优先（反向因子）
3. **溢价率因子 (cb_over_rate)**: 低溢价优先（反向因子）
4. **规模因子 (issue_size)**: 中小规模优先（反向因子）
5. **评级因子 (rating)**: 高评级优先（正向因子）

参考：
- 国金证券: 期权策略 AR=14%, MDD<10%, SR/Calmar>1.5
- 与现有策略差异化：现有39策略中无振幅/波动压缩因子
"""

from __future__ import annotations

import logging
import math
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
class CBVolSqueezeConfig(CBRotationConfig):
    """波动压缩策略配置。"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 135.0
    min_turnover: float = 20.0          # 万元
    min_issue_size: float = 0.0         # 万元
    max_issue_size: float = float("inf")  # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10         # 与 triple_low_v3 一致
    buffer_k: int = 2
    force_redeem_threshold: float = 130.0
    force_redeem_days: int = 8
    exclude_called: bool = True

    # --- 信用筛选 ---
    min_rating: str = ""                # 不按评级筛选（评级在评分中处理）
    exclude_unrated: bool = False

    # --- 溢价率上限 ---
    max_premium: float = 40.0           # 筛除高溢价债

    # --- 振幅参数 ---
    short_window: int = 10              # 近期振幅窗口（交易日）
    long_window: int = 60               # 长期振幅窗口（交易日）
    min_history: int = 20               # 最少历史数据量

    # --- 因子权重（自动归一化）---
    w_amplitude: float = 0.15           # 振幅压缩比（辅助因子）
    w_price: float = 0.35               # 价格（主因子）
    w_premium: float = 0.30             # 溢价率（主因子）
    w_size: float = 0.05                # 规模
    w_rating: float = 0.15              # 评级


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


@StrategyRegistry().register("cb_vol_squeeze")
class CBVolSqueezeStrategy(CBRotationStrategy):
    """可转债波动压缩策略。

    选取振幅压缩最严重（近/长振幅比最低）+低估值的转债，
    期望捕获波动率扩张+估值回归的双重收益。
    """

    name = "cb_vol_squeeze"

    def __init__(self, config: CBVolSqueezeConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBVolSqueezeConfig(**kwargs)
        # 历史振幅记录 {symbol: deque[(amplitude, close)]}
        self._amplitude_history: dict[str, deque] = {}

    @property
    def config(self) -> CBVolSqueezeConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._amplitude_history.clear()

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """溢价率上限过滤（与 triple_low_v3 一致）。"""
        cfg = self._config
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False
        return True

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """每 bar 记录振幅 = (high - low) / close。"""
        cfg = self._config
        maxlen = cfg.long_window + 10  # 留余量
        for symbol, bar in bars.items():
            if bar.close <= 0 or bar.high <= 0 or bar.low <= 0:
                continue
            amplitude = (bar.high - bar.low) / bar.close
            if symbol not in self._amplitude_history:
                self._amplitude_history[symbol] = deque(maxlen=maxlen)
            self._amplitude_history[symbol].append(amplitude)

    def _should_rebalance(self) -> bool:
        cfg = self._config
        if self._bar_count < cfg.min_history:
            return False
        return (self._bar_count - 1) % cfg.rebalance_days == 0

    def _compute_squeeze_ratio(self, symbol: str) -> float | None:
        """计算振幅压缩比 = 近期avg振幅 / 长期avg振幅。

        Returns:
            ratio in (0, +inf); 越低表示压缩越严重。None if insufficient data.
        """
        cfg = self._config
        hist = self._amplitude_history.get(symbol)
        if hist is None or len(hist) < cfg.min_history:
            return None

        amps = list(hist)
        n = len(amps)

        # 短期窗口
        short_n = min(cfg.short_window, n)
        short_avg = sum(amps[-short_n:]) / short_n

        # 长期窗口
        long_n = min(cfg.long_window, n)
        long_avg = sum(amps[-long_n:]) / long_n

        if long_avg < 1e-8:
            return None

        return short_avg / long_avg

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """多因子排名：振幅压缩比 + 价格 + 溢价率 + 规模 + 评级。"""
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []

        # --- 采集数据 ---
        data: list[dict] = []
        for c in candidates:
            squeeze = self._compute_squeeze_ratio(c.symbol)
            # 数据不足时使用中性值（1.0 = 不压缩也不扩张）
            if squeeze is None:
                squeeze = 1.0
            premium = c.cb_over_rate if c.cb_over_rate is not None else 50.0
            rating_num = _RATING_ORDER.get(c.rating, 30)
            data.append({
                "symbol": c.symbol,
                "squeeze": squeeze,
                "close": c.close,
                "premium": premium,
                "issue_size": c.issue_size,
                "rating": rating_num,
            })

        n = len(data)
        if n < 2:
            return [d["symbol"] for d in data[:cfg.top_k]]

        # --- 排名归一化 — 委托 rank_normalize_list (§13.2) ---
        # 反向因子（越小越好, rank 0=最优）
        for key in ("squeeze", "close", "premium", "issue_size"):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=True, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # 正向因子（越大越好, rank 0=最优）
        for key in ("rating",):
            scores = rank_normalize_list(
                [data[i][key] for i in range(n)], ascending=False, handle_ties=False,
            )
            for i, s in enumerate(scores):
                data[i][f"{key}_rank"] = s

        # --- 加权综合得分 ---
        total_w = (
            cfg.w_amplitude + cfg.w_price + cfg.w_premium
            + cfg.w_size + cfg.w_rating
        )
        if total_w < 1e-8:
            total_w = 1.0

        for d in data:
            d["score"] = (
                cfg.w_amplitude * d["squeeze_rank"]
                + cfg.w_price * d["close_rank"]
                + cfg.w_premium * d["premium_rank"]
                + cfg.w_size * d["issue_size_rank"]
                + cfg.w_rating * d["rating_rank"]
            ) / total_w

        # 分数越低越好（rank 0 = 最优）
        data.sort(key=lambda x: x["score"])
        return [d["symbol"] for d in data[:cfg.top_k]]
