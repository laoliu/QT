import os
import subprocess
import sys
import time
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aipha.api.live import engine_manager
from aipha.execution.emergency_control import StopAgent, atomic_json, control_status
from aipha.execution.gateway import GatewayError
from aipha.execution.qmt_gateway import QMTConfig, QMTGateway
from aipha.execution.qmt_callback import map_qmt_status
from aipha.execution.gateway import OrderStatus
from aipha.utils.paths import PROJECT_ROOT


CHILD = """
import sys, threading, time
from pathlib import Path
from aipha.execution.emergency_control import StopAgent
flag, mode = Path(sys.argv[1]), sys.argv[2]
pending = ['101']
def query():
    if mode == 'error':
        raise RuntimeError('broker unavailable')
    return list(pending)
def cancel(oid):
    flag.with_suffix('.cancel_sent').touch()
    if mode == 'confirmed':
        pending.remove(oid)
    return True
agent = StopAgent(flag, query, cancel, threading.RLock(), interval=.02, label=mode)
agent.start()
flag.with_suffix('.ready_' + mode).touch()
try:
    while not flag.with_suffix('.done').exists():
        time.sleep(.02)
finally:
    agent.close()
"""


@pytest.mark.parametrize("mode", ["confirmed", "pending", "error"])
def test_api_observes_independent_broker_process(tmp_path, monkeypatch, mode):
    flag = tmp_path / "kill.flag"
    monkeypatch.setattr(engine_manager, "KILL_SWITCH_PATH", flag)
    process = subprocess.Popen([sys.executable, "-c", CHILD, str(flag), mode],
                               cwd=PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not flag.with_suffix(".ready_" + mode).exists():
            assert process.poll() is None, "Broker process exited before registration"
            assert time.monotonic() < deadline, "Broker registration timed out"
            time.sleep(.02)
        result = engine_manager.EngineManager().stop("test halt", wait_seconds=.5)
        assert result["broker_cancellation_confirmed"] == (mode == "confirmed")
        assert result["workers"][0]["pid"] != os.getpid()
        assert result["workers"][0]["request_id"] == result["request_id"]
        assert not result["runner_attached"]
        if mode == "pending":
            assert flag.with_suffix(".cancel_sent").exists()
            assert result["workers"][0]["pending_orders"] == ["101"]
        elif mode == "error":
            assert result["workers"][0]["error"]
        else:
            second = engine_manager.EngineManager().stop("second halt", wait_seconds=.5)
            assert second["request_id"] != result["request_id"]
            assert second["broker_cancellation_confirmed"]
    finally:
        flag.with_suffix(".done").touch()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


def test_api_requires_confirmation_from_every_independent_broker_worker(tmp_path, monkeypatch):
    flag = tmp_path / "kill.flag"
    monkeypatch.setattr(engine_manager, "KILL_SWITCH_PATH", flag)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, str(flag), mode],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for mode in ("confirmed", "pending")
    ]
    try:
        deadline = time.monotonic() + 10
        for mode, process in zip(("confirmed", "pending"), processes):
            ready = flag.with_suffix(".ready_" + mode)
            while not ready.exists():
                assert process.poll() is None, f"{mode} broker process exited before registration"
                assert time.monotonic() < deadline, f"{mode} broker registration timed out"
                time.sleep(.02)

        result = engine_manager.EngineManager().stop("halt all broker workers", wait_seconds=.6)
        assert len(result["workers"]) == 2
        assert {worker["label"] for worker in result["workers"]} == {"confirmed", "pending"}
        assert not result["broker_cancellation_confirmed"]
        assert all(worker["request_id"] == result["request_id"] for worker in result["workers"])
        pending_worker = next(w for w in result["workers"] if w["label"] == "pending")
        assert pending_worker["pending_orders"] == ["101"]
    finally:
        flag.with_suffix(".done").touch()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


def test_stale_or_unreadable_acknowledgement_never_confirms(tmp_path):
    flag = tmp_path / "kill.flag"
    atomic_json(flag, {"request_id": "new"})
    assert not control_status(flag)["broker_cancellation_confirmed"]
    record = flag.parent / "kill_switch_control" / "worker.json"
    atomic_json(record, {"request_id": "old", "confirmed": True, "heartbeat": time.time(), "online": True})
    assert not control_status(flag)["broker_cancellation_confirmed"]
    atomic_json(record, {"request_id": "new", "confirmed": True, "heartbeat": 0, "online": True})
    assert not control_status(flag)["broker_cancellation_confirmed"]
    record.write_text("broken")
    assert control_status(flag)["control_errors"]
    assert not control_status(flag)["broker_cancellation_confirmed"]


def test_disconnected_broker_with_pending_orders_remains_unconfirmed(tmp_path):
    flag = tmp_path / "kill.flag"
    agent = StopAgent(flag, lambda: ["101"], lambda oid: True, threading.RLock())
    agent.start()
    agent.close()
    atomic_json(flag, {"request_id": "new"})
    snapshot = control_status(flag)
    assert len(snapshot["workers"]) == 1
    assert snapshot["workers"][0]["unresolved_orders"]
    assert not snapshot["broker_cancellation_confirmed"]


@pytest.mark.parametrize("statuses,expected", [([50, 54, 56], ["100"]), ([53, 54, 56, 57], [])])
def test_qmt_confirms_using_fresh_broker_orders(statuses, expected):
    gateway = QMTGateway(QMTConfig(name="test"))
    gateway._ensure_connected = Mock()
    gateway._trader = Mock()
    gateway._trader.query_stock_orders.return_value = [
        SimpleNamespace(order_id=100 + i, order_status=status) for i, status in enumerate(statuses)
    ]
    assert gateway.query_open_order_ids() == expected
    gateway._trader.query_stock_orders.assert_called_once()


@pytest.mark.parametrize("orders", [None, [SimpleNamespace(order_id=100, order_status=255)]])
def test_uncertain_qmt_queries_raise_instead_of_confirming(orders):
    gateway = QMTGateway(QMTConfig(name="test"))
    gateway._ensure_connected = Mock()
    gateway._trader = Mock()
    gateway._trader.query_stock_orders.return_value = orders
    with pytest.raises(GatewayError):
        gateway.query_open_order_ids()


def test_qmt_gateway_blocks_direct_submission(tmp_path, monkeypatch):
    from aipha.execution import qmt_gateway
    flag = tmp_path / "kill.flag"
    flag.touch()
    monkeypatch.setattr(qmt_gateway, "KILL_SWITCH_PATH", flag)
    gateway = QMTGateway(QMTConfig(name="test"))
    gateway._submit_order_impl = Mock()
    with pytest.raises(GatewayError, match="kill switch"):
        gateway.submit_order(Mock())
    gateway._submit_order_impl.assert_not_called()


def test_easytrader_gateway_blocks_direct_submission(tmp_path, monkeypatch):
    from aipha.execution import easytrader_gateway
    from aipha.execution.broker_gateway import BrokerType

    flag = tmp_path / "kill.flag"
    flag.touch()
    monkeypatch.setattr(easytrader_gateway, "KILL_SWITCH_PATH", flag)
    gateway = easytrader_gateway.EasytraderGateway(
        easytrader_gateway.EasytraderConfig(
            broker_type=BrokerType.EASYTRADER, name="stop-guard", dry_run=True,
        ),
    )
    gateway._submit_order_impl = Mock()
    with pytest.raises(easytrader_gateway.OrderValidationError, match="kill switch"):
        gateway.submit_order(Mock())
    gateway._submit_order_impl.assert_not_called()


@pytest.mark.parametrize("code,status", [(48, OrderStatus.PENDING), (50, OrderStatus.SUBMITTED),
                                        (53, OrderStatus.CANCELED), (54, OrderStatus.CANCELED),
                                        (55, OrderStatus.PARTIAL_FILLED), (56, OrderStatus.FILLED),
                                        (57, OrderStatus.REJECTED)])
def test_callback_uses_actual_xtquant_status_codes(code, status):
    assert map_qmt_status(code) == status


@pytest.mark.parametrize("broker", ["qmt", "easytrader"])
@pytest.mark.parametrize("clears", [True, False])
def test_gateway_login_starts_stop_listener_without_market_events(tmp_path, monkeypatch, broker, clears):
    from aipha.execution import qmt_gateway, easytrader_gateway
    from aipha.execution.broker_gateway import BrokerType

    flag = tmp_path / "kill.flag"
    monkeypatch.setattr(engine_manager, "KILL_SWITCH_PATH", flag)
    pending = ["101"]
    cancellations = []

    if broker == "qmt":
        monkeypatch.setattr(qmt_gateway, "KILL_SWITCH_PATH", flag)
        monkeypatch.setattr(qmt_gateway, "HAS_XTQUANT", True)
        trader = Mock()
        trader.register_callback.side_effect = lambda cb: setattr(trader, "callback", cb)

        def connect():
            trader.callback.on_connected()
            return 0

        def cancel(account, order_id):
            cancellations.append(str(order_id))
            if clears:
                pending.clear()
            return 0

        trader.connect.side_effect = connect
        trader.subscribe.return_value = 0
        trader.query_stock_orders.side_effect = lambda account: [
            SimpleNamespace(order_id=int(oid), order_status=50) for oid in pending
        ]
        trader.cancel_order_stock.side_effect = cancel
        monkeypatch.setitem(sys.modules, "xtquant.xttrader", SimpleNamespace(XtQuantTrader=lambda *args: trader))
        monkeypatch.setitem(sys.modules, "xtquant.xttype", SimpleNamespace(StockAccount=lambda *args: object()))
        gateway = QMTGateway(QMTConfig(mini_qmt_path="fake", account_id="test"))
        monkeypatch.setattr(gateway, "_event_poll_loop", lambda: None)
    else:
        monkeypatch.setattr(easytrader_gateway, "KILL_SWITCH_PATH", flag)

        class Client:
            def connect(self, path):
                pass

            @property
            def today_entrusts(self):
                return [{"合同编号": oid, "委托状态": "已报"} for oid in pending]

            def cancel_entrust(self, order_id):
                cancellations.append(order_id)
                if clears:
                    pending.clear()

        monkeypatch.setitem(sys.modules, "easytrader", SimpleNamespace(use=lambda kind: Client()))
        gateway = easytrader_gateway.EasytraderGateway(easytrader_gateway.EasytraderConfig(
            broker_type=BrokerType.EASYTRADER, name="listener-test", dry_run=False,
        ))
        monkeypatch.setattr(gateway, "_start_poll_thread", lambda: None)

    try:
        gateway.connect()
        assert gateway.login({"user": "test", "password": "fake"})
        assert gateway._stop_agent is not None
        assert gateway._stop_agent._thread.is_alive()
        response = engine_manager.EngineManager().stop("test stop", wait_seconds=1)
        assert cancellations
        assert response["broker_cancellation_confirmed"] is clears
        assert response["workers"][0]["pending_orders"] == ([] if clears else ["101"])
    finally:
        gateway.disconnect()
