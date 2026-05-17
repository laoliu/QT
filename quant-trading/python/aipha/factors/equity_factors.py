"""A 股通用因子计算器

将常见的因子计算逻辑从策略中抽取出来，策略可通过此模块获取标准化的因子值，
而非在 _score_candidates() 中重复内联计算。

设计原则：
- 不改变策略现有的 rank_normalize() 工作流
- 提供 {symbol: factor_value} 字典作为输出，直接兼容 rank_normalize()
- 每个因子函数是无状态的纯函数（输入 candidates + 辅助数据，输出字典）
- 策略可以选择性使用（渐进式迁移，不破坏已有逻辑）

使用示例::

    from aipha.factors.equity_factors import compute_value_factor, compute_size_factor

    class MyStrategy(EquityRotationStrategy):
        def _score_candidates(self, candidates, context):
            scores = {}
            f_value = compute_value_factor(candidates, metric="pe_ttm")
            self.rank_normalize(candidates, f_value, weight=0.3, scores=scores)
            f_size = compute_size_factor(candidates)
            self.rank_normalize(candidates, f_size, weight=0.2, scores=scores)
            return scores
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from aipha.strategy.equity_rotation_base import EquityCandidateInfo


# ============================================================================
# 价值因子
# ============================================================================


def compute_value_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "pe_ttm",
) -> dict[str, float]:
    """价值因子：低 PE/PB/PS 优先。

    Args:
        candidates: 候选标的列表
        metric: 使用的估值指标字段名，来自 metadata
                支持 "pe_ttm", "pb", "ps_ttm" 等

    Returns:
        {symbol: -metric_value}，取负使得低估值 = 高分
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(metric)
        if val is not None and math.isfinite(val) and val > 0:
            result[c.symbol] = -val
        else:
            result[c.symbol] = float("nan")
    return result


def compute_ep_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "pe_ttm",
) -> dict[str, float]:
    """EP 因子（盈利收益率 = 1/PE_TTM）。

    知识库 A1.1：EP = 净利润TTM/市值，与市场 E/P 排名确认后保留。
    A 股价值异象中，EP 因子月频 IC 约 2-4%（知识库 G1 表格）。

    Args:
        candidates: 候选标的列表
        metric: 估值指标，默认 "pe_ttm"；也可用 "ps_ttm"/"pb" 的倒数

    Returns:
        {symbol: 1/metric}，高 EP（低 PE）= 高分；PE ≤ 0 视为缺失
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(metric)
        if val is not None and math.isfinite(val) and val > 0:
            result[c.symbol] = 1.0 / val
        else:
            result[c.symbol] = float("nan")
    return result


def compute_bp_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "pb",
) -> dict[str, float]:
    """BP 因子（账面市值比 = 1/PB）。

    参考组合核心因子之一（R76 E1）。BP = 净资产/市值 = 1/PB。
    经济学解释：高 BP 代表价值型公司，长期有正向风险溢价
    （Fama-French 1993 HML 因子）。

    A 股 BP 因子月频 IC 约 2-5%，与 EP 正相关但互补性强
    （EP 偏盈利能力、BP 偏资产质量）。

    Args:
        candidates: 候选标的列表
        metric: 市净率字段名，默认 "pb"

    Returns:
        {symbol: 1/PB}，高 BP（低 PB）= 高分；PB ≤ 0 视为缺失
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(metric)
        if val is not None and math.isfinite(val) and val > 0:
            result[c.symbol] = 1.0 / val
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 质量因子
# ============================================================================


def compute_quality_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "roe",
) -> dict[str, float]:
    """质量因子：高 ROE/ROA/毛利率 优先。

    Args:
        candidates: 候选标的列表
        metric: 质量指标字段名（如 "roe", "roa", "grossprofit_margin"）

    Returns:
        {symbol: metric_value}，越高越好
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(metric)
        if val is not None and math.isfinite(val):
            result[c.symbol] = val
        else:
            result[c.symbol] = float("nan")
    return result


def compute_debt_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "debt_to_assets",
) -> dict[str, float]:
    """低负债因子：低资产负债率优先。

    Returns:
        {symbol: -debt_ratio}，取负使得低负债 = 高分
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(metric)
        if val is not None and math.isfinite(val):
            result[c.symbol] = -val
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 规模因子
# ============================================================================


def compute_size_factor(
    candidates: Sequence[EquityCandidateInfo],
) -> dict[str, float]:
    """小市值因子：市值越小分越高。

    Returns:
        {symbol: -log(circ_mv)}
    """
    result: dict[str, float] = {}
    for c in candidates:
        if c.circ_mv > 0:
            result[c.symbol] = -math.log(c.circ_mv)
        else:
            result[c.symbol] = -float("inf")
    return result


# ============================================================================
# 换手率因子
# ============================================================================


def compute_turnover_factor(
    candidates: Sequence[EquityCandidateInfo],
) -> dict[str, float]:
    """低换手因子：换手率越低分越高。

    Returns:
        {symbol: -turnover_rate}
    """
    result: dict[str, float] = {}
    for c in candidates:
        if c.turnover_rate >= 0:
            result[c.symbol] = -c.turnover_rate
        else:
            result[c.symbol] = -float("inf")
    return result


# ============================================================================
# 动量因子
# ============================================================================


def compute_momentum_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 20,
    invert: bool = False,
    skip_recent: int = 0,
    multi_period: bool = False,
) -> dict[str, float]:
    """动量因子：过去 N 日收益率。

    Args:
        price_history: {symbol: [close_prices...]} 历史价格序列
        candidates: 候选标的列表
        lookback: 回看天数（单周期模式下使用）
        invert: 是否取反（反转效应）
        skip_recent: 跳过最近 N 天（剔除短期反转噪声，经典 12-1 动量跳 1 天）
        multi_period: 启用多周期动量混合（5d×0.2 + 20d×0.5 + 60d×0.3）

    Returns:
        {symbol: momentum_value}
    """
    result: dict[str, float] = {}
    for c in candidates:
        hist = price_history.get(c.symbol, [])
        if multi_period:
            # 混合 5d + 20d + 60d 动量（衰减加权）
            periods = [(5, 0.2), (20, 0.5), (60, 0.3)]
            mom_sum = 0.0
            valid_weight = 0.0
            for period, w in periods:
                need = period + skip_recent + 1
                if len(hist) < need:
                    continue
                idx_old = -(period + skip_recent + 1)
                idx_new = -(skip_recent + 1) if skip_recent > 0 else -1
                old_p = hist[idx_old]
                new_p = hist[idx_new]
                if old_p > 0:
                    mom_sum += w * ((new_p - old_p) / old_p)
                    valid_weight += w
            if valid_weight > 0:
                mom = mom_sum / valid_weight
            else:
                result[c.symbol] = float("nan")
                continue
        else:
            need = lookback + skip_recent + 1
            if len(hist) < need:
                result[c.symbol] = float("nan")
                continue
            idx_old = -(lookback + skip_recent + 1)
            idx_new = -(skip_recent + 1) if skip_recent > 0 else -1
            old_p = hist[idx_old]
            new_p = hist[idx_new]
            if old_p <= 0:
                result[c.symbol] = float("nan")
                continue
            mom = (new_p - old_p) / old_p
        result[c.symbol] = -mom if invert else mom
    return result


# ============================================================================
# 风险调整动量因子
# ============================================================================


def compute_risk_adjusted_momentum(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 20,
) -> dict[str, float]:
    """风险调整动量：momentum / volatility (Sharpe-like).

    相比原始动量，该因子偏好稳定上涨的标的，惩罚高波动带来的
    随机涨幅，在选股排序中通常更为稳健。

    Args:
        price_history: {symbol: [close_prices...]} 历史价格序列
        candidates: 候选标的列表
        lookback: 回看天数

    Returns:
        {symbol: momentum / volatility}，NaN 表示数据不足或波动率为零
    """
    result: dict[str, float] = {}
    for c in candidates:
        hist = price_history.get(c.symbol, [])
        if len(hist) < lookback + 1:
            result[c.symbol] = float("nan")
            continue
        prices = hist[-(lookback + 1):]
        old_p = prices[0]
        new_p = prices[-1]
        if old_p <= 0:
            result[c.symbol] = float("nan")
            continue
        momentum = (new_p - old_p) / old_p
        # 日收益率序列
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if len(returns) < 2:
            result[c.symbol] = float("nan")
            continue
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        vol = math.sqrt(var)
        if vol > 1e-10:
            result[c.symbol] = momentum / vol
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 波动率因子
# ============================================================================


def compute_volatility_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 20,
) -> dict[str, float]:
    """低波动因子：波动率越低分越高。

    Args:
        price_history: {symbol: [close_prices...]}
        candidates: 候选标的列表
        lookback: 回看天数

    Returns:
        {symbol: -volatility}，取负使得低波动 = 高分
    """
    result: dict[str, float] = {}
    for c in candidates:
        hist = price_history.get(c.symbol, [])
        if len(hist) < lookback + 1:
            result[c.symbol] = float("nan")  # 数据不足标为缺失，避免虚假排名
            continue
        prices = hist[-(lookback + 1):]
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if len(returns) < 2:
            result[c.symbol] = float("nan")
            continue
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        result[c.symbol] = -math.sqrt(variance)
    return result


def compute_ivol_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    market_returns: list[float] | None = None,
    lookback: int = 20,
) -> dict[str, float]:
    """特异波动率因子（IVOL，反向信号 — 低 IVOL 优先）。

    知识库 A2.3：低特异波动率股票长期跑赢高 IVOL 股票（低波动异象）。
    A 股月频 IC 约 2-4%（知识库 G1 表格）。

    当市场收益率序列可用时，先用 OLS 剥离系统性风险（beta），
    以残差方差作为 IVOL；无市场数据时退化为总波动率。

    Args:
        price_history: {symbol: [close_prices...]}
        candidates: 候选标的列表
        market_returns: 市场日收益率序列（可选，用于 beta 剥离）
        lookback: 计算窗口天数

    Returns:
        {symbol: -ivol}，取负使低 IVOL = 高分
    """
    result: dict[str, float] = {}
    for c in candidates:
        hist = price_history.get(c.symbol, [])
        if len(hist) < lookback + 1:
            result[c.symbol] = float("nan")
            continue
        prices = hist[-(lookback + 1):]
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if len(returns) < 2:
            result[c.symbol] = float("nan")
            continue
        # Beta 剥离（提高 IVOL 质量）
        if market_returns is not None and len(market_returns) >= len(returns):
            mkt = market_returns[-len(returns):]
            mkt_mean = sum(mkt) / len(mkt)
            r_mean = sum(returns) / len(returns)
            cov = sum((r - r_mean) * (m - mkt_mean) for r, m in zip(returns, mkt))
            mkt_var = sum((m - mkt_mean) ** 2 for m in mkt)
            if mkt_var > 0:
                beta = cov / mkt_var
                residuals = [r - beta * m for r, m in zip(returns, mkt)]
                res_mean = sum(residuals) / len(residuals)
                ivol = math.sqrt(
                    sum((r - res_mean) ** 2 for r in residuals) / (len(residuals) - 1)
                )
                result[c.symbol] = -ivol
                continue
        # 无市场数据 → 退化为总波动率
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        result[c.symbol] = -math.sqrt(variance)
    return result


def compute_ewma_volatility_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 20,
    decay: float = 0.94,
    *,
    halflife: int | None = None,
) -> dict[str, float]:
    """EWMA 低波动因子：指数加权波动率，对近期行情更敏感。

    使用 RiskMetrics 风格的 EWMA 方差估计：
        var_t = decay * var_{t-1} + (1 - decay) * r_t^2

    相比等权波动率，EWMA 对近期的市场变化反应更快，
    适合在波动聚集（volatility clustering）明显的 A 股市场使用。

    Args:
        price_history: {symbol: [close_prices...]} 历史价格序列
        candidates: 候选标的列表
        lookback: 回看天数
        decay: 衰减系数 λ，越大则对历史波动越重视（0 < decay < 1）
        halflife: 半衰期（天数），若指定则覆盖 decay，转换公式: decay = 2^(-1/halflife)

    Returns:
        {symbol: -sqrt(ewma_var)}，取负使得低波动 = 高分；NaN 表示数据不足
    """
    # halflife 转换为 decay: decay = exp(-ln2 / halflife)
    if halflife is not None and halflife > 0:
        decay = 2.0 ** (-1.0 / halflife)
    result: dict[str, float] = {}
    for c in candidates:
        hist = price_history.get(c.symbol, [])
        if len(hist) < lookback + 1:
            result[c.symbol] = float("nan")
            continue
        prices = hist[-(lookback + 1):]
        # 计算日收益率
        returns: list[float] = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
            else:
                returns.append(0.0)
        if len(returns) < 2:
            result[c.symbol] = float("nan")
            continue
        # EWMA 方差
        ewma_var = returns[0] ** 2
        for r in returns[1:]:
            ewma_var = decay * ewma_var + (1 - decay) * r ** 2
        result[c.symbol] = -math.sqrt(ewma_var)
    return result


# ============================================================================
# 盈利增长因子
# ============================================================================


def compute_earnings_growth_factor(
    candidates: Sequence[EquityCandidateInfo],
    field: str = "dt_netprofit_yoy",
) -> dict[str, float]:
    """盈利增长因子：扣非利润/营收 YoY 增长率。

    Args:
        candidates: 候选标的列表
        field: metadata 中的增长率字段（如 "dt_netprofit_yoy", "revenue_yoy"）

    Returns:
        {symbol: growth_value}，越高越好
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(field)
        if val is not None and math.isfinite(val):
            result[c.symbol] = val
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 股息率因子
# ============================================================================


def compute_dividend_factor(
    candidates: Sequence[EquityCandidateInfo],
    field: str = "dv_ratio",
) -> dict[str, float]:
    """股息率因子：高股息率优先。

    Args:
        candidates: 候选标的列表
        field: metadata 中的股息率字段（"dv_ratio" 或 "dv_ttm"）

    Returns:
        {symbol: dividend_value}，越高越好
    """
    result: dict[str, float] = {}
    for c in candidates:
        val = c.metadata.get(field)
        if val is not None and math.isfinite(val) and val >= 0:
            result[c.symbol] = val
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# Piotroski F-Score 因子
# ============================================================================


def compute_piotroski_factor(
    candidates: Sequence[EquityCandidateInfo],
    min_dimensions: int = 5,
) -> dict[str, float]:
    """Piotroski F-Score 因子：9 维度质量评分。

    基于 metadata 中的财务指标计算 Piotroski F-Score（0-9 分）。
    数据要求较高，至少需要 ``min_dimensions`` 个维度可计算。

    所用 metadata 字段：
        roa, roa_prev, cfps, eps, debt_to_assets, debt_to_assets_prev,
        current_ratio, current_ratio_prev, total_share, total_share_prev,
        grossprofit_margin, grossprofit_margin_prev, asset_turnover,
        asset_turnover_prev

    Args:
        candidates: 候选标的列表
        min_dimensions: 最少可计算维度数（默认 5）

    Returns:
        {symbol: f_score}，0-9 分，越高越好
    """
    result: dict[str, float] = {}
    for c in candidates:
        m = c.metadata
        score = 0
        computable = 0

        def _sf(key: str) -> float | None:
            v = m.get(key)
            if v is None:
                return None
            try:
                f = float(v)
                return f if math.isfinite(f) else None
            except (TypeError, ValueError):
                return None

        # F1: ROA > 0
        roa = _sf("roa")
        if roa is not None:
            computable += 1
            score += 1 if roa > 0 else 0

        # F2: CFO > 0
        cfps = _sf("cfps")
        if cfps is not None:
            computable += 1
            score += 1 if cfps > 0 else 0

        # F3: ΔROA > 0
        roa_prev = _sf("roa_prev")
        if roa is not None and roa_prev is not None:
            computable += 1
            score += 1 if roa > roa_prev else 0

        # F4: CFO > NI
        eps = _sf("eps")
        if cfps is not None and eps is not None:
            computable += 1
            score += 1 if cfps > eps else 0

        # F5: 负债率下降
        debt = _sf("debt_to_assets")
        debt_prev = _sf("debt_to_assets_prev")
        if debt is not None and debt_prev is not None:
            computable += 1
            score += 1 if debt < debt_prev else 0

        # F6: 流动比率提升
        cr = _sf("current_ratio")
        cr_prev = _sf("current_ratio_prev")
        if cr is not None and cr_prev is not None:
            computable += 1
            score += 1 if cr > cr_prev else 0

        # F7: 无新增股份
        shares = _sf("total_share")
        shares_prev = _sf("total_share_prev")
        if shares is not None and shares_prev is not None:
            computable += 1
            score += 1 if shares <= shares_prev else 0

        # F8: 毛利率提升
        gm = _sf("grossprofit_margin") or _sf("gross_margin")
        gm_prev = _sf("grossprofit_margin_prev") or _sf("gross_margin_prev")
        if gm is not None and gm_prev is not None:
            computable += 1
            score += 1 if gm > gm_prev else 0

        # F9: 资产周转率提升
        at = _sf("asset_turnover")
        at_prev = _sf("asset_turnover_prev")
        if at is not None and at_prev is not None:
            computable += 1
            score += 1 if at > at_prev else 0

        if computable >= min_dimensions:
            result[c.symbol] = float(score)
        else:
            result[c.symbol] = float("nan")
    return result


# ============================================================================
# 应计盈余质量因子
# ============================================================================


def compute_accruals_quality_factor(
    candidates: Sequence[EquityCandidateInfo],
) -> dict[str, float]:
    """应计盈余质量因子：(CFO - NI) / TotalAssets。

    低应计 = 高现金流质量 = 高因子值（取负）。
    降级路径：绝对值 → 每股数据（eps, cfps）。

    Returns:
        {symbol: -accruals_ratio}，越高表示现金流质量越好
    """
    result: dict[str, float] = {}
    for c in candidates:
        m = c.metadata

        def _sf(key: str) -> float | None:
            v = m.get(key)
            if v is None:
                return None
            try:
                f = float(v)
                return f if math.isfinite(f) else None
            except (TypeError, ValueError):
                return None

        ni = _sf("n_income") or _sf("n_income_attr_p")
        cfo = _sf("n_cashflow_act")
        ta = _sf("total_assets")

        if ni is not None and cfo is not None and ta is not None and ta > 0:
            accruals = (ni - cfo) / ta
            result[c.symbol] = -accruals
            continue

        eps = _sf("eps")
        cfps = _sf("cfps")
        if eps is not None and cfps is not None:
            result[c.symbol] = -(eps - cfps)
            continue

        result[c.symbol] = float("nan")
    return result


# ============================================================================
# 盈利稳定性因子
# ============================================================================


def compute_earnings_stability_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "roe",
    min_periods: int = 3,
) -> dict[str, float]:
    """盈利稳定性因子：-CV（变异系数的负值）。

    使用 metadata 中 ``{metric}_history`` 列表或 ``{metric}_std`` +
    ``{metric}_mean`` 计算。稳定性越高（CV 越低），因子值越大。

    Args:
        candidates: 候选标的列表
        metric: 使用的盈利指标（"roe" 或 "roa"）
        min_periods: 最少需要的历史期数

    Returns:
        {symbol: -cv}，越大越稳定
    """
    import statistics as _stats

    result: dict[str, float] = {}
    hist_key = f"{metric}_history"
    std_key = f"{metric}_std"
    mean_key = f"{metric}_mean"

    for c in candidates:
        m = c.metadata

        # 路径 1：历史列表
        history = m.get(hist_key)
        if history and isinstance(history, (list, tuple)):
            valid = []
            for v in history:
                try:
                    fv = float(v)
                    if math.isfinite(fv):
                        valid.append(fv)
                except (TypeError, ValueError):
                    pass
            if len(valid) >= min_periods:
                mean = _stats.mean(valid)
                if mean != 0:
                    stdev = _stats.stdev(valid) if len(valid) >= 2 else 0.0
                    cv = stdev / abs(mean)
                    result[c.symbol] = -cv
                    continue

        # 路径 2：预计算 std + mean
        std_v = m.get(std_key)
        mean_v = m.get(mean_key)
        if std_v is not None and mean_v is not None:
            try:
                s = float(std_v)
                mu = float(mean_v)
                if math.isfinite(s) and math.isfinite(mu) and mu != 0:
                    result[c.symbol] = -(s / abs(mu))
                    continue
            except (TypeError, ValueError):
                pass

        result[c.symbol] = float("nan")
    return result


# ============================================================================
# 估值分位因子
# ============================================================================


def compute_valuation_percentile_factor(
    candidates: Sequence[EquityCandidateInfo],
    metric: str = "pe_ttm",
    history_field: str | None = None,
    min_history: int = 20,
) -> dict[str, float]:
    """估值历史分位因子：当前估值在历史分布中的百分位。

    低百分位 = 便宜 = 高因子值（返回 ``1 - percentile``）。

    所需 metadata 字段：
        - ``{metric}``: 当前估值（如 pe_ttm/pb）
        - ``{history_field}``: 历史估值列表（如 pe_history/pb_history）

    Args:
        candidates: 候选标的列表
        metric: 当前估值字段名（默认 "pe_ttm"）
        history_field: 历史序列字段名（默认 "{metric_base}_history"）
        min_history: 最少需要的有效历史数据点数

    Returns:
        {symbol: 1 - percentile}，越大表示越便宜
    """
    if history_field is None:
        # pe_ttm → pe_history, pb → pb_history
        base = metric.replace("_ttm", "")
        history_field = f"{base}_history"

    result: dict[str, float] = {}
    for c in candidates:
        m = c.metadata
        current = m.get(metric)

        try:
            current = float(current)  # type: ignore[arg-type]
            if not math.isfinite(current) or current <= 0:
                result[c.symbol] = float("nan")
                continue
        except (TypeError, ValueError):
            result[c.symbol] = float("nan")
            continue

        history = m.get(history_field)
        if not history or not isinstance(history, (list, tuple)):
            result[c.symbol] = float("nan")
            continue

        valid = []
        for v in history:
            try:
                fv = float(v)
                if math.isfinite(fv) and fv > 0:
                    valid.append(fv)
            except (TypeError, ValueError):
                pass

        if len(valid) < min_history:
            result[c.symbol] = float("nan")
            continue

        below = sum(1 for v in valid if v <= current)
        percentile = below / len(valid)
        result[c.symbol] = 1.0 - percentile
    return result


# ============================================================================
# 因子工具函数
# ============================================================================


def winsorize_factors(
    factor_values: dict[str, float],
    lower_pct: float = 0.01,
    upper_pct: float = 0.99,
) -> dict[str, float]:
    """对因子值做缩尾处理，将极端值裁剪到 [lower_pct, upper_pct] 分位。

    在 rank_normalize 之前使用可以降低异常值对排名的扭曲，
    提高因子值在截面上的区分度。

    Args:
        factor_values: {symbol: factor_value} 原始因子字典
        lower_pct: 下限分位数（默认 1%）
        upper_pct: 上限分位数（默认 99%）

    Returns:
        缩尾后的 {symbol: clipped_value}，NaN / -inf 保持不变
    """
    # 收集有限值
    finite_vals = [
        v for v in factor_values.values()
        if math.isfinite(v)
    ]
    if len(finite_vals) < 2:
        return dict(factor_values)

    finite_vals.sort()
    n = len(finite_vals)
    lo_idx = max(0, int(n * lower_pct))
    hi_idx = min(n - 1, int(n * upper_pct))
    lower_bound = finite_vals[lo_idx]
    upper_bound = finite_vals[hi_idx]

    result: dict[str, float] = {}
    for sym, v in factor_values.items():
        if math.isfinite(v):
            result[sym] = max(lower_bound, min(upper_bound, v))
        else:
            result[sym] = v  # NaN / -inf 保持原样
    return result


# ============================================================================
# 复合因子打分器
# ============================================================================


def score_candidates_with_factors(
    candidates: Sequence[EquityCandidateInfo],
    factor_specs: list[tuple[dict[str, float], float]],
    rank_normalize_fn: Any = None,
) -> dict[str, float]:
    """使用多因子打分。

    将计算好的因子值和权重列表传入，统一调用 rank_normalize 并返回综合分。

    Args:
        candidates: 候选标的列表
        factor_specs: [(factor_values_dict, weight), ...] 因子值-权重对
        rank_normalize_fn: rank_normalize 函数引用
                          （默认使用 EquityRotationStrategy.rank_normalize）

    Returns:
        {symbol: composite_score}

    Example::

        f_value = compute_value_factor(candidates, "pe_ttm")
        f_quality = compute_quality_factor(candidates, "roe")
        scores = score_candidates_with_factors(
            candidates,
            [(f_value, 0.3), (f_quality, 0.3)],
            rank_normalize_fn=self.rank_normalize,
        )
    """
    if rank_normalize_fn is None:
        from aipha.strategy.equity_rotation_base import EquityRotationStrategy
        rank_normalize_fn = EquityRotationStrategy.rank_normalize

    scores: dict[str, float] = {}
    for factor_values, weight in factor_specs:
        rank_normalize_fn(candidates, factor_values, weight, scores)
    return scores


# ============================================================================
# 行为金融因子
# ============================================================================


def compute_cgo_factor(
    price_history: dict[str, list[float]],
    volume_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 60,
) -> dict[str, float]:
    """资本利得凸出因子 (Capital Gains Overhang, CGO)。

    CGO 衡量投资者未实现损益的心理锚定效应。定义:
        reference_price = 成交量加权平均成本 (V-WAP over lookback)
        CGO = (current_price - reference_price) / reference_price

    CGO > 0: 投资者持有浮盈，可能出现 disposition effect (倾向卖出)
    CGO < 0: 投资者持有浮亏，可能惜售导致 underreaction

    行为金融研究表明:
    - CGO 对未来收益有正向预测力 (Grinblatt & Han, 2005)
    - CGO 高的股票后续表现更好 (浮盈股被过度卖出导致低估)

    Args:
        price_history: {symbol: [close_prices...]} 历史收盘价
        volume_history: {symbol: [volumes...]} 历史成交量
        candidates: 候选标的列表
        lookback: 回看天数 (默认 60 个交易日)

    Returns:
        {symbol: cgo_value} — CGO 越高说明浮盈越大
    """
    result: dict[str, float] = {}
    for c in candidates:
        prices = price_history.get(c.symbol, [])
        volumes = volume_history.get(c.symbol, [])
        if len(prices) < lookback or len(volumes) < lookback:
            continue
        p = prices[-lookback:]
        v = volumes[-lookback:]

        total_vol = sum(v)
        if total_vol <= 0:
            continue

        # VWAP = Σ(price_i * volume_i) / Σ(volume_i)
        vwap = sum(pi * vi for pi, vi in zip(p, v)) / total_vol
        current = c.close
        if vwap > 1e-8:
            result[c.symbol] = (current - vwap) / vwap
    return result


def compute_abnormal_turnover_factor(
    volume_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    short_window: int = 20,
    long_window: int = 250,
) -> dict[str, float]:
    """异常换手因子 (Abnormal Turnover, ABT)。

    ABT = MA(turnover, short_window) / MA(turnover, long_window)

    ABT > 1 表示短期换手异常放大，行为金融研究表明高异常换手通常
    伴随散户注意力涌入，后续收益倾向为负（注意力驱动溢价消散）。

    做多 ABT 低的标的（低异常关注度），因此取负。

    Args:
        volume_history: {symbol: [daily_volumes...]} 历史成交量
        candidates: 候选标的列表
        short_window: 短期均量窗口（默认20交易日≈1月）
        long_window: 长期均量窗口（默认250交易日≈1年）

    Returns:
        {symbol: -abt_value} — ABT 越低（异常度越低）分越高

    References:
        Barber & Odean (2008) "All That Glitters"
        Gervais, Kaniel, Mingelgrin (2001) "The High-Volume Return Premium"
    """
    result: dict[str, float] = {}
    for c in candidates:
        vols = volume_history.get(c.symbol, [])
        if len(vols) < long_window:
            result[c.symbol] = float("nan")
            continue
        short_avg = sum(vols[-short_window:]) / short_window
        long_avg = sum(vols[-long_window:]) / long_window
        if long_avg > 1e-8:
            result[c.symbol] = -(short_avg / long_avg)
        else:
            result[c.symbol] = float("nan")
    return result


def compute_max_daily_return_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 20,
) -> dict[str, float]:
    """最大日收益因子 (MAX)。

    MAX = max(daily_return) over past lookback days.

    Bali, Cakici, Whitelaw (2011) 发现 MAX 与未来收益负相关：
    投资者偏好彩票型股票（高 MAX），推高价格导致后续低收益。

    做多低 MAX 标的（理性定价），因此取负。

    Args:
        price_history: {symbol: [close_prices...]}
        candidates: 候选标的列表
        lookback: 回看天数

    Returns:
        {symbol: -max_return} — MAX 越低分越高

    References:
        Bali, Cakici, Whitelaw (2011) "Maxing Out"
    """
    result: dict[str, float] = {}
    for c in candidates:
        hist = price_history.get(c.symbol, [])
        if len(hist) < lookback + 1:
            result[c.symbol] = float("nan")
            continue
        prices = hist[-(lookback + 1):]
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 1e-8:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if returns:
            result[c.symbol] = -max(returns)
        else:
            result[c.symbol] = float("nan")
    return result


def compute_str_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[EquityCandidateInfo],
    lookback: int = 5,
) -> dict[str, float]:
    """短期反转因子 (Short-Term Reversal, STR)。

    经典行为金融异象: 近 1 周 (5 天) 涨幅最大的股票未来倾向回调，
    跌幅最大的股票倾向反弹。

    STR = -1 * return(lookback days)

    做多近期跌幅大的、做空近期涨幅大的（多头策略中仅做多 STR 高的）。

    Args:
        price_history: {symbol: [close_prices...]} 历史收盘价
        candidates: 候选标的列表
        lookback: 回看天数 (默认 5 = 1 周)

    Returns:
        {symbol: str_value} — 近期跌幅越大 STR 越高 (反转预期越强)

    References:
        Jegadeesh (1990) "Evidence of Predictable Behavior of Security Returns"
        Lehmann (1990) "Fads, Martingales, and Market Efficiency"
    """
    result: dict[str, float] = {}
    for c in candidates:
        prices = price_history.get(c.symbol, [])
        if len(prices) < lookback + 1:
            continue
        p_end = prices[-1]
        p_start = prices[-(lookback + 1)]
        if p_start > 1e-8:
            # 取反: 下跌 → STR 为正 (高反转预期)
            result[c.symbol] = -(p_end - p_start) / p_start
    return result


def compute_overreaction_composite(
    subfactors: dict[str, dict[str, float]],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """过度反应组合因子 (Overreaction Composite)。

    将多个行为偏差子因子通过排名融合生成单一复合信号。
    默认子因子: BM (Book-to-Market) + STR + IVOL + MAX + ABT。

    每个子因子独立排名后按权重加总，返回归一化分数。

    Args:
        subfactors: {因子名: {symbol: value}} 子因子字典
        weights: {因子名: weight} 权重字典，默认等权

    Returns:
        {symbol: composite_score} — 分数越高越值得做多

    References:
        De Bondt & Thaler (1985) "Does the Stock Market Overreact?"
        Stambaugh, Yu, Yuan (2015) "Arbitrage Asymmetry"
    """
    if not subfactors:
        return {}
    # 收集所有出现过的 symbol
    all_symbols: set[str] = set()
    for fv in subfactors.values():
        all_symbols.update(fv.keys())
    if not all_symbols:
        return {}

    if weights is None:
        weights = {name: 1.0 / len(subfactors) for name in subfactors}

    composite: dict[str, float] = {s: 0.0 for s in all_symbols}
    n = len(all_symbols)

    for fname, fdict in subfactors.items():
        w = weights.get(fname, 0.0)
        if w == 0.0:
            continue
        scored = []
        for s in all_symbols:
            v = fdict.get(s, float("nan"))
            scored.append((s, v if math.isfinite(v) else -float("inf")))
        scored.sort(key=lambda x: x[1], reverse=True)
        for rank_idx, (sym, _) in enumerate(scored):
            composite[sym] += w * ((n - rank_idx) / n)
    return composite


# ============================================================================
# PEG 因子 (Price/Earnings-to-Growth)
# ============================================================================


def compute_peg_factor(
    candidates: Sequence[EquityCandidateInfo],
    pe_field: str = "pe_ttm",
    growth_field: str = "dt_netprofit_yoy",
    max_peg: float = 5.0,
) -> dict[str, float]:
    """PEG 因子 — GARP (Growth At Reasonable Price) 核心因子。

    PEG = PE / 盈利增速(%)。PEG 低表示"相对成长性而言估值便宜"。
    Peter Lynch 经典选股指标: PEG < 1 为理想。

    本函数返回 **-PEG** (取反)，使得 PEG 越低得分越高，
    与 rank_normalize 的"高值=好"约定一致。

    Args:
        candidates: 候选标的列表
        pe_field: PE 字段名 (默认 ``pe_ttm``)
        growth_field: 盈利增速字段名 (默认 ``dt_netprofit_yoy``, 单位 %)
        max_peg: PEG 上限截断 (避免极端值干扰排名)

    Returns:
        {symbol: -peg_value} — PEG 越低 → 值越大 (越优)

    References:
        Lynch, P. (1989) "One Up on Wall Street"
        Estrada (2005) "Adjusting P/E ratios by growth and risk"
    """
    result: dict[str, float] = {}
    for c in candidates:
        pe = c.metadata.get(pe_field)
        growth = c.metadata.get(growth_field)
        if pe is None or growth is None:
            continue
        try:
            pe_val = float(pe)
            g_val = float(growth)
        except (TypeError, ValueError):
            continue
        # 过滤: PE 需正值, 增速需正值 (负增长的 PEG 无意义)
        if not math.isfinite(pe_val) or pe_val <= 0:
            continue
        if not math.isfinite(g_val) or g_val <= 1.0:
            continue
        peg = pe_val / g_val
        peg = min(peg, max_peg)
        result[c.symbol] = -peg  # 取反: 低 PEG = 高分
    return result


# ============================================================================
# 自由现金流收益率因子 (Free Cash Flow Yield)
# ============================================================================


def compute_fcfy_factor(
    candidates: Sequence[EquityCandidateInfo],
    fcf_field: str = "fcff",
    mv_field: str = "circ_mv",
) -> dict[str, float]:
    """自由现金流收益率因子 (FCFY = FCF / Market Cap)。

    巴菲特最重视的估值指标之一。高 FCFY 意味着公司产生的
    自由现金流相对市值充裕，是真实盈利能力的体现。

    当缺少直接 FCF 数据时，使用 **经营现金流 - 资本支出** 近似:
      FCF ≈ n_cashflow_act - capex (或简化为 cfps * total_share)

    Args:
        candidates: 候选标的列表
        fcf_field: 自由现金流字段名 (metadata 中)。
            若不存在则自动降级使用 n_cashflow_act - fixed_assets_delta
        mv_field: 市值字段 (默认用 circ_mv 属性, 单位万元)

    Returns:
        {symbol: fcfy} — FCFY 越高 = 越优

    References:
        Buffett (1996) Berkshire Hathaway Shareholder Letter
        Lakonishok, Shleifer, Vishny (1994) "Contrarian Investment"
    """
    result: dict[str, float] = {}
    for c in candidates:
        mv = getattr(c, mv_field, 0.0) or c.metadata.get(mv_field, 0)
        try:
            mv_val = float(mv)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(mv_val) or mv_val <= 0:
            continue

        # 尝试直接读 FCF
        fcf = c.metadata.get(fcf_field)
        if fcf is not None:
            try:
                fcf_val = float(fcf)
                if math.isfinite(fcf_val):
                    result[c.symbol] = fcf_val / (mv_val * 10000)
                    continue
            except (TypeError, ValueError):
                pass

        # 降级: 用经营现金流近似
        ocf = c.metadata.get("n_cashflow_act")
        if ocf is None:
            # 再降级: cfps * total_share
            cfps = c.metadata.get("cfps")
            total = c.metadata.get("total_share")
            if cfps is not None and total is not None:
                try:
                    ocf = float(cfps) * float(total) * 10000
                except (TypeError, ValueError):
                    continue
            else:
                continue
        try:
            ocf_val = float(ocf)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(ocf_val):
            continue
        # 简化: 假设 capex ~ 0, FCFY ≈ OCF/MV
        result[c.symbol] = ocf_val / (mv_val * 10000)
    return result


# ============================================================================
# 分红持续性因子 (Dividend Persistence)
# ============================================================================


def compute_dividend_persistence_factor(
    candidates: Sequence[EquityCandidateInfo],
    history_field: str = "dv_history",
    min_threshold: float = 0.5,
) -> dict[str, float]:
    """分红持续性因子 — 连续分红年数。

    衡量公司分红政策的持续性和稳定性。连续多年分红的公司
    通常具有稳定的盈利能力和良好的公司治理。

    计算逻辑:
    1. 从 metadata["dv_history"] 获取历史年度股息率列表
    2. 从最近一年向前回溯，统计连续分红(≥min_threshold%) 的年数
    3. 返回连续分红年数（越多越好）

    若 dv_history 不可用，降级使用 "dividend_years" 直接字段。

    Args:
        candidates: 候选标的列表
        history_field: 历史股息率列表字段
        min_threshold: 最低股息率阈值(%)，低于视为未分红

    Returns:
        {symbol: consecutive_years} — 连续分红年数，越多越优

    References:
        Lintner (1956) "Distribution of Incomes of Corporations"
        DeAngelo, DeAngelo, Skinner (2004) dividend policy
    """
    result: dict[str, float] = {}
    for c in candidates:
        # 路径 1: 使用历史股息率列表
        history = c.metadata.get(history_field)
        if history and isinstance(history, (list, tuple)):
            valid = []
            for v in history:
                try:
                    fv = float(v)
                    valid.append(fv if math.isfinite(fv) else 0.0)
                except (TypeError, ValueError):
                    valid.append(0.0)
            if valid:
                # 从最近一年向前回溯
                consec = 0
                for v in reversed(valid):
                    if v >= min_threshold:
                        consec += 1
                    else:
                        break
                result[c.symbol] = float(consec)
                continue

        # 路径 2: 降级使用直接字段
        dy = c.metadata.get("dividend_years")
        if dy is not None:
            try:
                result[c.symbol] = float(dy)
            except (TypeError, ValueError):
                pass
    return result

