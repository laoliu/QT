"""tests/models/test_feature_groups.py — feature_groups 单元测试"""
from __future__ import annotations

import pytest

from aipha.models.feature_groups import (
    FEATURE_GROUPS,
    get_all_groups,
    get_feature_group,
    get_group_features,
    validate_groups,
)

# ---------------------------------------------------------------------------
# 分组完整性
# ---------------------------------------------------------------------------

#: 分组全集。写死成显式集合（而不是 ``set(FEATURE_GROUPS)``）是有意的：
#: 新增一组特征必须同时改这里，避免有人悄悄加一组进模型却没人看见。
EXPECTED_GROUPS = {
    "quality", "valuation", "liquidity", "crowding", "alternative", "momentum",
    "behavioral", "advanced_alpha",
    # ↓ be42091 重写整个模块时新增的三组（生产侧的正当扩展）
    "fundamental_extended", "composite_momentum", "event_driven",
}


def test_all_groups_defined():
    assert set(FEATURE_GROUPS) == EXPECTED_GROUPS


def test_each_group_nonempty():
    for group, members in FEATURE_GROUPS.items():
        assert len(members) >= 3, f"Group {group!r} has too few members"


def test_validate_groups_no_duplicates():
    counts = validate_groups()
    assert set(counts) == set(FEATURE_GROUPS)
    for cnt in counts.values():
        assert cnt >= 3


# ---------------------------------------------------------------------------
# get_feature_group — 精确匹配
# ---------------------------------------------------------------------------

def test_ep_ttm_is_quality():
    assert get_feature_group("ep_ttm") == "quality"


def test_pe_ttm_is_valuation():
    assert get_feature_group("pe_ttm") == "valuation"


def test_turnover_rate_is_liquidity():
    assert get_feature_group("turnover_rate") == "liquidity"


def test_margin_buy_ratio_is_crowding():
    assert get_feature_group("margin_buy_ratio") == "crowding"


def test_chips_cost_concentration_is_alternative():
    assert get_feature_group("chips_cost_concentration") == "alternative"


def test_roc5_is_momentum():
    assert get_feature_group("ROC5") == "momentum"


# ---------------------------------------------------------------------------
# get_feature_group — 前缀匹配
# ---------------------------------------------------------------------------

def test_chips_prefix_matches_alternative():
    assert get_feature_group("chips_profit_ratio") == "alternative"


def test_flow_prefix_matches_alternative():
    assert get_feature_group("flow_net_inflow_ratio") == "alternative"


def test_roc_prefix_matches_momentum():
    assert get_feature_group("ROC60") == "momentum"


def test_unknown_feature_returns_none():
    assert get_feature_group("totally_unknown_xyz_999") is None


# ---------------------------------------------------------------------------
# get_group_features
# ---------------------------------------------------------------------------

def test_get_group_features_static():
    members = get_group_features("quality")
    assert "ep_ttm" in members


def test_get_group_features_unknown_raises():
    with pytest.raises(KeyError, match="Unknown group"):
        get_group_features("nonexistent_group")


def test_get_group_features_with_pool():
    pool = ["ROC5", "ROC10", "pe_ttm", "unknown_feat", "chips_profit_ratio"]
    momentum_feats = get_group_features("momentum", feature_pool=pool)
    assert set(momentum_feats) == {"ROC5", "ROC10"}


def test_get_group_features_empty_pool():
    result = get_group_features("quality", feature_pool=[])
    assert result == []


# ---------------------------------------------------------------------------
# get_all_groups
# ---------------------------------------------------------------------------

def test_get_all_groups_returns_copy():
    all_g = get_all_groups()
    assert isinstance(all_g, dict)
    # 修改不影响原始 FEATURE_GROUPS
    all_g["quality"].append("injected_feat")
    assert "injected_feat" not in FEATURE_GROUPS["quality"]


def test_get_all_groups_covers_every_group():
    # 断言 get_all_groups() 和 FEATURE_GROUPS 同步，而不是断言一个魔数 8 ——
    # 后者每加一组特征就误报一次，且只告诉你"数量变了"，不告诉你变了哪个。
    assert set(get_all_groups()) == EXPECTED_GROUPS
