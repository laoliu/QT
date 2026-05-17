"""Strategy health monitor.

Implements Principle B + E alpha-decay monitoring from
copilot-instructions.md §1.4:

- Rolling 12-month Sharpe < 0 for > 6 months -> forced review
- Rolling 12-month SR < 40% of full-period SR -> alpha decay
- Recent 3 years SR below full period -> decay warning

Consumes a daily returns series (list of (date, daily_return)
tuples) and produces a structured health verdict.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = TRADING_DAYS_PER_YEAR  # ~1 year
NEGATIVE_SR_MONTHS_THRESHOLD = 6
ALPHA_DECAY_RATIO = 0.40
RECENT_YEARS_FOR_DECAY = 3


@dataclass
class HealthIssue:
    """Single health monitor finding."""
    strategy: str
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class StrategyHealthReport:
    """Aggregate strategy health report."""
    strategy: str = ""
    n_days: int = 0
    full_sr: float = 0.0
    recent_3y_sr: float | None = None
    last_12m_sr: float | None = None
    consecutive_neg_12m_months: int = 0
    verdict: str = "unknown"  # healthy | warning | decay | dead
    issues: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _sharpe(returns: list[float]) -> float:
    """Annualized Sharpe with sqrt(244) convention."""
    if not returns:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    if n < 2:
        return 0.0
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std <= 1e-12:
        return 0.0
    return (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _rolling_sharpe_series(
    returns: list[float], window: int = ROLLING_WINDOW_DAYS,
) -> list[float]:
    """Rolling Sharpe with the given window length."""
    out: list[float] = []
    if len(returns) < window:
        return out
    for i in range(window, len(returns) + 1):
        out.append(_sharpe(returns[i - window:i]))
    return out


def _count_consecutive_negative_months(
    dates: list[date], rolling_sr: list[float],
) -> int:
    """Count trailing consecutive months with end-of-month rolling SR < 0."""
    if not rolling_sr or not dates:
        return 0
    aligned_dates = dates[-len(rolling_sr):]
    eom: dict[tuple[int, int], float] = {}
    for d, sr in zip(aligned_dates, rolling_sr):
        eom[(d.year, d.month)] = sr
    keys = sorted(eom.keys())
    run = 0
    for k in reversed(keys):
        if eom[k] < 0:
            run += 1
        else:
            break
    return run


def check_strategy_health(
    strategy: str,
    daily_returns: Iterable[tuple[date, float] | tuple[str, float]],
) -> StrategyHealthReport:
    """Evaluate rolling-SR health for a strategy.

    Args:
        strategy: strategy name.
        daily_returns: iterable of (date, daily_return) tuples. Dates
            can be date objects or ISO-format strings.
    """
    rows: list[tuple[date, float]] = []
    for d, r in daily_returns:
        if isinstance(d, str):
            d = date.fromisoformat(d)
        rows.append((d, float(r)))
    rows.sort(key=lambda x: x[0])

    dates = [r[0] for r in rows]
    rets = [r[1] for r in rows]

    report = StrategyHealthReport(strategy=strategy, n_days=len(rets))
    if len(rets) < 60:
        report.verdict = "insufficient_data"
        report.issues.append(HealthIssue(
            strategy=strategy, severity="info", code="HEALTH-000",
            message=f"only {len(rets)} trading days; need >=60 for SR",
        ).to_dict())
        return report

    report.full_sr = _sharpe(rets)

    if dates:
        cutoff_3y = dates[-1] - timedelta(days=int(RECENT_YEARS_FOR_DECAY * 365.25))
        recent = [r for d, r in rows if d >= cutoff_3y]
        if len(recent) >= 60:
            report.recent_3y_sr = _sharpe(recent)

    rolling = _rolling_sharpe_series(rets, ROLLING_WINDOW_DAYS)
    if rolling:
        report.last_12m_sr = rolling[-1]
        report.consecutive_neg_12m_months = _count_consecutive_negative_months(
            dates, rolling,
        )

    issues: list[HealthIssue] = []

    if report.consecutive_neg_12m_months >= NEGATIVE_SR_MONTHS_THRESHOLD:
        issues.append(HealthIssue(
            strategy=strategy, severity="error", code="HEALTH-DEAD-001",
            message=f"rolling 12M SR < 0 for {report.consecutive_neg_12m_months} "
                    f"consecutive months (>= {NEGATIVE_SR_MONTHS_THRESHOLD} "
                    "triggers forced review per Principle E)",
        ))
        report.verdict = "dead"

    elif (
        report.recent_3y_sr is not None
        and report.full_sr > 0
        and report.recent_3y_sr < ALPHA_DECAY_RATIO * report.full_sr
    ):
        issues.append(HealthIssue(
            strategy=strategy, severity="warning", code="HEALTH-DECAY-001",
            message=f"recent 3Y SR={report.recent_3y_sr:.2f} < "
                    f"{ALPHA_DECAY_RATIO:.0%} of full SR={report.full_sr:.2f} "
                    "(Principle B alpha-decay signal)",
        ))
        report.verdict = "decay"

    elif report.last_12m_sr is not None and report.last_12m_sr < 0:
        issues.append(HealthIssue(
            strategy=strategy, severity="warning", code="HEALTH-WARN-001",
            message=f"current rolling 12M SR={report.last_12m_sr:.2f} < 0",
        ))
        report.verdict = "warning"

    else:
        report.verdict = "healthy"

    report.issues = [i.to_dict() for i in issues]
    return report
