from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aipha.research import (
    ResearchConfig,
    ResearchPipeline,
    build_forward_labels,
    build_target_positions,
    load_research_config,
    validate_feature_frame,
    validate_market_frame,
    visible_financial_snapshots,
)
from aipha.research.backtest import run_portfolio_backtest
from aipha.research.contracts import FinancialSnapshot, TargetPosition
from aipha.research.pipeline import _filter_historical_universe
from aipha.research.artifacts import load_verified_selector


def _panel(n_days: int = 360, n_symbols: int = 8) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2022-01-03", periods=n_days)
    rows = []
    for symbol_idx in range(n_symbols):
        symbol = f"{600000 + symbol_idx:06d}.SH"
        price = 10.0 + symbol_idx
        for dt in dates:
            signal = rng.normal()
            ret = 0.0005 + 0.004 * signal + rng.normal(0, 0.005)
            price = max(1.0, price * (1.0 + ret))
            rows.append({
                "trade_date": dt,
                "symbol": symbol,
                "open": price * 0.999,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 100000.0,
                "amount": price * 100000.0,
                "adj_factor": 1.0,
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_index_member": True,
                "is_st": False,
                "listing_days": 1000,
                "feature_version": "cn_equity_v1",
                "momentum_signal": signal,
            })
    return pd.DataFrame(rows)


def test_default_yaml_is_canonical_and_safe() -> None:
    path = Path(__file__).parents[2] / "aipha" / "configs" / "research" / "a_stock_ml.yaml"
    config = load_research_config(path)
    assert config.data_provider == "tushare_pit"
    assert config.feature_version == "cn_equity_v1"
    assert config.rebalance == "monthly"
    assert config.forward_days == config.purge_days == 20
    assert config.label_skip_days == 1
    app_config = json.loads((Path(__file__).parents[3] / "config.json").read_text(encoding="utf-8"))
    assert app_config["tradingAccount"]["mode"] == "paper"
    assert app_config["tradingAccount"]["broker"] == "paper"
    assert app_config["tradingAccount"]["autoStart"] is False
    assert app_config["tradingAccount"]["confirmationRequired"] is True


def test_config_rejects_non_paper_and_non_pit_modes(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("data:\n  provider: akshare\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tushare_pit"):
        load_research_config(cfg)


def test_parquet_bridge_requires_provenance_sidecar(tmp_path: Path) -> None:
    from aipha.engines.aipha_ml_engine import _read_parquet, run

    assert run({"action": "list_models"})["data"]["models"] == ["lgbm", "linear"]
    path = tmp_path / "fixture.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(path)
    with pytest.raises(ValueError, match="sidecar required"):
        _read_parquet(str(path))
    path.with_suffix(".metadata.json").write_text(json.dumps({
        "provider": "akshare", "data_snapshot_id": "bad", "feature_version": "v1",
        "start_date": "2024-01-01", "end_date": "2024-01-02",
    }), encoding="utf-8")
    assert _read_parquet(str(path)).attrs["snapshot_metadata"]["provider"] == "akshare"


def test_market_frame_rejects_duplicate_rows_and_normalizes_dates() -> None:
    frame = _panel(n_days=1, n_symbols=1)
    normalized = validate_market_frame(frame)
    assert normalized.loc[0, "trade_date"].isoformat() == "2022-01-03"
    with pytest.raises(ValueError, match="duplicate"):
        validate_market_frame(pd.concat([frame, frame], ignore_index=True))


def test_feature_frame_requires_version_and_unique_symbol_date() -> None:
    frame = pd.DataFrame({
        "trade_date": ["2024-01-02"], "symbol": ["600000.SH"],
        "feature_version": ["cn_equity_v1"], "momentum": [0.2],
    })
    assert validate_feature_frame(frame).loc[0, "feature_version"] == "cn_equity_v1"
    with pytest.raises(ValueError, match="feature_version"):
        validate_feature_frame(frame.drop(columns="feature_version"))


def test_universe_is_filtered_using_row_level_historical_membership() -> None:
    panel = _panel(n_days=2, n_symbols=2)
    panel.loc[panel.index[0], "is_index_member"] = False
    panel.loc[panel.index[1], "is_st"] = True
    filtered = _filter_historical_universe(panel)
    assert len(filtered) == len(panel) - 2
    assert filtered["is_index_member"].all()
    assert not filtered["is_st"].any()


def test_forward_labels_respect_t_plus_one_skip() -> None:
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=4),
        "symbol": ["600000.SH"] * 4,
        "open": [10, 11, 12, 13], "high": [10, 11, 12, 13],
        "low": [10, 11, 12, 13], "close": [10, 12, 15, 20],
        "volume": [1, 1, 1, 1], "amount": [10, 12, 15, 20],
    })
    labelled = build_forward_labels(frame, horizon_days=1, skip_days=1)
    assert labelled.iloc[0]["fwd_return"] == pytest.approx(15 / 12 - 1)
    assert len(labelled) == 2
    assert "fwd_return" not in frame


def test_financial_facts_are_filtered_by_announcement_date() -> None:
    rows = [
        FinancialSnapshot("600000.SH", pd.Timestamp("2023-12-31").date(), pd.Timestamp("2024-03-20").date(), "roe", 0.1),
        FinancialSnapshot("600000.SH", pd.Timestamp("2024-03-31").date(), pd.Timestamp("2024-05-01").date(), "roe", 0.2),
    ]
    visible = visible_financial_snapshots(rows, pd.Timestamp("2024-04-01").date())
    assert [row.value for row in visible] == [0.1]


def test_portfolio_replay_executes_next_open_and_obeys_limit_up() -> None:
    market = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-30", periods=4),
        "symbol": ["600000.SH"] * 4,
        "open": [10.0, 10.0, 11.0, 12.0], "high": [10.2, 10.2, 11.2, 12.2],
        "low": [9.8, 9.8, 10.8, 11.8], "close": [10.0, 10.0, 11.0, 12.0],
        "volume": [100000] * 4, "amount": [1_000_000] * 4,
        "is_suspended": [False] * 4, "is_limit_up": [False, False, True, False],
        "is_limit_down": [False] * 4,
    })
    config = ResearchConfig(initial_capital=100_000, top_k=1, max_position_weight=0.5,
                            max_daily_turnover=1.0, commission_min=0, slippage_bps=0)
    target = TargetPosition(pd.Timestamp("2024-01-30").date(), "600000.SH", 0.5, "rank")
    result = run_portfolio_backtest(market, pd.DataFrame(), [target], config)
    assert result["trades"][0]["trade_date"] == "2024-01-31"
    assert result["trades"][0]["signal_date"] == "2024-01-30"
    assert result["trades"][0]["side"] == "buy"
    assert result["metrics"]["portfolio_costs"] > 0


def test_targets_are_monthly_top_k_and_weight_capped() -> None:
    rows = []
    for day, score in [("2024-01-10", 1), ("2024-01-31", 3), ("2024-02-29", 2)]:
        for i in range(5):
            rows.append({"trade_date": day, "symbol": str(i), "score": score + i})
    targets = build_target_positions(pd.DataFrame(rows), top_k=4, max_position_weight=0.05)
    assert sorted({t.rebalance_date.isoformat() for t in targets}) == ["2024-01-31", "2024-02-29"]
    assert len(targets) == 8
    assert all(t.target_weight <= 0.05 for t in targets)


def test_legacy_model_without_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest missing"):
        load_verified_selector(tmp_path, expected_feature_version="cn_equity_v1")


def test_end_to_end_oos_run_writes_reproducible_artifacts(tmp_path: Path) -> None:
    pytest.importorskip("lightgbm")
    cfg = ResearchConfig(
        train_years=1,
        test_months=1,
        forward_days=5,
        label_skip_days=1,
        purge_days=10,
        top_k=4,
        max_position_weight=0.25,
        n_estimators=5,
    )
    pipeline = ResearchPipeline(cfg)
    fixture = _panel(n_symbols=20)
    fixture.attrs["snapshot_metadata"] = {
        "provider": "tushare_pit", "data_snapshot_id": "fixture-v1",
        "feature_version": "cn_equity_v1",
        "start_date": "2022-01-03", "end_date": "2023-05-19",
    }
    result = pipeline.run(fixture, tmp_path / "runs", data_snapshot_id="fixture-v1")
    artifact_dir = Path(result["artifact_dir"])
    assert result["data_snapshot_id"] == "fixture-v1"
    with pytest.raises(ValueError, match="metadata"):
        pipeline.run(_panel(n_symbols=20), tmp_path / "missing-metadata", data_snapshot_id="fixture-v1")
    assert (artifact_dir / "model" / "model_manifest.json").is_file()
    assert (artifact_dir / "oos_predictions.parquet").is_file()
    assert (artifact_dir / "target_positions.json").is_file()
    manifest = json.loads((artifact_dir / "run_manifest.json").read_text(encoding="utf-8"))
    report = json.loads((artifact_dir / "report.json").read_text(encoding="utf-8"))
    assert manifest["data_snapshot_id"] == "fixture-v1"
    assert manifest["feature_version"] == "cn_equity_v1"
    assert np.isfinite(report["metrics"]["rank_ic"])
    assert 0.0 <= report["metrics"]["pbo_proxy"] <= 1.0
    assert np.isfinite(report["metrics"]["portfolio_total_return"])
    assert (artifact_dir / "portfolio_returns.parquet").is_file()
    assert (artifact_dir / "trades.json").is_file()
    assert report["walk_forward"]["n_folds"] >= 1
    model_manifest = json.loads((artifact_dir / "model" / "model_manifest.json").read_text(encoding="utf-8"))
    assert "momentum_signal" in model_manifest["feature_columns"]
    assert "is_limit_up" not in model_manifest["feature_columns"]
    assert "fwd_return" not in model_manifest["feature_columns"]
    selector = load_verified_selector(
        artifact_dir / "model", expected_feature_version="cn_equity_v1",
        expected_feature_columns=model_manifest["feature_columns"], expected_model_type="lightgbm",
    )
    assert np.isfinite(selector.predict(fixture.head(20)).to_numpy()).all()


def test_saved_selector_rejects_feature_version_or_column_order_mismatch(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "model_type": "lightgbm",
        "feature_version": "cn_equity_v1",
        "feature_columns": ["a", "b"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="version mismatch"):
        load_verified_selector(model_dir, expected_feature_version="cn_equity_v2")
    with pytest.raises(ValueError, match="columns/order"):
        load_verified_selector(
            model_dir,
            expected_feature_version="cn_equity_v1",
            expected_feature_columns=["b", "a"],
        )


def test_linear_elasticnet_baseline_is_a_real_walk_forward_model() -> None:
    pytest.importorskip("sklearn")
    cfg = ResearchConfig(train_years=1, test_months=1, forward_days=5,
                         label_skip_days=1, purge_days=10, model_type="elasticnet")
    oos, summary, selector = ResearchPipeline(cfg).train_oos(_panel(n_days=360, n_symbols=20))
    assert not oos.empty
    assert selector.config.model_type == "elasticnet"
    assert summary["model_type"] == "elasticnet"
