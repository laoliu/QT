import importlib.util
from pathlib import Path


_BRIDGE_PATH = Path(__file__).parents[1] / "strategies" / "BRIDGE.py"
_spec = importlib.util.spec_from_file_location("receiver_bridge", _BRIDGE_PATH)
assert _spec and _spec.loader
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class FakeContext:
    def __init__(self):
        self.subscriptions = []
        self.ticks = {
            "600519.SH": {
                "lastPrice": 1800.0,
                "lastClose": 1790.0,
                "open": 1795.0,
                "high": 1810.0,
                "low": 1788.0,
                "volume": 1000,
                "amount": 1_800_000,
                "bidPrice": [1799.9],
                "askPrice": [1800.1],
            }
        }

    def subscribe_quote(self, *args):
        self.subscriptions.append(args)

    def get_full_tick(self, codes):
        return {code: self.ticks[code] for code in codes if code in self.ticks}


def _reset_state(ctx):
    bridge._qmt_context = ctx
    bridge._trading_connected = True
    bridge._trading_events_queue[:] = []
    bridge._trading_subscribed_codes.clear()
    bridge._trading_order_snapshot.clear()
    bridge._trading_quote_last_push.clear()


def test_receiver_dispatch_supports_quote_subscription_and_push():
    ctx = FakeContext()
    _reset_state(ctx)

    result = bridge._TRADING_DISPATCH["subscribe_quote"]({"stock_codes": ["600519"]})

    assert result["ok"] is True
    assert result["data"]["subscribed"] == ["600519.SH"]
    assert ctx.subscriptions[0][0] == "600519.SH"

    bridge._trading_collect_events()
    assert bridge._trading_events_queue[0]["event"] == "quote"
    quote = bridge._trading_events_queue[0]["data"]
    assert quote["stock_code"] == "600519.SH"
    assert quote["last_price"] == 1800.0

    result = bridge._TRADING_DISPATCH["unsubscribe_quote"]({"stock_codes": ["600519.SH"]})
    assert result["ok"] is True
    assert result["data"]["subscribed"] == []


def test_receiver_dispatch_rejects_missing_qmt_quote_context():
    bridge._qmt_context = None
    bridge._trading_subscribed_codes.clear()
    result = bridge._TRADING_DISPATCH["subscribe_quote"]({"stock_codes": ["600519.SH"]})
    assert result["ok"] is False
    assert "QMT context" in result["error"]


def test_receiver_polling_bridge_rejects_raw_qmt_orders():
    _reset_state(FakeContext())
    called = []
    bridge.passorder = lambda *args: called.append(args)
    try:
        result = bridge._TRADING_DISPATCH["place_order"]({
            "stock_code": "600519.SH", "order_type": 23, "volume": 100,
            "run_id": "forged-run", "model_id": "forged-model", "signal_id": "forged-signal",
        })
        assert result["ok"] is False
        assert "direct QMT orders" in result["error"]
        assert called == []
    finally:
        del bridge.passorder

