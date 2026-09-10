"""影子运行器测试 — 双路径执行差异与 TradeRecord 适配。

Path A: NextBarOpen 成交 (回测口径)
Path B: 当日 Close 成交 (实盘/仿真口径)
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.backtest.shadow_runner import (
    ShadowComparator,
    ShadowReport,
    TradeEvent,
    build_trade_events,
)


@pytest.fixture
def comparator() -> ShadowComparator:
    return ShadowComparator.for_cn_equity()


def _event(**kw) -> TradeEvent:
    base = dict(
        trade_date=date(2026, 3, 2),
        symbol="600000.SH",
        side="buy",
        qty=1000,
        close_price=10.0,
        next_open_price=10.0,
    )
    base.update(kw)
    return TradeEvent(**base)


class TestPresets:
    """预设构造"""

    def test_cn_equity_preset(self) -> None:
        assert ShadowComparator.for_cn_equity() is not None

    def test_convertible_bond_preset(self) -> None:
        assert ShadowComparator.for_convertible_bond() is not None


class TestPathDivergence:
    """双路径差异"""

    def test_identical_prices_no_price_divergence(
        self, comparator: ShadowComparator,
    ) -> None:
        """收盘价 == 次日开盘价 → 成交价无差异"""
        d = comparator.compare_trade(_event(close_price=10.0, next_open_price=10.0))
        assert d.price_diff_bps == pytest.approx(0.0, abs=1e-6)

    def test_gap_up_penalizes_backtest_path(
        self, comparator: ShadowComparator,
    ) -> None:
        """次日跳空高开 → Path A 成交价更高，差异为正"""
        d = comparator.compare_trade(_event(close_price=10.0, next_open_price=10.5))
        assert d.price_diff_bps > 0

    def test_gap_down_favors_backtest_path(
        self, comparator: ShadowComparator,
    ) -> None:
        d = comparator.compare_trade(_event(close_price=10.0, next_open_price=9.5))
        assert d.price_diff_bps < 0

    def test_buy_slippage_raises_price(self, comparator: ShadowComparator) -> None:
        """买入滑点让成交价高于名义价"""
        d = comparator.compare_trade(_event(side="buy"))
        assert d.path_a.slipped_price >= d.path_a.fill_price
        assert d.path_b.slipped_price >= d.path_b.fill_price

    def test_sell_slippage_lowers_price(self, comparator: ShadowComparator) -> None:
        d = comparator.compare_trade(_event(side="sell"))
        assert d.path_a.slipped_price <= d.path_a.fill_price

    def test_buy_net_includes_cost(self, comparator: ShadowComparator) -> None:
        """买入净额 = 金额 + 费用"""
        d = comparator.compare_trade(_event(side="buy"))
        assert d.path_a.net_amount > d.path_a.slipped_price * 1000

    def test_sell_net_deducts_cost(self, comparator: ShadowComparator) -> None:
        d = comparator.compare_trade(_event(side="sell"))
        assert d.path_a.net_amount < d.path_a.slipped_price * 1000


class TestReportAggregation:
    """报告汇总"""

    def test_empty_report(self) -> None:
        report = ShadowReport()
        assert report.total_trades == 0
        assert report.mean_price_diff_bps == 0.0
        assert report.max_price_diff_bps == 0.0

    def test_batch_compare(self, comparator: ShadowComparator) -> None:
        events = [
            _event(next_open_price=10.5),
            _event(next_open_price=9.5),
            _event(next_open_price=10.0),
        ]
        report = comparator.compare_trades(events)

        assert report.total_trades == 3
        assert report.max_price_diff_bps > 0

    def test_summary_renders(self, comparator: ShadowComparator) -> None:
        report = comparator.compare_trades([_event(next_open_price=10.5)])
        text = report.summary()

        assert "Shadow Run Divergence Report" in text
        assert "Total trades compared: 1" in text

    def test_to_dict_json_safe(self, comparator: ShadowComparator) -> None:
        import json

        report = comparator.compare_trades([_event(next_open_price=10.5)])
        json.dumps(report.to_dict())

        assert report.to_dict()["total_trades"] == 1


class _FakeTrade:
    """模拟 BacktestResult.trades 元素。"""

    def __init__(self, symbol="600000.SH", side="buy", qty=1000,
                 trade_date=date(2026, 3, 2)) -> None:
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.trade_date = trade_date


class TestBuildTradeEvents:
    """TradeRecord → TradeEvent 适配"""

    def _lookup(self, symbol: str, d: date) -> tuple[float, float] | None:
        return (10.0, 10.2)

    def test_converts_trades(self) -> None:
        events = build_trade_events([_FakeTrade()], self._lookup)

        assert len(events) == 1
        assert events[0].symbol == "600000.SH"
        assert events[0].close_price == 10.0
        assert events[0].next_open_price == 10.2

    def test_skips_missing_quotes(self) -> None:
        """行情缺失时跳过该笔，不报错"""
        events = build_trade_events([_FakeTrade()], lambda s, d: None)
        assert events == []

    def test_skips_non_positive_prices(self) -> None:
        events = build_trade_events([_FakeTrade()], lambda s, d: (0.0, 10.0))
        assert events == []

    def test_skips_incomplete_records(self) -> None:
        events = build_trade_events([_FakeTrade(symbol="")], self._lookup)
        assert events == []

    def test_normalizes_enum_side(self) -> None:
        """Side 枚举被归一为小写字符串"""
        class _Side:
            value = "BUY"

        trade = _FakeTrade()
        trade.side = _Side()
        events = build_trade_events([trade], self._lookup)

        assert events[0].side == "buy"

    def test_partial_skip_keeps_rest(self) -> None:
        """个别缺行情不影响其余笔"""
        trades = [_FakeTrade(symbol="600000.SH"), _FakeTrade(symbol="600001.SH")]

        def lookup(symbol: str, d: date) -> tuple[float, float] | None:
            return (10.0, 10.2) if symbol == "600000.SH" else None

        events = build_trade_events(trades, lookup)

        assert [e.symbol for e in events] == ["600000.SH"]
