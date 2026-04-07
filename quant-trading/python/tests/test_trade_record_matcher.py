"""
Tests for trade_record_matcher.py — symbol normalization and grouping.
"""

import pytest

from engines.trade_record_matcher import (
    MARKET_CN,
    MARKET_FUND,
    MARKET_HK,
    MARKET_US,
    MARKET_UNKNOWN,
    SymbolGroup,
    TradeRecord,
    detect_market,
    group_records_by_symbol,
    normalize_symbol,
    trade_record_from_dict,
)


# ── normalize_symbol tests ───────────────────────────────────────


class TestNormalizeSymbol:
    """Test symbol normalization across different markets and formats."""

    # A-share: prefixed formats
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("sh600519", "600519"),
            ("SH600519", "600519"),
            ("sz000001", "000001"),
            ("SZ300750", "300750"),
        ],
    )
    def test_cn_prefixed(self, raw, expected):
        assert normalize_symbol(raw) == expected

    # A-share: suffixed formats
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("600519.SH", "600519"),
            ("600519.SS", "600519"),
            ("000001.SZ", "000001"),
            ("300750.sz", "300750"),
        ],
    )
    def test_cn_suffixed(self, raw, expected):
        assert normalize_symbol(raw) == expected

    # A-share: bare 6-digit codes
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("600519", "600519"),
            ("000001", "000001"),
            ("300750", "300750"),
        ],
    )
    def test_cn_bare(self, raw, expected):
        assert normalize_symbol(raw) == expected

    # HK: various formats
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("00700.HK", "00700"),
            ("0700.HK", "00700"),
            ("00700.hk", "00700"),
            ("HK00700", "00700"),
            ("HK0700", "00700"),
        ],
    )
    def test_hk_formats(self, raw, expected):
        assert normalize_symbol(raw) == expected

    # US: various formats
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("AAPL", "AAPL"),
            ("AAPL.US", "AAPL"),
            ("aapl.us", "AAPL"),
            ("US_AAPL", "AAPL"),
            ("US.AAPL", "AAPL"),
            ("TSLA", "TSLA"),
        ],
    )
    def test_us_formats(self, raw, expected):
        assert normalize_symbol(raw) == expected

    def test_empty_string(self):
        assert normalize_symbol("") == ""

    def test_whitespace_trimming(self):
        assert normalize_symbol("  sh600519  ") == "600519"

    def test_different_formats_same_stock(self):
        """All representations of Moutai should normalize to the same code."""
        variants = ["sh600519", "SH600519", "600519.SH", "600519.SS", "600519"]
        normalized = {normalize_symbol(v) for v in variants}
        assert len(normalized) == 1
        assert "600519" in normalized

    def test_different_formats_same_hk_stock(self):
        """All representations of Tencent should normalize to the same code."""
        variants = ["00700.HK", "0700.HK", "HK00700", "HK0700"]
        normalized = {normalize_symbol(v) for v in variants}
        assert len(normalized) == 1
        assert "00700" in normalized


# ── detect_market tests ──────────────────────────────────────────


class TestDetectMarket:
    # A-share stocks
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("sh600519", MARKET_CN),
            ("600519.SH", MARKET_CN),
            ("600519", MARKET_CN),
            ("000001", MARKET_CN),
            ("300750", MARKET_CN),
            ("688001", MARKET_CN),   # STAR market (68xxxx)
        ],
    )
    def test_cn_stock(self, raw, expected):
        assert detect_market(raw) == expected

    # Funds
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("510050", MARKET_FUND),   # 上证50ETF
            ("159919", MARKET_FUND),   # 沪深300ETF
            ("161725", MARKET_FUND),   # 白酒LOF
        ],
    )
    def test_fund(self, raw, expected):
        assert detect_market(raw) == expected

    # HK
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("00700.HK", MARKET_HK),
            ("HK00700", MARKET_HK),
        ],
    )
    def test_hk(self, raw, expected):
        assert detect_market(raw) == expected

    # US
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("AAPL", MARKET_US),
            ("AAPL.US", MARKET_US),
            ("US_AAPL", MARKET_US),
            ("TSLA", MARKET_US),
        ],
    )
    def test_us(self, raw, expected):
        assert detect_market(raw) == expected

    def test_empty(self):
        assert detect_market("") == MARKET_UNKNOWN


# ── group_records_by_symbol tests ────────────────────────────────


def _make_record(symbol: str, source: str, trade_id: str = "",
                 entry_time: str = "2025-01-01T09:30:00") -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id or f"t-{symbol}-{source}",
        symbol=symbol,
        side="buy",
        entry_time=entry_time,
        source=source,
    )


class TestGroupRecordsBySymbol:
    def test_basic_grouping(self):
        records = [
            _make_record("sh600519", "live"),
            _make_record("600519.SH", "backtest"),
            _make_record("600519", "live"),
        ]
        groups = group_records_by_symbol(records)
        assert len(groups) == 1
        assert "600519" in groups
        grp = groups["600519"]
        assert len(grp.live_records) == 2
        assert len(grp.backtest_records) == 1
        assert grp.total_count == 3

    def test_multiple_symbols(self):
        records = [
            _make_record("sh600519", "live"),
            _make_record("sz000001", "backtest"),
            _make_record("AAPL", "live"),
            _make_record("00700.HK", "backtest"),
        ]
        groups = group_records_by_symbol(records)
        assert len(groups) == 4
        assert "600519" in groups
        assert "000001" in groups
        assert "AAPL" in groups
        assert "00700" in groups

    def test_hk_normalization_groups(self):
        records = [
            _make_record("00700.HK", "live"),
            _make_record("HK00700", "backtest"),
            _make_record("0700.HK", "live"),
        ]
        groups = group_records_by_symbol(records)
        assert len(groups) == 1
        assert "00700" in groups
        grp = groups["00700"]
        assert grp.market == MARKET_HK
        assert len(grp.live_records) == 2
        assert len(grp.backtest_records) == 1

    def test_us_normalization_groups(self):
        records = [
            _make_record("AAPL", "live"),
            _make_record("AAPL.US", "backtest"),
            _make_record("US_AAPL", "live"),
        ]
        groups = group_records_by_symbol(records)
        assert len(groups) == 1
        assert "AAPL" in groups
        grp = groups["AAPL"]
        assert grp.market == MARKET_US

    def test_records_sorted_by_entry_time(self):
        records = [
            _make_record("600519", "live", entry_time="2025-01-03T09:30:00"),
            _make_record("sh600519", "live", entry_time="2025-01-01T09:30:00"),
            _make_record("600519.SH", "live", entry_time="2025-01-02T09:30:00"),
        ]
        groups = group_records_by_symbol(records)
        grp = groups["600519"]
        times = [r.entry_time for r in grp.live_records]
        assert times == sorted(times)

    def test_empty_records(self):
        groups = group_records_by_symbol([])
        assert groups == {}

    def test_normalized_symbol_populated(self):
        rec = _make_record("sh600519", "live")
        group_records_by_symbol([rec])
        assert rec.normalized_symbol == "600519"

    def test_market_auto_detected(self):
        rec = _make_record("sh600519", "live")
        rec.market = ""
        group_records_by_symbol([rec])
        assert rec.market == MARKET_CN


# ── trade_record_from_dict tests ─────────────────────────────────


class TestTradeRecordFromDict:
    def test_camelcase_keys(self):
        d = {
            "tradeId": "t-001",
            "symbol": "sh600519",
            "side": "buy",
            "entryTime": "2025-01-01T09:30:00",
            "exitTime": "2025-01-02T15:00:00",
            "entryPrice": 1800.0,
            "exitPrice": 1850.0,
            "quantity": 100,
            "grossPnl": 5000.0,
            "commission": 10.0,
            "netPnl": 4990.0,
            "holdingDays": 1,
            "strategyId": "dual-ma",
        }
        rec = trade_record_from_dict(d, source="live")
        assert rec.trade_id == "t-001"
        assert rec.entry_price == 1800.0
        assert rec.exit_price == 1850.0
        assert rec.holding_days == 1
        assert rec.source == "live"

    def test_snakecase_keys(self):
        d = {
            "trade_id": "t-002",
            "symbol": "AAPL",
            "side": "sell",
            "entry_time": "2025-03-01T14:30:00",
            "entry_price": 180.0,
            "quantity": 50,
        }
        rec = trade_record_from_dict(d, source="backtest")
        assert rec.trade_id == "t-002"
        assert rec.source == "backtest"
        assert rec.exit_time is None
        assert rec.exit_price is None
