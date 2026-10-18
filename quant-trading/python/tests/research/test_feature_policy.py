"""Canonical feature selection and persisted training-policy boundaries."""
from __future__ import annotations

import json
import errno
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aipha.features.schema import FEATURE_GROUPS, normalize_column_groups, select_feature_columns
from aipha.research import ResearchConfig, ResearchPipeline, load_research_config
from aipha.research.artifacts import load_verified_selector, write_model_manifest
from aipha.research.migration import migrate_legacy_model
from aipha.research.snapshots import build_cn_equity_features


def test_groups_control_training_columns_and_report_unavailable_groups(monkeypatch):
    from aipha.models.ml_stock_selector import MLStockSelector

    frame = pd.DataFrame({
        "trade_date": ["2024-01-03"], "symbol": ["600000.SH"],
        "is_index_member": [True], "is_st": [False], "is_suspended": [False],
        "listing_days": [300], "listing_days_basis": ["exchange_open_sessions"],
        "momentum_20": [0.2], "momentum_5": [0.1], "factor_pb": [2.0],
        "kmid": [0.02], "roe": [0.15], "unknown_numeric": [1.0],
        "fwd_return_rank": [0.5], "fwd_return": [0.01],
    })
    captured = []
    def train(self, panel):
        captured.append(self.config.feature_cols)
        return panel
    monkeypatch.setattr(MLStockSelector, "walk_forward_train", train)
    monkeypatch.setattr(MLStockSelector, "get_wf_summary", lambda self: {})
    _, report, _ = ResearchPipeline(ResearchConfig(feature_groups=("momentum", "quality"))).train_oos(frame)
    assert captured[-1] == ["momentum_20", "momentum_5"]
    assert report["feature_selection"]["unavailable_groups"] == ["quality"]
    ResearchPipeline(ResearchConfig(feature_groups=("value", "technical"))).train_oos(frame)
    assert captured[-1] == ["factor_pb", "kmid"]
    ResearchPipeline(ResearchConfig(feature_groups=("quality",),
                     feature_column_groups=(("roe", "quality"),))).train_oos(frame)
    assert captured[-1] == ["roe"]


@pytest.mark.parametrize("groups", [[], "momentum", ["unknown"], ["value", "value"], [1]])
def test_invalid_group_policy_is_rejected(groups):
    with pytest.raises(ValueError, match="feature_groups"):
        ResearchConfig(feature_groups=groups)


@pytest.mark.parametrize("mapping", [
    {"close": "momentum"}, {"future_raw": "value"}, {"label_profit": "quality"},
    {"target_weight": "value"}, {"fwd_return": "value"}, {"momentum_5": "value"},
    {"custom": "unknown"}, [("custom", "value"), ("custom", "quality")],
])
def test_reserved_or_ambiguous_custom_features_are_rejected(mapping):
    with pytest.raises(ValueError):
        normalize_column_groups(mapping)


def test_feature_order_is_stable_custom_columns_are_explicit_and_numeric():
    frame = pd.DataFrame({"unknown": [1.], "momentum_5": [.2], "alpha_custom": [.3]})
    columns, _ = select_feature_columns(frame, ["momentum"], {"alpha_custom": "momentum"})
    reverse, _ = select_feature_columns(frame[frame.columns[::-1]], ["momentum"], {"alpha_custom": "momentum"})
    assert columns == reverse == ["alpha_custom", "momentum_5"]
    with pytest.raises(ValueError, match="no registered"):
        select_feature_columns(frame, ["value"])
    with pytest.raises(ValueError, match="numeric"):
        select_feature_columns(frame.assign(momentum_5="bad"), ["momentum"])


def test_yaml_custom_groups_are_normalized_and_change_config_hash(tmp_path):
    config = tmp_path / "research.yaml"
    config.write_text("features:\n  groups: [momentum]\n  column_groups:\n    my_alpha: momentum\n", encoding="utf-8")
    policy = load_research_config(config)
    assert policy.feature_column_groups == (("my_alpha", "momentum"),)
    assert policy.model_config_hash() != ResearchConfig(feature_groups=("momentum",)).model_config_hash()


def test_alpha158_snapshot_features_align_multiple_symbols_and_are_causal():
    from aipha.factors.qlib_alpha import Alpha158

    days = pd.bdate_range("2023-01-02", periods=75)
    frames = []
    for offset, symbol in enumerate(["600001.SH", "600000.SH"]):
        price = 10. + offset * 20 + np.arange(len(days)) * .2 + np.sin(np.arange(len(days)))
        frames.append(pd.DataFrame({
            "trade_date": days, "symbol": symbol, "open": price * .99, "high": price * 1.02,
            "low": price * .98, "close": price, "volume": 1000. + np.arange(len(days)),
            "amount": price * 1000.,
        }))
    tape = pd.concat(frames).sample(frac=1., random_state=1).reset_index(drop=True)
    base = build_cn_equity_features(tape)
    names = Alpha158().factor_names
    assert len(names) == 158 and set(names) <= set(base)
    oracle = Alpha158().compute(tape.assign(datetime=tape.trade_date))
    pd.testing.assert_frame_equal(base[names], oracle[names].replace([np.inf, -np.inf], np.nan))
    changed = tape.copy()
    future = changed.trade_date >= days[65]
    changed.loc[future, ["open", "high", "low", "close", "volume", "amount"]] *= 3
    revised = build_cn_equity_features(changed)
    pd.testing.assert_frame_equal(base.loc[base.trade_date < days[65], names],
                                  revised.loc[revised.trade_date < days[65], names])


@pytest.fixture
def fitted_model(tmp_path):
    pytest.importorskip("sklearn")
    from sklearn.linear_model import ElasticNet
    from sklearn.preprocessing import StandardScaler
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector, _ElasticNetWrapper

    matrix = np.arange(40., dtype=float).reshape(20, 2)
    scaler = StandardScaler().fit(matrix)
    model = ElasticNet(alpha=.01).fit(scaler.transform(matrix), matrix[:, 0] * .1)
    cols = ["momentum_20", "momentum_5"]
    selector = MLStockSelector(MLSelectorConfig(model_type="elasticnet", feature_cols=cols))
    selector._models = [_ElasticNetWrapper(model, scaler, cols)]
    folder = tmp_path / "model"
    selector.save(folder)
    write_model_manifest(
        folder, model_type="elasticnet", feature_version="cn_equity_v1", feature_cols=cols,
        model_config_hash="fixture-config", universe="CSI500", min_listing_days=252,
        feature_groups=["momentum"], feature_column_groups={}, model_id="fixture-model",
        data_snapshot_id="fixture-snapshot",
    )
    return folder


@pytest.mark.parametrize("expected,error", [
    ({"expected_universe": "csi1000"}, "universe"),
    ({"expected_min_listing_days": 300}, "min_listing_days"),
    ({"expected_feature_groups": FEATURE_GROUPS}, "feature_groups"),
    ({"expected_feature_column_groups": {"custom": "momentum"}}, "column_groups"),
])
def test_training_policy_mismatch_rejected_before_deserialization(fitted_model, monkeypatch, expected, error):
    from aipha.models.ml_stock_selector import MLStockSelector

    monkeypatch.setattr(MLStockSelector, "load", lambda *a, **k: pytest.fail("deserialized mismatched model"))
    with pytest.raises(ValueError, match=error):
        load_verified_selector(fitted_model, expected_feature_version="cn_equity_v1", **expected)


@pytest.mark.parametrize("filename", ["meta.pkl", "fold_0.pkl", "fold_1.pkl"])
def test_model_mutation_or_extra_fold_rejected_before_deserialization(fitted_model, monkeypatch, filename):
    from aipha.models.ml_stock_selector import MLStockSelector

    (fitted_model / filename).write_bytes(b"changed-or-extra-file")
    monkeypatch.setattr(MLStockSelector, "load", lambda *a, **k: pytest.fail("deserialized corrupt model"))
    with pytest.raises(ValueError, match="hashes"):
        load_verified_selector(fitted_model, expected_feature_version="cn_equity_v1")


def test_schema_one_is_rejected_and_verified_prediction_requires_every_column(fitted_model):
    selector = load_verified_selector(fitted_model, expected_feature_version="cn_equity_v1")
    frame = pd.DataFrame({"momentum_5": [.3], "momentum_20": [.2], "symbol": ["600000.SH"]})
    assert np.isfinite(selector.predict(frame)).all()
    with pytest.raises(ValueError, match="missing feature columns"):
        selector.predict(frame.drop(columns="momentum_20"))
    path = fitted_model / "model_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["schema_version"] = 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version=2"):
        load_verified_selector(fitted_model, expected_feature_version="cn_equity_v1")


def test_previous_financial_feature_policy_cannot_score_corrected_features(fitted_model, monkeypatch):
    from aipha.models.ml_stock_selector import MLStockSelector

    path = fitted_model / "model_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["feature_policy_version"] = "cn_equity_feature_groups/1"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(MLStockSelector, "load", lambda *a, **k: pytest.fail("loaded old financial semantics"))
    with pytest.raises(ValueError, match="feature_policy_version"):
        load_verified_selector(fitted_model, expected_feature_version="cn_equity_v1")


def test_manifest_and_meta_cannot_override_fitted_fold_column_order(fitted_model):
    import joblib
    from aipha.research.artifacts import model_file_hashes

    meta = joblib.load(fitted_model / "meta.pkl")
    meta["config"].feature_cols.reverse()
    joblib.dump(meta, fitted_model / "meta.pkl")
    path = fitted_model / "model_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["feature_columns"].reverse()
    manifest["model_files"] = model_file_hashes(fitted_model)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="fitted fold feature columns/order"):
        load_verified_selector(fitted_model, expected_feature_version="cn_equity_v1")


def test_explicit_migration_validates_copy_and_preserves_source(fitted_model, tmp_path):
    (fitted_model / "model_manifest.json").unlink()
    before = {file.name: file.read_bytes() for file in fitted_model.iterdir()}
    destination = tmp_path / "migrated"
    kwargs = dict(feature_version="cn_equity_v1", feature_columns=["momentum_20", "momentum_5"],
                  model_type="elasticnet", data_snapshot_id="attested-snapshot", acknowledge_legacy=True)
    with pytest.raises(ValueError, match="explicitly"):
        migrate_legacy_model(fitted_model, destination, **kwargs)
    assert not destination.exists()
    kwargs.update(universe="CSI500", min_listing_days=252, feature_groups=["momentum"],
                  model_id="attested-model", model_config_hash="attested-config")
    migrate_legacy_model(fitted_model, destination, **kwargs)
    selector = load_verified_selector(destination, expected_feature_version="cn_equity_v1")
    assert selector.research_manifest["migration"]["legacy_attested"] is True
    assert selector.research_manifest["model_id"] == "attested-model"
    assert before == {file.name: file.read_bytes() for file in fitted_model.iterdir()}
    with pytest.raises(FileExistsError):
        migrate_legacy_model(fitted_model, destination, **kwargs)
    kwargs["feature_columns"] = ["momentum_5", "momentum_20"]
    with pytest.raises(ValueError, match="metadata feature columns"):
        migrate_legacy_model(fitted_model, tmp_path / "invalid", **kwargs)
    assert not (tmp_path / "invalid").exists()


def test_canonical_paper_passes_its_policy_to_model_loader(fitted_model, monkeypatch):
    from aipha.runner.scorers import _predict_ranks

    cross = pd.DataFrame({"momentum_20": [.1, .2], "momentum_5": [.3, .4], "symbol": ["a", "b"]})
    cross.attrs.update(canonical_pit=True, universe="csi500", min_listing_days=252,
                       feature_version="cn_equity_v1", feature_groups=("momentum",), feature_column_groups=())
    assert set(_predict_ranks(fitted_model, cross).index) == {"a", "b"}
    cross.attrs["min_listing_days"] = 300
    with pytest.raises(ValueError, match="min_listing_days"):
        _predict_ranks(fitted_model, cross)


@pytest.mark.parametrize("persistent", [False, True])
def test_migration_publication_handles_windows_sharing_lock(tmp_path, monkeypatch, persistent):
    import aipha.research.migration as migration
    from aipha.research.migration import _publish_model

    staged, destination = tmp_path / "staged", tmp_path / "published"
    staged.mkdir()
    (staged / "artifact").write_bytes(b"preserved")
    rename = migration._rename_exclusive
    attempts = []
    def locked_rename(path, target):
        attempts.append(path)
        if persistent or len(attempts) == 1:
            error = PermissionError("Windows directory sharing lock")
            error.winerror = 5
            raise error
        return rename(path, target)
    monkeypatch.setattr(migration, "_rename_exclusive", locked_rename)
    monkeypatch.setattr("aipha.research.migration.time.sleep", lambda _: None)
    if persistent:
        with pytest.raises(PermissionError):
            _publish_model(staged, destination)
        assert len(attempts) == 5
        assert not destination.exists()
        assert (staged / "artifact").read_bytes() == b"preserved"
    else:
        _publish_model(staged, destination)
        assert len(attempts) == 2
        assert not staged.exists()
        assert (destination / "artifact").read_bytes() == b"preserved"


def test_racing_empty_destination_is_never_replaced(tmp_path, monkeypatch):
    import aipha.research.migration as migration

    staged, destination = tmp_path / "staged", tmp_path / "published"
    staged.mkdir()
    (staged / "artifact").write_bytes(b"staged model")
    rename = migration._rename_exclusive
    def race(path, target):
        target.mkdir()  # Another publisher wins after the preliminary check.
        return rename(path, target)
    monkeypatch.setattr(migration, "_rename_exclusive", race)
    with pytest.raises(FileExistsError):
        migration._publish_model(staged, destination)
    assert list(destination.iterdir()) == []
    assert (staged / "artifact").read_bytes() == b"staged model"


@pytest.mark.parametrize("code", [errno.EEXIST, errno.ENOSYS, errno.EINVAL])
def test_linux_exclusive_rename_failure_never_falls_back_to_clobbering(tmp_path, monkeypatch, code):
    import ctypes
    from types import SimpleNamespace
    from aipha.research.migration import _rename_linux_exclusive

    calls = []
    class Kernel:
        def __call__(self, *args):
            calls.append(args)
            ctypes.set_errno(code)
            return -1
    kernel = Kernel()
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: SimpleNamespace(renameat2=kernel))
    monkeypatch.setattr(Path, "rename", lambda *a, **k: pytest.fail("unsafe rename fallback"))
    staged, destination = tmp_path / "staged", tmp_path / "destination"
    staged.mkdir()
    with pytest.raises(OSError) as exc:
        _rename_linux_exclusive(staged, destination)
    assert exc.value.errno == code
    assert calls == [(-100, os.fsencode(staged), -100, os.fsencode(destination), 1)]
    assert staged.is_dir() and not destination.exists()


def test_missing_linux_renameat2_fails_closed(tmp_path, monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from aipha.research.migration import _rename_linux_exclusive

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: SimpleNamespace())
    with pytest.raises(OSError) as exc:
        _rename_linux_exclusive(tmp_path / "staged", tmp_path / "destination")
    assert exc.value.errno == errno.ENOTSUP


def test_model_migration_rejects_dangling_destination_without_following_it(fitted_model, tmp_path):
    destination, outside = tmp_path / "destination", tmp_path / "missing-target"
    try:
        destination.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic-link creation unavailable on this host")
    with pytest.raises(FileExistsError):
        migrate_legacy_model(
            fitted_model, destination, feature_version="cn_equity_v1", feature_columns=["momentum_20", "momentum_5"],
            model_type="elasticnet", data_snapshot_id="attested-snapshot", acknowledge_legacy=True,
            universe="CSI500", min_listing_days=252, feature_groups=["momentum"],
            model_id="attested-model", model_config_hash="attested-config",
        )
    assert destination.is_symlink() and not outside.exists()


def _model_cli_args(source, destination):
    return [str(source), str(destination), "--feature-version", "cn_equity_v1",
            "--feature-columns", "momentum_20", "momentum_5", "--model-type", "elasticnet",
            "--data-snapshot-id", "attested-snapshot", "--universe", "CSI500",
            "--min-listing-days", "252", "--feature-groups", "momentum",
            "--model-id", "attested-model", "--model-config-hash", "attested-config", "--json"]


def test_model_cli_from_other_cwd_migrates_real_selector_and_preserves_predictions(fitted_model, tmp_path):
    (fitted_model / "model_manifest.json").unlink()
    before = {file.name: file.read_bytes() for file in fitted_model.iterdir()}
    output = tmp_path / "migrated"
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / "scripts/migrate_legacy_model.py"),
               *_model_cli_args(fitted_model, output), "--acknowledge-legacy"]
    result = subprocess.run(command, cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "destination": str(output)}
    assert before == {file.name: file.read_bytes() for file in fitted_model.iterdir()}
    from aipha.models.ml_stock_selector import MLStockSelector

    original = MLStockSelector()
    original.load(fitted_model)
    migrated = load_verified_selector(output, expected_feature_version="cn_equity_v1")
    cross = pd.DataFrame({"momentum_20": [.1, .2], "momentum_5": [.3, .4]})
    np.testing.assert_allclose(original.predict(cross), migrated.predict(cross))
    repeat = subprocess.run(command, cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""},
                            capture_output=True, text=True, timeout=30)
    assert repeat.returncode == 2 and json.loads(repeat.stdout)["error"]["type"] == "FileExistsError"


@pytest.mark.parametrize("bad_json", [False, True])
def test_model_cli_errors_are_structured_and_do_not_publish(fitted_model, tmp_path, capsys, bad_json):
    from scripts.migrate_legacy_model import main

    output = tmp_path / "not-published"
    args = _model_cli_args(fitted_model, output)
    if bad_json:
        args.extend(["--column-groups-json", "{", "--acknowledge-legacy"])
    assert main(args) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["ok"]
    assert report["error"]["type"] == ("JSONDecodeError" if bad_json else "ValueError")
    assert not output.exists()
