"""交易成本归因 (TCA) 测试

覆盖：
- build_cost_attribution: 正常/空输入/单笔/多维度聚合
- CostComponent: 费率计算/to_dict
- format_cost_report: 文本输出
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.reporting.cost_attribution import (
    CostAttributionReport,
    CostComponent,
    TradeCostRecord,
    build_cost_attribution,
    format_cost_report,
)


def _make_trade(
    d: date,
    symbol: str = "000001.SZ",
    side: str = "BUY",
    qty: int = 100,
    price: float = 10.0,
    commission: float = 5.0,
    stamp_tax: float = 0.0,
    transfer_fee: float = 0.02,
) -> TradeCostRecord:
    return TradeCostRecord(
        trade_date=d,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        notional=qty * price,
        commission=commission,
        stamp_tax=stamp_tax,
        transfer_fee=transfer_fee,
    )


# ============================================================
# CostComponent
# ============================================================


class TestCostComponent:

    def test_total(self):
        c = CostComponent(10, 5, 2, 3, 10000)
        assert c.total == 17.0

    def test_cost_rate_bps(self):
        c = CostComponent(10, 5, 2, 3, 10000)
        assert c.cost_rate_bps == pytest.approx(17.0)

    def test_zero_notional(self):
        c = CostComponent(0, 0, 0, 0, 0)
        assert c.cost_rate_bps == 0.0

    def test_to_dict(self):
        c = CostComponent(10, 5, 2, 3, 10000)
        d = c.to_dict()
        assert d["commission"] == 10
        assert d["trade_count"] == 3
        assert d["cost_rate_bps"] == 17.0


# ============================================================
# build_cost_attribution
# ============================================================


class TestBuildCostAttribution:

    def test_empty_trades(self):
        r = build_cost_attribution([])
        assert r.total.trade_count == 0
        assert r.by_side == {}
        assert r.avg_cost_per_trade == 0.0

    def test_single_buy(self):
        trades = [_make_trade(date(2024, 1, 2), commission=5, stamp_tax=0)]
        r = build_cost_attribution(trades)
        assert r.total.trade_count == 1
        assert r.total.commission == 5
        assert r.total.stamp_tax == 0
        assert "BUY" in r.by_side
        assert "SELL" not in r.by_side

    def test_buy_sell_split(self):
        trades = [
            _make_trade(date(2024, 1, 2), side="BUY", commission=5, stamp_tax=0),
            _make_trade(date(2024, 1, 3), side="SELL", commission=5, stamp_tax=1),
        ]
        r = build_cost_attribution(trades)
        assert r.by_side["BUY"].stamp_tax == 0
        assert r.by_side["SELL"].stamp_tax == 1
        assert r.total.stamp_tax == 1

    def test_multi_symbol_aggregation(self):
        trades = [
            _make_trade(date(2024, 1, 2), symbol="000001.SZ", commission=5),
            _make_trade(date(2024, 1, 2), symbol="000002.SZ", commission=10),
            _make_trade(date(2024, 1, 3), symbol="000001.SZ", commission=3),
        ]
        r = build_cost_attribution(trades)
        assert len(r.by_symbol) == 2
        assert r.by_symbol["000001.SZ"].trade_count == 2
        assert r.by_symbol["000001.SZ"].commission == 8

    def test_monthly_aggregation(self):
        trades = [
            _make_trade(date(2024, 1, 15)),
            _make_trade(date(2024, 1, 20)),
            _make_trade(date(2024, 2, 5)),
        ]
        r = build_cost_attribution(trades)
        assert "2024-01" in r.by_month
        assert "2024-02" in r.by_month
        assert r.by_month["2024-01"].trade_count == 2
        assert r.by_month["2024-02"].trade_count == 1

    def test_cost_drag_annualized(self):
        trades = [
            _make_trade(date(2024, 1, 2), commission=100, stamp_tax=50, transfer_fee=10),
        ]
        r = build_cost_attribution(
            trades,
            initial_nav=1_000_000,
            final_nav=1_100_000,
            total_calendar_days=365,
        )
        # (100+50+10) / 1_000_000 / 1.0 * 100 = 0.016%
        assert r.cost_drag_annualized == pytest.approx(0.016, abs=0.001)

    def test_avg_cost_per_trade(self):
        trades = [
            _make_trade(date(2024, 1, 2), commission=10, stamp_tax=0, transfer_fee=0),
            _make_trade(date(2024, 1, 3), commission=20, stamp_tax=0, transfer_fee=0),
        ]
        r = build_cost_attribution(trades)
        assert r.avg_cost_per_trade == 15.0


# ============================================================
# TradeCostRecord
# ============================================================


class TestTradeCostRecord:

    def test_total_fee(self):
        t = _make_trade(date(2024, 1, 1), commission=5, stamp_tax=1, transfer_fee=0.5)
        assert t.total_fee == pytest.approx(6.5)


# ============================================================
# CostAttributionReport.to_dict
# ============================================================


class TestReportDict:

    def test_to_dict_structure(self):
        trades = [
            _make_trade(date(2024, 1, 2), symbol="000001.SZ"),
            _make_trade(date(2024, 1, 3), symbol="000002.SZ"),
        ]
        r = build_cost_attribution(trades, final_nav=1_100_000, total_calendar_days=365)
        d = r.to_dict()
        assert "total" in d
        assert "by_side" in d
        assert "by_symbol_top10" in d
        assert "by_month" in d
        assert "cost_drag_annualized_pct" in d


# ============================================================
# format_cost_report
# ============================================================


class TestFormatReport:

    def test_nonempty(self):
        trades = [
            _make_trade(date(2024, 1, 2), side="BUY"),
            _make_trade(date(2024, 1, 3), side="SELL", stamp_tax=1),
        ]
        r = build_cost_attribution(trades)
        text = format_cost_report(r)
        assert "交易成本归因报告" in text
        assert "BUY" in text
        assert "SELL" in text

    def test_empty(self):
        r = build_cost_attribution([])
        text = format_cost_report(r)
        assert "总交易笔数: 0" in text
