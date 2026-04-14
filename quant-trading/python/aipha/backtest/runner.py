from __future__ import annotations

from dataclasses import dataclass

from aipha.domain.models import Bar, Fill, Order
from aipha.execution.fees import FeeBreakdown, FeeModel
from aipha.execution.fill_models import FillModel, FillResult
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.base import MarketRules


@dataclass(frozen=True, slots=True)
class BacktestOutcome:
    """单笔订单回测结果。

    Attributes:
        fill: 成交回报（未成交时为 None）
        fees: 费用明细（未成交时为 None）
        reject_reason: 拒单原因（无拒单时为 None）
        no_fill_reason: 未成交原因（如涨跌停、停牌等）
    """

    fill: Fill | None
    fees: FeeBreakdown | None
    reject_reason: str | None
    no_fill_reason: str | None


def simulate_single_order_next_bar(
    *,
    order: Order,
    next_bar: Bar,
    fill_model: FillModel,
    slippage: FixedBpsSlippageModel,
    fee_model: FeeModel,
    rules: "MarketRules",
) -> BacktestOutcome:
    """A tiny, formal MVP runner.

    This is intentionally minimal but *not* a mock:
        - uses real domain models
        - uses real rules + fill model + slippage + fee model

    It exists to support unit tests and quick sanity runs.
    """

    result: FillResult = fill_model.try_fill(order=order, next_bar=next_bar, rules=rules)
    if result.fill is None:
        return BacktestOutcome(
            fill=None,
            fees=None,
            reject_reason=str(result.reject_reason) if result.reject_reason else None,
            no_fill_reason=result.no_fill_reason,
        )

    slipped = slippage.apply(result.fill, next_bar)
    fees = fee_model.calc(slipped)
    return BacktestOutcome(fill=slipped, fees=fees, reject_reason=None, no_fill_reason=None)
