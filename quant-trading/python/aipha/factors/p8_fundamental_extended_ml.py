"""P8 基本面扩展因子 — ML 训练管线兼容版本.

来源: 灵均投资"基本面+量价融合"方法论 + 石川《因子投资》
参考: EQ-S03 基本面因子扩展（ROE/营收/PEG）

因子清单:
  P0 优先:
    - peg: PEG = PE_TTM / max(g, 1), g=扣非净利增速
    - fcf_yield: 自由现金流收益率 = 每股经营现金流 / 股价
    - ocf_ni_ratio: 现金流/利润比 = 每股经营现金流 / 每股收益
    - rev_growth_mom: 营收增速动量 = q_sales_yoy 的时序差分（加速度）
  P1:
    - neg_asset_growth: 负资产增长率（FF5 CMA因子，低增长=高收益）
    - consec_growth_q: 连续营收正增长季度数

前置依赖: 需要 deep_fundamental 已启用（dt_netprofit_yoy/ocfps/eps 等列存在）。
所有因子以面板数据 (datetime × symbol) 格式输出，含原始值 + 截面排名。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p8_fundamental_factors",
    "P8_FACTOR_NAMES",
]

P8_FACTOR_NAMES: list[str] = [
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
]


def add_p8_fundamental_factors(df: pd.DataFrame) -> int:
    """向面板 DataFrame 添加 P8 基本面扩展因子。

    前置条件: deep_fundamental 已启用（fina_* 列存在）。

    Args:
        df: 面板数据，需含 datetime/symbol/close/pe_ttm + deep fundamental 列。
            **就地修改** (in-place)。

    Returns:
        新增因子列数。
    """
    added = 0
    added += _add_peg(df)
    added += _add_fcf_yield(df)
    added += _add_ocf_ni_ratio(df)
    added += _add_rev_growth_mom(df)
    added += _add_neg_asset_growth(df)
    added += _add_consec_growth_q(df)
    logger.info("P8 基本面扩展因子: 新增 %d 列", added)
    return added


def _add_peg(df: pd.DataFrame) -> int:
    """PEG = PE_TTM / max(g, 1), g=扣非净利增速.

    Peter Lynch: PEG < 1 → 成长被低估。
    高 PEG → 估值过高 or 增速慢 → 预期收益低（负向因子，取倒数排名）。
    """
    pe_col = "pe_ttm"
    g_col = "dt_netprofit_yoy"
    if pe_col not in df.columns:
        logger.warning("P8 PEG: 无 %s 列，跳过", pe_col)
        return 0
    if g_col not in df.columns:
        # 尝试降级到 netprofit_yoy
        g_col = "netprofit_yoy"
        if g_col not in df.columns:
            logger.warning("P8 PEG: 无增速列，跳过")
            return 0

    pe = df[pe_col].clip(lower=0)  # 负 PE 无意义
    g = df[g_col].clip(lower=1.0)  # 避免除零/负增速
    raw_peg = pe / g
    # Winsorize 极端值
    upper = raw_peg.quantile(0.995)
    df["peg"] = raw_peg.clip(upper=upper)
    df["cs_rank_peg"] = df.groupby("datetime")["peg"].rank(pct=True)
    return 2


def _add_fcf_yield(df: pd.DataFrame) -> int:
    """自由现金流收益率 = 每股经营现金流 / 股价.

    FCF Yield 是真实盈利质量的金标准。高 FCF Yield → 现金流充裕且估值低。
    用 ocfps 近似 FCF（完整 FCF 需 CapEx 数据，不在标准 fina_indicator 中）。
    """
    ocf_col = "ocfps"
    price_col = "close"
    if ocf_col not in df.columns:
        logger.warning("P8 FCF Yield: 无 %s 列，跳过", ocf_col)
        return 0
    if price_col not in df.columns:
        logger.warning("P8 FCF Yield: 无 %s 列，跳过", price_col)
        return 0

    price_safe = df[price_col].replace(0, np.nan)
    raw = df[ocf_col] / price_safe
    upper = raw.quantile(0.995)
    lower = raw.quantile(0.005)
    df["fcf_yield"] = raw.clip(lower=lower, upper=upper)
    df["cs_rank_fcf_yield"] = df.groupby("datetime")["fcf_yield"].rank(pct=True)
    return 2


def _add_ocf_ni_ratio(df: pd.DataFrame) -> int:
    """现金流利润比 = 每股经营现金流 / 每股收益.

    OCF/NI > 1 表示利润有充足现金流支撑（高质量盈利）。
    低 OCF/NI → 盈利靠应计而非现金 → 盈利质量差（Sloan 1996）。
    """
    ocf_col = "ocfps"
    eps_col = "eps"
    if ocf_col not in df.columns or eps_col not in df.columns:
        logger.warning("P8 OCF/NI: 缺少 %s 或 %s，跳过", ocf_col, eps_col)
        return 0

    eps_safe = df[eps_col].replace(0, np.nan)
    raw = df[ocf_col] / eps_safe
    upper = raw.quantile(0.995)
    lower = raw.quantile(0.005)
    df["ocf_ni_ratio"] = raw.clip(lower=lower, upper=upper)
    df["cs_rank_ocf_ni_ratio"] = df.groupby("datetime")["ocf_ni_ratio"].rank(
        pct=True
    )
    return 2


def _add_rev_growth_mom(df: pd.DataFrame) -> int:
    """营收增速动量 = q_sales_yoy 的时序差分（季度加速度）.

    营收增速的变化比绝对增速更有预测力。
    加速增长（正动量）→ 超预期概率高 → 正向因子。
    """
    g_col = "q_sales_yoy"
    if g_col not in df.columns:
        g_col = "or_yoy"
        if g_col not in df.columns:
            logger.warning("P8 Rev Growth Mom: 无营收增速列，跳过")
            return 0

    # 按 symbol 做时序差分（季报频率已 forward-fill 到日频）
    df["rev_growth_mom"] = df.groupby("symbol")[g_col].transform(
        lambda s: s.diff(60)  # 约 1 个季度的交易日
    )
    upper = df["rev_growth_mom"].quantile(0.995)
    lower = df["rev_growth_mom"].quantile(0.005)
    df["rev_growth_mom"] = df["rev_growth_mom"].clip(lower=lower, upper=upper)
    df["cs_rank_rev_growth_mom"] = df.groupby("datetime")["rev_growth_mom"].rank(
        pct=True
    )
    return 2


def _add_neg_asset_growth(df: pd.DataFrame) -> int:
    """负资产增长率 (FF5 CMA 因子).

    Fama-French 2015: 低资产增长(保守投资)的公司平均收益高于高资产增长(激进投资)的公司。
    取负使其方向一致：高值 = 保守投资 = 预期收益高。
    """
    ag_col = "assets_yoy"
    if ag_col not in df.columns:
        logger.warning("P8 Asset Growth: 无 %s 列，跳过", ag_col)
        return 0

    df["neg_asset_growth"] = -df[ag_col]
    upper = df["neg_asset_growth"].quantile(0.995)
    lower = df["neg_asset_growth"].quantile(0.005)
    df["neg_asset_growth"] = df["neg_asset_growth"].clip(lower=lower, upper=upper)
    df["cs_rank_neg_asset_growth"] = df.groupby("datetime")["neg_asset_growth"].rank(
        pct=True
    )
    return 2


def _add_consec_growth_q(df: pd.DataFrame) -> int:
    """连续营收正增长季度数.

    增长持续性比单期增速更有预测力。连续 4+ 季度正增长 → 结构性增长。
    用 fina_q_sales_yoy > 0 的连续计数实现。
    """
    g_col = "q_sales_yoy"
    if g_col not in df.columns:
        g_col = "or_yoy"
        if g_col not in df.columns:
            logger.warning("P8 Consec Growth: 无营收增速列，跳过")
            return 0

    def _count_consecutive_positive(s: pd.Series) -> pd.Series:
        """计算连续正值的计数（重置于负值）."""
        pos = (s > 0).astype(int)
        # 在变化点标记零（每次转为正时重新计数）
        reset = pos.diff().fillna(0).clip(lower=0)
        groups = reset.cumsum()
        return pos.groupby(groups).cumsum()

    # 由于财务数据 forward-fill 到日频，先 drop duplicate 再计算
    df["consec_growth_q"] = df.groupby("symbol")[g_col].transform(
        _count_consecutive_positive
    )
    df["cs_rank_consec_growth_q"] = df.groupby("datetime")[
        "consec_growth_q"
    ].rank(pct=True)
    return 2
