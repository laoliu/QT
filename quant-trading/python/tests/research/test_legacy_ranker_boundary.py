"""Standalone rankers require explicit replay and retain old artifact names."""
from dataclasses import asdict
import importlib
import json
from pathlib import Path
import pickle
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from aipha.models.lgbm_ranker import (
    EquityLGBMRanker, EquityRankerConfig, EquityRankerWFValidator, make_equity_ranker,
)


@pytest.mark.parametrize("factory", [EquityLGBMRanker, EquityRankerWFValidator, make_equity_ranker])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_standalone_entry_requires_literal_current_call_authorization(factory, value):
    with pytest.raises(ValueError, match="disabled by default"):
        factory(legacy_compat=value)


def test_missing_authorization_rejects_defaults_and_native_load_before_io(tmp_path, monkeypatch):
    for factory in (EquityLGBMRanker, EquityRankerWFValidator, make_equity_ranker):
        with pytest.raises(ValueError, match="ResearchPipeline"):
            factory()
    ranker = EquityLGBMRanker(legacy_compat=True)
    import lightgbm

    monkeypatch.setattr(lightgbm, "Booster", lambda *a, **k: pytest.fail("unauthorized model read"))
    with pytest.raises(ValueError, match="legacy_compat=True"):
        ranker.load(tmp_path / "missing.txt")
    assert not ranker.is_fitted and not list(tmp_path.iterdir())
    assert "legacy_compat" not in asdict(ranker.config)


def test_explicit_old_package_imports_warn_and_resolve_to_same_implementation():
    import aipha.models

    module = importlib.import_module("aipha.models.lgbm_ranker")
    assert module is importlib.import_module("aipha.experiments.models.legacy_lgbm_ranker")
    for name in ("EquityRankerConfig", "EquityLGBMRanker", "EquityRankerWFValidator"):
        assert name not in aipha.models.__all__
        with pytest.warns(DeprecationWarning, match="legacy standalone API"):
            assert getattr(aipha.models, name) is getattr(module, name)


def test_historical_native_model_and_pickle_qualifiers_survive_cold_replay(tmp_path, monkeypatch):
    from aipha.research.artifacts import read_verified_model_manifest

    rng = np.random.default_rng(42)
    labels = np.tile(np.repeat(np.arange(4), 8), 4)
    frame = pd.DataFrame({"trade_date": np.repeat(pd.date_range("2020-01-01", periods=4), 32),
                          "quality": labels + rng.normal(0, .05, len(labels)),
                          "momentum": rng.normal(size=len(labels)), "fwd_return_rank": labels})
    config = EquityRankerConfig(feature_cols=["quality", "momentum"], n_estimators=8,
                                min_child_samples=1, num_leaves=7, colsample_bytree=1.)
    ranker = EquityLGBMRanker(config, legacy_compat=True).fit(frame)
    query = frame[["quality", "momentum"]].iloc[:32]
    expected = ranker.predict(query)
    assert np.std(expected) > 0
    native = tmp_path / "historical_model.txt"
    ranker.save(native)
    query.to_json(tmp_path / "query.json", orient="split")
    # These are the actual pre-move qualified names, not new-name pickles.
    with monkeypatch.context() as patch:
        patch.setattr(EquityRankerConfig, "__module__", "aipha.models.lgbm_ranker")
        patch.setattr(EquityLGBMRanker, "__module__", "aipha.models.lgbm_ranker")
        (tmp_path / "config.pkl").write_bytes(pickle.dumps(config))
        (tmp_path / "ranker.pkl").write_bytes(pickle.dumps(ranker))
    assert b"aipha.models.lgbm_ranker" in (tmp_path / "config.pkl").read_bytes()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="model manifest missing"):
        read_verified_model_manifest(tmp_path, expected_feature_version="cn_equity_v1")
    script = '''
import json, pickle, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pandas as pd
from aipha.models.lgbm_ranker import EquityLGBMRanker
root = Path(sys.argv[2])
config = pickle.loads((root / "config.pkl").read_bytes())
query = pd.read_json(root / "query.json", orient="split")
try:
    EquityLGBMRanker(config)
except ValueError:
    pass
else:
    raise AssertionError("stored configuration authorized legacy replay")
ranker = EquityLGBMRanker(config, legacy_compat=True)
try:
    ranker.load(root / "historical_model.txt")
except ValueError:
    pass
else:
    raise AssertionError("constructor authorization silently authorized model loading")
ranker.load(root / "historical_model.txt", legacy_compat=True)
# Trusted test fixtures also exercise Python's historical class-name resolver.
old_object = pickle.loads((root / "ranker.pkl").read_bytes())
print(json.dumps({"native": ranker.predict(query).tolist(), "pickle": old_object.predict(query).tolist()}))
'''
    completed = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve().parents[2]),
                                str(tmp_path)], text=True, capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    actual = json.loads(completed.stdout.strip().splitlines()[-1])
    np.testing.assert_allclose(actual["native"], expected)
    np.testing.assert_allclose(actual["pickle"], expected)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
