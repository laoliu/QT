"""可转债轮动基类 — 所有 CB 轮动策略的公共框架"""
from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

_RATING_ORDER: dict[str, int] = {
    "AAA": 100, "AA+": 90, "AA": 80, "AA-": 70,
    "A+": 60, "A": 50, "A-": 40,
    "BBB+": 35, "BBB": 30, "BBB-": 25,
    "BB+": 20, "BB": 15, "BB-": 10,
    "B+": 8, "B": 6, "B-": 4,
    "CCC": 3, "CC": 2, "C": 1, "D": 0,
}


def rating_score(rating: str) -> int:
    return _RATING_ORDER.get(rating.strip().upper(), 0)


def days_to_maturity(maturity_str: str, now: datetime) -> int:
    try:
        mat = datetime.strptime(maturity_str[:10], "%Y-%m-%d") if "-" in maturity_str \
            else datetime.strptime(maturity_str[:8], "%Y%m%d")
        return (mat - now).days
    except (ValueError, TypeError):
        return 9999


def days_since_listed(list_date_str: str, now: datetime) -> int:
    try:
        ld = datetime.strptime(list_date_str[:10], "%Y-%m-%d") if "-" in list_date_str \
            else datetime.strptime(list_date_str[:8], "%Y%m%d")
        return (now - ld).days
    except (ValueError, TypeError):
        return 0


@dataclass(slots=True)
class CandidateInfo:
    symbol: str
    close: float
    metadata: dict[str, Any]
    amount: float = 0.0
    issue_size: float = float("inf")
    cb_over_rate: float | None = None
    cb_value: float | None = None
    conv_price: float | None = None
    maturity_date: str = ""
    list_date: str = ""
    stk_short_name: str = ""
    rating: str = ""


@dataclass
class CBRotationConfig:
    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    min_turnover: float = 50.0
    min_issue_size: float = 0.0
    max_issue_size: float = float("inf")
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    min_rating: str = ""
    exclude_unrated: bool = False
    force_redeem_threshold: float = 130.0
    force_redeem_days: int = 8
    exclude_called: bool = True
    exclude_force_redeem_pending: bool = False
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0
    min_positions: int = 5
    min_candidates_to_trade: int = 0
    buffer_k: int = 0
    fallback_max_issue_size: float = 0.0
    fallback_max_premium: float = 0.0
    hold_on_empty: bool = True
    lot_size: int = 10


class CBRotationStrategy(Strategy):
    """可转债轮动策略抽象基类"""

    @property
    @abstractmethod
    def config(self) -> CBRotationConfig: ...

    @abstractmethod
    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """打分并返回最终选中的 symbol 列表 (已排序)"""
        ...

    def __init__(self, config: CBRotationConfig | None = None, **kwargs: object) -> None:
        if config is not None:
            self._config = config
        self._bar_count = 0
        self._last_rebalance_bar = 0
        self._entry_prices: dict[str, float] = {}
        self._peak_prices: dict[str, float] = {}

    def on_start(self) -> None:
        self._on_start_extra()

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        if side == Side.BUY:
            self._entry_prices[symbol] = price
            self._peak_prices[symbol] = price
        elif side == Side.SELL and qty <= 0:
            self._entry_prices.pop(symbol, None)
            self._peak_prices.pop(symbol, None)

    def on_bar(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        self._on_bar_update_state(bars, context)

        # 更新 peak prices
        for sym, bar in bars.items():
            if sym in self._peak_prices:
                self._peak_prices[sym] = max(self._peak_prices[sym], bar.close)

        intents: list[OrderIntent] = []
        intents.extend(self._check_stop_loss(bars, context))
        intents.extend(self._check_force_redeem(bars, context))
        intents.extend(self._custom_exit_check(bars, context))

        if self._should_rebalance():
            candidates = self._filter_with_fallback(bars, context)
            if candidates or not self.config.hold_on_empty:
                ranked = self._score_candidates(candidates, context)
                intents.extend(self._generate_rebalance(ranked, context, bars))
            self._last_rebalance_bar = self._bar_count

        return intents

    def _should_rebalance(self) -> bool:
        return (self._bar_count - self._last_rebalance_bar) >= self.config.rebalance_days

    def _check_stop_loss(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        cfg = self.config
        for sym in list(context.positions.keys()):
            bar = bars.get(sym)
            if bar is None:
                continue
            pos = context.positions.get(sym, 0)
            if pos <= 0:
                continue
            entry = self._entry_prices.get(sym)
            if entry and entry > 0:
                pnl = (bar.close - entry) / entry
                if pnl < -cfg.stop_loss_pct:
                    intents.append(OrderIntent(
                        symbol=sym, intent_type=IntentType.ORDER,
                        side=Side.SELL, qty=pos, reason=f"stop_loss {pnl:.2%}",
                    ))
            if cfg.trailing_stop_pct > 0:
                peak = self._peak_prices.get(sym, bar.close)
                drop = (peak - bar.close) / peak if peak > 0 else 0
                if drop > cfg.trailing_stop_pct:
                    intents.append(OrderIntent(
                        symbol=sym, intent_type=IntentType.ORDER,
                        side=Side.SELL, qty=pos, reason=f"trailing_stop {drop:.2%}",
                    ))
        return intents

    def _check_force_redeem(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        if not self.config.exclude_called:
            return []
        intents: list[OrderIntent] = []
        for sym in list(context.positions.keys()):
            bar = bars.get(sym)
            if bar and bar.close >= self.config.force_redeem_threshold:
                meta = getattr(bar, "metadata", {}) or {}
                if meta.get("force_redeem_triggered"):
                    pos = context.positions.get(sym, 0)
                    if pos > 0:
                        intents.append(OrderIntent(
                            symbol=sym, intent_type=IntentType.ORDER,
                            side=Side.SELL, qty=pos, reason="force_redeem",
                        ))
        return intents

    def _filter_candidates(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> list[CandidateInfo]:
        cfg = self.config
        now = datetime.now()
        candidates: list[CandidateInfo] = []
        for sym, bar in bars.items():
            meta = getattr(bar, "metadata", {}) or {}
            ci = CandidateInfo(
                symbol=sym, close=bar.close, metadata=meta,
                amount=getattr(bar, "amount", meta.get("amount", 0)),
                issue_size=meta.get("issue_size", float("inf")),
                cb_over_rate=meta.get("cb_over_rate"),
                cb_value=meta.get("cb_value"),
                conv_price=meta.get("conv_price"),
                maturity_date=meta.get("maturity_date", ""),
                list_date=meta.get("list_date", ""),
                stk_short_name=meta.get("stk_short_name", ""),
                rating=meta.get("rating", ""),
            )
            # 基本过滤
            if ci.close > cfg.max_price:
                continue
            if ci.amount < cfg.min_turnover:
                continue
            if ci.issue_size < cfg.min_issue_size or ci.issue_size > cfg.max_issue_size:
                continue
            mat_days = days_to_maturity(ci.maturity_date, now)
            if mat_days < cfg.min_days_to_maturity:
                continue
            if cfg.exclude_st and "ST" in ci.stk_short_name.upper():
                continue
            if cfg.min_rating and rating_score(ci.rating) < rating_score(cfg.min_rating):
                continue
            if cfg.exclude_unrated and not ci.rating:
                continue
            if not self._extra_filter(ci, context):
                continue
            candidates.append(ci)
        return candidates

    def _filter_with_fallback(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> list[CandidateInfo]:
        candidates = self._filter_candidates(bars, context)
        if len(candidates) < self.config.min_positions and self.config.fallback_max_issue_size > 0:
            self._relax_filters_one_step()
            candidates = self._filter_candidates(bars, context)
            self._maybe_tighten_filters()
        return candidates

    def _relax_filters_one_step(self) -> bool:
        return False

    def _maybe_tighten_filters(self) -> None:
        pass

    def _generate_rebalance(
        self, ranked_targets: list[str], context: StrategyContext,
        bars: dict[str, "Bar"],
    ) -> list[OrderIntent]:
        cfg = self.config
        targets = ranked_targets[:cfg.top_k]
        target_set = set(targets)
        current = set(context.positions.keys())
        buffer = set(ranked_targets[:cfg.top_k + cfg.buffer_k])
        lot = 10  # 可转债 10 张 1 手

        intents: list[OrderIntent] = []

        # 1) 先卖出不在 buffer 中的持仓
        for sym in current - buffer:
            pos = context.positions.get(sym, 0)
            if pos > 0:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.ORDER,
                    side=Side.SELL, qty=pos, reason="rotation_out",
                ))

        # 2) 基于 NAV 等权计算每只目标仓位
        n_targets = max(len(targets), 1)
        target_value_per = context.nav * 0.95 / n_targets

        for sym in targets:
            bar = bars.get(sym)
            if not bar or bar.close <= 0:
                continue
            target_qty = int(target_value_per / bar.close / lot) * lot
            current_qty = context.positions.get(sym, 0)
            diff = target_qty - current_qty
            if diff >= lot:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.ORDER,
                    side=Side.BUY, qty=diff, reason="rotation_in",
                ))
                if sym not in self._entry_prices:
                    self._entry_prices[sym] = bar.close
                    self._peak_prices[sym] = bar.close
            elif diff <= -lot:
                sell_qty = (abs(diff) // lot) * lot
                if sell_qty >= lot:
                    intents.append(OrderIntent(
                        symbol=sym, intent_type=IntentType.ORDER,
                        side=Side.SELL, qty=sell_qty, reason="rebalance_trim",
                    ))
        return intents

    # ── 子类可覆盖的钩子 ──────────────────────────────────
    def _on_start_extra(self) -> None:
        pass

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        pass

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        return True

    def _custom_exit_check(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        return []
