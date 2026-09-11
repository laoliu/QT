"""Tests for cb_factors — 可转债 8 个正交因子 (5 截面 + 3 时序)。

测试覆盖（≥10 用例 per module）：
- compute_iv_bias: 正常值、边界、None
- compute_gamma_convexity: ATM 最大、边界、数据不足
- compute_credit_spread: 折价/溢价/边界
- compute_liquidity_activity: 正常、零值、None
- compute_maturity_convexity: sweet_spot、边界、过期
- compute_delta_accel: 加速/减速/持平/数据不足
- compute_credit_spread_mom: 收窄/扩大/数据不足
- compute_liquidity_shock: 放量/缩量/零值/数据不足
- compute_all_factors: 综合计算
- CBFactorValues: 数据结构
- FACTOR_META: 元信息完整性
"""

from __future__ import annotations

from datetime import datetime

import pytest

from aipha.factors.cb_factors import (
    CBFactorValues,
    DEFAULT_RISK_FREE_RATE,
    FACTOR_META,
    compute_all_factors,
    compute_batch_factors,
    compute_credit_spread,
    compute_credit_spread_mom,
    compute_delta_accel,
    compute_gamma_convexity,
    compute_iv_bias,
    compute_liquidity_activity,
    compute_liquidity_shock,
    compute_maturity_convexity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 15, 15, 0)
MAT_3Y = "20270615"   # 3 年后到期
MAT_1Y = "20250615"   # 1 年后到期
MAT_EXPIRED = "20240101"  # 已到期


# ---------------------------------------------------------------------------
# 1. compute_iv_bias
# ---------------------------------------------------------------------------

class TestComputeIvBias:
    """隐含波动率偏差因子 (7 用例)。"""

    def test_basic(self) -> None:
        """close=110, cb_value=95, 有时间价值。"""
        result = compute_iv_bias(110.0, 95.0, MAT_3Y, NOW)
        assert result > 0.0  # time_value = max(0, 110-max(100,95)) = 10

    def test_deep_discount(self) -> None:
        """close=90 < par=100 → time_value=0 → iv_bias=0。"""
        result = compute_iv_bias(90.0, 80.0, MAT_3Y, NOW)
        assert result == pytest.approx(0.0)

    def test_near_maturity(self) -> None:
        """接近到期 短时间 → sqrt(years)小 → iv_bias 偏大。"""
        result = compute_iv_bias(110.0, 95.0, MAT_1Y, NOW)
        basic = compute_iv_bias(110.0, 95.0, MAT_3Y, NOW)
        assert result > basic  # 同等 tv, 短期 iv_bias 更大

    def test_expired(self) -> None:
        """已到期 → 返回 0.5 (中性)。"""
        assert compute_iv_bias(110.0, 95.0, MAT_EXPIRED, NOW) == 0.5

    def test_none_maturity(self) -> None:
        """maturity_date=None → 0.5。"""
        assert compute_iv_bias(110.0, 95.0, None, NOW) == 0.5

    def test_zero_close(self) -> None:
        """close=0 → 0.5。"""
        assert compute_iv_bias(0.0, 95.0, MAT_3Y, NOW) == 0.5

    def test_no_time_value(self) -> None:
        """close=100=par, cb_value=120>par → intrinsic=120>close → tv=0。"""
        result = compute_iv_bias(100.0, 120.0, MAT_3Y, NOW)
        assert result == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2. compute_gamma_convexity
# ---------------------------------------------------------------------------

class TestComputeGammaConvexity:
    """Gamma 凸性因子 (5 用例)。"""

    def test_atm_highest(self) -> None:
        """cb_value=50 (delta=0.5, ATM) → gamma 最大。"""
        atm = compute_gamma_convexity(50.0, MAT_3Y, NOW)  # delta=0.5
        otm = compute_gamma_convexity(10.0, MAT_3Y, NOW)  # delta=0.1
        itm = compute_gamma_convexity(95.0, MAT_3Y, NOW)  # delta=0.95
        # ATM gamma_proxy = 4*0.5*0.5 = 1.0 (最大)
        assert atm > otm
        assert atm > itm

    def test_deep_otm(self) -> None:
        """cb_value=10 → delta≈0.1 → gamma 较小。"""
        result = compute_gamma_convexity(10.0, MAT_3Y, NOW)
        assert result > 0
        assert result < 1.0

    def test_none_cb_value(self) -> None:
        """cb_value=None → 0.0。"""
        assert compute_gamma_convexity(None, MAT_3Y, NOW) == 0.0

    def test_near_maturity(self) -> None:
        """短期到期 → gamma_convexity 较低 (sqrt(years)小)。"""
        short = compute_gamma_convexity(50.0, MAT_1Y, NOW)  # ATM, 1Y
        long_ = compute_gamma_convexity(50.0, MAT_3Y, NOW)  # ATM, 3Y
        assert long_ > short

    def test_long_maturity(self) -> None:
        """6 年到期 ATM → sqrt(6) 放大。"""
        result = compute_gamma_convexity(50.0, "20300615", NOW)  # delta=0.5
        assert result > 0


# ---------------------------------------------------------------------------
# 3. compute_credit_spread
# ---------------------------------------------------------------------------

class TestComputeCreditSpread:
    """信用利差因子 (5 用例)。"""

    def test_discount_bond(self) -> None:
        """close=90 < par=100, 3Y → 正利差。"""
        spread = compute_credit_spread(90.0, MAT_3Y, NOW)
        assert spread > 0  # ytm > rf

    def test_premium_bond(self) -> None:
        """close=120 > par → 负 ytm → 负利差。"""
        spread = compute_credit_spread(120.0, MAT_3Y, NOW)
        assert spread < 0

    def test_expired(self) -> None:
        """已到期 → 0.0。"""
        assert compute_credit_spread(90.0, MAT_EXPIRED, NOW) == 0.0

    def test_extreme_price(self) -> None:
        """close>=200 → 0.0。"""
        assert compute_credit_spread(200.0, MAT_3Y, NOW) == 0.0
        assert compute_credit_spread(0.0, MAT_3Y, NOW) == 0.0

    def test_near_maturity(self) -> None:
        """1 年到期折价 → 利差更大 (年化更高)。"""
        spread_1y = compute_credit_spread(90.0, MAT_1Y, NOW)
        spread_3y = compute_credit_spread(90.0, MAT_3Y, NOW)
        assert spread_1y > spread_3y  # 同价格短期年化收益更高


# ---------------------------------------------------------------------------
# Remaining test classes (stubs for step 2)
# ---------------------------------------------------------------------------

class TestComputeLiquidityActivity:
    """流动性活跃度因子 (4 用例)。"""

    def test_normal(self) -> None:
        """正常计算: amount/issue_size。"""
        assert compute_liquidity_activity(100.0, 5000.0) == pytest.approx(0.02)

    def test_zero_amount(self) -> None:
        """amount=0 → 0.0。"""
        assert compute_liquidity_activity(0.0, 5000.0) == 0.0

    def test_none_values(self) -> None:
        """None 输入 → 0.0。"""
        assert compute_liquidity_activity(None, 5000.0) == 0.0
        assert compute_liquidity_activity(100.0, None) == 0.0
        assert compute_liquidity_activity(None, None) == 0.0

    def test_high_activity(self) -> None:
        """高活跃度 (amount > issue_size)。"""
        result = compute_liquidity_activity(10000.0, 5000.0)
        assert result == pytest.approx(2.0)


class TestComputeMaturityConvexity:
    """到期凸性因子 (5 用例)。"""

    def test_sweet_spot(self) -> None:
        """3年到期 = sweet_spot → score=1.0。"""
        result = compute_maturity_convexity(MAT_3Y, NOW)
        assert result == pytest.approx(1.0, abs=0.05)

    def test_boundary(self) -> None:
        """6年到期 (sweet_spot+range) → score≈0.0。"""
        result = compute_maturity_convexity("20300615", NOW)
        assert result == pytest.approx(0.0, abs=0.05)

    def test_expired(self) -> None:
        """已到期 → 0.0。"""
        assert compute_maturity_convexity(MAT_EXPIRED, NOW) == 0.0

    def test_very_long(self) -> None:
        """10年到期 → 超出范围 → 0.0。"""
        assert compute_maturity_convexity("20340615", NOW) == 0.0

    def test_custom_sweet_spot(self) -> None:
        """自定义 sweet_spot=2, half_range=2。"""
        # 1年到期, sweet=2, range=2 → distance=1 → score=0.5
        result = compute_maturity_convexity(
            MAT_1Y, NOW, sweet_spot=2.0, half_range=2.0,
        )
        assert result == pytest.approx(0.5, abs=0.1)


# ---------------------------------------------------------------------------
# 6. FACTOR_META 元信息
# ---------------------------------------------------------------------------

class TestFactorMeta:
    """因子元信息完整性 (3 用例)。"""

    def test_factor_meta_covers_all_groups(self) -> None:
        """8 个基础因子 (5 截面 + 3 时序) + 4 个 BSM IV 因子。

        原用例名叫 test_all_eight_factors 并断言恰好 8 个，
        Phase 3 / C4 接入 BSM IV 因子后就一直红着。这里按分组断言，
        既跟上新增，也仍然挡住"顺手塞一个没登记的因子"。
        """
        base = {
            "iv_bias", "gamma_convexity", "credit_spread",
            "liquidity_activity", "maturity_convexity",
            "delta_accel", "credit_spread_mom", "liquidity_shock",
        }
        bsm_iv = {"iv", "iv_prem", "ivpr", "pricing_bias"}
        assert set(FACTOR_META.keys()) == base | bsm_iv

    def test_meta_keys(self) -> None:
        """每个 meta 有 name_cn/direction/description/economic_logic。"""
        for name, meta in FACTOR_META.items():
            assert "name_cn" in meta, f"{name} missing name_cn"
            assert "direction" in meta, f"{name} missing direction"
            assert meta["direction"] in ("ascending", "descending")
            assert "economic_logic" in meta, f"{name} missing economic_logic"

    def test_time_series_factors_marked(self) -> None:
        """时序因子标有 requires_history=True。"""
        ts_factors = {"delta_accel", "credit_spread_mom", "liquidity_shock"}
        for name in ts_factors:
            assert FACTOR_META[name].get("requires_history") is True, (
                f"{name} should have requires_history=True"
            )
        # 截面因子不应有 requires_history
        cs_factors = {"iv_bias", "gamma_convexity", "credit_spread",
                      "liquidity_activity", "maturity_convexity"}
        for name in cs_factors:
            assert not FACTOR_META[name].get("requires_history"), (
                f"{name} should not have requires_history"
            )


# ---------------------------------------------------------------------------
# 7. compute_delta_accel (Delta 加速度)
# ---------------------------------------------------------------------------

class TestComputeDeltaAccel:
    """Delta 加速度因子 (6 用例)。"""

    def test_positive_accel(self) -> None:
        """delta 上升 → 正加速 (正股趋势加速)。"""
        hist = [0.8, 0.85, 0.92]
        result = compute_delta_accel(hist)
        assert result == pytest.approx(0.07)

    def test_negative_accel(self) -> None:
        """delta 下降 → 负加速 (正股趋势减弱)。"""
        hist = [0.92, 0.85, 0.80]
        result = compute_delta_accel(hist)
        assert result == pytest.approx(-0.05)

    def test_flat(self) -> None:
        """delta 不变 → 加速 = 0。"""
        hist = [0.5, 0.5]
        result = compute_delta_accel(hist)
        assert result == pytest.approx(0.0)

    def test_min_length(self) -> None:
        """长度 < 2 → 返回 0.0。"""
        assert compute_delta_accel([]) == 0.0
        assert compute_delta_accel([0.5]) == 0.0

    def test_exact_min_length(self) -> None:
        """恰好 2 个数据点也可计算。"""
        result = compute_delta_accel([0.3, 0.5])
        assert result == pytest.approx(0.2)

    def test_only_uses_last_two(self) -> None:
        """长序列只使用最后两个值。"""
        hist = [0.1, 0.2, 0.3, 0.4, 0.9]
        result = compute_delta_accel(hist)
        assert result == pytest.approx(0.5)  # 0.9 - 0.4


# ---------------------------------------------------------------------------
# 8. compute_credit_spread_mom (信用利差动量)
# ---------------------------------------------------------------------------

class TestComputeCreditSpreadMom:
    """信用利差动量因子 (6 用例)。"""

    def test_spread_narrowing(self) -> None:
        """利差收窄 → 负值 (买入信号)。"""
        # 21 个数据点: [0] = 0.05, [-1] = 0.03, lookback=20
        hist = [0.05] + [0.04] * 19 + [0.03]
        result = compute_credit_spread_mom(hist, lookback=20)
        assert result < 0  # 0.03 - 0.05 = -0.02
        assert result == pytest.approx(-0.02)

    def test_spread_widening(self) -> None:
        """利差扩大 → 正值。"""
        hist = [0.02] + [0.03] * 19 + [0.05]
        result = compute_credit_spread_mom(hist, lookback=20)
        assert result > 0  # 0.05 - 0.02 = 0.03
        assert result == pytest.approx(0.03)

    def test_spread_flat(self) -> None:
        """利差不变 → 0。"""
        hist = [0.03] * 21
        result = compute_credit_spread_mom(hist, lookback=20)
        assert result == pytest.approx(0.0)

    def test_insufficient_data(self) -> None:
        """数据不足 → 0.0。"""
        assert compute_credit_spread_mom([0.03] * 20, lookback=20) == 0.0
        assert compute_credit_spread_mom([], lookback=20) == 0.0

    def test_custom_lookback(self) -> None:
        """自定义回看期 lookback=5。"""
        hist = [0.06, 0.05, 0.04, 0.03, 0.02, 0.01]
        result = compute_credit_spread_mom(hist, lookback=5)
        assert result == pytest.approx(-0.05)  # 0.01 - 0.06

    def test_longer_history(self) -> None:
        """长历史序列: 只用 [-1] 和 [-lookback-1]。"""
        # 30 个点, lookback=20 → 用 [-1] 和 [-21]
        hist = list(range(30))  # [0, 1, ..., 29]
        result = compute_credit_spread_mom(hist, lookback=20)
        assert result == pytest.approx(20.0)  # 29 - 9


# ---------------------------------------------------------------------------
# 9. compute_liquidity_shock (流动性突变)
# ---------------------------------------------------------------------------

class TestComputeLiquidityShock:
    """流动性突变因子 (7 用例)。"""

    def test_volume_surge(self) -> None:
        """成交额翻倍 → shock ≈ 2.0。"""
        # 20 天均量 100, 今天 200
        hist = [100.0] * 20 + [200.0]
        result = compute_liquidity_shock(hist, lookback=20)
        assert result == pytest.approx(2.0)

    def test_normal_volume(self) -> None:
        """成交额与均量持平 → shock ≈ 1.0。"""
        hist = [100.0] * 21
        result = compute_liquidity_shock(hist, lookback=20)
        assert result == pytest.approx(1.0)

    def test_volume_shrink(self) -> None:
        """成交额萎缩 → shock < 1.0。"""
        hist = [100.0] * 20 + [50.0]
        result = compute_liquidity_shock(hist, lookback=20)
        assert result == pytest.approx(0.5)

    def test_extreme_surge(self) -> None:
        """异常放量 10x。"""
        hist = [100.0] * 20 + [1000.0]
        result = compute_liquidity_shock(hist, lookback=20)
        assert result == pytest.approx(10.0)

    def test_insufficient_data(self) -> None:
        """数据不足 → 1.0 (中性)。"""
        assert compute_liquidity_shock([100.0] * 20, lookback=20) == 1.0
        assert compute_liquidity_shock([], lookback=20) == 1.0

    def test_zero_ma(self) -> None:
        """历史均量为 0 → 返回 1.0 (中性)。"""
        hist = [0.0] * 20 + [100.0]
        result = compute_liquidity_shock(hist, lookback=20)
        assert result == 1.0

    def test_custom_lookback(self) -> None:
        """自定义 lookback=5。"""
        hist = [50.0] * 5 + [150.0]
        result = compute_liquidity_shock(hist, lookback=5)
        assert result == pytest.approx(3.0)  # 150 / 50
