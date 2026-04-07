"""
Tests for alignment engine data models and interfaces.

Covers: TradeSource, SymbolMatchMode, MatchQuality enums,
TradeFees, UnifiedTradeRecord, AlignmentConfig, PairDeviation,
PairedTrade, AlignmentSummary, AlignmentResult dataclasses,
and IAlignmentEngine interface (normalize_symbol, symbols_match).
"""

import pytest

from engines.alignment_types import (
    AlignmentConfig,
    AlignmentResult,
    AlignmentSummary,
    IAlignmentEngine,
    MatchQuality,
    PairDeviation,
    PairedTrade,
    SymbolMatchMode,
    TradeFees,
    TradeSource,
    UnifiedTradeRecord,
)


# ── Helpers ─────────────────────────────────────────────────────


def _make_trade(**overrides) -> UnifiedTradeRecord:
    """Factory for a minimal valid UnifiedTradeRecord."""
    defaults = dict(
        trade_id="t-001",
        source=TradeSource.LIVE,
        symbol="600519.SH",
        market="A_SHARE",
        side="BUY",
        entry_time="2025-06-01T09:30:00+08:00",
        entry_price=1800.0,
        quantity=100.0,
        gross_pnl=500.0,
        commission=5.0,
        net_pnl=495.0,
    )
    defaults.update(overrides)
    return UnifiedTradeRecord(**defaults)


def _make_trade_dict(**overrides) -> dict:
    """Factory for a minimal camelCase trade dict."""
    defaults = dict(
        tradeId="t-001",
        source="live",
        symbol="600519.SH",
        market="A_SHARE",
        side="BUY",
        entryTime="2025-06-01T09:30:00+08:00",
        entryPrice=1800.0,
        quantity=100.0,
        grossPnl=500.0,
        commission=5.0,
        netPnl=495.0,
    )
    defaults.update(overrides)
    return defaults


class _StubEngine(IAlignmentEngine):
    """Concrete stub so we can test the default helper methods."""

    def align(self, live_trades, backtest_trades, config):
        raise NotImplementedError


# ── Enum tests ──────────────────────────────────────────────────


class TestTradeSource:
    def test_values(self):
        assert TradeSource.LIVE.value == "live"
        assert TradeSource.BACKTEST.value == "backtest"
        assert TradeSource.PAPER.value == "paper"

    def test_from_string(self):
        assert TradeSource("live") is TradeSource.LIVE
        assert TradeSource("backtest") is TradeSource.BACKTEST

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            TradeSource("invalid")

    def test_is_str_subclass(self):
        assert isinstance(TradeSource.LIVE, str)
        assert TradeSource.LIVE == "live"


class TestSymbolMatchMode:
    def test_values(self):
        assert SymbolMatchMode.EXACT.value == "exact"
        assert SymbolMatchMode.NORMALIZED.value == "normalized"
        assert SymbolMatchMode.FUZZY.value == "fuzzy"

    def test_from_string(self):
        assert SymbolMatchMode("fuzzy") is SymbolMatchMode.FUZZY

    def test_invalid(self):
        with pytest.raises(ValueError):
            SymbolMatchMode("regex")


class TestMatchQuality:
    def test_all_values(self):
        assert set(q.value for q in MatchQuality) == {
            "exact", "close", "loose", "unmatched",
        }

    def test_from_string(self):
        assert MatchQuality("unmatched") is MatchQuality.UNMATCHED


# ── TradeFees ───────────────────────────────────────────────────


class TestTradeFees:
    def test_to_dict_required_only(self):
        fees = TradeFees(commission=5.0)
        d = fees.to_dict()
        assert d == {"commission": 5.0}
        assert "stampDuty" not in d

    def test_to_dict_all_fields(self):
        fees = TradeFees(commission=5.0, stamp_duty=0.9, transfer_fee=0.02, other_fees=0.1)
        d = fees.to_dict()
        assert d["commission"] == 5.0
        assert d["stampDuty"] == 0.9
        assert d["transferFee"] == 0.02
        assert d["otherFees"] == 0.1

    def test_from_dict_required_only(self):
        fees = TradeFees.from_dict({"commission": 3.5})
        assert fees.commission == 3.5
        assert fees.stamp_duty is None

    def test_from_dict_all_fields(self):
        fees = TradeFees.from_dict({
            "commission": 5.0,
            "stampDuty": 0.9,
            "transferFee": 0.02,
            "otherFees": 0.1,
        })
        assert fees.stamp_duty == 0.9
        assert fees.transfer_fee == 0.02
        assert fees.other_fees == 0.1

    def test_roundtrip(self):
        original = TradeFees(commission=5.0, stamp_duty=0.9)
        restored = TradeFees.from_dict(original.to_dict())
        assert restored.commission == original.commission
        assert restored.stamp_duty == original.stamp_duty
        assert restored.transfer_fee is None

    def test_from_dict_missing_commission_raises(self):
        with pytest.raises(KeyError):
            TradeFees.from_dict({})

    def test_zero_commission(self):
        fees = TradeFees(commission=0.0)
        assert fees.to_dict() == {"commission": 0.0}

    def test_stamp_duty_zero_vs_none(self):
        """Zero stamp_duty should appear in dict; None should not."""
        fees_zero = TradeFees(commission=1.0, stamp_duty=0.0)
        fees_none = TradeFees(commission=1.0, stamp_duty=None)
        assert "stampDuty" in fees_zero.to_dict()
        assert "stampDuty" not in fees_none.to_dict()


# ── UnifiedTradeRecord ──────────────────────────────────────────


class TestUnifiedTradeRecord:
    def test_to_dict_required_fields(self):
        trade = _make_trade()
        d = trade.to_dict()
        assert d["tradeId"] == "t-001"
        assert d["source"] == "live"
        assert d["symbol"] == "600519.SH"
        assert d["entryPrice"] == 1800.0
        assert "exitTime" not in d
        assert "fees" not in d

    def test_to_dict_optional_fields(self):
        trade = _make_trade(
            name="贵州茅台",
            exit_time="2025-06-02T15:00:00+08:00",
            exit_price=1850.0,
            holding_days=1,
            strategy_id="strat-01",
            entry_order_id="ord-e1",
            exit_order_id="ord-x1",
            backtest_id="bt-001",
            signal={"type": "entry", "strength": 0.9},
            fees=TradeFees(commission=5.0, stamp_duty=0.9),
        )
        d = trade.to_dict()
        assert d["name"] == "贵州茅台"
        assert d["exitTime"] == "2025-06-02T15:00:00+08:00"
        assert d["exitPrice"] == 1850.0
        assert d["holdingDays"] == 1
        assert d["strategyId"] == "strat-01"
        assert d["entryOrderId"] == "ord-e1"
        assert d["exitOrderId"] == "ord-x1"
        assert d["backtestId"] == "bt-001"
        assert d["signal"]["strength"] == 0.9
        assert d["fees"]["commission"] == 5.0
        assert d["fees"]["stampDuty"] == 0.9

    def test_from_dict_required_only(self):
        trade = UnifiedTradeRecord.from_dict(_make_trade_dict())
        assert trade.trade_id == "t-001"
        assert trade.source is TradeSource.LIVE
        assert trade.symbol == "600519.SH"
        assert trade.exit_time is None
        assert trade.fees is None

    def test_from_dict_with_fees(self):
        d = _make_trade_dict(fees={"commission": 5.0, "stampDuty": 0.9})
        trade = UnifiedTradeRecord.from_dict(d)
        assert trade.fees is not None
        assert trade.fees.commission == 5.0
        assert trade.fees.stamp_duty == 0.9

    def test_from_dict_fees_none(self):
        d = _make_trade_dict()
        d["fees"] = None
        trade = UnifiedTradeRecord.from_dict(d)
        assert trade.fees is None

    def test_roundtrip(self):
        original = _make_trade(
            name="test",
            exit_time="2025-06-02T15:00:00+08:00",
            exit_price=1850.0,
            holding_days=3,
            fees=TradeFees(commission=5.0, stamp_duty=0.9, transfer_fee=0.02),
        )
        restored = UnifiedTradeRecord.from_dict(original.to_dict())
        assert restored.trade_id == original.trade_id
        assert restored.source == original.source
        assert restored.exit_price == original.exit_price
        assert restored.fees.stamp_duty == original.fees.stamp_duty

    def test_from_dict_missing_required_field_raises(self):
        d = _make_trade_dict()
        del d["tradeId"]
        with pytest.raises(KeyError):
            UnifiedTradeRecord.from_dict(d)

    def test_from_dict_invalid_source_raises(self):
        d = _make_trade_dict(source="unknown")
        with pytest.raises(ValueError):
            UnifiedTradeRecord.from_dict(d)

    def test_backtest_source(self):
        trade = _make_trade(source=TradeSource.BACKTEST, backtest_id="bt-1")
        d = trade.to_dict()
        assert d["source"] == "backtest"
        assert d["backtestId"] == "bt-1"

    def test_paper_source(self):
        trade = _make_trade(source=TradeSource.PAPER)
        assert trade.to_dict()["source"] == "paper"

    def test_negative_pnl(self):
        trade = _make_trade(gross_pnl=-200.0, net_pnl=-205.0)
        d = trade.to_dict()
        assert d["grossPnl"] == -200.0
        assert d["netPnl"] == -205.0

    def test_zero_quantity(self):
        trade = _make_trade(quantity=0.0)
        assert trade.to_dict()["quantity"] == 0.0


# ── AlignmentConfig ─────────────────────────────────────────────


class TestAlignmentConfig:
    def test_defaults(self):
        cfg = AlignmentConfig()
        assert cfg.time_tolerance_sec == 86400.0
        assert cfg.symbol_match_mode is SymbolMatchMode.NORMALIZED
        assert cfg.require_same_direction is True
        assert cfg.one_to_one is True
        assert cfg.group_split_fills is False
        assert cfg.split_fill_window_sec == 60.0
        assert cfg.timezone == "Asia/Shanghai"
        assert cfg.max_price_deviation_pct is None
        assert cfg.max_quantity_deviation_pct is None
        assert cfg.date_range_start is None
        assert cfg.date_range_end is None
        assert cfg.market_filter == []
        assert cfg.min_match_quality is None
        assert cfg.strategy_ids == []
        assert cfg.symbol_aliases == {}

    def test_to_dict_defaults(self):
        d = AlignmentConfig().to_dict()
        assert d["timeToleranceSec"] == 86400.0
        assert d["symbolMatchMode"] == "normalized"
        assert d["requireSameDirection"] is True
        assert d["oneToOne"] is True
        # Default timezone should NOT appear
        assert "timezone" not in d
        # Empty collections should NOT appear
        assert "symbolAliases" not in d
        assert "strategyIds" not in d
        assert "groupSplitFills" not in d
        assert "dateRange" not in d
        assert "marketFilter" not in d
        assert "minMatchQuality" not in d

    def test_to_dict_with_all_options(self):
        cfg = AlignmentConfig(
            time_tolerance_sec=3600.0,
            symbol_match_mode=SymbolMatchMode.FUZZY,
            symbol_aliases={"600519": ["600519.SH", "600519.SS"]},
            require_same_direction=False,
            max_price_deviation_pct=0.05,
            max_quantity_deviation_pct=0.1,
            one_to_one=False,
            strategy_ids=["strat-01"],
            group_split_fills=True,
            split_fill_window_sec=120.0,
            timezone="US/Eastern",
            date_range_start="2025-01-01",
            date_range_end="2025-12-31",
            market_filter=["A_SHARE"],
            min_match_quality=MatchQuality.CLOSE,
        )
        d = cfg.to_dict()
        assert d["timeToleranceSec"] == 3600.0
        assert d["symbolMatchMode"] == "fuzzy"
        assert d["symbolAliases"] == {"600519": ["600519.SH", "600519.SS"]}
        assert d["requireSameDirection"] is False
        assert d["maxPriceDeviationPct"] == 0.05
        assert d["maxQuantityDeviationPct"] == 0.1
        assert d["oneToOne"] is False
        assert d["strategyIds"] == ["strat-01"]
        assert d["groupSplitFills"] is True
        assert d["splitFillWindowSec"] == 120.0
        assert d["timezone"] == "US/Eastern"
        assert d["dateRange"] == {"startDate": "2025-01-01", "endDate": "2025-12-31"}
        assert d["marketFilter"] == ["A_SHARE"]
        assert d["minMatchQuality"] == "close"

    def test_to_dict_date_range_start_only(self):
        cfg = AlignmentConfig(date_range_start="2025-01-01")
        d = cfg.to_dict()
        assert d["dateRange"] == {"startDate": "2025-01-01"}
        assert "endDate" not in d["dateRange"]

    def test_to_dict_date_range_end_only(self):
        cfg = AlignmentConfig(date_range_end="2025-12-31")
        d = cfg.to_dict()
        assert d["dateRange"] == {"endDate": "2025-12-31"}

    def test_from_dict_defaults(self):
        cfg = AlignmentConfig.from_dict({})
        assert cfg.time_tolerance_sec == 86400
        assert cfg.symbol_match_mode is SymbolMatchMode.NORMALIZED
        assert cfg.timezone == "Asia/Shanghai"

    def test_from_dict_full(self):
        d = {
            "timeToleranceSec": 300,
            "symbolMatchMode": "exact",
            "symbolAliases": {"AAPL": ["AAPL.US"]},
            "requireSameDirection": False,
            "maxPriceDeviationPct": 0.02,
            "maxQuantityDeviationPct": 0.05,
            "oneToOne": False,
            "strategyIds": ["s1", "s2"],
            "groupSplitFills": True,
            "splitFillWindowSec": 30,
            "timezone": "UTC",
            "dateRange": {"startDate": "2025-01-01", "endDate": "2025-06-30"},
            "marketFilter": ["A_SHARE", "HK"],
            "minMatchQuality": "exact",
        }
        cfg = AlignmentConfig.from_dict(d)
        assert cfg.time_tolerance_sec == 300
        assert cfg.symbol_match_mode is SymbolMatchMode.EXACT
        assert cfg.symbol_aliases == {"AAPL": ["AAPL.US"]}
        assert cfg.require_same_direction is False
        assert cfg.max_price_deviation_pct == 0.02
        assert cfg.one_to_one is False
        assert cfg.group_split_fills is True
        assert cfg.split_fill_window_sec == 30
        assert cfg.timezone == "UTC"
        assert cfg.date_range_start == "2025-01-01"
        assert cfg.date_range_end == "2025-06-30"
        assert cfg.market_filter == ["A_SHARE", "HK"]
        assert cfg.min_match_quality is MatchQuality.EXACT

    def test_roundtrip(self):
        original = AlignmentConfig(
            time_tolerance_sec=7200,
            symbol_match_mode=SymbolMatchMode.FUZZY,
            symbol_aliases={"600519": ["600519.SH"]},
            group_split_fills=True,
            split_fill_window_sec=90,
            timezone="US/Eastern",
            date_range_start="2025-03-01",
            min_match_quality=MatchQuality.CLOSE,
        )
        restored = AlignmentConfig.from_dict(original.to_dict())
        assert restored.time_tolerance_sec == original.time_tolerance_sec
        assert restored.symbol_match_mode == original.symbol_match_mode
        assert restored.symbol_aliases == original.symbol_aliases
        assert restored.group_split_fills == original.group_split_fills
        assert restored.timezone == original.timezone
        assert restored.date_range_start == original.date_range_start
        assert restored.min_match_quality == original.min_match_quality

    def test_from_dict_invalid_symbol_match_mode(self):
        with pytest.raises(ValueError):
            AlignmentConfig.from_dict({"symbolMatchMode": "invalid"})

    def test_from_dict_invalid_min_match_quality(self):
        with pytest.raises(ValueError):
            AlignmentConfig.from_dict({"minMatchQuality": "invalid"})

    def test_from_dict_empty_date_range(self):
        cfg = AlignmentConfig.from_dict({"dateRange": {}})
        assert cfg.date_range_start is None
        assert cfg.date_range_end is None

    def test_from_dict_no_date_range_key(self):
        cfg = AlignmentConfig.from_dict({})
        assert cfg.date_range_start is None
        assert cfg.date_range_end is None

    def test_split_fill_window_not_in_dict_when_disabled(self):
        cfg = AlignmentConfig(group_split_fills=False, split_fill_window_sec=999)
        d = cfg.to_dict()
        assert "splitFillWindowSec" not in d
        assert "groupSplitFills" not in d

    def test_zero_time_tolerance(self):
        cfg = AlignmentConfig(time_tolerance_sec=0)
        assert cfg.to_dict()["timeToleranceSec"] == 0


# ── PairDeviation ───────────────────────────────────────────────


class TestPairDeviation:
    def test_to_dict_required_only(self):
        dev = PairDeviation(
            entry_time_delta_sec=120.0,
            entry_price_deviation_pct=0.01,
            quantity_deviation_pct=0.0,
            pnl_delta=50.0,
        )
        d = dev.to_dict()
        assert d["entryTimeDeltaSec"] == 120.0
        assert d["entryPriceDeviationPct"] == 0.01
        assert d["quantityDeviationPct"] == 0.0
        assert d["pnlDelta"] == 50.0
        assert "exitTimeDeltaSec" not in d
        assert "slippagePct" not in d

    def test_to_dict_all_fields(self):
        dev = PairDeviation(
            entry_time_delta_sec=60.0,
            entry_price_deviation_pct=0.005,
            quantity_deviation_pct=0.02,
            pnl_delta=-10.0,
            exit_time_delta_sec=30.0,
            exit_price_deviation_pct=0.003,
            pnl_deviation_pct=0.05,
            slippage_pct=0.001,
        )
        d = dev.to_dict()
        assert d["exitTimeDeltaSec"] == 30.0
        assert d["exitPriceDeviationPct"] == 0.003
        assert d["pnlDeviationPct"] == 0.05
        assert d["slippagePct"] == 0.001

    def test_from_dict(self):
        d = {
            "entryTimeDeltaSec": 300,
            "entryPriceDeviationPct": 0.02,
            "quantityDeviationPct": 0.1,
            "pnlDelta": 100,
        }
        dev = PairDeviation.from_dict(d)
        assert dev.entry_time_delta_sec == 300
        assert dev.exit_time_delta_sec is None

    def test_roundtrip(self):
        original = PairDeviation(
            entry_time_delta_sec=45,
            entry_price_deviation_pct=-0.01,
            quantity_deviation_pct=0.0,
            pnl_delta=-5,
            slippage_pct=0.002,
        )
        restored = PairDeviation.from_dict(original.to_dict())
        assert restored.entry_time_delta_sec == original.entry_time_delta_sec
        assert restored.slippage_pct == original.slippage_pct
        assert restored.exit_time_delta_sec is None

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(KeyError):
            PairDeviation.from_dict({"entryTimeDeltaSec": 0})

    def test_negative_values(self):
        dev = PairDeviation(
            entry_time_delta_sec=-60.0,
            entry_price_deviation_pct=-0.05,
            quantity_deviation_pct=-0.1,
            pnl_delta=-100.0,
        )
        d = dev.to_dict()
        assert d["entryTimeDeltaSec"] == -60.0
        assert d["pnlDelta"] == -100.0


# ── PairedTrade ─────────────────────────────────────────────────


class TestPairedTrade:
    def test_matched_pair(self):
        live = _make_trade(trade_id="t-live-1")
        bt = _make_trade(trade_id="t-bt-1", source=TradeSource.BACKTEST)
        dev = PairDeviation(
            entry_time_delta_sec=60,
            entry_price_deviation_pct=0.01,
            quantity_deviation_pct=0.0,
            pnl_delta=10,
        )
        pair = PairedTrade(
            pair_id="p-001",
            match_quality=MatchQuality.EXACT,
            matched_symbol="600519",
            live_trade=live,
            backtest_trade=bt,
            deviation=dev,
        )
        d = pair.to_dict()
        assert d["pairId"] == "p-001"
        assert d["matchQuality"] == "exact"
        assert d["matchedSymbol"] == "600519"
        assert d["liveTrade"]["tradeId"] == "t-live-1"
        assert d["backtestTrade"]["tradeId"] == "t-bt-1"
        assert d["deviation"]["entryTimeDeltaSec"] == 60
        assert "groupId" not in d

    def test_unmatched_live_only(self):
        pair = PairedTrade(
            pair_id="p-002",
            match_quality=MatchQuality.UNMATCHED,
            matched_symbol="000001",
            live_trade=_make_trade(trade_id="t-live-2", symbol="000001.SZ"),
        )
        d = pair.to_dict()
        assert d["matchQuality"] == "unmatched"
        assert "backtestTrade" not in d
        assert "deviation" not in d

    def test_unmatched_backtest_only(self):
        pair = PairedTrade(
            pair_id="p-003",
            match_quality=MatchQuality.UNMATCHED,
            matched_symbol="AAPL",
            backtest_trade=_make_trade(trade_id="t-bt-3", source=TradeSource.BACKTEST),
        )
        d = pair.to_dict()
        assert "liveTrade" not in d
        assert d["backtestTrade"]["tradeId"] == "t-bt-3"

    def test_with_group_id(self):
        pair = PairedTrade(
            pair_id="p-004",
            match_quality=MatchQuality.CLOSE,
            matched_symbol="600519",
            group_id="grp-001",
        )
        d = pair.to_dict()
        assert d["groupId"] == "grp-001"

    def test_from_dict_full(self):
        d = {
            "pairId": "p-010",
            "matchQuality": "close",
            "matchedSymbol": "600519",
            "liveTrade": _make_trade_dict(tradeId="t-l"),
            "backtestTrade": _make_trade_dict(tradeId="t-b", source="backtest"),
            "deviation": {
                "entryTimeDeltaSec": 10,
                "entryPriceDeviationPct": 0.001,
                "quantityDeviationPct": 0.0,
                "pnlDelta": 5,
            },
            "groupId": "g-1",
        }
        pair = PairedTrade.from_dict(d)
        assert pair.pair_id == "p-010"
        assert pair.match_quality is MatchQuality.CLOSE
        assert pair.live_trade.trade_id == "t-l"
        assert pair.backtest_trade.source is TradeSource.BACKTEST
        assert pair.deviation.entry_time_delta_sec == 10
        assert pair.group_id == "g-1"

    def test_from_dict_minimal(self):
        d = {
            "pairId": "p-011",
            "matchQuality": "unmatched",
            "matchedSymbol": "000001",
        }
        pair = PairedTrade.from_dict(d)
        assert pair.live_trade is None
        assert pair.backtest_trade is None
        assert pair.deviation is None
        assert pair.group_id is None

    def test_roundtrip(self):
        original = PairedTrade(
            pair_id="p-rt",
            match_quality=MatchQuality.LOOSE,
            matched_symbol="AAPL",
            live_trade=_make_trade(trade_id="t-rt-l", symbol="AAPL.US"),
            deviation=PairDeviation(
                entry_time_delta_sec=0,
                entry_price_deviation_pct=0.0,
                quantity_deviation_pct=0.0,
                pnl_delta=0,
            ),
        )
        restored = PairedTrade.from_dict(original.to_dict())
        assert restored.pair_id == original.pair_id
        assert restored.match_quality == original.match_quality
        assert restored.live_trade.trade_id == "t-rt-l"
        assert restored.backtest_trade is None

    def test_from_dict_missing_pair_id_raises(self):
        with pytest.raises(KeyError):
            PairedTrade.from_dict({"matchQuality": "exact", "matchedSymbol": "X"})

    def test_from_dict_invalid_match_quality_raises(self):
        with pytest.raises(ValueError):
            PairedTrade.from_dict({
                "pairId": "p-err",
                "matchQuality": "super",
                "matchedSymbol": "X",
            })


# ── AlignmentSummary ────────────────────────────────────────────


class TestAlignmentSummary:
    def _make_summary(self, **overrides):
        defaults = dict(
            total_live_trades=10,
            total_backtest_trades=12,
            paired_count=8,
            unmatched_live_count=2,
            unmatched_backtest_count=4,
            quality_distribution={"exact": 5, "close": 2, "loose": 1},
            avg_entry_time_delta_sec=300.0,
            avg_entry_price_deviation_pct=0.02,
            live_total_pnl=5000.0,
            backtest_total_pnl=5500.0,
            pnl_gap=-500.0,
            match_rate=0.667,
            live_total_fees=100.0,
            backtest_total_fees=0.0,
        )
        defaults.update(overrides)
        return AlignmentSummary(**defaults)

    def test_to_dict(self):
        s = self._make_summary()
        d = s.to_dict()
        assert d["totalLiveTrades"] == 10
        assert d["pairedCount"] == 8
        assert d["pnlGap"] == -500.0
        assert d["matchRate"] == 0.667
        assert "avgSlippagePct" not in d

    def test_to_dict_with_slippage(self):
        s = self._make_summary(avg_slippage_pct=0.003)
        d = s.to_dict()
        assert d["avgSlippagePct"] == 0.003

    def test_from_dict(self):
        d = {
            "totalLiveTrades": 5,
            "totalBacktestTrades": 5,
            "pairedCount": 4,
            "unmatchedLiveCount": 1,
            "unmatchedBacktestCount": 1,
            "qualityDistribution": {"exact": 4},
            "avgEntryTimeDeltaSec": 10.0,
            "avgEntryPriceDeviationPct": 0.001,
            "liveTotalPnl": 1000.0,
            "backtestTotalPnl": 1100.0,
            "pnlGap": -100.0,
            "matchRate": 0.8,
            "liveTotalFees": 50.0,
            "backtestTotalFees": 0.0,
        }
        s = AlignmentSummary.from_dict(d)
        assert s.total_live_trades == 5
        assert s.paired_count == 4
        assert s.avg_slippage_pct is None

    def test_roundtrip(self):
        original = self._make_summary(avg_slippage_pct=0.01)
        restored = AlignmentSummary.from_dict(original.to_dict())
        assert restored.total_live_trades == original.total_live_trades
        assert restored.pnl_gap == original.pnl_gap
        assert restored.avg_slippage_pct == original.avg_slippage_pct

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(KeyError):
            AlignmentSummary.from_dict({"totalLiveTrades": 1})

    def test_zero_trades(self):
        s = self._make_summary(
            total_live_trades=0,
            total_backtest_trades=0,
            paired_count=0,
            unmatched_live_count=0,
            unmatched_backtest_count=0,
            quality_distribution={},
            avg_entry_time_delta_sec=0.0,
            avg_entry_price_deviation_pct=0.0,
            live_total_pnl=0.0,
            backtest_total_pnl=0.0,
            pnl_gap=0.0,
            match_rate=0.0,
            live_total_fees=0.0,
            backtest_total_fees=0.0,
        )
        d = s.to_dict()
        assert d["pairedCount"] == 0
        assert d["matchRate"] == 0.0


# ── AlignmentResult ─────────────────────────────────────────────


class TestAlignmentResult:
    def _make_result(self, **overrides):
        defaults = dict(
            run_id="run-001",
            config=AlignmentConfig(),
            pairs=[],
            summary=AlignmentSummary(
                total_live_trades=0, total_backtest_trades=0, paired_count=0,
                unmatched_live_count=0, unmatched_backtest_count=0,
                quality_distribution={}, avg_entry_time_delta_sec=0,
                avg_entry_price_deviation_pct=0, live_total_pnl=0,
                backtest_total_pnl=0, pnl_gap=0, match_rate=0,
                live_total_fees=0, backtest_total_fees=0,
            ),
            aligned_at="2025-06-01T16:00:00+08:00",
        )
        defaults.update(overrides)
        return AlignmentResult(**defaults)

    def test_to_dict_minimal(self):
        r = self._make_result()
        d = r.to_dict()
        assert d["runId"] == "run-001"
        assert d["alignedAt"] == "2025-06-01T16:00:00+08:00"
        assert d["pairs"] == []
        assert "strategyId" not in d
        assert "config" in d
        assert "summary" in d

    def test_to_dict_with_strategy_id(self):
        r = self._make_result(strategy_id="strat-01")
        d = r.to_dict()
        assert d["strategyId"] == "strat-01"

    def test_to_dict_with_pairs(self):
        pair = PairedTrade(
            pair_id="p-1",
            match_quality=MatchQuality.EXACT,
            matched_symbol="600519",
            live_trade=_make_trade(),
        )
        r = self._make_result(pairs=[pair])
        d = r.to_dict()
        assert len(d["pairs"]) == 1
        assert d["pairs"][0]["pairId"] == "p-1"

    def test_from_dict(self):
        d = {
            "runId": "run-002",
            "config": AlignmentConfig().to_dict(),
            "pairs": [],
            "summary": AlignmentSummary(
                total_live_trades=0, total_backtest_trades=0, paired_count=0,
                unmatched_live_count=0, unmatched_backtest_count=0,
                quality_distribution={}, avg_entry_time_delta_sec=0,
                avg_entry_price_deviation_pct=0, live_total_pnl=0,
                backtest_total_pnl=0, pnl_gap=0, match_rate=0,
                live_total_fees=0, backtest_total_fees=0,
            ).to_dict(),
            "alignedAt": "2025-06-01T16:00:00+08:00",
            "strategyId": "s1",
        }
        r = AlignmentResult.from_dict(d)
        assert r.run_id == "run-002"
        assert r.strategy_id == "s1"
        assert isinstance(r.config, AlignmentConfig)
        assert isinstance(r.summary, AlignmentSummary)

    def test_roundtrip(self):
        pair = PairedTrade(
            pair_id="p-rt",
            match_quality=MatchQuality.CLOSE,
            matched_symbol="000001",
            live_trade=_make_trade(trade_id="t-l", symbol="000001.SZ"),
        )
        original = self._make_result(
            pairs=[pair],
            strategy_id="strat-rt",
        )
        restored = AlignmentResult.from_dict(original.to_dict())
        assert restored.run_id == original.run_id
        assert restored.strategy_id == original.strategy_id
        assert len(restored.pairs) == 1
        assert restored.pairs[0].pair_id == "p-rt"
        assert restored.pairs[0].live_trade.trade_id == "t-l"

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(KeyError):
            AlignmentResult.from_dict({"runId": "x"})


# ── IAlignmentEngine (normalize_symbol / symbols_match) ─────────


class TestIAlignmentEngineHelpers:
    @pytest.fixture
    def engine(self):
        return _StubEngine()

    # normalize_symbol
    @pytest.mark.parametrize("symbol,expected", [
        ("600519.SH", "600519"),
        ("000001.SZ", "000001"),
        ("600519.SS", "600519"),
        ("AAPL.US", "AAPL"),
        ("0700.HK", "0700"),
        ("688001.BJ", "688001"),
        ("600519", "600519"),
        ("AAPL", "AAPL"),
    ])
    def test_normalize_symbol(self, engine, symbol, expected):
        assert engine.normalize_symbol(symbol) == expected

    def test_normalize_preserves_case_prefix(self, engine):
        """Only the suffix is stripped; the body retains original casing."""
        assert engine.normalize_symbol("aapl.US") == "aapl"

    def test_normalize_unknown_suffix(self, engine):
        assert engine.normalize_symbol("600519.XX") == "600519.XX"

    def test_normalize_case_insensitive_suffix(self, engine):
        """Suffixes are matched case-insensitively via .upper()."""
        assert engine.normalize_symbol("600519.sh") == "600519"
        assert engine.normalize_symbol("AAPL.us") == "AAPL"

    def test_normalize_empty_string(self, engine):
        assert engine.normalize_symbol("") == ""

    def test_normalize_only_suffix(self, engine):
        """Edge case: symbol is just a suffix like '.SH'."""
        assert engine.normalize_symbol(".SH") == ""

    # symbols_match — EXACT mode
    def test_exact_match_identical(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.EXACT)
        assert engine.symbols_match("600519.SH", "600519.SH", cfg) is True

    def test_exact_match_different_suffix(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.EXACT)
        assert engine.symbols_match("600519.SH", "600519.SS", cfg) is False

    def test_exact_match_no_suffix_vs_suffix(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.EXACT)
        assert engine.symbols_match("600519", "600519.SH", cfg) is False

    # symbols_match — NORMALIZED mode
    def test_normalized_strips_suffix(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.NORMALIZED)
        assert engine.symbols_match("600519.SH", "600519.SS", cfg) is True

    def test_normalized_same_bare(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.NORMALIZED)
        assert engine.symbols_match("600519", "600519", cfg) is True

    def test_normalized_different_symbols(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.NORMALIZED)
        assert engine.symbols_match("600519.SH", "000001.SZ", cfg) is False

    def test_normalized_bare_vs_suffix(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.NORMALIZED)
        assert engine.symbols_match("600519", "600519.SH", cfg) is True

    # symbols_match — FUZZY mode
    def test_fuzzy_normalized_match(self, engine):
        cfg = AlignmentConfig(symbol_match_mode=SymbolMatchMode.FUZZY)
        assert engine.symbols_match("600519.SH", "600519.SS", cfg) is True

    def test_fuzzy_alias_match(self, engine):
        cfg = AlignmentConfig(
            symbol_match_mode=SymbolMatchMode.FUZZY,
            symbol_aliases={"600519": ["600519.SH", "600519.SS"]},
        )
        assert engine.symbols_match("600519.SH", "600519.SS", cfg) is True

    def test_fuzzy_alias_with_canonical(self, engine):
        """Canonical key itself should be in the alias set."""
        cfg = AlignmentConfig(
            symbol_match_mode=SymbolMatchMode.FUZZY,
            symbol_aliases={"AAPL": ["AAPL.US", "AAPL.O"]},
        )
        # AAPL (canonical, normalized) should match AAPL.US
        assert engine.symbols_match("AAPL", "AAPL.US", cfg) is True

    def test_fuzzy_no_alias_no_normalized_match(self, engine):
        cfg = AlignmentConfig(
            symbol_match_mode=SymbolMatchMode.FUZZY,
            symbol_aliases={},
        )
        assert engine.symbols_match("600519.SH", "000001.SZ", cfg) is False

    def test_fuzzy_alias_cross_group_no_match(self, engine):
        """Symbols from different alias groups should not match."""
        cfg = AlignmentConfig(
            symbol_match_mode=SymbolMatchMode.FUZZY,
            symbol_aliases={
                "600519": ["600519.SH"],
                "000001": ["000001.SZ"],
            },
        )
        assert engine.symbols_match("600519.SH", "000001.SZ", cfg) is False

    # IAlignmentEngine abstract method
    def test_align_not_implemented(self, engine):
        with pytest.raises(NotImplementedError):
            engine.align([], [], AlignmentConfig())
