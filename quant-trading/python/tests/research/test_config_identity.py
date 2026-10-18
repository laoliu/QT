from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from aipha.engines import aipha_ml_engine as engine
from aipha.research import ResearchConfig, ResearchPipeline, load_research_config
from aipha.research.snapshots import TushareSnapshot, attest_snapshot_metadata
from aipha.universe.indices import universe_index_code


def _frame(*, universe="csi1000", version="cn_equity_v1", provider="tushare_pit", **metadata_overrides):
    frame = pd.DataFrame({
        "trade_date": ["2024-01-03"], "symbol": ["600000.SH"],
        "is_index_member": True, "is_st": False, "is_suspended": False,
        "listing_days": 400, "listing_days_basis": "exchange_open_sessions",
        "feature_version": version, "price_adjustment": "point_in_time_factor", "factor": 1.0,
    })
    metadata = {"provider": provider, "data_snapshot_id": "identity-fixture", "feature_version": version,
                "start_date": "2024-01-03", "end_date": "2024-01-03", "index_code": universe_index_code(universe),
                "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                                     "start_date": "2024-01-02", "end_date": "2024-01-03", "open_sessions": ["2024-01-03"]}}
    metadata.update(metadata_overrides)
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, metadata)
    return frame


@pytest.mark.parametrize("name,expected", [("CSI300", "000300.SH"), ("hs300", "000300.SH"),
                                         ("CSI500", "000905.SH"), ("zz500", "000905.SH"),
                                         ("CSI1000", "000852.SH"), ("CSI2000", "932000.CSI")])
def test_configured_universe_drives_tushare_membership_and_default_benchmark(name, expected, monkeypatch, tmp_path):
    import aipha.research.snapshots as snapshots

    cfg = ResearchConfig(universe=name)
    observed = {}
    def build(provider, spec, **kwargs):
        observed["spec"] = spec
        return TushareSnapshot(pd.DataFrame(), {"data_snapshot_id": "fixture"})
    monkeypatch.setattr(snapshots, "build_tushare_snapshot", build)
    pipeline = ResearchPipeline(cfg)
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: {"run_id": "fixture"})
    pipeline.run_tushare(object(), tmp_path, start_date="20240102", end_date="20240103", pit_provider=object())
    assert observed["spec"].index_code == expected
    assert observed["spec"].benchmark_code == expected


def test_explicit_index_mismatch_fails_before_fetching(monkeypatch, tmp_path):
    import aipha.research.snapshots as snapshots

    monkeypatch.setattr(snapshots, "build_tushare_snapshot", lambda *a, **k: pytest.fail("fetched mismatched universe"))
    with pytest.raises(ValueError, match="conflicts with research universe"):
        ResearchPipeline(ResearchConfig(universe="csi1000")).run_tushare(
            object(), tmp_path, start_date="20240102", end_date="20240103", index_code="000905.SH",
            pit_provider=object(),
        )


@pytest.mark.parametrize("index_code", ["000905.SH", None])
def test_offline_research_rejects_wrong_or_undeclared_snapshot_index_before_training(index_code, monkeypatch, tmp_path):
    frame = _frame(index_code=index_code).assign(open=10., high=10., low=10., close=10., volume=100., amount=1000.)
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, frame.attrs["snapshot_metadata"])
    pipeline = ResearchPipeline(ResearchConfig(universe="csi1000"))
    monkeypatch.setattr(pipeline, "train_oos", lambda *a: pytest.fail("trained on wrong historical universe"))
    with pytest.raises(ValueError, match="universe mismatch"):
        pipeline.run(frame, tmp_path)


@pytest.mark.parametrize("override,expected", [(None, "elasticnet"), ("linear", "elasticnet"), ("lgbm", "lightgbm")])
def test_training_bridge_keeps_yaml_model_unless_explicitly_overridden(override, expected, monkeypatch):
    import aipha.research

    monkeypatch.setattr(engine, "_load_config", lambda _: ResearchConfig(model_type="elasticnet", objective="regression"))
    monkeypatch.setattr(engine, "_read_parquet", lambda _: pd.DataFrame())
    observed = {}
    class Pipeline:
        def __init__(self, config): observed["config"] = config
        def run(self, *a, **k):
            return {"run_envelope": {}, "run_id": "fixture", "strategy_id": "fixture", "strategy_version": "1",
                    "data_as_of": "2024-01-03"}
    monkeypatch.setattr(aipha.research, "ResearchPipeline", Pipeline)
    result = engine.run({"action": "train", "modelType": override})
    assert result["ok"]
    assert observed["config"].model_type == expected
    assert observed["config"].objective == ("regression" if expected == "elasticnet" else "lambdarank")


@pytest.mark.parametrize("action", ["train", "predict"])
def test_unknown_explicit_model_is_not_silently_lightgbm(action, monkeypatch):
    monkeypatch.setattr(engine, "_read_parquet", lambda _: pytest.fail("read before rejecting invalid model"))
    result = engine.run({"action": action, "modelType": "gru"})
    assert not result["ok"] and "modelType must be" in result["error"]


def test_minimal_yaml_uses_documented_provider_default_and_linear_objective(tmp_path):
    path = tmp_path / "research.yaml"
    path.write_text("model:\n  type: linear\nuniverse:\n  base_index: CSI1000\n", encoding="utf-8")
    config = load_research_config(path)
    assert config.model_type == "elasticnet" and config.objective == "regression"
    assert config.data_provider == "tushare_pit" and config.universe == "csi1000"
    assert ResearchConfig(universe="zz500").model_config_hash() == ResearchConfig(universe="csi500").model_config_hash()
    with pytest.raises(ValueError, match="canonical universe"):
        ResearchConfig(universe="current_top_stocks")
    with pytest.raises(ValueError, match="tushare_pit"):
        ResearchConfig(data_provider="akshare")


def test_prediction_checks_yaml_model_and_input_snapshot_identity(monkeypatch):
    import aipha.research

    monkeypatch.setattr(engine, "_load_config", lambda _: ResearchConfig(universe="csi1000", model_type="elasticnet"))
    monkeypatch.setattr(engine, "_read_parquet", lambda _: _frame())
    observed = {}
    def load(*a, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(predict=lambda frame: pd.Series([1.] * len(frame)),
                               research_manifest={"model_id": "fixture-model", "feature_version": "cn_equity_v1"})
    monkeypatch.setattr(aipha.research, "load_verified_selector", load)
    result = engine.run({"action": "predict", "modelPath": "fixture", "featurePath": "fixture"})
    assert result["ok"]
    assert observed["expected_model_type"] == "elasticnet"
    assert observed["expected_universe"] == "csi1000"
    assert observed["expected_min_listing_days"] == 252
    assert observed["expected_feature_groups"] == ResearchConfig().feature_groups
    assert observed["expected_feature_column_groups"] == ()
    assert result["data"]["rankings"][0]["symbol"] == "600000.SH"
    assert result["data"]["rankings"][0]["model_id"] == "fixture-model"
    assert result["data"]["rankings"][0]["feature_version"] == "cn_equity_v1"


@pytest.mark.parametrize("changes,error", [
    ({"universe": "csi500"}, "universe mismatch"),
    ({"provider": "akshare"}, "provider must be"),
    ({"version": "cn_equity_v2"}, "feature_version"),
    ({"start_date": "2024-01-02"}, "date range"),
])
def test_invalid_prediction_snapshot_rejected_before_model_load(changes, error, monkeypatch):
    import aipha.research

    monkeypatch.setattr(engine, "_load_config", lambda _: ResearchConfig(universe="csi1000"))
    monkeypatch.setattr(engine, "_read_parquet", lambda _: _frame(**changes))
    monkeypatch.setattr(aipha.research, "load_verified_selector", lambda *a, **k: pytest.fail("loaded invalid snapshot"))
    result = engine.run({"action": "predict", "modelPath": "fixture", "featurePath": "fixture"})
    assert not result["ok"] and error in result["error"]
