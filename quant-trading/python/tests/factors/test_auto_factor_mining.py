"""自动因子挖掘 (AutoFactorMiner) 单元测试。"""

from __future__ import annotations

import pytest
import numpy as np
import pandas as pd

gplearn = pytest.importorskip("gplearn")

from aipha.factors.auto_factor_mining import (
    AutoFactorMiner,
    MinedFactor,
    MiningResult,
)


# ── 数据工厂 ──


def make_synthetic_data(
    n: int = 500, n_features: int = 5, seed: int = 42,
) -> tuple[pd.DataFrame, pd.Series]:
    """生成带有线性信号的合成数据"""
    rng = np.random.RandomState(seed)
    features = pd.DataFrame(
        rng.randn(n, n_features),
        columns=[f"f{i}" for i in range(n_features)],
    )
    # y = f0 + 0.5*f1 + noise
    returns = features["f0"] + 0.5 * features["f1"] + rng.randn(n) * 0.5
    return features, returns


# ── 数据结构测试 ──


class TestMinedFactor:
    def test_to_dict(self):
        mf = MinedFactor(
            expression="add(X0, X1)",
            ic_mean=0.05,
            ic_std=0.02,
            icir=2.5,
            t_stat=3.0,
            hlz_t_adj=1.5,
            is_significant=False,
        )
        d = mf.to_dict()
        assert d["expression"] == "add(X0, X1)"
        assert d["icir"] == 2.5

    def test_significant_flag(self):
        mf = MinedFactor(
            expression="X0",
            ic_mean=0.05, ic_std=0.01, icir=5.0,
            t_stat=5.0, hlz_t_adj=3.0, is_significant=True,
        )
        assert mf.is_significant


class TestMiningResult:
    def test_empty_result(self):
        r = MiningResult()
        assert r.total_explored == 0
        d = r.to_dict()
        assert d["factors"] == []

    def test_with_factors(self):
        r = MiningResult(
            total_explored=100,
            total_significant=2,
            factors=[
                MinedFactor("X0", 0.03, 0.01, 3.0, 3.0, 2.5, True),
            ],
        )
        assert r.total_significant == 2


# ── HLZ 校正测试 ──


class TestHLZAdjust:
    def test_single_trial(self):
        miner = AutoFactorMiner()
        adj = miner._hlz_adjust(3.0, 1)
        assert adj == 3.0  # 无惩罚

    def test_100_trials(self):
        miner = AutoFactorMiner()
        adj = miner._hlz_adjust(3.0, 100)
        # penalty = sqrt(2*ln(100)) ≈ 3.03, adj ≈ -0.03
        assert adj < 3.0
        assert adj == pytest.approx(3.0 - np.sqrt(2.0 * np.log(100)), abs=0.01)

    def test_10_trials(self):
        miner = AutoFactorMiner()
        adj = miner._hlz_adjust(3.0, 10)
        expected = 3.0 - np.sqrt(2.0 * np.log(10))
        assert adj == pytest.approx(expected, abs=0.01)


# ── IC 计算测试 ──


class TestComputeIC:
    def test_perfect_correlation(self):
        miner = AutoFactorMiner()
        x = np.arange(100, dtype=float)
        y = x * 2.0
        ic, ic_std, icir = miner._compute_ic(x, y)
        assert ic > 0.9

    def test_no_correlation(self):
        miner = AutoFactorMiner()
        rng = np.random.RandomState(0)
        x = rng.randn(200)
        y = rng.randn(200)
        ic, ic_std, icir = miner._compute_ic(x, y)
        assert abs(ic) < 0.2

    def test_too_few_samples(self):
        miner = AutoFactorMiner()
        ic, ic_std, icir = miner._compute_ic(
            np.array([1.0, 2.0]), np.array([1.0, 2.0]),
        )
        assert ic == 0.0


# ── 去相关测试 ──


class TestDecorrelate:
    def test_empty_list(self):
        miner = AutoFactorMiner()
        assert miner._decorrelate([]) == []

    def test_single_factor(self):
        miner = AutoFactorMiner()
        f = MinedFactor("X0", 0.05, 0.01, 5.0, 5.0, 3.0, True)
        assert len(miner._decorrelate([f])) == 1

    def test_removes_redundant(self):
        miner = AutoFactorMiner()
        factors = [
            MinedFactor("X0", 0.050, 0.01, 5.0, 5.0, 3.0, True),
            MinedFactor("X1", 0.051, 0.01, 4.0, 4.0, 2.5, True),  # 近似冗余
            MinedFactor("X2", 0.080, 0.01, 3.0, 3.0, 2.0, True),  # 不同
        ]
        result = miner._decorrelate(factors)
        assert len(result) == 2  # X0 和 X2


# ── 端到端挖掘测试 ──


class TestMineEndToEnd:
    def test_mine_with_synthetic_data(self):
        features, returns = make_synthetic_data(n=200, n_features=3)
        miner = AutoFactorMiner(
            population_size=50,
            generations=2,
            n_components=5,
            ic_threshold=0.01,
        )
        result = miner.mine(returns, features, n_generations=2)
        assert result.total_explored > 0
        assert isinstance(result.factors, list)

    def test_mine_too_few_rows(self):
        features = pd.DataFrame({"f0": [1.0], "f1": [2.0]})
        returns = pd.Series([0.5])
        miner = AutoFactorMiner(population_size=10, generations=1)
        result = miner.mine(returns, features)
        assert result.total_explored == 0

    def test_filter_hlz(self):
        result = MiningResult(
            total_explored=50,
            factors=[
                MinedFactor("X0", 0.05, 0.01, 5.0, 5.0, 0.0, False),
                MinedFactor("X1", 0.01, 0.02, 0.5, 0.5, 0.0, False),
            ],
        )
        miner = AutoFactorMiner(hlz_t_threshold=2.0)
        sig = miner.filter_hlz(result, n_total_trials=50)
        # 只有高 t-stat 的可能通过
        for f in sig:
            assert f.hlz_t_adj > 2.0
