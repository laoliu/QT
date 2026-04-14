"""Universe Policy E2E 测试。

覆盖:
- 引擎层 _apply_universe_filter 退市/停牌字段传递 (bug fix 验证)
- Engine + CompositeUniversePolicy 全规则联动
- FilterResult 边界场景
- ManualExclusionRule 边界: 空 pattern, 重复 code
- SuspendedExclusionRule 边界: is_suspended/suspend_days 组合
- YAML 配置完整性
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.backtest.engine import BacktestConfig, BacktestEngine
from aipha.domain.models import Bar, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.strategy.base import Strategy
from aipha.universe.exclusions import (
    DelistedExclusionRule,
    ManualExclusionRule,
    STExclusionRule,
    SuspendedExclusionRule,
    load_manual_exclusion_from_config,
)
from aipha.universe.policy import (
    CompositeUniversePolicy,
    ExclusionReason,
    ExclusionRecord,
    FilterResult,
)


def _make_bar(symbol: str, dt: date, metadata: dict | None = None) -> Bar:
    ts = datetime.combine(dt, datetime.min.time())
    return Bar(
        symbol=symbol,
        start_ts=ts,
        end_ts=ts,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=100_000,
        metadata=metadata,
    )


@dataclass
class _NopStrategy(Strategy):
    name: str = "nop"
    def on_bar(self, bars, context):
        return []


def _build_engine(rules):
    config = BacktestConfig(initial_capital=100_000)
    fee_model = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00002),
    )
    return BacktestEngine(
        config=config,
        strategy=_NopStrategy(),
        fee_model=fee_model,
        fill_model=NextBarOpenFillModel(),
        slippage=FixedBpsSlippageModel(bps=3),
        rules=MarketRulesCNEquity(),
        universe_policy=CompositeUniversePolicy(rules=rules),
    )


# ============================================================================
# 引擎层字段传递验证 (bug fix regression)
# ============================================================================

class TestEngineUniverseFieldPassing:
    """验证 _apply_universe_filter 正确传递所有 metadata 字段。"""

    def test_delisted_stock_filtered(self):
        """list_status='D' 的退市股票应被 DelistedExclusionRule 剔除。"""
        engine = _build_engine([DelistedExclusionRule()])
        bars = {
            "000001.SZ": _make_bar("000001.SZ", date(2024, 6, 3),
                                   metadata={"name": "正常股", "list_status": "L"}),
            "000099.SZ": _make_bar("000099.SZ", date(2024, 6, 3),
                                   metadata={"name": "退市股", "list_status": "D"}),
        }
        tradable = engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert "000001.SZ" in tradable
        assert "000099.SZ" not in tradable

    def test_paused_stock_filtered(self):
        """list_status='P' 的暂停上市股票应被剔除。"""
        engine = _build_engine([DelistedExclusionRule()])
        bars = {
            "600001.SH": _make_bar("600001.SH", date(2024, 6, 3),
                                   metadata={"name": "暂停股", "list_status": "P"}),
        }
        tradable = engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert "600001.SH" not in tradable

    def test_suspended_stock_filtered(self):
        """is_suspended=True 且 suspend_days>=10 的停牌股应被剔除。"""
        engine = _build_engine([SuspendedExclusionRule(min_suspend_days=10)])
        bars = {
            "000001.SZ": _make_bar("000001.SZ", date(2024, 6, 3),
                                   metadata={"name": "正常", "is_suspended": False}),
            "000002.SZ": _make_bar("000002.SZ", date(2024, 6, 3),
                                   metadata={"name": "停牌股", "is_suspended": True,
                                             "suspend_days": 30}),
        }
        tradable = engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert "000001.SZ" in tradable
        assert "000002.SZ" not in tradable

    def test_suspended_below_threshold_passes(self):
        """停牌天数未达阈值 → 不剔除。"""
        engine = _build_engine([SuspendedExclusionRule(min_suspend_days=10)])
        bars = {
            "000002.SZ": _make_bar("000002.SZ", date(2024, 6, 3),
                                   metadata={"name": "短停牌", "is_suspended": True,
                                             "suspend_days": 5}),
        }
        tradable = engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert "000002.SZ" in tradable

    def test_metadata_none_defaults_safe(self):
        """metadata=None 时不崩溃，默认通过所有过滤。"""
        engine = _build_engine([STExclusionRule(), DelistedExclusionRule(),
                                SuspendedExclusionRule()])
        bars = {
            "000001.SZ": _make_bar("000001.SZ", date(2024, 6, 3), metadata=None),
        }
        tradable = engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert "000001.SZ" in tradable

    def test_composite_all_rules(self):
        """ST + 退市 + 停牌 + 手动 全部联动。"""
        engine = _build_engine([
            STExclusionRule(),
            DelistedExclusionRule(),
            SuspendedExclusionRule(min_suspend_days=10),
            ManualExclusionRule(exclude_codes=["600999.SH"]),
        ])
        bars = {
            "000001.SZ": _make_bar("000001.SZ", date(2024, 6, 3),
                                   metadata={"name": "平安银行", "list_status": "L"}),
            "000002.SZ": _make_bar("000002.SZ", date(2024, 6, 3),
                                   metadata={"name": "ST万科", "is_st": True, "list_status": "L"}),
            "000003.SZ": _make_bar("000003.SZ", date(2024, 6, 3),
                                   metadata={"name": "退市股", "list_status": "D"}),
            "000004.SZ": _make_bar("000004.SZ", date(2024, 6, 3),
                                   metadata={"name": "停牌股", "is_suspended": True,
                                             "suspend_days": 20, "list_status": "L"}),
            "600999.SH": _make_bar("600999.SH", date(2024, 6, 3),
                                   metadata={"name": "黑名单", "list_status": "L"}),
            "601288.SH": _make_bar("601288.SH", date(2024, 6, 3),
                                   metadata={"name": "农业银行", "list_status": "L"}),
        }
        tradable = engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert tradable == {"000001.SZ", "601288.SH"}

    def test_exclusion_stats_accumulated(self):
        """剔除统计在 _exclusion_stats 中正确累积。"""
        engine = _build_engine([STExclusionRule(), DelistedExclusionRule()])
        bars = {
            "000001.SZ": _make_bar("000001.SZ", date(2024, 6, 3),
                                   metadata={"name": "ST股1", "is_st": True}),
            "000002.SZ": _make_bar("000002.SZ", date(2024, 6, 3),
                                   metadata={"name": "退市股", "list_status": "D"}),
            "000003.SZ": _make_bar("000003.SZ", date(2024, 6, 3),
                                   metadata={"name": "正常"}),
        }
        engine._apply_universe_filter(bars, date(2024, 6, 3))
        assert engine._exclusion_stats.get("ST", 0) >= 1
        assert engine._exclusion_stats.get("DELISTED", 0) >= 1


# ============================================================================
# FilterResult 边界
# ============================================================================

class TestFilterResultEdge:
    """FilterResult 的边界场景。"""

    def test_empty_excluded(self):
        """空 excluded → stats 为空 dict。"""
        fr = FilterResult(
            tradable=[{"ts_code": "000001.SZ"}],
            excluded=[],
            total_count=1,
            trade_date=date(2024, 1, 1),
        )
        assert fr.get_exclusion_stats() == {}
        assert fr.tradable_count == 1
        assert fr.excluded_count == 0

    def test_empty_tradable(self):
        """全部被剔除 → tradable_count=0。"""
        fr = FilterResult(
            tradable=[],
            excluded=[
                ExclusionRecord("000001.SZ", "ST股", ExclusionReason.ST, date(2024, 1, 1)),
            ],
            total_count=1,
            trade_date=date(2024, 1, 1),
        )
        assert fr.tradable_count == 0
        assert fr.excluded_count == 1

    def test_stats_count_by_reason(self):
        """get_exclusion_stats 按原因分类计数。"""
        excluded = [
            ExclusionRecord("a", "st1", ExclusionReason.ST, date(2024, 1, 1)),
            ExclusionRecord("b", "st2", ExclusionReason.ST, date(2024, 1, 1)),
            ExclusionRecord("c", "del1", ExclusionReason.DELISTED, date(2024, 1, 1)),
        ]
        fr = FilterResult(tradable=[], excluded=excluded, total_count=3,
                          trade_date=date(2024, 1, 1))
        stats = fr.get_exclusion_stats()
        assert stats[ExclusionReason.ST] == 2
        assert stats[ExclusionReason.DELISTED] == 1

    def test_tradable_preserves_extra_fields(self):
        """tradable 列表保留原始 dict 的额外字段。"""
        stocks = [{"ts_code": "000001.SZ", "name": "X", "industry": "银行", "extra": 42}]
        fr = FilterResult(tradable=stocks, excluded=[], total_count=1,
                          trade_date=date(2024, 1, 1))
        assert fr.tradable[0]["extra"] == 42


# ============================================================================
# SuspendedExclusionRule 边界
# ============================================================================

class TestSuspendedExclusionEdge:
    """停牌规则的边界组合。"""

    def test_suspended_true_zero_days(self):
        """is_suspended=True 但 suspend_days=0 → 不剔除（达不到阈值）。"""
        rule = SuspendedExclusionRule(min_suspend_days=10)
        excluded = rule.filter(
            [{"ts_code": "X", "name": "X", "is_suspended": True, "suspend_days": 0}],
            date(2024, 1, 1),
        )
        assert len(excluded) == 0

    def test_high_days_not_suspended(self):
        """is_suspended=False 但 suspend_days=100 → 不剔除。"""
        rule = SuspendedExclusionRule(min_suspend_days=10)
        excluded = rule.filter(
            [{"ts_code": "X", "name": "X", "is_suspended": False, "suspend_days": 100}],
            date(2024, 1, 1),
        )
        assert len(excluded) == 0

    def test_exact_threshold(self):
        """停牌天数恰好等于阈值 → 剔除。"""
        rule = SuspendedExclusionRule(min_suspend_days=10)
        excluded = rule.filter(
            [{"ts_code": "X", "name": "X", "is_suspended": True, "suspend_days": 10}],
            date(2024, 1, 1),
        )
        assert len(excluded) == 1


# ============================================================================
# ManualExclusionRule 边界
# ============================================================================

class TestManualExclusionEdge:
    """手动剔除规则边界场景。"""

    def test_empty_pattern_matches_nothing(self):
        """空列表 → 不剔除任何股票。"""
        rule = ManualExclusionRule(exclude_codes=[], exclude_name_patterns=[])
        excluded = rule.filter(
            [{"ts_code": "000001.SZ", "name": "正常"}],
            date(2024, 1, 1),
        )
        assert len(excluded) == 0

    def test_duplicate_codes(self):
        """重复代码只剔除一次。"""
        rule = ManualExclusionRule(
            exclude_codes=["000001.SZ", "000001.SZ"],
            exclude_name_patterns=[],
        )
        stocks = [{"ts_code": "000001.SZ", "name": "X"}]
        excluded = rule.filter(stocks, date(2024, 1, 1))
        # 代码匹配一次就 break，所以只产生一条记录
        assert len(excluded) == 1

    def test_name_pattern_partial_match(self):
        """名称包含模式 → 匹配。"""
        rule = ManualExclusionRule(
            exclude_codes=[],
            exclude_name_patterns=["退市"],
        )
        stocks = [
            {"ts_code": "000001.SZ", "name": "退市长油"},
            {"ts_code": "000002.SZ", "name": "正常股票"},
        ]
        excluded = rule.filter(stocks, date(2024, 1, 1))
        assert len(excluded) == 1
        assert excluded[0].ts_code == "000001.SZ"


# ============================================================================
# YAML 手动排除配置完整性
# ============================================================================

class TestManualExclusionConfig:
    """load_manual_exclusion_from_config 边界。"""

    def test_load_with_effective_range(self, tmp_path):
        """YAML 含 effective_from/to → 日期外不加载。"""
        content = """
items:
  - ts_code: "000001.SZ"
    reason: "测试"
    effective_from: "2024-01-01"
    effective_to: "2024-06-30"
"""
        p = tmp_path / "exclude.yml"
        p.write_text(content, encoding="utf-8")
        rule = load_manual_exclusion_from_config(p, trade_date=date(2024, 3, 15))
        assert "000001.SZ" in rule.exclude_codes

    def test_load_outside_effective_range(self, tmp_path):
        """trade_date 在 effective_to 之后 → 不加载。"""
        content = """
items:
  - ts_code: "000001.SZ"
    reason: "测试"
    effective_from: "2024-01-01"
    effective_to: "2024-06-30"
"""
        p = tmp_path / "exclude.yml"
        p.write_text(content, encoding="utf-8")
        rule = load_manual_exclusion_from_config(p, trade_date=date(2025, 1, 1))
        assert "000001.SZ" not in rule.exclude_codes

    def test_load_no_trade_date_includes_all(self, tmp_path):
        """trade_date=None → 不检查日期范围，全部加载。"""
        content = """
items:
  - ts_code: "000001.SZ"
    reason: "测试"
    effective_from: "2024-01-01"
    effective_to: "2024-06-30"
"""
        p = tmp_path / "exclude.yml"
        p.write_text(content, encoding="utf-8")
        rule = load_manual_exclusion_from_config(p, trade_date=None)
        assert "000001.SZ" in rule.exclude_codes

    def test_load_name_keyword(self, tmp_path):
        """name_keyword 类型条目加载到 exclude_name_patterns。"""
        content = """
items:
  - name_keyword: "退市"
    reason: "退市整理"
"""
        p = tmp_path / "exclude.yml"
        p.write_text(content, encoding="utf-8")
        rule = load_manual_exclusion_from_config(p)
        assert "退市" in rule.exclude_name_patterns

    def test_load_empty_items(self, tmp_path):
        """空 items → 规则为空。"""
        content = "items: []\n"
        p = tmp_path / "exclude.yml"
        p.write_text(content, encoding="utf-8")
        rule = load_manual_exclusion_from_config(p)
        assert rule.exclude_codes == []
        assert rule.exclude_name_patterns == []


# ============================================================================
# CompositeUniversePolicy 规则顺序与去重
# ============================================================================

class TestCompositeDedup:
    """验证 CompositeUniversePolicy 的去重逻辑。"""

    def test_dedup_first_rule_wins(self):
        """同一股票命中多个规则 → 只记录第一个原因。"""
        policy = CompositeUniversePolicy(rules=[
            STExclusionRule(),
            ManualExclusionRule(exclude_codes=["000001.SZ"]),
        ])
        stocks = [{"ts_code": "000001.SZ", "name": "ST股", "is_st": True}]
        result = policy.filter(stocks, date(2024, 1, 1))
        assert result.excluded_count == 1
        assert result.excluded[0].reason == ExclusionReason.ST

    def test_large_stock_pool_no_error(self):
        """1000 只股票的大池子不报错。"""
        policy = CompositeUniversePolicy(rules=[STExclusionRule()])
        stocks = [{"ts_code": f"{i:06d}.SZ", "name": f"Normal{i}"} for i in range(1000)]
        result = policy.filter(stocks, date(2024, 1, 1))
        assert result.tradable_count == 1000
        assert result.excluded_count == 0


# ============================================================================
# ExclusionRecord frozen 验证
# ============================================================================

class TestExclusionRecordFrozen:
    def test_frozen(self):
        """ExclusionRecord 不可变。"""
        rec = ExclusionRecord("000001.SZ", "X", ExclusionReason.ST, date(2024, 1, 1))
        with pytest.raises((AttributeError, TypeError)):
            rec.ts_code = "changed"
