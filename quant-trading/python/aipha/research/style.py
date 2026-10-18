"""Signal-date style budgets for long-only targets; cash has zero exposure."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def normalize_style_limits(value=()) -> tuple[tuple[str, float], ...]:
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, (tuple, list)):
        items = list(value)
    else:
        raise ValueError("max_style_exposure must be a mapping of factors to finite nonnegative limits")
    result = {}
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("max_style_exposure requires factor/limit pairs")
        name, bound = item
        if not isinstance(name, str) or not name or name.strip() != name or name in result:
            raise ValueError("max_style_exposure requires unique nonempty factor names")
        if isinstance(bound, (bool, str)) or not isinstance(bound, (int, float)) or not math.isfinite(bound) or bound < 0:
            raise ValueError("max_style_exposure limits must be finite nonnegative numbers")
        result[name] = float(bound)
    return tuple(sorted(result.items()))


def style_zscores(reference: pd.DataFrame, limits, signal_dates) -> pd.DataFrame:
    """Standardize each complete eligible cross-section, never across time."""
    names = [name for name, _ in normalize_style_limits(limits)]
    required = {"trade_date", "symbol", *names}
    if required - set(reference):
        raise ValueError(f"style reference missing columns: {sorted(required - set(reference))}")
    frame = reference[["trade_date", "symbol", *names]].copy()
    frame["trade_date"] = pd.to_datetime(frame.trade_date, errors="raise").dt.date
    dates = {pd.Timestamp(day).date() for day in signal_dates}
    frame = frame.loc[frame.trade_date.isin(dates)]
    if dates - set(frame.trade_date) or frame.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("style reference requires a unique complete cross-section on every signal date")
    for name in names:
        values = pd.to_numeric(frame[name], errors="coerce").astype(float)
        if not np.isfinite(values).all():
            raise ValueError(f"style reference requires finite {name} for every eligible symbol")
        grouped = values.groupby(frame.trade_date)
        std = grouped.transform(lambda rows: rows.std(ddof=0))
        frame[name] = (values - grouped.transform("mean")) / std.replace(0, 1.)
        if not np.isfinite(frame[name]).all():
            raise ValueError(f"cannot standardize style factor {name}")
    return frame


def constrain_style_weights(symbols, weight: float, cross: pd.DataFrame, limits) -> np.ndarray:
    """Keep the ranked shortlist, maximize capital, then prefer higher ranks."""
    limits = normalize_style_limits(limits)
    names = [name for name, _ in limits]
    if set(symbols) - set(cross.symbol):
        raise ValueError("target symbol missing from the eligible style reference")
    exposure = cross.set_index("symbol").loc[symbols, names].to_numpy(dtype=float).T
    caps = np.array([bound for _, bound in limits])
    weights = np.full(len(symbols), weight)
    if (np.abs(exposure @ weights) <= caps + 1e-10).all():
        return weights
    from scipy.optimize import linprog

    matrix, bounds = np.concatenate([exposure, -exposure]), np.concatenate([caps, caps])
    options = {"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9}
    maximum = linprog(-np.ones(len(symbols)), A_ub=matrix, b_ub=bounds,
                      bounds=[(0., weight)] * len(symbols), method="highs", options=options)
    if not maximum.success:
        raise ValueError(f"style allocation failed: {maximum.message}")
    preferred = linprog(-np.arange(len(symbols), 0, -1, dtype=float), A_ub=matrix, b_ub=bounds,
                        A_eq=np.ones((1, len(symbols))), b_eq=[maximum.x.sum()],
                        bounds=[(0., weight)] * len(symbols), method="highs", options=options)
    if not preferred.success:
        raise ValueError(f"style rank allocation failed: {preferred.message}")
    weights = np.clip(preferred.x, 0., weight)
    if (np.abs(exposure @ weights) > caps + 1e-8).any():
        raise ValueError("style allocation violated its exposure limit")
    return weights


def validate_target_styles(targets, reference: pd.DataFrame, limits) -> list[dict]:
    """Independently recompute target budgets from the signal-date universe."""
    limits = normalize_style_limits(limits)
    if not limits or not targets:
        return []
    frame = style_zscores(reference, limits, [target.rebalance_date for target in targets])
    by_date = {}
    for target in targets:
        day = pd.Timestamp(target.rebalance_date).date()
        by_date.setdefault(day, []).append(target)
    result = []
    for day, rows in sorted(by_date.items()):
        cross = frame.loc[frame.trade_date.eq(day)].set_index("symbol")
        exposure = {name: 0. for name, _ in limits}
        for target in rows:
            if target.target_weight == 0:
                continue
            if target.symbol not in cross.index:
                raise ValueError("target symbol missing from the eligible style reference")
            for name, _ in limits:
                exposure[name] += target.target_weight * float(cross.loc[target.symbol, name])
        if any(abs(exposure[name]) > bound + 1e-8 for name, bound in limits):
            raise ValueError("target portfolio exceeds max_style_exposure")
        result.append({"rebalance_date": day.isoformat(), "weighted_zscores": exposure,
                       "invested_weight": sum(target.target_weight for target in rows)})
    return result
