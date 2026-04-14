"""高优先级 Alpha/Beta 因子扩展模块 (P0 优先级).

基于 R124 因子挖掘研究成果，实现三项学术级高优先因子：

1. Gross Profitability (GP/TA) — Novy-Marx (2013) JFE
   Fama-French (2015) 五因子模型 RMW 核心成分
   A 股月频 IC: 3-5%

2. 3-Month Reversal — Gao et al. (2023) SSRN 4527729
   A 股无中期动量但有显著月度反转，SR=0.59, AR=13.09%
   A 股月频 IC: 2-4%

3. Value+Profitability Composite — Feng et al. (2022) SSRN 4029336
   6 指标复合排名，SR=0.88, 2024年唯一全年正收益因子
   A 股月频 IC: 3-5%

4. Amihud 非流动性因子 — Amihud (2002) JFM
   衡量价格冲击（区别于换手率），A 股 IC: 4-6%

5. 盈利加速因子 — 中金/中信研报 ★★★★
   二阶导优于绝对增速，避免高基数陷阱，IC: 3-4%

设计原则:
- 与 compute_*_factor() 同接口: 输入 candidates，输出 {symbol: value}
- 无状态纯函数
- 兼容 rank_normalize() 工作流
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

if TYPE_CHECKING:
    from aipha.strategy.equity_rotation_base import EquityCandidateInfo


# ============================================================================
# 1. Gross Profitability 因子
# ============================================================================


def compute_gross_profitability_factor(
    candidates: Sequence[EquityCandidateInfo],
) -> dict[str, float]:
    """Gross Profitability (GP/TA) — Novy-Marx (2013) JFE.

    公式: GP/TA = (营业收入 - 营业成本) / 总资产
    经济学解释: 高 GP/TA 代表高效的资产利用能力，
    比 ROE 更纯净（不受杠杆干扰）。
    Fama-French (2015) 五因子模型中 RMW 因子的核心成分。

    数据来源: c.metadata 需包含:
      - revenue: 营业收入 (TTM)
      - cost_of_revenue / oper_cost: 营业成本 (TTM)
      - total_assets: 总资产

    Returns:
        {symbol: GP/TA}，高值 = 高盈利质量 = 高分
    """
    result: dict[str, float] = {}
    for c in candidates:
        md = c.metadata
        revenue = md.get("revenue") or md.get("total_revenue")
        cost = md.get("cost_of_revenue") or md.get("oper_cost")
        total_assets = md.get("total_assets")

        if (revenue is not None and cost is not None
                and total_assets is not None
                and math.isfinite(revenue) and math.isfinite(cost)
                and math.isfinite(total_assets)
                and total_assets > 0):
            gp = revenue - cost
            result[c.symbol] = gp / total_assets
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 2. 3-Month Reversal 因子
# ============================================================================


def compute_3m_reversal_factor(
    candidates: Sequence[EquityCandidateInfo],
    window: int = 60,
    skip: int = 5,
) -> dict[str, float]:
    """3-Month Reversal — Gao et al. (2023) SSRN 4527729.

    公式: -R_{t-window:t-skip}  (过去 window 天收益取负)
    经济学解释: A 股无中期动量但有显著月度反转。
    做多近期表现差的股票（过度反应修正）。
    Gao et al. (2023, 港中文/深交所/Princeton): SR=0.59, AR=13.09%

    与 STR(5d) 的区别: STR 是短期反转(1周)，本因子是中期反转(3个月)。
    与 momentum(60d) 的区别: momentum 做多强势股，本因子做多弱势股。

    数据来源: c.metadata 需包含:
      - close_history: list[float]，最近 ≥window+skip 天收盘价序列
        或 cum_return_{window}d: 过去 window 天累计收益

    Returns:
        {symbol: -cumulative_return}，表现差 = 高分（反转逻辑）
    """
    result: dict[str, float] = {}
    for c in candidates:
        md = c.metadata
        # 优先使用预计算的累计收益
        cum_ret_key = f"cum_return_{window}d"
        cum_ret = md.get(cum_ret_key)
        if cum_ret is not None and math.isfinite(cum_ret):
            result[c.symbol] = -cum_ret
            continue

        # 从价格序列计算
        prices = md.get("close_history")
        if prices is not None and len(prices) >= window + skip:
            p_start = prices[-(window + skip)]
            p_end = prices[-skip] if skip > 0 else prices[-1]
            if p_start > 0:
                cum_ret_val = (p_end / p_start) - 1.0
                result[c.symbol] = -cum_ret_val
            else:
                result[c.symbol] = float("nan")
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 3. Value+Profitability Composite 因子
# ============================================================================


def compute_value_profitability_composite(
    candidates: Sequence[EquityCandidateInfo],
) -> dict[str, float]:
    """Value+Profitability Composite — Feng et al. (2022) SSRN 4029336.

    6 指标等权复合排名:
      1. GP/ME  (毛利/市值)
      2. OP/ME  (营业利润/市值)
      3. NP/ME  (净利润/市值)
      4. GP/TA  (毛利/总资产)
      5. OP/BE  (营业利润/净资产)
      6. NP/BE  (净利润/净资产)

    经济学解释: 综合价值与盈利质量，避免单一指标噪声。
    Premia Partners (2024): 2024 年唯一全年正收益的因子。
    SR=0.88, Beta=-0.016（纯 alpha，不受市场方向影响）。

    数据来源: c.metadata 需包含:
      - revenue, oper_cost: 营收/营业成本 → GP = revenue - oper_cost
      - operate_profit: 营业利润 → OP
      - n_income: 净利润 → NP
      - total_mv / circ_mv: 市值 → ME
      - total_assets: 总资产 → TA
      - total_hldr_eqy_exc_min_int: 归母股东权益 → BE

    Returns:
        {symbol: composite_score}，高分 = 高价值+高盈利质量
    """
    raw: dict[str, list[float]] = {}
    for c in candidates:
        md = c.metadata
        revenue = md.get("revenue") or md.get("total_revenue")
        cost = md.get("cost_of_revenue") or md.get("oper_cost")
        op = md.get("operate_profit")
        np_ = md.get("n_income")
        me = md.get("total_mv") or md.get("circ_mv")
        ta = md.get("total_assets")
        be = md.get("total_hldr_eqy_exc_min_int") or md.get("bvps")

        vals: list[float] = []
        gp = (revenue - cost) if (revenue and cost) else None
        # 6 ratios
        for num, denom in [
            (gp, me), (op, me), (np_, me),
            (gp, ta), (op, be), (np_, be),
        ]:
            if (num is not None and denom is not None
                    and math.isfinite(num) and math.isfinite(denom)
                    and denom > 0):
                vals.append(num / denom)
            else:
                vals.append(float("nan"))
        raw[c.symbol] = vals

    # 截面排名 → 等权平均
    n_metrics = 6
    symbols = list(raw.keys())
    if not symbols:
        return {}

    result: dict[str, float] = {s: 0.0 for s in symbols}
    for i in range(n_metrics):
        vals_i = [(s, raw[s][i]) for s in symbols
                  if math.isfinite(raw[s][i])]
        if len(vals_i) < 2:
            continue
        vals_i.sort(key=lambda x: x[1])
        n = len(vals_i)
        for rank, (s, _) in enumerate(vals_i):
            result[s] += (rank / max(n - 1, 1)) / n_metrics

    # NaN for symbols missing all metrics
    for s in symbols:
        valid = sum(1 for v in raw[s] if math.isfinite(v))
        if valid == 0:
            result[s] = float("nan")
    return result


# ============================================================================
# 4. Amihud 非流动性因子
# ============================================================================


def compute_amihud_illiquidity_factor(
    candidates: Sequence[EquityCandidateInfo],
    window: int = 20,
) -> dict[str, float]:
    """Amihud 非流动性因子 — Amihud (2002) JFM.

    公式: ILLIQ = avg(|r_t| / amount_t)  over past `window` days
    经济学解释: 高 Amihud = 低流动性 = 高流动性溢价（做多）。
    与 turnover 的区别: turnover 衡量交易活跃度，
    Amihud 衡量单位金额交易对价格的冲击。

    A 股月频 IC: 4-6%（知识库 ★★★★）。
    注意: 微盘股 Amihud 极高，需配合 size 约束避免流动性陷阱。

    数据来源: c.metadata 需包含:
      - daily_returns: list[float]，日收益率序列 (≥ window 天)
      - daily_amounts: list[float]，日成交额序列 (元, ≥ window 天)
        或 amihud_ratio: 预计算值

    Returns:
        {symbol: amihud_ratio}，高值 = 低流动性 = 高因子暴露
    """
    result: dict[str, float] = {}
    for c in candidates:
        md = c.metadata
        pre = md.get("amihud_ratio")
        if pre is not None and math.isfinite(pre):
            result[c.symbol] = pre
            continue

        rets = md.get("daily_returns")
        amounts = md.get("daily_amounts")
        if (rets is None or amounts is None
                or len(rets) < window or len(amounts) < window):
            result[c.symbol] = float("nan")
            continue

        # Use the last `window` days
        r_arr = rets[-window:]
        a_arr = amounts[-window:]
        ratios = []
        for r, a in zip(r_arr, a_arr):
            if a is not None and a > 0 and r is not None and math.isfinite(r):
                ratios.append(abs(r) / a)
        if ratios:
            result[c.symbol] = sum(ratios) / len(ratios)
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 5. 盈利加速因子
# ============================================================================


def compute_earnings_acceleration_factor(
    candidates: Sequence[EquityCandidateInfo],
) -> dict[str, float]:
    """盈利加速因子 — Earnings Acceleration (ΔGrowth).

    公式: EA = Growth_Q - Growth_{Q-1}
    其中 Growth_Q = (Revenue_Q / Revenue_{Q-4} - 1)  (同比增速)
    EA > 0 表示增速加快（二阶导数为正），动量延续概率高。

    经济学解释: 盈利加速反映企业处于扩张上升期，
    市场对渐进式改善反应不足（underreaction to gradual change）。

    A 股月频 IC: 3-4%（中金/中信研报 ★★★★）。
    注意: 须使用 PIT 财务数据，避免前视偏差。

    数据来源: c.metadata 需包含:
      - revenue_growth_q0: float  当季同比营收增速
      - revenue_growth_q1: float  上季同比营收增速
        或 earnings_acceleration: 预计算值

    Returns:
        {symbol: ea_value}，正值 = 盈利加速，看多信号
    """
    result: dict[str, float] = {}
    for c in candidates:
        md = c.metadata
        pre = md.get("earnings_acceleration")
        if pre is not None and math.isfinite(pre):
            result[c.symbol] = pre
            continue

        g_q0 = md.get("revenue_growth_q0")
        g_q1 = md.get("revenue_growth_q1")
        if (g_q0 is not None and g_q1 is not None
                and math.isfinite(g_q0) and math.isfinite(g_q1)):
            result[c.symbol] = g_q0 - g_q1
        else:
            result[c.symbol] = float("nan")
    return result
