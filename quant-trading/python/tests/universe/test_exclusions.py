"""
股票池剔除规则测试

测试覆盖：
- STExclusionRule: is_st/名称模式/正常股
- DelistedExclusionRule: 退市/暂停/正常
- SuspendedExclusionRule: 停牌天数/未停牌/低于阈值
- ManualExclusionRule: 代码黑名单/名称匹配/空规则
- CompositeUniversePolicy: 组合/去重/统计
- load_manual_exclusion_from_config: 文件加载/缺失文件/区间过滤
"""

from datetime import date
from pathlib import Path

import pytest

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
)


TRADE_DATE = date(2024, 6, 1)


def _stock(
    ts_code="000001.SZ",
    name="平安银行",
    is_st=False,
    list_status="L",
    is_suspended=False,
    suspend_days=0,
):
    return {
        "ts_code": ts_code,
        "name": name,
        "is_st": is_st,
        "list_status": list_status,
        "is_suspended": is_suspended,
        "suspend_days": suspend_days,
    }


# ============================================================
# STExclusionRule
# ============================================================


class TestSTExclusionRule:
    def setup_method(self):
        self.rule = STExclusionRule()

    def test_exclude_by_is_st_flag(self):
        stocks = [_stock(is_st=True, name="ST某某")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].reason == ExclusionReason.ST

    def test_exclude_by_name_pattern(self):
        """名称含 ST 应剔除（即使 is_st=False）"""
        stocks = [_stock(is_st=False, name="*ST搜特")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_pass_normal_stock(self):
        stocks = [_stock(is_st=False, name="贵州茅台")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_multiple_stocks(self):
        stocks = [
            _stock("A", "ST甲", is_st=True),
            _stock("B", "正常乙", is_st=False),
            _stock("C", "*ST丙", is_st=False),
        ]
        records = self.rule.filter(stocks, TRADE_DATE)
        codes = {r.ts_code for r in records}
        assert "A" in codes
        assert "C" in codes
        assert "B" not in codes

    def test_empty_list(self):
        assert self.rule.filter([], TRADE_DATE) == []

    def test_record_fields(self):
        stocks = [_stock("000001.SZ", "ST平安", is_st=True)]
        records = self.rule.filter(stocks, TRADE_DATE)
        r = records[0]
        assert r.ts_code == "000001.SZ"
        assert r.name == "ST平安"
        assert r.reason == ExclusionReason.ST
        assert r.effective_date == TRADE_DATE


# ============================================================
# DelistedExclusionRule
# ============================================================


class TestDelistedExclusionRule:
    def setup_method(self):
        self.rule = DelistedExclusionRule()

    def test_exclude_delisted(self):
        stocks = [_stock(list_status="D", name="退市甲")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].reason == ExclusionReason.DELISTED
        assert "退市" in records[0].detail

    def test_exclude_paused(self):
        stocks = [_stock(list_status="P", name="暂停甲")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert "暂停" in records[0].detail

    def test_pass_listed(self):
        stocks = [_stock(list_status="L")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_unknown_status(self):
        stocks = [_stock(list_status="X")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_empty_list(self):
        assert self.rule.filter([], TRADE_DATE) == []


# ============================================================
# SuspendedExclusionRule
# ============================================================


class TestSuspendedExclusionRule:
    def test_default_threshold(self):
        rule = SuspendedExclusionRule()
        assert rule.min_suspend_days == 10

    def test_exclude_long_suspension(self):
        rule = SuspendedExclusionRule(min_suspend_days=10)
        stocks = [_stock(is_suspended=True, suspend_days=15)]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].reason == ExclusionReason.SUSPENDED
        assert "15" in records[0].detail

    def test_pass_short_suspension(self):
        rule = SuspendedExclusionRule(min_suspend_days=10)
        stocks = [_stock(is_suspended=True, suspend_days=5)]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_pass_not_suspended(self):
        rule = SuspendedExclusionRule()
        stocks = [_stock(is_suspended=False, suspend_days=0)]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_exact_threshold(self):
        rule = SuspendedExclusionRule(min_suspend_days=10)
        stocks = [_stock(is_suspended=True, suspend_days=10)]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1  # >= threshold

    def test_empty_list(self):
        assert SuspendedExclusionRule().filter([], TRADE_DATE) == []


# ============================================================
# ManualExclusionRule
# ============================================================


class TestManualExclusionRule:
    def test_exclude_by_code(self):
        rule = ManualExclusionRule(exclude_codes=["000001.SZ", "000002.SZ"])
        stocks = [
            _stock("000001.SZ", "平安银行"),
            _stock("000003.SZ", "正常股"),
        ]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].ts_code == "000001.SZ"
        assert records[0].reason == ExclusionReason.MANUAL

    def test_exclude_by_name_pattern(self):
        rule = ManualExclusionRule(exclude_name_patterns=["蓝盾", "搜特"])
        stocks = [
            _stock("A", "蓝盾股份"),
            _stock("B", "搜特科技"),
            _stock("C", "正常股"),
        ]
        records = rule.filter(stocks, TRADE_DATE)
        codes = {r.ts_code for r in records}
        assert codes == {"A", "B"}

    def test_code_takes_priority_over_name(self):
        """同一股票代码和名称都匹配时，代码优先，不重复"""
        rule = ManualExclusionRule(
            exclude_codes=["000001.SZ"],
            exclude_name_patterns=["平安"],
        )
        stocks = [_stock("000001.SZ", "平安银行")]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_empty_rules(self):
        rule = ManualExclusionRule()
        stocks = [_stock("000001.SZ", "平安银行")]
        assert rule.filter(stocks, TRADE_DATE) == []

    def test_detail_contains_type(self):
        rule = ManualExclusionRule(exclude_codes=["000001.SZ"])
        stocks = [_stock("000001.SZ", "平安银行")]
        records = rule.filter(stocks, TRADE_DATE)
        assert "手动" in records[0].detail or "黑名单" in records[0].detail

    def test_name_pattern_detail(self):
        rule = ManualExclusionRule(exclude_name_patterns=["蓝盾"])
        stocks = [_stock("A", "蓝盾股份")]
        records = rule.filter(stocks, TRADE_DATE)
        assert "蓝盾" in records[0].detail


# ============================================================
# CompositeUniversePolicy
# ============================================================


class TestCompositeUniversePolicy:
    def test_combine_rules(self):
        policy = CompositeUniversePolicy(
            rules=[
                STExclusionRule(),
                DelistedExclusionRule(),
            ]
        )
        stocks = [
            _stock("A", "ST甲", is_st=True, list_status="L"),
            _stock("B", "退市乙", is_st=False, list_status="D"),
            _stock("C", "正常丙", is_st=False, list_status="L"),
        ]
        result = policy.filter(stocks, TRADE_DATE)
        excluded_codes = {r.ts_code for r in result.excluded}
        assert "A" in excluded_codes
        assert "B" in excluded_codes
        assert "C" not in excluded_codes

    def test_dedup_same_code(self):
        """同一代码被多条规则命中应去重"""
        policy = CompositeUniversePolicy(
            rules=[
                STExclusionRule(),
                ManualExclusionRule(exclude_codes=["000001.SZ"]),
            ]
        )
        stocks = [_stock("000001.SZ", "ST平安", is_st=True)]
        result = policy.filter(stocks, TRADE_DATE)
        codes = [r.ts_code for r in result.excluded]
        assert codes.count("000001.SZ") == 1

    def test_tradable_count(self):
        policy = CompositeUniversePolicy(rules=[STExclusionRule()])
        stocks = [
            _stock("A", "ST甲", is_st=True),
            _stock("B", "正常乙"),
            _stock("C", "正常丙"),
        ]
        result = policy.filter(stocks, TRADE_DATE)
        assert len(result.tradable) == 2
        assert result.total_count == 3

    def test_empty_rules(self):
        policy = CompositeUniversePolicy(rules=[])
        stocks = [_stock("A", "正常甲")]
        result = policy.filter(stocks, TRADE_DATE)
        assert len(result.tradable) == 1

    def test_empty_stocks(self):
        policy = CompositeUniversePolicy(rules=[STExclusionRule()])
        result = policy.filter([], TRADE_DATE)
        assert len(result.tradable) == 0
        assert len(result.excluded) == 0


# ============================================================
# load_manual_exclusion_from_config
# ============================================================


class TestLoadManualExclusionConfig:
    def test_missing_file_returns_empty_rule(self, tmp_path):
        path = tmp_path / "nonexistent.yml"
        rule = load_manual_exclusion_from_config(path)
        assert isinstance(rule, ManualExclusionRule)
        assert rule.exclude_codes == []
        assert rule.exclude_name_patterns == []

    def test_load_from_yaml(self, tmp_path):
        config_path = tmp_path / "exclude.yml"
        config_path.write_text(
            "items:\n"
            "  - ts_code: '000001.SZ'\n"
            "  - name_keyword: '蓝盾'\n",
            encoding="utf-8",
        )
        rule = load_manual_exclusion_from_config(config_path)
        assert "000001.SZ" in rule.exclude_codes
        assert "蓝盾" in rule.exclude_name_patterns

    def test_effective_date_filtering(self, tmp_path):
        config_path = tmp_path / "exclude.yml"
        config_path.write_text(
            "items:\n"
            "  - ts_code: 'ACTIVE'\n"
            "    effective_from: '2024-01-01'\n"
            "    effective_to: '2024-12-31'\n"
            "  - ts_code: 'EXPIRED'\n"
            "    effective_from: '2023-01-01'\n"
            "    effective_to: '2023-06-30'\n",
            encoding="utf-8",
        )
        rule = load_manual_exclusion_from_config(
            config_path, trade_date=date(2024, 6, 1)
        )
        assert "ACTIVE" in rule.exclude_codes
        assert "EXPIRED" not in rule.exclude_codes
