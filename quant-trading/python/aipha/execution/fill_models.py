from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aipha.domain.models import Bar, Fill, Order, Side
from aipha.market.base import MarketRules


def _precheck_with_bar_context(
    rules: MarketRules, order: Order, bar: Bar,
) -> "PrecheckDecision":
    """统一预检入口：从 Bar 自动提取 trade_date/prev_close 传给规则层。

    修复 P0-2：此前 precheck_order 调用不传 trade_date/prev_close，
    导致 CB 2022 新规涨跌停检查因默认值 None 全部短路。
    """
    from aipha.market.base import PrecheckDecision  # noqa: F811

    trade_date = bar.start_ts.date() if bar.start_ts else None
    prev_close = bar.prev_close
    # is_first_day: 从 metadata 获取（若数据层设置了 'is_first_day' 字段）
    is_first_day = bool(
        bar.metadata.get("is_first_day", False) if bar.metadata else False
    )
    return rules.precheck_order(
        order=order,
        bar=bar,
        trade_date=trade_date,
        prev_close=prev_close,
        is_first_day=is_first_day,
    )


class NoFillReason(str):
    """Reasons why an order did not fill in a fill model (not a rule rejection)."""

    LIMIT_NOT_TOUCHED = "LIMIT_NOT_TOUCHED"
    ZERO_VOLUME = "ZERO_VOLUME"
    VOLUME_EXHAUSTED = "VOLUME_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class FillResult:
    """Result of a fill attempt.

    Attributes:
        fill: The fill if successful (may be partial if qty < order.qty)
        reject_reason: Market rule rejection reason if order was rejected
        no_fill_reason: Model-specific reason if no fill occurred
        remaining_qty: Quantity remaining unfilled (for partial fills)
    """

    fill: Fill | None
    reject_reason: str | None = None
    no_fill_reason: str | None = None
    remaining_qty: int = 0

    @property
    def is_partial(self) -> bool:
        """Check if this is a partial fill."""
        return self.fill is not None and self.remaining_qty > 0

    @property
    def is_full(self) -> bool:
        """Check if this is a full fill."""
        return self.fill is not None and self.remaining_qty == 0

    @property
    def is_rejected(self) -> bool:
        """Check if order was rejected by market rules."""
        return self.reject_reason is not None


class FillModel:
    """成交模型基类。

    定义订单如何被撮合成交。所有成交模型必须实现 ``try_fill()``。
    子类可选覆盖 ``is_same_bar`` 属性（默认返回 False）。
    """

    name: str

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试撮合订单（抽象方法，由子类实现具体成交逻辑）。"""
        raise NotImplementedError

    @property
    def is_same_bar(self) -> bool:
        """是否为同 bar 即时成交模型，默认 False（延迟到下一 bar）。"""
        return False


class NextBarOpenFillModel(FillModel):
    """下一 Bar 开盘价成交模型。

    保守成交假设，使用信号 bar 之后的下一个 bar 的开盘价成交，
    避免同 bar 前视偏差。
    """

    name = "next_bar_open"

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试以下一根K线开盘价成交。"""
        decision = _precheck_with_bar_context(rules, order, next_bar)
        if not decision.allowed:
            return FillResult(fill=None, reject_reason=decision.reason)

        # 防御性检查：open <= 0 时拒绝成交（停牌/数据异常）
        if next_bar.open <= 0:
            return FillResult(
                fill=None,
                reject_reason="ZERO_OPEN_PRICE",
            )

        # V1: assume full fill at next bar open.
        return FillResult(
            fill=Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=next_bar.open,
                t_fill=next_bar.start_ts,
                model=self.name,
            )
        )


class NextBarCloseFillModel(FillModel):
    """下一 Bar 收盘价成交模型。

    使用信号 bar 之后的下一个 bar 的收盘价成交，适用于收盘撮合场景。
    """

    name = "next_bar_close"

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试以下一根K线收盘价成交。"""
        decision = _precheck_with_bar_context(rules, order, next_bar)
        if not decision.allowed:
            return FillResult(fill=None, reject_reason=decision.reason)

        # 防御性检查：close <= 0 时拒绝成交（数据异常）
        if next_bar.close <= 0:
            return FillResult(
                fill=None,
                reject_reason="ZERO_CLOSE_PRICE",
            )

        return FillResult(
            fill=Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=next_bar.close,
                t_fill=next_bar.end_ts,
                model=self.name,
            )
        )


class SameBarCloseFillModel(FillModel):
    """Same-bar close fill model.

    信号日当 bar 的 close 价立即成交，用于可转债等 T+0 策略。
    与 NextBarCloseFillModel 不同，本模型不延迟到下一 bar，
    而是由引擎在同一 bar 事件中立即撮合。

    引擎行为：
        - _process_bar_event 产生订单后，立即调用 _process_pending_orders
        - 传入的 bar 就是信号 bar 本身（不是 next bar）
        - 成交价 = bar.close, t_fill = bar.end_ts

    .. warning:: 轻微前视偏差（R94-V11）

        收盘价在 bar 结束后才最终确定，但策略信号在 bar 内生成时
        即以 close 成交，存在轻微前视偏差。对于日频策略，这意味着
        在收盘集合竞价确定最终价格之前已假设成交。
        当前无 CB 策略使用此模型（均使用 next_bar_open），因此
        不影响现有回测结果。使用此模型时应注意此偏差。
    """

    name = "same_bar_close"

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试以当前K线收盘价即时成交（T+0场景）。"""
        decision = _precheck_with_bar_context(rules, order, next_bar)
        if not decision.allowed:
            return FillResult(fill=None, reject_reason=decision.reason)

        # 防御性检查：close <= 0 时拒绝成交（数据异常）
        if next_bar.close <= 0:
            return FillResult(
                fill=None,
                reject_reason="ZERO_CLOSE_PRICE",
            )

        return FillResult(
            fill=Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=next_bar.close,
                t_fill=next_bar.end_ts,
                model=self.name,
            )
        )

    @property
    def is_same_bar(self) -> bool:
        """标识为同 bar 成交模型，引擎据此决定撮合时序。"""
        return True


class BarLimitTouchFillModel(FillModel):
    """Bar-level limit trigger model.

    V1 contract:
        - BUY limit: touched if limit_price >= bar.low
        - SELL limit: touched if limit_price <= bar.high
        - Fill price: limit_price (conservative; avoids assuming better price)

    Notes:
        This is *not* orderbook-accurate. It's a bar approximation with explicit assumptions.
    """

    name = "bar_limit_touch"

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试以限价单触发方式成交（Bar级别限价触发模型）。"""
        decision = _precheck_with_bar_context(rules, order, next_bar)
        if not decision.allowed:
            return FillResult(fill=None, reject_reason=decision.reason)

        if order.limit_price is None:
            raise ValueError("limit_price is required for BarLimitTouchFillModel")

        touched = (
            (order.side == Side.BUY and order.limit_price >= next_bar.low)
            or (order.side == Side.SELL and order.limit_price <= next_bar.high)
        )
        if not touched:
            return FillResult(fill=None, no_fill_reason=NoFillReason.LIMIT_NOT_TOUCHED)

        return FillResult(
            fill=Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=order.limit_price,
                t_fill=next_bar.start_ts,
                model=self.name,
            )
        )


class VWAPFillModel(FillModel):
    """Fill at VWAP if available, otherwise at bar midpoint.

    V1.2 contract:
        - If bar.vwap is available, fill at VWAP
        - Otherwise, fall back to bar midpoint: (high + low) / 2
        - Fill time is bar end_ts (conservative: assumes order worked through bar)

    Notes:
        This is a simplified VWAP model. Real VWAP execution would require
        slicing orders across time periods proportional to expected volume.
    """

    name = "vwap"

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试以VWAP价格成交，无VWAP时回退到Bar中间价。"""
        decision = _precheck_with_bar_context(rules, order, next_bar)
        if not decision.allowed:
            return FillResult(fill=None, reject_reason=decision.reason)

        # Use VWAP if available, otherwise midpoint
        fill_price = next_bar.vwap if next_bar.vwap is not None else (next_bar.high + next_bar.low) / 2

        # 防御性检查：价格 <= 0 时拒绝成交（停牌/数据异常）
        if fill_price <= 0:
            return FillResult(
                fill=None,
                reject_reason="ZERO_FILL_PRICE",
            )

        return FillResult(
            fill=Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=fill_price,
                t_fill=next_bar.end_ts,
                model=self.name,
            )
        )


@dataclass(frozen=True, slots=True)
class VolumeConstraint:
    """Configuration for volume-based fill constraints.

    Attributes:
        max_participation_rate: Maximum % of bar volume allowed (0.0-1.0).
            E.g., 0.1 means max 10% of bar volume.
        lot_size: Minimum tradeable quantity (A股=100). Filled qty rounded down.
        opening_volume_fraction: Fraction of daily volume available at opening
            auction. When inner model is NextBarOpen, effective volume =
            bar_volume * opening_volume_fraction. Default 0.15 (conservative).
            Typical values: 0.10-0.15 for A股/CB opening auction.
    """

    max_participation_rate: float = 0.10  # Default: 10% participation
    lot_size: int = 100
    opening_volume_fraction: float = 0.15  # Conservative: opening auction ≈15% of daily volume

    def __post_init__(self) -> None:
        if not 0.0 < self.max_participation_rate <= 1.0:
            raise ValueError("max_participation_rate must be in (0, 1]")
        if not 0.0 < self.opening_volume_fraction <= 1.0:
            raise ValueError("opening_volume_fraction must be in (0, 1]")

    def compute_max_qty(self, bar_volume: float) -> int:
        """Compute maximum fillable quantity based on bar volume.

        When opening_volume_fraction < 1.0, the effective volume is reduced
        to model that only a fraction of daily volume is available at the
        opening auction (relevant for NextBarOpen fill model).
        """
        effective_volume = bar_volume * self.opening_volume_fraction
        max_qty_raw = effective_volume * self.max_participation_rate
        # Round down to nearest lot
        return int(max_qty_raw // self.lot_size) * self.lot_size


class VolumeConstrainedFillModel(FillModel):
    """Wrapper that applies volume participation constraint to any fill model.

    V1.2 contract:
        - Wraps an inner fill model (e.g., NextBarOpenFillModel)
        - Limits fill quantity to max_participation_rate of bar volume
        - If bar volume is zero, order is rejected with ZERO_VOLUME
        - Partial fills are returned if qty exceeds volume constraint

    Configuration:
        - max_participation_rate: Max % of bar volume (default 10%)
        - lot_size: Minimum lot size for rounding (default 100 for A股)

    Notes:
        This constraint helps model market impact and realistic execution.
        Orders larger than available volume are partially filled.
    """

    def __init__(
        self,
        inner_model: FillModel,
        constraint: VolumeConstraint | None = None,
    ) -> None:
        self._inner = inner_model
        self._constraint = constraint or VolumeConstraint()

    @property
    def name(self) -> str:
        """返回带成交量约束后缀的模型名称。"""
        return f"{self._inner.name}_volume_constrained"

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """在成交量参与率约束下尝试撮合订单。"""
        # Check for zero volume first
        if next_bar.volume <= 0:
            return FillResult(fill=None, no_fill_reason=NoFillReason.ZERO_VOLUME)

        # Compute max fillable quantity
        max_qty = self._constraint.compute_max_qty(next_bar.volume)

        if max_qty <= 0:
            return FillResult(fill=None, no_fill_reason=NoFillReason.VOLUME_EXHAUSTED)

        # Determine actual fill quantity
        fill_qty = min(order.qty, max_qty)
        remaining_qty = order.qty - fill_qty

        # Round down to lot size (this may be redundant with precheck but ensures consistency)
        fill_qty = (fill_qty // self._constraint.lot_size) * self._constraint.lot_size

        if fill_qty <= 0:
            return FillResult(fill=None, no_fill_reason=NoFillReason.VOLUME_EXHAUSTED)

        # Create a modified order with reduced quantity for inner model
        from dataclasses import replace

        partial_order = replace(order, qty=fill_qty)

        # Delegate to inner model
        inner_result = self._inner.try_fill(order=partial_order, next_bar=next_bar, rules=rules)

        if inner_result.fill is None:
            return inner_result

        # Return result with remaining quantity info
        return FillResult(
            fill=inner_result.fill,
            reject_reason=None,
            no_fill_reason=None,
            remaining_qty=remaining_qty,
        )


class TwapSliceFillModel(FillModel):
    """Simulated TWAP execution across multiple bars.

    V1.2 contract:
        - This model is for single-bar context but simulates TWAP assumption
        - Slices order uniformly across expected number of bars
        - Fill price is bar VWAP (or midpoint if unavailable)
        - Designed to be called once per bar within a TWAP window

    Notes:
        Full TWAP orchestration (across multiple bars) should be done at
        the execution engine level, calling this model for each bar in window.
    """

    name = "twap_slice"

    def __init__(self, n_slices: int = 10) -> None:
        """
        Args:
            n_slices: Expected number of bars to slice order across.
        """
        if n_slices < 1:
            raise ValueError("n_slices must be >= 1")
        self._n_slices = n_slices

    def try_fill(self, *, order: Order, next_bar: Bar, rules: MarketRules) -> FillResult:
        """尝试以TWAP切片方式成交（均匀分配到多个Bar）。"""
        decision = _precheck_with_bar_context(rules, order, next_bar)
        if not decision.allowed:
            return FillResult(fill=None, reject_reason=decision.reason)

        # TWAP: uniform slice
        slice_qty = order.qty // self._n_slices
        lot_size = rules.lot_size  # 从规则获取，不再硬编码

        # Round to lot size
        slice_qty = (slice_qty // lot_size) * lot_size

        if slice_qty <= 0:
            return FillResult(fill=None, no_fill_reason=NoFillReason.VOLUME_EXHAUSTED)

        remaining_qty = order.qty - slice_qty

        # Fill price: VWAP or midpoint
        fill_price = next_bar.vwap if next_bar.vwap is not None else (next_bar.high + next_bar.low) / 2

        return FillResult(
            fill=Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=slice_qty,
                price=fill_price,
                t_fill=next_bar.end_ts,
                model=self.name,
            ),
            remaining_qty=remaining_qty,
        )


# =============================================================================
# Factory / Registry
# =============================================================================

_FILL_MODEL_REGISTRY: dict[str, type[FillModel]] = {
    "next_bar_open": NextBarOpenFillModel,
    "next_bar_close": NextBarCloseFillModel,
    "same_bar_close": SameBarCloseFillModel,
    "bar_limit_touch": BarLimitTouchFillModel,
    "vwap": VWAPFillModel,
}


def get_fill_model(name: str, **kwargs: Any) -> FillModel:
    """Get a fill model by name.

    Args:
        name: Model name (e.g., "next_bar_open", "vwap")
        **kwargs: Additional arguments for specific models

    Returns:
        Instantiated fill model

    Raises:
        KeyError: If model name not recognized
    """
    if name not in _FILL_MODEL_REGISTRY:
        raise KeyError(f"Unknown fill model: {name!r}. Available: {list(_FILL_MODEL_REGISTRY.keys())}")

    model_cls = _FILL_MODEL_REGISTRY[name]
    return model_cls(**kwargs) if kwargs else model_cls()


def create_volume_constrained_model(
    base_name: str = "next_bar_open",
    max_participation_rate: float = 0.10,
    lot_size: int = 100,
) -> VolumeConstrainedFillModel:
    """Create a volume-constrained fill model.

    Args:
        base_name: Name of inner fill model
        max_participation_rate: Max % of bar volume (default 10%)
        lot_size: Lot size for qty rounding (default 100)

    Returns:
        VolumeConstrainedFillModel wrapping the specified inner model
    """
    inner = get_fill_model(base_name)
    constraint = VolumeConstraint(max_participation_rate=max_participation_rate, lot_size=lot_size)
    return VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)

