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


def test_connect_binds_requested_account_to_actual_queries(monkeypatch):
    monkeypatch.setattr(bridge, "TRADING_ACCOUNT_ID", "")
    monkeypatch.setattr(bridge, "_trading_connected", False)
    monkeypatch.setattr(bridge, "_qmt_context", FakeContext())
    calls = []

    def query(account_id, account_type, kind):
        calls.append((account_id, kind))
        return [{}] if kind == "ACCOUNT" else []

    monkeypatch.setattr(bridge, "get_trade_detail_data", query, raising=False)
    connected = bridge._trading_connect({"account_id": "account-1"})
    assert connected["ok"] is True
    assert bridge._trading_query_account({})["data"]["account_id"] == "account-1"
    assert bridge._trading_query_positions({})["ok"] is True
    assert bridge._trading_query_orders({})["ok"] is True
    assert calls == [("account-1", "ACCOUNT"), ("account-1", "POSITION"), ("account-1", "ORDER")]


def test_connect_cannot_switch_configured_or_connected_account(monkeypatch):
    monkeypatch.setattr(bridge, "TRADING_ACCOUNT_ID", "account-1")
    for connected in (False, True):
        monkeypatch.setattr(bridge, "_trading_connected", connected)
        result = bridge._trading_connect({"account_id": "account-2"})
        assert result["ok"] is False
        assert "does not match" in result["error"]
        assert bridge.TRADING_ACCOUNT_ID == "account-1"


def test_connect_requires_real_query_function_even_if_passorder_exists(monkeypatch):
    monkeypatch.setattr(bridge, "TRADING_ACCOUNT_ID", "")
    monkeypatch.setattr(bridge, "_trading_connected", False)
    monkeypatch.setattr(bridge, "_qmt_context", FakeContext())
    monkeypatch.setattr(bridge, "passorder", lambda *args: None, raising=False)
    monkeypatch.delattr(bridge, "get_trade_detail_data", raising=False)
    result = bridge._trading_connect({"account_id": "account-1"})
    assert result["ok"] is False
    assert "get_trade_detail_data" in result["error"]
    assert bridge._trading_connected is False
    assert bridge.TRADING_ACCOUNT_ID == ""


def test_bridge_http_bypasses_system_proxy_for_explicit_receiver(monkeypatch):
    import urllib.request
    handlers = []
    calls = []

    class Response:
        def read(self):
            return b'{"status":"ok"}'

        def close(self):
            pass

    class Opener:
        def open(self, req, timeout):
            calls.append((req.full_url, req.data, timeout))
            return Response()

    def build(*args):
        handlers.extend(args)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    monkeypatch.setattr(bridge, "_http_opener", None)
    assert bridge._http_get("http://receiver:18889/pending", verbose=False) == '{"status":"ok"}'
    assert bridge._http_post("http://receiver:18889/result/1", {"ok": True}) == '{"status":"ok"}'
    assert len(handlers) == 1
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    assert calls == [("http://receiver:18889/pending", None, 10),
                     ("http://receiver:18889/result/1", b'{"ok": true}', 10)]

