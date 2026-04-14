"""equity_factors 通用因子计算器单元测试

覆盖：
- compute_value_factor: 正常/None/inf
- compute_quality_factor: ROE 正常/缺失
- compute_debt_factor: 正常/缺失
- compute_size_factor: 正常/零市值
- compute_turnover_factor: 正常
- compute_momentum_factor: 正常/数据不足/反转
- compute_volatility_factor: 正常/数据不足
- compute_earnings_growth_factor: 正常/缺失
- score_candidates_with_factors: 组合打分
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from aipha.factors.equity_factors import (
    compute_abnormal_turnover_factor,
    compute_accruals_quality_factor,
    compute_cgo_factor,
    compute_debt_factor,
    compute_dividend_persistence_factor,
    compute_earnings_growth_factor,
    compute_earnings_stability_factor,
    compute_ewma_volatility_factor,
    compute_fcfy_factor,
    compute_max_daily_return_factor,
    compute_momentum_factor,
    compute_overreaction_composite,
    compute_peg_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_risk_adjusted_momentum,
    compute_size_factor,
    compute_str_factor,
    compute_turnover_factor,
    compute_valuation_percentile_factor,
    compute_value_factor,
    compute_volatility_factor,
    score_candidates_with_factors,
    winsorize_factors,
)
from aipha.strategy.equity_rotation_base import EquityCandidateInfo


def _c(symbol: str, metadata: dict | None = None,
       circ_mv: float = 100.0, turnover_rate: float = 5.0) -> EquityCandidateInfo:
    """快速构造候选标的。"""
    return EquityCandidateInfo(
        symbol=symbol,
        close=10.0,
        metadata=metadata or {},
        circ_mv=circ_mv,
        turnover_rate=turnover_rate,
    )


# ============================================================================
# 价值因子
# ============================================================================


class TestValueFactor:

    def test_normal_pe(self):
        candidates = [
            _c("A", {"pe_ttm": 10.0}),
            _c("B", {"pe_ttm": 20.0}),
        ]
        f = compute_value_factor(candidates, "pe_ttm")
        assert f["A"] > f["B"]  # 低 PE → 更高分

    def test_none_pe(self):
        f = compute_value_factor([_c("A", {"pe_ttm": None})], "pe_ttm")
        assert math.isnan(f["A"])

    def test_zero_pe(self):
        f = compute_value_factor([_c("A", {"pe_ttm": 0.0})], "pe_ttm")
        assert math.isnan(f["A"])  # PE=0 视为无效

    def test_missing_field(self):
        f = compute_value_factor([_c("A", {})], "pe_ttm")
        assert math.isnan(f["A"])

    def test_pb_metric(self):
        candidates = [
            _c("A", {"pb": 1.5}),
            _c("B", {"pb": 3.0}),
        ]
        f = compute_value_factor(candidates, "pb")
        assert f["A"] > f["B"]


# ============================================================================
# 质量因子
# ============================================================================


class TestQualityFactor:

    def test_roe_ordering(self):
        candidates = [
            _c("A", {"roe": 20.0}),
            _c("B", {"roe": 10.0}),
        ]
        f = compute_quality_factor(candidates, "roe")
        assert f["A"] > f["B"]

    def test_missing_roe(self):
        f = compute_quality_factor([_c("A", {})], "roe")
        assert math.isnan(f["A"])

    def test_grossprofit_margin(self):
        f = compute_quality_factor([_c("A", {"grossprofit_margin": 35.0})], "grossprofit_margin")
        assert f["A"] == 35.0


# ============================================================================
# 负债因子
# ============================================================================


class TestDebtFactor:

    def test_lower_debt_better(self):
        candidates = [
            _c("A", {"debt_to_assets": 30.0}),
            _c("B", {"debt_to_assets": 60.0}),
        ]
        f = compute_debt_factor(candidates)
        assert f["A"] > f["B"]

    def test_missing_debt(self):
        f = compute_debt_factor([_c("A", {})])
        assert math.isnan(f["A"])


# ============================================================================
# 规模因子
# ============================================================================


class TestSizeFactor:

    def test_smaller_is_better(self):
        candidates = [
            _c("A", circ_mv=50.0),
            _c("B", circ_mv=500.0),
        ]
        f = compute_size_factor(candidates)
        assert f["A"] > f["B"]

    def test_zero_market_cap(self):
        f = compute_size_factor([_c("A", circ_mv=0.0)])
        assert f["A"] == -float("inf")

    def test_positive_values(self):
        f = compute_size_factor([_c("A", circ_mv=100.0)])
        assert math.isfinite(f["A"])


# ============================================================================
# 换手率因子
# ============================================================================


class TestTurnoverFactor:

    def test_lower_turnover_better(self):
        candidates = [
            _c("A", turnover_rate=2.0),
            _c("B", turnover_rate=10.0),
        ]
        f = compute_turnover_factor(candidates)
        assert f["A"] > f["B"]


# ============================================================================
# 动量因子
# ============================================================================


class TestMomentumFactor:

    def _hist(self, n: int, start: float = 10.0, step: float = 0.1):
        return [start + i * step for i in range(n)]

    def test_positive_momentum(self):
        history = {"A": self._hist(25, 10.0, 0.1)}  # 上涨序列
        f = compute_momentum_factor(history, [_c("A")], lookback=20)
        assert f["A"] > 0

    def test_insufficient_data(self):
        history = {"A": [10.0, 10.5]}
        f = compute_momentum_factor(history, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_invert(self):
        history = {"A": self._hist(25, 10.0, 0.1)}
        f_normal = compute_momentum_factor(history, [_c("A")], lookback=20, invert=False)
        f_invert = compute_momentum_factor(history, [_c("A")], lookback=20, invert=True)
        assert f_normal["A"] > 0
        assert f_invert["A"] < 0

    def test_missing_symbol(self):
        f = compute_momentum_factor({}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])


# ============================================================================
# 波动率因子
# ============================================================================


class TestVolatilityFactor:

    def test_low_vol_better(self):
        # 低波动序列
        low_vol = [10.0 + 0.01 * i for i in range(25)]
        # 高波动序列
        high_vol = [10.0 + 0.5 * ((-1) ** i) for i in range(25)]
        history = {"A": low_vol, "B": high_vol}
        candidates = [_c("A"), _c("B")]
        f = compute_volatility_factor(history, candidates, lookback=20)
        assert f["A"] > f["B"]  # 低波动 → 更高分（取负）

    def test_insufficient_data(self):
        f = compute_volatility_factor({"A": [10.0]}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])


# ============================================================================
# 盈利增长因子
# ============================================================================


class TestEarningsGrowthFactor:

    def test_higher_growth_better(self):
        candidates = [
            _c("A", {"dt_netprofit_yoy": 30.0}),
            _c("B", {"dt_netprofit_yoy": 10.0}),
        ]
        f = compute_earnings_growth_factor(candidates)
        assert f["A"] > f["B"]

    def test_missing_field(self):
        f = compute_earnings_growth_factor([_c("A", {})])
        assert math.isnan(f["A"])

    def test_revenue_yoy(self):
        f = compute_earnings_growth_factor(
            [_c("A", {"revenue_yoy": 25.0})], field="revenue_yoy"
        )
        assert f["A"] == 25.0


# ============================================================================
# 复合打分
# ============================================================================


class TestScoreCandidatesWithFactors:

    def test_basic_scoring(self):
        candidates = [
            _c("A", {"pe_ttm": 10.0, "roe": 20.0}, circ_mv=50.0),
            _c("B", {"pe_ttm": 20.0, "roe": 10.0}, circ_mv=500.0),
        ]
        f_value = compute_value_factor(candidates, "pe_ttm")
        f_quality = compute_quality_factor(candidates, "roe")
        f_size = compute_size_factor(candidates)

        scores = score_candidates_with_factors(
            candidates,
            [(f_value, 0.3), (f_quality, 0.3), (f_size, 0.4)],
        )
        # A 在所有因子上都优于 B
        assert scores["A"] > scores["B"]

    def test_empty_candidates(self):
        scores = score_candidates_with_factors([], [])
        assert scores == {}

    def test_zero_weight_ignored(self):
        candidates = [_c("A", {"pe_ttm": 10.0})]
        f_value = compute_value_factor(candidates, "pe_ttm")
        scores = score_candidates_with_factors(
            candidates,
            [(f_value, 0.0)],
        )
        assert scores.get("A", 0.0) == 0.0


# ============================================================================
# 多周期动量 & skip_recent 测试
# ============================================================================


class TestMomentumSkipRecent:
    """测试 skip_recent 参数：跳过最近 N 天剔除短期反转噪声。"""

    @staticmethod
    def _rising(n: int, start: float = 10.0, step: float = 0.5) -> list[float]:
        return [start + i * step for i in range(n)]

    def test_skip_recent_excludes_last_day(self):
        """skip_recent=1 时使用倒数第 2 天的价格而非最后一天。"""
        hist = self._rising(25)
        hist[-1] = hist[-2] - 5.0  # 最后一天暴跌
        f0 = compute_momentum_factor({"A": hist}, [_c("A")], lookback=20, skip_recent=0)
        f1 = compute_momentum_factor({"A": hist}, [_c("A")], lookback=20, skip_recent=1)
        # 不跳过：包含暴跌 → 动量较低；跳过：不含暴跌 → 动量更高
        assert f1["A"] > f0["A"]

    def test_skip_recent_needs_more_data(self):
        """skip_recent 增加数据需求量：lookback=20, skip_recent=1 需要 23 个点。"""
        hist_21 = self._rising(21)  # lookback=20 + 1 = 21, 刚好够 skip_recent=0
        hist_22 = self._rising(22)
        f0 = compute_momentum_factor({"A": hist_21}, [_c("A")], lookback=20, skip_recent=0)
        assert math.isfinite(f0["A"]), "21 点够 skip_recent=0"
        # 21 点对 skip_recent=1 不够（需要 20 + 1 + 1 = 22）
        f1 = compute_momentum_factor({"A": hist_21}, [_c("A")], lookback=20, skip_recent=1)
        assert math.isnan(f1["A"]), "21 点不够 skip_recent=1"
        # 22 点刚好够 skip_recent=1
        f2 = compute_momentum_factor({"A": hist_22}, [_c("A")], lookback=20, skip_recent=1)
        assert math.isfinite(f2["A"]), "22 点刚好够 skip_recent=1"

    def test_skip_recent_2(self):
        hist = self._rising(25)
        f2 = compute_momentum_factor({"A": hist}, [_c("A")], lookback=20, skip_recent=2)
        assert math.isfinite(f2["A"])


class TestMultiPeriodMomentum:
    """测试 multi_period 多周期动量混合（5d×0.2 + 20d×0.5 + 60d×0.3）。"""

    @staticmethod
    def _rising(n: int, start: float = 10.0, step: float = 0.1) -> list[float]:
        return [start + i * step for i in range(n)]

    def test_multi_period_needs_at_least_6_points(self):
        """多周期模式至少需要 5d+1=6 个数据点。"""
        short_hist = [10.0, 11.0, 12.0, 13.0, 14.0]  # 5 点
        f = compute_momentum_factor({"A": short_hist}, [_c("A")], multi_period=True)
        assert math.isnan(f["A"])

    def test_multi_period_7_points_uses_5d_only(self):
        """7 个数据点只够 5d 周期。"""
        hist = self._rising(7)
        f = compute_momentum_factor({"A": hist}, [_c("A")], multi_period=True)
        assert math.isfinite(f["A"])
        assert f["A"] > 0  # 上涨序列

    def test_multi_period_65_points_uses_all_three(self):
        """65 个数据点够所有三个周期。"""
        hist = self._rising(65)
        f = compute_momentum_factor({"A": hist}, [_c("A")], multi_period=True)
        assert math.isfinite(f["A"])
        assert f["A"] > 0

    def test_multi_period_invert(self):
        """invert 反转多周期动量。"""
        hist = self._rising(65)
        fn = compute_momentum_factor({"A": hist}, [_c("A")], multi_period=True, invert=False)
        fi = compute_momentum_factor({"A": hist}, [_c("A")], multi_period=True, invert=True)
        assert fn["A"] > 0
        assert fi["A"] < 0

    def test_multi_period_with_skip_recent(self):
        """multi_period + skip_recent 组合使用。"""
        hist = self._rising(70)
        hist[-1] = hist[-2] - 10.0  # 最后一天暴跌
        f0 = compute_momentum_factor({"A": hist}, [_c("A")], multi_period=True, skip_recent=0)
        f1 = compute_momentum_factor({"A": hist}, [_c("A")], multi_period=True, skip_recent=1)
        # 跳过暴跌日 → 动量更高
        assert f1["A"] > f0["A"]


# ============================================================================
# 风险调整动量因子
# ============================================================================


class TestRiskAdjustedMomentum:

    @staticmethod
    def _rising(n: int, start: float = 10.0, step: float = 0.1) -> list[float]:
        return [start + i * step for i in range(n)]

    def test_positive_for_rising(self):
        hist = self._rising(25)
        f = compute_risk_adjusted_momentum({"A": hist}, [_c("A")], lookback=20)
        assert f["A"] > 0

    def test_insufficient_data(self):
        f = compute_risk_adjusted_momentum({"A": [10.0, 11.0]}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_missing_symbol(self):
        f = compute_risk_adjusted_momentum({}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_zero_old_price(self):
        hist = [0.0] + [10.0] * 24
        f = compute_risk_adjusted_momentum({"A": hist}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_constant_prices_nan(self):
        """Constant prices → vol=0 → NaN."""
        hist = [10.0] * 25
        f = compute_risk_adjusted_momentum({"A": hist}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_favors_consistent_uptrend(self):
        """Consistent uptrend scores higher than volatile uptrend of same magnitude."""
        n = 25
        # Smooth uptrend
        smooth = [10.0 + 0.1 * i for i in range(n)]
        # Volatile uptrend with same start/end but big swings
        volatile = [10.0 + 0.1 * i + 1.0 * ((-1) ** i) for i in range(n)]
        volatile[-1] = smooth[-1]  # same endpoint
        volatile[0] = smooth[0]  # same start
        candidates = [_c("A"), _c("B")]
        history = {"A": smooth, "B": volatile}
        f = compute_risk_adjusted_momentum(history, candidates, lookback=20)
        # Smooth should have higher risk-adjusted momentum
        if math.isfinite(f["A"]) and math.isfinite(f["B"]):
            assert f["A"] > f["B"]


# ============================================================================
# EWMA 波动率因子
# ============================================================================


class TestEWMAVolatilityFactor:

    @staticmethod
    def _prices(n: int, start: float = 10.0, step: float = 0.01) -> list[float]:
        return [start + i * step for i in range(n)]

    def test_negative_values(self):
        """EWMA volatility returns negative values (lower vol = higher score)."""
        hist = self._prices(25)
        f = compute_ewma_volatility_factor({"A": hist}, [_c("A")], lookback=20)
        assert f["A"] < 0

    def test_insufficient_data(self):
        f = compute_ewma_volatility_factor({"A": [10.0]}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_missing_symbol(self):
        f = compute_ewma_volatility_factor({}, [_c("A")], lookback=20)
        assert math.isnan(f["A"])

    def test_low_vol_better(self):
        low_vol = [10.0 + 0.01 * i for i in range(25)]
        high_vol = [10.0 + 0.5 * ((-1) ** i) for i in range(25)]
        candidates = [_c("A"), _c("B")]
        f = compute_ewma_volatility_factor({"A": low_vol, "B": high_vol}, candidates, lookback=20)
        assert f["A"] > f["B"]  # 低波动 → 更高分

    def test_decay_sensitivity(self):
        """Higher decay → smoother (less sensitive to recent spike)."""
        hist = [10.0 + 0.01 * i for i in range(24)] + [15.0]  # spike at end
        f_high = compute_ewma_volatility_factor({"A": hist}, [_c("A")], lookback=20, decay=0.99)
        f_low = compute_ewma_volatility_factor({"A": hist}, [_c("A")], lookback=20, decay=0.50)
        # Lower decay reacts more to the spike → higher vol → more negative
        assert f_low["A"] < f_high["A"]


# ============================================================================
# 缩尾处理工具
# ============================================================================


class TestWinsorizeFactors:

    def test_basic_clipping(self):
        vals = {f"S{i}": float(i) for i in range(100)}
        result = winsorize_factors(vals, lower_pct=0.05, upper_pct=0.95)
        assert result["S0"] >= 5.0  # clipped up
        assert result["S99"] <= 95.0  # clipped down

    def test_preserves_nan(self):
        vals = {"A": 1.0, "B": float("nan"), "C": 3.0}
        result = winsorize_factors(vals)
        assert math.isnan(result["B"])

    def test_preserves_neg_inf(self):
        vals = {"A": 1.0, "B": -float("inf"), "C": 3.0}
        result = winsorize_factors(vals)
        assert result["B"] == -float("inf")

    def test_single_value(self):
        vals = {"A": 5.0}
        result = winsorize_factors(vals)
        assert result == {"A": 5.0}

    def test_all_nan(self):
        vals = {"A": float("nan"), "B": float("nan")}
        result = winsorize_factors(vals)
        assert math.isnan(result["A"])
        assert math.isnan(result["B"])

    def test_empty(self):
        assert winsorize_factors({}) == {}

    def test_no_clipping_within_range(self):
        vals = {"A": 1.0, "B": 2.0, "C": 3.0}
        result = winsorize_factors(vals, lower_pct=0.0, upper_pct=1.0)
        assert result == vals

    def test_mixed_finite_and_nan(self):
        vals = {f"S{i}": float(i) for i in range(50)}
        vals["NAN1"] = float("nan")
        vals["NAN2"] = float("nan")
        result = winsorize_factors(vals, lower_pct=0.1, upper_pct=0.9)
        assert math.isnan(result["NAN1"])
        assert math.isnan(result["NAN2"])
        # Finite values should be within bounds
        finite = [v for v in result.values() if math.isfinite(v)]
        assert min(finite) >= 5.0
        assert max(finite) <= 45.0


# ============================================================================
# Piotroski F-Score 因子
# ============================================================================


class TestPiotroskiFactor:
    """compute_piotroski_factor 测试"""

    def _full_meta(self) -> dict:
        return {
            "roa": 5.0, "roa_prev": 4.0,
            "cfps": 1.0, "eps": 0.8,
            "debt_to_assets": 40.0, "debt_to_assets_prev": 45.0,
            "current_ratio": 2.0, "current_ratio_prev": 1.5,
            "total_share": 1000.0, "total_share_prev": 1000.0,
            "grossprofit_margin": 30.0, "grossprofit_margin_prev": 28.0,
            "asset_turnover": 0.8, "asset_turnover_prev": 0.7,
        }

    def test_perfect_score(self):
        """全 9 维度通过 → 9 分"""
        cs = [_c("A", self._full_meta())]
        r = compute_piotroski_factor(cs)
        assert r["A"] == 9.0

    def test_zero_score(self):
        """全部不通过 → 0 分"""
        m = {
            "roa": -2.0, "roa_prev": 0.0, "cfps": -0.5, "eps": 0.3,
            "debt_to_assets": 60.0, "debt_to_assets_prev": 50.0,
            "current_ratio": 1.0, "current_ratio_prev": 1.5,
            "total_share": 1100.0, "total_share_prev": 1000.0,
            "grossprofit_margin": 25.0, "grossprofit_margin_prev": 28.0,
            "asset_turnover": 0.6, "asset_turnover_prev": 0.7,
        }
        r = compute_piotroski_factor([_c("A", m)])
        assert r["A"] == 0.0

    def test_insufficient_data(self):
        """不足 5 个维度 → NaN"""
        r = compute_piotroski_factor([_c("A", {"roa": 5.0})])
        assert math.isnan(r["A"])

    def test_partial_data_5(self):
        """恰好 5 个维度"""
        m = {
            "roa": 5.0, "cfps": 1.0, "eps": 0.8,
            "debt_to_assets": 40.0, "debt_to_assets_prev": 45.0,
            "current_ratio": 2.0, "current_ratio_prev": 1.5,
        }
        r = compute_piotroski_factor([_c("A", m)])
        assert not math.isnan(r["A"])

    def test_empty(self):
        """空 metadata"""
        r = compute_piotroski_factor([_c("A", {})])
        assert math.isnan(r["A"])

    def test_multiple_candidates(self):
        """多标的排序"""
        c_good = _c("GOOD", self._full_meta())
        c_bad = _c("BAD", {"roa": -1.0, "roa_prev": 0.0, "cfps": -0.5,
                            "eps": 0.3, "debt_to_assets": 60.0,
                            "debt_to_assets_prev": 50.0,
                            "current_ratio": 1.0, "current_ratio_prev": 1.5,
                            "total_share": 1100.0, "total_share_prev": 1000.0,
                            "grossprofit_margin": 25.0,
                            "grossprofit_margin_prev": 28.0,
                            "asset_turnover": 0.6, "asset_turnover_prev": 0.7})
        r = compute_piotroski_factor([c_good, c_bad])
        assert r["GOOD"] > r["BAD"]


# ============================================================================
# 应计盈余质量因子
# ============================================================================


class TestAccrualsQualityFactor:
    """compute_accruals_quality_factor 测试"""

    def test_absolute_path(self):
        """绝对值口径"""
        m = {"n_income": 100.0, "n_cashflow_act": 80.0, "total_assets": 1000.0}
        r = compute_accruals_quality_factor([_c("A", m)])
        assert r["A"] == pytest.approx(-0.02)  # -(100-80)/1000

    def test_per_share_fallback(self):
        """每股降级路径"""
        m = {"eps": 0.5, "cfps": 0.8}
        r = compute_accruals_quality_factor([_c("A", m)])
        assert r["A"] == pytest.approx(0.3)  # -(0.5-0.8)

    def test_missing_data(self):
        """无数据 → NaN"""
        r = compute_accruals_quality_factor([_c("A", {})])
        assert math.isnan(r["A"])

    def test_zero_total_assets(self):
        """总资产 0 → 降级"""
        m = {"n_income": 100.0, "n_cashflow_act": 80.0, "total_assets": 0.0}
        r = compute_accruals_quality_factor([_c("A", m)])
        # ta=0 → absolute path fails, no eps/cfps → NaN
        assert math.isnan(r["A"])


# ============================================================================
# 盈利稳定性因子
# ============================================================================


class TestEarningsStabilityFactor:
    """compute_earnings_stability_factor 测试"""

    def test_stable(self):
        """稳定盈利 → 接近 0"""
        m = {"roe_history": [10.0, 10.1, 9.9, 10.0, 10.2]}
        r = compute_earnings_stability_factor([_c("A", m)], min_periods=3)
        assert r["A"] < 0
        assert r["A"] > -0.1

    def test_volatile(self):
        """波动盈利 → 大负值"""
        m = {"roe_history": [5.0, 20.0, 2.0, 15.0]}
        r = compute_earnings_stability_factor([_c("A", m)], min_periods=3)
        assert r["A"] < -0.3

    def test_insufficient(self):
        """历史不足 → NaN"""
        m = {"roe_history": [10.0]}
        r = compute_earnings_stability_factor([_c("A", m)], min_periods=3)
        assert math.isnan(r["A"])

    def test_precomputed(self):
        """预计算 std+mean"""
        m = {"roe_std": 2.0, "roe_mean": 10.0}
        r = compute_earnings_stability_factor([_c("A", m)])
        assert r["A"] == pytest.approx(-0.2)

    def test_empty(self):
        """无数据"""
        r = compute_earnings_stability_factor([_c("A", {})])
        assert math.isnan(r["A"])


# ============================================================================
# 估值分位因子
# ============================================================================


class TestValuationPercentileFactor:
    """compute_valuation_percentile_factor 测试"""

    def test_cheap(self):
        """低百分位 → 高因子值"""
        m = {"pe_ttm": 5.0, "pe_history": list(range(10, 110, 10))}
        r = compute_valuation_percentile_factor(
            [_c("A", m)], metric="pe_ttm", min_history=5,
        )
        assert r["A"] > 0.9

    def test_expensive(self):
        """高百分位 → 低因子值"""
        m = {"pe_ttm": 200.0, "pe_history": list(range(10, 110, 10))}
        r = compute_valuation_percentile_factor(
            [_c("A", m)], metric="pe_ttm", min_history=5,
        )
        assert r["A"] < 0.1

    def test_missing_current(self):
        """缺少当前值"""
        m = {"pe_history": list(range(10, 50))}
        r = compute_valuation_percentile_factor(
            [_c("A", m)], metric="pe_ttm", min_history=3,
        )
        assert math.isnan(r["A"])

    def test_missing_history(self):
        """缺少历史"""
        m = {"pe_ttm": 20.0}
        r = compute_valuation_percentile_factor(
            [_c("A", m)], metric="pe_ttm", min_history=3,
        )
        assert math.isnan(r["A"])

    def test_insufficient_history(self):
        """历史不足"""
        m = {"pe_ttm": 20.0, "pe_history": [15.0, 25.0]}
        r = compute_valuation_percentile_factor(
            [_c("A", m)], metric="pe_ttm", min_history=5,
        )
        assert math.isnan(r["A"])

    def test_pb_metric(self):
        """PB 指标"""
        m = {"pb": 1.0, "pb_history": [0.5, 0.8, 1.2, 1.5, 2.0, 2.5, 3.0]}
        r = compute_valuation_percentile_factor(
            [_c("A", m)], metric="pb", min_history=3,
        )
        assert not math.isnan(r["A"])
        assert 0 <= r["A"] <= 1


# ============================================================================
# CGO 因子 (Capital Gains Overhang)
# ============================================================================


class TestCGOFactor:
    """compute_cgo_factor 测试。"""

    def test_basic_positive_cgo(self):
        """当前价高于 VWAP → CGO > 0。"""
        c = EquityCandidateInfo(symbol="A", close=12.0, metadata={})
        prices = [10.0] * 60
        volumes = [1000.0] * 60
        r = compute_cgo_factor({"A": prices}, {"A": volumes}, [c], lookback=60)
        # close=12, vwap=10 → CGO = 0.2
        assert abs(r["A"] - 0.2) < 1e-6

    def test_basic_negative_cgo(self):
        """当前价低于 VWAP → CGO < 0。"""
        c = EquityCandidateInfo(symbol="A", close=8.0, metadata={})
        prices = [10.0] * 60
        volumes = [1000.0] * 60
        r = compute_cgo_factor({"A": prices}, {"A": volumes}, [c], lookback=60)
        # close=8, vwap=10 → CGO = -0.2
        assert abs(r["A"] - (-0.2)) < 1e-6

    def test_insufficient_data(self):
        """数据不足 → 不出现在结果中。"""
        c = EquityCandidateInfo(symbol="A", close=10.0, metadata={})
        r = compute_cgo_factor({"A": [10.0] * 5}, {"A": [100.0] * 5}, [c], lookback=60)
        assert "A" not in r

    def test_zero_volume(self):
        """成交量全 0 → 跳过。"""
        c = EquityCandidateInfo(symbol="A", close=10.0, metadata={})
        r = compute_cgo_factor(
            {"A": [10.0] * 60}, {"A": [0.0] * 60}, [c], lookback=60,
        )
        assert "A" not in r

    def test_multiple_candidates(self):
        """多标的计算。"""
        c1 = EquityCandidateInfo(symbol="A", close=12.0, metadata={})
        c2 = EquityCandidateInfo(symbol="B", close=8.0, metadata={})
        prices = {"A": [10.0] * 60, "B": [10.0] * 60}
        volumes = {"A": [100.0] * 60, "B": [100.0] * 60}
        r = compute_cgo_factor(prices, volumes, [c1, c2], lookback=60)
        assert r["A"] > 0  # 浮盈
        assert r["B"] < 0  # 浮亏

    def test_custom_lookback(self):
        """自定义回看天数。"""
        c = EquityCandidateInfo(symbol="A", close=15.0, metadata={})
        prices = [10.0] * 30
        volumes = [100.0] * 30
        r = compute_cgo_factor({"A": prices}, {"A": volumes}, [c], lookback=20)
        assert "A" in r
        assert r["A"] > 0

    def test_volume_weighted(self):
        """确认是成交量加权（非简单平均）。"""
        c = EquityCandidateInfo(symbol="A", close=10.0, metadata={})
        # 前 5 天 price=8, vol=900; 后 5 天 price=12, vol=100
        prices = [8.0] * 5 + [12.0] * 5
        volumes = [900.0] * 5 + [100.0] * 5
        r = compute_cgo_factor({"A": prices}, {"A": volumes}, [c], lookback=10)
        # VWAP = (8*4500 + 12*500) / 5000 = 42000/5000 = 8.4
        expected_vwap = (8.0 * 4500 + 12.0 * 500) / 5000
        expected_cgo = (10.0 - expected_vwap) / expected_vwap
        assert abs(r["A"] - expected_cgo) < 1e-6


# ============================================================================
# STR 因子 (Short-Term Reversal)
# ============================================================================


class TestSTRFactor:
    """compute_str_factor 测试。"""

    def test_recent_rise_negative_str(self):
        """近期上涨 → STR < 0 (符号取反)。"""
        c = EquityCandidateInfo(symbol="A", close=12.0, metadata={})
        # 需要 lookback + 1 = 6 个数据点
        prices = [10.0, 10.5, 11.0, 11.5, 12.0, 12.0]
        r = compute_str_factor({"A": prices}, [c], lookback=5)
        # return = (12-10)/10 = 0.2, STR = -0.2
        assert r["A"] < 0

    def test_recent_drop_positive_str(self):
        """近期下跌 → STR > 0 (反转预期强)。"""
        c = EquityCandidateInfo(symbol="A", close=8.0, metadata={})
        prices = [10.0, 9.5, 9.0, 8.5, 8.0, 8.0]
        r = compute_str_factor({"A": prices}, [c], lookback=5)
        assert r["A"] > 0

    def test_insufficient_data(self):
        """数据不足 → 不出现在结果中。"""
        c = EquityCandidateInfo(symbol="A", close=10.0, metadata={})
        r = compute_str_factor({"A": [10.0, 11.0]}, [c], lookback=5)
        assert "A" not in r

    def test_flat_price(self):
        """价格不变 → STR = 0。"""
        c = EquityCandidateInfo(symbol="A", close=10.0, metadata={})
        prices = [10.0] * 10
        r = compute_str_factor({"A": prices}, [c], lookback=5)
        assert abs(r["A"]) < 1e-10

    def test_custom_lookback(self):
        """自定义回看天数。"""
        c = EquityCandidateInfo(symbol="A", close=10.0, metadata={})
        prices = [8.0] + [9.0] * 10 + [10.0]
        r = compute_str_factor({"A": prices}, [c], lookback=3)
        assert "A" in r

    def test_multiple_candidates(self):
        """多标的。"""
        c1 = EquityCandidateInfo(symbol="A", close=12.0, metadata={})
        c2 = EquityCandidateInfo(symbol="B", close=8.0, metadata={})
        prices_a = [10.0, 10.5, 11.0, 11.5, 12.0, 12.0]
        prices_b = [10.0, 9.5, 9.0, 8.5, 8.0, 8.0]
        r = compute_str_factor({"A": prices_a, "B": prices_b}, [c1, c2], lookback=5)
        assert r["A"] < r["B"]  # B 跌更多 → STR 更高


# ============================================================================
# PEG 因子测试
# ============================================================================


class TestPEGFactor:
    """compute_peg_factor 单元测试。"""

    def test_normal_case(self):
        """正常 PEG: PE=20, Growth=20% → PEG=1 → 返回 -1。"""
        c = _c("A", {"pe_ttm": 20, "dt_netprofit_yoy": 20})
        r = compute_peg_factor([c])
        assert "A" in r
        assert abs(r["A"] - (-1.0)) < 1e-6

    def test_low_peg_higher_score(self):
        """低 PEG 应得分更高（返回值更大/更接近 0）。"""
        c1 = _c("A", {"pe_ttm": 10, "dt_netprofit_yoy": 20})  # PEG=0.5
        c2 = _c("B", {"pe_ttm": 40, "dt_netprofit_yoy": 10})  # PEG=4
        r = compute_peg_factor([c1, c2])
        assert r["A"] > r["B"]

    def test_negative_pe_excluded(self):
        """负 PE 应被排除。"""
        c = _c("A", {"pe_ttm": -5, "dt_netprofit_yoy": 10})
        r = compute_peg_factor([c])
        assert "A" not in r

    def test_negative_growth_excluded(self):
        """负增速应被排除（PEG 无意义）。"""
        c = _c("A", {"pe_ttm": 20, "dt_netprofit_yoy": -5})
        r = compute_peg_factor([c])
        assert "A" not in r

    def test_missing_fields(self):
        """缺少字段应跳过。"""
        c = _c("A", {"pe_ttm": 20})
        r = compute_peg_factor([c])
        assert "A" not in r

    def test_max_peg_cap(self):
        """PEG 应被截断在 max_peg。"""
        c = _c("A", {"pe_ttm": 100, "dt_netprofit_yoy": 2})  # PEG=50
        r = compute_peg_factor([c], max_peg=5.0)
        assert abs(r["A"] - (-5.0)) < 1e-6


# ============================================================================
# FCFY 因子测试
# ============================================================================


class TestFCFYFactor:
    """compute_fcfy_factor 单元测试。"""

    def test_direct_fcf(self):
        """直接使用 fcff 字段。"""
        c = _c("A", {"fcff": 1e8}, circ_mv=100000)  # FCF=1亿, MV=10亿
        r = compute_fcfy_factor([c])
        assert "A" in r
        assert abs(r["A"] - 0.10) < 1e-4

    def test_fallback_to_ocf(self):
        """fcff 缺失时降级使用经营现金流。"""
        c = _c("A", {"n_cashflow_act": 5e7}, circ_mv=100000)
        r = compute_fcfy_factor([c])
        assert "A" in r
        assert r["A"] > 0

    def test_fallback_to_cfps(self):
        """经营现金流也缺失时用 cfps*total_share。"""
        c = _c("A", {"cfps": 2.0, "total_share": 1e4}, circ_mv=100000)
        r = compute_fcfy_factor([c])
        assert "A" in r

    def test_zero_mv_excluded(self):
        """零市值应排除。"""
        c = _c("A", {"fcff": 1e8}, circ_mv=0)
        r = compute_fcfy_factor([c])
        assert "A" not in r

    def test_higher_fcfy_better(self):
        """高 FCFY 应得分更高。"""
        c1 = _c("A", {"fcff": 2e8}, circ_mv=100000)  # FCFY=0.20
        c2 = _c("B", {"fcff": 5e7}, circ_mv=100000)  # FCFY=0.05
        r = compute_fcfy_factor([c1, c2])
        assert r["A"] > r["B"]


# ============================================================================
# 分红持续性因子测试
# ============================================================================


class TestDividendPersistenceFactor:
    """compute_dividend_persistence_factor 单元测试。"""

    def test_consecutive_dividends(self):
        """连续 5 年分红。"""
        c = _c("A", {"dv_history": [2.0, 2.5, 3.0, 2.8, 3.2]})
        r = compute_dividend_persistence_factor([c])
        assert r["A"] == 5.0

    def test_broken_streak(self):
        """中间断裂: 只计最近连续部分。"""
        c = _c("A", {"dv_history": [2.0, 0.0, 3.0, 2.8, 3.2]})
        r = compute_dividend_persistence_factor([c])
        assert r["A"] == 3.0  # 最后 3 年连续

    def test_no_dividend(self):
        """全部不分红。"""
        c = _c("A", {"dv_history": [0.0, 0.0, 0.0]})
        r = compute_dividend_persistence_factor([c])
        assert r["A"] == 0.0

    def test_fallback_to_dividend_years(self):
        """降级到 dividend_years 字段。"""
        c = _c("A", {"dividend_years": 7})
        r = compute_dividend_persistence_factor([c])
        assert r["A"] == 7.0

    def test_missing_all_data(self):
        """数据完全缺失。"""
        c = _c("A", {})
        r = compute_dividend_persistence_factor([c])
        assert "A" not in r

    def test_longer_history_better(self):
        """分红年数越多分数越高。"""
        c1 = _c("A", {"dv_history": [1.0, 1.0, 1.0, 1.0, 1.0]})
        c2 = _c("B", {"dv_history": [1.0, 1.0]})
        r = compute_dividend_persistence_factor([c1, c2])
        assert r["A"] > r["B"]


# ============================================================================
# 异常换手因子 (ABT)
# ============================================================================


class TestAbnormalTurnoverFactor:

    def _vols(self, short_avg: float, long_avg: float) -> list[float]:
        """构造 volume 历史：前230天=long_avg, 后20天=short_avg。"""
        return [long_avg] * 230 + [short_avg] * 20

    def test_normal_abt(self):
        """短期放量 → ABT>1 → 分数为负。"""
        vols = self._vols(200.0, 100.0)
        c = [_c("A")]
        r = compute_abnormal_turnover_factor({"A": vols}, c)
        assert r["A"] < 0  # 取负

    def test_low_abt_better(self):
        """低 ABT（未放量）应得更高分。"""
        vols_high = self._vols(300.0, 100.0)
        vols_low = self._vols(80.0, 100.0)
        c = [_c("A"), _c("B")]
        r = compute_abnormal_turnover_factor(
            {"A": vols_high, "B": vols_low}, c
        )
        assert r["B"] > r["A"]

    def test_insufficient_data(self):
        """数据不足返回nan。"""
        c = [_c("A")]
        r = compute_abnormal_turnover_factor({"A": [100.0] * 10}, c)
        assert math.isnan(r["A"])

    def test_zero_long_avg(self):
        """长期均量为零 → nan。"""
        vols = [0.0] * 250
        c = [_c("A")]
        r = compute_abnormal_turnover_factor({"A": vols}, c)
        assert math.isnan(r["A"])

    def test_missing_symbol(self):
        """volume_history 中无此标的。"""
        c = [_c("A")]
        r = compute_abnormal_turnover_factor({}, c)
        assert math.isnan(r["A"])


# ============================================================================
# 最大日收益因子 (MAX)
# ============================================================================


class TestMaxDailyReturnFactor:

    def test_normal_max(self):
        """正常计算 MAX = 取负。"""
        # 21 个价格 → 20 日收益
        prices = [10.0] * 20 + [11.0]  # 最后一天涨 10%
        c = [_c("A")]
        r = compute_max_daily_return_factor({"A": prices}, c)
        assert r["A"] < 0  # 取负
        assert abs(r["A"] - (-0.1)) < 0.01

    def test_lower_max_better(self):
        """低 MAX 标的（理性定价）得分更高。"""
        # A: 有一天暴涨 20%
        pa = [10.0] * 20 + [12.0]
        # B: 平稳，最大涨 2%
        pb = [10.0 + 0.01 * i for i in range(21)]
        c = [_c("A"), _c("B")]
        r = compute_max_daily_return_factor({"A": pa, "B": pb}, c)
        assert r["B"] > r["A"]

    def test_insufficient_data(self):
        """数据不足返回nan。"""
        c = [_c("A")]
        r = compute_max_daily_return_factor({"A": [10.0, 11.0]}, c)
        assert math.isnan(r["A"])

    def test_all_declining(self):
        """全部下跌时 MAX 为负 → 取负后为正。"""
        prices = [10.0 - 0.1 * i for i in range(22)]
        c = [_c("A")]
        r = compute_max_daily_return_factor({"A": prices}, c)
        assert r["A"] > 0  # max(returns) < 0, 取负后 > 0


# ============================================================================
# 过度反应组合因子 (Overreaction Composite)
# ============================================================================


class TestOverreactionComposite:

    def test_equal_weight_fusion(self):
        """等权融合多个子因子。"""
        subfactors = {
            "bm": {"A": 0.5, "B": 0.8, "C": 0.3},
            "str": {"A": 0.7, "B": 0.2, "C": 0.9},
        }
        r = compute_overreaction_composite(subfactors)
        assert len(r) == 3
        # A: bm 排第2, str 排第2
        # B: bm 排第1, str 排第3
        # C: bm 排第3, str 排第1
        # 等权融合后分数应接近
        assert all(v > 0 for v in r.values())

    def test_custom_weights(self):
        """自定义权重。"""
        subfactors = {
            "f1": {"A": 10.0, "B": 1.0},
            "f2": {"A": 1.0, "B": 10.0},
        }
        # f1 权重 90%
        r = compute_overreaction_composite(
            subfactors, weights={"f1": 0.9, "f2": 0.1}
        )
        assert r["A"] > r["B"]

    def test_empty_input(self):
        """空输入返回空。"""
        assert compute_overreaction_composite({}) == {}

    def test_nan_handling(self):
        """NaN 值被排到最后。"""
        subfactors = {
            "f": {"A": float("nan"), "B": 5.0, "C": 3.0},
        }
        r = compute_overreaction_composite(subfactors)
        assert r["B"] >= r["A"]

    def test_single_factor(self):
        """单因子退化为直接排序。"""
        subfactors = {"f": {"X": 100, "Y": 50, "Z": 75}}
        r = compute_overreaction_composite(subfactors)
        assert r["X"] > r["Z"] > r["Y"]

