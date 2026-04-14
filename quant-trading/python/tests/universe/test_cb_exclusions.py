"""CB 专用剔除规则测试

覆盖：
- ForceRedeemExclusionRule: 强赎公告剔除 / 启发式可选 / 未触发不剔除
- NearMaturityExclusionRule: 近到期剔除 / 距到期多天不剔除 / 无到期日跳过
- 两规则组合进 CompositeUniversePolicy 联合过滤
"""
from datetime import date

import pytest

from aipha.universe.exclusions import (
    ForceRedeemExclusionRule,
    NearMaturityExclusionRule,
)
from aipha.universe.policy import (
    CompositeUniversePolicy,
    ExclusionReason,
)


TRADE_DATE = date(2024, 6, 1)


def _cb(
    ts_code="127001.SZ",
    name="测试转债",
    is_called=None,
    force_redeem_status="",
    maturity_date="",
):
    """构造模拟可转债条目"""
    meta = {}
    if is_called is not None:
        meta["is_called"] = is_called
    if force_redeem_status:
        meta["force_redeem_status"] = force_redeem_status
    if maturity_date:
        meta["maturity_date"] = maturity_date
    return {"ts_code": ts_code, "name": name, "metadata": meta}


# ============================================================
# ForceRedeemExclusionRule
# ============================================================


class TestForceRedeemExclusionRule:
    """强赎剔除规则测试（≥8 用例）"""

    def setup_method(self):
        self.rule = ForceRedeemExclusionRule()

    def test_exclude_announced_is_called_true(self):
        """is_called=True → 剔除"""
        stocks = [_cb(is_called=True, force_redeem_status="公告实施强赎")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].reason == ExclusionReason.FORCE_REDEEM

    def test_exclude_status_keyword(self):
        """force_redeem_status 含'已公告强赎' → 剔除"""
        stocks = [_cb(is_called=False, force_redeem_status="已公告强赎")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_exclude_status_keyword_partial(self):
        """force_redeem_status 含'公告强赎' → 剔除"""
        stocks = [_cb(force_redeem_status="公告强赎")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_no_exclude_heuristic_by_default(self):
        """启发式判断默认不剔除"""
        stocks = [_cb(force_redeem_status="启发式: cb_value=135.00≥130")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_heuristic_excluded_when_enabled(self):
        """check_heuristic=True 时启发式也剔除"""
        rule = ForceRedeemExclusionRule(check_heuristic=True)
        stocks = [_cb(force_redeem_status="启发式: cb_value=135.00≥130")]
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert "启发式" in records[0].detail

    def test_no_exclude_normal_cb(self):
        """正常可转债（无强赎）不剔除"""
        stocks = [_cb(is_called=False, force_redeem_status="")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_no_exclude_none_called(self):
        """is_called=None 且无关键字 → 不剔除"""
        stocks = [_cb(is_called=None, force_redeem_status="")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_no_metadata(self):
        """无 metadata 字段 → 不剔除"""
        stocks = [{"ts_code": "127001.SZ", "name": "测试转债"}]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_mixed_batch(self):
        """混合批次：3只中剔除1只"""
        stocks = [
            _cb("127001.SZ", "正常债"),
            _cb("127002.SZ", "强赎债", is_called=True, force_redeem_status="公告实施强赎"),
            _cb("127003.SZ", "另一只正常"),
        ]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].ts_code == "127002.SZ"

    def test_detail_contains_status(self):
        """剔除记录 detail 包含强赎状态"""
        stocks = [_cb(is_called=True, force_redeem_status="公告实施强赎")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert "公告实施强赎" in records[0].detail


# ============================================================
# NearMaturityExclusionRule
# ============================================================


class TestNearMaturityExclusionRule:
    """近到期剔除规则测试（≥8 用例）"""

    def setup_method(self):
        self.rule = NearMaturityExclusionRule(min_remaining_days=30)

    def test_exclude_matured(self):
        """已到期 → 剔除"""
        stocks = [_cb(maturity_date="20240501")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].reason == ExclusionReason.NEAR_MATURITY

    def test_exclude_near_maturity(self):
        """距到期15天 → 剔除"""
        stocks = [_cb(maturity_date="20240616")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert "15天" in records[0].detail

    def test_exclude_exactly_30_days(self):
        """距到期正好30天 → 剔除（≤30）"""
        # 2024-06-01 + 30 = 2024-07-01
        stocks = [_cb(maturity_date="20240701")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_no_exclude_31_days(self):
        """距到期31天 → 不剔除"""
        stocks = [_cb(maturity_date="20240702")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_no_exclude_far_maturity(self):
        """距到期1年 → 不剔除"""
        stocks = [_cb(maturity_date="20250601")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_no_maturity_date(self):
        """无到期日 → 跳过（不剔除）"""
        stocks = [_cb(maturity_date="")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_no_metadata(self):
        """无 metadata → 不剔除"""
        stocks = [{"ts_code": "127001.SZ", "name": "测试"}]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_invalid_date_format(self):
        """无效日期格式 → 跳过"""
        stocks = [_cb(maturity_date="invalid")]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 0

    def test_custom_threshold(self):
        """自定义阈值60天"""
        rule = NearMaturityExclusionRule(min_remaining_days=60)
        stocks = [_cb(maturity_date="20240715")]  # 44天
        records = rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1

    def test_mixed_batch(self):
        """混合批次"""
        stocks = [
            _cb("127001.SZ", "近到期", maturity_date="20240610"),  # 9天
            _cb("127002.SZ", "正常", maturity_date="20260601"),
            _cb("127003.SZ", "无到期日"),
        ]
        records = self.rule.filter(stocks, TRADE_DATE)
        assert len(records) == 1
        assert records[0].ts_code == "127001.SZ"


# ============================================================
# 组合测试
# ============================================================


class TestCBCompositePolicy:
    """两个 CB 规则与 CompositeUniversePolicy 联合测试"""

    def test_combined_force_redeem_and_near_maturity(self):
        """同时应用强赎剔除 + 近到期剔除"""
        policy = CompositeUniversePolicy(rules=[
            ForceRedeemExclusionRule(),
            NearMaturityExclusionRule(min_remaining_days=30),
        ])
        stocks = [
            _cb("127001.SZ", "强赎债", is_called=True, force_redeem_status="公告实施强赎"),
            _cb("127002.SZ", "近到期", maturity_date="20240610"),
            _cb("127003.SZ", "正常", maturity_date="20260601"),
        ]
        result = policy.filter(stocks, TRADE_DATE)
        assert result.tradable_count == 1
        assert result.excluded_count == 2
        assert result.tradable[0]["ts_code"] == "127003.SZ"

    def test_dedup_same_bond(self):
        """同一只同时满足两个规则 → 只剔除一次"""
        policy = CompositeUniversePolicy(rules=[
            ForceRedeemExclusionRule(),
            NearMaturityExclusionRule(min_remaining_days=30),
        ])
        stocks = [
            _cb(
                "127001.SZ", "双重风险",
                is_called=True,
                force_redeem_status="公告实施强赎",
                maturity_date="20240610",
            ),
        ]
        result = policy.filter(stocks, TRADE_DATE)
        assert result.tradable_count == 0
        assert result.excluded_count == 1  # 去重后只计1次

    def test_exclusion_stats(self):
        """剔除统计按原因分类"""
        policy = CompositeUniversePolicy(rules=[
            ForceRedeemExclusionRule(),
            NearMaturityExclusionRule(min_remaining_days=30),
        ])
        stocks = [
            _cb("127001.SZ", "强赎", is_called=True, force_redeem_status="公告实施强赎"),
            _cb("127002.SZ", "近到期", maturity_date="20240610"),
            _cb("127003.SZ", "正常", maturity_date="20260601"),
        ]
        result = policy.filter(stocks, TRADE_DATE)
        stats = result.get_exclusion_stats()
        assert stats[ExclusionReason.FORCE_REDEEM] == 1
        assert stats[ExclusionReason.NEAR_MATURITY] == 1
