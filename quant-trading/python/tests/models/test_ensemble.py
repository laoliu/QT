"""多模型集成框架单元测试。"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.models.ensemble import (
    EnsembleConfig,
    MultiModelEnsemble,
)


class TestRankAverage:
    """截面 rank 平均测试。"""

    def test_basic_rank_average(self):
        cfg = EnsembleConfig(method="rank_average")
        ens = MultiModelEnsemble(cfg)
        preds = [
            np.array([1.0, 3.0, 2.0]),
            np.array([3.0, 1.0, 2.0]),
        ]
        result = ens._rank_average(preds)
        assert len(result) == 3
        # 第3个元素在两个预测中都排第2
        assert result[2] == pytest.approx(2.0, abs=0.1)

    def test_single_model_rank(self):
        cfg = EnsembleConfig()
        ens = MultiModelEnsemble(cfg)
        preds = [np.array([10.0, 20.0, 30.0])]
        result = ens._rank_average(preds)
        assert result[0] < result[1] < result[2]

    def test_identical_preds(self):
        cfg = EnsembleConfig()
        ens = MultiModelEnsemble(cfg)
        p = np.array([5.0, 3.0, 1.0])
        result = ens._rank_average([p, p])
        assert result[2] < result[1] < result[0]


class TestWeightedAverage:
    """加权平均测试。"""

    def test_equal_weights(self):
        cfg = EnsembleConfig(
            method="weighted_average",
            weights=[0.5, 0.5],
        )
        ens = MultiModelEnsemble(cfg)
        preds = [
            np.array([1.0, 2.0]),
            np.array([3.0, 4.0]),
        ]
        result = ens._weighted_average(preds)
        np.testing.assert_allclose(result, [2.0, 3.0])

    def test_unequal_weights(self):
        cfg = EnsembleConfig(
            method="weighted_average",
            weights=[0.75, 0.25],
        )
        ens = MultiModelEnsemble(cfg)
        preds = [
            np.array([4.0, 0.0]),
            np.array([0.0, 4.0]),
        ]
        result = ens._weighted_average(preds)
        np.testing.assert_allclose(result, [3.0, 1.0])

    def test_mismatched_weights_fallback(self):
        cfg = EnsembleConfig(weights=[1.0])  # 只有1个权重
        ens = MultiModelEnsemble(cfg)
        preds = [np.array([1.0]), np.array([3.0])]
        result = ens._weighted_average(preds)
        # 回退到等权
        np.testing.assert_allclose(result, [2.0])
