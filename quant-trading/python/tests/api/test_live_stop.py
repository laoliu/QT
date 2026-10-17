import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from aipha.api import deps
from aipha.api.live import engine_manager
from aipha.api.live.engine_manager import EngineManager, get_engine_manager
from aipha.api.live.router import StopRequest
from aipha.execution.emergency_control import StopAgent


@pytest.fixture
def controls(tmp_path, monkeypatch, app):
    flag = tmp_path / "configs" / "kill_switch.flag"
    monkeypatch.setattr(engine_manager, "KILL_SWITCH_PATH", flag)
    monkeypatch.setattr(deps, "_DB_PATH", tmp_path / "api.db")
    deps.get_result_store.cache_clear()
    manager = EngineManager()
    app.dependency_overrides[get_engine_manager] = lambda: manager
    yield TestClient(app), manager, flag
    app.dependency_overrides.pop(get_engine_manager, None)
    deps.get_result_store.cache_clear()


def _headers(role):
    deps.get_result_store().upsert_user({
        "username": role, "name": role, "password_hash": "unused", "role": role,
    })
    return {"Authorization": f"Bearer {deps.create_access_token(role, role)}"}


@pytest.mark.parametrize("role", [None, "viewer", "researcher"])
def test_stop_requires_operator(controls, role):
    client, _, flag = controls
    response = client.post("/api/v1/live/engine/stop", json={"reason": "test"},
                           headers=_headers(role) if role else {})
    assert response.status_code == (401 if role is None else 403)
    assert not flag.exists()


@pytest.mark.parametrize("role", ["ops", "risk_approver", "admin"])
def test_stop_is_persistent_without_local_runner(controls, role):
    client, _, flag = controls
    response = client.post("/api/v1/live/engine/stop", json={"reason": "manual halt"},
                           headers=_headers(role))
    assert response.status_code == 503
    assert json.loads(flag.read_text())["actor"] == role
    data = response.json()["data"]
    assert data["kill_switch_active"]
    assert not data["runner_notified"]
    assert not data["broker_cancellation_confirmed"]
    assert data["stop_state"] == "unconfirmed"
    assert client.get("/api/v1/live/engine/status", headers=_headers(role)).json()["data"]["kill_switch_active"]


def test_stop_persists_before_runner_notification_and_survives_failure(controls):
    client, manager, flag = controls
    runner = Mock()
    runner.state = SimpleNamespace(is_running=True)

    def fail(reason):
        assert flag.exists()
        raise RuntimeError("broker unreachable")

    runner.emergency_stop.side_effect = fail
    manager.attach(runner)
    response = client.post("/api/v1/live/engine/stop", json={"reason": "halt"}, headers=_headers("ops"))
    assert response.status_code == 503
    assert response.json()["data"]["error"]
    assert flag.exists()
    with pytest.raises(RuntimeError):
        EngineManager().attach(runner)
    assert flag.exists()


@pytest.mark.parametrize("stage", ["replace", "mkstemp"])
def test_persistence_failure_is_not_reported_as_success(controls, monkeypatch, stage):
    client, manager, flag = controls
    runner = Mock()
    manager.attach(runner)
    target = engine_manager.os if stage == "replace" else engine_manager.tempfile
    monkeypatch.setattr(target, stage, Mock(side_effect=OSError("disk error")))
    response = client.post("/api/v1/live/engine/stop", json={"reason": "halt"}, headers=_headers("admin"))
    assert response.status_code == 503
    assert not flag.exists()
    runner.emergency_stop.assert_called_once_with("halt")


def test_blank_reason_rejected(controls):
    client, _, flag = controls
    assert client.post("/api/v1/live/engine/stop", json={"reason": "   "}, headers=_headers("ops")).status_code == 422
    assert not flag.exists()


def test_stop_request_defaults_to_synchronous_confirmation_window():
    assert StopRequest(reason="halt").wait_seconds == 2.0
    assert StopRequest(reason="halt", wait_seconds=0).wait_seconds == 0.0


def test_emergency_stop_routes_are_registered_in_app(app):
    paths = {route.path for route in app.routes}
    assert "/api/v1/live/engine/stop" in paths
    assert "/api/v1/live/engine/status" in paths


def test_api_default_waits_for_cross_process_broker_confirmation(controls):
    client, _, flag = controls
    pending = ["order-1"]

    def cancel(order_id):
        pending.remove(order_id)

    agent = StopAgent(flag, lambda: list(pending), cancel, threading.RLock())
    agent.start()
    try:
        response = client.post(
            "/api/v1/live/engine/stop", json={"reason": "operator halt"},
            headers=_headers("ops"),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["broker_cancellation_confirmed"]
        assert data["stop_state"] == "confirmed"
        assert data["workers"][0]["pending_orders"] == []
    finally:
        agent.close()


def test_async_publication_is_not_reported_as_success(controls):
    client, _, flag = controls
    response = client.post(
        "/api/v1/live/engine/stop", json={"reason": "operator halt", "wait_seconds": 0},
        headers=_headers("ops"),
    )
    assert response.status_code == 503
    data = response.json()["data"]
    assert data["kill_switch_active"]
    assert data["stop_state"] == "unconfirmed"
