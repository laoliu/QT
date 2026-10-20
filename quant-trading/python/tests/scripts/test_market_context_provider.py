"""Contracts between market-context snapshots and their engine adapters."""

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts import market_context_provider as provider


def test_auction_without_watchlist_is_valid_and_has_no_import_error(capsys):
    assert provider.handle_auction_snapshot({}) == {"auction_signals": {}}
    assert capsys.readouterr().err == ""


def test_auction_calls_existing_scanner_and_preserves_signal_fields(monkeypatch):
    from engines import auction_sniper
    calls = []
    signal = {"symbol": "600519", "score": 80, "signal_type": "strong", "gap_up_pct": 3.2,
              "signals": ["late surge"], "risk_notes": []}
    def scan(**kwargs):
        calls.append(kwargs)
        return [signal]
    monkeypatch.setattr(auction_sniper, "scan_auction_sniper", scan)
    snapshots = [[{"symbol": "600519", "timestamp": "09:24:50"}]]
    result = provider.handle_auction_snapshot({"symbols": ["600519"], "snapshots": snapshots})
    assert result["auction_signals"]["600519"] == signal
    assert calls == [{"symbols": ["600519"], "snapshots_data": snapshots}]


def test_catalyst_consumes_real_picks_contract_and_threshold(monkeypatch):
    from engines import event_catalyst_engine
    calls = []
    def scan(config):
        calls.append(config)
        return {"picks": [{"code": "600519", "catalysts": [{"usChangePct": 6}],
                           "signals": ["QCOM +6%"]}]}
    monkeypatch.setattr(event_catalyst_engine, "_scan", scan)
    result = provider.handle_catalyst_snapshot({})
    assert result["catalysts"]["600519"]["impact"] == "high"
    assert result["catalysts"]["600519"]["description"] == "QCOM +6%"
    assert calls == [{"min_change_pct": 3.0, "include_news": False}]


def test_unavailable_catalysts_are_distinct_from_successful_empty_scan(monkeypatch):
    from engines import event_catalyst_engine
    monkeypatch.setattr(event_catalyst_engine, "_scan", lambda config: {"error": "unavailable"})
    assert provider.handle_catalyst_snapshot({}) == {"catalysts": None}
    monkeypatch.setattr(event_catalyst_engine, "_scan", lambda config: {"triggered": False})
    assert provider.handle_catalyst_snapshot({}) == {"catalysts": {}}


def test_st_uses_current_record_contract(monkeypatch):
    from engines import st_restructuring
    record = st_restructuring.RestructuringRecord("600000", "test", current_stage="MAIN-5")
    monkeypatch.setattr(st_restructuring, "scan_all_restructuring", lambda **kwargs: {"600000": record})
    assert provider.handle_st_snapshot({})["st_statuses"]["600000"]["risk_level"] == "extreme"


def test_cli_emits_payload_expected_by_python_bridge():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-m", "scripts.market_context_provider"],
                            input=json.dumps({"action": "auction_snapshot"}),
                            capture_output=True, text=True, cwd=root, timeout=10,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"auction_signals": {}}
    assert "ImportError" not in result.stderr
