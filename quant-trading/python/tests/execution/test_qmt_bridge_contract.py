from __future__ import annotations

import pytest

from brokers.qmt_broker import QMTBrokerBridge


def test_qmt_bridge_requires_plan_provenance_when_profile_is_gated() -> None:
    bridge = QMTBrokerBridge({"require_execution_plan": True})
    bridge.trader = object()
    bridge.account = object()
    with pytest.raises(RuntimeError, match="run_id and signal_id"):
        bridge.place_order({
            "stock_code": "600000.SH",
            "order_type": 23,
            "volume": 100,
        })

