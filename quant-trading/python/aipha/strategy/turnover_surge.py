"""可转债放量突破信号策略

Alpha 来源：
1. 成交额突然放大（相对于近期均值）预示机构关注或事件催化
2. 放量 + 低溢价率 = 有方向的资金进场（非噪声交易）
3. 转债市场流动性较差，量能异动的信号值比股票市场更高

策略逻辑：
1. 维护每只转债的近期成交额历史（deque）
2. 计算 surge_ratio = 当日成交额 / MA(成交额, lookback_days)
3. 筛选 surge_ratio > min_surge_ratio 的放量标的
4. 叠加溢价率/价格过滤（防追高垃圾债）
5. 按放量倍数降序取 top_k

风险：放量可能是主力出货（上涨放量 vs 下跌放量需区分），
     通过叠加 max_price/max_premium 过滤降低风险。
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

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class TurnoverSurgeConfig(CBRotationConfig):
    """放量突破策略配置"""

    top_k: int = 10
    lookback_days: int = 20      # 成交额均值回看窗口
    rebalance_days: int = 3

    # 放量阈值
    min_surge_ratio: float = 2.0  # 至少是近期均值的 N 倍

    # 筛选
    max_price: float = 140.0
    max_premium: float = 40.0
    min_turnover: float = 30.0    # 绝对成交额下限
    min_days_to_maturity: int = 30
    exclude_st: bool = True

    # 要求价格相对前一日上涨（涨放量而非跌放量）
    require_positive_return: bool = True

    # 风控
    stop_loss_pct: float = 0.10
    lot_size: int = 10


@StrategyRegistry().register("turnover_surge")
class TurnoverSurgeStrategy(CBRotationStrategy):
    """放量突破信号策略

    捕捉成交额异常放大的转债，视为催化剂/机构进场信号。
    放量倍数越大 → 信号越强。叠加方向性过滤减少噪声。
    使用 issue_size（静态字段）替代 remain_size，消除前视偏差。
    """

    name = "turnover_surge"

    def __init__(self, config: TurnoverSurgeConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or TurnoverSurgeConfig(**kwargs)
        # 成交额历史
        self._amount_history: dict[str, deque[float]] = {}
        # 前日收盘价（用于方向过滤）
        self._prev_close: dict[str, float] = {}

    @property
    def config(self) -> TurnoverSurgeConfig:
        """策略配置。"""
        return self._config

    def _on_start_extra(self) -> None:
        self._amount_history.clear()
        self._prev_close.clear()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """每 bar 更新成交额历史（用于放量计算）。"""
        max_len = self._config.lookback_days + 1
        for symbol, bar in bars.items():
            amt = (bar.metadata or {}).get("amount", 0)
            if symbol not in self._amount_history:
                self._amount_history[symbol] = deque(maxlen=max_len)
            self._amount_history[symbol].append(amt)

    def on_bar(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list:
        """包装基类 on_bar，在最后更新 prev_close。"""
        intents = super().on_bar(bars, context)
        # prev_close 需要在所有过滤/评分完成后再更新
        for symbol, bar in bars.items():
            self._prev_close[symbol] = bar.close
        return intents

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率 + 方向 + 放量倍数过滤。"""
        cfg = self._config

        # 溢价率上限
        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False

        # 方向过滤：要求上涨放量
        if cfg.require_positive_return:
            prev = self._prev_close.get(candidate.symbol)
            if prev and candidate.close <= prev:
                return False

        # 放量倍数计算
        hist = self._amount_history.get(candidate.symbol)
        if not hist or len(hist) < cfg.lookback_days + 1:
            return False
        # 排除当前 bar（已在 _on_bar_update_state 中追加）
        history_amounts = list(hist)[:-1]
        if len(history_amounts) < cfg.lookback_days:
            return False
        avg_amt = sum(history_amounts) / len(history_amounts)
        if avg_amt <= 0:
            return False
        surge_ratio = candidate.amount / avg_amt
        if surge_ratio < cfg.min_surge_ratio:
            return False

        # 存入 metadata 供 _score_candidates 使用
        candidate.metadata["_surge_ratio"] = surge_ratio
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按放量倍数降序（最强信号优先）。"""
        candidates.sort(key=lambda c: (-c.metadata.get("_surge_ratio", 0), c.symbol))
        return [c.symbol for c in candidates[: self._config.top_k]]
