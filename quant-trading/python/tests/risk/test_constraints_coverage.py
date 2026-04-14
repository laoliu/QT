"""补充风控约束单元测试 — StyleExposureLimit / ForceRedeemExposureLimit /
LiquidityConstraint / SectorLimit / DailyLossLimit / TrailingStopLoss.

目标：将每个约束类的测试数量提升至 ≥8（项目规范要求）。

覆盖矩阵:
- StyleExposureLimit: 正常通过 / 单风格超标 / 多风格超标 / SELL放行 /
  NAV=0放行 / 无价格放行 / update_style_scores / 边界值恰好等于限制
- ForceRedeemExposureLimit: 正常通过 / 超强赎限制 / 不在强赎区间 /
  update_cb_values / SELL放行 / NAV=0 / 无价格 / adjusted_qty计算 / lot_size对齐
- LiquidityConstraint: 正常通过 / 超参与率 / 无成交量降级放行 /
  update_volumes / SELL放行 / adjusted_qty计算 / lot_size对齐 /
  已有持仓加买入超限 / 恰好等于限制
- SectorLimit: 多行业同时超标 / 无行业映射放行 / 补充到 ≥8
"""

from __future__ import annotations

import pytest

from aipha.risk.constraints import (
    ForceRedeemExposureLimit,
    LiquidityConstraint,
    RiskCheckResult,
    RiskViolationType,
    SectorLimit,
    StyleExposureLimit,
)


# ============================================================================
# StyleExposureLimit Tests
# ============================================================================


class TestStyleExposureLimit:
    """StyleExposureLimit 直接逻辑测试。"""

    def _make(self, max_exposure: dict[str, float] | None = None) -> StyleExposureLimit:
        return StyleExposureLimit(
            max_exposure=max_exposure or {"size": 0.5, "value": 0.3},
        )

    # -- 基本行为 --------------------------------------------------------

    def test_buy_within_limit_passes(self) -> None:
        """暴露未超限 → 通过。"""
        sel = self._make()
        sel.update_style_scores({"A": {"size": 0.2, "value": 0.1}})
        r = sel.check("A", 100, "BUY", {}, 100_000, {"A": 10.0})
        assert r.passed is True

    def test_sell_always_passes(self) -> None:
        """SELL 方向不检查。"""
        sel = self._make()
        sel.update_style_scores({"A": {"size": 9.0, "value": 9.0}})
        r = sel.check("A", 100, "SELL", {"A": 200}, 100_000, {"A": 10.0})
        assert r.passed is True

    def test_single_style_exceeds(self) -> None:
        """单个风格超标 → 拦截。"""
        sel = self._make({"size": 0.01})  # 非常紧的限制
        sel.update_style_scores({"A": {"size": 5.0}})
        r = sel.check("A", 1000, "BUY", {}, 100_000, {"A": 10.0})
        # new_weight = 1000*10/100000 = 0.1; exposure = 0.1*5 = 0.5 > 0.01
        assert r.passed is False
        assert "size" in r.message

    def test_multiple_styles_first_violation_reported(self) -> None:
        """多个风格都超标 → 报告第一个违规。"""
        sel = self._make({"size": 0.001, "value": 0.001})
        sel.update_style_scores({"A": {"size": 5.0, "value": 5.0}})
        r = sel.check("A", 1000, "BUY", {}, 100_000, {"A": 10.0})
        assert r.passed is False

    def test_nav_zero_passes(self) -> None:
        """NAV ≤ 0 时直接通过（安全降级）。"""
        sel = self._make()
        r = sel.check("A", 100, "BUY", {}, 0, {"A": 10.0})
        assert r.passed is True

    def test_no_price_passes(self) -> None:
        """无价格数据时通过（安全降级）。"""
        sel = self._make()
        r = sel.check("A", 100, "BUY", {}, 100_000, {})
        assert r.passed is True

    def test_update_style_scores_merges(self) -> None:
        """update_style_scores 增量合并。"""
        sel = self._make()
        sel.update_style_scores({"A": {"size": 1.0}})
        sel.update_style_scores({"B": {"size": 2.0}})
        assert "A" in sel.style_scores
        assert "B" in sel.style_scores

    def test_existing_positions_counted(self) -> None:
        """已有持仓的暴露计入当前暴露。"""
        sel = self._make({"size": 0.10})
        sel.update_style_scores({
            "A": {"size": 1.0},
            "B": {"size": 1.0},
        })
        # Existing: B 5000 shares * 10 = 50000 → weight 0.5, exposure 0.5*1=0.5
        # Buy A: 100*10/100000 = 0.01, exposure 0.01*1=0.01
        # total = 0.51 > 0.10
        r = sel.check(
            "A", 100, "BUY",
            current_positions={"B": 5000},
            nav=100_000,
            prices={"A": 10.0, "B": 10.0},
        )
        assert r.passed is False
        assert r.metadata["current_exposure"] == pytest.approx(0.5)

    def test_empty_max_exposure_passes(self) -> None:
        """空的 max_exposure 配置 → 不检查。"""
        sel = StyleExposureLimit(max_exposure={})
        r = sel.check("A", 100, "BUY", {}, 100_000, {"A": 10.0})
        assert r.passed is True


# ============================================================================
# ForceRedeemExposureLimit Tests
# ============================================================================


class TestForceRedeemExposureLimit:
    """ForceRedeemExposureLimit 直接逻辑测试。"""

    def _make(self, **kw) -> ForceRedeemExposureLimit:
        defaults = {"max_weight": 0.30, "cb_value_threshold": 125.0, "lot_size": 10}
        defaults.update(kw)
        return ForceRedeemExposureLimit(**defaults)

    # -- 基本行为 --------------------------------------------------------

    def test_buy_below_threshold_passes(self) -> None:
        """标的转股价值 < 阈值 → 通过。"""
        fr = self._make()
        fr.update_cb_values({"CB001": 100.0})
        r = fr.check("CB001", 100, "BUY", {}, 1_000_000, {"CB001": 110.0})
        assert r.passed is True

    def test_buy_above_threshold_within_weight(self) -> None:
        """标的在强赎区间但总暴露 < max_weight → 通过。"""
        fr = self._make()
        fr.update_cb_values({"CB001": 130.0})
        r = fr.check("CB001", 100, "BUY", {}, 1_000_000, {"CB001": 110.0})
        # 100 * 110 = 11000, 11000/1M = 1.1% < 30%
        assert r.passed is True

    def test_buy_exceeds_weight(self) -> None:
        """暴露超过 max_weight → 拦截。"""
        fr = self._make(max_weight=0.05)
        fr.update_cb_values({"CB001": 130.0})
        # 1000 * 110 = 110000, nav=1M → 11% > 5%
        r = fr.check("CB001", 1000, "BUY", {}, 1_000_000, {"CB001": 110.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.FORCE_REDEEM_EXPOSURE

    def test_adjusted_qty_lot_aligned(self) -> None:
        """adjusted_qty 对齐 lot_size。"""
        fr = self._make(max_weight=0.10, lot_size=10)
        fr.update_cb_values({"CB001": 130.0})
        # max allowed value = 1M * 0.10 = 100000
        # max qty = 100000/100 = 1000, aligned to lot=10 → 1000
        r = fr.check("CB001", 2000, "BUY", {}, 1_000_000, {"CB001": 100.0})
        assert r.passed is False
        assert r.adjusted_qty is not None
        assert r.adjusted_qty % 10 == 0

    def test_sell_always_passes(self) -> None:
        """SELL 方向不检查。"""
        fr = self._make()
        r = fr.check("CB001", 100, "SELL", {}, 1_000_000, {"CB001": 110.0})
        assert r.passed is True

    def test_nav_zero_blocks(self) -> None:
        """NAV ≤ 0 → 拦截。"""
        fr = self._make()
        r = fr.check("CB001", 100, "BUY", {}, 0, {"CB001": 110.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.FORCE_REDEEM_EXPOSURE

    def test_no_price_blocks(self) -> None:
        """标的在强赎区间但无价格 → 拦截。"""
        fr = self._make()
        fr.update_cb_values({"CB001": 130.0})
        r = fr.check("CB001", 100, "BUY", {}, 1_000_000, {})
        assert r.passed is False

    def test_existing_zone_positions_counted(self) -> None:
        """已持有强赎区间标的占据暴露额度。"""
        fr = self._make(max_weight=0.10)
        fr.update_cb_values({"CB001": 130.0, "CB002": 135.0})
        # CB002 already 90 * 1000 = 90000 → 9% of 1M
        # Buying CB001: 200 * 100 = 20000 → total 110000 → 11% > 10%
        r = fr.check(
            "CB001", 200, "BUY",
            current_positions={"CB002": 90},
            nav=1_000_000,
            prices={"CB001": 100.0, "CB002": 1000.0},
        )
        assert r.passed is False
        assert r.metadata["current_exposure"] == pytest.approx(0.09)

    def test_update_cb_values_merges(self) -> None:
        """update_cb_values 增量合并。"""
        fr = self._make()
        fr.update_cb_values({"CB001": 130.0})
        fr.update_cb_values({"CB002": 110.0})
        assert fr.cb_values["CB001"] == 130.0
        assert fr.cb_values["CB002"] == 110.0


# ============================================================================
# LiquidityConstraint Tests
# ============================================================================


class TestLiquidityConstraint:
    """LiquidityConstraint 直接逻辑测试。"""

    def _make(self, **kw) -> LiquidityConstraint:
        defaults = {"max_participation_rate": 0.10, "lot_size": 10}
        defaults.update(kw)
        return LiquidityConstraint(**defaults)

    # -- 基本行为 --------------------------------------------------------

    def test_buy_within_limit_passes(self) -> None:
        """持仓未超日均量限制 → 通过。"""
        lc = self._make()
        lc.update_volumes({"A": 10000})
        # 100 < 10000*0.10 = 1000
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is True

    def test_buy_exceeds_participation_rate(self) -> None:
        """持仓超过参与率限制 → 拦截 + adjusted_qty。"""
        lc = self._make(max_participation_rate=0.10, lot_size=10)
        lc.update_volumes({"A": 1000})
        # Buy 200, no existing → 200 > 1000*0.10=100
        r = lc.check("A", 200, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT
        assert r.adjusted_qty is not None
        assert r.adjusted_qty <= 100
        assert r.adjusted_qty % 10 == 0

    def test_sell_always_passes(self) -> None:
        """SELL 方向不检查。"""
        lc = self._make()
        r = lc.check("A", 100, "SELL", {"A": 200}, 1_000_000, {"A": 10.0})
        assert r.passed is True

    def test_no_volume_data_blocks(self) -> None:
        """无日均成交量数据 → 阻止买入（P1-2 修复）。"""
        lc = self._make()
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT

    def test_zero_volume_blocks(self) -> None:
        """日均成交量为 0 → 阻止买入（P1-2 修复）。"""
        lc = self._make()
        lc.update_volumes({"A": 0})
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT

    def test_existing_position_counted(self) -> None:
        """已有持仓 + 新买入超过参与率 → 拦截。"""
        lc = self._make(max_participation_rate=0.10)
        lc.update_volumes({"A": 1000})
        # Existing: 80, buy 30 → total 110 > 100
        r = lc.check("A", 30, "BUY", {"A": 80}, 1_000_000, {"A": 10.0})
        assert r.passed is False

    def test_adjusted_qty_respects_existing(self) -> None:
        """adjusted_qty 扣除已有持仓后仍对齐 lot_size。"""
        lc = self._make(max_participation_rate=0.10, lot_size=10)
        lc.update_volumes({"A": 1000})  # max 100
        # Existing 50, want 200 → allowed_buy = 100-50 = 50, lot=10→50
        r = lc.check("A", 200, "BUY", {"A": 50}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.adjusted_qty == 50

    def test_update_volumes_merges(self) -> None:
        """update_volumes 增量合并。"""
        lc = self._make()
        lc.update_volumes({"A": 5000})
        lc.update_volumes({"B": 8000})
        assert lc.avg_volumes["A"] == 5000
        assert lc.avg_volumes["B"] == 8000

    def test_exactly_at_limit_passes(self) -> None:
        """恰好等于参与率限制 → 通过。"""
        lc = self._make(max_participation_rate=0.10)
        lc.update_volumes({"A": 1000})
        # 100 == 1000 * 0.10 → should pass (not strictly >)
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is True

    # ------ P1-2: 无成交量/NaN 阻止买入 ------

    def test_zero_volume_blocks_buy(self) -> None:
        """成交量为 0 时阻止买入（P1-2 修复）。"""
        lc = self._make()
        lc.update_volumes({"A": 0})
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT
        assert "无日均成交量" in r.message

    def test_missing_volume_blocks_buy(self) -> None:
        """标的无成交量记录时阻止买入。"""
        lc = self._make()
        # 不调用 update_volumes → symbol 不在 avg_volumes 字典中
        r = lc.check("UNKNOWN", 100, "BUY", {}, 1_000_000, {"UNKNOWN": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT

    def test_nan_volume_blocks_buy(self) -> None:
        """成交量为 NaN 时应视为 0，阻止买入。"""
        lc = self._make()
        lc.update_volumes({"A": float("nan")})
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT

    def test_negative_volume_blocks_buy(self) -> None:
        """成交量为负数时阻止买入。"""
        lc = self._make()
        lc.update_volumes({"A": -100})
        r = lc.check("A", 100, "BUY", {}, 1_000_000, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.LIQUIDITY_LIMIT

    def test_zero_volume_sell_still_passes(self) -> None:
        """即使无成交量，卖出方向不受限制。"""
        lc = self._make()
        lc.update_volumes({"A": 0})
        r = lc.check("A", 100, "SELL", {}, 1_000_000, {"A": 10.0})
        assert r.passed is True


# ============================================================================
# SectorLimit 补充 Tests
# ============================================================================


class TestSectorLimitAdditional:
    """SectorLimit 补充测试，将总数提升至 ≥8。"""

    def test_multiple_sectors_independent(self) -> None:
        """两个行业各自不超标 → 通过。"""
        sl = SectorLimit(
            max_weight=0.30,
            sector_mapping={"A": "银行", "B": "医药", "C": "银行"},
        )
        positions = {"A": 1000, "B": 500}  # 银行 10000, 医药 5000
        prices = {"A": 10.0, "B": 10.0, "C": 10.0}
        # 买入 C（银行）1000 → 银行总值 20000 → 20% < 30%
        r = sl.check("C", 1000, "BUY", positions, 100_000, prices)
        assert r.passed is True

    def test_sector_overflow_with_existing(self) -> None:
        """已有同行业持仓导致超标 → 拦截。"""
        sl = SectorLimit(
            max_weight=0.20,
            sector_mapping={"A": "银行", "B": "银行"},
        )
        positions = {"A": 1500}  # 银行已有 15000 → 15%
        prices = {"A": 10.0, "B": 10.0}
        # 买入 B 1000 → 银行总值 25000 → 25% > 20%
        r = sl.check("B", 1000, "BUY", positions, 100_000, prices)
        assert r.passed is False
        assert r.metadata["sector"] == "银行"

    def test_no_mapping_passes(self) -> None:
        """标的无行业映射 → 通过。"""
        sl = SectorLimit(max_weight=0.10, sector_mapping={"A": "银行"})
        r = sl.check("B", 10000, "BUY", {}, 100_000, {"B": 10.0})
        assert r.passed is True
