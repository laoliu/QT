"""影子运行器 — 双路径执行比较 (S-1 QEL P0 行动项).

§4.3 回测-仿真-实盘一致性契约要求同一信号在不同执行路径下产生一致结果。
本模块量化 BacktestEngine (Path A) 与 trading_ops (Path B) 的执行差异。

Path A: NextBarOpen 成交 + FeeModelCNEquity (时变印花税 + Decimal)
Path B: 当日 Close 成交 + calc_trade_cost (固定费率 + float)

用法:
    comparator = ShadowComparator.for_cn_equity()
    report = comparator.compare_trades(trades)
    print(report.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from aipha.domain.models import Fill, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TimeVaryingStampTaxModel,
    TransferFeeModel,
)
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.execution.trading_ops import (
    FeeParams,
    apply_slippage,
    calc_trade_cost,
    get_fee_params,
)

logger = logging.getLogger(__name__)


# ── Data Models ──────────────────────────────────────────────────


@dataclass
class TradeEvent:
    """一笔待比较的交易事件."""

    trade_date: date
    symbol: str
    side: str          # "buy" | "sell"
    qty: int
    close_price: float
    next_open_price: float
    prev_close: float | None = None


@dataclass
class PathResult:
    """单条路径的执行结果."""

    fill_price: float
    slipped_price: float
    cost: float
    net_amount: float  # 含费用的净金额


@dataclass
class TradeDivergence:
    """单笔交易的双路径差异."""

    event: TradeEvent
    path_a: PathResult  # BacktestEngine
    path_b: PathResult  # trading_ops
    price_diff_bps: float = 0.0
    cost_diff: float = 0.0
    net_diff: float = 0.0


@dataclass
class ShadowReport:
    """影子运行汇总报告."""

    divergences: list[TradeDivergence] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.divergences)

    @property
    def mean_price_diff_bps(self) -> float:
        if not self.divergences:
            return 0.0
        return sum(d.price_diff_bps for d in self.divergences) / len(self.divergences)

    @property
    def total_cost_diff(self) -> float:
        return sum(d.cost_diff for d in self.divergences)

    @property
    def total_net_diff(self) -> float:
        return sum(d.net_diff for d in self.divergences)

    @property
    def max_price_diff_bps(self) -> float:
        if not self.divergences:
            return 0.0
        return max(abs(d.price_diff_bps) for d in self.divergences)

    def summary(self) -> str:
        """生成人类可读的汇总."""
        lines = [
            "=== Shadow Run Divergence Report ===",
            f"Total trades compared: {self.total_trades}",
            f"Mean fill price diff:  {self.mean_price_diff_bps:+.2f} bps",
            f"Max  fill price diff:  {self.max_price_diff_bps:.2f} bps",
            f"Total cost diff (A-B): {self.total_cost_diff:+.2f}",
            f"Total net  diff (A-B): {self.total_net_diff:+.2f}",
        ]
        # Top 5 largest divergences
        top = sorted(self.divergences, key=lambda d: abs(d.net_diff), reverse=True)[:5]
        if top:
            lines.append("\nTop 5 divergences by |net_diff|:")
            for d in top:
                lines.append(
                    f"  {d.event.trade_date} {d.event.symbol} "
                    f"{d.event.side:4s} {d.event.qty:>6d} "
                    f"price_diff={d.price_diff_bps:+.1f}bps "
                    f"net_diff={d.net_diff:+.2f}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict (JSON-safe)."""
        return {
            "total_trades": self.total_trades,
            "mean_price_diff_bps": round(self.mean_price_diff_bps, 4),
            "max_price_diff_bps": round(self.max_price_diff_bps, 4),
            "total_cost_diff": round(self.total_cost_diff, 4),
            "total_net_diff": round(self.total_net_diff, 4),
            "divergences": [
                {
                    "date": str(d.event.trade_date),
                    "symbol": d.event.symbol,
                    "side": d.event.side,
                    "qty": d.event.qty,
                    "price_diff_bps": round(d.price_diff_bps, 4),
                    "cost_diff": round(d.cost_diff, 4),
                    "net_diff": round(d.net_diff, 4),
                }
                for d in self.divergences
            ],
        }


class ShadowComparator:
    """双路径执行比较器.

    Path A (Backtest): NextBarOpen + FeeModelCNEquity (时变印花税)
    Path B (Paper):    Close + calc_trade_cost (FeeParams)
    """

    def __init__(
        self,
        fee_model: FeeModelCNEquity,
        fee_params: FeeParams,
        slippage_bps: float = 10.0,
    ) -> None:
        self._fee_model = fee_model
        self._fee_params = fee_params
        self._slippage_bps = slippage_bps
        self._slippage_model = FixedBpsSlippageModel(bps=slippage_bps)

    @classmethod
    def for_cn_equity(cls) -> "ShadowComparator":
        """A 股预设."""
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        rules = MarketRulesCNEquity()
        fee_model = FeeModelCNEquity(
            commission=CommissionModel(
                rate=float(rules.commission_rate),
                min_fee=float(rules.commission_min),
            ),
            stamp_tax=TimeVaryingStampTaxModel(
                default_rate=float(rules.stamp_tax_rate),
            ),
            transfer_fee=TransferFeeModel(
                rate=float(rules.transfer_fee_rate),
            ),
        )
        fp = get_fee_params("cn_equity")
        return cls(fee_model, fp, slippage_bps=rules.slippage_bps_default)

    @classmethod
    def for_convertible_bond(cls) -> "ShadowComparator":
        """CB 预设."""
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )
        rules = MarketRulesConvertibleBond()
        fee_model = FeeModelCNEquity(
            commission=CommissionModel(
                rate=float(rules.commission_rate),
                min_fee=float(rules.commission_min),
            ),
            stamp_tax=StampTaxModel(sell_rate=0.0),
            transfer_fee=TransferFeeModel(rate=0.0),
        )
        fp = get_fee_params("convertible_bond")
        return cls(fee_model, fp, slippage_bps=rules.slippage_bps_default)

    # ── Core comparison ──────────────────────────────────────────

    def _exec_path_a(self, event: TradeEvent) -> PathResult:
        """Path A: NextBarOpen + FeeModelCNEquity."""
        fill_price = event.next_open_price
        side = Side.BUY if event.side == "buy" else Side.SELL
        fill = Fill(
            order_id="shadow-a",
            symbol=event.symbol,
            side=side,
            qty=event.qty,
            price=fill_price,
            t_fill=datetime.combine(event.trade_date, datetime.min.time()),
            model="next_bar_open",
        )
        slipped = self._slippage_model.apply(fill)
        slipped_price = slipped.price
        amount = slipped_price * event.qty
        breakdown = self._fee_model.calc(slipped)
        cost = breakdown.total
        if event.side == "sell":
            net = amount - cost
        else:
            net = amount + cost
        return PathResult(fill_price, slipped_price, cost, net)

    def _exec_path_b(self, event: TradeEvent) -> PathResult:
        """Path B: Close + calc_trade_cost."""
        fill_price = event.close_price
        slipped_price = apply_slippage(
            fill_price, event.side, self._slippage_bps,
        )
        amount = slipped_price * event.qty
        cost = calc_trade_cost(amount, event.side, self._fee_params)
        if event.side == "sell":
            net = amount - cost
        else:
            net = amount + cost
        return PathResult(fill_price, slipped_price, cost, net)

    def compare_trade(self, event: TradeEvent) -> TradeDivergence:
        """比较单笔交易."""
        a = self._exec_path_a(event)
        b = self._exec_path_b(event)
        mid = (a.slipped_price + b.slipped_price) / 2 if (a.slipped_price + b.slipped_price) else 1.0
        price_diff_bps = (a.slipped_price - b.slipped_price) / mid * 10000
        return TradeDivergence(
            event=event,
            path_a=a,
            path_b=b,
            price_diff_bps=price_diff_bps,
            cost_diff=a.cost - b.cost,
            net_diff=a.net_amount - b.net_amount,
        )

    def compare_trades(self, events: list[TradeEvent]) -> ShadowReport:
        """批量比较并生成报告."""
        report = ShadowReport()
        for ev in events:
            report.divergences.append(self.compare_trade(ev))
        return report


# ── TradeRecord → TradeEvent 适配 ────────────────────────────────


def build_trade_events(
    trades: list[Any],
    price_lookup: Callable[[str, date], tuple[float, float] | None],
) -> list[TradeEvent]:
    """把回测 TradeRecord 转成可比较的 TradeEvent.

    TradeRecord 只保存实际成交价，不保存当日收盘价与次日开盘价，
    因此双路径比较必须由调用方提供行情查询 —— 这是此前无法接入
    例行流程的原因。

    Args:
        trades: BacktestResult.trades (需有 symbol / side / qty / trade_date)
        price_lookup: (symbol, trade_date) → (close_price, next_open_price)。
            返回 None 表示该笔缺行情，跳过而非报错（避免个别缺失
            拖垮整份报告）。

    Returns:
        可交给 ShadowComparator.compare_trades 的事件列表
    """
    events: list[TradeEvent] = []
    skipped = 0
    for tr in trades:
        symbol = getattr(tr, "symbol", None) or getattr(tr, "code", None)
        trade_date = getattr(tr, "trade_date", None)
        if not symbol or trade_date is None:
            skipped += 1
            continue

        prices = price_lookup(symbol, trade_date)
        if not prices:
            skipped += 1
            continue
        close_price, next_open = prices
        if not (close_price > 0 and next_open > 0):
            skipped += 1
            continue

        raw_side = getattr(tr, "side", "buy")
        side = getattr(raw_side, "value", raw_side)
        events.append(TradeEvent(
            trade_date=trade_date,
            symbol=symbol,
            side=str(side).lower(),
            qty=int(getattr(tr, "qty", 0)),
            close_price=float(close_price),
            next_open_price=float(next_open),
        ))

    if skipped:
        logger.info(
            "shadow 比较跳过 %d/%d 笔交易（缺行情或字段不全）",
            skipped, len(trades),
        )
    return events
