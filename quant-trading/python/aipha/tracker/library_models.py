"""Data models for the Public Quantitative Portfolio Library.

Two-layer architecture:
- L1: Broad-coverage index (LibraryAccount + LibraryPortfolio)
- L2: Deep archive metadata (ArchiveEntry)

Design principles:
- Immutable (frozen dataclass) where possible
- Serialization via to_dict() / from_dict() for JSON/SQLite storage
- Compatible with existing tracker models (PortfolioSource enum extended)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Platform(str, Enum):
    """Supported platforms for the portfolio library."""

    XUEQIU = "xueqiu"
    JOINQUANT = "joinquant"
    BIGQUANT = "bigquant"
    JISILU = "jisilu"
    ETORO = "etoro"
    ZULUTRADE = "zulutrade"
    DARWINEX = "darwinex"
    QUANTCONNECT = "quantconnect"
    MANUAL = "manual"


class FetchStability(str, Enum):
    """Stability of automated data fetching."""

    STABLE = "stable"
    UNSTABLE = "unstable"
    BROKEN = "broken"
    MANUAL_ONLY = "manual_only"


class EvidenceLevel(str, Enum):
    """How verifiable is the account's claimed performance."""

    A = "A"  # Fully verifiable (regulated platform, audited track record)
    B = "B"  # Partially verifiable (public page with some metrics)
    C = "C"  # Self-reported (community post, no independent verification)
    D = "D"  # Unverified (no evidence available)


class ArchiveStatus(str, Enum):
    """Lifecycle status of a deep archive entry."""

    ACTIVE = "active"
    OUTDATED = "outdated"
    ARCHIVED = "archived"


# ---------------------------------------------------------------------------
# LibraryAccount — L1 index entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LibraryAccount:
    """A single account/portfolio entry in the L1 broad-coverage index.

    Attributes:
        account_id: Internal unique identifier (platform_shortcode format).
        platform: Source platform.
        platform_uid: Original user ID/username on the platform.
        display_name: Human-readable display name.
        profile_url: URL to the account's profile page.
        portfolio_name: Name of the portfolio/strategy.
        portfolio_url: URL to the specific portfolio page.
        asset_classes: Asset class tags (e.g. ["stock", "cb", "etf"]).
        strategy_tags: Strategy style tags (e.g. ["value", "momentum"]).
        requires_login: Whether login is needed to access data.
        last_update_seen: Last observed update time on the platform.
        perf_summary: Visible performance metrics dict.
        followers_copiers: Number of followers/copiers/AUM value.
        fetch_stability: Automated fetch reliability.
        l1_score: Weighted quality score (0-5).
        l2_promoted: Whether promoted to L2 deep archive.
        notes: Free-form human notes.
        created_at: First indexed time.
        updated_at: Last metadata update time.
    """

    account_id: str
    platform: Platform
    platform_uid: str
    display_name: str
    profile_url: str = ""
    portfolio_name: str = ""
    portfolio_url: str = ""
    asset_classes: tuple[str, ...] = ()
    strategy_tags: tuple[str, ...] = ()
    requires_login: bool = False
    last_update_seen: str = ""
    perf_summary: dict[str, Any] = field(default_factory=dict)
    followers_copiers: int = 0
    fetch_stability: FetchStability = FetchStability.MANUAL_ONLY
    l1_score: float = 0.0
    l2_promoted: bool = False
    notes: str = ""
    source_verified_at: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.D
    strategy_description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must not be empty")
        if not self.platform_uid:
            raise ValueError("platform_uid must not be empty")

    @staticmethod
    def make_id(platform: Platform, uid: str) -> str:
        """Generate a deterministic account_id from platform + uid."""
        return f"{platform.value}_{uid}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "account_id": self.account_id,
            "platform": self.platform.value,
            "platform_uid": self.platform_uid,
            "display_name": self.display_name,
            "profile_url": self.profile_url,
            "portfolio_name": self.portfolio_name,
            "portfolio_url": self.portfolio_url,
            "asset_classes": list(self.asset_classes),
            "strategy_tags": list(self.strategy_tags),
            "requires_login": self.requires_login,
            "last_update_seen": self.last_update_seen,
            "perf_summary": self.perf_summary,
            "followers_copiers": self.followers_copiers,
            "fetch_stability": self.fetch_stability.value,
            "l1_score": self.l1_score,
            "l2_promoted": self.l2_promoted,
            "notes": self.notes,
            "source_verified_at": self.source_verified_at,
            "evidence_level": self.evidence_level.value,
            "strategy_description": self.strategy_description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LibraryAccount:
        """Deserialize from dictionary."""
        return cls(
            account_id=d["account_id"],
            platform=Platform(d["platform"]),
            platform_uid=d["platform_uid"],
            display_name=d["display_name"],
            profile_url=d.get("profile_url", ""),
            portfolio_name=d.get("portfolio_name", ""),
            portfolio_url=d.get("portfolio_url", ""),
            asset_classes=tuple(d.get("asset_classes", [])),
            strategy_tags=tuple(d.get("strategy_tags", [])),
            requires_login=d.get("requires_login", False),
            last_update_seen=d.get("last_update_seen", ""),
            perf_summary=d.get("perf_summary", {}),
            followers_copiers=d.get("followers_copiers", 0),
            fetch_stability=FetchStability(d.get("fetch_stability", "manual_only")),
            l1_score=d.get("l1_score", 0.0),
            l2_promoted=d.get("l2_promoted", False),
            notes=d.get("notes", ""),
            source_verified_at=d.get("source_verified_at", ""),
            evidence_level=EvidenceLevel(d.get("evidence_level", "D")),
            strategy_description=d.get("strategy_description", ""),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else datetime.now(),
        )


# ---------------------------------------------------------------------------
# ArchiveEntry — L2 deep archive metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """Metadata for a deep archive (L2) research document.

    The actual research content lives in a Markdown file;
    this model tracks the metadata and links back to L1.

    Attributes:
        archive_id: Unique identifier.
        account_id: FK to LibraryAccount.
        archive_path: Relative path to the Markdown research file.
        methodology_summary: Brief summary of the strategy methodology.
        factor_exposure: Estimated factor exposures (JSON-serializable dict).
        replicability_score: How replicable (0-5 scale).
        last_reviewed: Last review date.
        reviewer: Who performed the review (expert role).
        status: Lifecycle status.
    """

    archive_id: str
    account_id: str
    archive_path: str
    methodology_summary: str = ""
    factor_exposure: dict[str, Any] = field(default_factory=dict)
    replicability_score: float = 0.0
    last_reviewed: str = ""
    reviewer: str = ""
    status: ArchiveStatus = ArchiveStatus.ACTIVE

    def __post_init__(self) -> None:
        if not self.archive_id:
            raise ValueError("archive_id must not be empty")
        if not self.account_id:
            raise ValueError("account_id must not be empty")

    @staticmethod
    def new_id() -> str:
        """Generate a new unique archive ID."""
        return f"ARC-{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "archive_id": self.archive_id,
            "account_id": self.account_id,
            "archive_path": self.archive_path,
            "methodology_summary": self.methodology_summary,
            "factor_exposure": self.factor_exposure,
            "replicability_score": self.replicability_score,
            "last_reviewed": self.last_reviewed,
            "reviewer": self.reviewer,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArchiveEntry:
        """Deserialize from dictionary."""
        return cls(
            archive_id=d["archive_id"],
            account_id=d["account_id"],
            archive_path=d["archive_path"],
            methodology_summary=d.get("methodology_summary", ""),
            factor_exposure=d.get("factor_exposure", {}),
            replicability_score=d.get("replicability_score", 0.0),
            last_reviewed=d.get("last_reviewed", ""),
            reviewer=d.get("reviewer", ""),
            status=ArchiveStatus(d.get("status", "active")),
        )


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------


def compute_l1_score(
    transparency: float,
    update_frequency: float,
    explainability: float,
    perf_verifiability: float,
    risk_disclosure: float,
    replicability: float,
    capacity_fit: float,
) -> float:
    """Compute weighted L1 quality score.

    All inputs should be on a 1-5 scale.
    Returns weighted average (0-5).

    Weights per plan §1.3:
      transparency=25%, update_frequency=20%, explainability=15%,
      perf_verifiability=15%, risk_disclosure=10%, replicability=10%,
      capacity_fit=5%
    """
    weights = (0.25, 0.20, 0.15, 0.15, 0.10, 0.10, 0.05)
    values = (
        transparency,
        update_frequency,
        explainability,
        perf_verifiability,
        risk_disclosure,
        replicability,
        capacity_fit,
    )
    for v in values:
        if not (0.0 <= v <= 5.0):
            raise ValueError(f"Score values must be 0-5, got {v}")
    return sum(w * v for w, v in zip(weights, values))


# ---------------------------------------------------------------------------
# Batch / programmatic scoring
# ---------------------------------------------------------------------------

# Dimension keys matching compute_l1_score() positional args
SCORE_DIMENSIONS = (
    "transparency",
    "update_frequency",
    "explainability",
    "perf_verifiability",
    "risk_disclosure",
    "replicability",
    "capacity_fit",
)

L2_PROMOTION_THRESHOLD = 3.50


def score_account(scores: dict[str, float]) -> float:
    """Non-interactive scoring API for batch/programmatic use.

    Args:
        scores: Dict mapping dimension name -> score (1-5).
                Must include all keys in SCORE_DIMENSIONS.

    Returns:
        Weighted L1 score (0-5).

    Raises:
        KeyError: If a required dimension is missing.
        ValueError: If a score is out of range.
    """
    vals = []
    for dim in SCORE_DIMENSIONS:
        if dim not in scores:
            raise KeyError(f"Missing required dimension: {dim}")
        vals.append(scores[dim])
    return compute_l1_score(*vals)
