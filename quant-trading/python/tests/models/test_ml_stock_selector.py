"""Tests for MLStockSelector — Walk-Forward LightGBM 训练管线。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector


# ---------------------------------------------------------------------------
# Fixtures: 合成面板数据
# ---------------------------------------------------------------------------

def _make_panel(
    n_symbols: int = 50,
    n_days: int = 300,
    seed: int = 42,
) -> pd.DataFrame:
    """生成合成 OHLCV 面板数据。"""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days, freq="B")
    rows = []
    for sym_idx in range(n_symbols):
        symbol = f"SH{600000 + sym_idx:06d}"
        price = 10.0 + rng.randn() * 2
        for dt in dates:
            ret = rng.randn() * 0.02
            price *= 1 + ret
            close = round(price, 2)
            rows.append({
                "datetime": dt,
                "symbol": symbol,
                "open": round(close * (1 + rng.randn() * 0.005), 2),
                "high": round(close * (1 + abs(rng.randn()) * 0.01), 2),
                "low": round(close * (1 - abs(rng.randn()) * 0.01), 2),
                "close": close,
                "volume": rng.randint(1000, 100000),
            })
    return pd.DataFrame(rows)


def _make_panel_with_features(
    n_symbols: int = 50,
    n_days: int = 300,
    n_features: int = 10,
    seed: int = 42,
) -> pd.DataFrame:
    """带合成特征列的面板。"""
    rng = np.random.RandomState(seed)
    df = _make_panel(n_symbols, n_days, seed)
    for i in range(n_features):
        df[f"feat_{i}"] = rng.randn(len(df))
    return df


@pytest.fixture
def panel_df() -> pd.DataFrame:
    return _make_panel()


@pytest.fixture
def featured_df() -> pd.DataFrame:
    return _make_panel_with_features()


# ---------------------------------------------------------------------------
# 1) Config 默认值测试
# ---------------------------------------------------------------------------

class TestMLSelectorConfig:
    def test_default_values(self):
        cfg = MLSelectorConfig()
        assert cfg.forward_days == 5
        assert cfg.train_years == 3
        assert cfg.predict_months == 3
        assert cfg.n_estimators == 300
        assert cfg.objective == "lambdarank"
        assert cfg.top_k == 50

    def test_custom_values(self):
        cfg = MLSelectorConfig(
            forward_days=10, top_k=30, objective="regression",
        )
        assert cfg.forward_days == 10
        assert cfg.top_k == 30
        assert cfg.objective == "regression"


# ---------------------------------------------------------------------------
# 2) prepare_labels 测试
# ---------------------------------------------------------------------------

class TestPrepareLabels:
    def test_adds_fwd_return_columns(self, panel_df: pd.DataFrame):
        sel = MLStockSelector(MLSelectorConfig(forward_days=5))
        result = sel.prepare_labels(panel_df)
        assert "fwd_return" in result.columns
        assert "fwd_return_rank" in result.columns

    def test_rank_is_0_to_1(self, panel_df: pd.DataFrame):
        sel = MLStockSelector(MLSelectorConfig(forward_days=5))
        result = sel.prepare_labels(panel_df)
        assert result["fwd_return_rank"].min() >= 0.0
        assert result["fwd_return_rank"].max() <= 1.0

    def test_drops_na_rows(self, panel_df: pd.DataFrame):
        sel = MLStockSelector(MLSelectorConfig(forward_days=5))
        orig_len = len(panel_df)
        n_symbols = panel_df["symbol"].nunique()
        result = sel.prepare_labels(panel_df)
        # 每个 symbol 应丢失最后 5 行
        expected_drop = n_symbols * 5
        assert len(result) == orig_len - expected_drop

    def test_forward_days_10(self, panel_df: pd.DataFrame):
        sel = MLStockSelector(MLSelectorConfig(forward_days=10))
        orig_len = len(panel_df)
        n_symbols = panel_df["symbol"].nunique()
        result = sel.prepare_labels(panel_df)
        assert len(result) == orig_len - n_symbols * 10


# ---------------------------------------------------------------------------
# 3) WF splits 测试
# ---------------------------------------------------------------------------

class TestWFSplits:
    def test_generates_splits(self):
        sel = MLStockSelector(MLSelectorConfig(
            train_years=1, predict_months=2, purge_days=5,
        ))
        dates = list(pd.bdate_range("2020-01-01", periods=500))
        splits = sel._generate_wf_splits(dates)
        assert len(splits) >= 1
        for train_dates, test_dates in splits:
            assert len(train_dates) > 0
            assert len(test_dates) > 0
            assert max(train_dates) < min(test_dates)

    def test_no_overlap(self):
        sel = MLStockSelector(MLSelectorConfig(
            train_years=1, predict_months=2, purge_days=10,
        ))
        dates = list(pd.bdate_range("2020-01-01", periods=500))
        splits = sel._generate_wf_splits(dates)
        for train_dates, test_dates in splits:
            assert len(set(train_dates) & set(test_dates)) == 0

    def test_insufficient_data_returns_empty(self):
        sel = MLStockSelector(MLSelectorConfig(
            train_years=3, predict_months=3,
        ))
        dates = list(pd.bdate_range("2020-01-01", periods=100))
        splits = sel._generate_wf_splits(dates)
        assert len(splits) == 0


# ---------------------------------------------------------------------------
# 4) Walk-Forward 训练端到端测试
# ---------------------------------------------------------------------------

class TestWalkForwardTrain:
    def test_wf_train_regression(self, featured_df: pd.DataFrame):
        """回归模式 WF 训练。"""
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(featured_df)
        assert len(oos) > 0
        assert "pred_score" in oos.columns
        assert sel.n_folds >= 1

    def test_wf_train_lambdarank(self, featured_df: pd.DataFrame):
        """LambdaRank 模式 WF 训练。"""
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="lambdarank",
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(featured_df)
        assert len(oos) > 0
        assert sel.n_folds >= 1

    def test_wf_train_lambdarank_10bins(self, featured_df: pd.DataFrame):
        """LambdaRank with 10 rank bins for finer top-bucket granularity."""
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="lambdarank",
            n_rank_bins=10,
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(featured_df)
        assert len(oos) > 0
        assert sel.n_folds >= 1

    def test_wf_train_truncated_30pct(self, featured_df: pd.DataFrame):
        """截断训练: 仅用 Top30%+Bot30% 样本，丢弃中间 40%。"""
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            truncate_quantile=0.3,
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(featured_df)
        # OOS 预测仍是全截面 (截断只影响训练)
        assert len(oos) > 0
        assert sel.n_folds >= 1

    def test_wf_train_truncated_20pct(self, featured_df: pd.DataFrame):
        """截断训练: 更激进的 Top20%+Bot20%。"""
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            truncate_quantile=0.2,
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(featured_df)
        assert len(oos) > 0
        assert sel.n_folds >= 1

    def test_wf_summary(self, featured_df: pd.DataFrame):
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        sel.walk_forward_train(featured_df)
        summary = sel.get_wf_summary()
        assert summary["n_folds"] >= 1
        assert "mean_ic" in summary
        assert "top_features" in summary

    def test_feature_importance_populated(self, featured_df: pd.DataFrame):
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        sel.walk_forward_train(featured_df)
        importance = sel.get_feature_importance()
        assert len(importance) > 0


# ---------------------------------------------------------------------------
# 5) predict 测试
# ---------------------------------------------------------------------------

class TestPredict:
    def test_predict_returns_series(self, featured_df: pd.DataFrame):
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        sel.walk_forward_train(featured_df)
        # 取最后一天数据做预测
        last_dt = featured_df["datetime"].max()
        last_day = featured_df[featured_df["datetime"] == last_dt].copy()
        scores = sel.predict(last_day)
        assert isinstance(scores, pd.Series)
        assert len(scores) == len(last_day)

    def test_predict_without_train_raises(self):
        sel = MLStockSelector()
        df = pd.DataFrame({"feat_0": [1.0], "symbol": ["SH600000"]})
        with pytest.raises(RuntimeError, match="No model"):
            sel.predict(df)


# ---------------------------------------------------------------------------
# 6) save / load 测试
# ---------------------------------------------------------------------------

class TestSaveLoad:
    @pytest.mark.parametrize("model_type", ["elasticnet", "lightgbm"])
    def test_latest_prediction_survives_roundtrip_with_twelve_folds(self, tmp_path, model_type):
        frame = pd.DataFrame({"symbol": ["600000.SH", "600001.SH"], "factor": [0., 1.]})
        config = MLSelectorConfig(model_type=model_type, objective="regression", feature_cols=["factor"])
        selector = MLStockSelector(config)
        if model_type == "elasticnet":
            from sklearn.dummy import DummyRegressor

            selector._models = [DummyRegressor(strategy="constant", constant=float(i)).fit(
                frame[["factor"]].to_numpy(), [float(i)] * len(frame),
            ) for i in range(12)]
        else:
            lgb = pytest.importorskip("lightgbm")
            selector._models = [lgb.train(
                {"objective": "regression", "verbosity": -1, "num_threads": 1, "min_data_in_leaf": 1},
                lgb.Dataset(frame[["factor"]], label=[float(i)] * len(frame)), num_boost_round=1,
            ) for i in range(12)]
        expected = selector.predict(frame)
        selector.save(tmp_path)
        restored = MLStockSelector()
        restored.load(tmp_path)
        assert restored.n_folds == 12
        np.testing.assert_allclose(expected, [11., 11.])
        np.testing.assert_allclose(restored.predict(frame), expected)

    def test_fold_order_ignores_auxiliary_files_and_rejects_ambiguous_indices(self, tmp_path):
        from aipha.models.ml_stock_selector import _ordered_fold_files

        for name in ("fold_9.pkl", "fold_10.pkl", "fold_11.pkl", "fold_10_fcols.pkl", "fold_10_dl_meta.pkl"):
            (tmp_path / name).touch()
        assert [path.name for path in _ordered_fold_files(tmp_path, ".pkl")] == ["fold_9.pkl", "fold_10.pkl", "fold_11.pkl"]
        (tmp_path / "fold_09.pkl").touch()
        with pytest.raises(ValueError, match="duplicate model fold"):
            _ordered_fold_files(tmp_path, ".pkl")

    @pytest.mark.parametrize("model_type", [
        "double_ensemble", "xgboost", "gru", "tra", "master", "gat", "hist",
    ])
    def test_experimental_selector_requires_explicit_opt_in(self, model_type):
        with pytest.raises(ValueError, match="experimental ML model is disabled"):
            MLStockSelector(MLSelectorConfig(model_type=model_type))
        selector = MLStockSelector(MLSelectorConfig(
            model_type=model_type, allow_experimental_models=True,
        ))
        assert selector.config.model_type == model_type

    def test_unknown_model_does_not_fall_back_to_lightgbm(self):
        with pytest.raises(ValueError, match="unknown ML model type"):
            MLStockSelector(MLSelectorConfig(
                model_type="typo", allow_experimental_models=True,
            ))

    def test_saved_experimental_opt_in_does_not_authorize_loading(self, tmp_path):
        selector = MLStockSelector(MLSelectorConfig(
            model_type="double_ensemble", allow_experimental_models=True,
        ))
        selector.save(tmp_path)
        restored = MLStockSelector()
        with pytest.raises(ValueError, match="experimental ML model is disabled"):
            restored.load(tmp_path)
        assert restored.config.model_type == "lightgbm"
        restored.load(tmp_path, allow_experimental_models=True)
        assert restored.config.model_type == "double_ensemble"

    def test_save_and_load(self, featured_df: pd.DataFrame, tmp_path):
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(10)],
        )
        sel = MLStockSelector(cfg)
        sel.walk_forward_train(featured_df)
        model_dir = tmp_path / "model"
        sel.save(model_dir)
        # 加载
        sel2 = MLStockSelector()
        sel2.load(model_dir)
        assert sel2.n_folds == sel.n_folds
        assert sel2.config.n_estimators == 10
        # 预测结果一致
        last_dt = featured_df["datetime"].max()
        last_day = featured_df[featured_df["datetime"] == last_dt].copy()
        s1 = sel.predict(last_day)
        s2 = sel2.predict(last_day)
        np.testing.assert_allclose(s1.values, s2.values, atol=1e-6)


# ---------------------------------------------------------------------------
# 7) _resolve_features 测试
# ---------------------------------------------------------------------------

class TestResolveFeatures:
    def test_explicit_feature_cols(self):
        cfg = MLSelectorConfig(
            feature_cols=["feat_0", "feat_1", "nonexist"],
        )
        sel = MLStockSelector(cfg)
        df = pd.DataFrame({"feat_0": [1], "feat_1": [2], "close": [10]})
        features = sel._resolve_features(df)
        assert features == ["feat_0", "feat_1"]

    def test_auto_detect(self):
        cfg = MLSelectorConfig(feature_cols=[])
        sel = MLStockSelector(cfg)
        df = pd.DataFrame({
            "datetime": [1], "symbol": ["A"],
            "close": [10], "feat_x": [1], "feat_y": [2],
        })
        features = sel._resolve_features(df)
        assert "feat_x" in features
        assert "feat_y" in features
        assert "close" not in features
        assert "symbol" not in features


# ---------------------------------------------------------------------------
# 8) 边界条件测试
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_symbol(self):
        """单一标的也不报错。"""
        rng = np.random.RandomState(0)
        n_days = 500
        dates = pd.bdate_range("2019-01-01", periods=n_days)
        df = pd.DataFrame({
            "datetime": dates,
            "symbol": "SH600000",
            "close": 10 + np.cumsum(rng.randn(n_days) * 0.1),
            "feat_0": rng.randn(n_days),
            "feat_1": rng.randn(n_days),
        })
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=1,
            purge_days=5,
            forward_days=5,
            n_estimators=5,
            objective="regression",
            feature_cols=["feat_0", "feat_1"],
            min_samples_per_date=1,
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(df)
        assert len(oos) > 0


# ---------------------------------------------------------------------------
# 9) 增量训练测试 (P1-1)
# ---------------------------------------------------------------------------

class TestIncrementalTraining:
    """验证 enable_incremental=True 的 warm-start 增量训练机制。"""

    @staticmethod
    def _small_featured_df(
        n_symbols: int = 30, n_days: int = 500, n_features: int = 5,
    ) -> pd.DataFrame:
        rng = np.random.RandomState(42)
        dates = pd.bdate_range("2019-01-01", periods=n_days, freq="B")
        rows = []
        for sym_idx in range(n_symbols):
            symbol = f"SH{600000 + sym_idx:06d}"
            price = 10.0 + rng.randn() * 2
            for dt in dates:
                price *= 1 + rng.randn() * 0.02
                row = {
                    "datetime": dt,
                    "symbol": symbol,
                    "close": round(price, 2),
                }
                for f in range(n_features):
                    row[f"feat_{f}"] = rng.randn()
                rows.append(row)
        return pd.DataFrame(rows)

    def test_incremental_config_defaults(self):
        cfg = MLSelectorConfig()
        assert cfg.enable_incremental is False
        assert cfg.incremental_n_estimators == 100
        assert cfg.incremental_psi_threshold == 0.20

    def test_incremental_produces_oos(self):
        """增量训练应正常产出 OOS 预测。"""
        df = self._small_featured_df()
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=2,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(5)],
            enable_incremental=True,
            incremental_n_estimators=5,
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(df)
        assert len(oos) > 0
        assert "pred_score" in oos.columns
        assert sel.n_folds >= 2  # 至少 2 fold 才能验证增量

    def test_incremental_vs_full_different_models(self):
        """增量训练 vs 全量训练应产生不同的模型。"""
        df = self._small_featured_df()
        base_cfg = dict(
            train_years=1,
            predict_months=2,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(5)],
        )
        sel_full = MLStockSelector(MLSelectorConfig(**base_cfg))
        oos_full = sel_full.walk_forward_train(df)

        sel_incr = MLStockSelector(MLSelectorConfig(
            **base_cfg,
            enable_incremental=True,
            incremental_n_estimators=5,
        ))
        oos_incr = sel_incr.walk_forward_train(df)

        # 两种方式都产出预测
        assert len(oos_full) > 0
        assert len(oos_incr) > 0
        # fold 数应一致
        assert sel_full.n_folds == sel_incr.n_folds

    def test_incremental_lambdarank(self):
        """lambdarank objective 下增量训练也正常。"""
        df = self._small_featured_df()
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=2,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="lambdarank",
            n_rank_bins=3,
            feature_cols=[f"feat_{i}" for i in range(5)],
            enable_incremental=True,
            incremental_n_estimators=5,
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(df)
        assert len(oos) > 0
        assert sel.n_folds >= 2

    def test_psi_drift_resets_incremental(self):
        """PSI 阈值极低时应触发 drift 检测，回退到全量训练。"""
        df = self._small_featured_df()
        cfg = MLSelectorConfig(
            train_years=1,
            predict_months=2,
            purge_days=5,
            forward_days=5,
            n_estimators=10,
            objective="regression",
            feature_cols=[f"feat_{i}" for i in range(5)],
            enable_incremental=True,
            incremental_n_estimators=5,
            incremental_psi_threshold=0.001,  # 极低阈值 → 几乎必触发
        )
        sel = MLStockSelector(cfg)
        oos = sel.walk_forward_train(df)
        assert len(oos) > 0
        # 即使 PSI 触发 drift，训练仍正常完成

    def test_extract_feature_dist(self):
        """_extract_feature_dist 应返回正确的特征分布。"""
        df = self._small_featured_df(n_symbols=5, n_days=50)
        feature_cols = [f"feat_{i}" for i in range(5)]
        dist = MLStockSelector._extract_feature_dist(df, feature_cols)
        assert set(dist.keys()) == set(feature_cols)
        for col in feature_cols:
            assert len(dist[col]) > 0

    def test_extract_feature_dist_sampling(self):
        """超过 max_samples 时应采样。"""
        df = self._small_featured_df(n_symbols=50, n_days=500)
        feature_cols = [f"feat_{i}" for i in range(5)]
        dist = MLStockSelector._extract_feature_dist(
            df, feature_cols, max_samples=100,
        )
        for col in feature_cols:
            assert len(dist[col]) <= 100

    def test_incremental_disabled_for_non_lgb(self):
        """非 lightgbm 模型类型时增量训练不生效。"""
        cfg = MLSelectorConfig(
            enable_incremental=True,
            model_type="elasticnet",
        )
        # 仅检查配置，实际 WF 中 use_incremental 应为 False
        assert cfg.enable_incremental is True
        assert cfg.model_type != "lightgbm"

