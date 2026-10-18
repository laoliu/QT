"""Default imports stay small while historical pickle names remain readable."""
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


MODULES = [
    ("double_ensemble", "DEnsembleConfig"),
    ("ensemble", "EnsembleConfig"),
    ("gru_ranker", "GRUConfig"),
    ("gat_ranker", "GATRankerConfig"),
    ("tra_model", "TRAConfig"),
    ("master_model", "MASTERConfig"),
    ("hist_model", "HISTConfig"),
]


def test_default_research_registry_and_model_listing_do_not_load_experiments(tmp_path):
    script = tmp_path / "check_imports.py"
    script.write_text('''
import importlib.abc
import sys

blocked = ("torch", "torch_geometric", "xgboost", "aipha.experiments.models.", "aipha.models.lgbm_ranker")
class NoExperimentalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") or
               (name.endswith(".") and fullname.startswith(name)) for name in blocked):
            raise RuntimeError("default path imported " + fullname)
sys.meta_path.insert(0, NoExperimentalImports())

from aipha.research import ResearchConfig, ResearchPipeline
from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector
from aipha.strategy.register_all import _reg
from aipha.engines.aipha_ml_engine import run
import aipha.experiments.models
import aipha.models
assert "EquityLGBMRanker" not in aipha.models.__all__
assert "EquityLGBMRanker" not in vars(aipha.models)
defaults = {}
exec("from aipha.models import *", defaults)
assert "EquityLGBMRanker" not in defaults
assert run({"action": "list_models"})["data"]["models"] == ["lgbm", "linear"]
assert ResearchPipeline(ResearchConfig()).config.model_type == "lightgbm"
assert "ml_factor" not in _reg.list_discoverable_strategies()
for model in ["double_ensemble", "xgboost", "gru", "gat", "tra", "master", "hist"]:
    try:
        MLStockSelector(MLSelectorConfig(model_type=model))
    except ValueError as exc:
        assert "experimental ML model is disabled" in str(exc)
    else:
        raise AssertionError("experimental model accepted: " + model)
assert not any(name.startswith("aipha.experiments.models.") for name in sys.modules)
print("canonical imports isolated")
''', encoding="utf-8")
    # The subprocess starts with an empty module cache, unlike the pytest
    # process, which may already have imported optional model test modules.
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, "-c",
                               "import runpy,sys; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[2])",
                               str(root), str(script)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "canonical imports isolated" in completed.stdout


@pytest.mark.parametrize("module_name,config_name", MODULES)
def test_legacy_module_alias_and_pickled_configuration_remain_compatible(module_name, config_name, monkeypatch):
    if module_name in {"gru_ranker", "tra_model", "master_model", "hist_model"}:
        pytest.importorskip("torch")
    old_name = "aipha.models." + module_name
    new_name = "aipha.experiments.models." + module_name
    legacy = importlib.import_module(old_name)
    implementation = importlib.import_module(new_name)
    assert legacy is implementation
    cls = getattr(legacy, config_name)
    assert cls.__module__ == new_name
    original = cls()
    # Serialize with the exact historical module qualifier, then restore the
    # class to its new location before unpickling the old record.
    with monkeypatch.context() as context:
        context.setattr(cls, "__module__", old_name)
        saved = pickle.dumps(original, protocol=4)
    assert old_name.encode() in saved
    restored = pickle.loads(saved)
    assert type(restored) is cls and asdict(restored) == asdict(original)
    assert new_name.encode() in pickle.dumps(restored, protocol=4)


def test_legacy_fitted_double_ensemble_load_preserves_predictions_and_requires_opt_in(tmp_path, monkeypatch):
    pytest.importorskip("lightgbm")
    from aipha.experiments.models.double_ensemble import DEnsembleConfig, DoubleEnsembleModel
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

    # Resolve the legacy qualifier just as an old joblib record would.
    importlib.import_module("aipha.models.double_ensemble")
    rng = np.random.default_rng(42)
    values = rng.normal(size=(100, 4))
    columns = [f"f{i}" for i in range(4)]
    model = DoubleEnsembleModel(DEnsembleConfig(num_models=2, epochs=3, early_stopping_rounds=None,
                                              enable_sr=False, enable_fs=False))
    model.fit(values, values[:, 0] - values[:, 1], feature_names=columns)
    selector = MLStockSelector(MLSelectorConfig(model_type="double_ensemble", feature_cols=columns,
                                               allow_experimental_models=True))
    selector._models = [model]
    frame = pd.DataFrame(values[:10], columns=columns)
    expected = selector.predict(frame).to_numpy()
    with monkeypatch.context() as context:
        context.setattr(DEnsembleConfig, "__module__", "aipha.models.double_ensemble")
        context.setattr(DoubleEnsembleModel, "__module__", "aipha.models.double_ensemble")
        selector.save(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert b"aipha.models.double_ensemble" in before["fold_0.pkl"]
    restored = MLStockSelector()
    with pytest.raises(ValueError, match="experimental ML model is disabled"):
        restored.load(tmp_path)
    restored.load(tmp_path, allow_experimental_models=True)
    np.testing.assert_allclose(restored.predict(frame).to_numpy(), expected)
    cold_load = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pandas as pd
from aipha.models.ml_stock_selector import MLStockSelector
selector = MLStockSelector()
selector.load(Path(sys.argv[2]), allow_experimental_models=True)
print("PREDICTIONS=" + json.dumps(selector.predict(pd.DataFrame(json.loads(sys.argv[3]))).tolist()))
'''
    completed = subprocess.run([sys.executable, "-c", cold_load, str(Path(__file__).resolve().parents[2]),
                                str(tmp_path), json.dumps(frame.to_dict("list"))],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result, = [line.removeprefix("PREDICTIONS=") for line in completed.stdout.splitlines()
               if line.startswith("PREDICTIONS=")]
    np.testing.assert_allclose(json.loads(result), expected)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
