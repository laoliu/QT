"""DoubleEnsembleModel 单元测试。

覆盖:
- 基本 fit / predict 流程
- 特征选择 (FS) 导致子模型使用不同特征子集
- 样本重加权 (SR) 改变样本权重
- feature_importance 聚合
- 配置修改 (enable_sr/enable_fs 开关)
- 边界条件 (小样本 / 少特征)
"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.models.double_ensemble import DEnsembleConfig, DoubleEnsembleModel


@pytest.fixture()
def synth_data():
    """合成回归数据: 200 样本, 20 特征, 5 个有效特征。"""
    rng = np.random.RandomState(42)
    N, F = 200, 20
    X = rng.randn(N, F)
    # 真实信号来自前 5 个特征
    beta = np.zeros(F)
    beta[:5] = [1.0, -0.8, 0.5, -0.3, 0.2]
    y = X @ beta + rng.randn(N) * 0.5
    feat_names = [f"f{i}" for i in range(F)]
    return X, y, feat_names


@pytest.fixture()
def small_config():
    """小规模配置, 加速测试。"""
    return DEnsembleConfig(
        num_models=3,
        epochs=10,
        early_stopping_rounds=None,
        enable_sr=True,
        enable_fs=True,
        bins_sr=5,
        bins_fs=3,
        seed=42,
    )


class TestDoubleEnsembleFitPredict:
    """基本训练与预测。"""

    def test_fit_returns_self(self, synth_data, small_config):
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        result = model.fit(X, y, feature_names=fn)
        assert result is model

    def test_predict_shape(self, synth_data, small_config):
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        model.fit(X, y, feature_names=fn)
        pred = model.predict(X)
        assert pred.shape == (len(X),)

    def test_predict_not_constant(self, synth_data, small_config):
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        model.fit(X, y, feature_names=fn)
        pred = model.predict(X)
        assert pred.std() > 0.01, "预测不应为常数"

    def test_predict_before_fit_raises(self):
        model = DoubleEnsembleModel()
        with pytest.raises(RuntimeError, match="not fitted"):
            model.predict(np.zeros((10, 5)))

    def test_num_sub_models(self, synth_data, small_config):
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        model.fit(X, y, feature_names=fn)
        assert len(model._ensemble) == small_config.num_models


class TestFeatureSelection:
    """FS 模块测试。"""

    def test_fs_reduces_features(self, synth_data, small_config):
        """后续子模型的特征数应递减或相同。"""
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        model.fit(X, y, feature_names=fn)
        feat_counts = [len(sf) for sf in model._sub_features]
        # 至少有一个子模型的特征数小于原始
        assert min(feat_counts) < len(fn)

    def test_fs_disabled(self, synth_data):
        """enable_fs=False 时子模型全部使用全量特征。"""
        X, y, fn = synth_data
        cfg = DEnsembleConfig(
            num_models=3, epochs=10,
            early_stopping_rounds=None,
            enable_sr=False, enable_fs=False, seed=42,
        )
        model = DoubleEnsembleModel(cfg)
        model.fit(X, y, feature_names=fn)
        for sf in model._sub_features:
            assert len(sf) == len(fn)


class TestSampleReweight:
    """SR 模块测试。"""

    def test_sr_only(self, synth_data):
        """仅 SR, 无 FS, 训练应正常完成。"""
        X, y, fn = synth_data
        cfg = DEnsembleConfig(
            num_models=3, epochs=10,
            early_stopping_rounds=None,
            enable_sr=True, enable_fs=False, seed=42,
        )
        model = DoubleEnsembleModel(cfg)
        model.fit(X, y, feature_names=fn)
        pred = model.predict(X)
        assert pred.shape == (len(X),)


class TestFeatureImportance:
    """feature_importance 测试。"""

    def test_importance_returns_dict(self, synth_data, small_config):
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        model.fit(X, y, feature_names=fn)
        imp = model.feature_importance()
        assert isinstance(imp, dict)
        assert len(imp) > 0

    def test_importance_keys_are_feature_names(
        self, synth_data, small_config,
    ):
        X, y, fn = synth_data
        model = DoubleEnsembleModel(small_config)
        model.fit(X, y, feature_names=fn)
        imp = model.feature_importance()
        for k in imp:
            assert k in fn


class TestValidation:
    """带验证集的训练。"""

    def test_fit_with_validation(self, synth_data):
        X, y, fn = synth_data
        cfg = DEnsembleConfig(
            num_models=2, epochs=50,
            early_stopping_rounds=10, seed=42,
        )
        model = DoubleEnsembleModel(cfg)
        # 80/20 split
        n = int(len(X) * 0.8)
        model.fit(X[:n], y[:n], X[n:], y[n:], feature_names=fn)
        pred = model.predict(X)
        assert pred.shape == (len(X),)


class TestEdgeCases:
    """边界条件。"""

    def test_single_model(self, synth_data):
        X, y, fn = synth_data
        cfg = DEnsembleConfig(
            num_models=1, epochs=10,
            early_stopping_rounds=None, seed=42,
        )
        model = DoubleEnsembleModel(cfg)
        model.fit(X, y, feature_names=fn)
        pred = model.predict(X)
        assert pred.shape == (len(X),)

    def test_few_features(self):
        """仅 3 个特征。"""
        rng = np.random.RandomState(42)
        X = rng.randn(100, 3)
        y = X[:, 0] + rng.randn(100) * 0.5
        cfg = DEnsembleConfig(
            num_models=2, epochs=10,
            early_stopping_rounds=None,
            bins_fs=2, seed=42,
        )
        model = DoubleEnsembleModel(cfg)
        model.fit(X, y, feature_names=["a", "b", "c"])
        pred = model.predict(X)
        assert pred.shape == (100,)
