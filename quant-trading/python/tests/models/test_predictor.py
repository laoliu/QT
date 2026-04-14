"""Tests for aipha.models.predictor module.

覆盖要求 (§13.2): ≥15 tests.
目标: PredictionResult, PredictorConfig, Predictor,
      EnsemblePredictor, OnlinePredictor, CrossSectionalPredictor.
"""

from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aipha.models.predictor import (
    CrossSectionalPredictor,
    EnsemblePredictor,
    OnlinePredictor,
    Predictor,
    PredictorConfig,
    PredictionResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_sklearn_model(path: Path, n_features: int = 3) -> list[str]:
    """Train a tiny sklearn model and save as pickle for Predictor tests.

    Returns feature_names used.
    """
    from sklearn.linear_model import Ridge

    feature_names = [f"f{i}" for i in range(n_features)]
    np.random.seed(42)
    X = np.random.randn(50, n_features)
    y = X @ np.arange(1, n_features + 1, dtype=float) + np.random.randn(50) * 0.1
    model = Ridge().fit(X, y)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"model": model, "feature_names": feature_names}, f)
    return feature_names


@pytest.fixture()
def model_path(tmp_path: Path) -> Path:
    p = tmp_path / "test_model.pkl"
    _save_sklearn_model(p, n_features=3)
    return p


@pytest.fixture()
def sample_X() -> pd.DataFrame:
    np.random.seed(0)
    return pd.DataFrame({
        "f0": np.random.randn(20),
        "f1": np.random.randn(20),
        "f2": np.random.randn(20),
    })


@pytest.fixture()
def loaded_predictor(model_path: Path) -> Predictor:
    """A Predictor that has already been loaded."""
    p = Predictor()
    assert p.load(model_path)
    return p


# ============================= PredictionResult ============================

class TestPredictionResult:
    """PredictionResult dataclass tests."""

    def test_to_dataframe(self) -> None:
        result = PredictionResult(
            predictions=np.array([0.1, 0.2, 0.3]),
            symbols=["A", "B", "C"],
            timestamp=datetime(2024, 1, 1),
            model_id="m1",
        )
        df = result.to_dataframe()
        assert list(df.columns) == ["symbol", "prediction", "timestamp"]
        assert len(df) == 3
        assert df["symbol"].tolist() == ["A", "B", "C"]

    def test_to_dataframe_with_confidence(self) -> None:
        result = PredictionResult(
            predictions=np.array([0.5, 0.3]),
            symbols=["X", "Y"],
            timestamp=datetime.now(),
            model_id="m2",
            confidence=np.array([0.9, 0.8]),
        )
        df = result.to_dataframe()
        assert "confidence" in df.columns

    def test_get_top_n(self) -> None:
        result = PredictionResult(
            predictions=np.array([0.1, 0.5, 0.3, 0.9, 0.2]),
            symbols=["A", "B", "C", "D", "E"],
            timestamp=datetime.now(),
            model_id="m3",
        )
        top2 = result.get_top_n(n=2)
        assert len(top2) == 2
        assert top2.iloc[0]["symbol"] == "D"  # highest

    def test_get_top_n_ascending(self) -> None:
        result = PredictionResult(
            predictions=np.array([0.1, 0.5, 0.3]),
            symbols=["A", "B", "C"],
            timestamp=datetime.now(),
            model_id="m4",
        )
        bottom1 = result.get_top_n(n=1, ascending=True)
        assert bottom1.iloc[0]["symbol"] == "A"  # lowest


# ============================= PredictorConfig =============================

class TestPredictorConfig:
    """PredictorConfig defaults and custom."""

    def test_defaults(self) -> None:
        cfg = PredictorConfig()
        assert cfg.batch_size == 10000
        assert cfg.output_type == "raw"
        assert cfg.enable_cache is False

    def test_custom(self) -> None:
        cfg = PredictorConfig(
            batch_size=500,
            output_type="rank",
            enable_cache=True,
        )
        assert cfg.batch_size == 500
        assert cfg.output_type == "rank"


# ================================ Predictor ================================

class TestPredictor:
    """Predictor: load, predict, batch, postprocess."""

    def test_load_success(self, model_path: Path) -> None:
        p = Predictor()
        assert p.load(model_path)
        assert p.is_loaded()
        assert p.get_model_id() == "test_model"

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        p = Predictor()
        assert not p.load(tmp_path / "nonexistent.pkl")
        assert not p.is_loaded()

    def test_predict_not_loaded(self, sample_X: pd.DataFrame) -> None:
        p = Predictor()
        with pytest.raises(RuntimeError, match="not loaded"):
            p.predict(sample_X)

    def test_predict_basic(
        self,
        loaded_predictor: Predictor,
        sample_X: pd.DataFrame,
    ) -> None:
        result = loaded_predictor.predict(sample_X, symbols=[f"S{i}" for i in range(20)])
        assert len(result.predictions) == 20
        assert len(result.symbols) == 20

    def test_predict_raw_output(
        self,
        loaded_predictor: Predictor,
        sample_X: pd.DataFrame,
    ) -> None:
        result = loaded_predictor.predict(sample_X)
        assert result.predictions.dtype == np.float64

    def test_predict_rank_output(self, model_path: Path, sample_X: pd.DataFrame) -> None:
        cfg = PredictorConfig(output_type="rank")
        p = Predictor(config=cfg)
        p.load(model_path)
        result = p.predict(sample_X)
        # Rank percentiles should be in (0, 1]
        assert result.predictions.min() > 0
        assert result.predictions.max() <= 1.0

    def test_predict_zscore_output(self, model_path: Path, sample_X: pd.DataFrame) -> None:
        cfg = PredictorConfig(output_type="zscore")
        p = Predictor(config=cfg)
        p.load(model_path)
        result = p.predict(sample_X)
        # Z-score mean ≈ 0
        assert abs(result.predictions.mean()) < 0.1

    def test_predict_batch_split(self, model_path: Path) -> None:
        """Test batch splitting with small batch_size."""
        cfg = PredictorConfig(batch_size=5)
        p = Predictor(config=cfg)
        p.load(model_path)

        np.random.seed(0)
        X = pd.DataFrame({
            "f0": np.random.randn(12),
            "f1": np.random.randn(12),
            "f2": np.random.randn(12),
        })
        result = p.predict(X)
        assert len(result.predictions) == 12

    def test_missing_features_filled(self, model_path: Path) -> None:
        """Missing features auto-filled with 0."""
        p = Predictor()
        p.load(model_path)
        X = pd.DataFrame({"f0": [1.0, 2.0]})  # missing f1, f2
        result = p.predict(X)
        assert len(result.predictions) == 2

    def test_get_feature_names(self, loaded_predictor: Predictor) -> None:
        names = loaded_predictor.get_feature_names()
        assert names == ["f0", "f1", "f2"]
        # Returned copy — mutation should not affect predictor
        names.append("extra")
        assert loaded_predictor.get_feature_names() == ["f0", "f1", "f2"]


# ============================ EnsemblePredictor ============================

class TestEnsemblePredictor:
    """EnsemblePredictor: add, remove, weighted/average/median."""

    def _make_loaded_predictor(self, tmp_path: Path, name: str, n_features: int = 3) -> Predictor:
        model_path = tmp_path / f"{name}.pkl"
        _save_sklearn_model(model_path, n_features=n_features)
        p = Predictor()
        p.load(model_path)
        return p

    def test_no_predictors_raises(self) -> None:
        ep = EnsemblePredictor()
        with pytest.raises(RuntimeError, match="No predictors"):
            ep.predict(pd.DataFrame({"f0": [1.0]}))

    def test_weighted_predict(self, tmp_path: Path, sample_X: pd.DataFrame) -> None:
        ep = EnsemblePredictor()
        p1 = self._make_loaded_predictor(tmp_path, "m1")
        p2 = self._make_loaded_predictor(tmp_path, "m2")
        ep.add_predictor("m1", p1, weight=2.0)
        ep.add_predictor("m2", p2, weight=1.0)
        result = ep.predict(sample_X, method="weighted")
        assert len(result.predictions) == 20
        assert result.model_id == "ensemble"

    def test_average_predict(self, tmp_path: Path, sample_X: pd.DataFrame) -> None:
        ep = EnsemblePredictor()
        p1 = self._make_loaded_predictor(tmp_path, "m1")
        ep.add_predictor("m1", p1)
        result = ep.predict(sample_X, method="average")
        assert len(result.predictions) == 20

    def test_median_predict(self, tmp_path: Path, sample_X: pd.DataFrame) -> None:
        ep = EnsemblePredictor()
        p1 = self._make_loaded_predictor(tmp_path, "m1")
        p2 = self._make_loaded_predictor(tmp_path, "m2")
        ep.add_predictor("m1", p1)
        ep.add_predictor("m2", p2)
        result = ep.predict(sample_X, method="median")
        assert len(result.predictions) == 20

    def test_remove_predictor(self, tmp_path: Path) -> None:
        ep = EnsemblePredictor()
        p1 = self._make_loaded_predictor(tmp_path, "m1")
        ep.add_predictor("m1", p1)
        assert ep.remove_predictor("m1")
        assert not ep.remove_predictor("no_exist")

    def test_set_weights(self, tmp_path: Path) -> None:
        ep = EnsemblePredictor()
        p1 = self._make_loaded_predictor(tmp_path, "m1")
        ep.add_predictor("m1", p1, weight=1.0)
        ep.set_weights({"m1": 5.0})
        assert ep._weights["m1"] == 5.0

    def test_get_predictor_names(self, tmp_path: Path) -> None:
        ep = EnsemblePredictor()
        p1 = self._make_loaded_predictor(tmp_path, "m1")
        p2 = self._make_loaded_predictor(tmp_path, "m2")
        ep.add_predictor("alpha", p1)
        ep.add_predictor("beta", p2)
        names = ep.get_predictor_names()
        assert set(names) == {"alpha", "beta"}


# ============================= OnlinePredictor =============================

class TestOnlinePredictor:
    """OnlinePredictor: update, predict, cache, clear."""

    def test_update_and_get(self, loaded_predictor: Predictor) -> None:
        op = OnlinePredictor(loaded_predictor)
        op.update_features("S1", {"f0": 1.0, "f1": 2.0, "f2": 3.0})
        pred = op.get_prediction("S1")
        assert pred is not None
        assert isinstance(pred, float)

    def test_no_features_returns_cached_or_none(self, loaded_predictor: Predictor) -> None:
        op = OnlinePredictor(loaded_predictor)
        # No features set for S1 => should return None (no cache)
        assert op.get_prediction("S1") is None

    def test_batch_predict(self, loaded_predictor: Predictor) -> None:
        op = OnlinePredictor(loaded_predictor)
        op.update_features("A", {"f0": 0.5, "f1": 0.3, "f2": 0.1})
        op.update_features("B", {"f0": -0.5, "f1": 0.8, "f2": -0.2})
        preds = op.batch_predict(["A", "B", "C"])
        assert "A" in preds
        assert "B" in preds
        assert "C" in preds  # C not updated → uses default 0.0

    def test_clear_cache_specific(self, loaded_predictor: Predictor) -> None:
        op = OnlinePredictor(loaded_predictor)
        op.update_features("A", {"f0": 1.0, "f1": 1.0, "f2": 1.0})
        op.get_prediction("A")
        op.clear_cache("A")
        assert op.get_prediction("A") is None

    def test_clear_cache_all(self, loaded_predictor: Predictor) -> None:
        op = OnlinePredictor(loaded_predictor)
        op.update_features("A", {"f0": 1.0, "f1": 1.0, "f2": 1.0})
        op.get_prediction("A")
        op.clear_cache()
        assert op.get_prediction("A") is None


# ========================= CrossSectionalPredictor =========================

class TestCrossSectionalPredictor:
    """CrossSectionalPredictor: rank and long/short portfolio."""

    def test_predict_and_rank(self, loaded_predictor: Predictor) -> None:
        np.random.seed(42)
        n = 30
        X = pd.DataFrame({
            "f0": np.random.randn(n),
            "f1": np.random.randn(n),
            "f2": np.random.randn(n),
        })
        symbols = [f"S{i:03d}" for i in range(n)]
        csp = CrossSectionalPredictor(loaded_predictor)
        ranked = csp.predict_and_rank(X, symbols)
        assert "rank" in ranked.columns
        assert "percentile" in ranked.columns
        assert "quantile" in ranked.columns
        assert len(ranked) == n
        # Sorted by rank ascending
        assert ranked["rank"].iloc[0] == 1

    def test_get_long_short_portfolio(self, loaded_predictor: Predictor) -> None:
        np.random.seed(42)
        n = 50
        X = pd.DataFrame({
            "f0": np.random.randn(n),
            "f1": np.random.randn(n),
            "f2": np.random.randn(n),
        })
        symbols = [f"S{i:03d}" for i in range(n)]
        csp = CrossSectionalPredictor(loaded_predictor)
        long_syms, short_syms = csp.get_long_short_portfolio(
            X, symbols, top_n=5, bottom_n=5,
        )
        assert len(long_syms) == 5
        assert len(short_syms) == 5
        # No overlap
        assert len(set(long_syms) & set(short_syms)) == 0


# ======================== Model Hash Verification (C-3) ========================

class TestModelHashVerification:
    """Predictor 模型哈希校验功能测试（C-3 安全序列化改进）。"""

    def test_save_model_hash(self, model_path: Path) -> None:
        """save_model_hash 生成 .sha256 伴随文件。"""
        file_hash = Predictor.save_model_hash(model_path)
        hash_file = model_path.with_suffix(".pkl.sha256")
        assert hash_file.exists()
        assert hash_file.read_text(encoding="utf-8") == file_hash
        assert len(file_hash) == 64  # SHA-256 hex digest length

    def test_load_with_valid_hash(self, model_path: Path) -> None:
        """当 .sha256 存在且匹配时，加载正常。"""
        Predictor.save_model_hash(model_path)
        p = Predictor()
        assert p.load(model_path, verify_hash=True)
        assert p.is_loaded()

    def test_load_with_invalid_hash(self, model_path: Path) -> None:
        """当 .sha256 存在但不匹配时，记录 WARNING 但仍加载成功。"""
        hash_file = model_path.with_suffix(".pkl.sha256")
        hash_file.write_text("0000" * 16, encoding="utf-8")
        p = Predictor()
        # 加载仍然成功（WARNING 级别，不阻断）
        assert p.load(model_path, verify_hash=True)
        assert p.is_loaded()

    def test_load_without_hash_file(self, model_path: Path) -> None:
        """当 .sha256 不存在时，跳过校验正常加载。"""
        hash_file = model_path.with_suffix(".pkl.sha256")
        if hash_file.exists():
            hash_file.unlink()
        p = Predictor()
        assert p.load(model_path, verify_hash=True)
        assert p.is_loaded()

    def test_load_skip_verify(self, model_path: Path) -> None:
        """verify_hash=False 时完全跳过校验。"""
        p = Predictor()
        assert p.load(model_path, verify_hash=False)
        assert p.is_loaded()

    def test_verify_model_hash_returns_false_on_mismatch(self, model_path: Path) -> None:
        """_verify_model_hash 在哈希不匹配时返回 False。"""
        hash_file = model_path.with_suffix(".pkl.sha256")
        hash_file.write_text("bad_hash", encoding="utf-8")
        assert Predictor._verify_model_hash(model_path) is False

    def test_verify_model_hash_returns_true_no_file(self, model_path: Path) -> None:
        """_verify_model_hash 在无哈希文件时返回 True。"""
        hash_file = model_path.with_suffix(".pkl.sha256")
        if hash_file.exists():
            hash_file.unlink()
        assert Predictor._verify_model_hash(model_path) is True
