"""成交记录必须带上 signal_price。

没有这个字段就无法事后度量「信号价 → 成交价」跑掉了多少，
``max_chase_pct`` 也就只能凭直觉给。挂单/意图里本就带着这个数，
此前在写成交记录时被丢掉了。

参见 aipha.advisory.chase_calibration 与 scripts/calibrate_chase_cap.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aipha.runner.daily_utils import ComboState
from aipha.runner.paper_engine_eod import _fill_pending_orders
from aipha.execution.trading_ops import (
    RebalanceResult,
    TradeRecord,
)


def _state(pending: list[dict]) -> ComboState:
    return ComboState(
        profile_name="t", initial_capital=1_000_000.0, cash=1_000_000.0,
        pending_orders=list(pending),
    )


def _intent(
    code: str = "600000.SH",
    side: str = "buy",
    signal_price: float = 10.0,
    signal_date: str = "20250106",
) -> dict:
    return {
        "code": code, "side": side, "shares": 100,
        "signal_price": signal_price, "component": "c1",
        "reason": "new_position", "signal_date": signal_date,
    }


def _trade(
    code: str = "600000.SH",
    side: str = "buy",
    price: float = 10.3,
    trade_date: str = "20250107",
) -> TradeRecord:
    return TradeRecord(
        code=code, side=side, shares=100, price=price,
        amount=price * 100, cost=5.0, net=-(price * 100 + 5.0),
        trade_date=trade_date, component="c1",
    )


def _run_fill(state: ComboState, trades: list[TradeRecord], prices: dict):
    """跑一遍挂单成交，行情与撮合都替换成固定值。"""
    result = RebalanceResult(
        new_cash=state.cash, new_positions=[],
        buys=[t for t in trades if t.side == "buy"],
        sells=[t for t in trades if t.side == "sell"],
    )
    with patch(
        "aipha.runner.paper_engine_eod._ops_fetch_open_prices",
        return_value=prices,
    ), patch(
        "aipha.runner.paper_engine_eod._ops_fill_order_intents",
        return_value=result,
    ):
        return _fill_pending_orders({}, state, "20250107", {})


class TestSignalPriceRecorded:
    """Paper 路径"""

    def test_signal_price_written_to_trade_log(self) -> None:
        state = _state([_intent(signal_price=10.0)])

        _run_fill(state, [_trade(price=10.3)], {"600000.SH": 10.3})

        assert len(state.trades) == 1
        assert state.trades[0]["signal_price"] == pytest.approx(10.0)

    def test_signal_date_written(self) -> None:
        """信号日与成交日不同 —— 两阶段模型下差一个交易日"""
        state = _state([_intent(signal_date="20250106")])

        _run_fill(state, [_trade(trade_date="20250107")], {"600000.SH": 10.3})

        assert state.trades[0]["signal_date"] == "20250106"
        assert state.trades[0]["date"] == "20250107"

    def test_chase_is_measurable_from_log(self) -> None:
        """落盘后追高幅度必须能直接算出来 —— 这就是整件事的目的"""
        state = _state([_intent(signal_price=10.0)])

        _run_fill(state, [_trade(price=10.3)], {"600000.SH": 10.3})

        t = state.trades[0]
        chase = t["price"] / t["signal_price"] - 1
        assert chase == pytest.approx(0.03, abs=1e-9)

    def test_existing_fields_preserved(self) -> None:
        """新增字段不能挤掉原有字段"""
        state = _state([_intent()])

        _run_fill(state, [_trade()], {"600000.SH": 10.3})

        t = state.trades[0]
        for key in ("date", "code", "side", "price", "shares",
                    "amount", "cost", "component"):
            assert key in t, f"原有字段 {key} 丢失"

    def test_joins_buy_and_sell_of_same_code(self) -> None:
        """同一标的可能一买一卖 —— 按 (code, side) 关联，不能串号"""
        state = _state([
            _intent(side="buy", signal_price=10.0),
            _intent(side="sell", signal_price=20.0),
        ])

        _run_fill(
            state,
            [_trade(side="buy", price=10.3), _trade(side="sell", price=20.5)],
            {"600000.SH": 10.3},
        )

        by_side = {t["side"]: t for t in state.trades}
        assert by_side["buy"]["signal_price"] == pytest.approx(10.0)
        assert by_side["sell"]["signal_price"] == pytest.approx(20.0)

    def test_unmatched_trade_gets_none_not_crash(self) -> None:
        """成交与挂单对不上时记 None，而不是抛异常或写个错的价"""
        state = _state([_intent(code="600000.SH")])

        _run_fill(state, [_trade(code="000001.SZ")], {"600000.SH": 10.3})

        assert state.trades[0]["signal_price"] is None

    def test_intent_without_signal_price_records_none(self) -> None:
        intent = _intent()
        del intent["signal_price"]
        state = _state([intent])

        _run_fill(state, [_trade()], {"600000.SH": 10.3})

        assert state.trades[0]["signal_price"] is None


class TestLiveIntentCarriesSignalPrice:
    """Live 路径 —— 意图转 order dict 时不能丢字段"""

    def test_execution_intent_exposes_signal_price(self) -> None:
        from aipha.execution.trading_ops import ExecutionIntent

        intent = ExecutionIntent(
            code="600000.SH", side="buy", shares=100, signal_price=10.0,
        )

        assert intent.signal_price == pytest.approx(10.0)

    def test_live_engine_entry_includes_signal_price(self) -> None:
        """守住 live_engine_eod 里 intent→entry 的转换含 signal_price"""
        import inspect

        from aipha.runner import live_engine_eod

        src = inspect.getsource(live_engine_eod)
        # entry dict 与成交记录两处都必须带上
        assert src.count('"signal_price": intent.signal_price') == 1
        assert '"signal_price": signal_px.get((order["code"], side))' in src
