"""股息因子测试"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.dividend import DividendGrowthFactor, DividendYieldFactor


# ============================================================================
# DividendYieldFactor Tests
# ============================================================================


class TestDividendYieldFactor:
    """股息率因子测试"""

    def test_name_default(self):
        """默认因子名称"""
        f = DividendYieldFactor()
        assert f.name == "DIV_YIELD"

    def test_name_ttm(self):
        """TTM 因子名称"""
        f = DividendYieldFactor(field="dv_ttm")
        assert f.name == "DIV_YIELD_TTM"

    def test_compute_normal(self):
        """正常计算"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="601398.SH",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": 5.2},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(5.2)
        assert r.factor_name == "DIV_YIELD"

    def test_compute_ttm_field(self):
        """使用 dv_ttm 字段"""
        f = DividendYieldFactor(field="dv_ttm")
        data = FactorData(
            symbol="601398.SH",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ttm": 4.8},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(4.8)

    def test_missing_data(self):
        """缺少股息数据"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)
        assert r.metadata.get("invalid") is True

    def test_min_yield_filter(self):
        """低于最低股息率过滤"""
        f = DividendYieldFactor(min_yield=1.0)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": 0.5},
        )
        r = f.compute(data)
        assert math.isnan(r.value)
        assert "下限" in r.metadata.get("reason", "")

    def test_min_yield_pass(self):
        """通过最低股息率过滤"""
        f = DividendYieldFactor(min_yield=1.0)
        data = FactorData(
            symbol="601398.SH",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": 3.5},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(3.5)

    def test_zero_yield(self):
        """零股息率"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": 0.0},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(0.0)

    def test_negative_yield(self):
        """负股息率（异常）"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": -1.0},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_nan_input(self):
        """NaN 输入"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": float("nan")},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_inf_input(self):
        """Inf 输入"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": float("inf")},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_string_input(self):
        """字符串输入（应能转换）"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": "2.5"},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(2.5)

    def test_invalid_string(self):
        """无效字符串输入"""
        f = DividendYieldFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_ratio": "abc"},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_batch_compute(self):
        """批量计算"""
        f = DividendYieldFactor()
        data_list = [
            FactorData(
                symbol=f"00000{i}.SZ",
                trade_date=date(2025, 6, 15),
                metadata={"dv_ratio": float(i)},
            )
            for i in range(1, 4)
        ]
        results = f.compute_batch(data_list)
        assert len(results) == 3
        assert results[0].value == pytest.approx(1.0)
        assert results[2].value == pytest.approx(3.0)


# ============================================================================
# DividendGrowthFactor Tests
# ============================================================================


class TestDividendGrowthFactor:
    """股息增长因子测试"""

    def test_name_consecutive(self):
        """连续增长模式名称"""
        f = DividendGrowthFactor(mode="consecutive")
        assert f.name == "DIV_GROWTH_CONSEC"

    def test_name_cagr(self):
        """CAGR 模式名称"""
        f = DividendGrowthFactor(mode="cagr")
        assert f.name == "DIV_GROWTH_CAGR"

    def test_consecutive_normal(self):
        """连续增长正常计算"""
        f = DividendGrowthFactor(mode="consecutive", min_years=3)
        data = FactorData(
            symbol="601398.SH",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [1.0, 1.5, 2.0, 2.5, 3.0]},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(4.0)  # 4 consecutive increases

    def test_consecutive_broken(self):
        """中间中断的连续增长"""
        f = DividendGrowthFactor(mode="consecutive", min_years=3)
        data = FactorData(
            symbol="601398.SH",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [1.0, 2.0, 1.5, 2.0, 2.5]},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(2.0)  # Only last 2 are consecutive

    def test_consecutive_no_growth(self):
        """完全无增长"""
        f = DividendGrowthFactor(mode="consecutive", min_years=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [2.0, 2.0, 2.0, 2.0]},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(0.0)

    def test_cagr_normal(self):
        """CAGR 正常计算"""
        f = DividendGrowthFactor(mode="cagr", min_years=3)
        # 1.0 → 2.0 in 4 years: CAGR = 2^(1/4) - 1 ≈ 0.1892
        data = FactorData(
            symbol="601398.SH",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [1.0, 1.2, 1.4, 1.6, 2.0]},
        )
        r = f.compute(data)
        expected_cagr = (2.0 / 1.0) ** (1.0 / 4) - 1
        assert r.value == pytest.approx(expected_cagr, abs=1e-6)

    def test_cagr_declining(self):
        """CAGR 下降"""
        f = DividendGrowthFactor(mode="cagr", min_years=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [3.0, 2.5, 2.0, 1.5]},
        )
        r = f.compute(data)
        assert r.value < 0  # Negative CAGR

    def test_insufficient_history(self):
        """历史数据不足"""
        f = DividendGrowthFactor(mode="consecutive", min_years=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [1.0, 2.0]},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_missing_history(self):
        """缺少历史数据"""
        f = DividendGrowthFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_empty_history(self):
        """空历史列表"""
        f = DividendGrowthFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": []},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_history_with_nan(self):
        """含 NaN 的历史（过滤后）"""
        f = DividendGrowthFactor(mode="consecutive", min_years=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [1.0, float("nan"), 2.0, 2.5, 3.0]},
        )
        r = f.compute(data)
        # NaN is filtered out, 4 valid → 3 consecutive
        assert not math.isnan(r.value)

    def test_cagr_zero_start(self):
        """起始为零的 CAGR"""
        f = DividendGrowthFactor(mode="cagr", min_years=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [0.0, 1.0, 2.0, 3.0]},
        )
        r = f.compute(data)
        # first=0, so CAGR returns 0
        assert r.value == pytest.approx(0.0)

    def test_unknown_mode(self):
        """未知计算模式"""
        f = DividendGrowthFactor(mode="unknown", min_years=2)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"dv_history": [1.0, 2.0, 3.0]},
        )
        r = f.compute(data)
        assert math.isnan(r.value)
