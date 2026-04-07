"""
Unit tests for futu_broker.py — Futu OpenD NDJSON bridge.

Tests the FutuBrokerBridge class methods, NDJSON I/O helpers,
status/side mapping, and dispatch logic without requiring futu-api
or a live OpenD connection.
"""

import json
import os
import sys
import io
import threading
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure the brokers package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# We need to mock futu before importing futu_broker
# so that HAS_FUTU=False path is testable as well.


# ── NDJSON helpers ────────────────────────────────────────────────


class TestNDJSONHelpers:
    """Test the send/reply_ok/reply_err/push_event functions."""

    def _capture_stdout(self, func, *args):
        """Capture stdout output from a function call."""
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            func(*args)
        return buf.getvalue().strip()

    def test_reply_ok(self):
        # Import with futu mocked out
        with mock.patch.dict("sys.modules", {"futu": None}):
            # Force reimport
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            from brokers.futu_broker import reply_ok

            output = self._capture_stdout(reply_ok, "req-1", {"status": "ok"})
            parsed = json.loads(output)
            assert parsed["requestId"] == "req-1"
            assert parsed["ok"] is True
            assert parsed["data"]["status"] == "ok"

    def test_reply_err(self):
        with mock.patch.dict("sys.modules", {"futu": None}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            from brokers.futu_broker import reply_err

            output = self._capture_stdout(reply_err, "req-2", "something broke")
            parsed = json.loads(output)
            assert parsed["requestId"] == "req-2"
            assert parsed["ok"] is False
            assert parsed["error"] == "something broke"

    def test_push_event(self):
        with mock.patch.dict("sys.modules", {"futu": None}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            from brokers.futu_broker import push_event

            output = self._capture_stdout(push_event, "fill", {"order_id": "123"})
            parsed = json.loads(output)
            assert parsed["event"] == "fill"
            assert parsed["data"]["order_id"] == "123"

    def test_send_handles_unicode(self):
        with mock.patch.dict("sys.modules", {"futu": None}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            from brokers.futu_broker import send

            output = self._capture_stdout(send, {"msg": "腾讯控股"})
            parsed = json.loads(output)
            assert parsed["msg"] == "腾讯控股"


# ── Status / side mapping (without futu-api) ─────────────────────


class TestMappingWithoutFutu:
    """Test status and side mapping when futu-api is NOT installed."""

    @pytest.fixture(autouse=True)
    def _reload_module(self):
        """Force reimport with futu mocked as unavailable."""
        with mock.patch.dict("sys.modules", {"futu": None}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            import brokers.futu_broker as mod
            self.mod = mod
            yield
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]

    def test_has_futu_is_false(self):
        assert self.mod.HAS_FUTU is False

    def test_map_futu_status_defaults_to_submitted(self):
        assert self.mod.map_futu_status("FILLED_ALL") == "submitted"
        assert self.mod.map_futu_status("UNKNOWN") == "submitted"
        assert self.mod.map_futu_status("") == "submitted"

    def test_map_side_to_futu_passthrough(self):
        assert self.mod.map_side_to_futu("buy") == "buy"
        assert self.mod.map_side_to_futu("sell") == "sell"

    def test_map_futu_side_defaults_to_buy(self):
        assert self.mod.map_futu_side("anything") == "buy"

    def test_get_trd_env_returns_none(self):
        assert self.mod.get_trd_env("REAL") is None
        assert self.mod.get_trd_env("SIMULATE") is None


# ── FutuBrokerBridge (no futu-api) ────────────────────────────────


class TestFutuBrokerBridgeNoFutu:
    """Test FutuBrokerBridge initialization and connect failure without futu-api."""

    @pytest.fixture(autouse=True)
    def _reload_module(self):
        with mock.patch.dict("sys.modules", {"futu": None}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            import brokers.futu_broker as mod
            self.mod = mod
            yield
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]

    def test_init_defaults(self):
        bridge = self.mod.FutuBrokerBridge({})
        assert bridge.host == "127.0.0.1"
        assert bridge.port == 11111
        assert bridge.trade_env_str == "REAL"
        assert bridge.pwd_unlock == ""
        assert bridge._connected is False

    def test_init_custom_args(self):
        bridge = self.mod.FutuBrokerBridge({
            "opend_host": "192.168.1.100",
            "opend_port": 22222,
            "trade_env": "SIMULATE",
            "pwd_unlock": "secret",
            "market_quote_type": 2,
        })
        assert bridge.host == "192.168.1.100"
        assert bridge.port == 22222
        assert bridge.trade_env_str == "SIMULATE"
        assert bridge.pwd_unlock == "secret"
        assert bridge.market_quote_type == 2

    def test_connect_raises_without_futu(self):
        bridge = self.mod.FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="futu-api is not installed"):
            bridge.connect()

    def test_disconnect_when_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        result = bridge.disconnect()
        assert result["status"] == "disconnected"
        assert bridge._connected is False

    def test_ping_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        result = bridge.ping()
        assert result["ok"] is False

    def test_query_account_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.query_account()

    def test_query_positions_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.query_positions()

    def test_place_order_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.place_order({"stock_code": "HK.00700", "volume": 100})

    def test_cancel_order_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        with pytest.raises(RuntimeError, match="Not connected"):
            bridge.cancel_order({"order_id": "123"})

    def test_get_account_list_empty_when_not_connected(self):
        bridge = self.mod.FutuBrokerBridge({})
        result = bridge.get_account_list()
        assert result == []

    def test_subscribe_quote_no_context(self):
        bridge = self.mod.FutuBrokerBridge({})
        result = bridge.subscribe_quote({"stock_codes": ["HK.00700"]})
        assert "subscribed" in result

    def test_unsubscribe_quote_no_context(self):
        bridge = self.mod.FutuBrokerBridge({})
        result = bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})
        assert "subscribed" in result


# ── FutuBrokerBridge with mocked futu-api ─────────────────────────


class TestFutuBrokerBridgeWithMockedFutu:
    """Test FutuBrokerBridge with a mocked futu module."""

    @pytest.fixture(autouse=True)
    def _setup_mocked_futu(self):
        """Create a fake futu module and inject it."""
        fake_futu = MagicMock()

        # Set up constants that the module uses
        fake_futu.RET_OK = 0
        fake_futu.RET_ERROR = -1
        fake_futu.TrdEnv.REAL = "REAL"
        fake_futu.TrdEnv.SIMULATE = "SIMULATE"
        fake_futu.TrdMarket.NONE = 0
        fake_futu.OrderType.MARKET = "MARKET"
        fake_futu.OrderType.NORMAL = "NORMAL"
        fake_futu.TrdSide.BUY = "BUY"
        fake_futu.TrdSide.SELL = "SELL"
        fake_futu.TrdSide.SELL_SHORT = "SELL_SHORT"
        fake_futu.TrdSide.BUY_BACK = "BUY_BACK"
        fake_futu.SecurityFirm.FUTUSECURITIES = "FUTUSECURITIES"
        fake_futu.SubType.QUOTE = "QUOTE"
        fake_futu.ModifyOrderOp.CANCEL = "CANCEL"

        # OrderStatus enum-like
        fake_futu.OrderStatus.NONE = "NONE"
        fake_futu.OrderStatus.UNSUBMITTED = "UNSUBMITTED"
        fake_futu.OrderStatus.WAITING_SUBMIT = "WAITING_SUBMIT"
        fake_futu.OrderStatus.SUBMITTING = "SUBMITTING"
        fake_futu.OrderStatus.SUBMIT_FAILED = "SUBMIT_FAILED"
        fake_futu.OrderStatus.SUBMITTED = "SUBMITTED"
        fake_futu.OrderStatus.FILLED_PART = "FILLED_PART"
        fake_futu.OrderStatus.FILLED_ALL = "FILLED_ALL"
        fake_futu.OrderStatus.CANCELLING_PART = "CANCELLING_PART"
        fake_futu.OrderStatus.CANCELLING_ALL = "CANCELLING_ALL"
        fake_futu.OrderStatus.CANCELLED_PART = "CANCELLED_PART"
        fake_futu.OrderStatus.CANCELLED_ALL = "CANCELLED_ALL"
        fake_futu.OrderStatus.FAILED = "FAILED"
        fake_futu.OrderStatus.DISABLED = "DISABLED"
        fake_futu.OrderStatus.DELETED = "DELETED"

        # Mock context classes
        self.mock_quote_ctx = MagicMock()
        self.mock_trade_ctx = MagicMock()
        fake_futu.OpenQuoteContext.return_value = self.mock_quote_ctx
        fake_futu.OpenSecTradeContext.return_value = self.mock_trade_ctx

        # Patch the module in sys.modules
        with mock.patch.dict("sys.modules", {"futu": fake_futu}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            import brokers.futu_broker as mod
            self.mod = mod
            self.fake_futu = fake_futu
            yield
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]

    def _make_bridge(self, **kwargs):
        init_args = {
            "opend_host": "127.0.0.1",
            "opend_port": 11111,
            "trade_env": "SIMULATE",
            "pwd_unlock": "",
        }
        init_args.update(kwargs)
        return self.mod.FutuBrokerBridge(init_args)

    def _make_dataframe(self, rows):
        """Create a simple mock DataFrame-like object."""
        import pandas as pd
        return pd.DataFrame(rows)

    # ── connect ──────────────────────────────────────────────

    def test_connect_success(self):
        bridge = self._make_bridge()
        # Mock get_acc_list to return empty accounts
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        self.mock_trade_ctx.unlock_trade.return_value = (0, "ok")

        result = bridge.connect()
        assert result["status"] == "connected"
        assert bridge._connected is True

    def test_connect_with_password_unlock(self):
        bridge = self._make_bridge(pwd_unlock="mypassword")
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        self.mock_trade_ctx.unlock_trade.return_value = (0, "ok")

        bridge.connect()
        self.mock_trade_ctx.unlock_trade.assert_called_once_with("mypassword", is_unlock=True)

    def test_connect_caches_account_list(self):
        bridge = self._make_bridge()
        accounts_df = self._make_dataframe([
            {"acc_id": 123, "acc_type": "CASH", "trd_env": "SIMULATE", "card_num": "XX", "sim_acc_type": "STOCK"},
        ])
        self.mock_trade_ctx.get_acc_list.return_value = (0, accounts_df)

        result = bridge.connect()
        assert len(result["accounts"]) == 1
        assert result["accounts"][0]["acc_id"] == 123

    def test_connect_quote_ctx_failure(self):
        self.fake_futu.OpenQuoteContext.side_effect = Exception("Connection refused")
        bridge = self._make_bridge()
        with pytest.raises(RuntimeError, match="Failed to connect quote context"):
            bridge.connect()

    def test_connect_trade_ctx_failure_closes_quote(self):
        self.fake_futu.OpenSecTradeContext.side_effect = Exception("Trade connect failed")
        bridge = self._make_bridge()
        with pytest.raises(RuntimeError, match="Failed to connect trade context"):
            bridge.connect()
        # Quote context should have been closed
        self.mock_quote_ctx.close.assert_called_once()

    def test_connect_unlock_failure_warns_but_continues(self):
        bridge = self._make_bridge(pwd_unlock="bad_password")
        self.mock_trade_ctx.unlock_trade.return_value = (-1, "wrong password")
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))

        # Should not raise
        result = bridge.connect()
        assert result["status"] == "connected"

    # ── disconnect ───────────────────────────────────────────

    def test_disconnect_closes_contexts(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        result = bridge.disconnect()
        assert result["status"] == "disconnected"
        assert bridge._connected is False
        self.mock_quote_ctx.close.assert_called_once()
        self.mock_trade_ctx.close.assert_called_once()

    # ── query_account ────────────────────────────────────────

    def test_query_account_success(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (
            0,
            self._make_dataframe([{"acc_id": 456, "acc_type": "CASH", "trd_env": "SIMULATE", "card_num": "", "sim_acc_type": ""}]),
        )
        bridge.connect()

        acc_data = self._make_dataframe([{
            "cash": 100000,
            "frozen_cash": 5000,
            "total_assets": 250000,
            "market_val": 150000,
            "avl_withdrawal_cash": 95000,
            "unrealized_pl": 8000,
            "realized_pl": 2000,
        }])
        self.mock_trade_ctx.accinfo_query.return_value = (0, acc_data)

        result = bridge.query_account()
        assert result["cash"] == 100000
        assert result["frozen_cash"] == 5000
        assert result["total_asset"] == 250000
        assert result["market_value"] == 150000
        assert result["available_funds"] == 95000
        assert result["profit"] == 10000  # 8000 + 2000
        assert result["account_id"] == "456"

    def test_query_account_empty_result(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.accinfo_query.return_value = (0, self._make_dataframe([]))

        with pytest.raises(RuntimeError, match="No account info"):
            bridge.query_account()

    def test_query_account_api_failure(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.accinfo_query.return_value = (-1, "Network error")

        with pytest.raises(RuntimeError, match="accinfo_query failed"):
            bridge.query_account()

    def test_query_account_profit_pct_zero_when_no_assets(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        acc_data = self._make_dataframe([{
            "cash": 0, "frozen_cash": 0, "total_assets": 0,
            "market_val": 0, "avl_withdrawal_cash": 0,
            "unrealized_pl": 0, "realized_pl": 0,
        }])
        self.mock_trade_ctx.accinfo_query.return_value = (0, acc_data)

        result = bridge.query_account()
        assert result["profit_ratio"] == 0

    # ── query_positions ──────────────────────────────────────

    def test_query_positions_success(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        positions_df = self._make_dataframe([{
            "code": "HK.00700",
            "stock_name": "腾讯控股",
            "qty": 1000,
            "can_sell_qty": 800,
            "cost_price": 340.0,
            "market_val": 350000.0,
            "nominal_price": 350.0,
            "pl_val": 10000.0,
            "pl_ratio": 2.9,
            "today_buy_qty": 500,
            "today_sell_qty": 0,
        }])
        self.mock_trade_ctx.position_list_query.return_value = (0, positions_df)

        result = bridge.query_positions()
        assert len(result) == 1
        pos = result[0]
        assert pos["stock_code"] == "HK.00700"
        assert pos["plain_code"] == "00700"
        assert pos["market_prefix"] == "HK"
        assert pos["stock_name"] == "腾讯控股"
        assert pos["volume"] == 1000
        assert pos["can_use_volume"] == 800
        assert pos["frozen_volume"] == 200  # 1000 - 800
        assert pos["avg_price"] == 340.0
        assert pos["last_price"] == 350.0
        assert pos["unrealized_pnl"] == 10000.0
        assert pos["unrealized_pnl_pct"] == pytest.approx(0.029)  # 2.9 / 100

    def test_query_positions_api_failure(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.position_list_query.return_value = (-1, "Error")

        with pytest.raises(RuntimeError, match="position_list_query failed"):
            bridge.query_positions()

    def test_query_positions_zero_nominal_price(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        positions_df = self._make_dataframe([{
            "code": "SH.600519",
            "stock_name": "贵州茅台",
            "qty": 100,
            "can_sell_qty": 100,
            "cost_price": 1800.0,
            "market_val": 185000.0,
            "nominal_price": 0.0,
            "pl_val": 5000.0,
            "pl_ratio": 0.0,
            "today_buy_qty": 0,
            "today_sell_qty": 0,
        }])
        self.mock_trade_ctx.position_list_query.return_value = (0, positions_df)

        result = bridge.query_positions()
        assert result[0]["last_price"] is None  # 0 is not > 0

    # ── place_order ──────────────────────────────────────────

    def test_place_order_limit(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        order_df = self._make_dataframe([{"order_id": "FT-001"}])
        self.mock_trade_ctx.place_order.return_value = (0, order_df)

        result = bridge.place_order({
            "stock_code": "HK.00700",
            "side": "buy",
            "order_type": "limit",
            "price": 350,
            "volume": 100,
        })
        assert result["broker_order_id"] == "FT-001"

    def test_place_order_market(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        order_df = self._make_dataframe([{"order_id": "FT-002"}])
        self.mock_trade_ctx.place_order.return_value = (0, order_df)

        result = bridge.place_order({
            "stock_code": "US.AAPL",
            "side": "buy",
            "order_type": "market",
            "price": 0,
            "volume": 10,
        })
        assert result["broker_order_id"] == "FT-002"

    def test_place_order_failure(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.place_order.return_value = (-1, "Insufficient funds")

        with pytest.raises(RuntimeError, match="place_order failed"):
            bridge.place_order({
                "stock_code": "HK.00700",
                "side": "buy",
                "order_type": "limit",
                "price": 350,
                "volume": 100,
            })

    # ── cancel_order ─────────────────────────────────────────

    def test_cancel_order_success(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.modify_order.return_value = (0, "ok")

        result = bridge.cancel_order({"order_id": "FT-001"})
        assert result["result"] == "ok"

    def test_cancel_order_failure(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.modify_order.return_value = (-1, "Order not found")

        with pytest.raises(RuntimeError, match="cancel_order failed"):
            bridge.cancel_order({"order_id": "FT-999"})

    # ── query_order / query_orders ───────────────────────────

    def test_query_order_success(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        order_df = self._make_dataframe([{
            "order_id": "FT-100",
            "code": "HK.00700",
            "trd_side": "BUY",
            "price": 350,
            "qty": 100,
            "dealt_qty": 50,
            "dealt_avg_price": 349.5,
            "order_status": "FILLED_PART",
            "create_time": "2024-01-15 10:00:00",
            "updated_time": "2024-01-15 10:15:00",
            "remark": "",
        }])
        self.mock_trade_ctx.order_list_query.return_value = (0, order_df)

        result = bridge.query_order({"order_id": "FT-100"})
        assert result["order_id"] == "FT-100"
        assert result["stock_code"] == "HK.00700"
        assert result["traded_volume"] == 50
        assert result["traded_price"] == 349.5

    def test_query_order_not_found(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.order_list_query.return_value = (0, self._make_dataframe([]))

        with pytest.raises(RuntimeError, match="not found"):
            bridge.query_order({"order_id": "FT-999"})

    def test_query_orders_with_filter(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        orders_df = self._make_dataframe([
            {
                "order_id": "FT-200",
                "code": "HK.00700",
                "trd_side": "SELL",
                "price": 355,
                "qty": 100,
                "dealt_qty": 0,
                "dealt_avg_price": 0,
                "order_status": "SUBMITTED",
                "create_time": "2024-01-15",
                "updated_time": "2024-01-15",
                "remark": "",
            },
        ])
        self.mock_trade_ctx.order_list_query.return_value = (0, orders_df)

        result = bridge.query_orders({"stock_code": "HK.00700"})
        assert len(result) == 1
        assert result[0]["stock_code"] == "HK.00700"

    # ── subscribe_quote / unsubscribe_quote ──────────────────

    def test_subscribe_quote_success(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_quote_ctx.subscribe.return_value = (0, "ok")

        result = bridge.subscribe_quote({"stock_codes": ["HK.00700", "US.AAPL"]})
        assert "HK.00700" in result["subscribed"]
        assert "US.AAPL" in result["subscribed"]

    def test_subscribe_quote_deduplicates(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_quote_ctx.subscribe.return_value = (0, "ok")

        bridge.subscribe_quote({"stock_codes": ["HK.00700"]})
        bridge.subscribe_quote({"stock_codes": ["HK.00700", "US.AAPL"]})

        # Only AAPL should be newly subscribed on second call
        assert self.mock_quote_ctx.subscribe.call_count == 2

    def test_unsubscribe_quote_success(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_quote_ctx.subscribe.return_value = (0, "ok")
        self.mock_quote_ctx.unsubscribe.return_value = (0, "ok")

        bridge.subscribe_quote({"stock_codes": ["HK.00700"]})
        result = bridge.unsubscribe_quote({"stock_codes": ["HK.00700"]})
        assert "HK.00700" not in result["subscribed"]

    def test_unsubscribe_quote_ignores_unknown(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        result = bridge.unsubscribe_quote({"stock_codes": ["HK.09999"]})
        # Should not call unsubscribe since code was never subscribed
        self.mock_quote_ctx.unsubscribe.assert_not_called()

    # ── ping ─────────────────────────────────────────────────

    def test_ping_healthy(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.get_acc_list.return_value = (0, "ok")
        result = bridge.ping()
        assert result["ok"] is True

    def test_ping_unhealthy(self):
        bridge = self._make_bridge()
        self.mock_trade_ctx.get_acc_list.return_value = (0, self._make_dataframe([]))
        bridge.connect()

        self.mock_trade_ctx.get_acc_list.side_effect = Exception("timeout")
        result = bridge.ping()
        assert result["ok"] is False

    # ── _serialize_order ─────────────────────────────────────

    def test_serialize_order(self):
        row = {
            "order_id": "FT-300",
            "code": "US.AAPL",
            "trd_side": "SELL",
            "price": 180.5,
            "qty": 50,
            "dealt_qty": 25,
            "dealt_avg_price": 180.2,
            "order_status": "FILLED_PART",
            "create_time": "2024-01-15 10:00:00",
            "updated_time": "2024-01-15 10:30:00",
            "remark": "test",
        }
        import pandas as pd
        series = pd.Series(row)

        result = self.mod.FutuBrokerBridge._serialize_order(series)
        assert result["order_id"] == "FT-300"
        assert result["stock_code"] == "US.AAPL"
        assert result["price"] == 180.5
        assert result["order_volume"] == 50
        assert result["traded_volume"] == 25
        assert result["traded_price"] == 180.2
        assert result["remark"] == "test"

    def test_serialize_order_handles_none_values(self):
        row = {
            "order_id": "FT-301",
            "code": "HK.00700",
            "trd_side": "BUY",
            "price": None,
            "qty": None,
            "dealt_qty": None,
            "dealt_avg_price": None,
            "order_status": "",
            "create_time": "",
            "updated_time": "",
            "remark": "",
        }
        import pandas as pd
        series = pd.Series(row)

        result = self.mod.FutuBrokerBridge._serialize_order(series)
        assert result["price"] == 0
        assert result["order_volume"] == 0
        assert result["traded_volume"] == 0
        assert result["traded_price"] == 0


# ── Dispatch loop ─────────────────────────────────────────────────


class TestDispatchConstants:
    """Test the METHODS and NO_PARAM_METHODS dispatch tables."""

    @pytest.fixture(autouse=True)
    def _reload_module(self):
        with mock.patch.dict("sys.modules", {"futu": None}):
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]
            import brokers.futu_broker as mod
            self.mod = mod
            yield
            if "brokers.futu_broker" in sys.modules:
                del sys.modules["brokers.futu_broker"]

    def test_all_methods_registered(self):
        expected = {
            "connect", "disconnect", "place_order", "cancel_order",
            "query_order", "query_orders", "query_positions",
            "query_account", "get_account_list", "subscribe_quote",
            "unsubscribe_quote", "ping",
        }
        assert set(self.mod.METHODS.keys()) == expected

    def test_no_param_methods_subset(self):
        assert self.mod.NO_PARAM_METHODS.issubset(set(self.mod.METHODS.keys()))

    def test_no_param_methods_content(self):
        expected_no_param = {
            "connect", "disconnect", "query_positions",
            "query_account", "get_account_list", "ping",
        }
        assert self.mod.NO_PARAM_METHODS == expected_no_param
