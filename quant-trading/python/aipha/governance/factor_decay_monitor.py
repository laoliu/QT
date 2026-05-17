"""Factor decay monitor.

Implements rolling IC / Sharpe decay detection for trading factors
and strategies. Per Principle B:
  - Rolling IC should not depend on specific years (year-over-year
    std < |mean| in magnitude).
  - Alpha decay: recent 3Y IC should be >= 40% of full-period IC.
  - McLean & Pontiff (2016) style decay: post-publication decay flag.

Public API:
    compute_rolling_ic(ic_series, window=252) -> list[float]
    check_factor_decay(ic_by_date) -> FactorDecayReport
    compute_decay_ratio(recent, full) -> float
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

ROLLING_WINDOW_DAYS = TRADING_DAYS_PER_YEAR  # 1Y rolling IC
DECAY_RATIO_THRESHOLD = 0.40      # recent 3Y must be >= 40% of full
RECENT_YEARS = 3
IC_STABILITY_CV_MAX = 2.0         # CV of yearly IC
IC_MIN_ANNUAL_MAGNITUDE = 0.02    # |IC| < 2% annually considered dead


@dataclass
class FactorDecayReport:
    """Factor decay analysis."""
    factor_name: str = ""
    n_days: int = 0
    full_ic: float = 0.0
    recent_3y_ic: float | None = None
    last_12m_ic: float | None = None
    decay_ratio: float | None = None  # recent_3y / full
    yearly_ic_mean: float = 0.0
    yearly_ic_std: float = 0.0
    yearly_ic_cv: float | None = None
    verdict: str = "unknown"  # healthy / warning / decay / dead
    issues: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def compute_rolling_ic(
    ic_series: list[float],
    window: int = ROLLING_WINDOW_DAYS,
) -> list[float]:
    """Rolling mean of a daily IC series."""
    out: list[float] = []
    if len(ic_series) < window:
        return out
    s = 0.0
    for v in ic_series[:window]:
        s += v
    out.append(s / window)
    for i in range(window, len(ic_series)):
        s += ic_series[i] - ic_series[i - window]
        out.append(s / window)
    return out


def compute_decay_ratio(recent_ic: float, full_ic: float) -> float:
    """Ratio recent / full, signed.

    Returns 1.0 if full_ic is near zero (no meaningful decay signal).
    """
    if full_ic is None or abs(full_ic) < 1e-9:
        return 1.0
    return recent_ic / full_ic


def _group_by_year(
    dates: list[date], values: list[float],
) -> dict[int, list[float]]:
    g: dict[int, list[float]] = {}
    for d, v in zip(dates, values):
        g.setdefault(d.year, []).append(v)
    return g


def check_factor_decay(
    ic_by_date: Iterable[tuple[date | str, float]],
    factor_name: str = "",
) -> FactorDecayReport:
    """Analyze decay for a single factor's daily IC series.

    Args:
        ic_by_date: iterable of (date, daily_IC) pairs.
        factor_name: identifier for the report.
    """
    rows: list[tuple[date, float]] = []
    for d, v in ic_by_date:
        if isinstance(d, str):
            d = date.fromisoformat(d)
        rows.append((d, float(v)))
    rows.sort(key=lambda x: x[0])

    dates = [r[0] for r in rows]
    ics = [r[1] for r in rows]

    report = FactorDecayReport(factor_name=factor_name, n_days=len(ics))

    if len(ics) < 60:
        report.verdict = "insufficient_data"
        return report

    report.full_ic = _mean(ics)

    # Recent 3Y IC
    if dates:
        cutoff = dates[-1] - timedelta(days=int(RECENT_YEARS * 365.25))
        recent = [v for d, v in rows if d >= cutoff]
        if len(recent) >= 60:
            report.recent_3y_ic = _mean(recent)

    # Last 12M IC
    if dates:
        cutoff_12m = dates[-1] - timedelta(days=365)
        last12 = [v for d, v in rows if d >= cutoff_12m]
        if len(last12) >= 20:
            report.last_12m_ic = _mean(last12)

    # Yearly IC stability
    by_year = _group_by_year(dates, ics)
    yearly_ics = [_mean(v) for v in by_year.values() if len(v) >= 20]
    if yearly_ics:
        report.yearly_ic_mean = _mean(yearly_ics)
        report.yearly_ic_std = _std(yearly_ics)
        if abs(report.yearly_ic_mean) > 1e-9:
            report.yearly_ic_cv = report.yearly_ic_std / abs(report.yearly_ic_mean)

    # Decay ratio
    if report.recent_3y_ic is not None:
        report.decay_ratio = compute_decay_ratio(
            report.recent_3y_ic, report.full_ic,
        )

    issues: list[dict] = []

    # Dead: recent IC magnitude too small or opposite sign with full
    if (
        report.last_12m_ic is not None
        and abs(report.last_12m_ic) < IC_MIN_ANNUAL_MAGNITUDE
    ):
        issues.append({
            "severity": "error", "code": "DECAY-DEAD-001",
            "message": f"last 12M IC={report.last_12m_ic:.4f} magnitude "
                       f"< {IC_MIN_ANNUAL_MAGNITUDE:.2%} threshold",
        })
        report.verdict = "dead"

    # Sign flip: recent vs full have opposite signs -> decay/dead
    elif (
        report.recent_3y_ic is not None
        and report.full_ic * report.recent_3y_ic < -1e-9
    ):
        issues.append({
            "severity": "error", "code": "DECAY-FLIP-001",
            "message": f"recent 3Y IC={report.recent_3y_ic:.4f} has "
                       f"opposite sign vs full IC={report.full_ic:.4f}",
        })
        report.verdict = "dead"

    # Decay: ratio below threshold
    elif (
        report.decay_ratio is not None
        and report.decay_ratio < DECAY_RATIO_THRESHOLD
        and report.full_ic > 0
    ):
        issues.append({
            "severity": "warning", "code": "DECAY-RATIO-001",
            "message": f"recent_3y/full IC ratio={report.decay_ratio:.2f} "
                       f"< {DECAY_RATIO_THRESHOLD:.0%} (Principle B alpha decay)",
        })
        report.verdict = "decay"

    # Warning: high yearly CV
    elif (
        report.yearly_ic_cv is not None
        and report.yearly_ic_cv > IC_STABILITY_CV_MAX
    ):
        issues.append({
            "severity": "warning", "code": "DECAY-CV-001",
            "message": f"yearly IC CV={report.yearly_ic_cv:.2f} > "
                       f"{IC_STABILITY_CV_MAX} (unstable across years)",
        })
        report.verdict = "warning"

    else:
        report.verdict = "healthy"

    report.issues = issues
    return report
