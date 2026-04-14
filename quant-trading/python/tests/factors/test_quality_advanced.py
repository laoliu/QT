"""高阶质量因子测试"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.quality_advanced import (
    AccrualsQualityFactor,
    DebtQualityFactor,
    EarningsStabilityFactor,
    PiotroskiFScoreFactor,
)


# ============================================================================
# PiotroskiFScoreFactor Tests
# ============================================================================


class TestPiotroskiFScoreFactor:
    """Piotroski F-Score 因子测试"""

    def _full_metadata(self) -> dict:
        """全 9 维度通过的 metadata"""
        return {
            "roa": 5.0,
            "roa_prev": 4.0,  # ↑ improving
            "cfps": 1.0,  # > 0
            "eps": 0.8,  # cfps > eps → accruals quality
            "debt_to_assets": 40.0,
            "debt_to_assets_prev": 45.0,  # ↓ leverage down
            "current_ratio": 2.0,
            "current_ratio_prev": 1.5,  # ↑ improving
            "total_share": 1000.0,
            "total_share_prev": 1000.0,  # no dilution
            "grossprofit_margin": 30.0,
            "grossprofit_margin_prev": 28.0,  # ↑ improving
            "asset_turnover": 0.8,
            "asset_turnover_prev": 0.7,  # ↑ improving
        }

    def test_name(self):
        """因子名称"""
        f = PiotroskiFScoreFactor()
        assert f.name == "PIOTROSKI_F"

    def test_perfect_score(self):
        """满分 9 分"""
        f = PiotroskiFScoreFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=self._full_metadata(),
        )
        r = f.compute(data)
        assert r.value == pytest.approx(9.0)
        assert r.metadata["computable_dimensions"] == 9

    def test_zero_score(self):
        """全部不通过 → 0 分"""
        f = PiotroskiFScoreFactor()
        m = {
            "roa": -2.0,  # F1 fail
            "roa_prev": 0.0,  # F3 fail (roa < roa_prev)
            "cfps": -0.5,  # F2 fail
            "eps": 0.3,  # F4 fail (cfps < eps)
            "debt_to_assets": 60.0,
            "debt_to_assets_prev": 50.0,  # F5 fail (leverage up)
            "current_ratio": 1.0,
            "current_ratio_prev": 1.5,  # F6 fail (ratio down)
            "total_share": 1100.0,
            "total_share_prev": 1000.0,  # F7 fail (dilution)
            "grossprofit_margin": 25.0,
            "grossprofit_margin_prev": 28.0,  # F8 fail
            "asset_turnover": 0.6,
            "asset_turnover_prev": 0.7,  # F9 fail
        }
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=m,
        )
        r = f.compute(data)
        assert r.value == pytest.approx(0.0)

    def test_partial_data(self):
        """部分数据可用（≥5 维度）"""
        f = PiotroskiFScoreFactor()
        m = {
            "roa": 5.0,       # F1 ✓
            "cfps": 1.0,      # F2 ✓
            "eps": 0.8,       # F4 ✓ (cfps>eps)
            "debt_to_assets": 40.0,
            "debt_to_assets_prev": 45.0,  # F5 ✓
            "current_ratio": 2.0,
            "current_ratio_prev": 1.5,    # F6 ✓
        }
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=m,
        )
        r = f.compute(data)
        assert not math.isnan(r.value)
        assert r.value == pytest.approx(5.0)  # 5 out of 5 computable

    def test_insufficient_data(self):
        """数据不足（< 5 维度）"""
        f = PiotroskiFScoreFactor()
        m = {
            "roa": 5.0,   # F1 only
            "cfps": 1.0,  # F2
        }
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=m,
        )
        r = f.compute(data)
        assert math.isnan(r.value)
        assert "不足最低 5 个" in r.metadata.get("reason", "")

    def test_min_score_filter(self):
        """最低分过滤"""
        f = PiotroskiFScoreFactor(min_score=7)
        # Use full metadata → score 9 → passes
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=self._full_metadata(),
        )
        r = f.compute(data)
        assert r.value == pytest.approx(9.0)

    def test_min_score_reject(self):
        """低于最低分过滤"""
        f = PiotroskiFScoreFactor(min_score=7)
        m = {
            "roa": 5.0,
            "roa_prev": 6.0,  # F3 fail
            "cfps": 1.0,
            "eps": 0.8,
            "debt_to_assets": 50.0,
            "debt_to_assets_prev": 45.0,  # F5 fail
            "current_ratio": 1.0,
            "current_ratio_prev": 1.5,  # F6 fail
            "total_share": 1000.0,
            "total_share_prev": 1000.0,
            "grossprofit_margin": 25.0,
            "grossprofit_margin_prev": 28.0,  # F8 fail
            "asset_turnover": 0.6,
            "asset_turnover_prev": 0.7,  # F9 fail
        }
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=m,
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_details_in_metadata(self):
        """检查详情记录"""
        f = PiotroskiFScoreFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=self._full_metadata(),
        )
        r = f.compute(data)
        details = r.metadata["details"]
        assert details["f1_roa_positive"] == 1
        assert details["f4_cfo_gt_ni"] == 1
        assert details["f7_no_dilution"] == 1

    def test_empty_metadata(self):
        """空 metadata"""
        f = PiotroskiFScoreFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_gross_margin_alias(self):
        """gross_margin 别名支持"""
        f = PiotroskiFScoreFactor()
        m = self._full_metadata()
        del m["grossprofit_margin"]
        del m["grossprofit_margin_prev"]
        m["gross_margin"] = 30.0
        m["gross_margin_prev"] = 28.0
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata=m,
        )
        r = f.compute(data)
        assert r.value == pytest.approx(9.0)

    def test_repr(self):
        """repr 输出"""
        f = PiotroskiFScoreFactor()
        assert "PiotroskiFScoreFactor" in repr(f)


# ============================================================================
# AccrualsQualityFactor Tests
# ============================================================================


class TestAccrualsQualityFactor:
    """应计盈余质量因子测试"""

    def test_name(self):
        """因子名称"""
        f = AccrualsQualityFactor()
        assert f.name == "ACCRUALS_QUALITY"

    def test_absolute_path(self):
        """绝对值口径计算"""
        f = AccrualsQualityFactor()
        # Accruals = (NI - CFO) / TA = (100 - 80) / 1000 = 0.02
        # Factor value = -0.02 (lower accruals = higher quality)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "n_income": 100.0,
                "n_cashflow_act": 80.0,
                "total_assets": 1000.0,
            },
        )
        r = f.compute(data)
        assert r.value == pytest.approx(-0.02)
        assert r.metadata["method"] == "absolute"

    def test_per_share_fallback(self):
        """每股数据降级路径"""
        f = AccrualsQualityFactor(use_per_share=True)
        # Accruals_ps = eps - cfps = 0.5 - 0.8 = -0.3
        # Factor value = -(-0.3) = 0.3
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"eps": 0.5, "cfps": 0.8},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(0.3)
        assert r.metadata["method"] == "per_share"

    def test_high_accruals(self):
        """高应计（低质量）→ 负因子值"""
        f = AccrualsQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "n_income": 200.0,
                "n_cashflow_act": 50.0,
                "total_assets": 1000.0,
            },
        )
        r = f.compute(data)
        # Accruals = 0.15, value = -0.15
        assert r.value < 0

    def test_negative_accruals(self):
        """负应计（高质量）→ 正因子值"""
        f = AccrualsQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "n_income": 50.0,
                "n_cashflow_act": 120.0,
                "total_assets": 1000.0,
            },
        )
        r = f.compute(data)
        assert r.value > 0

    def test_missing_all_data(self):
        """缺少所有数据"""
        f = AccrualsQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_zero_total_assets(self):
        """总资产为 0"""
        f = AccrualsQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "n_income": 100.0,
                "n_cashflow_act": 80.0,
                "total_assets": 0.0,
            },
        )
        r = f.compute(data)
        # total_assets=0 → fallback to per_share if available
        assert math.isnan(r.value)

    def test_per_share_disabled(self):
        """禁用每股降级"""
        f = AccrualsQualityFactor(use_per_share=False)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"eps": 0.5, "cfps": 0.8},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_n_income_attr_p_alias(self):
        """n_income_attr_p 别名"""
        f = AccrualsQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "n_income_attr_p": 100.0,
                "n_cashflow_act": 80.0,
                "total_assets": 1000.0,
            },
        )
        r = f.compute(data)
        assert r.value == pytest.approx(-0.02)


# ============================================================================
# DebtQualityFactor Tests
# ============================================================================


class TestDebtQualityFactor:
    """负债质量因子测试"""

    def test_name(self):
        """因子名称"""
        f = DebtQualityFactor()
        assert f.name == "DEBT_QUALITY"

    def test_full_data(self):
        """三维度数据齐全"""
        f = DebtQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "current_ratio": 2.0,
                "quick_ratio": 1.5,
                "debt_to_assets": 40.0,
            },
        )
        r = f.compute(data)
        assert not math.isnan(r.value)
        assert r.value > 0

    def test_low_debt_high_quality(self):
        """低负债 = 高质量"""
        f = DebtQualityFactor()
        d_low = FactorData(
            symbol="A.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "current_ratio": 3.0,
                "quick_ratio": 2.5,
                "debt_to_assets": 20.0,
            },
        )
        d_high = FactorData(
            symbol="B.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "current_ratio": 0.8,
                "quick_ratio": 0.5,
                "debt_to_assets": 80.0,
            },
        )
        r_low = f.compute(d_low)
        r_high = f.compute(d_high)
        assert r_low.value > r_high.value

    def test_partial_data(self):
        """部分指标缺失"""
        f = DebtQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"current_ratio": 2.0},
        )
        r = f.compute(data)
        assert not math.isnan(r.value)

    def test_no_data(self):
        """无数据"""
        f = DebtQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_extreme_current_ratio(self):
        """极端流动比率（capped at 10）"""
        f = DebtQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"current_ratio": 50.0, "quick_ratio": 40.0},
        )
        r = f.compute(data)
        assert not math.isnan(r.value)
        # Should be capped, so value shouldn't be astronomical
        assert r.value <= 20.0

    def test_custom_weights(self):
        """自定义权重"""
        f = DebtQualityFactor(w_current=0.5, w_quick=0.3, w_debt=0.2)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "current_ratio": 2.0,
                "quick_ratio": 1.5,
                "debt_to_assets": 40.0,
            },
        )
        r = f.compute(data)
        assert not math.isnan(r.value)

    def test_debt_to_assets_out_of_range(self):
        """资产负债率超出 0-100 范围"""
        f = DebtQualityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "current_ratio": 2.0,
                "debt_to_assets": 150.0,  # out of 0-100
            },
        )
        r = f.compute(data)
        # debt_to_assets > 100 is ignored, only current_ratio counts
        assert not math.isnan(r.value)


# ============================================================================
# EarningsStabilityFactor Tests
# ============================================================================


class TestEarningsStabilityFactor:
    """盈利稳定性因子测试"""

    def test_name_roe(self):
        """ROE 名称"""
        f = EarningsStabilityFactor(metric="roe")
        assert f.name == "EARNINGS_STABILITY_ROE"

    def test_name_roa(self):
        """ROA 名称"""
        f = EarningsStabilityFactor(metric="roa")
        assert f.name == "EARNINGS_STABILITY_ROA"

    def test_stable_earnings(self):
        """稳定盈利 → 高因子值"""
        f = EarningsStabilityFactor(min_periods=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [10.0, 10.1, 9.9, 10.0, 10.2]},
        )
        r = f.compute(data)
        assert r.value < 0  # -CV, but close to 0
        assert r.value > -0.1  # Very stable

    def test_volatile_earnings(self):
        """波动盈利 → 低因子值"""
        f = EarningsStabilityFactor(min_periods=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [5.0, 20.0, 2.0, 15.0, 8.0]},
        )
        r = f.compute(data)
        assert r.value < -0.3  # High CV → very negative

    def test_stable_beats_volatile(self):
        """稳定 > 波动"""
        f = EarningsStabilityFactor(min_periods=3)
        d_stable = FactorData(
            symbol="A.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [10.0, 10.1, 9.9, 10.0]},
        )
        d_volatile = FactorData(
            symbol="B.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [5.0, 20.0, 2.0, 15.0]},
        )
        r_stable = f.compute(d_stable)
        r_volatile = f.compute(d_volatile)
        assert r_stable.value > r_volatile.value

    def test_insufficient_periods(self):
        """历史期数不足"""
        f = EarningsStabilityFactor(min_periods=5)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [10.0, 11.0, 12.0]},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_precomputed_std_mean(self):
        """使用预计算的 std + mean"""
        f = EarningsStabilityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_std": 2.0, "roe_mean": 10.0},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(-0.2)  # -CV = -(2/10) = -0.2

    def test_zero_mean(self):
        """均值为零"""
        f = EarningsStabilityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_std": 2.0, "roe_mean": 0.0},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_missing_data(self):
        """无数据"""
        f = EarningsStabilityFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_history_with_invalid(self):
        """含无效值的历史"""
        f = EarningsStabilityFactor(min_periods=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [10.0, "bad", None, 11.0, 9.5]},
        )
        r = f.compute(data)
        assert not math.isnan(r.value)  # 3 valid values remain

    def test_single_value_history(self):
        """只有一个值的历史"""
        f = EarningsStabilityFactor(min_periods=1)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"roe_history": [10.0]},
        )
        r = f.compute(data)
        # stdev of 1 value = 0 → CV = 0 → value = 0
        assert r.value == pytest.approx(0.0)
