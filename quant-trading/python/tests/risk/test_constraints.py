"""
风控约束单元测试
"""

import pytest

from aipha.risk.constraints import (
    CompositeRiskManager,
    ConcentrationLimit,
    DrawdownLimit,
    ForceRedeemExposureLimit,
    IndustryNeutralConstraint,
    LiquidityConstraint,
    PositionLimit,
    RiskCheckResult,
    RiskViolationType,
    SectorLimit,
    StyleExposureLimit,
    TurnoverLimit,
)


# ============================================================================
# PositionLimit Tests
# ============================================================================


class TestPositionLimit:
    """单票持仓限制测试"""

    def test_creation(self):
        """创建限制对象"""
        limit = PositionLimit(max_weight=0.10)
        assert limit.max_weight == 0.10

    def test_within_limit(self):
        """在限制内通过"""
        limit = PositionLimit(max_weight=0.10)
        result = limit.check(
            symbol="000001.SZ",
            qty=1000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 1000 * 10 = 10000，占 1%，应通过
        assert result.passed

    def test_exceeds_limit(self):
        """超过限制失败"""
        limit = PositionLimit(max_weight=0.10)
        result = limit.check(
            symbol="000001.SZ",
            qty=20000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 20000 * 10 = 200000，占 20%，超过 10% 限制
        assert not result.passed
        assert result.violation_type == RiskViolationType.POSITION_LIMIT
        assert result.adjusted_qty is not None
        assert result.adjusted_qty <= 10000  # 最多 10% = 100000 / 10 = 10000

    def test_with_existing_position(self):
        """考虑已有持仓"""
        limit = PositionLimit(max_weight=0.10)
        result = limit.check(
            symbol="000001.SZ",
            qty=5000,
            side="BUY",
            current_positions={"000001.SZ": 8000},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 已有 8000，买入 5000，总 13000 * 10 = 130000，占 13%
        assert not result.passed

    def test_sell_always_passes(self):
        """卖出总是通过"""
        limit = PositionLimit(max_weight=0.10)
        result = limit.check(
            symbol="000001.SZ",
            qty=100000,
            side="SELL",
            current_positions={"000001.SZ": 100000},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        assert result.passed

    def test_zero_nav_fails(self):
        """NAV 为 0 失败"""
        limit = PositionLimit(max_weight=0.10)
        result = limit.check(
            symbol="000001.SZ",
            qty=1000,
            side="BUY",
            current_positions={},
            nav=0,
            prices={"000001.SZ": 10.0},
        )
        assert not result.passed

    def test_missing_price_fails(self):
        """缺少价格失败"""
        limit = PositionLimit(max_weight=0.10)
        result = limit.check(
            symbol="000001.SZ",
            qty=1000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={},
        )
        assert not result.passed

    def test_adjusted_qty_is_rounded(self):
        """调整后数量取整到 100"""
        limit = PositionLimit(max_weight=0.05)  # 5%
        result = limit.check(
            symbol="000001.SZ",
            qty=10000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 最大 5% = 50000 / 10 = 5000
        assert result.adjusted_qty is not None
        assert result.adjusted_qty % 100 == 0


# ============================================================================
# SectorLimit Tests
# ============================================================================


class TestSectorLimit:
    """行业暴露限制测试"""

    @pytest.fixture
    def sector_mapping(self) -> dict[str, str]:
        return {
            "000001.SZ": "银行",
            "600000.SH": "银行",
            "601398.SH": "银行",
            "000858.SZ": "白酒",
            "600519.SH": "白酒",
        }

    def test_creation(self, sector_mapping):
        """创建限制对象"""
        limit = SectorLimit(max_weight=0.30, sector_mapping=sector_mapping)
        assert limit.max_weight == 0.30

    def test_within_limit(self, sector_mapping):
        """在限制内通过"""
        limit = SectorLimit(max_weight=0.30, sector_mapping=sector_mapping)
        result = limit.check(
            symbol="000001.SZ",
            qty=1000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 1000 * 10 = 10000，占 1%，应通过
        assert result.passed

    def test_exceeds_sector_limit(self, sector_mapping):
        """超过行业限制失败"""
        limit = SectorLimit(max_weight=0.20, sector_mapping=sector_mapping)
        result = limit.check(
            symbol="600000.SH",
            qty=10000,
            side="BUY",
            current_positions={"000001.SZ": 20000},  # 银行已有 200000
            nav=1_000_000,
            prices={"000001.SZ": 10.0, "600000.SH": 10.0},
        )
        # 银行板块：已有 200000 + 新增 100000 = 300000，占 30%
        assert not result.passed
        assert result.violation_type == RiskViolationType.SECTOR_LIMIT
        assert "银行" in result.message

    def test_different_sector_ok(self, sector_mapping):
        """不同行业不影响"""
        limit = SectorLimit(max_weight=0.20, sector_mapping=sector_mapping)
        result = limit.check(
            symbol="000858.SZ",  # 白酒
            qty=10000,
            side="BUY",
            current_positions={"000001.SZ": 20000},  # 银行
            nav=1_000_000,
            prices={"000001.SZ": 10.0, "000858.SZ": 100.0},
        )
        # 白酒：10000 * 100 = 1000000，占 100%...但是这超过限制了
        # 让我们用合理的数量
        result = limit.check(
            symbol="000858.SZ",
            qty=1000,
            side="BUY",
            current_positions={"000001.SZ": 20000},
            nav=1_000_000,
            prices={"000001.SZ": 10.0, "000858.SZ": 100.0},
        )
        # 白酒：1000 * 100 = 100000，占 10%，应通过
        assert result.passed

    def test_no_sector_mapping_passes(self, sector_mapping):
        """无行业映射通过"""
        limit = SectorLimit(max_weight=0.20, sector_mapping=sector_mapping)
        result = limit.check(
            symbol="UNKNOWN.SZ",  # 未知标的
            qty=10000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"UNKNOWN.SZ": 10.0},
        )
        assert result.passed

    def test_sell_always_passes(self, sector_mapping):
        """卖出总是通过"""
        limit = SectorLimit(max_weight=0.20, sector_mapping=sector_mapping)
        result = limit.check(
            symbol="000001.SZ",
            qty=100000,
            side="SELL",
            current_positions={"000001.SZ": 100000},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        assert result.passed


# ============================================================================
# CompositeRiskManager Tests
# ============================================================================


class TestCompositeRiskManager:
    """组合风控管理器测试"""

    def test_empty_constraints_passes(self):
        """无约束通过"""
        manager = CompositeRiskManager(constraints=[])
        result = manager.check(
            symbol="000001.SZ",
            qty=100000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        assert result.passed

    def test_all_constraints_pass(self):
        """所有约束通过"""
        manager = CompositeRiskManager(
            constraints=[
                PositionLimit(max_weight=0.50),
                SectorLimit(max_weight=0.50, sector_mapping={"000001.SZ": "银行"}),
            ]
        )
        result = manager.check(
            symbol="000001.SZ",
            qty=1000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        assert result.passed

    def test_first_failure_returns(self):
        """多约束聚合模式：收集全部违规，取最严格 adjusted_qty"""
        manager = CompositeRiskManager(
            constraints=[
                PositionLimit(max_weight=0.05),  # 5%
                SectorLimit(max_weight=0.50),
            ]
        )
        result = manager.check(
            symbol="000001.SZ",
            qty=10000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 10% 超过单票 5% 限制
        assert not result.passed
        assert result.violation_type == RiskViolationType.POSITION_LIMIT

    def test_aggregate_takes_tightest_qty(self):
        """聚合模式：多约束取最严格的 adjusted_qty"""
        manager = CompositeRiskManager(
            constraints=[
                PositionLimit(max_weight=0.10),  # 10% 限制 → ≤10000 qty
                PositionLimit(max_weight=0.05),  # 5% 限制 → ≤5000 qty (更严格)
            ]
        )
        result = manager.check(
            symbol="000001.SZ",
            qty=20000,
            side="BUY",
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0},
        )
        # 20% 超过两个约束，聚合后 adjusted_qty ≤ 5000（更严格的）
        assert not result.passed
        assert result.adjusted_qty is not None
        assert result.adjusted_qty <= 5000

    def test_batch_check(self):
        """批量检查"""
        manager = CompositeRiskManager(
            constraints=[PositionLimit(max_weight=0.10)]
        )
        orders = [
            ("000001.SZ", 1000, "BUY"),  # 1%，通过
            ("000002.SZ", 20000, "BUY"),  # 20%，失败
        ]
        results = manager.check_batch(
            orders=orders,
            current_positions={},
            nav=1_000_000,
            prices={"000001.SZ": 10.0, "000002.SZ": 10.0},
        )
        assert results["000001.SZ"].passed
        assert not results["000002.SZ"].passed


# ============================================================================
# IndustryNeutralConstraint Tests
# ============================================================================


class TestIndustryNeutralConstraint:
    """行业中性约束测试"""

    def _make_constraint(
        self,
        max_deviation: float = 0.05,
        sector_mapping: dict | None = None,
        benchmark_weights: dict | None = None,
    ) -> IndustryNeutralConstraint:
        return IndustryNeutralConstraint(
            max_deviation=max_deviation,
            sector_mapping=sector_mapping or {
                "000001.SZ": "银行",
                "000002.SZ": "银行",
                "600519.SH": "食品饮料",
                "000858.SZ": "食品饮料",
                "300750.SZ": "电力设备",
            },
            benchmark_weights=benchmark_weights or {
                "银行": 0.15,
                "食品饮料": 0.10,
                "电力设备": 0.08,
            },
        )

    def test_sell_always_passes(self):
        """卖出不受行业中性约束"""
        c = self._make_constraint()
        r = c.check("000001.SZ", 1000, "SELL", {}, nav=1_000_000, prices={"000001.SZ": 10})
        assert r.passed

    def test_within_deviation_passes(self):
        """行业偏离在 max_deviation 内通过"""
        c = self._make_constraint(max_deviation=0.05)
        r = c.check("000001.SZ", 1000, "BUY",
                     current_positions={},
                     nav=1_000_000,
                     prices={"000001.SZ": 10.0})
        # 买入 1000*10=10000, 占比 1%, 基准 15%, 偏离 -14% < 5% → 通过
        assert r.passed

    def test_exceeds_deviation_fails(self):
        """行业偏离超过 max_deviation 失败"""
        c = self._make_constraint(max_deviation=0.05)
        # 当前持有银行 200,000 (20%), 基准 15%, 再买 60,000 → 26% → 偏离 11%
        r = c.check("000002.SZ", 6000, "BUY",
                     current_positions={"000001.SZ": 20000},
                     nav=1_000_000,
                     prices={"000001.SZ": 10.0, "000002.SZ": 10.0})
        assert not r.passed
        assert r.violation_type == RiskViolationType.SECTOR_LIMIT

    def test_adjusted_qty_returned(self):
        """超限时返回调整后数量"""
        c = self._make_constraint(max_deviation=0.05)
        r = c.check("000001.SZ", 50000, "BUY",
                     current_positions={},
                     nav=1_000_000,
                     prices={"000001.SZ": 10.0})
        # 基准 15% + 限制 5% = 最大 20%, 即 200,000 价值 = 20,000 股
        assert not r.passed
        assert r.adjusted_qty is not None
        assert r.adjusted_qty <= 20000

    def test_update_benchmark(self):
        """update_benchmark 可动态更新基准权重"""
        c = self._make_constraint()
        c.update_benchmark({"银行": 0.25, "食品饮料": 0.20})
        assert c.benchmark_weights["银行"] == 0.25

    def test_unknown_sector_passes(self):
        """sector_mapping 中找不到的股票通过"""
        c = self._make_constraint()
        r = c.check("999999.SZ", 1000, "BUY",
                     current_positions={},
                     nav=1_000_000,
                     prices={"999999.SZ": 10.0})
        assert r.passed

    def test_zero_nav_fails(self):
        """NAV 为 0 时失败"""
        c = self._make_constraint()
        r = c.check("000001.SZ", 1000, "BUY",
                     current_positions={},
                     nav=0,
                     prices={"000001.SZ": 10.0})
        assert not r.passed

    def test_metadata_includes_details(self):
        """超限结果包含行业偏离详情"""
        c = self._make_constraint(max_deviation=0.01)
        r = c.check("000001.SZ", 50000, "BUY",
                     current_positions={},
                     nav=1_000_000,
                     prices={"000001.SZ": 10.0})
        assert not r.passed
        assert "sector" in r.metadata
        assert "deviation" in r.metadata
        assert r.metadata["sector"] == "银行"


# ============================================================================
# _build_risk_manager Industry Neutral Integration Tests
# ============================================================================


class TestBuildRiskManagerIndustryNeutral:
    """验证 _build_risk_manager 正确接入 IndustryNeutralConstraint"""

    def _make_config(self, industry_neutral_enabled=False, **kwargs):
        from datetime import date

        from aipha.backtest.config_runner import (
            BacktestRunConfig,
            CostConfig,
            DataConfig,
            RiskConfig,
            StrategyConfig,
            UniverseConfig,
        )
        risk = RiskConfig(
            enabled=True,
            industry_neutral_enabled=industry_neutral_enabled,
            **kwargs,
        )
        return BacktestRunConfig(
            name="test",
            description="test",
            data=DataConfig(
                source="synthetic",
                symbols=["000001.SZ"],
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            ),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="smallcap_rotation"),
            cost=CostConfig(),
            risk=risk,
        )

    def test_disabled_no_industry_constraint(self):
        """industry_neutral_enabled=False 时不包含行业中性约束"""
        from aipha.backtest.config_runner import _build_risk_manager
        cfg = self._make_config(industry_neutral_enabled=False)
        rm = _build_risk_manager(cfg)
        assert rm is not None
        names = [type(c).__name__ for c in rm.constraints]
        assert "IndustryNeutralConstraint" not in names

    def test_enabled_includes_industry_constraint(self):
        """industry_neutral_enabled=True 时包含行业中性约束"""
        from aipha.backtest.config_runner import _build_risk_manager
        cfg = self._make_config(
            industry_neutral_enabled=True,
            industry_max_deviation=0.03,
        )
        rm = _build_risk_manager(cfg)
        assert rm is not None
        names = [type(c).__name__ for c in rm.constraints]
        assert "IndustryNeutralConstraint" in names
        # 验证 max_deviation 正确传递
        ic = [c for c in rm.constraints if isinstance(c, IndustryNeutralConstraint)][0]
        assert ic.max_deviation == 0.03

    def test_default_deviation(self):
        """默认 industry_max_deviation=0.05"""
        from aipha.backtest.config_runner import _build_risk_manager
        cfg = self._make_config(industry_neutral_enabled=True)
        rm = _build_risk_manager(cfg)
        ic = [c for c in rm.constraints if isinstance(c, IndustryNeutralConstraint)][0]
        assert ic.max_deviation == 0.05


# ─────────────────────────────────────────────────────────────────────────────
# B2 回归测试: DrawdownLimit.allow_rotation + scale_factor_floor
# ─────────────────────────────────────────────────────────────────────────────

class TestDrawdownLimitB2Fix:
    """B2 fix: allow_rotation + scale_factor_floor 防止 CB 策略反馈循环。"""

    def _make_dd_limit(self, **kwargs) -> "DrawdownLimit":
        from aipha.risk.constraints import DrawdownLimit
        dl = DrawdownLimit(**kwargs)
        dl.peak_nav = 1_000_000.0
        dl.current_nav = 1_000_000.0
        return dl

    def test_scale_factor_floor_prevents_zero_qty(self):
        """scale_factor_floor=0.3 时，减仓区间仍保留至少 30% 规模。"""
        from aipha.risk.constraints import DrawdownLimit
        dl = DrawdownLimit(
            max_drawdown=0.20,
            reduce_position_at=0.10,
            scale_factor_floor=0.30,
            lot_size=10,
        )
        # 模拟 15% 回撤（在减仓区间内）
        dl.peak_nav = 1_000_000.0
        dl.current_nav = 850_000.0  # 15% dd
        result = dl.check(
            symbol="110001.SH",
            qty=100,
            side="BUY",
            current_positions={},
            nav=850_000.0,
            prices={"110001.SH": 100.0},
        )
        # floor=0.3 → adjusted_qty >= 30
        assert result.passed
        assert result.adjusted_qty is not None and result.adjusted_qty >= 30

    def test_scale_factor_floor_zero_default_same_as_before(self):
        """默认 scale_factor_floor=0.0 行为与修改前一致（接近回撤顶时可缩减至0）。"""
        from aipha.risk.constraints import DrawdownLimit
        dl = DrawdownLimit(
            max_drawdown=0.20,
            reduce_position_at=0.10,
            scale_factor_floor=0.0,
            lot_size=100,
        )
        dl.peak_nav = 1_000_000.0
        dl.current_nav = 820_000.0  # 18% dd → almost at max
        result = dl.check(
            symbol="110001.SH",
            qty=100,
            side="BUY",
            current_positions={},
            nav=820_000.0,
            prices={},
        )
        assert not result.passed  # 应被阻止（接近最大回撤）

    def test_allow_rotation_bypass_when_has_positions(self):
        """allow_rotation=True 且有持仓时，减仓区间直接通过，不缩减。"""
        from aipha.risk.constraints import DrawdownLimit
        dl = DrawdownLimit(
            max_drawdown=0.20,
            reduce_position_at=0.10,
            scale_factor_floor=0.0,
            allow_rotation=True,
            lot_size=10,
        )
        dl.peak_nav = 1_000_000.0
        dl.current_nav = 870_000.0  # 13% dd
        result = dl.check(
            symbol="110002.SH",
            qty=100,
            side="BUY",
            current_positions={"110001.SH": 50},  # 有持仓
            nav=870_000.0,
            prices={},
        )
        assert result.passed
        assert result.adjusted_qty is None  # 未调整规模

    def test_allow_rotation_still_blocks_above_max_drawdown(self):
        """allow_rotation=True 但超过 max_drawdown 时仍完全阻止开仓。"""
        from aipha.risk.constraints import DrawdownLimit
        dl = DrawdownLimit(
            max_drawdown=0.20,
            reduce_position_at=0.10,
            allow_rotation=True,
            lot_size=10,
        )
        dl.peak_nav = 1_000_000.0
        dl.current_nav = 790_000.0  # 21% dd > max
        result = dl.check(
            symbol="110002.SH",
            qty=100,
            side="BUY",
            current_positions={"110001.SH": 50},
            nav=790_000.0,
            prices={},
        )
        assert not result.passed
        assert result.adjusted_qty == 0

    def test_allow_rotation_no_bypass_without_positions(self):
        """allow_rotation=True 但无持仓时不绕过，按正常减仓逻辑处理。"""
        from aipha.risk.constraints import DrawdownLimit
        dl = DrawdownLimit(
            max_drawdown=0.20,
            reduce_position_at=0.10,
            allow_rotation=True,
            scale_factor_floor=0.0,
            lot_size=100,
        )
        dl.peak_nav = 1_000_000.0
        dl.current_nav = 870_000.0  # 13% dd
        result = dl.check(
            symbol="110002.SH",
            qty=100,
            side="BUY",
            current_positions={},  # 无持仓
            nav=870_000.0,
            prices={},
        )
        # 无持仓时按正常减仓比例，result 取决于缩减后是否达到 lot_size
        # 这里只断言不是 rotation bypass
        if result.passed:
            assert result.adjusted_qty is not None
            assert result.metadata.get("rotation_bypass") is not True


class TestDrawdownLimitPeakReset:
    """peak_reset_after_flat_days: 全仓现金 N 天后重置 peak_nav 防止死锁。"""

    def _make(self, reset_days: int = 5, max_dd: float = 0.25) -> "DrawdownLimit":
        dl = DrawdownLimit(
            max_drawdown=max_dd,
            reduce_position_at=max_dd * 0.5,
            peak_reset_after_flat_days=reset_days,
        )
        dl.peak_nav = 1_500_000.0
        dl.current_nav = 1_100_000.0  # dd ≈ 26.7%
        return dl

    def test_disabled_by_default(self):
        """peak_reset_after_flat_days=0 时不重置（向后兼容）。"""
        dl = DrawdownLimit(max_drawdown=0.25, reduce_position_at=0.125)
        dl.peak_nav = 1_500_000.0
        dl.current_nav = 1_000_000.0
        for _ in range(100):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == 1_500_000.0  # 不变

    def test_reset_after_n_flat_days(self):
        """连续 N 天无持仓且在回撤区间时，peak_nav 重置为 current_nav。"""
        dl = self._make(reset_days=5)
        for d in range(4):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == 1_500_000.0  # 还没到 5 天
        dl.update_position_state(has_any_position=False)  # 第 5 天
        assert dl.peak_nav == 1_100_000.0  # 已重置
        assert dl.get_current_drawdown() == pytest.approx(0.0)

    def test_counter_resets_on_position(self):
        """有持仓时计数器归零。"""
        dl = self._make(reset_days=5)
        for _ in range(3):
            dl.update_position_state(has_any_position=False)
        dl.update_position_state(has_any_position=True)  # 中断
        for _ in range(4):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == 1_500_000.0  # 未到 5 天连续

    def test_counter_resets_when_drawdown_recovers(self):
        """回撤恢复到 reduce_position_at 以下时计数器归零。"""
        dl = self._make(reset_days=5, max_dd=0.25)
        # dd ≈ 26.7%，超过 reduce_position_at=12.5%，所以计数器会累加
        dl.update_position_state(has_any_position=False)
        assert dl._no_position_days == 1
        # 现在恢复 NAV → dd 降到 reduce 以下
        dl.current_nav = 1_450_000.0  # dd ≈ 3.3%
        dl.update_position_state(has_any_position=False)
        assert dl._no_position_days == 0  # 归零

    def test_after_reset_buys_allowed(self):
        """重置后买入不再被回撤拦截。"""
        dl = self._make(reset_days=3)
        # 超过 max_drawdown → 买入应被阻止
        result = dl.check("A", 100, "BUY", {}, 1_100_000, {"A": 10.0})
        assert not result.passed
        # 触发重置
        for _ in range(3):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == dl.current_nav
        # 现在应允许买入
        result = dl.check("A", 100, "BUY", {}, 1_100_000, {"A": 10.0})
        assert result.passed

    def test_sell_always_allowed_during_drawdown(self):
        """回撤超限期间卖出始终放行。"""
        dl = self._make(reset_days=5)
        result = dl.check("A", 100, "SELL", {"A": 200}, 1_100_000, {"A": 10.0})
        assert result.passed

    def test_peak_nav_continues_tracking_after_reset(self):
        """重置后 peak_nav 继续正常跟踪新高。"""
        dl = self._make(reset_days=2)
        for _ in range(2):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == 1_100_000.0
        # 模拟 NAV 上升
        dl.update_nav(1_200_000.0)
        assert dl.peak_nav == 1_200_000.0

    def test_multiple_reset_cycles(self):
        """可多次重置（每次死锁都能恢复）。"""
        dl = self._make(reset_days=2)
        # 第一次重置
        for _ in range(2):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == 1_100_000.0
        # 模拟新的 peak 和新的回撤
        dl.update_nav(1_500_000.0)  # 新 peak
        dl.current_nav = 1_000_000.0  # 新回撤
        for _ in range(2):
            dl.update_position_state(has_any_position=False)
        assert dl.peak_nav == 1_000_000.0  # 第二次重置
