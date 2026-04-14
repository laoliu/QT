"""tests/risk/test_crowding_limit.py — CrowdingLimit 单元测试"""
from __future__ import annotations

import pytest

from aipha.risk.crowding_limit import CrowdingLimit, CrowdingResult


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

PORTFOLIO_A = {"000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"}
PORTFOLIO_B = {"000001.SZ", "000002.SZ", "000006.SZ", "000007.SZ", "000008.SZ"}
# A∩B = {000001, 000002}, A∪B = 8 → Jaccard = 2/8 = 0.25


# ---------------------------------------------------------------------------
# 构造参数校验
# ---------------------------------------------------------------------------

def test_invalid_max_jaccard_zero():
    with pytest.raises(ValueError, match="max_jaccard"):
        CrowdingLimit(max_jaccard=0.0)


def test_invalid_max_jaccard_above_one():
    with pytest.raises(ValueError, match="max_jaccard"):
        CrowdingLimit(max_jaccard=1.1)


# ---------------------------------------------------------------------------
# get_jaccard 计算
# ---------------------------------------------------------------------------

def test_jaccard_identical_sets():
    limit = CrowdingLimit()
    assert limit.get_jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets():
    limit = CrowdingLimit()
    assert limit.get_jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap():
    limit = CrowdingLimit()
    # intersection=2, union=8
    j = limit.get_jaccard(PORTFOLIO_A, PORTFOLIO_B)
    assert abs(j - 2 / 8) < 1e-9


def test_jaccard_empty_sets():
    limit = CrowdingLimit()
    assert limit.get_jaccard(set(), set()) == 0.0


def test_jaccard_one_empty():
    limit = CrowdingLimit()
    assert limit.get_jaccard({"a"}, set()) == 0.0


# ---------------------------------------------------------------------------
# check — 无参考策略（空参考）
# ---------------------------------------------------------------------------

def test_check_no_reference_passes():
    limit = CrowdingLimit(max_jaccard=0.30)
    result = limit.check(PORTFOLIO_A)
    assert result.passed is True
    assert result.jaccard == 0.0


def test_check_empty_portfolio():
    limit = CrowdingLimit(max_jaccard=0.30)
    result = limit.check(set())
    assert result.passed is True
    assert "empty" in result.warnings[0].lower()


# ---------------------------------------------------------------------------
# check — 有参考策略
# ---------------------------------------------------------------------------

def test_check_below_threshold_passes():
    # Jaccard = 0.25, threshold = 0.30 → pass
    limit = CrowdingLimit(
        max_jaccard=0.30,
        reference_portfolios={"mainstream": PORTFOLIO_B},
    )
    result = limit.check(PORTFOLIO_A)
    assert result.passed is True
    assert abs(result.jaccard - 2 / 8) < 1e-9


def test_check_above_threshold_fails():
    # Complete overlap → Jaccard = 1.0 > 0.30
    limit = CrowdingLimit(
        max_jaccard=0.30,
        reference_portfolios={"mainstream": PORTFOLIO_A},
    )
    result = limit.check(PORTFOLIO_A)
    assert result.passed is False
    assert result.jaccard == 1.0


def test_check_worst_reference_identified():
    ref1 = {"000001.SZ"}
    ref2 = PORTFOLIO_A  # 完全重叠
    limit = CrowdingLimit(
        max_jaccard=0.30,
        reference_portfolios={"small_ref": ref1, "full_ref": ref2},
    )
    result = limit.check(PORTFOLIO_A)
    assert result.worst_reference == "full_ref"


# ---------------------------------------------------------------------------
# add_reference 动态添加
# ---------------------------------------------------------------------------

def test_add_reference_affects_check():
    limit = CrowdingLimit(max_jaccard=0.30)
    assert limit.check(PORTFOLIO_A).passed is True
    limit.add_reference("new_ref", PORTFOLIO_A)
    # 完全重叠 → 不通过
    assert limit.check(PORTFOLIO_A).passed is False


def test_add_reference_empty_name_raises():
    limit = CrowdingLimit()
    with pytest.raises(ValueError, match="name"):
        limit.add_reference("", PORTFOLIO_A)


# ---------------------------------------------------------------------------
# to_dict 序列化
# ---------------------------------------------------------------------------

def test_crowding_result_to_dict():
    limit = CrowdingLimit(
        max_jaccard=0.30,
        reference_portfolios={"ref": PORTFOLIO_B},
    )
    result = limit.check(PORTFOLIO_A)
    d = result.to_dict()
    assert set(d.keys()) >= {"passed", "jaccard", "max_jaccard",
                             "crowding_score", "worst_reference", "warnings"}
    assert d["max_jaccard"] == 0.30
