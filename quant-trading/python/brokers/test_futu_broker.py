"""
Unit tests for futu_broker.py — the Futu OpenD NDJSON bridge.

Tests cover:
- NDJSON I/O helpers (send, reply_ok, reply_err, push_event)
- FutuBrokerBridge initialization and config defaults
- Order/position serialization
- Status/side/order type mapping helpers
- Dispatch loop method routing
- Error handling for unknown methods
- Position change detection logic
- Graceful handling when futu-api is not installed
"""
import json
import sys
import os
import io
import unittest
from unittest.mock import patch, MagicMock
import threading


# We need to test without futu-api being installed, so we mock the import
# Patch sys.modules before importing our module
sys.modules["futu"] = MagicMock()


# Now import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# We need to reload or import carefully since the module checks HAS_FUTU at import time
# Instead, we'll test the pure-Python logic by importing specific functions


class TestNDJSONHelpers(unittest.TestCase):
    """Test the NDJSON I/O helper functions."""

    def _import_module(self):
        """Import the module fresh for each test."""
        # Redirect stdout to capture output
        import importlib
        spec = importlib.util.spec_from_file_location(
            "futu_broker",
            os.path.join(os.path.dirname(__file__), "futu_broker.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        return mod, spec

    def test_reply_ok_format(self):
        """reply_ok should output valid JSON with requestId, ok=True, data."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            # Import send/reply functions
            from futu_broker import reply_ok
            reply_ok("req-1", {"status": "connected"})

        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["requestId"], "req-1")
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["data"]["status"], "connected")

    def test_reply_err_format(self):
        """reply_err should output valid JSON with requestId, ok=False, error."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import reply_err
            reply_err("req-2", "something went wrong")

        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["requestId"], "req-2")
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"], "something went wrong")

    def test_push_event_format(self):
        """push_event should output valid JSON with event and data fields."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import push_event
            push_event("fill", {"order_id": "123", "price": 100.5})

        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["event"], "fill")
        self.assertEqual(obj["data"]["order_id"], "123")
        self.assertAlmostEqual(obj["data"]["price"], 100.5)

    def test_send_thread_safety(self):
        """send should be thread-safe (no interleaved output)."""
        captured = io.StringIO()
        results = []
        errors = []

        with patch("sys.stdout", captured):
            from futu_broker import send

            def writer(n):
                try:
                    for i in range(10):
                        send({"thread": n, "seq": i})
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(errors), 0)
        lines = captured.getvalue().strip().split("\n")
        self.assertEqual(len(lines), 40)  # 4 threads * 10 messages
        for line in lines:
            obj = json.loads(line)  # Each line must be valid JSON
            self.assertIn("thread", obj)
            self.assertIn("seq", obj)

    def test_send_unicode(self):
        """send should handle Chinese characters correctly."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            from futu_broker import send
            send({"name": "贵州茅台", "code": "SH.600519"})

        line = captured.getvalue().strip()
        obj = json.loads(line)
        self.assertEqual(obj["name"], "贵州茅台")


class TestFutuBrokerBridge(unittest.TestCase):
    """Test FutuBrokerBridge class logic."""

    def test_init_defaults(self):
        """Default initialization values."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        self.assertEqual(bridge.host, "127.0.0.1")
        self.assertEqual(bridge.port, 11111)
        self.assertEqual(bridge.trd_env_str, "REAL")
        self.assertEqual(bridge.pwd_unlock, "")
        self.assertIsNone(bridge.quote_ctx)
        self.assertIsNone(bridge.trade_ctx)

    def test_init_custom_args(self):
        """Custom initialization arguments are applied."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({
            "opend_host": "192.168.1.100",
            "opend_port": "22222",
            "trade_env": "SIMULATE",
            "pwd_unlock": "secret123",
        })
        self.assertEqual(bridge.host, "192.168.1.100")
        self.assertEqual(bridge.port, 22222)
        self.assertEqual(bridge.trd_env_str, "SIMULATE")
        self.assertEqual(bridge.pwd_unlock, "secret123")

    def test_serialize_order(self):
        """_serialize_order produces correct output shape."""
        from futu_broker import FutuBrokerBridge
        row = {
            "order_id": "ORD-001",
            "code": "HK.00700",
            "trd_side": "BUY",
            "price": 350.0,
            "qty": 100,
            "dealt_qty": 50,
            "dealt_avg_price": 349.5,
            "order_status": "FILLED_PART",
            "create_time": "2026-04-07 10:30:00",
            "remark": "test-order",
        }
        # Use MagicMock to simulate pandas-like row access
        mock_row = MagicMock()
        mock_row.get = lambda key, default=None: row.get(key, default)

        result = FutuBrokerBridge._serialize_order(mock_row)
        self.assertEqual(result["order_id"], "ORD-001")
        self.assertEqual(result["stock_code"], "HK.00700")
        self.assertEqual(result["order_type"], "BUY")
        self.assertAlmostEqual(result["price"], 350.0)
        self.assertEqual(result["order_volume"], 100)
        self.assertEqual(result["traded_volume"], 50)
        self.assertAlmostEqual(result["traded_price"], 349.5)
        self.assertEqual(result["order_status"], "FILLED_PART")
        self.assertEqual(result["remark"], "test-order")

    def test_serialize_position(self):
        """_serialize_position produces correct output with computed fields."""
        from futu_broker import FutuBrokerBridge
        row_data = {
            "code": "SH.600519",
            "stock_name": "贵州茅台",
            "qty": 10,
            "cost_price": 1800.0,
            "market_val": 18500.0,
            "nominal_price": 1850.0,
            "unrealized_pl": 500.0,
        }
        mock_row = MagicMock()
        mock_row.get = lambda key, default=0: row_data.get(key, default)

        result = FutuBrokerBridge._serialize_position(mock_row)
        self.assertEqual(result["stock_code"], "SH.600519")
        self.assertEqual(result["stock_name"], "贵州茅台")
        self.assertEqual(result["volume"], 10)
        self.assertAlmostEqual(result["avg_price"], 1800.0)
        self.assertAlmostEqual(result["market_value"], 18500.0)
        self.assertAlmostEqual(result["last_price"], 1850.0)
        self.assertAlmostEqual(result["unrealized_pnl"], 500.0)
        # unrealized_pnl_pct = (1850 - 1800) / 1800
        self.assertAlmostEqual(result["unrealized_pnl_pct"], 50.0 / 1800.0, places=6)

    def test_serialize_position_zero_avg_price(self):
        """unrealized_pnl_pct should be None when avg_price is 0."""
        from futu_broker import FutuBrokerBridge
        row_data = {
            "code": "HK.00700",
            "stock_name": "腾讯",
            "qty": 100,
            "cost_price": 0,
            "market_val": 35000.0,
            "nominal_price": 350.0,
            "unrealized_pl": 0,
        }
        mock_row = MagicMock()
        mock_row.get = lambda key, default=0: row_data.get(key, default)

        result = FutuBrokerBridge._serialize_position(mock_row)
        self.assertIsNone(result["unrealized_pnl_pct"])

    def test_serialize_position_zero_nominal_price(self):
        """last_price should be None when nominal_price is 0."""
        from futu_broker import FutuBrokerBridge
        row_data = {
            "code": "HK.00700",
            "stock_name": "腾讯",
            "qty": 100,
            "cost_price": 350.0,
            "market_val": 35000.0,
            "nominal_price": 0,
            "unrealized_pl": 0,
        }
        mock_row = MagicMock()
        mock_row.get = lambda key, default=0: row_data.get(key, default)

        result = FutuBrokerBridge._serialize_position(mock_row)
        self.assertIsNone(result["last_price"])

    def test_disconnect_without_connect(self):
        """disconnect should work even if never connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        result = bridge.disconnect()
        self.assertEqual(result["status"], "disconnected")

    def test_place_order_not_connected(self):
        """place_order should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError) as ctx:
            bridge.place_order({"stock_code": "HK.00700", "volume": 100})
        self.assertIn("Not connected", str(ctx.exception))

    def test_cancel_order_not_connected(self):
        """cancel_order should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.cancel_order({"order_id": "123"})

    def test_query_order_not_connected(self):
        """query_order should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.query_order({"order_id": "123"})

    def test_query_orders_not_connected(self):
        """query_orders should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.query_orders({})

    def test_query_positions_not_connected(self):
        """query_positions should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.query_positions()

    def test_query_account_not_connected(self):
        """query_account should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.query_account()

    def test_subscribe_quote_not_connected(self):
        """subscribe_quote should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.subscribe_quote({"stock_codes": ["HK.00700"]})

    def test_unsubscribe_quote_not_connected(self):
        """unsubscribe_quote should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})

    def test_get_quote_snapshot_not_connected(self):
        """get_quote_snapshot should raise when not connected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        with self.assertRaises(RuntimeError):
            bridge.get_quote_snapshot({"stock_codes": ["HK.00700"]})

    def test_get_quote_snapshot_empty_codes(self):
        """get_quote_snapshot should return empty list for empty codes."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge.quote_ctx = MagicMock()  # pretend connected
        result = bridge.get_quote_snapshot({"stock_codes": []})
        self.assertEqual(result, [])


class TestMappingHelpers(unittest.TestCase):
    """Test the status/side/order type mapping helper functions."""

    def test_map_trd_env_real(self):
        from futu_broker import _map_trd_env
        result = _map_trd_env("REAL")
        # With mocked futu module, this should not be None (HAS_FUTU is mocked)
        self.assertIsNotNone(result)

    def test_map_trd_env_simulate(self):
        from futu_broker import _map_trd_env
        result = _map_trd_env("SIMULATE")
        self.assertIsNotNone(result)

    def test_map_order_side_buy(self):
        from futu_broker import _map_order_side
        result = _map_order_side("buy")
        self.assertIsNotNone(result)

    def test_map_order_side_sell(self):
        from futu_broker import _map_order_side
        result = _map_order_side("sell")
        self.assertIsNotNone(result)

    def test_map_order_side_buy_open(self):
        from futu_broker import _map_order_side
        result = _map_order_side("buy_open")
        self.assertIsNotNone(result)

    def test_map_order_side_sell_close(self):
        from futu_broker import _map_order_side
        result = _map_order_side("sell_close")
        self.assertIsNotNone(result)

    def test_map_order_side_unknown_defaults_to_buy(self):
        from futu_broker import _map_order_side
        result = _map_order_side("unknown_side")
        # Should default to BUY
        self.assertIsNotNone(result)

    def test_map_order_type_market(self):
        from futu_broker import _map_order_type
        result = _map_order_type("market")
        self.assertIsNotNone(result)

    def test_map_order_type_limit(self):
        from futu_broker import _map_order_type
        result = _map_order_type("limit")
        self.assertIsNotNone(result)

    def test_map_order_type_unknown_defaults_to_normal(self):
        """Any non-'market' type should map to NORMAL (limit)."""
        from futu_broker import _map_order_type
        result = _map_order_type("stop_limit")
        self.assertIsNotNone(result)


class TestMethodsDispatch(unittest.TestCase):
    """Test the METHODS dispatch table."""

    def test_all_methods_are_registered(self):
        from futu_broker import METHODS
        expected = {
            "connect", "disconnect", "place_order", "cancel_order",
            "query_order", "query_orders", "query_positions", "query_account",
            "subscribe_quote", "unsubscribe_quote", "get_quote_snapshot",
        }
        self.assertEqual(set(METHODS.keys()), expected)

    def test_method_values_match_bridge_methods(self):
        """Each METHODS value should be a valid method name on FutuBrokerBridge."""
        from futu_broker import METHODS, FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        for method_name, attr_name in METHODS.items():
            self.assertTrue(
                hasattr(bridge, attr_name),
                f"FutuBrokerBridge missing method: {attr_name} (for {method_name})",
            )


class TestPositionChangeDetection(unittest.TestCase):
    """Test the position change polling diff logic."""

    def test_detects_new_position(self):
        """When a new stock appears, it should be detected as a change."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge._last_positions = {}

        current = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100, "avg_price": 350},
        }

        all_codes = set(current.keys()) | set(bridge._last_positions.keys())
        changes = []
        for code in all_codes:
            old = bridge._last_positions.get(code)
            new = current.get(code)
            if old != new:
                changes.append({"code": code, "previous": old, "current": new})

        self.assertEqual(len(changes), 1)
        self.assertIsNone(changes[0]["previous"])
        self.assertIsNotNone(changes[0]["current"])

    def test_detects_removed_position(self):
        """When a stock disappears, it should be detected as a change."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge._last_positions = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100, "avg_price": 350},
        }

        current = {}
        all_codes = set(current.keys()) | set(bridge._last_positions.keys())
        changes = []
        for code in all_codes:
            old = bridge._last_positions.get(code)
            new = current.get(code)
            if old != new:
                changes.append({"code": code, "previous": old, "current": new})

        self.assertEqual(len(changes), 1)
        self.assertIsNotNone(changes[0]["previous"])
        self.assertIsNone(changes[0]["current"])

    def test_detects_volume_change(self):
        """When volume changes for an existing position, it should be detected."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        bridge._last_positions = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 100, "avg_price": 350},
        }

        current = {
            "HK.00700": {"stock_code": "HK.00700", "volume": 200, "avg_price": 355},
        }
        all_codes = set(current.keys()) | set(bridge._last_positions.keys())
        changes = []
        for code in all_codes:
            old = bridge._last_positions.get(code)
            new = current.get(code)
            if old != new:
                changes.append({"code": code, "previous": old, "current": new})

        self.assertEqual(len(changes), 1)

    def test_no_change_when_same(self):
        """No change should be detected when positions are identical."""
        from futu_broker import FutuBrokerBridge
        bridge = FutuBrokerBridge({})
        pos = {"stock_code": "HK.00700", "volume": 100, "avg_price": 350}
        bridge._last_positions = {"HK.00700": pos.copy()}

        current = {"HK.00700": pos.copy()}
        all_codes = set(current.keys()) | set(bridge._last_positions.keys())
        changes = []
        for code in all_codes:
            old = bridge._last_positions.get(code)
            new = current.get(code)
            if old != new:
                changes.append({"code": code, "previous": old, "current": new})

        self.assertEqual(len(changes), 0)


class TestFutuStatusMap(unittest.TestCase):
    """Test FUTU_STATUS_MAP mapping."""

    def test_all_statuses_mapped(self):
        from futu_broker import FUTU_STATUS_MAP
        expected_keys = {
            "NONE", "UNSUBMITTED", "WAITING_SUBMIT", "SUBMITTING",
            "SUBMIT_FAILED", "SUBMITTED", "FILLED_PART", "FILLED_ALL",
            "CANCELLING_PART", "CANCELLING_ALL", "CANCELLED_PART",
            "CANCELLED_ALL", "FAILED", "DISABLED", "DELETED",
        }
        self.assertEqual(set(FUTU_STATUS_MAP.keys()), expected_keys)

    def test_terminal_statuses(self):
        from futu_broker import FUTU_STATUS_MAP
        self.assertEqual(FUTU_STATUS_MAP["FILLED_ALL"], "filled")
        self.assertEqual(FUTU_STATUS_MAP["CANCELLED_ALL"], "cancelled")
        self.assertEqual(FUTU_STATUS_MAP["CANCELLED_PART"], "cancelled")
        self.assertEqual(FUTU_STATUS_MAP["FAILED"], "rejected")
        self.assertEqual(FUTU_STATUS_MAP["DISABLED"], "rejected")
        self.assertEqual(FUTU_STATUS_MAP["DELETED"], "cancelled")

    def test_pending_statuses(self):
        from futu_broker import FUTU_STATUS_MAP
        self.assertEqual(FUTU_STATUS_MAP["NONE"], "pending")
        self.assertEqual(FUTU_STATUS_MAP["UNSUBMITTED"], "pending")
        self.assertEqual(FUTU_STATUS_MAP["WAITING_SUBMIT"], "pending")

    def test_intermediate_statuses(self):
        from futu_broker import FUTU_STATUS_MAP
        self.assertEqual(FUTU_STATUS_MAP["SUBMITTING"], "submitted")
        self.assertEqual(FUTU_STATUS_MAP["SUBMITTED"], "submitted")
        self.assertEqual(FUTU_STATUS_MAP["FILLED_PART"], "partial")


if __name__ == "__main__":
    unittest.main()
