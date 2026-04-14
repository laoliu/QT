"""下修条款因子测试

测试覆盖：
- PutbackProximityFactor: 接近度计算
  - 正常流：正股低于触发线
  - 正股高于触发线
  - 刚好在触发线
  - 时间衰减
  - 缺失字段/无效输入
- putback_optionality_score: 综合评分
  - 正常组合
  - 边界条件
  - 各因素影响验证
"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.putback_clause import (
    PutbackProximityFactor,
    putback_optionality_score,
)


# ============================================================================
# PutbackProximityFactor 测试
# ============================================================================


class TestPutbackProximityFactor:
    """下修接近度因子测试"""

    def _make_data(
        self,
        stock_price: float | None = None,
        conversion_price: float | None = None,
        trigger_ratio: float | None = None,
        remaining_years: float | None = None,
        symbol: str = "113001.SH",
    ) -> FactorData:
        meta: dict = {}
        if stock_price is not None:
            meta["stock_price"] = stock_price
        if conversion_price is not None:
            meta["conversion_price"] = conversion_price
        if trigger_ratio is not None:
            meta["trigger_ratio"] = trigger_ratio
        if remaining_years is not None:
            meta["remaining_years"] = remaining_years

        return FactorData(
            symbol=symbol,
            trade_date=date(2025, 1, 10),
            metadata=meta,
        )

    def test_below_trigger_line(self):
        """正股已低于触发线 → proximity > 0"""
        # 正股 8，转股价 12，触发线 = 12 * 0.85 = 10.2
        # price_ratio = 8/12 = 0.667, proximity = 1 - 0.667/0.85 = 0.216
        factor = PutbackProximityFactor(trigger_ratio=0.85)
        result = factor.compute(
            self._make_data(stock_price=8.0, conversion_price=12.0)
        )
        assert result.value > 0
        assert result.value == pytest.approx(1.0 - (8.0 / 12.0) / 0.85, abs=1e-4)

    def test_above_trigger_line(self):
        """正股远高于触发线 → proximity < 0"""
        # 正股 15，转股价 12，ratio = 1.25, proximity = 1 - 1.25/0.85 < 0
        factor = PutbackProximityFactor(trigger_ratio=0.85)
        result = factor.compute(
            self._make_data(stock_price=15.0, conversion_price=12.0)
        )
        assert result.value < 0

    def test_at_trigger_line(self):
        """正股刚好在触发线 → proximity ≈ 0"""
        # 正股 = 转股价 * 0.85 = 10.2
        factor = PutbackProximityFactor(trigger_ratio=0.85)
        result = factor.compute(
            self._make_data(stock_price=10.2, conversion_price=12.0)
        )
        assert result.value == pytest.approx(0.0, abs=0.01)

    def test_custom_trigger_ratio_from_metadata(self):
        """从 metadata 覆盖触发比例"""
        factor = PutbackProximityFactor(trigger_ratio=0.85)
        result = factor.compute(
            self._make_data(
                stock_price=8.0, conversion_price=12.0, trigger_ratio=0.80
            )
        )
        expected = 1.0 - (8.0 / 12.0) / 0.80
        assert result.value == pytest.approx(expected, abs=1e-4)
        assert result.metadata["trigger_ratio"] == 0.80

    def test_time_decay(self):
        """距回售期远 → 因子值衰减"""
        factor = PutbackProximityFactor(trigger_ratio=0.85, decay_years=2.0)

        # 临近回售期（1 年）
        r_near = factor.compute(
            self._make_data(
                stock_price=8.0, conversion_price=12.0, remaining_years=1.0
            )
        )

        # 远离回售期（5 年）
        r_far = factor.compute(
            self._make_data(
                stock_price=8.0, conversion_price=12.0, remaining_years=5.0
            )
        )

        # 近的应该比远的绝对值大
        assert abs(r_near.value) > abs(r_far.value)

    def test_no_decay_within_threshold(self):
        """在 decay_years 内不衰减"""
        factor = PutbackProximityFactor(trigger_ratio=0.85, decay_years=2.0)
        r_1y = factor.compute(
            self._make_data(
                stock_price=8.0, conversion_price=12.0, remaining_years=1.0
            )
        )
        r_no = factor.compute(
            self._make_data(stock_price=8.0, conversion_price=12.0)
        )
        # 1 年 < decay_years=2 年，不应衰减
        assert r_1y.value == pytest.approx(r_no.value, abs=1e-6)

    def test_missing_stock_price(self):
        """缺少正股价格 → invalid"""
        factor = PutbackProximityFactor()
        result = factor.compute(
            self._make_data(conversion_price=12.0)
        )
        assert math.isnan(result.value)
        assert result.metadata.get("reason") == "missing_stock_or_conversion_price"

    def test_missing_conversion_price(self):
        """缺少转股价 → invalid"""
        factor = PutbackProximityFactor()
        result = factor.compute(
            self._make_data(stock_price=8.0)
        )
        assert math.isnan(result.value)

    def test_zero_conversion_price(self):
        """转股价为 0 → invalid"""
        factor = PutbackProximityFactor()
        result = factor.compute(
            self._make_data(stock_price=8.0, conversion_price=0.0)
        )
        assert math.isnan(result.value)
        assert result.metadata.get("reason") == "non_positive_price"

    def test_negative_stock_price(self):
        """负价格 → invalid"""
        factor = PutbackProximityFactor()
        result = factor.compute(
            self._make_data(stock_price=-1.0, conversion_price=12.0)
        )
        assert math.isnan(result.value)

    def test_name_auto_generation(self):
        """因子名称"""
        f1 = PutbackProximityFactor(trigger_ratio=0.85)
        assert f1.name == "PUTBACK_PROX_85"

        f2 = PutbackProximityFactor(trigger_ratio=0.80)
        assert f2.name == "PUTBACK_PROX_80"

        f3 = PutbackProximityFactor(trigger_ratio=0.85, name="custom")
        assert f3.name == "custom"

    def test_metadata_contents(self):
        """结果 metadata 包含计算细节"""
        factor = PutbackProximityFactor(trigger_ratio=0.85)
        result = factor.compute(
            self._make_data(stock_price=9.0, conversion_price=12.0)
        )
        assert "stock_price" in result.metadata
        assert "conversion_price" in result.metadata
        assert "price_ratio" in result.metadata
        assert "trigger_ratio" in result.metadata
        assert result.metadata["price_ratio"] == pytest.approx(0.75, abs=0.01)


# ============================================================================
# putback_optionality_score 测试
# ============================================================================


class TestPutbackOptionalityScore:
    """综合下修期权价值评分测试"""

    def test_favorable_conditions(self):
        """有利条件：正股低、转债低价、临近回售 → 高分"""
        score = putback_optionality_score(
            stock_price=8.0,
            conversion_price=12.0,
            cb_price=90.0,
            remaining_years=1.0,
            trigger_ratio=0.85,
            has_putback_clause=True,
            issue_size=60000.0,
        )
        assert score > 0.5

    def test_unfavorable_conditions(self):
        """不利条件：正股高、转债高价、远离回售 → 低分"""
        score = putback_optionality_score(
            stock_price=20.0,
            conversion_price=12.0,
            cb_price=140.0,
            remaining_years=5.0,
            trigger_ratio=0.85,
            has_putback_clause=True,
        )
        assert score < 0.5

    def test_zero_price_returns_zero(self):
        """无效价格 → 0"""
        assert putback_optionality_score(0, 12, 90, 1.0) == 0.0
        assert putback_optionality_score(8, 0, 90, 1.0) == 0.0
        assert putback_optionality_score(8, 12, 0, 1.0) == 0.0

    def test_score_range(self):
        """评分范围 [0, 1]"""
        for sp in [5, 8, 12, 20]:
            for cp in [10, 15, 20]:
                for cbp in [80, 100, 130]:
                    for ry in [0.5, 2.0, 5.0]:
                        s = putback_optionality_score(sp, cp, cbp, ry)
                        assert 0.0 <= s <= 1.0, f"Out of range: {s}"

    def test_distance_effect(self):
        """正股越低（距触发线越近） → 分数越高"""
        s_near = putback_optionality_score(
            stock_price=8.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=1.5,
        )
        s_far = putback_optionality_score(
            stock_price=14.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=1.5,
        )
        assert s_near > s_far

    def test_space_effect(self):
        """转债价格越低 → 下修空间越大 → 分数越高"""
        s_low = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=85.0,
            remaining_years=1.5,
        )
        s_high = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=120.0,
            remaining_years=1.5,
        )
        assert s_low > s_high

    def test_urgency_effect(self):
        """临近回售期 → 分数更高"""
        s_urgent = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=0.5, has_putback_clause=True,
        )
        s_calm = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=5.0, has_putback_clause=True,
        )
        assert s_urgent > s_calm

    def test_size_motive_effect(self):
        """大发行规模 → 发行人动机更强 → 分数更高"""
        s_large = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=1.5, issue_size=80000.0,
        )
        s_small = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=1.5, issue_size=10000.0,
        )
        assert s_large > s_small

    def test_no_putback_clause(self):
        """无回售条款 → 紧迫性低"""
        s_with = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=0.5, has_putback_clause=True,
        )
        s_without = putback_optionality_score(
            stock_price=9.0, conversion_price=12.0, cb_price=95.0,
            remaining_years=0.5, has_putback_clause=False,
        )
        assert s_with > s_without
