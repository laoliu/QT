"""Portfolio lifecycle health monitor.

Reads NAV history, computes health metrics (rolling SR, MDD, alpha decay),
evaluates promotion/demotion gates, and outputs actionable recommendations.

Integrates with:
- portfolio_lifecycle.py (state transitions)
- failure_counter.py (failure state)
- feishu_alert.py (notifications)
- metrics_collector.py (raw metrics)

References:
- Phase D items 11-14
- §1.2a Three-layer AR framework
- §1.4 Five principles (Principle B: alpha decay)
"""
from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROJ_ROOT = PROJECT_ROOT
_STATE_DIR = _PROJ_ROOT / "data" / "paper_state"
_METRICS_DIR = _PROJ_ROOT / "data" / "metrics"

TRADING_DAYS_PER_YEAR = 244


# ── Health Recommendation ──


class HealthAction(str, enum.Enum):
    """Recommended action from lifecycle monitor."""

    MAINTAIN = "maintain"           # All clear, continue
    REVIEW = "review"               # Needs human review (soft breach)
    DEMOTE = "demote"               # Recommend demotion to OBSERVATION
    SUSPEND = "suspend"             # Recommend suspension (hard breach)
    PROMOTE_REVIEW = "promote_review"  # Eligible for promotion review
    FREEZE = "freeze"               # Critical issue, immediate freeze


# ── Gate Definitions ──


@dataclass(frozen=True)
class PromotionGate:
    """Hard gates for Candidate/Canary → Active promotion."""

    min_paper_days: int = 60          # S4 minimum observation period
    min_p22_ar: float = 0.13          # Tier S P22 hard gate
    max_nav_deviation_pct: float = 5.0  # Paper vs backtest deviation
    min_signal_consistency: float = 0.90  # 90% signal match rate
    max_slippage_ratio: float = 1.5   # Actual/assumed slippage
    max_risk_contribution: float = 0.50  # Single strategy risk contribution

    # Soft gates (trigger REVIEW, not block)
    soft_min_sr: float = 1.2
    soft_max_mdd: float = 0.20
    soft_min_calmar: float = 0.8


@dataclass(frozen=True)
class DemotionGate:
    """Gates for demotion/suspension triggers."""

    sr_negative_months: int = 6       # SR < 0 for N months → SUSPEND
    sr_warning_months: int = 3        # SR < 0 for N months → REVIEW
    mdd_halt_pct: float = 0.30        # MDD > 30% → SUSPEND
    mdd_warning_pct: float = 0.20     # MDD > 20% → REVIEW
    consecutive_failures: int = 5     # → FREEZE (handled by failure_counter)
    risk_contribution_days: int = 10  # >50% for N days → REVIEW
    p22_below_gate_months: int = 3    # P22 below gate for N months → DEMOTE


# ── Health Report ──


@dataclass
class PortfolioHealthReport:
    """Health assessment for a single portfolio."""

    portfolio_id: str
    check_date: str
    status: str                  # Current lifecycle status
    action: HealthAction = HealthAction.MAINTAIN
    metrics: dict = field(default_factory=dict)
    gate_results: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action"] = self.action.value
        return d


# ── Core Monitor ──


class LifecycleMonitor:
    """Evaluates portfolio health and recommends lifecycle actions."""

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        promotion_gate: Optional[PromotionGate] = None,
        demotion_gate: Optional[DemotionGate] = None,
    ):
        self._state_dir = state_dir or _STATE_DIR
        self._promotion = promotion_gate or PromotionGate()
        self._demotion = demotion_gate or DemotionGate()

    def check_portfolio(self, portfolio_id: str) -> PortfolioHealthReport:
        """Run full health check on a single portfolio."""
        from aipha.runner.portfolio_lifecycle import load_lifecycle_registry

        registry = load_lifecycle_registry()
        entry = registry.get(portfolio_id)
        status = entry.status.value if entry else "unknown"

        report = PortfolioHealthReport(
            portfolio_id=portfolio_id,
            check_date=date.today().isoformat(),
            status=status,
        )

        # Load NAV history
        nav_history = self._load_nav_history(portfolio_id)
        if not nav_history:
            report.action = HealthAction.REVIEW
            report.reasons.append("No NAV history available")
            return report

        # Compute metrics
        metrics = self._compute_metrics(nav_history)
        report.metrics = metrics

        # Paper days
        paper_days = self._get_paper_days(portfolio_id)
        metrics["paper_days"] = paper_days

        # Evaluate demotion (higher priority)
        demote_action, demote_reasons = self._evaluate_demotion(
            portfolio_id, metrics, status,
        )

        # Evaluate promotion
        promote_action, promote_reasons = self._evaluate_promotion(
            portfolio_id, metrics, status,
        )

        # Priority: FREEZE > SUSPEND > DEMOTE > PROMOTE_REVIEW > REVIEW > MAINTAIN
        action_priority = [
            HealthAction.FREEZE, HealthAction.SUSPEND, HealthAction.DEMOTE,
            HealthAction.PROMOTE_REVIEW, HealthAction.REVIEW, HealthAction.MAINTAIN,
        ]

        final_action = HealthAction.MAINTAIN
        all_reasons: list[str] = []

        for action in action_priority:
            if demote_action == action:
                final_action = action
                all_reasons.extend(demote_reasons)
                break
            if promote_action == action:
                final_action = action
                all_reasons.extend(promote_reasons)
                break

        report.action = final_action
        report.reasons = all_reasons
        return report

    def check_all(self) -> list[PortfolioHealthReport]:
        """Run health checks on all active portfolios."""
        from aipha.runner.portfolio_lifecycle import load_lifecycle_registry

        registry = load_lifecycle_registry()
        reports: list[PortfolioHealthReport] = []
        for pid, entry in registry.items():
            if entry.status.is_active:
                try:
                    report = self.check_portfolio(pid)
                    reports.append(report)
                except Exception as exc:
                    logger.error("Health check failed for %s: %s", pid, exc)
                    reports.append(PortfolioHealthReport(
                        portfolio_id=pid,
                        check_date=date.today().isoformat(),
                        status=entry.status.value,
                        action=HealthAction.REVIEW,
                        reasons=[f"Check error: {exc}"],
                    ))
        return reports

    def _load_nav_history(self, portfolio_id: str) -> list[tuple[str, float]]:
        """Load NAV time series from state directory.

        Tries multiple sources: nav_history.json, state.json, daily_snapshots.
        Returns list of (date_str, nav_value) sorted by date.
        """
        # Try nav_history.json first
        nav_file = self._state_dir / portfolio_id / "nav_history.json"
        if nav_file.exists():
            try:
                with open(nav_file, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    result = []
                    for entry in data:
                        if isinstance(entry, dict):
                            d = entry.get("date") or entry.get("trade_date", "")
                            n = entry.get("nav", 0.0)
                            if d and n > 0:
                                result.append((str(d), float(n)))
                    if result:
                        return sorted(result, key=lambda x: x[0])
            except Exception:
                logger.debug("Failed to load nav_history.json for %s", portfolio_id)

        # Try state.json with nav_history array
        state_file = self._state_dir / portfolio_id / "state.json"
        if not state_file.exists():
            state_file = self._state_dir / f"{portfolio_id}_state.json"
        if state_file.exists():
            try:
                with open(state_file, encoding="utf-8") as f:
                    state = json.load(f)
                nav_hist = state.get("nav_history", [])
                result = []
                for entry in nav_hist:
                    if isinstance(entry, dict):
                        d = entry.get("date") or entry.get("trade_date", "")
                        n = entry.get("nav", 0.0)
                        if d and n > 0:
                            result.append((str(d), float(n)))
                if result:
                    return sorted(result, key=lambda x: x[0])
            except Exception:
                logger.debug("Failed to load state.json for %s", portfolio_id)

        # Try nav_history.jsonl (append-only log)
        jsonl_file = self._state_dir / portfolio_id / "nav_history.jsonl"
        if jsonl_file.exists():
            try:
                result = []
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        entry = json.loads(line)
                        d = entry.get("date") or entry.get("trade_date", "")
                        n = entry.get("nav", 0.0)
                        if d and n > 0:
                            result.append((str(d), float(n)))
                if result:
                    return sorted(result, key=lambda x: x[0])
            except Exception:
                logger.debug("Failed to load nav_history.jsonl for %s", portfolio_id)

        return []

    def _compute_metrics(
        self, nav_history: list[tuple[str, float]],
    ) -> dict:
        """Compute health metrics from NAV history."""
        if len(nav_history) < 2:
            return {"insufficient_data": True}

        navs = np.array([n for _, n in nav_history])
        dates = [d for d, _ in nav_history]

        # Daily returns
        returns = np.diff(navs) / navs[:-1]

        # Total return
        total_return = (navs[-1] / navs[0]) - 1.0

        # Annualized return (based on trading days)
        n_days = len(navs) - 1
        if n_days > 0:
            ann_return = (1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / n_days) - 1.0
        else:
            ann_return = 0.0

        # Max drawdown
        peak = np.maximum.accumulate(navs)
        drawdowns = (navs - peak) / peak
        max_drawdown = float(np.min(drawdowns))

        # Current drawdown
        current_dd = float((navs[-1] - peak[-1]) / peak[-1]) if peak[-1] > 0 else 0.0

        # Rolling Sharpe (full period)
        if len(returns) >= 10:
            mean_r = float(np.mean(returns))
            std_r = float(np.std(returns, ddof=1))
            sharpe = (mean_r / std_r * np.sqrt(TRADING_DAYS_PER_YEAR)) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        # Rolling 60-day Sharpe (recent)
        window = min(60, len(returns))
        recent_returns = returns[-window:]
        if len(recent_returns) >= 10:
            mean_recent = float(np.mean(recent_returns))
            std_recent = float(np.std(recent_returns, ddof=1))
            recent_sr = (mean_recent / std_recent * np.sqrt(TRADING_DAYS_PER_YEAR)) if std_recent > 0 else 0.0
        else:
            recent_sr = 0.0

        # Monthly SR negative streak (count months with negative rolling SR)
        monthly_sr_negative_streak = 0
        if len(returns) >= 20:
            # Compute monthly chunks (20 trading days each)
            n_months = len(returns) // 20
            for i in range(n_months - 1, -1, -1):
                chunk = returns[i * 20:(i + 1) * 20]
                m = float(np.mean(chunk))
                s = float(np.std(chunk, ddof=1))
                month_sr = (m / s * np.sqrt(TRADING_DAYS_PER_YEAR)) if s > 0 else 0.0
                if month_sr < 0:
                    monthly_sr_negative_streak += 1
                else:
                    break

        # Calmar ratio
        calmar = ann_return / abs(max_drawdown) if max_drawdown < -0.001 else 0.0

        # Volatility (annualized)
        volatility = float(np.std(returns, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(returns) > 1 else 0.0

        return {
            "nav_latest": float(navs[-1]),
            "nav_initial": float(navs[0]),
            "total_return": float(total_return),
            "annualized_return": float(ann_return),
            "max_drawdown": float(max_drawdown),
            "current_drawdown": float(current_dd),
            "sharpe_ratio": float(sharpe),
            "recent_sr_60d": float(recent_sr),
            "monthly_sr_negative_streak": monthly_sr_negative_streak,
            "calmar_ratio": float(calmar),
            "volatility": float(volatility),
            "n_trading_days": n_days,
            "date_range": f"{dates[0]} ~ {dates[-1]}",
        }

    def _evaluate_demotion(
        self, portfolio_id: str, metrics: dict, status: str,
    ) -> tuple[HealthAction, list[str]]:
        """Evaluate demotion gates."""
        reasons: list[str] = []

        if metrics.get("insufficient_data"):
            return HealthAction.MAINTAIN, []

        mdd = abs(metrics.get("max_drawdown", 0.0))
        current_dd = abs(metrics.get("current_drawdown", 0.0))
        sr_neg_streak = metrics.get("monthly_sr_negative_streak", 0)

        # FREEZE: check failure counter
        try:
            from aipha.runner.failure_counter import FailureCounter
            counter = FailureCounter(state_dir=self._state_dir)
            if counter.is_failure_frozen(portfolio_id):
                return HealthAction.FREEZE, ["Auto-frozen by failure counter"]
        except Exception:
            pass

        # SUSPEND: MDD > halt threshold
        if mdd >= self._demotion.mdd_halt_pct:
            reasons.append(
                f"MDD {mdd:.1%} >= halt threshold {self._demotion.mdd_halt_pct:.0%}"
            )
            return HealthAction.SUSPEND, reasons

        # SUSPEND: SR negative for 6+ months
        if sr_neg_streak >= self._demotion.sr_negative_months:
            reasons.append(
                f"SR < 0 for {sr_neg_streak} consecutive months "
                f"(threshold: {self._demotion.sr_negative_months})"
            )
            return HealthAction.SUSPEND, reasons

        # DEMOTE: SR negative for 3+ months (warning)
        if sr_neg_streak >= self._demotion.sr_warning_months:
            reasons.append(
                f"SR < 0 for {sr_neg_streak} months "
                f"(demote threshold: {self._demotion.sr_warning_months})"
            )
            return HealthAction.DEMOTE, reasons

        # REVIEW: MDD > warning threshold
        if mdd >= self._demotion.mdd_warning_pct:
            reasons.append(
                f"MDD {mdd:.1%} >= warning threshold {self._demotion.mdd_warning_pct:.0%}"
            )
            return HealthAction.REVIEW, reasons

        # REVIEW: current drawdown approaching halt
        if current_dd >= self._demotion.mdd_warning_pct:
            reasons.append(
                f"Current DD {current_dd:.1%} >= warning {self._demotion.mdd_warning_pct:.0%}"
            )
            return HealthAction.REVIEW, reasons

        return HealthAction.MAINTAIN, []

    def _evaluate_promotion(
        self, portfolio_id: str, metrics: dict, status: str,
    ) -> tuple[HealthAction, list[str]]:
        """Evaluate promotion eligibility."""
        # Only candidates and canaries are eligible for promotion
        if status not in ("candidate", "canary"):
            return HealthAction.MAINTAIN, []

        if metrics.get("insufficient_data"):
            return HealthAction.MAINTAIN, []

        reasons: list[str] = []
        paper_days = metrics.get("paper_days", 0)
        sr = metrics.get("sharpe_ratio", 0.0)
        mdd = abs(metrics.get("max_drawdown", 0.0))
        calmar = metrics.get("calmar_ratio", 0.0)

        # Hard gate: minimum paper days
        if paper_days < self._promotion.min_paper_days:
            return HealthAction.MAINTAIN, [
                f"Paper days {paper_days} < required {self._promotion.min_paper_days}"
            ]

        # Hard gate: MDD must be within limits
        if mdd >= self._demotion.mdd_halt_pct:
            return HealthAction.MAINTAIN, [
                f"MDD {mdd:.1%} exceeds promotion threshold"
            ]

        # Soft gates (recommend review if mostly met)
        soft_pass = 0
        soft_total = 3

        if sr >= self._promotion.soft_min_sr:
            soft_pass += 1
        else:
            reasons.append(f"SR {sr:.2f} < soft gate {self._promotion.soft_min_sr}")

        if mdd <= self._promotion.soft_max_mdd:
            soft_pass += 1
        else:
            reasons.append(f"MDD {mdd:.1%} > soft gate {self._promotion.soft_max_mdd:.0%}")

        if calmar >= self._promotion.soft_min_calmar:
            soft_pass += 1
        else:
            reasons.append(f"Calmar {calmar:.2f} < soft gate {self._promotion.soft_min_calmar}")

        # If enough paper days and at least 2/3 soft gates pass → eligible
        if soft_pass >= 2:
            promote_reasons = [
                f"Eligible: {paper_days} paper days, SR={sr:.2f}, "
                f"MDD={mdd:.1%}, Calmar={calmar:.2f} ({soft_pass}/{soft_total} soft gates)"
            ]
            return HealthAction.PROMOTE_REVIEW, promote_reasons

        return HealthAction.MAINTAIN, reasons

    def _get_paper_days(self, portfolio_id: str) -> int:
        """Count valid paper trading days."""
        nav_history = self._load_nav_history(portfolio_id)
        return len(nav_history)


# ── Runner Entry Point ──


def run_lifecycle_monitor(
    *,
    notify: bool = True,
    save_report: bool = True,
    dry_run: bool = False,
) -> list[PortfolioHealthReport]:
    """Run lifecycle health check on all active portfolios.

    Args:
        notify: Send Feishu alerts for non-MAINTAIN actions.
        save_report: Save JSON report to data/metrics/.
        dry_run: Don't send alerts or trigger transitions.

    Returns:
        List of health reports.
    """
    monitor = LifecycleMonitor()
    reports = monitor.check_all()

    # Save report
    if save_report:
        _save_health_report(reports)

    # Alert on non-MAINTAIN actions
    if notify and not dry_run:
        _notify_health_issues(reports)

    # Log summary
    actions = {}
    for r in reports:
        actions.setdefault(r.action.value, []).append(r.portfolio_id)

    logger.info(
        "Lifecycle monitor complete: %d portfolios checked. "
        "Actions: %s",
        len(reports),
        {k: len(v) for k, v in actions.items()},
    )

    return reports


def _save_health_report(reports: list[PortfolioHealthReport]) -> None:
    """Save health report to data/metrics/."""
    _METRICS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    report_file = _METRICS_DIR / f"lifecycle_health_{today}.json"
    try:
        data = {
            "check_date": today,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "portfolios": [r.to_dict() for r in reports],
        }
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Health report saved: %s", report_file)
    except Exception:
        logger.error("Failed to save health report", exc_info=True)


def _notify_health_issues(reports: list[PortfolioHealthReport]) -> None:
    """Send Feishu alerts for portfolios needing attention."""
    actionable = [r for r in reports if r.action != HealthAction.MAINTAIN]
    if not actionable:
        return

    try:
        from aipha.monitor.feishu_alert import send_feishu_alert
    except ImportError:
        logger.warning("feishu_alert not available, skipping notifications")
        return

    # Group by severity
    critical = [r for r in actionable if r.action in (HealthAction.FREEZE, HealthAction.SUSPEND)]
    warnings = [r for r in actionable if r.action in (HealthAction.DEMOTE, HealthAction.REVIEW)]
    promotions = [r for r in actionable if r.action == HealthAction.PROMOTE_REVIEW]

    if critical:
        lines = []
        for r in critical:
            lines.append(f"**{r.portfolio_id}** [{r.action.value}]: {'; '.join(r.reasons)}")
        try:
            send_feishu_alert(
                f"[CRITICAL] 组合健康警报: {len(critical)} 个需立即处理",
                "\n".join(lines),
                level="CRITICAL",
            )
        except Exception:
            logger.error("Critical health alert failed", exc_info=True)

    if warnings:
        lines = []
        for r in warnings:
            lines.append(f"**{r.portfolio_id}** [{r.action.value}]: {'; '.join(r.reasons)}")
        try:
            send_feishu_alert(
                f"[WARNING] 组合健康检查: {len(warnings)} 个需关注",
                "\n".join(lines),
                level="WARNING",
            )
        except Exception:
            logger.debug("Warning health alert failed", exc_info=True)

    if promotions:
        lines = []
        for r in promotions:
            lines.append(f"**{r.portfolio_id}**: {'; '.join(r.reasons)}")
        try:
            send_feishu_alert(
                f"[INFO] 组合晋升候选: {len(promotions)} 个可评审",
                "\n".join(lines),
                level="INFO",
            )
        except Exception:
            logger.debug("Promotion alert failed", exc_info=True)
