"""CLI entry point for the Public Quantitative Portfolio Library.

Commands:
    python -m aipha.tracker.library_cli seed     -- Import seeds from YAML config
    python -m aipha.tracker.library_cli list     -- List all indexed accounts
    python -m aipha.tracker.library_cli search   -- Search accounts by criteria
    python -m aipha.tracker.library_cli stats    -- Show library statistics
    python -m aipha.tracker.library_cli add      -- Add a single account interactively
    python -m aipha.tracker.library_cli score    -- Compute/update L1 score for an account
    python -m aipha.tracker.library_cli stale    -- List stale accounts (not updated recently)
    python -m aipha.tracker.library_cli maintain -- Run full maintenance cycle
    python -m aipha.tracker.library_cli map      -- Show replicability mapping for an account
    python -m aipha.tracker.library_cli quality  -- Run quality checks
    python -m aipha.tracker.library_cli creds    -- Manage credentials (list/check)
    python -m aipha.tracker.library_cli collect  -- Run automated data collection
    python -m aipha.tracker.library_cli evidence -- Show evidence history/failure summary
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "ERROR: pyyaml is required. Install it with:\n"
        "  conda install pyyaml   # or: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)

from .l2_audit_gate import (
    L2AuditGate,
    ReviewRecord,
    ReviewRole,
    ReviewVerdict,
)
from .library_models import (
    ArchiveEntry,
    EvidenceLevel,
    FetchStability,
    LibraryAccount,
    Platform,
    compute_l1_score,
    score_account,
)
from .library_storage import LibraryStorage

logger = logging.getLogger("aipha.tracker.library")

DEFAULT_DATA_DIR = "data/tracker"
DEFAULT_SEEDS_PATH = "configs/tracker/library_seeds.yml"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_seed(args: argparse.Namespace) -> None:
    """Import seed accounts from YAML configuration."""
    seeds_path = Path(args.seeds)
    if not seeds_path.exists():
        logger.error("Seeds file not found: %s", seeds_path)
        sys.exit(1)

    data = yaml.safe_load(seeds_path.read_text(encoding="utf-8")) or {}
    seeds = data.get("seeds", [])
    if not seeds:
        print("No seeds found in config file.")
        return

    storage = LibraryStorage(args.data_dir)
    imported = 0
    skipped = 0

    for entry in seeds:
        try:
            platform = Platform(entry["platform"])
            uid = entry["platform_uid"]
            account_id = LibraryAccount.make_id(platform, uid)

            account = LibraryAccount(
                account_id=account_id,
                platform=platform,
                platform_uid=uid,
                display_name=entry.get("display_name", uid),
                profile_url=entry.get("profile_url", ""),
                portfolio_name=entry.get("portfolio_name", ""),
                portfolio_url=entry.get("portfolio_url", ""),
                asset_classes=tuple(entry.get("asset_classes", [])),
                strategy_tags=tuple(entry.get("strategy_tags", [])),
                requires_login=entry.get("requires_login", False),
                perf_summary=entry.get("perf_summary", {}),
                followers_copiers=entry.get("followers_copiers", 0),
                fetch_stability=FetchStability.MANUAL_ONLY,
                notes=entry.get("notes", ""),
                source_verified_at=entry.get("source_verified_at", ""),
                evidence_level=EvidenceLevel(entry.get("evidence_level", "D")),
                strategy_description=entry.get("strategy_description", ""),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            storage.upsert_account(account)
            imported += 1
        except (KeyError, ValueError) as e:
            logger.warning("Skipping invalid seed entry: %s", e)
            skipped += 1

    storage.close()
    print(f"Seed import complete: {imported} imported, {skipped} skipped.")


def cmd_list(args: argparse.Namespace) -> None:
    """List all indexed accounts."""
    storage = LibraryStorage(args.data_dir)
    accounts = storage.list_all_accounts()
    storage.close()

    if not accounts:
        print("Library is empty. Run 'seed' command first.")
        return

    print(f"{'ID':<30} {'Platform':<12} {'Name':<20} {'Score':>5} {'L2':>3}")
    print("-" * 75)
    for a in accounts:
        l2_mark = "✓" if a.l2_promoted else ""
        print(f"{a.account_id:<30} {a.platform.value:<12} {a.display_name:<20} {a.l1_score:>5.2f} {l2_mark:>3}")

    print(f"\nTotal: {len(accounts)} accounts")


def cmd_search(args: argparse.Namespace) -> None:
    """Search accounts with filters."""
    storage = LibraryStorage(args.data_dir)
    results = storage.search_accounts(
        platform=args.platform,
        min_score=args.min_score,
        l2_only=args.l2_only,
        limit=args.limit,
    )
    storage.close()

    if not results:
        print("No results found.")
        return

    for a in results:
        print(f"[{a.l1_score:.2f}] {a.account_id} — {a.display_name} ({a.platform.value})")
        if a.portfolio_name:
            print(f"       Portfolio: {a.portfolio_name}")
        if a.strategy_tags:
            print(f"       Tags: {', '.join(a.strategy_tags)}")
        print()


def cmd_stats(args: argparse.Namespace) -> None:
    """Show library statistics."""
    storage = LibraryStorage(args.data_dir)
    stats = storage.get_stats()
    storage.close()

    print("=== Portfolio Library Statistics ===")
    print(f"Total accounts: {stats['total_accounts']}")
    print(f"L2 promoted:    {stats['l2_promoted']}")
    print("\nBy platform:")
    for platform, count in sorted(stats["by_platform"].items()):
        print(f"  {platform:<15} {count}")


def cmd_score(args: argparse.Namespace) -> None:
    """Compute and update L1 score for a specific account."""
    storage = LibraryStorage(args.data_dir)
    account = storage.get_account(args.account_id)

    if account is None:
        print(f"Account not found: {args.account_id}")
        storage.close()
        sys.exit(1)

    print(f"Scoring: {account.display_name} ({account.platform.value})")
    print("Enter scores (1-5 scale) for each dimension:")

    dims = [
        ("transparency", "透明度 (持仓/策略说明完整度)"),
        ("update_frequency", "持续更新 (近期活跃度)"),
        ("explainability", "可解释性 (策略逻辑清晰度)"),
        ("perf_verifiability", "绩效可核验 (收益数据可验证度)"),
        ("risk_disclosure", "风险披露 (回撤/波动信息)"),
        ("replicability", "可复刻性 (映射到内部框架)"),
        ("capacity_fit", "容量适配 (资产类别匹配度)"),
    ]

    scores = {}
    for key, label in dims:
        while True:
            try:
                val = float(input(f"  {label}: "))
                if 1.0 <= val <= 5.0:
                    scores[key] = val
                    break
                print("    Must be 1-5.")
            except (ValueError, EOFError):
                print("    Invalid input.")

    final_score = compute_l1_score(**scores)
    print(f"\nWeighted L1 Score: {final_score:.2f}")
    print(f"L2 promotion threshold: 3.50")
    print(f"Eligible for L2: {'YES' if final_score >= 3.5 else 'NO'}")

    # Update account with new score (create new frozen instance)
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
        l1_score=final_score,
        l2_promoted=final_score >= 3.5,
        notes=account.notes,
        source_verified_at=account.source_verified_at,
        evidence_level=account.evidence_level,
        strategy_description=account.strategy_description,
        created_at=account.created_at,
        updated_at=datetime.now(),
    )
    storage.upsert_account(updated)
    storage.close()
    print("Score saved.")


def cmd_stale(args: argparse.Namespace) -> None:
    """List accounts not updated within the threshold."""
    from .library_maintenance import LibraryMaintainer

    storage = LibraryStorage(args.data_dir)
    maintainer = LibraryMaintainer(
        storage, stale_threshold_days=args.days
    )
    stale = maintainer.detect_stale()
    storage.close()

    if not stale:
        print(f"No stale accounts (threshold: {args.days} days).")
        return

    print(f"Stale accounts (not updated in {args.days}+ days):")
    print(f"{'ID':<30} {'Platform':<12} {'Last Updated':<20}")
    print("-" * 65)
    for a in stale:
        print(f"{a.account_id:<30} {a.platform.value:<12} {a.updated_at.strftime('%Y-%m-%d'):<20}")
    print(f"\nTotal: {len(stale)} stale accounts")


def cmd_maintain(args: argparse.Namespace) -> None:
    """Run a full maintenance cycle."""
    from .library_maintenance import LibraryMaintainer

    storage = LibraryStorage(args.data_dir)
    maintainer = LibraryMaintainer(
        storage,
        stale_threshold_days=args.stale_days,
        archive_outdated_days=args.archive_days,
    )
    report = maintainer.run_full_cycle()
    storage.close()

    print(report.summary())
    if report.stale_accounts:
        print(f"\nStale accounts: {', '.join(report.stale_accounts[:10])}")
    if report.broken_accounts:
        print(f"\nBroken accounts: {', '.join(report.broken_accounts[:10])}")
    if report.archived_entries:
        print(f"\nNewly outdated archives: {', '.join(report.archived_entries[:10])}")
    if report.errors:
        print(f"\nErrors:")
        for err in report.errors:
            print(f"  - {err}")


def cmd_map(args: argparse.Namespace) -> None:
    """Show or create replicability mapping for an account."""
    from .library_maintenance import (
        MappingDimension,
        ReplicabilityMapping,
        compute_replicability_score,
    )

    storage = LibraryStorage(args.data_dir)
    account = storage.get_account(args.account_id)

    if account is None:
        print(f"Account not found: {args.account_id}")
        storage.close()
        sys.exit(1)

    print(f"Replicability mapping: {account.display_name} ({account.platform.value})")
    print("Enter scores (1-5) for each replicability dimension:")

    dim_labels = [
        ("data_availability", "数据可得性 (Aipha-Q 已有数据能否支持)"),
        ("factor_mappable", "因子可映射 (核心因子是否在内部因子库中)"),
        ("rules_implementable", "交易规则可实现 (调仓/止损能否程序化)"),
        ("capacity_compatible", "容量兼容 (资金规模是否匹配)"),
        ("market_reachable", "市场可达 (标的是否在支持范围内)"),
    ]

    dimensions = []
    for key, label in dim_labels:
        while True:
            try:
                val = float(input(f"  {label}: "))
                if 1.0 <= val <= 5.0:
                    note = input(f"    备注 (可选): ").strip()
                    dimensions.append(MappingDimension(name=key, score=val, notes=note))
                    break
                print("    Must be 1-5.")
            except (ValueError, EOFError):
                print("    Invalid input.")

    score = compute_replicability_score(dimensions)
    print(f"\nComposite Replicability Score: {score:.2f}/5.00")
    print(f"Assessment: ", end="")
    if score >= 4.0:
        print("HIGH — ready for replication attempt")
    elif score >= 3.0:
        print("MEDIUM — partial replication feasible")
    elif score >= 2.0:
        print("LOW — significant gaps exist")
    else:
        print("MINIMAL — not suitable for replication")

    # Update archive replicability_score if L2 exists
    archives = storage.get_archives_for_account(args.account_id)
    if archives:
        arc = archives[0]
        updated_arc = ArchiveEntry(
            archive_id=arc.archive_id,
            account_id=arc.account_id,
            archive_path=arc.archive_path,
            methodology_summary=arc.methodology_summary,
            factor_exposure=arc.factor_exposure,
            replicability_score=score,
            last_reviewed=datetime.now().strftime("%Y-%m-%d"),
            reviewer="expert_d",
            status=arc.status,
        )
        storage.upsert_archive(updated_arc)
        print(f"Updated archive {arc.archive_id} replicability_score = {score:.2f}")

    storage.close()


def cmd_quality(args: argparse.Namespace) -> None:
    """Run automated quality checks."""
    from .library_credentials import CredentialStore
    from .library_quality import QualityChecker

    storage = LibraryStorage(args.data_dir)
    cred_store = CredentialStore(args.data_dir)
    checker = QualityChecker(storage, cred_store, stale_l2_days=args.stale_days)
    report = checker.run_all_checks()
    storage.close()
    cred_store.close()

    print(report.summary())
    if report.alerts:
        print("\nAlerts:")
        for alert in report.alerts:
            icon = "🔴" if alert.severity == "critical" else "🟡" if alert.severity == "warning" else "ℹ️"
            print(f"  {icon} [{alert.rule_name}] {alert.account_id}: {alert.message}")
    else:
        print("\nNo quality issues detected.")


def cmd_creds(args: argparse.Namespace) -> None:
    """List and check credential health."""
    from .library_credentials import CredentialStore, Platform

    cred_store = CredentialStore(args.data_dir)

    if args.platform:
        creds = cred_store.list_by_platform(Platform(args.platform))
    else:
        creds = []
        for p in Platform:
            creds.extend(cred_store.list_by_platform(p))

    cred_store.close()

    if not creds:
        print("No credentials stored.")
        return

    print(f"{'ID':<25} {'Platform':<12} {'Type':<10} {'Status':<15} {'Expires':<12}")
    print("-" * 75)
    for c in creds:
        print(
            f"{c.credential_id:<25} {c.platform.value:<12} "
            f"{c.credential_type.value:<10} {c.status.value:<15} {c.expires_at:<12}"
        )
    print(f"\nTotal: {len(creds)} credentials")

    expiring = [c for c in creds if c.is_expiring_soon() or c.is_expired()]
    if expiring:
        print(f"\n⚠️  {len(expiring)} credential(s) expiring/expired!")


def cmd_collect(args: argparse.Namespace) -> None:
    """Run automated collection for one or all platforms."""
    import asyncio
    from .collection_orchestrator import CollectionOrchestrator

    platform = getattr(args, "platform", None)
    account_uid = getattr(args, "account_uid", None)
    authenticated_xueqiu = getattr(args, "xueqiu_authenticated", False)

    if authenticated_xueqiu:
        if platform != "xueqiu" or not account_uid:
            raise SystemExit(
                "--xueqiu-authenticated requires --platform xueqiu and --account-uid"
            )
        portfolio_codes = getattr(args, "portfolio_code", None) or []
        if not portfolio_codes:
            raise SystemExit(
                "--xueqiu-authenticated requires at least one --portfolio-code"
            )

        async def _collect_authenticated_xueqiu():
            from .browser.base import BrowserSession
            from .collectors.xueqiu import XueqiuAuthenticatedCollector

            async with BrowserSession(
                data_dir=Path(args.data_dir) / "browser",
                headless=not getattr(args, "headed", False),
            ) as session:
                adapter = XueqiuAuthenticatedCollector(
                    session,
                    portfolio_codes,
                    author_id=getattr(args, "author_id", "") or account_uid,
                    author_name=getattr(args, "author_name", "") or account_uid,
                )
                orchestrator = CollectionOrchestrator(
                    data_dir=args.data_dir,
                    collector_factory=lambda requested: adapter if requested == "xueqiu" else None,
                )
                try:
                    snapshot = await orchestrator.collect_account(platform, account_uid)
                    return snapshot
                finally:
                    orchestrator.close()

        snapshot = asyncio.run(_collect_authenticated_xueqiu())
        if snapshot.is_success:
            print(
                f"✓ Collected authenticated xueqiu/{account_uid} — "
                f"{len(snapshot.parsed_fields.get('holdings') or [])} holdings; "
                "performance not inferred"
            )
        else:
            print(
                f"✗ Failed xueqiu/{account_uid} — "
                f"{snapshot.failure_type.value}: {snapshot.error_detail[:100]}"
            )
        return

    orchestrator = CollectionOrchestrator(data_dir=args.data_dir)

    try:
        if account_uid and platform:
            # Single account collection
            snapshot = asyncio.run(orchestrator.collect_account(platform, account_uid))
            if snapshot.is_success:
                print(f"✓ Collected {platform}/{account_uid} — quality: {snapshot.evidence_quality.value}")
                if snapshot.parsed_fields.get("perf_summary"):
                    ps = snapshot.parsed_fields["perf_summary"]
                    print(f"  perf: {json.dumps(ps, ensure_ascii=False)}")
            else:
                print(f"✗ Failed {platform}/{account_uid} — {snapshot.failure_type.value}: {snapshot.error_detail[:100]}")
        elif platform:
            # All accounts for a platform
            report = asyncio.run(orchestrator.collect_platform(platform))
            print(f"Collection complete for [{platform}]:")
            print(f"  Attempted: {report.total_attempted}")
            print(f"  Successful: {report.successful} ({report.success_rate:.0%})")
            print(f"  Failed: {report.failed}")
            if report.errors:
                print(f"\nErrors ({len(report.errors)}):")
                for e in report.errors[:10]:
                    print(f"  • {e}")
        else:
            # All platforms
            report = asyncio.run(orchestrator.collect_all())
            print("Collection complete (all platforms):")
            print(f"  Attempted: {report.total_attempted}")
            print(f"  Successful: {report.successful} ({report.success_rate:.0%})")
            print(f"  Failed: {report.failed}")
            if report.errors:
                print(f"\nErrors ({min(len(report.errors), 20)} shown):")
                for e in report.errors[:20]:
                    print(f"  • {e}")
    finally:
        orchestrator.close()


def cmd_evidence(args: argparse.Namespace) -> None:
    """Show evidence history or failure summary."""
    from .evidence import EvidenceStore

    store = EvidenceStore(data_dir=args.data_dir)
    account_id = getattr(args, "account_id", None)

    try:
        if account_id:
            # Show history for a specific account
            history = store.get_history(account_id, limit=args.limit)
            if not history:
                print(f"No evidence snapshots for: {account_id}")
                return
            print(f"Evidence history for {account_id} ({len(history)} snapshots):\n")
            for snap in history:
                status = "✓" if snap.is_success else "✗"
                print(f"  {status} {snap.collected_at:%Y-%m-%d %H:%M} | "
                      f"{snap.evidence_quality.value:<8} | "
                      f"{snap.failure_type.value:<15} | "
                      f"{snap.response_time_ms}ms")
                if snap.error_detail:
                    print(f"    error: {snap.error_detail[:80]}")
        else:
            # Show failure summary
            summary = store.count_by_failure_type(days=args.days)
            if not summary:
                print(f"No evidence snapshots in the last {args.days} days.")
                return
            print(f"Evidence summary (last {args.days} days):\n")
            total = sum(summary.values())
            for ftype, count in sorted(summary.items(), key=lambda x: -x[1]):
                pct = count / total * 100
                print(f"  {ftype:<18} {count:>5} ({pct:.1f}%)")
            print(f"\n  {'TOTAL':<18} {total:>5}")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# promote command
# ---------------------------------------------------------------------------


def cmd_promote(args: argparse.Namespace) -> None:
    """Submit reviews and promote an account to L2."""
    storage = LibraryStorage(data_dir=args.data_dir)
    try:
        gate = L2AuditGate(storage)
        account_id = args.account_id

        account = storage.get_account(account_id)
        if account is None:
            print(f"ERROR: Account '{account_id}' not found.", file=sys.stderr)
            sys.exit(1)

        if args.check_only:
            result = gate.check(account_id)
            print(f"Account: {account_id} ({account.display_name})")
            print(f"L1 Score: {account.l1_score:.2f}")
            print(f"Gate: {result.summary}")
            if result.reviews:
                print("\nSubmitted reviews:")
                for r in result.reviews:
                    print(f"  {r.role.value:<28} {r.verdict.value:<12} by {r.reviewer} ({r.date})")
            return

        if args.submit_role:
            role = ReviewRole(args.submit_role)
            verdict = ReviewVerdict(args.verdict or "pass")
            review = ReviewRecord(
                role=role,
                verdict=verdict,
                reviewer=args.reviewer or "cli_user",
                date=datetime.now().strftime("%Y-%m-%d"),
                notes=args.notes or "",
            )
            gate.submit_review(account_id, review)
            print(f"Review submitted: {role.value} -> {verdict.value}")
            result = gate.check(account_id)
            print(f"Gate status: {result.summary}")
            return

        if args.execute:
            # Submit all three roles as PASS (for automated promotion)
            for role in ReviewRole:
                gate.submit_review(
                    account_id,
                    ReviewRecord(
                        role=role,
                        verdict=ReviewVerdict.PASS,
                        reviewer=args.reviewer or "cli_automated",
                        date=datetime.now().strftime("%Y-%m-%d"),
                        notes=args.notes or "Automated promotion via CLI",
                    ),
                )
            entry = gate.promote(
                account_id,
                archive_path=args.archive_path or "",
                methodology_summary=args.notes or "",
                replicability_score=args.replicability or 0.0,
            )
            print(f"SUCCESS: {account_id} promoted to L2")
            print(f"  Archive ID: {entry.archive_id}")
            print(f"  Archive path: {entry.archive_path}")
            return

        # Default: show status
        result = gate.check(account_id)
        print(f"Account: {account_id} ({account.display_name})")
        print(f"Gate: {result.summary}")
    finally:
        storage.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="library_cli",
        description="Public Quantitative Portfolio Library management",
    )
    parser.add_argument(
        "--data-dir", default=DEFAULT_DATA_DIR,
        help=f"Data directory (default: {DEFAULT_DATA_DIR})",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # seed
    p_seed = subparsers.add_parser("seed", help="Import seeds from YAML")
    p_seed.add_argument("--seeds", default=DEFAULT_SEEDS_PATH, help="Seeds YAML path")

    # list
    subparsers.add_parser("list", help="List all indexed accounts")

    # search
    p_search = subparsers.add_parser("search", help="Search accounts")
    p_search.add_argument("--platform", help="Filter by platform")
    p_search.add_argument("--min-score", type=float, default=0.0, help="Minimum L1 score")
    p_search.add_argument("--l2-only", action="store_true", help="Only L2 promoted")
    p_search.add_argument("--limit", type=int, default=50, help="Max results")

    # stats
    subparsers.add_parser("stats", help="Show library statistics")

    # score
    p_score = subparsers.add_parser("score", help="Score an account")
    p_score.add_argument("account_id", help="Account ID to score")

    # stale
    p_stale = subparsers.add_parser("stale", help="List stale accounts")
    p_stale.add_argument("--days", type=int, default=30, help="Stale threshold in days")

    # maintain
    p_maintain = subparsers.add_parser("maintain", help="Run full maintenance cycle")
    p_maintain.add_argument("--stale-days", type=int, default=30, help="Stale threshold")
    p_maintain.add_argument("--archive-days", type=int, default=90, help="Archive outdated threshold")

    # map
    p_map = subparsers.add_parser("map", help="Assess replicability mapping")
    p_map.add_argument("account_id", help="Account ID to map")

    # quality
    p_quality = subparsers.add_parser("quality", help="Run quality checks")
    p_quality.add_argument("--stale-days", type=int, default=60, help="Stale L2 threshold")

    # creds
    p_creds = subparsers.add_parser("creds", help="List/check credentials")
    p_creds.add_argument("--platform", help="Filter by platform")

    # collect
    p_collect = subparsers.add_parser("collect", help="Run automated data collection")
    p_collect.add_argument("--platform", help="Collect only this platform")
    p_collect.add_argument("--account-uid", dest="account_uid", help="Collect a single account (requires --platform)")
    p_collect.add_argument(
        "--xueqiu-authenticated", action="store_true",
        help="Explicitly collect Xueqiu holdings with the persistent Playwright login session",
    )
    p_collect.add_argument(
        "--portfolio-code", action="append", default=[],
        help="Xueqiu portfolio code (repeat for multiple portfolios)",
    )
    p_collect.add_argument("--author-id", default="", help="Optional tracker author ID")
    p_collect.add_argument("--author-name", default="", help="Optional display name")
    p_collect.add_argument(
        "--headed", action="store_true",
        help="Show the browser window (useful for interactive login)",
    )

    # evidence
    p_evidence = subparsers.add_parser("evidence", help="Show evidence history/summary")
    p_evidence.add_argument("account_id", nargs="?", help="Account ID for history (omit for summary)")
    p_evidence.add_argument("--days", type=int, default=30, help="Summary lookback days")
    p_evidence.add_argument("--limit", type=int, default=20, help="Max history entries")

    # promote
    p_promote = subparsers.add_parser("promote", help="L2 promotion audit gate")
    p_promote.add_argument("account_id", help="Account ID to promote/check")
    p_promote.add_argument("--check-only", action="store_true", help="Only check gate status")
    p_promote.add_argument("--submit-role", choices=[r.value for r in ReviewRole], help="Submit a review for this role")
    p_promote.add_argument("--verdict", choices=[v.value for v in ReviewVerdict], help="Review verdict (with --submit-role)")
    p_promote.add_argument("--reviewer", help="Reviewer name")
    p_promote.add_argument("--notes", help="Review notes")
    p_promote.add_argument("--execute", action="store_true", help="Auto-approve all roles and promote")
    p_promote.add_argument("--archive-path", help="Path to the L2 archive report")
    p_promote.add_argument("--replicability", type=float, default=0.0, help="Replicability score (0-5)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    commands = {
        "seed": cmd_seed,
        "list": cmd_list,
        "search": cmd_search,
        "stats": cmd_stats,
        "score": cmd_score,
        "stale": cmd_stale,
        "maintain": cmd_maintain,
        "map": cmd_map,
        "quality": cmd_quality,
        "creds": cmd_creds,
        "collect": cmd_collect,
        "evidence": cmd_evidence,
        "promote": cmd_promote,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
