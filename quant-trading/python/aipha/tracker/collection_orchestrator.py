"""Collection orchestrator — ties collectors, evidence store, and library together.

Usage:
    orchestrator = CollectionOrchestrator(data_dir="data/tracker")
    report = await orchestrator.collect_account("darwinex", "THA")
    report = await orchestrator.collect_all(platform="darwinex")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .collectors.base import CollectorResult, LibraryCollector
from .evidence import (
    EvidenceQuality,
    EvidenceSnapshot,
    EvidenceStore,
    FailureType,
)
from .library_models import FetchStability, LibraryAccount, Platform
from .library_storage import LibraryStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Collector registry
# ---------------------------------------------------------------------------

_COLLECTOR_REGISTRY: dict[str, type[LibraryCollector]] = {}


def register_collector(cls: type[LibraryCollector]) -> type[LibraryCollector]:
    """Decorator to register a collector class."""
    # Instantiate to get platform_name
    _COLLECTOR_REGISTRY[cls.__name__] = cls
    return cls


def get_collector(platform: str) -> LibraryCollector | None:
    """Get a collector instance for the given platform."""
    from .collectors.darwinex import DarwinexCollector
    from .collectors.etoro import EtoroCollector
    from .collectors.zulutrade import ZuluTradeCollector
    from .collectors.jisilu import JisiluCollector
    from .collectors.joinquant import JoinquantCollector
    from .collectors.bigquant import BigquantCollector
    from .collectors.xueqiu import XueqiuLibraryCollector

    mapping: dict[str, type[LibraryCollector]] = {
        "darwinex": DarwinexCollector,
        "etoro": EtoroCollector,
        "zulutrade": ZuluTradeCollector,
        "jisilu": JisiluCollector,
        "joinquant": JoinquantCollector,
        "bigquant": BigquantCollector,
        "xueqiu": XueqiuLibraryCollector,
    }
    cls = mapping.get(platform)
    if cls is None:
        return None
    return cls()


# ---------------------------------------------------------------------------
# Collection report
# ---------------------------------------------------------------------------


@dataclass
class CollectionReport:
    """Summary of a collection run."""

    total_attempted: int = 0
    successful: int = 0
    failed: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    snapshots: list[EvidenceSnapshot] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_attempted == 0:
            return 0.0
        return self.successful / self.total_attempted


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class CollectionOrchestrator:
    """Coordinates collection across platforms with evidence storage."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = data_dir
        self._storage = LibraryStorage(data_dir)
        self._evidence = EvidenceStore(data_dir)

    async def collect_account(
        self, platform: str, platform_uid: str
    ) -> EvidenceSnapshot:
        """Collect data for a single account and store evidence."""
        collector = get_collector(platform)
        if collector is None:
            return EvidenceSnapshot(
                account_id=f"{platform}_{platform_uid}",
                platform=platform,
                platform_uid=platform_uid,
                failure_type=FailureType.PARSE_ERROR,
                error_detail=f"No collector available for platform: {platform}",
                evidence_quality=EvidenceQuality.FAILED,
            )

        account_id = f"{platform}_{platform_uid}"
        start_ms = time.time()

        try:
            result = await collector.fetch_account_info(platform_uid)
        except Exception as e:
            snapshot = EvidenceSnapshot(
                account_id=account_id,
                platform=platform,
                platform_uid=platform_uid,
                failure_type=FailureType.NETWORK_ERROR,
                error_detail=str(e),
                evidence_quality=EvidenceQuality.FAILED,
                response_time_ms=int((time.time() - start_ms) * 1000),
            )
            self._evidence.save_snapshot(snapshot)
            logger.warning("Collection failed for %s: %s", account_id, e)
            return snapshot

        elapsed_ms = int((time.time() - start_ms) * 1000)
        result.response_time_ms = elapsed_ms

        # Build evidence snapshot from result
        snapshot = result.to_evidence_snapshot(account_id)
        snapshot.platform = platform
        snapshot.response_time_ms = elapsed_ms
        self._evidence.save_snapshot(snapshot)

        # Update library account if successful
        if result.success:
            self._update_account(platform, result)

        return snapshot

    async def collect_platform(
        self, platform: str, limit: int = 50
    ) -> CollectionReport:
        """Collect all known accounts for a platform."""
        report = CollectionReport()

        # Get accounts from storage
        accounts = self._storage.search_accounts(platform=platform)
        if not accounts:
            report.errors.append(f"No accounts found for platform: {platform}")
            return report

        for account in accounts[:limit]:
            report.total_attempted += 1
            snapshot = await self.collect_account(
                platform, account.platform_uid
            )
            report.snapshots.append(snapshot)

            if snapshot.is_success:
                report.successful += 1
                report.updated += 1
            else:
                report.failed += 1
                report.errors.append(
                    f"{account.account_id}: {snapshot.failure_type.value} - {snapshot.error_detail[:80]}"
                )

        return report

    async def collect_all(self, limit_per_platform: int = 50) -> CollectionReport:
        """Collect from all platforms."""
        report = CollectionReport()
        platforms = ["darwinex", "etoro", "zulutrade", "jisilu", "joinquant", "bigquant", "xueqiu"]

        for platform in platforms:
            sub_report = await self.collect_platform(platform, limit=limit_per_platform)
            report.total_attempted += sub_report.total_attempted
            report.successful += sub_report.successful
            report.failed += sub_report.failed
            report.updated += sub_report.updated
            report.errors.extend(sub_report.errors)
            report.snapshots.extend(sub_report.snapshots)

        return report

    def get_failure_summary(self, days: int = 30) -> dict[str, int]:
        """Get failure counts by type."""
        return self._evidence.count_by_failure_type(days=days)

    def close(self) -> None:
        self._storage.close()
        self._evidence.close()

    def _update_account(self, platform: str, result: CollectorResult) -> None:
        """Update or create library account from collector result."""
        account_id = f"{platform}_{result.platform_uid}"
        existing = self._storage.get_account(account_id)

        try:
            plat = Platform(platform)
        except ValueError:
            logger.warning("Unknown platform: %s", platform)
            return

        now = datetime.now()
        account = LibraryAccount(
            account_id=account_id,
            platform=plat,
            platform_uid=result.platform_uid,
            display_name=result.display_name,
            profile_url=result.profile_url,
            portfolio_name=result.portfolio_name or (existing.portfolio_name if existing else ""),
            portfolio_url=result.portfolio_url or (existing.portfolio_url if existing else ""),
            asset_classes=tuple(result.asset_classes) if result.asset_classes else (existing.asset_classes if existing else ()),
            strategy_tags=tuple(result.strategy_tags) if result.strategy_tags else (existing.strategy_tags if existing else ()),
            requires_login=existing.requires_login if existing else False,
            last_update_seen=result.last_update_seen or now.strftime("%Y-%m-%d"),
            perf_summary=result.perf_summary,
            followers_copiers=result.followers_copiers or (existing.followers_copiers if existing else 0),
            fetch_stability=FetchStability.STABLE,
            l1_score=existing.l1_score if existing else 0.0,
            l2_promoted=existing.l2_promoted if existing else False,
            notes=existing.notes if existing else "",
            source_verified_at=now.strftime("%Y-%m-%d"),
            evidence_level=existing.evidence_level if existing else LibraryAccount.__dataclass_fields__["evidence_level"].default,
            strategy_description=result.strategy_description or (existing.strategy_description if existing else ""),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._storage.upsert_account(account)
