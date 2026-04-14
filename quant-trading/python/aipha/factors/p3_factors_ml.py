"""R128 P3 因子批次 — 制度防御/反拥挤因子.

目标: 应对2024小盘踩踏类事件, 增强组合的跨风格适应能力.
设计原则:
  - 与已有因子(Alpha158/deep_fundamental/P1/P2)正交
  - 聚焦崩溃保护和风格轮动信号(模型当前盲区)
  - 遵循 CF-018 单因子逐一测试策略, 避免 CF-015 IC陷阱

因子清单:
  - quality_composite: QMJ 质量综合 (Asness 2019, 衰减最慢的因子类)
  - low_beta: 低贝塔因子 (Frazzini BAB 2014, 崩溃缓冲器)
  - value_quality: 价值×质量交互 (Novy-Marx 2013, 跨风格稳健)
  - earnings_stability: 盈利持续性 (Penman 2002, 受拥挤影响最小)
  - crowding_beta: 拥挤度贝塔 (AQR框架, 踩踏脆弱性检测)

学术依据:
  - quality_composite: Asness, Frazzini & Pedersen "Quality Minus Junk" (2019)
  - low_beta: Frazzini & Pedersen "Betting Against Beta" (2014)
  - value_quality: Novy-Marx "The Other Side of Value" (2013) + FF5
  - earnings_stability: Penman & Zhang "Accounting Conservatism" (2002)
  - crowding_beta: 本地 factor_decay_and_esg_china_research §1.3 拥挤度框架
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p3_factors",
    "P3_FACTOR_NAMES",
]

P3_FACTOR_NAMES: list[str] = [
    "quality_composite", "cs_rank_quality_composite",
    "low_beta", "cs_rank_low_beta",
    "value_quality", "cs_rank_value_quality",
    "earnings_stability", "cs_rank_earnings_stability",
    "crowding_beta", "cs_rank_crowding_beta",
]


def add_p3_factors(df: pd.DataFrame) -> int:
    """向面板 DataFrame 添加 P3 制度防御因子列 (就地修改).

    Returns:
        新增因子列数.
    """
    n = 0
    n += _add_quality_composite(df)
    n += _add_low_beta(df)
    n += _add_value_quality(df)
    n += _add_earnings_stability(df)
    n += _add_crowding_beta(df)
    logger.info("P3 制度防御因子: 新增 %d 列", n)
    return n


def _add_quality_composite(df: pd.DataFrame) -> int:
    """Quality Composite: QMJ 质量综合评分 (Asness 2019).

    三维综合: Profitability + Safety + Growth.
      profitability = cs_rank(ROE) + cs_rank(gross_margin)
      safety = cs_rank(-debt_to_assets) + cs_rank(-volatility_60d)
      growth = cs_rank(dt_netprofit_yoy)
    各维度等权 → 综合评分 → 截面rank.

    数据需求: roe, grossprofit_margin, debt_to_assets, close, dt_netprofit_yoy.
    若任一字段缺失, 该维度置 0.5(中性)继续计算.
    """
    added = 0
    dims: list[pd.Series] = []

    # --- Profitability ---
    prof_parts: list[pd.Series] = []
    if "roe" in df.columns:
        prof_parts.append(df.groupby("datetime")["roe"].rank(pct=True))
    if "grossprofit_margin" in df.columns:
        prof_parts.append(
            df.groupby("datetime")["grossprofit_margin"].rank(pct=True)
        )
    if prof_parts:
        prof = sum(prof_parts) / len(prof_parts)
    else:
        prof = pd.Series(0.5, index=df.index)
    dims.append(prof)

    # --- Safety ---
    safety_parts: list[pd.Series] = []
    if "debt_to_assets" in df.columns:
        # 低负债 = 安全, 取负
        neg_debt = -df["debt_to_assets"].astype(np.float64)
        safety_parts.append(df.groupby("datetime")[[]].assign(v=neg_debt).groupby(
            df["datetime"])["v"].rank(pct=True) if False else
            neg_debt.groupby(df["datetime"]).rank(pct=True))
    # 60日收益波动率 (低波动 = 安全)
    if "close" in df.columns:
        ret = df.groupby("symbol")["close"].pct_change()
        vol60 = df.groupby("symbol")[[]].assign(_r=ret).groupby(
            df["symbol"])["_r"].transform(
            lambda x: x.rolling(60, min_periods=20).std()
        ) if False else ret.groupby(df["symbol"]).transform(
            lambda x: x.rolling(60, min_periods=20).std()
        )
        neg_vol = -vol60
        safety_parts.append(neg_vol.groupby(df["datetime"]).rank(pct=True))
    if safety_parts:
        safety = sum(safety_parts) / len(safety_parts)
    else:
        safety = pd.Series(0.5, index=df.index)
    dims.append(safety)

    # --- Growth ---
    if "dt_netprofit_yoy" in df.columns:
        yoy_clipped = df["dt_netprofit_yoy"].astype(np.float64).clip(-200, 200)
        growth = yoy_clipped.groupby(df["datetime"]).rank(pct=True)
    else:
        growth = pd.Series(0.5, index=df.index)
    dims.append(growth)

    # 等权综合
    qmj = sum(dims) / len(dims)
    df["quality_composite"] = qmj
    added += 1

    df["cs_rank_quality_composite"] = (
        df.groupby("datetime")["quality_composite"].rank(pct=True)
    )
    added += 1
    _n_valid = df["quality_composite"].notna().sum()
    logger.info("quality_composite: %d 有效值", _n_valid)
    return added


def _add_low_beta(df: pd.DataFrame) -> int:
    """Low Beta: 低贝塔因子 (Frazzini & Pedersen BAB 2014).

    β = cov(r_i, r_mkt) / var(r_mkt), 回望252天.
    r_mkt 用截面等权市场收益率近似.
    取负号: 低β(安全) = 高因子值.
    2024踩踏中低β股仅损失5-10% vs 高β小盘-30%.
    """
    if "close" not in df.columns:
        logger.warning("low_beta: 缺少 close, 跳过")
        return 0

    added = 0
    window = 252
    min_obs = 120

    # 个股日收益率
    df["_ret_lb"] = df.groupby("symbol")["close"].pct_change()

    # 市场等权收益率
    mkt_ret = df.groupby("datetime")["_ret_lb"].mean()
    df["_mkt_ret"] = df["datetime"].map(mkt_ret)

    # 滚动协方差和方差 → β
    def _rolling_beta(grp: pd.DataFrame) -> pd.Series:
        r = grp["_ret_lb"]
        m = grp["_mkt_ret"]
        cov_rm = r.rolling(window, min_periods=min_obs).cov(m)
        var_m = m.rolling(window, min_periods=min_obs).var()
        beta = cov_rm / var_m.replace(0, np.nan)
        return beta.clip(-3, 5)

    betas = df.groupby("symbol", group_keys=False).apply(
        _rolling_beta, include_groups=False,
    )
    df["low_beta"] = -betas  # 取负: 低β = 高值
    added += 1

    df["cs_rank_low_beta"] = df.groupby("datetime")["low_beta"].rank(pct=True)
    added += 1

    # 清理临时列
    df.drop(columns=["_ret_lb", "_mkt_ret"], inplace=True, errors="ignore")
    _n_valid = df["low_beta"].notna().sum()
    logger.info("low_beta: %d 有效值", _n_valid)
    return added


def _add_value_quality(df: pd.DataFrame) -> int:
    """Value-Quality 交互: EP × ROE_rank (Novy-Marx 2013 + FF5).

    便宜(高EP) + 好(高ROE) → 跨风格稳健.
    2024 价值因子领涨; 2025 质量因子领涨; 两者交互在两种风格下都不太差.

    EP = 1 / PE_TTM (正值表示便宜).
    Value-Quality = cs_rank(EP) × cs_rank(ROE).
    """
    has_pe = "pe_ttm" in df.columns
    has_roe = "roe" in df.columns
    if not has_pe and not has_roe:
        logger.warning("value_quality: 缺少 pe_ttm 和 roe, 跳过")
        return 0

    added = 0

    # EP = 1/PE_TTM, 处理负PE和极端值
    if has_pe:
        pe = df["pe_ttm"].astype(np.float64)
        # 仅保留正PE (盈利公司)
        ep = 1.0 / pe.where(pe > 0, np.nan)
        # Winsorize at 1%/99%
        lo, hi = ep.quantile(0.01), ep.quantile(0.99)
        ep = ep.clip(lo, hi)
        ep_rank = ep.groupby(df["datetime"]).rank(pct=True)
    else:
        ep_rank = pd.Series(0.5, index=df.index)

    if has_roe:
        roe = df["roe"].astype(np.float64).clip(-50, 100)
        roe_rank = roe.groupby(df["datetime"]).rank(pct=True)
    else:
        roe_rank = pd.Series(0.5, index=df.index)

    # 交互: 两个排名的乘积
    df["value_quality"] = ep_rank * roe_rank
    added += 1

    df["cs_rank_value_quality"] = (
        df.groupby("datetime")["value_quality"].rank(pct=True)
    )
    added += 1
    _n_valid = df["value_quality"].notna().sum()
    logger.info("value_quality: %d 有效值", _n_valid)
    return added


def _add_earnings_stability(df: pd.DataFrame) -> int:
    """Earnings Stability: 盈利持续性因子 (Penman 2002).

    低盈利波动 → 可持续盈利 → 受拥挤影响最小.
    EarnStab = -CV(dt_netprofit_yoy) 过去4个不同值(≈4个季度).

    由于季报数据被 ffill 到日频, 用 rolling unique 值的标准差.
    直接取 dt_netprofit_yoy 的 rolling 252日标准差做近似:
    稳定增长公司的 yoy 数值在窗口内波动小.
    """
    if "dt_netprofit_yoy" not in df.columns:
        logger.warning("earnings_stability: 缺少 dt_netprofit_yoy, 跳过")
        return 0

    added = 0
    yoy = df["dt_netprofit_yoy"].astype(np.float64).clip(-200, 200)

    # 滚动252天标准差
    roll_std = yoy.groupby(df["symbol"]).transform(
        lambda x: x.rolling(252, min_periods=60).std()
    )
    # 滚动均值的绝对值 (避免除以0)
    roll_mean_abs = yoy.groupby(df["symbol"]).transform(
        lambda x: x.rolling(252, min_periods=60).mean().abs().clip(lower=1.0)
    )
    # CV = std / |mean|, 取负: 低波动 = 高值
    cv = roll_std / roll_mean_abs
    df["earnings_stability"] = -cv.clip(0, 10)
    added += 1

    df["cs_rank_earnings_stability"] = (
        df.groupby("datetime")["earnings_stability"].rank(pct=True)
    )
    added += 1
    _n_valid = df["earnings_stability"].notna().sum()
    logger.info("earnings_stability: %d 有效值", _n_valid)
    return added


def _add_crowding_beta(df: pd.DataFrame) -> int:
    """Crowding Beta: 拥挤度贝塔 (AQR框架 + 本地研究§1.3).

    衡量个股对小盘群体行为的敏感度.
    高拥挤β → 与小盘群体同涨同跌 → 踩踏时受损最重.

    构建: 个股收益率与"最小市值组等权收益"的120日滚动相关系数.
    取负号: 低拥挤(低相关) = 高因子值 = 更安全.

    近似实现: 用截面收益率中位数以下股票的平均收益作为小盘收益代理
    (因为 CSI2000 本身偏小盘, 中位以下即最小一批).
    """
    if "close" not in df.columns:
        logger.warning("crowding_beta: 缺少 close, 跳过")
        return 0

    added = 0
    window = 120
    min_obs = 60

    # 个股日收益率
    df["_ret_cb"] = df.groupby("symbol")["close"].pct_change()

    # 小盘代理收益率: 每日截面收益率的 bottom 30% 等权均值
    def _small_cap_ret(g: pd.DataFrame) -> float:
        rets = g["_ret_cb"].dropna()
        if len(rets) < 10:
            return np.nan
        thresh = rets.quantile(0.30)
        bottom = rets[rets <= thresh]
        return bottom.mean() if len(bottom) > 0 else np.nan

    small_ret = df.groupby("datetime").apply(
        _small_cap_ret, include_groups=False,
    )
    df["_small_ret"] = df["datetime"].map(small_ret)

    # 滚动相关
    def _rolling_corr(grp: pd.DataFrame) -> pd.Series:
        r = grp["_ret_cb"]
        s = grp["_small_ret"]
        return r.rolling(window, min_periods=min_obs).corr(s)

    corrs = df.groupby("symbol", group_keys=False).apply(
        _rolling_corr, include_groups=False,
    )
    # 取负号: 与小盘群体低相关 = 高值 = 更安全
    df["crowding_beta"] = -corrs.clip(-1, 1)
    added += 1

    df["cs_rank_crowding_beta"] = (
        df.groupby("datetime")["crowding_beta"].rank(pct=True)
    )
    added += 1

    # 清理
    df.drop(
        columns=["_ret_cb", "_small_ret"], inplace=True, errors="ignore"
    )
    _n_valid = df["crowding_beta"].notna().sum()
    logger.info("crowding_beta: %d 有效值", _n_valid)
    return added
