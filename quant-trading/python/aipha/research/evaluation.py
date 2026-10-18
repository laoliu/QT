"""Report metrics with explicit observation columns and evaluation dates."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from aipha.utils.metrics import compute_annualized_volatility


def json_safe_metrics(value: Any) -> Any:
    """Represent undefined diagnostics as JSON null, never NaN/Infinity."""
    if isinstance(value, dict):
        return {key: json_safe_metrics(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_metrics(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def feature_missing_rates(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    """Measure the exact model inputs, including non-finite numeric values."""
    missing = set(columns) - set(frame)
    if missing:
        raise ValueError(f"model feature columns absent from report input: {sorted(missing)}")
    if not columns or frame.empty:
        return {"mean": 0.0, "by_feature": {}, "rows": len(frame), "columns": list(columns)}
    values = frame[columns].apply(pd.to_numeric, errors="coerce").astype(float)
    rates = (~np.isfinite(values)).mean()
    return {"mean": float(rates.mean()), "by_feature": rates.to_dict(),
            "rows": len(frame), "columns": list(columns)}


def benchmark_diagnostics(
    source: pd.DataFrame,
    predictions: pd.DataFrame,
    portfolio_returns: list[dict[str, Any]] | pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Align benchmark and strategy to identical close-to-close intervals.

    A replay's first row is a NAV anchor. Missing benchmark sessions invalidate
    headline metrics rather than being dropped or forward filled. Without a
    replay, the prediction-date range defines the diagnostic window.
    """
    column = next((c for c in ("benchmark_return", "benchmark_close") if c in source), None)
    if column is None:
        return {"available": False, "reason": "benchmark_not_in_snapshot"}
    dates = pd.to_datetime(source["trade_date"], errors="raise").dt.normalize()
    values = pd.to_numeric(source[column], errors="coerce")
    if (values.groupby(dates).nunique() > 1).any():
        raise ValueError("benchmark values disagree across symbols on the same date")
    series = values.groupby(dates).first().sort_index()
    if column == "benchmark_close":
        series = series.where(np.isfinite(series) & series.gt(0)).pct_change(fill_method=None)
    else:
        series = series.where(np.isfinite(series) & series.ge(-1))

    strategy_returns = None
    if portfolio_returns is not None:
        strategy = pd.DataFrame(portfolio_returns)
        if not {"trade_date", "daily_return"}.issubset(strategy):
            return {"available": False, "reason": "no_portfolio_replay_observations"}
        strategy["trade_date"] = pd.to_datetime(strategy["trade_date"], errors="raise").dt.normalize()
        strategy = strategy.sort_values("trade_date")
        if strategy.trade_date.isna().any() or strategy.trade_date.duplicated().any():
            raise ValueError("portfolio replay dates must be valid and unique")
        strategy_returns = pd.to_numeric(strategy.set_index("trade_date")["daily_return"], errors="coerce").iloc[1:]
        expected = strategy_returns.index
        start = strategy.trade_date.iloc[0] if len(strategy) else None
        end = strategy.trade_date.iloc[-1] if len(strategy) else None
        scope = "portfolio_replay"
    else:
        prediction_dates = pd.to_datetime(predictions["trade_date"], errors="raise")
        start, end = prediction_dates.min(), prediction_dates.max()
        expected = series.index[(series.index > start) & (series.index <= end)]
        scope = "prediction_oos"

    aligned = series.reindex(expected)
    observed = int(aligned.notna().sum())
    info: dict[str, Any] = {
        "available": False, "scope": scope,
        "start": start.date().isoformat() if start is not None and pd.notna(start) else None,
        "end": end.date().isoformat() if end is not None and pd.notna(end) else None,
        "expected_return_observations": len(expected),
        "observed_return_observations": observed,
        "missing_return_observations": len(expected) - observed,
    }
    if not len(expected) or observed != len(expected):
        info["reason"] = "incomplete_benchmark_coverage" if len(expected) else "no_return_intervals"
        info["relative"] = {"available": False, "reason": info["reason"], "aligned_days": observed}
        return info
    nav = pd.Series(np.r_[1.0, (1.0 + aligned).cumprod().to_numpy()])
    total = float(nav.iloc[-1] - 1.0)
    info.update({
        "available": True, "total_return": total,
        "annualized_return": float(nav.iloc[-1] ** (252 / len(aligned)) - 1.0),
        "annualized_volatility": compute_annualized_volatility(aligned.to_numpy(), trading_days=252),
        "max_drawdown": float((nav / nav.cummax() - 1.0).min()),
    })
    if strategy_returns is not None:
        if not np.isfinite(strategy_returns).all():
            raise ValueError("portfolio replay returns must be finite")
        active = strategy_returns - aligned
        tracking_error = compute_annualized_volatility(active.to_numpy(), trading_days=252)
        strategy_total = float((1.0 + strategy_returns).prod() - 1.0)
        info["relative"] = {
            "available": True, "aligned_days": len(aligned),
            "strategy_total_return": strategy_total, "benchmark_total_return": total,
            "excess_return": strategy_total - total, "tracking_error": tracking_error,
            "information_ratio": float(active.mean() * 252 / tracking_error) if tracking_error > 0 else 0.0,
        }
    return info
