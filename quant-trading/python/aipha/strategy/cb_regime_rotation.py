"""可转债市场状态轮动策略

Alpha 来源：
- 不同市场环境下，不同风格因子表现差异显著
- 趋势上行期：动量/放量因子占优
- 震荡/下行期：反转/低价/小盘因子占优
- 通过检测市场状态动态切换因子权重，实现全天候 alpha

市场状态检测：
- 使用全市场 CB 等权收益率的 N 日滚动指标
- BULL: 20 日等权涨幅 > bull_threshold (趋势向上)
- BEAR: 20 日等权涨幅 < bear_threshold (趋势向下)
- NEUTRAL: 介于两者之间 (震荡)

各状态下的因子权重:
- BULL: 偏重 momentum + surge（追热度）
- NEUTRAL: 偏重 size + premium（选安全边际）
- BEAR: 偏重 reversal + price（抄底反弹）
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
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


# ---------------------------------------------------------------------------
# 市场状态枚举
# ---------------------------------------------------------------------------

class MarketRegime(Enum):
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBRegimeRotationConfig(CBRotationConfig):
    """市场状态轮动策略配置。"""

    # --- 持仓 ---
    top_k: int = 8
    rebalance_days: int = 1
    buffer_k: int = 3

    # --- 筛选 ---
    max_price: float = 150.0
    max_issue_size: float = 30000.0
    min_issue_size: float = 0.0
    min_turnover: float = 10.0
    min_days_to_maturity: int = 60
    exclude_st: bool = True
    exclude_called: bool = True
    max_premium: float = 80.0

    # --- 市场状态检测 ---
    regime_lookback: int = 20          # 状态检测回望天数
    bull_threshold: float = 0.03       # 20 日等权涨幅 > 3% = 牛市
    bear_threshold: float = -0.02      # 20 日等权涨幅 < -2% = 熊市

    # --- 动态因子参数 ---
    momentum_lookback: int = 10
    surge_lookback: int = 20
    reversal_lookback: int = 5
    warmup_bars: int = 25

    # --- BULL 状态因子权重（偏进攻）---
    bull_w_size: float = 0.10
    bull_w_momentum: float = 0.35
    bull_w_surge: float = 0.30
    bull_w_premium: float = 0.10
    bull_w_price: float = 0.05
    bull_w_reversal: float = 0.10

    # --- NEUTRAL 状态因子权重（偏均衡）---
    neutral_w_size: float = 0.25
    neutral_w_momentum: float = 0.15
    neutral_w_surge: float = 0.10
    neutral_w_premium: float = 0.20
    neutral_w_price: float = 0.15
    neutral_w_reversal: float = 0.15

    # --- BEAR 状态因子权重（偏防守+反转）---
    bear_w_size: float = 0.15
    bear_w_momentum: float = 0.00
    bear_w_surge: float = 0.05
    bear_w_premium: float = 0.15
    bear_w_price: float = 0.25
    bear_w_reversal: float = 0.40

    # --- 风控 ---
    stop_loss_pct: float = 0.12
    min_positions: int = 3

    lot_size: int = 10


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_regime_rotation")
class CBRegimeRotationStrategy(CBRotationStrategy):
    """可转债市场状态轮动策略。

    根据全市场 CB 等权指数的近期表现动态调整因子权重，
    实现"牛市追动量、熊市抄反转"的自适应轮动。
    """

    name = "cb_regime_rotation"

    def __init__(
        self,
        config: CBRegimeRotationConfig | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__()
        self._config = config or CBRegimeRotationConfig(**kwargs)
        self._price_history: dict[str, deque[float]] = {}
        self._amount_history: dict[str, deque[float]] = {}
        # 市场等权指数历史（每 bar 计算全市场平均收盘价）
        self._market_index: deque[float] = deque(maxlen=100)
        self._current_regime: MarketRegime = MarketRegime.NEUTRAL

    @property
    def config(self) -> CBRegimeRotationConfig:
        return self._config

    @property
    def current_regime(self) -> MarketRegime:
        """当前检测到的市场状态（供外部查询/日志）。"""
        return self._current_regime

    # ------------------------------------------------------------------
    # 生命周期钩子
    # ------------------------------------------------------------------

    def _on_start_extra(self) -> None:
        self._price_history.clear()
        self._amount_history.clear()
        self._market_index.clear()
        self._current_regime = MarketRegime.NEUTRAL

    def _on_bar_update_state(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> None:
        """更新价格/成交额历史 + 全市场等权指数。"""
        cfg = self._config
        price_maxlen = max(cfg.momentum_lookback, cfg.reversal_lookback) + 10
        amount_maxlen = cfg.surge_lookback + 5

        prices_this_bar: list[float] = []

        for symbol, bar in bars.items():
            # 价格历史
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=price_maxlen)
            self._price_history[symbol].append(bar.close)

            # 成交额历史
            amt = (bar.metadata or {}).get("amount", 0)
            if symbol not in self._amount_history:
                self._amount_history[symbol] = deque(maxlen=amount_maxlen)
            self._amount_history[symbol].append(amt)

            # 收集价格用于等权指数
            if bar.close > 0:
                prices_this_bar.append(bar.close)

        # 更新等权指数
        if prices_this_bar:
            avg_price = sum(prices_this_bar) / len(prices_this_bar)
            self._market_index.append(avg_price)

        # 检测市场状态
        self._detect_regime()

    def _detect_regime(self) -> None:
        """根据等权指数的 regime_lookback 天涨幅检测市场状态。"""
        cfg = self._config
        idx = self._market_index

        if len(idx) < cfg.regime_lookback + 1:
            self._current_regime = MarketRegime.NEUTRAL
            return

        past_val = idx[-(cfg.regime_lookback + 1)]
        current_val = idx[-1]

        if past_val <= 0:
            self._current_regime = MarketRegime.NEUTRAL
            return

        market_return = (current_val - past_val) / past_val

        if market_return > cfg.bull_threshold:
            self._current_regime = MarketRegime.BULL
        elif market_return < cfg.bear_threshold:
            self._current_regime = MarketRegime.BEAR
        else:
            self._current_regime = MarketRegime.NEUTRAL

        logger.info(
            f"[{self.name}] 市场状态={self._current_regime.value} "
            f"market_return={market_return:.4f} "
            f"({cfg.regime_lookback}d)"
        )

    def _should_rebalance(self) -> bool:
        cfg = self._config
        effective = self._bar_count - cfg.warmup_bars
        if effective < 1:
            return False
        return (effective - 1) % cfg.rebalance_days == 0

    def _get_regime_weights(self) -> dict[str, float]:
        """返回当前状态下的因子权重。"""
        cfg = self._config
        regime = self._current_regime

        if regime == MarketRegime.BULL:
            return {
                "size": cfg.bull_w_size,
                "momentum": cfg.bull_w_momentum,
                "surge": cfg.bull_w_surge,
                "premium": cfg.bull_w_premium,
                "price": cfg.bull_w_price,
                "reversal": cfg.bull_w_reversal,
            }
        elif regime == MarketRegime.BEAR:
            return {
                "size": cfg.bear_w_size,
                "momentum": cfg.bear_w_momentum,
                "surge": cfg.bear_w_surge,
                "premium": cfg.bear_w_premium,
                "price": cfg.bear_w_price,
                "reversal": cfg.bear_w_reversal,
            }
        else:
            return {
                "size": cfg.neutral_w_size,
                "momentum": cfg.neutral_w_momentum,
                "surge": cfg.neutral_w_surge,
                "premium": cfg.neutral_w_premium,
                "price": cfg.neutral_w_price,
                "reversal": cfg.neutral_w_reversal,
            }

    # ------------------------------------------------------------------
    # 筛选
    # ------------------------------------------------------------------

    def _extra_filter(
        self,
        candidate: CandidateInfo,
        context: StrategyContext,
    ) -> bool:
        cfg = self._config

        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False

        # 需要价格历史
        price_hist = self._price_history.get(candidate.symbol)
        min_history = max(cfg.momentum_lookback, cfg.reversal_lookback) + 1
        if not price_hist or len(price_hist) < min_history:
            return False

        # 需要成交额历史
        amount_hist = self._amount_history.get(candidate.symbol)
        if not amount_hist or len(amount_hist) < cfg.surge_lookback + 1:
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
        """六因子排名，权重由当前市场状态决定。"""
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []
        if n == 1:
            return [candidates[0].symbol]

        weights = self._get_regime_weights()

        # --- 计算因子值 ---
        factor_data: list[dict] = []
        for c in candidates:
            price_hist = self._price_history[c.symbol]

            # 动量（momentum_lookback 天）
            past_mom = price_hist[-(cfg.momentum_lookback + 1)]
            momentum = (c.close - past_mom) / past_mom if past_mom > 0 else 0.0

            # 反转（reversal_lookback 天，较短）
            past_rev = price_hist[-(cfg.reversal_lookback + 1)]
            reversal_ret = (c.close - past_rev) / past_rev if past_rev > 0 else 0.0

            # 放量比
            amount_hist = self._amount_history[c.symbol]
            history_amounts = list(amount_hist)[-(cfg.surge_lookback + 1):-1]
            avg_amt = sum(history_amounts) / len(history_amounts) if history_amounts else 1.0
            surge_ratio = c.amount / avg_amt if avg_amt > 0 else 0.0

            factor_data.append({
                "symbol": c.symbol,
                "size": c.issue_size,
                "momentum": momentum,
                "surge": surge_ratio,
                "premium": c.cb_over_rate if c.cb_over_rate is not None else 50.0,
                "price": c.close,
                "reversal": reversal_ret,  # 越小（跌幅大）→ 反转信号越强
            })

        # --- 排名归一化 §13.2 委托给共享实现 ---
        from aipha.utils.rank_utils import rank_normalize_list

        def _rank_field(key: str, ascending: bool = True) -> None:
            raw_values = [d[key] for d in factor_data]
            ranks = rank_normalize_list(raw_values, ascending=ascending, handle_ties=False)
            for i, r in enumerate(ranks):
                factor_data[i][f"{key}_rank"] = r

        # 升序因子（值越小 → rank 越低 → 越优）
        _rank_field("size", ascending=True)
        _rank_field("premium", ascending=True)
        _rank_field("price", ascending=True)
        _rank_field("reversal", ascending=True)  # 收益率越负 → 反转信号越强

        # 降序因子（值越大 → rank 越低 → 越优）
        _rank_field("momentum", ascending=False)
        _rank_field("surge", ascending=False)

        # --- 加权合并 ---
        w_total = sum(weights.values()) or 1.0
        for d in factor_data:
            d["score"] = (
                weights["size"] * d["size_rank"]
                + weights["momentum"] * d["momentum_rank"]
                + weights["surge"] * d["surge_rank"]
                + weights["premium"] * d["premium_rank"]
                + weights["price"] * d["price_rank"]
                + weights["reversal"] * d["reversal_rank"]
            ) / w_total

        factor_data.sort(key=lambda x: x["score"])

        top = cfg.top_k + cfg.buffer_k
        result = [d["symbol"] for d in factor_data[:top]]

        if logger.isEnabledFor(logging.DEBUG) and factor_data:
            best = factor_data[0]
            logger.debug(
                f"[{self.name}] regime={self._current_regime.value} "
                f"最优: {best['symbol']} score={best['score']:.4f}"
            )

        return result
