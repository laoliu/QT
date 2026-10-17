from __future__ import annotations

import pytest
import importlib.util
from pathlib import Path

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

