from unittest.mock import Mock

from aipha.api.live import engine_manager
from aipha.domain.models import OrderIntent, Side
from aipha.runner import live_engine_eod, live_runner


def test_http_control_flag_blocks_urgent_orders(tmp_path, monkeypatch):
    flag = tmp_path / "kill_switch.flag"
    monkeypatch.setattr(engine_manager, "KILL_SWITCH_PATH", flag)
    monkeypatch.setattr(live_runner, "KILL_SWITCH_PATH", flag)
    engine_manager.EngineManager().stop("operator halt", actor="ops")
    runner = object.__new__(live_runner.LiveRunner)
    runner._is_trading_halted = False
    runner.gateway = Mock()
    runner.emergency_stop = Mock()
    runner._submit_intents_urgent([OrderIntent("TEST", Side.SELL, 100)])
    runner.gateway.submit_order.assert_not_called()
    runner.emergency_stop.assert_called_once()


def test_eod_stops_remaining_batch_when_flag_appears(tmp_path, monkeypatch):
    flag = tmp_path / "kill_switch.flag"
    monkeypatch.setattr(live_engine_eod, "KILL_SWITCH_PATH", flag)
    engine = object.__new__(live_engine_eod.LiveEngineEOD)
    engine.cfg = {"profile_name": "test"}
    engine._risk_limits = {"order_interval_seconds": 0}
    gateway = Mock()

    def submit(order):
        flag.touch()
        return "first-order"

    gateway.submit_order.side_effect = submit
    engine._ensure_qmt = Mock(return_value=gateway)
    result = engine._submit_live_orders(
        [], [{"code": "A", "shares": 100}, {"code": "B", "shares": 100}],
        {"A": 10, "B": 10}, "2026-10-12",
    )
    assert len(result) == 1
    assert gateway.submit_order.call_count == 1
    engine._ensure_qmt.reset_mock()
    assert engine._submit_live_orders([], [], {}, "2026-10-12") == []
    engine._ensure_qmt.assert_not_called()
