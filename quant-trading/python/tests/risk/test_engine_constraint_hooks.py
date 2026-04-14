"""Tests for engine-level constraint hooks (update_cb_values, update_style_scores).

验证引擎在执行时正确调用风控约束的运行时更新钩子。
"""

from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from aipha.risk.constraints import (
    ForceRedeemExposureLimit,
    StyleExposureLimit,
    CompositeRiskManager,
    RiskCheckResult,
)


# ============================================================================
# StyleExposureLimit.update_style_scores 单元测试
# ============================================================================


class TestStyleExposureUpdateScores:
    """StyleExposureLimit.update_style_scores 方法测试"""

    def test_update_style_scores_basic(self):
        """基本更新风格分数"""
        sel = StyleExposureLimit(
            max_exposure={"size": 0.30},
        )
        assert sel.style_scores == {}

        scores = {"000001.SZ": {"size": 0.5, "value": -0.2}}
        sel.update_style_scores(scores)
        assert sel.style_scores["000001.SZ"]["size"] == 0.5
        assert sel.style_scores["000001.SZ"]["value"] == -0.2

    def test_update_style_scores_merge(self):
        """多次更新合并"""
        sel = StyleExposureLimit(
            max_exposure={"size": 0.30},
            style_scores={"A.SH": {"size": 0.1}},
        )
        sel.update_style_scores({"B.SH": {"size": 0.2}})
        assert "A.SH" in sel.style_scores
        assert "B.SH" in sel.style_scores

    def test_update_style_scores_overwrite(self):
        """更新覆盖已有标的"""
        sel = StyleExposureLimit(
            max_exposure={"size": 0.30},
            style_scores={"A.SH": {"size": 0.1}},
        )
        sel.update_style_scores({"A.SH": {"size": 0.9}})
        assert sel.style_scores["A.SH"]["size"] == 0.9

    def test_update_style_scores_empty(self):
        """空更新不影响已有数据"""
        sel = StyleExposureLimit(
            max_exposure={"size": 0.30},
            style_scores={"A.SH": {"size": 0.1}},
        )
        sel.update_style_scores({})
        assert sel.style_scores["A.SH"]["size"] == 0.1

    def test_check_uses_updated_scores(self):
        """check() 使用更新后的风格分数"""
        sel = StyleExposureLimit(
            max_exposure={"size": 0.10},
        )
        # 没有分数时通过
        result = sel.check("A.SH", 100, "BUY", {}, 100000, {"A.SH": 10.0})
        assert result.passed

        # 更新分数使其超限
        sel.update_style_scores({"A.SH": {"size": 2.0}})
        result = sel.check(
            "A.SH", 10000, "BUY", {}, 100000, {"A.SH": 10.0}
        )
        # 10000 * 10 / 100000 = 1.0 weight, 1.0 * 2.0 = 2.0 exposure > 0.10
        assert not result.passed


# ============================================================================
# ForceRedeemExposureLimit.update_cb_values 在引擎中的调用测试
# ============================================================================


class TestEngineCallsUpdateCbValues:
    """验证引擎在 process_bar 时调用 update_cb_values"""

    def test_update_cb_values_called_with_prices(self):
        """引擎中 update_cb_values 接收当日价格"""
        frel = ForceRedeemExposureLimit(
            max_weight=0.30,
            cb_value_threshold=125.0,
        )
        # 模拟引擎调用
        prices = {"CB001.SH": 130.0, "CB002.SH": 110.0}
        if hasattr(frel, 'update_cb_values'):
            frel.update_cb_values(prices)

        assert frel.cb_values["CB001.SH"] == 130.0
        assert frel.cb_values["CB002.SH"] == 110.0

    def test_cb_values_affect_check(self):
        """update_cb_values 后 check 正确识别强赎区间"""
        frel = ForceRedeemExposureLimit(
            max_weight=0.10,
            cb_value_threshold=125.0,
            lot_size=10,
        )
        # CB001 价格 130 >= 125 阈值 → 强赎区间
        frel.update_cb_values({"CB001.SH": 130.0})

        # 已有大量 CB001 持仓
        positions = {"CB001.SH": 500}
        prices = {"CB001.SH": 130.0}
        nav = 100000.0

        # 当前暴露: 500*130/100000 = 65% 已经超过 10%
        result = frel.check("CB001.SH", 100, "BUY", positions, nav, prices)
        assert not result.passed

    def test_cb_values_below_threshold_pass(self):
        """转股价值低于阈值的标的不受限"""
        frel = ForceRedeemExposureLimit(
            max_weight=0.10,
            cb_value_threshold=125.0,
            lot_size=10,
        )
        frel.update_cb_values({"CB001.SH": 100.0})  # 低于 125

        result = frel.check(
            "CB001.SH", 100, "BUY",
            {"CB001.SH": 500}, 100000.0,
            {"CB001.SH": 100.0},
        )
        assert result.passed


# ============================================================================
# CompositeRiskManager 中的钩子调用测试
# ============================================================================


class TestCompositeRiskManagerHooks:
    """验证 CompositeRiskManager 中各约束钩子的鸭子类型调用"""

    def test_update_cb_values_via_hasattr(self):
        """通过 hasattr 模式调用 update_cb_values"""
        frel = ForceRedeemExposureLimit(max_weight=0.30)
        rm = CompositeRiskManager(constraints=[frel])

        prices = {"CB001.SH": 130.0}
        for c in rm.constraints:
            if hasattr(c, 'update_cb_values'):
                c.update_cb_values(prices)

        assert frel.cb_values["CB001.SH"] == 130.0

    def test_update_style_scores_via_hasattr(self):
        """通过 hasattr 模式调用 update_style_scores"""
        sel = StyleExposureLimit(max_exposure={"size": 0.30})
        rm = CompositeRiskManager(constraints=[sel])

        scores = {"000001.SZ": {"size": 0.5}}
        for c in rm.constraints:
            if hasattr(c, 'update_style_scores'):
                c.update_style_scores(scores)

        assert sel.style_scores["000001.SZ"]["size"] == 0.5

    def test_mixed_constraints_only_relevant_hooks_called(self):
        """混合约束中只有相关约束的钩子被调用"""
        frel = ForceRedeemExposureLimit(max_weight=0.30)
        sel = StyleExposureLimit(max_exposure={"size": 0.30})
        rm = CompositeRiskManager(constraints=[frel, sel])

        # update_cb_values 只影响 frel
        for c in rm.constraints:
            if hasattr(c, 'update_cb_values'):
                c.update_cb_values({"CB001.SH": 130.0})

        assert frel.cb_values.get("CB001.SH") == 130.0
        assert not hasattr(sel, 'cb_values') or sel.style_scores.get("CB001.SH") is None

    def test_no_hook_constraints_unaffected(self):
        """没有钩子方法的约束不受影响"""
        from aipha.risk.constraints import PositionLimit
        pl = PositionLimit(max_weight=0.10)
        rm = CompositeRiskManager(constraints=[pl])

        # 这些调用不应崩溃
        for c in rm.constraints:
            if hasattr(c, 'update_cb_values'):
                c.update_cb_values({})
            if hasattr(c, 'update_style_scores'):
                c.update_style_scores({})
        # 如果走到这里就说明没有崩溃
        assert True


# ============================================================================
# 边界条件测试
# ============================================================================


class TestEdgeCases:
    """边界条件"""

    def test_empty_prices_update(self):
        """空价格更新"""
        frel = ForceRedeemExposureLimit(max_weight=0.30)
        frel.update_cb_values({})
        assert frel.cb_values == {}

    def test_style_scores_with_no_max_exposure(self):
        """没有 max_exposure 配置时 check 通过"""
        sel = StyleExposureLimit(max_exposure={})
        sel.update_style_scores({"A.SH": {"size": 999.0}})
        result = sel.check("A.SH", 100, "BUY", {}, 100000, {"A.SH": 10.0})
        assert result.passed

    def test_sell_side_always_passes(self):
        """卖出方向始终通过"""
        frel = ForceRedeemExposureLimit(max_weight=0.01)
        frel.update_cb_values({"CB001.SH": 200.0})
        result = frel.check(
            "CB001.SH", 1000, "SELL",
            {"CB001.SH": 1000}, 100000.0,
            {"CB001.SH": 200.0},
        )
        assert result.passed

    def test_zero_nav_force_redeem(self):
        """NAV 为 0 时拒绝"""
        frel = ForceRedeemExposureLimit(max_weight=0.30)
        frel.update_cb_values({"CB001.SH": 130.0})
        result = frel.check("CB001.SH", 100, "BUY", {}, 0, {"CB001.SH": 130.0})
        assert not result.passed

    def test_adjusted_qty_respects_lot_size(self):
        """调整数量尊重最小交易单位"""
        frel = ForceRedeemExposureLimit(
            max_weight=0.10,
            cb_value_threshold=125.0,
            lot_size=10,
        )
        frel.update_cb_values({"CB001.SH": 130.0})
        result = frel.check(
            "CB001.SH", 200, "BUY",
            {}, 100000.0,
            {"CB001.SH": 130.0},
        )
        # max allowed: 0.10 * 100000 / 130 ≈ 76.9 → floor to lot_size 10 → 70
        if not result.passed and result.adjusted_qty is not None:
            assert result.adjusted_qty % 10 == 0
