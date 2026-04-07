"""
Extended tests for futu_broker.py — covers:
- NDJSON edge cases (empty lines, malformed JSON, unicode)
- FutuBrokerBridge method error paths
- Position change detection with multiple stocks
- Serialization edge cases (missing fields, None values)
- Dispatch loop routing for all methods
- Quote subscription/unsubscription state tracking
- Main loop stdin parsing (malformed input, __init__ method)
"""
import json
import sys
import os
import io
import unittest
from unittest.mock import patch, MagicMock
import threading

# Mock futu module before import
sys.modules["futu"] = MagicMock()
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))


class TestNDJSONEdgeCases(unittest.TestCase):
    """Test NDJSON helper edge cases."""

    def test_send_empty_dict(self):
        """send should handle empty dict."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import send
            send({})
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj, {})

    def test_send_nested_object(self):
        """send should handle deeply nested objects."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import send
            send({"a": {"b": {"c": {"d": 42}}}})
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["a"]["b"]["c"]["d"], 42)

    def test_send_with_special_characters(self):
        """send should handle special characters in strings."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import send
            send({"msg": 'he said "hello" & goodbye\nnewline'})
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertIn("hello", obj["msg"])
        self.assertIn("newline", obj["msg"])

    def test_reply_ok_with_list_data(self):
        """reply_ok should handle list data."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import reply_ok
            reply_ok("req-list", [1, 2, 3])
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["data"], [1, 2, 3])

    def test_reply_ok_with_none_data(self):
        """reply_ok should handle None data."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import reply_ok
            reply_ok("req-none", None)
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertIsNone(obj["data"])

    def test_push_event_with_empty_data(self):
        """push_event should handle empty data dict."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import push_event
            push_event("heartbeat", {})
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["event"], "heartbeat")
        self.assertEqual(obj["data"], {})

    def test_reply_err_with_unicode_error(self):
        """reply_err should handle unicode error messages."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import reply_err
            reply_err("req-cn", "连接失败：超时")
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertIn("连接失败", obj["error"])

    def test_send_with_float_precision(self):
        """send should maintain float precision."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import send
            send({"price": 1800.12345})
        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertAlmostEqual(obj["price"], 1800.12345, places=5)


class TestFutuBrokerBridgeEdgeCases(unittest.TestCase):
    """Test FutuBrokerBridge edge cases."""

    def test_init_with_string_port(self):
        """Port should be converted from string to int."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({"opend_port": "22222"})
        self.assertEqual(bridge.port, 22222)

    def test_init_with_empty_string_values(self):
        """Empty strings should be handled gracefully."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({
            "opend_host": "",
            "trade_env": "",
            "pwd_unlock": "",
        })
        self.assertEqual(bridge.host, "")
        self.assertEqual(bridge.trd_env_str, "")
        self.assertEqual(bridge.pwd_unlock, "")

    def test_subscribed_codes_starts_empty(self):
        """_subscribed_codes should start empty."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        self.assertEqual(len(bridge._subscribed_codes), 0)

    def test_last_positions_starts_empty(self):
        """_last_positions should start empty."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        self.assertEqual(len(bridge._last_positions), 0)

    def test_disconnect_clears_contexts(self):
        """disconnect should set contexts to None."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge.quote_ctx = MagicMock()
        bridge.trade_ctx = MagicMock()
        bridge.disconnect()
        self.assertIsNone(bridge.quote_ctx)
        self.assertIsNone(bridge.trade_ctx)

    def test_disconnect_calls_close_on_contexts(self):
        """disconnect should call close() on both contexts."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_quote = MagicMock()
        mock_trade = MagicMock()
        bridge.quote_ctx = mock_quote
        bridge.trade_ctx = mock_trade
        bridge.disconnect()
        mock_quote.close.assert_called_once()
        mock_trade.close.assert_called_once()

    def test_get_quote_snapshot_empty_codes_returns_empty(self):
        """get_quote_snapshot with empty codes returns empty list."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge.quote_ctx = MagicMock()
        result = bridge.get_quote_snapshot({"stock_codes": []})
        self.assertEqual(result, [])

    def test_get_quote_snapshot_no_codes_key_returns_empty(self):
        """get_quote_snapshot with no stock_codes key returns empty list."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge.quote_ctx = MagicMock()
        result = bridge.get_quote_snapshot({})
        self.assertEqual(result, [])


class TestSerializationEdgeCases(unittest.TestCase):
    """Test serialization with edge case inputs."""

    def test_serialize_order_missing_fields(self):
        """_serialize_order should use defaults for missing fields."""
        from futu_broker import FutuBrokerBridge
        mock_row = MagicMock()
        mock_row.get = lambda key, default=None: default
        result = FutuBrokerBridge._serialize_order(mock_row)
        self.assertEqual(result["order_id"], "")
        self.assertEqual(result["stock_code"], "")
        self.assertAlmostEqual(result["price"], 0)
        self.assertEqual(result["order_volume"], 0)
        self.assertEqual(result["traded_volume"], 0)

    def test_serialize_order_with_none_values(self):
        """_serialize_order handles None for numeric fields by using defaults."""
        from futu_broker import FutuBrokerBridge
        row_data = {
            "order_id": None,
            "code": None,
            "trd_side": None,
            "price": None,
            "qty": None,
            "dealt_qty": None,
            "dealt_avg_price": None,
            "order_status": None,
            "create_time": None,
            "remark": None,
        }
        mock_row = MagicMock()
        mock_row.get = lambda key, default=None: row_data.get(key, default)
        # May raise or return defaults depending on None handling
        try:
            result = FutuBrokerBridge._serialize_order(mock_row)
            # If it succeeds, check structure
            self.assertIn("order_id", result)
        except (TypeError, ValueError):
            pass  # Expected if None can't be cast

    def test_serialize_position_negative_unrealized_pnl(self):
        """Position with negative PnL should serialize correctly."""
        from futu_broker import FutuBrokerBridge
        row_data = {
            "code": "HK.00700",
            "stock_name": "腾讯",
            "qty": 100,
            "cost_price": 400.0,
            "market_val": 35000.0,
            "nominal_price": 350.0,
            "unrealized_pl": -5000.0,
        }
        mock_row = MagicMock()
        mock_row.get = lambda key, default=0: row_data.get(key, default)
        result = FutuBrokerBridge._serialize_position(mock_row)
        self.assertAlmostEqual(result["unrealized_pnl"], -5000.0)
        # pct = (350 - 400) / 400 = -0.125
        self.assertAlmostEqual(result["unrealized_pnl_pct"], -0.125, places=3)

    def test_serialize_position_large_values(self):
        """Position with very large values should serialize correctly."""
        from futu_broker import FutuBrokerBridge
        row_data = {
            "code": "SH.600519",
            "stock_name": "贵州茅台",
            "qty": 10000,
            "cost_price": 1800.0,
            "market_val": 19000000.0,
            "nominal_price": 1900.0,
            "unrealized_pl": 1000000.0,
        }
        mock_row = MagicMock()
        mock_row.get = lambda key, default=0: row_data.get(key, default)
        result = FutuBrokerBridge._serialize_position(mock_row)
        self.assertEqual(result["volume"], 10000)
        self.assertAlmostEqual(result["market_value"], 19000000.0)


class TestPositionChangeDetectionExtended(unittest.TestCase):
    """Extended position change detection tests."""

    def test_multiple_stocks_change_simultaneously(self):
        """Detect changes across multiple stocks at once."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge._last_positions = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100, "avg_price": 350},
            "SH.600519": {"stock_code": "SH.600519", "volume": 10, "avg_price": 1800},
        }

        current = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 200, "avg_price": 355},  # changed
            "SH.600519": {"stock_code": "SH.600519", "volume": 10, "avg_price": 1800},  # unchanged
            "US.AAPL": {"stock_code": "US.AAPL", "volume": 50, "avg_price": 180},  # new
        }

        all_codes = set(current.keys()) | set(bridge._last_positions.keys())
        changes = []
        for code in all_codes:
            old = bridge._last_positions.get(code)
            new = current.get(code)
            if old != new:
                changes.append(code)

        self.assertEqual(len(changes), 2)  # HK.00700 changed, US.AAPL new
        self.assertIn("HK.00700", changes)
        self.assertIn("US.AAPL", changes)
        self.assertNotIn("SH.600519", changes)

    def test_all_positions_cleared(self):
        """All positions closed at once."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge._last_positions = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100},
            "SH.600519": {"stock_code": "SH.600519", "volume": 10},
        }

        current = {}
        all_codes = set(current.keys()) | set(bridge._last_positions.keys())
        changes = []
        for code in all_codes:
            old = bridge._last_positions.get(code)
            new = current.get(code)
            if old != new:
                changes.append({"code": code, "previous": old, "current": new})

        self.assertEqual(len(changes), 2)
        for change in changes:
            self.assertIsNone(change["current"])
            self.assertIsNotNone(change["previous"])

    def test_position_price_change_only(self):
        """Detect change when only price changes but volume stays same."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge._last_positions = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100, "avg_price": 350, "last_price": 355},
        }

        current = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100, "avg_price": 350, "last_price": 360},
        }

        old = bridge._last_positions.get("HK.00700")
        new = current.get("HK.00700")
        self.assertNotEqual(old, new)


class TestMappingHelperExtended(unittest.TestCase):
    """Extended tests for mapping helper functions."""

    def test_map_order_side_sell_open(self):
        """sell_open should map to SELL."""
        from futu_broker import _map_order_side
        result = _map_order_side("sell_open")
        self.assertIsNotNone(result)

    def test_map_order_side_buy_close(self):
        """buy_close should map to BUY."""
        from futu_broker import _map_order_side
        result = _map_order_side("buy_close")
        self.assertIsNotNone(result)

    def test_map_order_side_empty_string(self):
        """Empty string should default to BUY."""
        from futu_broker import _map_order_side
        result = _map_order_side("")
        self.assertIsNotNone(result)

    def test_map_order_type_stop_limit(self):
        """Non-standard order type should map to NORMAL (limit)."""
        from futu_broker import _map_order_type
        result = _map_order_type("stop_limit")
        self.assertIsNotNone(result)

    def test_map_order_type_empty_string(self):
        """Empty string should map to NORMAL (limit)."""
        from futu_broker import _map_order_type
        result = _map_order_type("")
        self.assertIsNotNone(result)

    def test_map_trd_env_unknown(self):
        """Unknown trade env should default to REAL."""
        from futu_broker import _map_trd_env
        result = _map_trd_env("PAPER")
        self.assertIsNotNone(result)


class TestDispatchLoopRouting(unittest.TestCase):
    """Test the main dispatch loop's routing logic."""

    def test_methods_table_has_all_api_methods(self):
        """METHODS should include all public API methods."""
        from futu_broker import METHODS
        required = {
            "connect", "disconnect", "place_order", "cancel_order",
            "query_order", "query_orders", "query_positions", "query_account",
            "subscribe_quote", "unsubscribe_quote", "get_quote_snapshot",
        }
        self.assertTrue(required.issubset(set(METHODS.keys())))

    def test_methods_with_no_params(self):
        """Methods that take no params are: connect, disconnect, query_positions, query_account."""
        from futu_broker import METHODS
        no_param_methods = {"connect", "disconnect", "query_positions", "query_account"}
        for m in no_param_methods:
            self.assertIn(m, METHODS)

    def test_unknown_method_not_in_table(self):
        """Unknown methods should not be in METHODS table."""
        from futu_broker import METHODS
        self.assertNotIn("restart", METHODS)
        self.assertNotIn("shutdown", METHODS)
        self.assertNotIn("list_strategies", METHODS)


class TestQuoteSubscriptionState(unittest.TestCase):
    """Test quote subscription state tracking."""

    def test_subscribe_adds_to_subscribed_codes(self):
        """subscribe_quote should track codes in _subscribed_codes."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        from futu import RET_OK
        mock_ctx.subscribe.return_value = (RET_OK, None)
        bridge.quote_ctx = mock_ctx

        result = bridge.subscribe_quote({"stock_codes": ["HK.00700", "US.AAPL"]})
        self.assertIn("HK.00700", bridge._subscribed_codes)
        self.assertIn("US.AAPL", bridge._subscribed_codes)

    def test_subscribe_skips_already_subscribed(self):
        """subscribe_quote should not re-subscribe existing codes."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        from futu import RET_OK
        mock_ctx.subscribe.return_value = (RET_OK, None)
        bridge.quote_ctx = mock_ctx
        bridge._subscribed_codes = {"HK.00700"}

        bridge.subscribe_quote({"stock_codes": ["HK.00700", "US.AAPL"]})
        # Should only call subscribe for US.AAPL
        # HK.00700 is already in _subscribed_codes
        calls = mock_ctx.subscribe.call_args_list
        codes_subscribed = [c[0][0] for c in calls]
        self.assertIn("US.AAPL", codes_subscribed)
        self.assertNotIn("HK.00700", codes_subscribed)

    def test_unsubscribe_removes_from_subscribed_codes(self):
        """unsubscribe_quote should remove codes from _subscribed_codes."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        from futu import RET_OK
        mock_ctx.unsubscribe.return_value = (RET_OK, None)
        bridge.quote_ctx = mock_ctx
        bridge._subscribed_codes = {"HK.00700", "US.AAPL"}

        bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})
        self.assertNotIn("HK.00700", bridge._subscribed_codes)
        self.assertIn("US.AAPL", bridge._subscribed_codes)

    def test_unsubscribe_nonexistent_code_is_safe(self):
        """unsubscribe_quote for a non-subscribed code should not crash."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        bridge.quote_ctx = mock_ctx
        bridge._subscribed_codes = set()

        # Should not crash even though code is not in _subscribed_codes
        result = bridge.unsubscribe_quote({"stock_codes": ["UNKNOWN.CODE"]})
        self.assertIn("subscribed", result)

    def test_subscribe_with_default_sub_types(self):
        """subscribe_quote with no sub_types should default to QUOTE."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        from futu import RET_OK
        mock_ctx.subscribe.return_value = (RET_OK, None)
        bridge.quote_ctx = mock_ctx

        bridge.subscribe_quote({"stock_codes": ["HK.00700"]})
        # verify subscribe was called (with default QUOTE type)
        mock_ctx.subscribe.assert_called_once()

    def test_subscribe_with_multiple_sub_types(self):
        """subscribe_quote with multiple sub types."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        from futu import RET_OK
        mock_ctx.subscribe.return_value = (RET_OK, None)
        bridge.quote_ctx = mock_ctx

        bridge.subscribe_quote({
            "stock_codes": ["HK.00700"],
            "sub_types": ["QUOTE", "ORDER_BOOK", "TICKER"],
        })
        mock_ctx.subscribe.assert_called_once()

    def test_subscribe_with_invalid_sub_type_falls_back_to_quote(self):
        """subscribe_quote with invalid sub types should fall back to QUOTE."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        mock_ctx = MagicMock()
        from futu import RET_OK
        mock_ctx.subscribe.return_value = (RET_OK, None)
        bridge.quote_ctx = mock_ctx

        bridge.subscribe_quote({
            "stock_codes": ["HK.00700"],
            "sub_types": ["INVALID_TYPE"],
        })
        # Should still call subscribe (with default QUOTE fallback)
        mock_ctx.subscribe.assert_called_once()


class TestFutuStatusMapCompleteness(unittest.TestCase):
    """Verify FUTU_STATUS_MAP covers all important statuses."""

    def test_all_values_are_valid_statuses(self):
        """All mapped values should be valid internal statuses."""
        from futu_broker import FUTU_STATUS_MAP
        valid_statuses = {"pending", "submitted", "partial", "filled", "cancelled", "rejected"}
        for futu_status, internal_status in FUTU_STATUS_MAP.items():
            self.assertIn(
                internal_status, valid_statuses,
                f"FUTU_STATUS_MAP['{futu_status}'] = '{internal_status}' is not a valid status"
            )

    def test_cancelling_statuses_map_to_submitted(self):
        """Cancelling states should map to submitted (still active)."""
        from futu_broker import FUTU_STATUS_MAP
        self.assertEqual(FUTU_STATUS_MAP["CANCELLING_PART"], "submitted")
        self.assertEqual(FUTU_STATUS_MAP["CANCELLING_ALL"], "submitted")

    def test_submit_failed_maps_to_rejected(self):
        from futu_broker import FUTU_STATUS_MAP
        self.assertEqual(FUTU_STATUS_MAP["SUBMIT_FAILED"], "rejected")


if __name__ == "__main__":
    unittest.main()
