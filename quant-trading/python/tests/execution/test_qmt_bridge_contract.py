from __future__ import annotations

import pytest
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from brokers.qmt_broker import QMTBrokerBridge


_DATA_BRIDGE_PATH = Path(__file__).parents[2] / "aipha" / "execution" / "qmt_data_bridge.py"
_data_spec = importlib.util.spec_from_file_location("qmt_data_bridge_contract", _DATA_BRIDGE_PATH)
assert _data_spec and _data_spec.loader
data_bridge = importlib.util.module_from_spec(_data_spec)
_data_spec.loader.exec_module(data_bridge)
_strategy_data_bridge_path = Path(__file__).parents[2] / "strategies" / "qmt_data_bridge.py"
_strategy_data_spec = importlib.util.spec_from_file_location("qmt_strategy_data_bridge_contract", _strategy_data_bridge_path)
assert _strategy_data_spec and _strategy_data_spec.loader
strategy_data_bridge = importlib.util.module_from_spec(_strategy_data_spec)
_strategy_data_spec.loader.exec_module(strategy_data_bridge)


@pytest.mark.parametrize("sdk_spelling", ["current", "legacy"])
@pytest.mark.parametrize("transport", ["broker", "aipha-http", "strategy-http"])
def test_order_queries_accept_sdk_argument_spellings_and_include_filled_orders(monkeypatch, sdk_spelling, transport):
    account = object()
    filled = SimpleNamespace(order_id=123, stock_code="600000.SH", order_type=23,
        price=10.0, order_volume=100, traded_volume=100, traded_price=10.0,
        order_status=56, strategy_name="fixture")
    calls = []

    class CurrentSDK:
        def query_stock_orders(self, account_arg, cancelable_only=False):
            calls.append((account_arg, cancelable_only))
            return [] if cancelable_only else [filled]

    class LegacySDK:
        def query_stock_orders(self, account_arg, cancel_able_only=False):
            calls.append((account_arg, cancel_able_only))
            return [] if cancel_able_only else [filled]

    trader = (CurrentSDK if sdk_spelling == "current" else LegacySDK)()
    if transport == "broker":
        bridge = QMTBrokerBridge({})
        bridge.trader, bridge.account = trader, account
        orders = bridge.query_orders({"stock_code": "600000.SH"})
        order = bridge.query_order({"order_id": "123"})
        assert bridge.query_orders({"stock_code": "000001.SZ"}) == []
    else:
        module = data_bridge if transport == "aipha-http" else strategy_data_bridge
        monkeypatch.setattr(module, "_trading_trader", trader)
        monkeypatch.setattr(module, "_trading_account", account)
        all_result = module._trading_query_orders({"stock_code": "600000.SH"})
        one_result = module._trading_query_order({"order_id": "123"})
        assert all_result["ok"] and one_result["ok"]
        orders, order = all_result["data"], one_result["data"]
        assert module._trading_query_orders({"stock_code": "000001.SZ"}) == {"ok": True, "data": []}
    assert [item["order_id"] for item in orders] == ["123"]
    assert order["traded_volume"] == 100
    assert calls == [(account, False)] * 3


@pytest.mark.parametrize("has_profit", [False, True])
def test_account_queries_preserve_native_assets_without_optional_profit(has_profit):
    values = dict(cash=120.0, frozen_cash=20.0, total_asset=300.0, market_value=180.0)
    if has_profit:
        values.update(profit=15.0, profit_ratio=.05)
    bridge = QMTBrokerBridge({"account_id": "fixture-account"})
    bridge.account = object()
    bridge.trader = SimpleNamespace(query_stock_asset=lambda account: SimpleNamespace(**values))
    result = bridge.query_account({})
    assert all(result[name] == value for name, value in values.items())
    assert result["account_id"] == "fixture-account"
    if has_profit:
        assert "_unavailable_fields" not in result
    else:
        assert result["profit"] == result["profit_ratio"] == 0
        assert result["_unavailable_fields"] == ["profit", "profit_ratio"]


def test_qmt_bridge_rejects_raw_orders_when_profile_is_gated() -> None:
    bridge = QMTBrokerBridge({"require_execution_plan": True})
    bridge.trader = object()
    bridge.account = object()
    with pytest.raises(RuntimeError, match="direct order bridge is disabled"):
        bridge.place_order({
            "stock_code": "600000.SH",
            "order_type": 23,
            "volume": 100,
        })


def test_qmt_bridge_rejects_forged_provenance_even_when_complete() -> None:
    class FakeTrader:
        def __init__(self):
            self.args = None

        def order_stock(self, *args):
            self.args = args
            return 12345

    bridge = QMTBrokerBridge({"require_execution_plan": True})
    trader = FakeTrader()
    bridge.trader = trader
    bridge.account = object()
    with pytest.raises(RuntimeError, match="direct order bridge is disabled"):
        bridge.place_order({
            "stock_code": "600000.SH",
            "order_type": 23,
            "price_type": 11,
            "price": 10.0,
            "volume": 100,
            "strategy_name": "aipha-ml-ranking",
            "run_id": "run-1",
            "model_id": "model-1",
            "signal_id": "signal-1",
            "strategy_version": "1.0.0",
        })
    assert trader.args is None


def test_qmt_bridge_never_places_raw_orders_even_without_profile_flag() -> None:
    bridge = QMTBrokerBridge({"require_execution_plan": False})
    bridge.trader = object()
    bridge.account = object()
    with pytest.raises(RuntimeError, match="direct order bridge is disabled"):
        bridge.place_order({
            "stock_code": "600000.SH", "order_type": 23, "volume": 100,
            "run_id": "forged", "model_id": "forged", "signal_id": "forged",
        })


@pytest.mark.parametrize("bridge_module", [data_bridge, strategy_data_bridge], ids=["aipha", "qmt-strategy"])
def test_http_data_bridge_rejects_raw_orders_even_with_forged_provenance(bridge_module) -> None:
    class FakeTrader:
        def __init__(self):
            self.args = None

        def order_stock(self, *args):
            self.args = args
            return 67890

    trader = FakeTrader()
    bridge_module._trading_trader = trader
    bridge_module._trading_account = object()
    bridge_module._trading_connected = True
    bridge_module._require_execution_plan = True
    try:
        rejected = bridge_module._trading_place_order({
            "stock_code": "600000.SH",
            "order_type": 23,
            "volume": 100,
        })
        assert rejected["ok"] is False
        assert "direct QMT orders" in rejected["error"]

        result = bridge_module._trading_place_order({
            "stock_code": "600000.SH",
            "order_type": 23,
            "price_type": 11,
            "price": 10.0,
            "volume": 100,
            "strategy_name": "aipha-ml-ranking",
            "run_id": "run-http-1",
            "model_id": "model-http-1",
            "signal_id": "signal-http-1",
            "strategy_version": "1.0.0",
        })
        assert result["ok"] is False
        assert "direct QMT orders" in result["error"]
        assert trader.args is None
    finally:
        bridge_module._trading_trader = None
        bridge_module._trading_account = None
        bridge_module._trading_connected = False
        bridge_module._require_execution_plan = False

