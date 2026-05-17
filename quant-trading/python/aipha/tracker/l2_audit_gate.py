"""L2 Promotion Audit Gate for the Public Quantitative Portfolio Library.

Enforces a 3-role review process before promoting an L1 account to L2:
  1. 策略抽取 (Strategy Extraction) — Methodology documented, factors identified
  2. 绩效校验 (Performance Verification) — Claims cross-checked against evidence
  3. 复刻映射 (Replication Mapping) — Internal module mapping identified

All three roles must pass before promotion is allowed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .library_models import (
    ArchiveEntry,
    ArchiveStatus,
    LibraryAccount,
    L2_PROMOTION_THRESHOLD,
)
from .library_storage import LibraryStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ReviewRole(str, Enum):
    """The three mandatory review roles for L2 promotion."""

    STRATEGY_EXTRACTION = "strategy_extraction"
    PERFORMANCE_VERIFICATION = "performance_verification"
    REPLICATION_MAPPING = "replication_mapping"


class ReviewVerdict(str, Enum):
    """Outcome of a single role review."""

    PASS = "pass"
    FAIL = "fail"
    CONDITIONAL = "conditional"  # Pass with noted caveats


# ---------------------------------------------------------------------------
# ReviewRecord — single role's review
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    """A single reviewer's assessment for one role."""

    role: ReviewRole
    verdict: ReviewVerdict
    reviewer: str  # Who performed this review
    date: str  # ISO date YYYY-MM-DD
    notes: str = ""
    conditions: tuple[str, ...] = ()  # Required conditions if CONDITIONAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "verdict": self.verdict.value,
            "reviewer": self.reviewer,
            "date": self.date,
            "notes": self.notes,
            "conditions": list(self.conditions),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewRecord:
        return cls(
            role=ReviewRole(d["role"]),
            verdict=ReviewVerdict(d["verdict"]),
            reviewer=d["reviewer"],
            date=d["date"],
            notes=d.get("notes", ""),
            conditions=tuple(d.get("conditions", [])),
        )


# ---------------------------------------------------------------------------
# AuditGateResult — aggregate result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditGateResult:
    """Result of the L2 promotion audit gate check."""

    passed: bool
    reviews: tuple[ReviewRecord, ...]
    missing_roles: tuple[ReviewRole, ...] = ()
    blocking_reasons: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if self.passed:
            return "PASS — All 3 roles reviewed and approved"
        parts = []
        if self.missing_roles:
            parts.append(f"Missing: {[r.value for r in self.missing_roles]}")
        if self.blocking_reasons:
            parts.append(f"Blocked: {list(self.blocking_reasons)}")
        return "FAIL — " + "; ".join(parts)


# ---------------------------------------------------------------------------
# L2AuditGate — main gate logic
# ---------------------------------------------------------------------------


class L2AuditGate:
    """Enforces the 3-role review gate for L2 promotion.

    Usage:
        gate = L2AuditGate(storage)
        gate.submit_review(account_id, review_record)
        result = gate.check(account_id)
        if result.passed:
            gate.promote(account_id, archive_path="docs/reports/...")
    """

    def __init__(self, storage: LibraryStorage) -> None:
        self._storage = storage
        self._reviews: dict[str, list[ReviewRecord]] = {}

    def submit_review(self, account_id: str, review: ReviewRecord) -> None:
        """Submit a review for a specific account and role."""
        if account_id not in self._reviews:
            self._reviews[account_id] = []
        # Replace existing review for same role (latest wins)
        self._reviews[account_id] = [
            r for r in self._reviews[account_id] if r.role != review.role
        ] + [review]
        logger.info(
            "Review submitted: %s / %s -> %s",
            account_id,
            review.role.value,
            review.verdict.value,
        )

    def get_reviews(self, account_id: str) -> list[ReviewRecord]:
        """Get all submitted reviews for an account."""
        return list(self._reviews.get(account_id, []))

    def check(self, account_id: str) -> AuditGateResult:
        """Check if an account passes the L2 promotion gate.

        Requirements:
          1. Account exists and has l1_score >= L2_PROMOTION_THRESHOLD
          2. All 3 roles have submitted reviews
          3. No role has verdict == FAIL
        """
        blocking: list[str] = []

        # Check account exists and meets score threshold
        account = self._storage.get_account(account_id)
        if account is None:
            return AuditGateResult(
                passed=False,
                reviews=(),
                blocking_reasons=("Account not found",),
            )
        if account.l1_score < L2_PROMOTION_THRESHOLD:
            blocking.append(
                f"L1 score {account.l1_score:.2f} < threshold {L2_PROMOTION_THRESHOLD}"
            )

        # Check reviews
        reviews = self._reviews.get(account_id, [])
        reviewed_roles = {r.role for r in reviews}
        missing = tuple(
            role for role in ReviewRole if role not in reviewed_roles
        )

        # Check for FAIL verdicts
        for r in reviews:
            if r.verdict == ReviewVerdict.FAIL:
                blocking.append(f"Role {r.role.value} verdict is FAIL: {r.notes}")

        passed = not missing and not blocking
        return AuditGateResult(
            passed=passed,
            reviews=tuple(reviews),
            missing_roles=missing,
            blocking_reasons=tuple(blocking),
        )

    def promote(
        self,
        account_id: str,
        archive_path: str,
        methodology_summary: str = "",
        factor_exposure: dict[str, Any] | None = None,
        replicability_score: float = 0.0,
    ) -> ArchiveEntry:
        """Promote account to L2 after passing the audit gate.

        Raises:
            ValueError: If account does not pass the audit gate.
        """
        result = self.check(account_id)
        if not result.passed:
            raise ValueError(
                f"Cannot promote {account_id}: {result.summary}"
            )

        # Create archive entry
        entry = ArchiveEntry(
            archive_id=ArchiveEntry.new_id(),
            account_id=account_id,
            archive_path=archive_path,
            methodology_summary=methodology_summary,
            factor_exposure=factor_exposure or {},
            replicability_score=replicability_score,
            last_reviewed=datetime.now().strftime("%Y-%m-%d"),
            reviewer="l2_audit_gate",
            status=ArchiveStatus.ACTIVE,
        )
        self._storage.upsert_archive(entry)

        # Mark account as l2_promoted
        account = self._storage.get_account(account_id)
        if account and not account.l2_promoted:
            # Recreate with l2_promoted=True
            updated = LibraryAccount(
                account_id=account.account_id,
                platform=account.platform,
                platform_uid=account.platform_uid,
                display_name=account.display_name,
                profile_url=account.profile_url,
                portfolio_name=account.portfolio_name,
                portfolio_url=account.portfolio_url,
                asset_classes=account.asset_classes,
                strategy_tags=account.strategy_tags,
                requires_login=account.requires_login,
                last_update_seen=account.last_update_seen,
                perf_summary=account.perf_summary,
                followers_copiers=account.followers_copiers,
                fetch_stability=account.fetch_stability,
                l1_score=account.l1_score,
                l2_promoted=True,
                notes=account.notes,
                source_verified_at=account.source_verified_at,
                evidence_level=account.evidence_level,
                strategy_description=account.strategy_description,
                created_at=account.created_at,
                updated_at=datetime.now(),
            )
            self._storage.upsert_account(updated)

        logger.info("Account %s promoted to L2, archive: %s", account_id, entry.archive_id)
        return entry
