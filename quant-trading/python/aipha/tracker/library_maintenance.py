"""Replicability mapping and maintenance for the Portfolio Library.

Phase 3 components:
- ReplicabilityMapping: Structured mapping from external strategy to internal modules
- compute_replicability_score: Algorithmic scoring of replication feasibility
- LibraryMaintainer: Periodic maintenance workflows (stale detection, health, refresh)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .library_models import (
    ArchiveEntry,
    ArchiveStatus,
    FetchStability,
    LibraryAccount,
    Platform,
)
from .library_storage import LibraryStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Replicability Mapping Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MappingDimension:
    """A single dimension of the replicability assessment.

    Attributes:
        name: Dimension identifier (e.g. 'data_availability').
        score: Score on 1-5 scale.
        notes: Explanation for the score.
    """

    name: str
    score: float
    notes: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 5.0):
            raise ValueError(f"Score must be 0-5, got {self.score}")


# Dimension weights for composite replicability score
REPLICABILITY_WEIGHTS: dict[str, float] = {
    "data_availability": 0.25,
    "factor_mappable": 0.25,
    "rules_implementable": 0.20,
    "capacity_compatible": 0.15,
    "market_reachable": 0.15,
}


@dataclass(frozen=True, slots=True)
class ReplicabilityMapping:
    """Structured mapping from an external strategy to internal Aipha-Q modules.

    Attributes:
        account_id: FK to LibraryAccount.
        dimensions: Scored mapping dimensions.
        internal_modules: Mapping of external components to internal modules.
        assumptions: Key assumptions for replication.
        blockers: Known blockers or gaps.
        composite_score: Weighted composite score (auto-computed).
        assessed_by: Expert role who did the assessment.
        assessed_at: Assessment timestamp.
    """

    account_id: str
    dimensions: tuple[MappingDimension, ...] = ()
    internal_modules: dict[str, str] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    composite_score: float = 0.0
    assessed_by: str = ""
    assessed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "account_id": self.account_id,
            "dimensions": [
                {"name": d.name, "score": d.score, "notes": d.notes}
                for d in self.dimensions
            ],
            "internal_modules": self.internal_modules,
            "assumptions": list(self.assumptions),
            "blockers": list(self.blockers),
            "composite_score": self.composite_score,
            "assessed_by": self.assessed_by,
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReplicabilityMapping:
        """Deserialize from dictionary."""
        dims = tuple(
            MappingDimension(name=x["name"], score=x["score"], notes=x.get("notes", ""))
            for x in d.get("dimensions", [])
        )
        return cls(
            account_id=d["account_id"],
            dimensions=dims,
            internal_modules=d.get("internal_modules", {}),
            assumptions=tuple(d.get("assumptions", [])),
            blockers=tuple(d.get("blockers", [])),
            composite_score=d.get("composite_score", 0.0),
            assessed_by=d.get("assessed_by", ""),
            assessed_at=d.get("assessed_at", ""),
        )


def compute_replicability_score(dimensions: list[MappingDimension]) -> float:
    """Compute weighted replicability score from dimension assessments.

    Uses REPLICABILITY_WEIGHTS. Dimensions not in the weight dict are ignored.
    Returns 0.0 if no valid dimensions provided.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for dim in dimensions:
        w = REPLICABILITY_WEIGHTS.get(dim.name, 0.0)
        if w > 0:
            weighted_sum += w * dim.score
            total_weight += w
    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight


# ---------------------------------------------------------------------------
# Maintenance Results
# ---------------------------------------------------------------------------


@dataclass
class MaintenanceReport:
    """Result of a maintenance cycle.

    Attributes:
        run_date: When the maintenance was executed.
        stale_accounts: Accounts not updated beyond threshold.
        broken_accounts: Accounts with broken fetch stability.
        score_refreshed: Accounts whose L1 score was recalculated.
        archived_entries: L2 entries marked as outdated.
        errors: Any errors encountered during maintenance.
    """

    run_date: str = ""
    stale_accounts: list[str] = field(default_factory=list)
    broken_accounts: list[str] = field(default_factory=list)
    score_refreshed: list[str] = field(default_factory=list)
    archived_entries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Maintenance Report ({self.run_date})",
            f"  Stale accounts: {len(self.stale_accounts)}",
            f"  Broken accounts: {len(self.broken_accounts)}",
            f"  Scores refreshed: {len(self.score_refreshed)}",
            f"  Archives outdated: {len(self.archived_entries)}",
            f"  Errors: {len(self.errors)}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# LibraryMaintainer
# ---------------------------------------------------------------------------


class LibraryMaintainer:
    """Performs periodic maintenance on the portfolio library.

    Workflows:
    - detect_stale(): Find accounts not updated within threshold
    - detect_broken(): Find accounts with broken fetch stability
    - refresh_scores(): Recalculate L1 scores based on updated data
    - mark_outdated_archives(): Flag old L2 entries as outdated
    - run_full_cycle(): Execute all maintenance steps

    Usage:
        storage = LibraryStorage(data_dir="data/tracker")
        maintainer = LibraryMaintainer(storage)
        report = maintainer.run_full_cycle()
        print(report.summary())
    """

    def __init__(
        self,
        storage: LibraryStorage,
        stale_threshold_days: int = 30,
        archive_outdated_days: int = 90,
    ) -> None:
        self._storage = storage
        self._stale_days = stale_threshold_days
        self._archive_outdated_days = archive_outdated_days

    def detect_stale(self, reference_date: datetime | None = None) -> list[LibraryAccount]:
        """Find accounts whose updated_at exceeds the stale threshold.

        Args:
            reference_date: Compare against this date (default: now).

        Returns:
            List of stale accounts.
        """
        ref = reference_date or datetime.now()
        threshold = ref - timedelta(days=self._stale_days)
        all_accounts = self._storage.list_all_accounts()
        return [a for a in all_accounts if a.updated_at < threshold]

    def detect_broken(self) -> list[LibraryAccount]:
        """Find accounts marked as broken fetch stability."""
        all_accounts = self._storage.list_all_accounts()
        return [a for a in all_accounts if a.fetch_stability == FetchStability.BROKEN]

    def mark_outdated_archives(
        self, reference_date: datetime | None = None
    ) -> list[str]:
        """Mark L2 archives as outdated if not reviewed within threshold.

        Returns list of archive_ids that were marked outdated.
        """
        ref = reference_date or datetime.now()
        threshold_str = (ref - timedelta(days=self._archive_outdated_days)).strftime(
            "%Y-%m-%d"
        )
        outdated_ids: list[str] = []

        all_accounts = self._storage.list_all_accounts()
        for account in all_accounts:
            if not account.l2_promoted:
                continue
            archives = self._storage.get_archives_for_account(account.account_id)
            for arc in archives:
                if arc.status != ArchiveStatus.ACTIVE:
                    continue
                if arc.last_reviewed and arc.last_reviewed < threshold_str:
                    # Mark as outdated
                    updated = ArchiveEntry(
                        archive_id=arc.archive_id,
                        account_id=arc.account_id,
                        archive_path=arc.archive_path,
                        methodology_summary=arc.methodology_summary,
                        factor_exposure=arc.factor_exposure,
                        replicability_score=arc.replicability_score,
                        last_reviewed=arc.last_reviewed,
                        reviewer=arc.reviewer,
                        status=ArchiveStatus.OUTDATED,
                    )
                    self._storage.upsert_archive(updated)
                    outdated_ids.append(arc.archive_id)
        return outdated_ids

    def run_full_cycle(
        self, reference_date: datetime | None = None
    ) -> MaintenanceReport:
        """Execute a full maintenance cycle.

        Steps:
        1. Detect stale accounts
        2. Detect broken accounts
        3. Mark outdated L2 archives

        Returns a MaintenanceReport with findings.
        """
        ref = reference_date or datetime.now()
        report = MaintenanceReport(run_date=ref.strftime("%Y-%m-%d %H:%M"))

        try:
            stale = self.detect_stale(ref)
            report.stale_accounts = [a.account_id for a in stale]
        except Exception as e:
            report.errors.append(f"stale detection: {e}")

        try:
            broken = self.detect_broken()
            report.broken_accounts = [a.account_id for a in broken]
        except Exception as e:
            report.errors.append(f"broken detection: {e}")

        try:
            outdated = self.mark_outdated_archives(ref)
            report.archived_entries = outdated
        except Exception as e:
            report.errors.append(f"archive outdating: {e}")

        return report
