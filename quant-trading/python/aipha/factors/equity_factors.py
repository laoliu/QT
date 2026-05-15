"""股票因子计算工具集 — 估值/质量/动量/波动/成长/分红/行为/复合"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    pass  # EquityCandidateInfo 等仅 type hint


# ── 辅助 ─────────────────────────────────────────────────────

def _val(candidate: Any, field: str, default: float | None = None) -> float | None:
    v = getattr(candidate, field, None)
    if v is None and isinstance(candidate, dict):
        v = candidate.get(field)
    if v is None:
        return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _code(c: Any) -> str:
    return getattr(c, "code", str(c))


# ── 估值类 ────────────────────────────────────────────────────

def compute_value_factor(candidates: Sequence[Any], metric: str = "pe_ttm") -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        v = _val(c, metric)
        if v is not None and v > 0:
            result[_code(c)] = -v  # 低估值 = 高分
    return result


def compute_ep_factor(candidates: Sequence[Any], metric: str = "pe_ttm") -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        pe = _val(c, metric)
        if pe is not None and pe > 0:
            result[_code(c)] = 1.0 / pe
    return result


def compute_bp_factor(candidates: Sequence[Any], metric: str = "pb") -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        pb = _val(c, metric)
        if pb is not None and pb > 0:
            result[_code(c)] = 1.0 / pb
    return result


def compute_peg_factor(
    candidates: Sequence[Any],
    pe_field: str = "pe_ttm",
    growth_field: str = "dt_netprofit_yoy",
    max_peg: float = 5.0,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        pe = _val(c, pe_field)
        g = _val(c, growth_field)
        if pe is not None and g is not None and g > 0 and pe > 0:
            peg = pe / g
            if 0 < peg < max_peg:
                result[_code(c)] = -peg
    return result


def compute_fcfy_factor(
    candidates: Sequence[Any],
    fcf_field: str = "fcff",
    mv_field: str = "circ_mv",
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        fcf = _val(c, fcf_field)
        mv = _val(c, mv_field)
        if fcf is not None and mv is not None and mv > 0:
            result[_code(c)] = fcf / mv
    return result


# ── 质量类 ────────────────────────────────────────────────────

def compute_quality_factor(candidates: Sequence[Any], metric: str = "roe") -> dict[str, float]:
    return {_code(c): v for c in candidates if (v := _val(c, metric)) is not None}


def compute_debt_factor(candidates: Sequence[Any], metric: str = "debt_to_assets") -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        v = _val(c, metric)
        if v is not None:
            result[_code(c)] = -v  # 低负债 = 高分
    return result


def compute_piotroski_factor(
    candidates: Sequence[Any], min_dimensions: int = 5, *, min_dims: int | None = None,
) -> dict[str, float]:
    if min_dims is not None:
        min_dimensions = min_dims
    from aipha.factors.quality_advanced import PiotroskiFScoreFactor, _safe_float
    result: dict[str, float] = {}
    for c in candidates:
        fund = getattr(c, "fundamentals", {}) or {}
        dims_avail = sum(1 for k in ["roa", "ocfps", "debt_to_assets", "current_ratio",
                                      "gross_margin", "asset_turnover"] if fund.get(k) is not None)
        if dims_avail < min_dimensions:
            continue
        score = 0
        roa = _safe_float(fund.get("roa"))
        if roa is not None and roa > 0:
            score += 1
        cfo = _safe_float(fund.get("ocfps"))
        if cfo is not None and cfo > 0:
            score += 1
        if cfo is not None and roa is not None and cfo > roa:
            score += 1
        result[_code(c)] = float(score)
    return result


def compute_accruals_quality_factor(candidates: Sequence[Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        fund = getattr(c, "fundamentals", {}) or {}
        ni = _val(fund, "eps") if isinstance(fund, dict) else _val(c, "eps")
        cfo = _val(fund, "ocfps") if isinstance(fund, dict) else _val(c, "ocfps")
        ta = _val(fund, "total_assets") if isinstance(fund, dict) else _val(c, "total_assets")
        if ni is not None and cfo is not None and ta is not None and abs(ta) > 1e-10:
            result[_code(c)] = -(ni - cfo) / ta
    return result


def compute_earnings_stability_factor(
    candidates: Sequence[Any], metric: str = "roe", min_periods: int = 3,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        hist = getattr(c, f"{metric}_history", None) or getattr(c, "fundamentals", {}).get(f"{metric}_history")
        if hist is None:
            continue
        vals = list(hist.values()) if isinstance(hist, dict) else list(hist)
        nums = [float(v) for v in vals if v is not None]
        if len(nums) < min_periods:
            continue
        import statistics
        mu = statistics.mean(nums)
        if abs(mu) < 1e-10:
            continue
        cv = statistics.stdev(nums) / abs(mu)
        result[_code(c)] = 1.0 / (1.0 + cv)
    return result


# ── 规模 ──────────────────────────────────────────────────────

def compute_size_factor(candidates: Sequence[Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        mv = _val(c, "circ_mv")
        if mv is not None and mv > 0:
            result[_code(c)] = -math.log(mv)
    return result


# ── 换手率 ─────────────────────────────────────────────────────

def compute_turnover_factor(candidates: Sequence[Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        tr = _val(c, "turnover_rate")
        if tr is not None:
            result[_code(c)] = -tr
    return result


# ── 动量 ──────────────────────────────────────────────────────

def compute_momentum_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 20,
    invert: bool = False,
    skip_recent: int = 0,
    multi_period: bool = False,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        if prices is None or len(prices) < lookback + skip_recent:
            continue
        end = len(prices) - skip_recent if skip_recent > 0 else len(prices)
        start = end - lookback
        if start < 0 or prices[start] <= 0:
            continue
        mom = prices[end - 1] / prices[start] - 1
        if invert:
            mom = -mom
        result[code] = mom
    return result


def compute_risk_adjusted_momentum(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 20,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        if prices is None or len(prices) < lookback + 1:
            continue
        seg = prices[-(lookback + 1):]
        rets = [(seg[i] / seg[i - 1] - 1) for i in range(1, len(seg)) if seg[i - 1] > 0]
        if len(rets) < 5:
            continue
        import statistics
        mu = statistics.mean(rets)
        std = statistics.stdev(rets)
        if std > 1e-10:
            result[code] = mu / std
    return result


# ── 波动率 ─────────────────────────────────────────────────────

def compute_volatility_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 20,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        if prices is None or len(prices) < lookback + 1:
            continue
        seg = prices[-(lookback + 1):]
        rets = [(seg[i] / seg[i - 1] - 1) for i in range(1, len(seg)) if seg[i - 1] > 0]
        if len(rets) < 5:
            continue
        import statistics
        result[code] = -statistics.stdev(rets)  # 低波 = 高分
    return result


def compute_ivol_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    market_returns: list[float] | None = None,
    lookback: int = 20,
) -> dict[str, float]:
    """特异波动率 (IVOL): 剔除市场 beta 后的残差波动"""
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        if prices is None or len(prices) < lookback + 1:
            continue
        seg = prices[-(lookback + 1):]
        rets = [(seg[i] / seg[i - 1] - 1) for i in range(1, len(seg)) if seg[i - 1] > 0]
        if len(rets) < 5:
            continue
        if market_returns is not None and len(market_returns) >= len(rets):
            mkt = market_returns[-len(rets):]
            # OLS
            mx = sum(mkt) / len(mkt)
            my = sum(rets) / len(rets)
            cov = sum((m - mx) * (r - my) for m, r in zip(mkt, rets))
            var = sum((m - mx) ** 2 for m in mkt)
            beta = cov / var if var > 1e-15 else 0
            residuals = [r - beta * m for r, m in zip(rets, mkt)]
        else:
            residuals = rets
        import statistics
        result[code] = -statistics.stdev(residuals)  # 低 IVOL = 高分
    return result


def compute_ewma_volatility_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 20,
    decay: float = 0.94,
    *,
    halflife: int | None = None,
) -> dict[str, float]:
    if halflife is not None:
        decay = 1 - math.log(2) / halflife
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        if prices is None or len(prices) < lookback + 1:
            continue
        seg = prices[-(lookback + 1):]
        rets = [(seg[i] / seg[i - 1] - 1) for i in range(1, len(seg)) if seg[i - 1] > 0]
        if len(rets) < 2:
            continue
        var = rets[0] ** 2
        for r in rets[1:]:
            var = decay * var + (1 - decay) * r ** 2
        result[code] = -math.sqrt(var)
    return result


# ── 成长 ──────────────────────────────────────────────────────

def compute_earnings_growth_factor(
    candidates: Sequence[Any], field: str = "dt_netprofit_yoy",
) -> dict[str, float]:
    return {_code(c): v for c in candidates if (v := _val(c, field)) is not None}


def compute_dividend_factor(
    candidates: Sequence[Any], field: str = "dv_ratio",
) -> dict[str, float]:
    return {_code(c): v for c in candidates if (v := _val(c, field)) is not None and v > 0}


def compute_dividend_persistence_factor(
    candidates: Sequence[Any],
    history_field: str = "dv_history",
    min_threshold: float = 0.5,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        hist = getattr(c, history_field, None)
        if not hist:
            continue
        vals = list(hist.values()) if isinstance(hist, dict) else list(hist)
        count = sum(1 for v in vals if v is not None and float(v) >= min_threshold)
        result[_code(c)] = float(count)
    return result


# ── 估值百分位 ─────────────────────────────────────────────────

def compute_valuation_percentile_factor(
    candidates: Sequence[Any],
    metric: str = "pe_ttm",
    history_field: str | None = None,
    min_history: int = 20,
) -> dict[str, float]:
    hist_key = history_field or f"{metric}_history"
    result: dict[str, float] = {}
    for c in candidates:
        hist = getattr(c, hist_key, None)
        current = _val(c, metric)
        if hist is None or current is None:
            continue
        vals = list(hist.values()) if isinstance(hist, dict) else list(hist)
        nums = sorted(float(v) for v in vals if v is not None)
        if len(nums) < min_history:
            continue
        pct = sum(1 for v in nums if v <= current) / len(nums)
        result[_code(c)] = -pct  # 低百分位 = 便宜 = 高分
    return result


# ── 行为因子 ──────────────────────────────────────────────────

def compute_cgo_factor(
    price_history: dict[str, list[float]],
    volume_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 60,
) -> dict[str, float]:
    """Capital Gains Overhang — 未实现盈利比例"""
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        vols = volume_history.get(code)
        if prices is None or vols is None:
            continue
        if len(prices) < lookback or len(vols) < lookback:
            continue
        p = prices[-lookback:]
        v = vols[-lookback:]
        total_vol = sum(v) or 1
        ref_price = sum(pi * vi for pi, vi in zip(p, v)) / total_vol
        current = prices[-1]
        result[code] = (current - ref_price) / ref_price if ref_price > 0 else 0.0
    return result


def compute_abnormal_turnover_factor(
    volume_history: dict[str, list[float]],
    candidates: Sequence[Any],
    short_window: int = 20,
    long_window: int = 250,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        vols = volume_history.get(code)
        if vols is None or len(vols) < long_window:
            continue
        short_avg = sum(vols[-short_window:]) / short_window
        long_avg = sum(vols[-long_window:]) / long_window
        if long_avg > 0:
            result[code] = short_avg / long_avg - 1
    return result


def compute_max_daily_return_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 20,
) -> dict[str, float]:
    """MAX 因子: 最近 lookback 日内单日最大收益 (lottery effect)"""
    result: dict[str, float] = {}
    for c in candidates:
        code = _code(c)
        prices = price_history.get(code)
        if prices is None or len(prices) < lookback + 1:
            continue
        seg = prices[-(lookback + 1):]
        rets = [(seg[i] / seg[i - 1] - 1) for i in range(1, len(seg)) if seg[i - 1] > 0]
        if rets:
            result[code] = -max(rets)  # 高 MAX = 彩票效应 = 低分
    return result


def compute_str_factor(
    price_history: dict[str, list[float]],
    candidates: Sequence[Any],
    lookback: int = 5,
) -> dict[str, float]:
    """短期反转"""
    return compute_momentum_factor(price_history, candidates, lookback=lookback, invert=True)


# ── 复合 ──────────────────────────────────────────────────────

def compute_overreaction_composite(
    subfactors: dict[str, dict[str, float]],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """多因子 rank-blend 复合评分"""
    if not subfactors:
        return {}
    # Rank normalize each sub-factor
    ranked: dict[str, dict[str, float]] = {}
    for name, vals in subfactors.items():
        sorted_codes = sorted(vals.keys(), key=lambda c: vals[c])
        n = len(sorted_codes)
        ranked[name] = {c: (i + 1) / n for i, c in enumerate(sorted_codes)} if n > 0 else {}

    all_codes = set()
    for r in ranked.values():
        all_codes.update(r.keys())

    w = weights or {n: 1.0 for n in subfactors}
    total_w = sum(w.get(n, 1.0) for n in subfactors)

    result: dict[str, float] = {}
    for code in all_codes:
        score = 0.0
        for name in subfactors:
            if code in ranked[name]:
                score += w.get(name, 1.0) * ranked[name][code]
        result[code] = score / total_w
    return result


def winsorize_factors(
    factor_values: dict[str, float],
    lower_pct: float = 0.01,
    upper_pct: float = 0.99,
) -> dict[str, float]:
    """缩尾处理"""
    if not factor_values:
        return {}
    vals = sorted(factor_values.values())
    n = len(vals)
    lo = vals[max(0, int(n * lower_pct))]
    hi = vals[min(n - 1, int(n * upper_pct))]
    return {k: max(lo, min(hi, v)) for k, v in factor_values.items()}


def score_candidates_with_factors(
    candidates: Sequence[Any],
    factor_specs: list[dict[str, Any]],
    rank_normalize_fn: Any = None,
) -> dict[str, float]:
    """按因子规格列表计算综合得分

    factor_specs: [{"func": callable, "weight": float, "kwargs": {...}}, ...]
    """
    subfactors: dict[str, dict[str, float]] = {}
    weights: dict[str, float] = {}
    for i, spec in enumerate(factor_specs):
        func = spec["func"]
        kw = spec.get("kwargs", {})
        name = spec.get("name", f"f{i}")
        w = spec.get("weight", 1.0)
        vals = func(candidates=candidates, **kw)
        if rank_normalize_fn:
            vals = rank_normalize_fn(vals)
        subfactors[name] = vals
        weights[name] = w
    return compute_overreaction_composite(subfactors, weights)
