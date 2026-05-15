"""三低策略 — 价格 + 溢价率 + 转债价值三维排序"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationStrategy,
    CBRotationConfig,
    CandidateInfo,
)

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class TripleLowConfig:
    top_k: int = 10
    rebalance_days: int = 3
    max_price: float = 130.0
    min_price: float = 0.0
    max_premium: float = 40.0
    min_issue_size: float = 0.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    price_weight: float = 0.33
    premium_weight: float = 0.33
    cb_value_weight: float = 0.34
    low_size_threshold: float = 30000.0
    low_size_bonus: float = 0.1
    stop_loss_pct: float = 0.10
    trailing_stop_pct: float = 0.0
    force_redeem_threshold: float = 130.0
    force_redeem_days: int = 8
    exclude_called: bool = True
    exclude_st: bool = True
    exclude_force_redeem_pending: bool = False
    buffer_k: int = 5
    lot_size: int = 10
    equal_weight: bool = True
    # base class compatibility
    min_rating: str = ""
    exclude_unrated: bool = False
    min_positions: int = 5
    min_candidates_to_trade: int = 0
    fallback_max_issue_size: float = 0.0
    fallback_max_premium: float = 0.0
    hold_on_empty: bool = True


@dataclass
class TripleLowScore:
    ts_code: str
    name: str
    price: float
    premium_rate: float
    cb_value: float
    issue_size: float
    price_score: float = 0.0
    premium_score: float = 0.0
    cb_value_score: float = 0.0
    total_score: float = 0.0


def calculate_normalized_scores(
    bonds: list[dict],
    price_weight: float = 0.33,
    premium_weight: float = 0.33,
    cb_value_weight: float = 0.34,
    low_size_threshold: float = 30000.0,
    low_size_bonus: float = 0.1,
) -> list[TripleLowScore]:
    """对 bonds 列表进行三维标准化评分"""
    if not bonds:
        return []
    prices = [b["price"] for b in bonds]
    premiums = [b["premium_rate"] for b in bonds]
    cb_values = [b.get("cb_value", 100.0) for b in bonds]

    p_min, p_max = min(prices), max(prices)
    pr_min, pr_max = min(premiums), max(premiums)
    cv_min, cv_max = min(cb_values), max(cb_values)
    p_range = p_max - p_min or 1.0
    pr_range = pr_max - pr_min or 1.0
    cv_range = cv_max - cv_min or 1.0

    results: list[TripleLowScore] = []
    for b in bonds:
        ps = (b["price"] - p_min) / p_range
        prs = (b["premium_rate"] - pr_min) / pr_range
        cvs = (b.get("cb_value", 100.0) - cv_min) / cv_range
        total = price_weight * ps + premium_weight * prs + cb_value_weight * cvs
        # 小规模减分(更优)
        size = b.get("issue_size", b.get("remain_size", float("inf")))
        if low_size_bonus > 0 and size < low_size_threshold:
            total -= low_size_bonus
        results.append(TripleLowScore(
            ts_code=b["ts_code"], name=b.get("name", ""),
            price=b["price"], premium_rate=b["premium_rate"],
            cb_value=b.get("cb_value", 100.0),
            issue_size=b.get("issue_size", b.get("remain_size", 0.0)),
            price_score=ps, premium_score=prs, cb_value_score=cvs,
            total_score=total,
        ))
    results.sort(key=lambda x: x.total_score)
    return results


class TripleLowStrategy(CBRotationStrategy):
    """三低策略 — 价格 + 溢价率 + 转债价值"""
    name = "triple_low"

    def __init__(self, config: TripleLowConfig | None = None, **kwargs: object):
        cfg = config or TripleLowConfig()
        # Apply kwargs to TripleLowConfig
        for k, v in kwargs.items():
            if hasattr(cfg, k):
                object.__setattr__(cfg, k, v)
        # 将 TripleLowConfig 转换为 CBRotationConfig 的字段供父类使用
        cb_cfg = CBRotationConfig(
            top_k=cfg.top_k, rebalance_days=cfg.rebalance_days,
            max_price=cfg.max_price, stop_loss_pct=cfg.stop_loss_pct,
            buffer_k=cfg.buffer_k,
        )
        super().__init__(config=cb_cfg)
        self._tl_config = cfg
        self._force_redeem_counts: dict[str, int] = {}
        self._positions: dict[str, int] = {}
        self._last_rebalance_date: date | None = None

    @property
    def config(self) -> TripleLowConfig:
        return self._tl_config

    @property
    def _force_redeem_counter(self) -> dict[str, int]:
        return self._force_redeem_counts

    @_force_redeem_counter.setter
    def _force_redeem_counter(self, value):
        self._force_redeem_counts = value

    @property
    def tl_config(self) -> TripleLowConfig:
        return self._tl_config

    def on_start(self) -> None:
        super().on_start()
        self._force_redeem_counts.clear()
        self._positions.clear()

    def _check_stop_loss(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        cfg = self._tl_config
        positions = context.positions if context.positions else self._positions
        for code, qty in list(positions.items()):
            if qty <= 0 or code not in bars:
                continue
            bar = bars[code]
            entry = self._entry_prices.get(code)
            if entry and entry > 0:
                pnl = (bar.low - entry) / entry
                if pnl < -cfg.stop_loss_pct:
                    intents.append(OrderIntent(
                        symbol=code, intent_type=IntentType.EXIT,
                        side=Side.SELL, qty=qty,
                        reason=f"止损 {pnl:.2%} low={bar.low:.2f} entry={entry:.2f}",
                    ))
        return intents

    def _update_force_redeem_counter(self, bars: dict[str, "Bar"]) -> None:
        cfg = self._tl_config
        for code, bar in bars.items():
            meta = getattr(bar, "metadata", None) or {}
            cb_value = meta.get("cb_value", None)
            if cb_value is not None and cb_value > cfg.force_redeem_threshold:
                self._force_redeem_counts[code] = self._force_redeem_counts.get(code, 0) + 1
            else:
                self._force_redeem_counts[code] = 0

    def _should_rebalance(self) -> bool:  # type: ignore[override]
        return (self._bar_count - self._last_rebalance_bar) >= self._tl_config.rebalance_days

    def _filter_bonds(self, bars: dict[str, "Bar"], current_date: date) -> list[dict]:
        cfg = self._tl_config
        result: list[dict] = []
        for code, bar in bars.items():
            meta = getattr(bar, "metadata", None) or {}
            if not meta:
                continue
            if bar.close < cfg.min_price or bar.close > cfg.max_price:
                continue
            cb_over_rate = meta.get("cb_over_rate", 0.0) or 0.0
            if cb_over_rate > cfg.max_premium:
                continue
            amount = meta.get("amount", 0.0) or 0.0
            if amount < cfg.min_turnover:
                continue
            # ST 过滤
            stk_name = meta.get("stk_short_name", "")
            if cfg.exclude_st and stk_name and "ST" in stk_name.upper():
                continue
            # 到期日过滤
            mat_date = meta.get("maturity_date", "")
            if mat_date and cfg.min_days_to_maturity > 0:
                try:
                    fmt = "%Y-%m-%d" if "-" in mat_date else "%Y%m%d"
                    md = datetime.strptime(mat_date[:10], fmt).date()
                    if (md - current_date).days < cfg.min_days_to_maturity:
                        continue
                except (ValueError, TypeError):
                    pass
            # 已强赎
            if cfg.exclude_called and meta.get("is_called", False):
                continue
            # 强赎预警计数
            if cfg.exclude_called and self._force_redeem_counts.get(code, 0) >= cfg.force_redeem_days:
                continue
            cb_value = meta.get("cb_value", 100.0) or 100.0
            result.append({
                "ts_code": code,
                "name": meta.get("stk_short_name", code),
                "price": bar.close,
                "premium_rate": cb_over_rate,
                "cb_value": cb_value,
                "issue_size": meta.get("remain_size", 0.0) or 0.0,
            })
        return result

    def _select_targets(
        self, scores: list[TripleLowScore],
        current_positions: dict[str, int] | None = None,
    ) -> set[str]:
        cfg = self._tl_config
        return {s.ts_code for s in scores[:cfg.top_k]}

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self._tl_config
        bonds = []
        for c in candidates:
            bonds.append({
                "ts_code": c.symbol, "name": getattr(c, "stk_short_name", c.symbol),
                "price": c.close,
                "premium_rate": (c.cb_over_rate or 0.0),
                "cb_value": (c.cb_value or 100.0),
                "issue_size": (c.issue_size or 0.0),
            })
        scores = calculate_normalized_scores(
            bonds, cfg.price_weight, cfg.premium_weight, cfg.cb_value_weight,
            cfg.low_size_threshold, cfg.low_size_bonus,
        )
        return [s.ts_code for s in scores[:cfg.top_k]]

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        cur = self._positions.get(symbol, 0)
        if side == Side.BUY:
            self._positions[symbol] = cur + qty
        else:
            self._positions[symbol] = max(0, cur - qty)

    def get_current_targets(self) -> set[str]:
        return {k for k, v in self._positions.items() if v > 0}

    def _generate_rebalance_intents(
        self,
        target_symbols: set[str],
        scores: dict[str, TripleLowScore],
        current_positions: dict[str, int],
        cash: float,
        bars: dict[str, "Bar"],
        nav: float,
    ) -> list[OrderIntent]:
        cfg = self._tl_config
        intents: list[OrderIntent] = []
        current = set(current_positions.keys())

        # SELL positions not in targets
        for sym in current - target_symbols:
            qty = current_positions.get(sym, 0)
            if qty > 0:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.EXIT,
                    side=Side.SELL, qty=qty, reason="轮出",
                ))

        # BUY targets not in positions
        n_targets = len(target_symbols - current)
        if n_targets > 0:
            per_target = nav / max(len(target_symbols), 1)
            for sym in target_symbols - current:
                if sym in bars:
                    price = bars[sym].close
                    if price > 0:
                        raw_qty = int(per_target / price)
                        qty = (raw_qty // cfg.lot_size) * cfg.lot_size
                        if qty > 0:
                            intents.append(OrderIntent(
                                symbol=sym, intent_type=IntentType.ENTRY,
                                side=Side.BUY, qty=qty, reason="轮入",
                            ))
        return intents
