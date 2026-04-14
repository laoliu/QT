"""组合优化器边界条件测试

覆盖 optimizer.py 与 convex_optimizer.py 中尚未测试的边界分支。
"""

import math

import numpy as np
import pytest

from aipha.portfolio.optimizer import (
    EqualWeightOptimizer,
    MarketCapWeightOptimizer,
    MaxSharpeOptimizer,
    MinVarianceOptimizer,
    RiskParityOptimizer,
    apply_weight_constraints,
    calculate_portfolio_metrics,
)
from aipha.portfolio.convex_optimizer import (
    CVXPY_AVAILABLE,
    BlackLittermanOptimizer,
    ConvexMaxSharpeOptimizer,
    ConvexMinVarianceOptimizer,
    ConvexRiskParityOptimizer,
    MeanVarianceOptimizer,
    OptimizationConstraints,
    create_optimizer,
)


# ── helpers ──────────────────────────────────────────────────


def _constant_returns(symbols: list[str], value: float = 0.01, n: int = 60):
    """所有标的收益率完全相同（方差=0）"""
    return {s: [value] * n for s in symbols}


def _diverse_returns(symbols: list[str], seed: int = 42, n: int = 60):
    """正常随机收益率"""
    rng = np.random.RandomState(seed)
    return {s: list(rng.normal(0.001, 0.02, n)) for s in symbols}


def _zero_mean_returns(symbols: list[str], seed: int = 7, n: int = 60):
    """均值恰好等于日无风险利率（夏普=0）"""
    daily_rf = 0.02 / 252
    rng = np.random.RandomState(seed)
    result = {}
    for s in symbols:
        noise = list(rng.normal(0, 0.01, n))
        # 强制均值 == daily_rf
        mean_noise = sum(noise) / len(noise)
        result[s] = [x - mean_noise + daily_rf for x in noise]
    return result


# ══════════════════════════════════════════════════════════════
# 1. optimizer.py 边界测试
# ══════════════════════════════════════════════════════════════


class TestRiskParityZeroVol:
    """风险平价: 零波动率分支"""

    def test_constant_returns_fallback(self):
        """所有标的收益率完全相同(variance=0) → vol=0 → inv_vol 用 1.0"""
        opt = RiskParityOptimizer()
        rets = _constant_returns(["A", "B", "C"])
        w = opt.optimize(symbols=["A", "B", "C"], returns=rets)
        # vol 全为 0 → inv_vols 全为 1.0 → 等权
        assert w["A"] == pytest.approx(1 / 3, abs=1e-9)
        assert w["B"] == pytest.approx(1 / 3, abs=1e-9)

    def test_partial_zero_vol(self):
        """只有一个标的 vol=0, 其余正常"""
        opt = RiskParityOptimizer()
        rets = _diverse_returns(["A", "B"], seed=1)
        rets["C"] = [0.01] * 60  # C 方差=0
        w = opt.optimize(symbols=["A", "B", "C"], returns=rets)
        assert sum(w.values()) == pytest.approx(1.0)
        # C 的 inv_vol = 1.0, 比其他的小（他们的 vol < 1 → inv_vol > 1），所以 C 权重较小
        assert w["C"] < w["A"] or w["C"] < w["B"]


class TestMinVarianceZeroVol:
    """最小方差: 零波动率分支"""

    def test_constant_returns_fallback(self):
        """方差=0 → vol=0 → inv_vol_sq 用 1.0"""
        opt = MinVarianceOptimizer()
        rets = _constant_returns(["X", "Y"])
        w = opt.optimize(symbols=["X", "Y"], returns=rets)
        # 全 0 波动率 → inv_vol_sq 全 1.0 → 等权
        assert w["X"] == pytest.approx(0.5)


class TestMaxSharpeZeroSharpe:
    """最大夏普: 所有标的夏普恰好为 0"""

    def test_sharpe_exactly_zero_fallback(self):
        """std=0 时 sharpe 强制为 0 → total_sharpe==0 → 等权"""
        daily_rf = 0.02 / 252
        # 常数收益率 = daily_rf → variance=0 → std=0 → sharpe=0
        rets = {"A": [daily_rf] * 60, "B": [daily_rf] * 60}
        opt = MaxSharpeOptimizer(risk_free_rate=0.02)
        w = opt.optimize(symbols=["A", "B"], returns=rets)
        assert w["A"] == pytest.approx(0.5)
        assert w["B"] == pytest.approx(0.5)


class TestApplyWeightConstraintsEdge:
    """apply_weight_constraints 边界"""

    def test_empty_weights_returns_empty(self):
        """空 dict → 空 dict"""
        assert apply_weight_constraints({}) == {}

    def test_all_clipped_to_max(self):
        """全部权重超过 max_weight"""
        w = apply_weight_constraints({"A": 0.8, "B": 0.7}, max_weight=0.6)
        assert w["A"] == pytest.approx(0.5)
        assert w["B"] == pytest.approx(0.5)

    def test_normalize_false(self):
        """normalize=False 只 clip 不归一"""
        w = apply_weight_constraints(
            {"A": 0.8, "B": 0.3}, max_weight=0.5, normalize=False
        )
        assert w["A"] == 0.5
        assert w["B"] == 0.3


class TestCalculatePortfolioMetricsEdge:
    """calculate_portfolio_metrics 边界"""

    def test_symbol_missing_in_returns(self):
        """权重中有个 symbol 不在 returns → min_len=0 → 回退零指标"""
        result = calculate_portfolio_metrics(
            weights={"A": 0.5, "Z": 0.5},
            returns={"A": [0.01, 0.02]},
        )
        assert result["expected_return"] == 0.0

    def test_single_data_point(self):
        """只有 1 条收益率 → min_len < 2 → 零指标"""
        result = calculate_portfolio_metrics(
            weights={"A": 1.0},
            returns={"A": [0.05]},
        )
        assert result["volatility"] == 0.0

    def test_empty_weights(self):
        """空权重 → 零指标"""
        result = calculate_portfolio_metrics(weights={}, returns={"A": [0.01, 0.02]})
        assert result["sharpe_ratio"] == 0.0


# ══════════════════════════════════════════════════════════════
# 2. convex_optimizer.py 边界测试
# ══════════════════════════════════════════════════════════════


class TestConvexMinVarianceSingleAsset:
    """单标的传入凸优化器"""

    def test_single_symbol_weight_one(self):
        """n=1 时权重应为 1.0"""
        opt = ConvexMinVarianceOptimizer()
        rets = _diverse_returns(["A"])
        w = opt.optimize(symbols=["A"], returns=rets)
        assert w["A"] == pytest.approx(1.0, abs=1e-4)


class TestConvexFromCovarianceDict:
    """从协方差 dict（而非 returns）构建矩阵"""

    def test_covariance_dict_path(self):
        """直接传 covariance dict，不传 returns"""
        cov = {
            "A": {"A": 0.04, "B": 0.01},
            "B": {"A": 0.01, "B": 0.09},
        }
        opt = ConvexMinVarianceOptimizer()
        w = opt.optimize(symbols=["A", "B"], covariance=cov)
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)
        # A 方差更小 → 权重更大
        assert w["A"] > w["B"]

    def test_risk_parity_covariance_dict(self):
        """风险平价也支持 covariance dict"""
        cov = {
            "X": {"X": 0.01, "Y": 0.002},
            "Y": {"X": 0.002, "Y": 0.04},
        }
        opt = ConvexRiskParityOptimizer()
        w = opt.optimize(symbols=["X", "Y"], covariance=cov)
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)


class TestCvxpyFallbackPath:
    """cvxpy 求解失败 → _fallback_solve"""

    def test_infeasible_problem_triggers_fallback(self):
        """min_weight=0.6 + 3 个资产 → sum >= 1.8 > 1 → infeasible → fallback"""
        opt = ConvexMinVarianceOptimizer(
            constraints=OptimizationConstraints(min_weight=0.6, max_weight=1.0),
        )
        rets = _diverse_returns(["A", "B", "C"])
        # cvxpy 无法满足约束 → 回退到 _fallback_solve
        w = opt.optimize(symbols=["A", "B", "C"], returns=rets)
        # 应该得到某种有效权重（fallback 不强制 min_weight）
        assert sum(w.values()) == pytest.approx(1.0, abs=0.01)

    def test_fallback_singular_matrix(self):
        """完全线性相关 → fallback 中 inv 失败 → 等权"""
        # 构造完全相同的收益率 → 协方差矩阵奇异
        rets = {"A": [0.01, 0.02, 0.03] * 20, "B": [0.01, 0.02, 0.03] * 20}
        opt = ConvexMinVarianceOptimizer()
        w = opt.optimize(symbols=["A", "B"], returns=rets)
        # ensure_positive_definite 会修正，仍返回权重
        assert sum(w.values()) == pytest.approx(1.0, abs=0.01)


class TestConvexRiskParityCustomTarget:
    """风险平价: 自定义 target_risk_contrib"""

    def test_unequal_risk_contribution(self):
        """target = [0.7, 0.3] → 权重不等"""
        opt = ConvexRiskParityOptimizer(target_risk_contrib=[0.7, 0.3])
        rets = _diverse_returns(["A", "B"])
        w = opt.optimize(symbols=["A", "B"], returns=rets)
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)
        # 不应该是等权
        assert abs(w["A"] - w["B"]) > 0.01


class TestConvexMaxSharpeEdge:
    """最大夏普凸优化边界"""

    def test_all_negative_excess_returns_fallback_to_minvar(self):
        """所有超额收益 <= 0 → 回退到 MinVariance"""
        # 构造非常差的收益率
        rets = {
            "A": [-0.05] * 60,
            "B": [-0.03] * 60,
        }
        opt = ConvexMaxSharpeOptimizer(risk_free_rate=0.5)  # 很高无风险利率
        w = opt.optimize(symbols=["A", "B"], returns=rets)
        assert sum(w.values()) == pytest.approx(1.0, abs=0.01)


class TestMeanVarianceEdge:
    """均值-方差优化器边界"""

    def test_target_return_constraint(self):
        """给定 target_return 时走另一个分支"""
        opt = MeanVarianceOptimizer(target_return=0.10)
        rets = _diverse_returns(["A", "B", "C"])
        w = opt.optimize(symbols=["A", "B", "C"], returns=rets)
        assert sum(w.values()) == pytest.approx(1.0, abs=0.01)

    def test_no_returns_fallback(self):
        """无收益率数据 → 等权"""
        opt = MeanVarianceOptimizer()
        w = opt.optimize(symbols=["A", "B"])
        assert w["A"] == pytest.approx(0.5)


class TestBlackLittermanEdge:
    """BL 优化器边界"""

    def test_no_views_returns_market_weights(self):
        """无观点 → 返回市场权重"""
        opt = BlackLittermanOptimizer(views=[])
        rets = _diverse_returns(["A", "B"])
        caps = {"A": 1000, "B": 3000}
        w = opt.optimize(symbols=["A", "B"], returns=rets, market_caps=caps)
        assert w["A"] == pytest.approx(0.25)
        assert w["B"] == pytest.approx(0.75)

    def test_single_absolute_view(self):
        """单条绝对观点"""
        # A 预期收益 10%, confidence=1
        opt = BlackLittermanOptimizer(
            views=[(["A"], [1.0], 0.10, 1.0)],
        )
        rets = _diverse_returns(["A", "B"])
        caps = {"A": 1000, "B": 1000}
        w = opt.optimize(symbols=["A", "B"], returns=rets, market_caps=caps)
        assert sum(w.values()) == pytest.approx(1.0, abs=0.05)

    def test_relative_view(self):
        """相对观点: A 超越 B 5%"""
        opt = BlackLittermanOptimizer(
            views=[(["A", "B"], [1.0, -1.0], 0.05, 1.0)],
        )
        rets = _diverse_returns(["A", "B"])
        caps = {"A": 1000, "B": 1000}
        w = opt.optimize(symbols=["A", "B"], returns=rets, market_caps=caps)
        assert sum(w.values()) == pytest.approx(1.0, abs=0.05)
        # A 被看好 → 权重应更大
        assert w["A"] > w["B"]

    def test_missing_market_caps_fallback(self):
        """缺少市值 → 等权"""
        opt = BlackLittermanOptimizer()
        w = opt.optimize(symbols=["A", "B"])
        assert w["A"] == pytest.approx(0.5)

    def test_missing_returns_fallback(self):
        """有市值但缺收益率 → 等权"""
        opt = BlackLittermanOptimizer()
        caps = {"A": 1000, "B": 1000}
        w = opt.optimize(symbols=["A", "B"], market_caps=caps)
        assert w["A"] == pytest.approx(0.5)


class TestCreateOptimizerEdge:
    """create_optimizer 工厂边界"""

    def test_unknown_method_raises(self):
        """未知方法 → ValueError"""
        with pytest.raises(ValueError, match="未知优化方法"):
            create_optimizer("unknown_method_xyz")

    def test_alias_mv(self):
        """别名 'mv' → ConvexMinVarianceOptimizer"""
        opt = create_optimizer("mv")
        assert isinstance(opt, ConvexMinVarianceOptimizer)

    def test_alias_bl(self):
        """别名 'bl' → BlackLittermanOptimizer"""
        opt = create_optimizer("bl")
        assert isinstance(opt, BlackLittermanOptimizer)

    def test_alias_with_hyphens(self):
        """带连字符的方法名也能匹配"""
        opt = create_optimizer("risk-parity")
        assert isinstance(opt, ConvexRiskParityOptimizer)

    def test_alias_with_spaces(self):
        """带空格的方法名也能匹配"""
        opt = create_optimizer("mean variance")
        assert isinstance(opt, MeanVarianceOptimizer)

    def test_market_cap_alias(self):
        """market_cap → MarketCapWeightOptimizer"""
        opt = create_optimizer("market_cap")
        assert isinstance(opt, MarketCapWeightOptimizer)
