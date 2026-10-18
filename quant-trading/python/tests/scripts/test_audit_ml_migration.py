from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import pytest
import yaml

from scripts.audit_ml_migration import audit_config, audit_tree, main


def _config(tmp_path, raw, name="config.yml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.fixture
def canonical_inputs(tmp_path, monkeypatch):
    from aipha.research.artifacts import write_model_manifest
    from aipha.research.snapshots import write_attested_parquet
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector, _ElasticNetWrapper
    from sklearn.linear_model import ElasticNet
    from sklearn.preprocessing import StandardScaler

    monkeypatch.setenv("AIPHA_PIT_SNAPSHOT_KEY", "offline-audit-test-key-never-use-in-production")
    frame = pd.DataFrame({
        "trade_date": ["2024-01-03", "2024-01-04"], "symbol": ["600000.SH"] * 2,
        "is_index_member": True, "is_st": False, "is_suspended": False,
        "listing_days": 400, "listing_days_basis": "exchange_open_sessions",
        "feature_version": "cn_equity_v1", "price_adjustment": "point_in_time_factor",
        "momentum_20": [.1, .2],
    })
    metadata = {
        "provider": "tushare_pit", "data_snapshot_id": "audit-snapshot", "feature_version": "cn_equity_v1",
        "start_date": "2024-01-03", "end_date": "2024-01-04", "index_code": "000905.SH",
        "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                             "start_date": "2024-01-02", "end_date": "2024-01-04",
                             "open_sessions": ["2024-01-03", "2024-01-04"]},
    }
    write_attested_parquet(frame, tmp_path / "snapshot.parquet", metadata)
    model = tmp_path / "model"
    matrix = frame[["momentum_20"]].to_numpy()
    scaler = StandardScaler().fit(matrix)
    fitted = ElasticNet().fit(scaler.transform(matrix), [.05, .1])
    selector = MLStockSelector(MLSelectorConfig(model_type="elasticnet", feature_cols=["momentum_20"]))
    selector._models = [_ElasticNetWrapper(fitted, scaler, ["momentum_20"])]
    selector.save(model)
    write_model_manifest(
        model, model_type="elasticnet", feature_version="cn_equity_v1", feature_cols=["momentum_20"],
        model_config_hash="audit-config", universe="CSI500", min_listing_days=252,
        feature_groups=["momentum"], feature_column_groups={}, model_id="audit-model",
        data_snapshot_id="training-snapshot",
    )
    return {
        "feature_snapshot_path": "snapshot.parquet",
        "model_dir": "model",
        "universe": "CSI500", "feature_groups": ["momentum"],
    }


def test_audit_verifies_real_contracts_without_deserializing(tmp_path, monkeypatch, canonical_inputs):
    from aipha.models.ml_stock_selector import MLStockSelector

    monkeypatch.setattr(MLStockSelector, "load", lambda *a, **k: pytest.fail("audit must not load pickle"))
    result = audit_config(_config(tmp_path, canonical_inputs), project_root=tmp_path)
    assert result["status"] == "canonical"
    assert result["model_manifests"][0]["verified"] is True
    assert result["scopes"][0]["inference_exercised"] is False
    assert result["scopes"][0]["trade_date"] == "2024-01-04"
    assert result["model_manifests"][0]["missing_rates"] == {"momentum_20": 0.0}


@pytest.mark.parametrize("raw", [
    {"feature_snapshot_path": "missing.parquet"},
    {"feature_snapshot_path": "missing.parquet", "model_dir": "model", "universe": "CSI500"},
    {"model_dirs": [], "feature_snapshot_path": "missing.parquet"},
])
def test_missing_inputs_and_empty_manifest_cannot_pass(tmp_path, raw):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model_manifest.json").write_text("{}", encoding="utf-8")
    result = audit_config(_config(tmp_path, raw), project_root=tmp_path)
    assert result["status"] == "unmigrated"
    assert result["scopes"][0]["error"]


@pytest.mark.parametrize("override,error", [
    ({"universe": "CSI1000"}, "universe mismatch"),
    ({"min_listing_days": 300}, "min_listing_days"),
    ({"feature_groups": ["value"]}, "feature_groups"),
    ({"feature_version": "different"}, "feature version mismatch"),
    ({"model_dirs": ["model", "missing"]}, "manifest missing"),
    ({"model_dirs": ["model", 123]}, "invalid model_dirs"),
])
def test_audit_rejects_policy_and_input_mismatch(tmp_path, canonical_inputs, override, error):
    result = audit_config(_config(tmp_path, {**canonical_inputs, **override}), project_root=tmp_path)
    assert result["status"] == "unmigrated"
    assert error in result["scopes"][0]["error"]


@pytest.mark.parametrize("mutation,error", [
    ("model_bytes", "hashes"), ("snapshot_bytes", "artifact_sha256"),
    ("signature", "signature"), ("old_schema", "canonical snapshot builder"),
    ("calendar", "open_sessions"), ("missing_column", "missing model feature"),
    ("manifest_list", "JSON object"), ("empty_manifest", "schema_version"),
])
def test_audit_rejects_corrupt_or_incompatible_materials(tmp_path, canonical_inputs, mutation, error):
    from aipha.research.snapshots import write_attested_parquet

    snapshot = tmp_path / "snapshot.parquet"
    sidecar = snapshot.with_suffix(".metadata.json")
    if mutation == "model_bytes":
        (tmp_path / "model" / "meta.pkl").write_bytes(b"changed")
    elif mutation == "snapshot_bytes":
        with snapshot.open("ab") as stream:
            stream.write(b"changed")
    elif mutation in {"manifest_list", "empty_manifest"}:
        (tmp_path / "model" / "model_manifest.json").write_text(
            "[]" if mutation == "manifest_list" else "{}", encoding="utf-8",
        )
    else:
        frame = pd.read_parquet(snapshot)
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if mutation == "signature":
            metadata["data_snapshot_id"] = "tampered"
            sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        elif mutation == "calendar":
            metadata["listing_calendar"]["open_sessions"] = ["2024-01-04"]
            write_attested_parquet(frame, snapshot, metadata)
        elif mutation == "missing_column":
            write_attested_parquet(frame.drop(columns="momentum_20"), snapshot, metadata)
        elif mutation == "old_schema":
            from aipha.research.snapshots import sign_snapshot_metadata
            metadata["generator_version"] = "aipha.research.snapshots/3"
            sidecar.write_text(json.dumps(sign_snapshot_metadata(metadata)), encoding="utf-8")
    result = audit_config(_config(tmp_path, canonical_inputs), project_root=tmp_path)
    assert result["status"] == "unmigrated"
    assert error in result["scopes"][0]["error"]


def test_template_requires_date_and_does_not_reuse_latest_signal(tmp_path, canonical_inputs):
    from shutil import copyfile

    for suffix in [".parquet", ".metadata.json"]:
        copyfile(tmp_path / ("snapshot" + suffix), tmp_path / ("20240104" + suffix))
    config = _config(tmp_path, {**canonical_inputs, "feature_snapshot_path": "{trade_date}.parquet"})
    assert audit_config(config, project_root=tmp_path)["status"] == "unmigrated"
    assert audit_config(config, project_root=tmp_path, trade_date="2024-01-04")["status"] == "canonical"
    fixed = _config(tmp_path, canonical_inputs)
    result = audit_config(fixed, project_root=tmp_path, trade_date="2024-01-05")
    assert result["status"] == "unmigrated" and "no rows" in result["scopes"][0]["error"]


def test_components_cannot_borrow_other_components_provenance_or_legacy_flag(tmp_path, canonical_inputs):
    config = _config(tmp_path, {"components": {
        "ready": canonical_inputs,
        "missing_snapshot": {"model_dir": "model", "universe": "CSI500"},
        "legacy": {"model_dir": "old", "legacy_ml_compat": True},
    }})
    result = audit_config(config, project_root=tmp_path)
    assert result["status"] == "mixed"
    assert {scope["scope"]: scope["status"] for scope in result["scopes"]} == {
        "$.components.ready": "canonical", "$.components.missing_snapshot": "unmigrated",
        "$.components.legacy": "legacy_compat",
    }


def test_legacy_flag_does_not_certify_canonical_materials(tmp_path, canonical_inputs):
    result = audit_config(_config(tmp_path, {**canonical_inputs, "legacy_ml_compat": True}), project_root=tmp_path)
    assert result["status"] == "legacy_compat"


def test_missing_signature_key_fails_closed(tmp_path, monkeypatch, canonical_inputs):
    monkeypatch.delenv("AIPHA_PIT_SNAPSHOT_KEY")
    result = audit_config(_config(tmp_path, canonical_inputs), project_root=tmp_path)
    assert result["status"] == "unmigrated"
    assert "AIPHA_PIT_SNAPSHOT_KEY" in result["scopes"][0]["error"]


def test_strict_gate_accepts_only_verified_components(tmp_path, canonical_inputs, capsys):
    # CLI relative paths resolve against its project, so keep these inputs
    # absolute when exercising the actual command boundary.
    raw = {**canonical_inputs,
           "model_dir": str(tmp_path / "model"),
           "feature_snapshot_path": str(tmp_path / "snapshot.parquet")}
    config_dir = tmp_path / "profiles"
    config_dir.mkdir()
    _config(config_dir, raw)
    assert main(["--config-dir", str(config_dir), "--strict"]) == 0
    _config(config_dir, {"legacy_ml_compat": True}, name="legacy.yaml")
    assert main(["--config-dir", str(config_dir), "--strict"]) == 2
    capsys.readouterr()


def test_audit_marks_explicit_legacy_config(tmp_path: Path) -> None:
    config = tmp_path / "legacy.yml"
    config.write_text(yaml.safe_dump({
        "strategy": {"legacy_ml_compat": True, "model_dir": "models/old"},
    }), encoding="utf-8")
    result = audit_config(config, project_root=tmp_path)
    assert result["status"] == "legacy_compat"
    assert result["legacy_references"]


def test_strict_audit_fails_when_legacy_configs_remain(tmp_path: Path, capsys) -> None:
    (tmp_path / "legacy.yml").write_text(
        yaml.safe_dump({"legacy_ml_compat": True}), encoding="utf-8",
    )
    assert main(["--config-dir", str(tmp_path), "--strict"]) == 2
    assert '"legacy_compat": 1' in capsys.readouterr().out


def test_audit_tree_reports_empty_directory(tmp_path: Path) -> None:
    result = audit_tree(tmp_path, project_root=tmp_path)
    assert result["total"] == 0
    assert result["counts"] == {}


@pytest.mark.parametrize("missing", [True, False])
def test_strict_rejects_empty_or_missing_directory(tmp_path, missing):
    directory = tmp_path / "absent" if missing else tmp_path
    assert main(["--config-dir", str(directory), "--strict"]) == 2


def test_parse_errors_are_reported_without_aborting_inventory(tmp_path):
    (tmp_path / "bad.yaml").write_text("a: [", encoding="utf-8")
    (tmp_path / "scalar.yaml").write_text("42", encoding="utf-8")
    _config(tmp_path, {"legacy_ml_compat": True})
    report = audit_tree(tmp_path, project_root=tmp_path)
    assert report["counts"] == {"parse_error": 2, "legacy_compat": 1}
    assert main(["--config-dir", str(tmp_path), "--strict"]) == 2


def test_repository_paper_ml_configs_are_marked_or_canonical() -> None:
    project_root = Path(__file__).resolve().parents[2]
    report = audit_tree(project_root / "configs" / "paper", project_root=project_root)
    assert report["counts"].get("unmigrated", 0) == 0
    assert report["counts"].get("legacy_compat", 0) == 20
    assert report["counts"].get("not_applicable", 0) == 16
