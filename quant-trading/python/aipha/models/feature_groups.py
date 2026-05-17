"""
aipha/models/feature_groups.py
——————————————————————————————
标准化特征分组管理，防止制度变化时特征黑箱失效。

分组用途
--------
1. 训练时按组监控特征重要性衰减（与 DriftMonitor 配合）
2. 因子 IC 归因分析（哪类特征贡献了 alpha）
3. 可复现实验：通过分组名称在配置中指定特征子集

使用方式
--------
>>> from aipha.models.feature_groups import get_feature_group, get_group_features
>>> get_feature_group("ep_ttm")
'quality'
>>> get_group_features("valuation")
['pe_ttm', 'pb', ...]
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 静态分组定义（按业务语义）
# ---------------------------------------------------------------------------

#: 核心分组 → 代表性特征列表（非穷举，用于文档与默认配置）
FEATURE_GROUPS: dict[str, list[str]] = {
    "quality": [
        "ep_ttm",       # EP = 1/PE，盈利收益率
        "bp",           # BP = 1/PB，账面价值收益率
        "cs_rank_ep",   # EP 截面排名
        "cs_rank_bp",   # BP 截面排名
        "log_circ_mv",  # 对数流通市值（规模因子）
        "cs_rank_log_mv",
    ],
    "valuation": [
        "pe_ttm",
        "pb",
        "dv_ttm",       # 股息率（TTM）
        "dv_ratio",     # 当期股息率
        "dv_ttm_clean",
        "cs_rank_pe_ttm",
        "cs_rank_pb",
        "cs_rank_dv",
        "pe_ttm_chg_5d",
        "pe_ttm_chg_20d",
        "pb_chg_5d",
        "pb_chg_20d",
    ],
    "liquidity": [
        "turnover_rate",
        "cs_rank_turnover_rate",
        "turnover_rate_chg_5d",
        "turnover_rate_chg_20d",
        "total_mv",
        "circ_mv",
        "VSUMP5",   # Alpha158 volume sum +
        "VSUMP10",
        "VSUMP20",
        "VSUMD5",   # Alpha158 volume sum -
        "VSTD5",    # Alpha158 volume std
        "VSTD10",
        "VSTD20",
        # P7 换手率增强因子
        "turnover_zscore",
        "cs_rank_turnover_zscore",
        "turnover_vol",
        "cs_rank_turnover_vol",
        "turnover_mom",
        "cs_rank_turnover_mom",
        "turnover_ivol_adj",
        "cs_rank_turnover_ivol_adj",
    ],
    "crowding": [
        "margin_buy_ratio",          # 融资买入占比
        "margin_ratio_change",       # 融资余额变化率
        "margin_balance",            # 融资余额截面排名
        "cs_rank_margin_buy",
        "short_sell_ratio",          # 融券卖出占比
    ],
    "alternative": [
        "chips_cost_concentration",  # 筹码集中度
        "chips_profit_ratio",        # 获利比例
        "chips_cost_center",         # 成本中枢
        "flow_buy_ratio",            # 主力净买入比例
        "flow_net_inflow_ratio",     # 净流入比例
        "flow_sup_buy_ratio",        # 超大单净比
        "analyst_revision_score",    # 分析师上调次数
        "analyst_coverage_change",   # 覆盖数变化
        "eps_revision_3m",           # EPS 预测修正
    ],
    "fundamental_extended": [
        # P8 基本面扩展因子
        "peg",
        "cs_rank_peg",
        "fcf_yield",
        "cs_rank_fcf_yield",
        "ocf_ni_ratio",
        "cs_rank_ocf_ni_ratio",
        "rev_growth_mom",
        "cs_rank_rev_growth_mom",
        "neg_asset_growth",
        "cs_rank_neg_asset_growth",
        "consec_growth_q",
        "cs_rank_consec_growth_q",
    ],
    "momentum": [
        "ROC5",    # Alpha158 Rate of Change 5d
        "ROC10",
        "ROC20",
        "ROC30",
        "ROC60",
        "MACD",             # 自定义 MACD 因子
        "RSRS",             # RSRS（相对强弱阻力支撑）
        "RESI5",   # Alpha158 趋势残差（反转信号）
        "RESI10",
        "RESI20",
        "CORR5",   # Alpha158 相关系数
        "CORR10",
        "CORR20",
    ],
    "behavioral": [
        "abt",              # 注意力偏差 (Abnormal Turnover)
        "max_ret",          # 彩票效应 (MAX Daily Return)
        "overreact",        # 过度反应组合 (Overreaction Composite)
        "cs_rank_abt",      # ABT 截面排名
        "cs_rank_max_ret",  # MAX 截面排名
        "cs_rank_overreact",  # Overreaction 截面排名
    ],
    "advanced_alpha": [
        "res_momentum_20d",        # 残差动量 (Gao et al. 2023)
        "res_volatility_20d",      # 残差波动
        "cs_rank_res_mom",         # 残差动量截面排名
        "cs_rank_res_vol",         # 残差波动截面排名
        "value_profit_composite",  # 价值盈利复合 (Novy-Marx 2013)
        "cs_rank_val_prof",        # 价值盈利复合截面排名
    ],
    "composite_momentum": [
        "mom_composite_sml",           # 多周期融合(5d+20d+60d)
        "mom_composite_mid_long",      # 中长期融合(10d+30d+60d)
        "reversal_composite",          # 短期反转融合(5d+10d取反)
        "vol_composite",               # 波动率融合(5d+20d+60d)
        "cs_rank_mom_composite_sml",   # 多周期融合截面排名
        "cs_rank_mom_composite_mid_long",
        "cs_rank_reversal_composite",
        "cs_rank_vol_composite",
    ],
    "event_driven": [
        "sue_score",                   # 标准化意外盈利 (SUE)
        "analyst_revision_3m",         # 分析师 EPS 修正率 (3月)
        "forecast_surprise",           # 业绩预告超预期分数
        "analyst_coverage_count",      # 分析师覆盖数 (置信度权重)
        "insider_net_buy_90d",         # 管理层净增持 (对数化)
        "insider_txn_count",           # 增减持交易笔数
    ],
}

# ---------------------------------------------------------------------------
# 前缀/关键字匹配规则（按顺序）
# ---------------------------------------------------------------------------

#: 动态匹配规则：列表中的字符串为 *子串前缀关键字*，匹配时按顺序检查
_GROUP_PATTERNS: list[tuple[str, list[str]]] = [
    ("alternative",  ["chips_", "flow_", "analyst_", "eps_revision"]),
    ("crowding",     ["margin_", "short_sell"]),
    ("valuation",    ["pe_ttm", "pb_ch", "cs_rank_pe", "cs_rank_pb", "cs_rank_dv",
                      "dv_ttm", "dv_ratio"]),
    ("quality",      ["ep_ttm", "bp", "cs_rank_ep", "cs_rank_bp",
                      "log_circ_mv", "cs_rank_log_mv"]),
    ("liquidity",    ["turnover_rate", "cs_rank_turnover", "total_mv",
                      "circ_mv", "VSUMP", "VSUMD", "VSTD"]),
    ("momentum",     ["ROC", "RESI", "CORR", "MACD", "RSRS", "RSI"]),
    ("behavioral",   ["abt", "max_ret", "overreact", "cs_rank_abt",
                      "cs_rank_max_ret", "cs_rank_overreact"]),
    ("advanced_alpha", ["res_momentum", "res_volatility", "cs_rank_res_mom",
                        "cs_rank_res_vol", "value_profit", "cs_rank_val_prof"]),
    ("composite_momentum", ["mom_composite", "reversal_composite", "vol_composite",
                            "cs_rank_mom_composite", "cs_rank_reversal_composite",
                            "cs_rank_vol_composite"]),
    ("event_driven",        ["sue_", "analyst_revision", "forecast_surprise",
                            "analyst_coverage", "insider_net_buy",
                            "insider_txn"]),
]


def get_feature_group(feature_name: str) -> str | None:
    """
    返回特征所属分组名称。

    匹配优先级：
    1. 精确匹配 FEATURE_GROUPS 中的静态列表
    2. 前缀/关键字匹配 _GROUP_PATTERNS

    Parameters
    ----------
    feature_name : str

    Returns
    -------
    str | None
        分组名称；不匹配时返回 None。
    """
    # 精确匹配（静态 FEATURE_GROUPS）
    for group, members in FEATURE_GROUPS.items():
        if feature_name in members:
            return group

    # 动态前缀匹配
    for group, patterns in _GROUP_PATTERNS:
        for pat in patterns:
            if feature_name.startswith(pat):
                return group

    return None


def get_group_features(
    group_name: str,
    feature_pool: list[str] | None = None,
) -> list[str]:
    """
    返回指定分组的特征列表。

    Parameters
    ----------
    group_name : str
        分组名称（质量/估值/流动性/拥挤度/替代/动量）。
    feature_pool : list[str] | None
        若提供，从该池中筛选属于 group_name 的特征（动态匹配）；
        若为 None，返回静态 FEATURE_GROUPS 中的代表性列表。

    Returns
    -------
    list[str]

    Raises
    ------
    KeyError
        group_name 不在已知分组时。
    """
    if group_name not in FEATURE_GROUPS:
        raise KeyError(
            f"Unknown group: {group_name!r}. "
            f"Available: {list(FEATURE_GROUPS)}"
        )

    if feature_pool is None:
        return list(FEATURE_GROUPS[group_name])

    # 动态筛选
    return [f for f in feature_pool if get_feature_group(f) == group_name]


def get_all_groups() -> dict[str, list[str]]:
    """返回所有分组的静态代表性特征列表（深拷贝）。"""
    return {g: list(members) for g, members in FEATURE_GROUPS.items()}


def validate_groups() -> dict[str, int]:
    """
    校验 FEATURE_GROUPS 完整性。

    Returns
    -------
    dict[str, int]
        各分组特征数量 {group_name: count}。

    Raises
    ------
    ValueError
        存在重复的特征名（一个特征出现在多个分组）时。
    """
    seen: dict[str, str] = {}  # feature → first_group
    duplicates: list[str] = []

    for group, members in FEATURE_GROUPS.items():
        for feat in members:
            if feat in seen:
                duplicates.append(f"{feat!r}: {seen[feat]!r} vs {group!r}")
            else:
                seen[feat] = group

    if duplicates:
        raise ValueError(
            "Duplicate features across groups:\n" + "\n".join(duplicates)
        )

    return {g: len(m) for g, m in FEATURE_GROUPS.items()}
