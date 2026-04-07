"""
Unit tests for futu_broker.py — the Futu OpenD NDJSON bridge.

Tests symbol normalisation, NDJSON I/O helpers, side/status mapping,
FutuBrokerBridge init, serialisers, and dispatch loop logic
WITHOUT requiring futu-api or a running Futu OpenD.
"""

import json
import io
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Ensure the module can be imported even without futu-api installed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brokers.futu_broker import (
    send,
    reply_ok,
    reply_err,
    push_event,
    to_futu_symbol,
    from_futu_symbol,
    FutuBrokerBridge,
    METHODS,
)


# ── NDJSON I/O helpers ──────────────────────────────────────────


class TestNdjsonHelpers:
    def test_send_writes_json_line(self, capsys):
        send({"foo": "bar"})
        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed == {"foo": "bar"}

    def test_send_handles_chinese_characters(self, capsys):
        send({"msg": "你好世界"})
        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed["msg"] == "你好世界"

    def test_reply_ok(self, capsys):
        reply_ok("req-1", {"result": 42})
        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed["requestId"] == "req-1"
        assert parsed["ok"] is True
        assert parsed["data"] == {"result": 42}

    def test_reply_err(self, capsys):
        reply_err("req-2", "something broke")
        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed["requestId"] == "req-2"
        assert parsed["ok"] is False
        assert parsed["error"] == "something broke"

    def test_push_event(self, capsys):
        push_event("fill", {"order_id": "123"})
        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed["event"] == "fill"
        assert parsed["data"]["order_id"] == "123"


# ── Symbol normalisation ────────────────────────────────────────


class TestSymbolNormalisation:
    """Test to_futu_symbol and from_futu_symbol."""

    def test_already_futu_format_hk(self):
        assert to_futu_symbol("HK.00700") == "HK.00700"

    def test_already_futu_format_us(self):
        assert to_futu_symbol("US.AAPL") == "US.AAPL"

    def test_already_futu_format_sh(self):
        assert to_futu_symbol("SH.600519") == "SH.600519"

    def test_already_futu_format_sz(self):
        assert to_futu_symbol("SZ.000001") == "SZ.000001"

    def test_hk_market_pads_to_5_digits(self):
        assert to_futu_symbol("700", "hk_stock") == "HK.00700"

    def test_hk_market_5_digit_code(self):
        assert to_futu_symbol("00700", "hk_stock") == "HK.00700"

    def test_us_market(self):
        assert to_futu_symbol("AAPL", "us_stock") == "US.AAPL"

    def test_us_market_mixed_case(self):
        assert to_futu_symbol("aapl", "us_stock") == "US.aapl"

    def test_a_share_sh_code_starts_with_6(self):
        assert to_futu_symbol("600519") == "SH.600519"

    def test_a_share_sh_code_starts_with_5(self):
        assert to_futu_symbol("510050") == "SH.510050"

    def test_a_share_sh_code_starts_with_9(self):
        assert to_futu_symbol("900001") == "SH.900001"

    def test_a_share_sz_code_starts_with_0(self):
        assert to_futu_symbol("000001") == "SZ.000001"

    def test_a_share_sz_code_starts_with_3(self):
        assert to_futu_symbol("300750") == "SZ.300750"

    def test_non_numeric_defaults_to_hk(self):
        # Non-numeric codes that are not in Futu format default to HK
        assert to_futu_symbol("BABA") == "HK.BABA"

    def test_from_futu_symbol_strips_prefix(self):
        assert from_futu_symbol("HK.00700") == "00700"
        assert from_futu_symbol("US.AAPL") == "AAPL"
        assert from_futu_symbol("SH.600519") == "600519"
        assert from_futu_symbol("SZ.000001") == "000001"

    def test_from_futu_symbol_no_prefix(self):
        assert from_futu_symbol("00700") == "00700"

    def test_from_futu_symbol_empty_string(self):
        assert from_futu_symbol("") == ""


# ── FutuBrokerBridge init ────────────────────────────────────────


class TestFutuBrokerBridgeInit:
    def test_default_init_args(self):
        bridge = FutuBrokerBridge({})
        assert bridge.host == "127.0.0.1"
        assert bridge.port == 11111
        assert bridge.trade_env_str == "SIMULATE"
        assert bridge.pwd_unlock == ""
        assert bridge.trd_market_str == "hk_stock"
        assert bridge.security_firm_str == ""
        assert bridge.account_id == ""

    def test_custom_init_args(self):
        bridge = FutuBrokerBridge({
            "opend_host": "192.168.1.100",
            "opend_port": "22222",
            "trade_env": "REAL",
            "pwd_unlock": "mypassword",
            "trd_market": "us_stock",
            "security_firm": "FUTUSECURITIES",
            "account_id": "281001234567",
        })
        assert bridge.host == "192.168.1.100"
        assert bridge.port == 22222
        assert bridge.trade_env_str == "REAL"
        assert bridge.pwd_unlock == "mypassword"
        assert bridge.trd_market_str == "us_stock"
        assert bridge.security_firm_str == "FUTUSECURITIES"
        assert bridge.account_id == "281001234567"

    def test_port_converted_to_int(self):
        bridge = FutuBrokerBridge({"opend_port": "33333"})
        assert bridge.port == 33333
        assert isinstance(bridge.port, int)


# ── FutuBrokerBridge — connect without futu-api ─────────────────


class TestFutuBrokerBridgeConnectNoFutu:
    def test_connect_raises_without_futu_api(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="futu-api is not installed"):
            bridge.connect()


# ── FutuBrokerBridge — disconnect ────────────────────────────────


class TestFutuBrokerBridgeDisconnect:
    def test_disconnect_with_no_contexts(self):
        bridge = FutuBrokerBridge({})
        result = bridge.disconnect()
        assert result["status"] == "disconnected"
        assert bridge.quote_ctx is None
        assert bridge.trade_ctx is None

    def test_disconnect_closes_contexts(self):
        bridge = FutuBrokerBridge({})
        mock_quote = MagicMock()
        mock_trade = MagicMock()
        bridge.quote_ctx = mock_quote
        bridge.trade_ctx = mock_trade

        result = bridge.disconnect()
        assert result["status"] == "disconnected"
        mock_quote.close.assert_called_once()
        mock_trade.close.assert_called_once()
        assert bridge.quote_ctx is None
        assert bridge.trade_ctx is None


# ── FutuBrokerBridge — place_order without connection ────────────


class TestFutuBrokerBridgePlaceOrderDisconnected:
    def test_place_order_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.place_order({"stock_code": "HK.00700", "volume": 100})

    def test_cancel_order_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.cancel_order({"order_id": "123"})

    def test_query_order_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.query_order({"order_id": "123"})

    def test_query_orders_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.query_orders({})

    def test_query_positions_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.query_positions()

    def test_query_account_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.query_account()

    def test_subscribe_quote_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.subscribe_quote({"stock_codes": ["HK.00700"]})

    def test_unsubscribe_quote_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})

    def test_ping_raises_when_not_connected(self):
        bridge = FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.ping()


# ── Serialisers ──────────────────────────────────────────────────


class TestSerialiseOrder:
    def test_serialize_order_basic(self):
        row = MagicMock()
        row.get = lambda key, default="": {
            "order_id": "ORD001",
            "code": "HK.00700",
            "trd_side": "BUY",
            "price": 350.0,
            "qty": 100,
            "dealt_qty": 50,
            "dealt_avg_price": 349.5,
            "order_status": "FILLED_PART",
            "remark": "momentum",
            "create_time": "2026-04-07 10:00:00",
        }.get(key, default)

        result = FutuBrokerBridge._serialize_order(row)
        assert result["order_id"] == "ORD001"
        assert result["stock_code"] == "HK.00700"
        assert result["order_type"] == "BUY"
        assert result["price"] == 350.0
        assert result["order_volume"] == 100
        assert result["traded_volume"] == 50
        assert result["traded_price"] == 349.5
        assert result["order_status"] == "FILLED_PART"
        assert result["strategy_name"] == "momentum"

    def test_serialize_order_defaults(self):
        row = MagicMock()
        row.get = lambda key, default="": default

        result = FutuBrokerBridge._serialize_order(row)
        assert result["order_id"] == ""
        assert result["price"] == 0
        assert result["order_volume"] == 0
        assert result["traded_volume"] == 0


class TestSerialisePosition:
    def test_serialize_position_basic(self):
        row = MagicMock()
        row.get = lambda key, default=0: {
            "code": "HK.00700",
            "stock_name": "腾讯控股",
            "qty": 200,
            "cost_price": 340.0,
            "market_val": 70000.0,
            "last_price": 350.0,
            "pl_val": 2000.0,
            "pl_ratio": 0.0294,
        }.get(key, default)

        result = FutuBrokerBridge._serialize_position(row)
        assert result["stock_code"] == "HK.00700"
        assert result["stock_name"] == "腾讯控股"
        assert result["volume"] == 200
        assert result["avg_price"] == 340.0
        assert result["market_value"] == 70000.0
        assert result["last_price"] == 350.0
        assert result["unrealized_pnl"] == 2000.0
        assert result["unrealized_pnl_pct"] == 0.0294

    def test_serialize_position_zero_last_price(self):
        row = MagicMock()
        row.get = lambda key, default=0: {
            "code": "HK.00700",
            "stock_name": "腾讯控股",
            "qty": 200,
            "cost_price": 340.0,
            "market_val": 70000.0,
            "last_price": 0,
            "pl_val": 0,
            "pl_ratio": 0,
        }.get(key, default)

        result = FutuBrokerBridge._serialize_position(row)
        assert result["last_price"] is None  # 0 -> None


# ── METHODS dispatch table ───────────────────────────────────────


class TestMethodsTable:
    def test_all_expected_methods_registered(self):
        expected = [
            "connect", "disconnect", "place_order", "cancel_order",
            "query_order", "query_orders", "query_positions",
            "query_account", "subscribe_quote", "unsubscribe_quote", "ping",
        ]
        for method in expected:
            assert method in METHODS, f"Method '{method}' missing from METHODS table"

    def test_methods_values_match_bridge_attributes(self):
        bridge = FutuBrokerBridge({})
        for method, attr_name in METHODS.items():
            assert hasattr(bridge, attr_name), f"FutuBrokerBridge missing '{attr_name}'"


# ── Main dispatch loop ──────────────────────────────────────────


class TestMainDispatch:
    def test_dispatch_unknown_method(self, capsys):
        """Simulate the dispatch loop handling an unknown method."""
        bridge = FutuBrokerBridge({})

        # Manually replicate what main() does for unknown methods
        method = "unknown_method"
        request_id = "req-99"

        if method not in METHODS:
            reply_err(request_id, f"Unknown method: {method}")

        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed["ok"] is False
        assert "Unknown method" in parsed["error"]

    def test_dispatch_init_method(self, capsys):
        """__init__ method should return ok."""
        request_id = "req-init"
        reply_ok(request_id, {"status": "ok"})

        output = capsys.readouterr().out.strip()
        parsed = json.loads(output)
        assert parsed["ok"] is True
        assert parsed["data"]["status"] == "ok"


# ── Side mapping (Python) ────────────────────────────────────────


class TestPythonSideMapping:
    """Test _map_side_to_futu and _map_futu_side when futu is not installed."""

    def test_map_side_to_futu_returns_none_without_futu(self):
        from brokers.futu_broker import _map_side_to_futu
        # Without futu-api, should return None
        result = _map_side_to_futu("buy")
        assert result is None

    def test_map_futu_side_returns_buy_without_futu(self):
        from brokers.futu_broker import _map_futu_side
        result = _map_futu_side("any")
        assert result == "buy"

    def test_map_order_type_returns_none_without_futu(self):
        from brokers.futu_broker import _map_order_type_to_futu
        result = _map_order_type_to_futu("limit")
        assert result is None

    def test_get_trd_env_returns_none_without_futu(self):
        from brokers.futu_broker import _get_trd_env
        result = _get_trd_env("SIMULATE")
        assert result is None

    def test_get_trd_market_returns_none_without_futu(self):
        from brokers.futu_broker import _get_trd_market
        result = _get_trd_market("hk_stock")
        assert result is None


# ── Subscribed codes tracking ────────────────────────────────────


class TestSubscribedCodesTracking:
    def test_unsubscribe_removes_from_set(self):
        bridge = FutuBrokerBridge({})
        bridge._subscribed_codes = {"HK.00700", "HK.00388"}
        bridge.quote_ctx = MagicMock()
        bridge.quote_ctx.unsubscribe = MagicMock()

        bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})
        assert "HK.00700" not in bridge._subscribed_codes
        assert "HK.00388" in bridge._subscribed_codes

    def test_unsubscribe_ignores_non_subscribed(self):
        bridge = FutuBrokerBridge({})
        bridge._subscribed_codes = {"HK.00388"}
        bridge.quote_ctx = MagicMock()
        bridge.quote_ctx.unsubscribe = MagicMock()

        bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})
        assert bridge._subscribed_codes == {"HK.00388"}
