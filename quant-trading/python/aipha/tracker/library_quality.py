"""Quality checking and alerting for the Portfolio Library.

Automated quality rules:
- High-risk label detection (excessive returns + low transparency)
- Anomalous performance alerts (sudden jumps/drops)
- Freshness scoring (penalize stale accounts)
- Credential health monitoring
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .library_credentials import CredentialEntry, CredentialStatus, CredentialStore
from .library_models import FetchStability, LibraryAccount, Platform
from .library_storage import LibraryStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality Alert Model
# ---------------------------------------------------------------------------


class AlertSeverity:
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class QualityAlert:
    """A single quality alert raised during checking.

    Attributes:
        account_id: Which account triggered the alert.
        severity: info / warning / critical.
        rule_name: Which rule generated this alert.
        message: Human-readable description.
        details: Additional structured data.
        timestamp: When the alert was generated.
    """

    account_id: str
    severity: str
    rule_name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class QualityReport:
    """Result of a quality check cycle.

    Attributes:
        run_date: When the check was executed.
        alerts: All alerts generated.
        accounts_checked: Number of accounts examined.
        credentials_checked: Number of credentials examined.
    """

    run_date: str = ""
    alerts: list[QualityAlert] = field(default_factory=list)
    accounts_checked: int = 0
    credentials_checked: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == AlertSeverity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == AlertSeverity.WARNING)

    def summary(self) -> str:
        lines = [
            f"Quality Report ({self.run_date})",
            f"  Accounts checked: {self.accounts_checked}",
            f"  Credentials checked: {self.credentials_checked}",
            f"  Alerts: {len(self.alerts)} "
            f"(critical={self.critical_count}, warning={self.warning_count})",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quality Checker
# ---------------------------------------------------------------------------


# Thresholds for anomaly detection
HIGH_RETURN_THRESHOLD = 100.0  # AR > 100% with low transparency = suspicious
SUDDEN_JUMP_THRESHOLD = 50.0  # >50% change in perf metric = alert


class QualityChecker:
    """Runs automated quality rules against the portfolio library.

    Rules:
    1. high_risk_label: Flag accounts with extreme returns + low transparency
    2. broken_fetch: Flag accounts with broken data fetch
    3. credential_expiry: Flag expiring/expired credentials
    4. stale_l2: Flag L2 accounts not updated in threshold period

    Usage:
        checker = QualityChecker(library_storage, credential_store)
        report = checker.run_all_checks()
    """

    def __init__(
        self,
        library: LibraryStorage,
        credentials: CredentialStore | None = None,
        stale_l2_days: int = 60,
    ) -> None:
        self._library = library
        self._credentials = credentials
        self._stale_l2_days = stale_l2_days

    def run_all_checks(self) -> QualityReport:
        """Execute all quality rules and return aggregated report."""
        report = QualityReport(run_date=datetime.now().strftime("%Y-%m-%d %H:%M"))

        accounts = self._library.list_all_accounts()
        report.accounts_checked = len(accounts)

        # Rule 1: High risk label
        for alert in self._check_high_risk(accounts):
            report.alerts.append(alert)

        # Rule 2: Broken fetch
        for alert in self._check_broken_fetch(accounts):
            report.alerts.append(alert)

        # Rule 3: Stale L2 accounts
        for alert in self._check_stale_l2(accounts):
            report.alerts.append(alert)

        # Rule 4: Credential health
        if self._credentials:
            cred_alerts = self._check_credentials()
            report.credentials_checked = len(cred_alerts[1])
            for alert in cred_alerts[0]:
                report.alerts.append(alert)

        return report

    def _check_high_risk(self, accounts: list[LibraryAccount]) -> list[QualityAlert]:
        """Flag accounts with suspiciously high returns and low score."""
        alerts: list[QualityAlert] = []
        for a in accounts:
            ann_return = a.perf_summary.get("ann_return", 0.0)
            if isinstance(ann_return, (int, float)) and ann_return > HIGH_RETURN_THRESHOLD:
                if a.l1_score < 2.5:
                    alerts.append(
                        QualityAlert(
                            account_id=a.account_id,
                            severity=AlertSeverity.WARNING,
                            rule_name="high_risk_label",
                            message=(
                                f"High return ({ann_return:.0f}%) with low L1 score "
                                f"({a.l1_score:.2f}). Possible overfitting or unreliable data."
                            ),
                            details={"ann_return": ann_return, "l1_score": a.l1_score},
                        )
                    )
        return alerts

    def _check_broken_fetch(self, accounts: list[LibraryAccount]) -> list[QualityAlert]:
        """Flag accounts with broken fetch stability."""
        alerts: list[QualityAlert] = []
        for a in accounts:
            if a.fetch_stability == FetchStability.BROKEN:
                alerts.append(
                    QualityAlert(
                        account_id=a.account_id,
                        severity=AlertSeverity.WARNING,
                        rule_name="broken_fetch",
                        message=f"Data fetch is broken for {a.display_name} ({a.platform.value}).",
                        details={"platform": a.platform.value},
                    )
                )
        return alerts

    def _check_stale_l2(self, accounts: list[LibraryAccount]) -> list[QualityAlert]:
        """Flag L2-promoted accounts that haven't been updated."""
        alerts: list[QualityAlert] = []
        threshold = datetime.now() - timedelta(days=self._stale_l2_days)
        for a in accounts:
            if a.l2_promoted and a.updated_at < threshold:
                days_stale = (datetime.now() - a.updated_at).days
                alerts.append(
                    QualityAlert(
                        account_id=a.account_id,
                        severity=AlertSeverity.CRITICAL if days_stale > 90 else AlertSeverity.WARNING,
                        rule_name="stale_l2",
                        message=(
                            f"L2 account {a.display_name} not updated in {days_stale} days."
                        ),
                        details={"days_stale": days_stale},
                    )
                )
        return alerts

    def _check_credentials(self) -> tuple[list[QualityAlert], list[CredentialEntry]]:
        """Check credential health. Returns (alerts, all_creds)."""
        if not self._credentials:
            return [], []
        alerts: list[QualityAlert] = []
        # Get all credentials by checking each platform
        all_creds: list[CredentialEntry] = []
        for platform in Platform:
            creds = self._credentials.list_by_platform(platform)
            all_creds.extend(creds)

        for cred in all_creds:
            if cred.is_expired():
                alerts.append(
                    QualityAlert(
                        account_id=f"cred:{cred.credential_id}",
                        severity=AlertSeverity.CRITICAL,
                        rule_name="credential_expired",
                        message=(
                            f"Credential {cred.credential_id} for {cred.platform.value} "
                            f"has expired (was: {cred.expires_at})."
                        ),
                        details={"platform": cred.platform.value, "expires_at": cred.expires_at},
                    )
                )
            elif cred.is_expiring_soon(days=7):
                alerts.append(
                    QualityAlert(
                        account_id=f"cred:{cred.credential_id}",
                        severity=AlertSeverity.WARNING,
                        rule_name="credential_expiring",
                        message=(
                            f"Credential {cred.credential_id} for {cred.platform.value} "
                            f"expires soon ({cred.expires_at})."
                        ),
                        details={"platform": cred.platform.value, "expires_at": cred.expires_at},
                    )
                )
        return alerts, all_creds
