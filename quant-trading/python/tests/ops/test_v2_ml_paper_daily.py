"""Tests for V2 ML paper daily script core functions."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

_PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJ / "scripts" / "ops"))
sys.path.insert(0, str(_PROJ / "src"))

from v2_ml_paper_daily import (
    PaperState,
    _update_nav,
    add_fundamental_factors,
    ensemble_predict,
    execute_rebalance,
    _load_feature_panel,
    load_state,
    save_state,
)


# ── Fixtures ─────────────────────────────────────────
@pytest.fixture
def sample_state():
    return PaperState(
        cash=500_000.0,
        positions=[
            {"code": "000001.SZ", "shares": 1000,
             "avg_cost": 10.0, "entry_date": "20260401"},
            {"code": "000002.SZ", "shares": 500,
             "avg_cost": 20.0, "entry_date": "20260401"},
        ],
    )


@pytest.fixture
def sample_prices():
    return {"000001.SZ": 11.0, "000002.SZ": 19.0,
            "000003.SZ": 30.0, "000004.SZ": 15.0}


@pytest.fixture
def sample_ohlcv():
    """3天 × 5股的简单面板数据."""
    dates = pd.date_range("2026-03-01", periods=30, freq="B")
    symbols = [f"00000{i}.SZ" for i in range(1, 6)]
    rows = []
    for d in dates:
        for s in symbols:
            rows.append({
                "datetime": d, "symbol": s,
                "open": 10.0, "high": 11.0, "low": 9.0,
                "close": 10.5, "volume": 1e6,
                "pe_ttm": 15.0, "pb": 2.0,
                "total_mv": 5e9, "circ_mv": 3e9,
                "turnover_rate": 3.0,
                "dv_ratio": 1.5, "dv_ttm": 2.0,
            })
    return pd.DataFrame(rows)


# ── State Tests ──────────────────────────────────────
def test_load_state_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "v2_ml_paper_daily._STATE_DIR", tmp_path / "nonexistent")
    s = load_state()
    assert s.cash == 1_000_000.0
    assert s.positions == []


def test_save_and_load_state(tmp_path, monkeypatch):
    monkeypatch.setattr("v2_ml_paper_daily._STATE_DIR", tmp_path)
    s = PaperState(cash=800_000.0)
    s.positions = [{"code": "000001.SZ", "shares": 100,
                    "avg_cost": 10.0, "entry_date": "20260401"}]
    save_state(s)
    loaded = load_state()
    assert loaded.cash == 800_000.0
    assert len(loaded.positions) == 1


# ── NAV Tests ────────────────────────────────────────
def test_update_nav(sample_state, sample_prices):
    nav = _update_nav(sample_state, sample_prices, "20260403")
    expected = 500_000 + 1000 * 11.0 + 500 * 19.0
    assert nav == pytest.approx(expected, abs=1)
    assert len(sample_state.nav_history) == 1


# ── Rebalance Tests ──────────────────────────────────
def test_rebalance_sell_and_buy(sample_state, sample_prices):
    # Target: keep 000001.SZ, drop 000002.SZ, add 000003.SZ
    target = ["000001.SZ", "000003.SZ"]
    execute_rebalance(sample_state, target, sample_prices, "20260403")
    codes = {p["code"] for p in sample_state.positions}
    assert "000002.SZ" not in codes  # sold
    assert "000003.SZ" in codes       # bought
    assert "000001.SZ" in codes       # kept
    assert len(sample_state.trades) >= 1


def test_rebalance_empty_targets(sample_state, sample_prices):
    execute_rebalance(sample_state, ["000001.SZ", "000002.SZ"],
                      sample_prices, "20260403")
    # No new buys, no sells — targets match current holdings
    assert len(sample_state.positions) == 2


# ── Feature Tests ────────────────────────────────────
def test_add_fundamental_factors(sample_ohlcv):
    with pytest.raises(ValueError, match="规范 Paper 入口"):
        add_fundamental_factors(sample_ohlcv.copy())

    result = add_fundamental_factors(sample_ohlcv.copy(), allow_legacy=True)
    assert "cs_rank_pe_ttm" in result.columns
    assert "cs_rank_ep" in result.columns
    assert "pe_ttm_chg_5d" in result.columns
    assert result["cs_rank_pe_ttm"].notna().any()


def test_ensemble_predict_rejects_unmanifested_model_by_default(tmp_path, sample_ohlcv):
    model_dir = tmp_path / "legacy-model"
    model_dir.mkdir()
    with pytest.raises(ValueError, match="model_manifest.json"):
        ensemble_predict(sample_ohlcv.tail(5), [model_dir])


def test_ensemble_predict_uses_verified_canonical_model(tmp_path, sample_ohlcv, monkeypatch):
    model_dir = tmp_path / "canonical-model"
    model_dir.mkdir()
    (model_dir / "model_manifest.json").write_text("{}", encoding="utf-8")

    class _Selector:
        def predict(self, frame):
            return pd.Series(np.arange(len(frame), dtype=float), index=frame.index)

    captured = {}
    def load(path, **kwargs):
        captured.update(kwargs)
        return _Selector()
    monkeypatch.setattr("aipha.research.load_verified_selector", load)
    result = ensemble_predict(sample_ohlcv.tail(5), [model_dir])
    assert result.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert captured["expected_universe"] == "csi500"
    assert captured["expected_min_listing_days"] == 252
    assert captured["expected_feature_groups"]


def test_load_feature_panel_requires_attested_pit_snapshot(tmp_path):
    from aipha.research.snapshots import write_attested_parquet

    frame = pd.DataFrame({
        "trade_date": ["2026-03-02"], "symbol": ["000001.SZ"],
        "open": [10.0], "high": [10.5], "low": [9.8], "close": [10.2],
        "volume": [1000.0], "amount": [10000.0], "adj_factor": [1.0],
        "is_suspended": [False], "is_limit_up": [False], "is_limit_down": [False],
        "is_index_member": [True], "is_st": [False], "listing_days": [500],
        "listing_days_basis": ["exchange_open_sessions"],
        "price_adjustment": ["point_in_time_factor"],
        "feature_version": ["cn_equity_v1"],
    })
    path = tmp_path / "snapshot.parquet"
    write_attested_parquet(
        frame,
        path,
        {
            "provider": "tushare_pit", "feature_version": "cn_equity_v1",
            "data_snapshot_id": "fixture", "start_date": "2026-03-02",
            "end_date": "2026-03-02", "index_code": "000905.SH",
        },
    )
    loaded = _load_feature_panel(path, feature_version="cn_equity_v1")
    assert loaded.loc[0, "datetime"] == pd.Timestamp("2026-03-02")
    from aipha.research import ResearchConfig
    with pytest.raises(ValueError, match="universe mismatch"):
        _load_feature_panel(path, feature_version="cn_equity_v1", research_config=ResearchConfig(universe="csi1000"))
    older_threshold = _load_feature_panel(path, feature_version="cn_equity_v1", research_config=ResearchConfig(min_listing_days=600))
    assert older_threshold.empty

    path.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="sidecar|provider"):
        _load_feature_panel(path, feature_version="cn_equity_v1")
