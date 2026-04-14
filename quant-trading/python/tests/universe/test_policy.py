"""
UniversePolicy 单元测试

TDD: 先写测试，再实现

测试覆盖：
1. 基础接口 - UniversePolicy 抽象类
2. 自动剔除 - ST/*ST/退市整理/终止上市/长期停牌
3. 手动剔除 - 从配置文件读取黑名单
4. 组合策略 - 多规则叠加
5. 剔除统计 - 按原因聚合
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from aipha.universe.policy import (
    ExclusionReason,
    ExclusionRecord,
    UniversePolicy,
    CompositeUniversePolicy,
)
from aipha.universe.exclusions import (
    STExclusionRule,
    SuspendedExclusionRule,
    ManualExclusionRule,
    DelistedExclusionRule,
    load_manual_exclusion_from_config,
)


# ============================================================================
# Mock Data
# ============================================================================


def make_stock_info(
    ts_code: str,
    name: str,
    list_status: str = "L",  # L=上市, D=退市, P=暂停上市
    is_st: bool = False,
    is_suspended: bool = False,
    suspend_days: int = 0,
) -> dict[str, Any]:
    """创建测试用股票信息"""
    return {
        "ts_code": ts_code,
        "name": name,
        "list_status": list_status,
        "is_st": is_st,
        "is_suspended": is_suspended,
        "suspend_days": suspend_days,
    }


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def normal_stocks() -> list[dict]:
    """正常股票列表"""
    return [
        make_stock_info("600000.SH", "浦发银行"),
        make_stock_info("000001.SZ", "平安银行"),
        make_stock_info("600519.SH", "贵州茅台"),
    ]


@pytest.fixture
def mixed_stocks() -> list[dict]:
    """包含各种状态的股票列表"""
    return [
        make_stock_info("600000.SH", "浦发银行"),
        make_stock_info("000001.SZ", "平安银行"),
        make_stock_info("600001.SH", "*ST某股", is_st=True),
        make_stock_info("600002.SH", "ST某股2", is_st=True),
        make_stock_info("600003.SH", "退市某股", list_status="D"),
        make_stock_info("600004.SH", "暂停某股", list_status="P"),
        make_stock_info("600005.SH", "停牌某股", is_suspended=True, suspend_days=30),
        make_stock_info("600006.SH", "短暂停牌", is_suspended=True, suspend_days=3),
    ]


# ============================================================================
# ExclusionReason Tests
# ============================================================================


class TestExclusionReason:
    """剔除原因枚举测试"""

    def test_reason_values(self):
        """剔除原因值正确"""
        assert ExclusionReason.ST.value == "ST"
        assert ExclusionReason.DELISTED.value == "DELISTED"
        assert ExclusionReason.SUSPENDED.value == "SUSPENDED"
        assert ExclusionReason.MANUAL.value == "MANUAL"


class TestExclusionRecord:
    """剔除记录测试"""

    def test_record_creation(self):
        """创建剔除记录"""
        record = ExclusionRecord(
            ts_code="600001.SH",
            name="*ST某股",
            reason=ExclusionReason.ST,
            effective_date=date(2025, 1, 6),
            detail="名称包含*ST",
        )
        assert record.ts_code == "600001.SH"
        assert record.reason == ExclusionReason.ST


# ============================================================================
# STExclusionRule Tests
# ============================================================================


class TestSTExclusionRule:
    """ST剔除规则测试"""

    def test_excludes_st_stocks(self, mixed_stocks: list[dict]):
        """剔除ST股票"""
        rule = STExclusionRule()
        trade_date = date(2025, 1, 6)

        excluded = rule.filter(mixed_stocks, trade_date)

        # 应剔除 2 只 ST 股票
        assert len(excluded) == 2
        codes = {r.ts_code for r in excluded}
        assert "600001.SH" in codes
        assert "600002.SH" in codes

    def test_all_reasons_are_st(self, mixed_stocks: list[dict]):
        """所有剔除原因都是ST"""
        rule = STExclusionRule()
        excluded = rule.filter(mixed_stocks, date(2025, 1, 6))

        for record in excluded:
            assert record.reason == ExclusionReason.ST

    def test_no_st_returns_empty(self, normal_stocks: list[dict]):
        """无ST股票返回空列表"""
        rule = STExclusionRule()
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))
        assert len(excluded) == 0


# ============================================================================
# DelistedExclusionRule Tests
# ============================================================================


class TestDelistedExclusionRule:
    """退市剔除规则测试"""

    def test_excludes_delisted(self, mixed_stocks: list[dict]):
        """剔除退市和暂停上市股票"""
        rule = DelistedExclusionRule()
        excluded = rule.filter(mixed_stocks, date(2025, 1, 6))

        # 应剔除 D 和 P 状态的股票
        assert len(excluded) == 2
        codes = {r.ts_code for r in excluded}
        assert "600003.SH" in codes  # D=退市
        assert "600004.SH" in codes  # P=暂停上市

    def test_all_reasons_are_delisted(self, mixed_stocks: list[dict]):
        """所有剔除原因都是DELISTED"""
        rule = DelistedExclusionRule()
        excluded = rule.filter(mixed_stocks, date(2025, 1, 6))

        for record in excluded:
            assert record.reason == ExclusionReason.DELISTED


# ============================================================================
# SuspendedExclusionRule Tests
# ============================================================================


class TestSuspendedExclusionRule:
    """停牌剔除规则测试"""

    def test_excludes_long_suspended(self, mixed_stocks: list[dict]):
        """剔除长期停牌（默认>=10天）"""
        rule = SuspendedExclusionRule(min_suspend_days=10)
        excluded = rule.filter(mixed_stocks, date(2025, 1, 6))

        # 只剔除停牌>=10天的
        assert len(excluded) == 1
        assert excluded[0].ts_code == "600005.SH"

    def test_custom_threshold(self, mixed_stocks: list[dict]):
        """自定义停牌天数阈值"""
        rule = SuspendedExclusionRule(min_suspend_days=1)
        excluded = rule.filter(mixed_stocks, date(2025, 1, 6))

        # 剔除所有停牌的
        assert len(excluded) == 2


# ============================================================================
# ManualExclusionRule Tests
# ============================================================================


class TestManualExclusionRule:
    """手动剔除规则测试"""

    def test_excludes_by_code(self, normal_stocks: list[dict]):
        """按代码剔除"""
        rule = ManualExclusionRule(
            exclude_codes=["600000.SH"],
            exclude_name_patterns=[],
        )
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))

        assert len(excluded) == 1
        assert excluded[0].ts_code == "600000.SH"

    def test_excludes_by_name_pattern(self, normal_stocks: list[dict]):
        """按名称模式剔除"""
        rule = ManualExclusionRule(
            exclude_codes=[],
            exclude_name_patterns=["茅台"],
        )
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))

        assert len(excluded) == 1
        assert excluded[0].ts_code == "600519.SH"

    def test_combined_code_and_pattern(self, normal_stocks: list[dict]):
        """代码和模式组合"""
        rule = ManualExclusionRule(
            exclude_codes=["600000.SH"],
            exclude_name_patterns=["茅台"],
        )
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))

        assert len(excluded) == 2

    def test_all_reasons_are_manual(self, normal_stocks: list[dict]):
        """所有剔除原因都是MANUAL"""
        rule = ManualExclusionRule(exclude_codes=["600000.SH"])
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))

        for record in excluded:
            assert record.reason == ExclusionReason.MANUAL


# ============================================================================
# CompositeUniversePolicy Tests
# ============================================================================


class TestCompositeUniversePolicy:
    """组合策略测试"""

    def test_applies_all_rules(self, mixed_stocks: list[dict]):
        """应用所有规则"""
        policy = CompositeUniversePolicy(
            rules=[
                STExclusionRule(),
                DelistedExclusionRule(),
                SuspendedExclusionRule(min_suspend_days=10),
            ]
        )

        result = policy.filter(mixed_stocks, date(2025, 1, 6))

        # 可交易的只剩：600000, 000001, 600006(短暂停牌<10天)
        assert len(result.tradable) == 3
        tradable_codes = {s["ts_code"] for s in result.tradable}
        assert "600000.SH" in tradable_codes
        assert "000001.SZ" in tradable_codes
        assert "600006.SH" in tradable_codes

    def test_exclusion_statistics(self, mixed_stocks: list[dict]):
        """剔除统计"""
        policy = CompositeUniversePolicy(
            rules=[
                STExclusionRule(),
                DelistedExclusionRule(),
            ]
        )

        result = policy.filter(mixed_stocks, date(2025, 1, 6))

        stats = result.get_exclusion_stats()
        assert stats[ExclusionReason.ST] == 2
        assert stats[ExclusionReason.DELISTED] == 2

    def test_no_duplicate_exclusions(self, mixed_stocks: list[dict]):
        """同一股票不重复剔除"""
        # 假设一只股票同时满足多个剔除条件
        policy = CompositeUniversePolicy(
            rules=[
                STExclusionRule(),
                ManualExclusionRule(exclude_codes=["600001.SH"]),  # 与ST重复
            ]
        )

        result = policy.filter(mixed_stocks, date(2025, 1, 6))

        # 600001.SH 只应被剔除一次（按第一个匹配的规则）
        excluded_codes = [r.ts_code for r in result.excluded]
        assert excluded_codes.count("600001.SH") == 1

    def test_result_includes_total_counts(self, mixed_stocks: list[dict]):
        """结果包含总数统计"""
        policy = CompositeUniversePolicy(rules=[STExclusionRule()])
        result = policy.filter(mixed_stocks, date(2025, 1, 6))

        assert result.total_count == len(mixed_stocks)
        assert result.tradable_count == len(result.tradable)
        assert result.excluded_count == len(result.excluded)


# ============================================================================
# UniversePolicy Abstract Interface Tests
# ============================================================================


class TestUniversePolicyInterface:
    """UniversePolicy 接口测试"""

    def test_policy_is_abstract(self):
        """UniversePolicy 是抽象类"""
        with pytest.raises(TypeError):
            UniversePolicy()  # type: ignore


# ============================================================================
# Config Loading Tests
# ============================================================================


class TestConfigLoading:
    """配置文件加载测试"""

    def test_load_from_existing_config(self, tmp_path: Path, normal_stocks: list[dict]):
        """从配置文件加载手动剔除规则"""
        config_content = """
items:
  - ts_code: "600000.SH"
    reason: "测试剔除"
  - name_keyword: "茅台"
    reason: "测试关键字剔除"
"""
        config_file = tmp_path / "exclude_list.yml"
        config_file.write_text(config_content, encoding="utf-8")

        rule = load_manual_exclusion_from_config(config_file)
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))

        assert len(excluded) == 2
        codes = {r.ts_code for r in excluded}
        assert "600000.SH" in codes
        assert "600519.SH" in codes

    def test_load_with_effective_period(self, tmp_path: Path, normal_stocks: list[dict]):
        """加载带生效区间的配置"""
        config_content = """
items:
  - ts_code: "600000.SH"
    reason: "测试"
    effective_from: "2025-01-01"
    effective_to: "2025-06-30"
"""
        config_file = tmp_path / "exclude_list.yml"
        config_file.write_text(config_content, encoding="utf-8")

        # 在生效区间内
        rule1 = load_manual_exclusion_from_config(config_file, trade_date=date(2025, 3, 1))
        excluded1 = rule1.filter(normal_stocks, date(2025, 3, 1))
        assert len(excluded1) == 1

        # 在生效区间外
        rule2 = load_manual_exclusion_from_config(config_file, trade_date=date(2025, 12, 1))
        excluded2 = rule2.filter(normal_stocks, date(2025, 12, 1))
        assert len(excluded2) == 0

    def test_load_nonexistent_config(self, tmp_path: Path, normal_stocks: list[dict]):
        """加载不存在的配置文件返回空规则"""
        rule = load_manual_exclusion_from_config(tmp_path / "nonexistent.yml")
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))
        assert len(excluded) == 0

    def test_load_empty_config(self, tmp_path: Path, normal_stocks: list[dict]):
        """加载空配置文件返回空规则"""
        config_file = tmp_path / "empty.yml"
        config_file.write_text("", encoding="utf-8")

        rule = load_manual_exclusion_from_config(config_file)
        excluded = rule.filter(normal_stocks, date(2025, 1, 6))
        assert len(excluded) == 0
