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
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    price_weight: float = 0.33
    premium_weight: float = 0.33
    cb_value_weight: float = 0.34
    low_size_threshold: float = 30000.0
    low_size_bonus: float = 0.1
    stop_loss_pct: float = 0.10
    force_redeem_threshold: float = 130.0
    force_redeem_days: int = 8
    exclude_called: bool = True
    exclude_st: bool = True
    buffer_k: int = 5
    lot_size: int = 10
    equal_weight: bool = True


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
        ps = 1.0 - (b["price"] - p_min) / p_range       # 低价高分
        prs = 1.0 - (b["premium_rate"] - pr_min) / pr_range  # 低溢价高分
        cvs = 1.0 - (b.get("cb_value", 100.0) - cv_min) / cv_range  # 低转债价值高分 (接近债底)
        total = price_weight * ps + premium_weight * prs + cb_value_weight * cvs
        # 小规模加分
        if low_size_bonus > 0 and b.get("issue_size", float("inf")) < low_size_threshold:
            total += low_size_bonus
        results.append(TripleLowScore(
            ts_code=b["ts_code"], name=b.get("name", ""),
            price=b["price"], premium_rate=b["premium_rate"],
            cb_value=b.get("cb_value", 100.0),
            issue_size=b.get("issue_size", 0.0),
            price_score=ps, premium_score=prs, cb_value_score=cvs,
            total_score=total,
        ))
    results.sort(key=lambda x: x.total_score, reverse=True)
    return results


class TripleLowStrategy(CBRotationStrategy):
    """三低策略 — 价格 + 溢价率 + 转债价值"""
    name = "triple_low"

    def __init__(self, config: TripleLowConfig | None = None, **kwargs: object):
        cfg = config or TripleLowConfig()
        # 将 TripleLowConfig 转换为 CBRotationConfig 的字段供父类使用
        cb_cfg = CBRotationConfig(
            top_k=cfg.top_k, rebalance_days=cfg.rebalance_days,
            max_price=cfg.max_price, stop_loss_pct=cfg.stop_loss_pct,
            buffer_k=cfg.buffer_k,
        )
        super().__init__(config=cb_cfg, **kwargs)
        self._tl_config = cfg
        self._force_redeem_counts: dict[str, int] = {}
        self._positions: dict[str, int] = {}
        self._last_rebalance_date: date | None = None

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
        for code, qty in list(self._positions.items()):
            if qty <= 0 or code not in bars:
                continue
            bar = bars[code]
            cost = getattr(context, "avg_cost", {}).get(code)
            if cost and cost > 0:
                ret = (bar.close - cost) / cost
                if ret < -cfg.stop_loss_pct:
                    intents.append(OrderIntent(
                        symbol=code, intent_type=IntentType.EXIT,
                        side=Side.SELL, quantity=qty,
                    ))
        return intents

    def _update_force_redeem_counter(self, bars: dict[str, "Bar"]) -> None:
        cfg = self._tl_config
        for code, bar in bars.items():
            cb_value = getattr(bar, "cb_value", None)
            if cb_value and cb_value > cfg.force_redeem_threshold:
                self._force_redeem_counts[code] = self._force_redeem_counts.get(code, 0) + 1
            else:
                self._force_redeem_counts.pop(code, None)

    def _should_rebalance(self, current_date: date) -> bool:  # type: ignore[override]
        if self._last_rebalance_date is None:
            return True
        return (current_date - self._last_rebalance_date).days >= self._tl_config.rebalance_days

    def _filter_bonds(self, bars: dict[str, "Bar"], current_date: date) -> list[dict]:
        cfg = self._tl_config
        result: list[dict] = []
        for code, bar in bars.items():
            if bar.close < cfg.min_price or bar.close > cfg.max_price:
                continue
            premium = getattr(bar, "premium_rate", None) or 0.0
            if premium * 100 > cfg.max_premium:
                continue
            turnover = getattr(bar, "turnover", None) or 0.0
            if turnover < cfg.min_turnover:
                continue
            # 排除强赎
            if cfg.exclude_called and self._force_redeem_counts.get(code, 0) >= cfg.force_redeem_days:
                continue
            result.append({
                "ts_code": code,
                "name": getattr(bar, "name", code),
                "price": bar.close,
                "premium_rate": premium,
                "cb_value": getattr(bar, "cb_value", 100.0) or 100.0,
                "issue_size": getattr(bar, "issue_size", 0.0) or 0.0,
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
                "ts_code": c.code, "name": getattr(c, "name", c.code),
                "price": c.price,
                "premium_rate": getattr(c, "premium_rate", 0.0) or 0.0,
                "cb_value": getattr(c, "cb_value", 100.0) or 100.0,
                "issue_size": getattr(c, "issue_size", 0.0) or 0.0,
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
