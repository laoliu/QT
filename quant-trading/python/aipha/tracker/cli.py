"""CLI entry point for the VIP portfolio tracker.

Commands:
    python -m aipha.tracker.cli fetch       -- Fetch snapshots for all tracked VIPs
    python -m aipha.tracker.cli diff        -- Compare latest vs previous snapshot
    python -m aipha.tracker.cli status      -- Show storage stats & latest snapshots
    python -m aipha.tracker.cli login       -- Interactive login to Xueqiu
    python -m aipha.tracker.cli plan create -- Create a new trade plan
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
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

logger = logging.getLogger("aipha.tracker")


def _load_config(config_dir: str = "configs/tracker") -> dict:
    """Load tracker configuration from YAML files.

    Raises SystemExit if critical config files are not found.
    """
    config_path = Path(config_dir)
    settings_file = config_path / "settings.yml"
    authors_file = config_path / "authors.yml"

    if not config_path.is_dir():
        logger.error("Config directory not found: %s", config_path.resolve())
        sys.exit(1)

    if not settings_file.exists():
        logger.error("Settings file not found: %s", settings_file.resolve())
        sys.exit(1)

    if not authors_file.exists():
        logger.error("Authors file not found: %s", authors_file.resolve())
        sys.exit(1)

    settings = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
    authors_data = yaml.safe_load(authors_file.read_text(encoding="utf-8")) or {}

    return {**settings, "authors_raw": authors_data.get("authors", [])}


def _parse_authors(raw_list: list[dict]) -> list:
    """Parse author configs into VIPAuthor objects."""
    from .models import VIPAuthor

    authors = []
    for item in raw_list:
        try:
            author = VIPAuthor.from_dict(item)
            if author.enabled:
                authors.append(author)
        except Exception as e:
            logger.warning("Failed to parse author config: %s — %s", item, e)
    return authors


def _should_notify(diff: object, level: str) -> bool:
    """Determine if a diff warrants notification based on level config.

    Args:
        diff: A PortfolioDiff object.
        level: One of 'all', 'changes', 'major'.

    Returns:
        True if notification should be sent.
    """
    if not getattr(diff, "has_changes", False):
        return False
    if level == "all":
        return True
    if level == "major":
        return bool(getattr(diff, "added", []) or getattr(diff, "removed", []))
    # default: "changes" — any change
    return True


def _write_audit_log(
    audit_config: dict, author_id: str, event: str, detail: str
) -> None:
    """Append a structured audit log entry.

    Args:
        audit_config: The 'audit' section from settings.yml.
        author_id: Author identifier.
        event: Event type (e.g. 'fetch', 'diff', 'notify').
        detail: Human-readable detail string.
    """
    if not audit_config.get("enabled", False):
        return
    from datetime import datetime as _dt

    log_dir = Path(audit_config.get("log_dir", "logs/audit"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"tracker_{_dt.now():%Y%m%d}.log"
    entry = f"{_dt.now():%Y-%m-%d %H:%M:%S} | {author_id} | {event} | {detail}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as e:
        logger.debug("Failed to write audit log: %s", e)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_fetch(args: argparse.Namespace, config: dict) -> None:
    """Fetch portfolio snapshots for all tracked VIP authors."""
    from .browser.base import BrowserSession
    from .browser.xueqiu import XueqiuCollector
    from .diff import compute_diff
    from .models import PortfolioSource
    from .notify.wxpusher import WxPusherNotifier
    from .storage import TrackerStorage

    authors = _parse_authors(config.get("authors_raw", []))
    if not authors:
        logger.error("No authors configured. Check configs/tracker/authors.yml")
        return

    # --author-id filter
    author_filter = getattr(args, "author_id", None)
    if author_filter:
        authors = [a for a in authors if a.author_id == author_filter]
        if not authors:
            logger.error("Author '%s' not found or not enabled.", author_filter)
            return

    dry_run = getattr(args, "dry_run", False)

    data_dir = config.get("data_dir", "data/tracker")
    notify_config = config.get("notification", {}).get("wxpusher", {})
    notify_level = notify_config.get("level", "changes")
    audit_config = config.get("audit", {})
    browser_config = config.get("browser", {})

    # Initialize notifier if configured (env var overrides YAML)
    notifier = None
    app_token = os.environ.get("WXPUSHER_APP_TOKEN") or notify_config.get("app_token")
    if notify_config.get("enabled") and app_token:
        notifier = WxPusherNotifier(
            app_token=app_token,
            uids=notify_config.get("uids", []),
        )

    if dry_run:
        print("🔍 Dry-run mode — will NOT save snapshots or send notifications.")

    storage = TrackerStorage(data_dir=data_dir)
    try:
        async with BrowserSession(
            data_dir=f"{data_dir}/browser",
            headless=browser_config.get("headless", True),
        ) as session:
            collector = XueqiuCollector(session)

            # Check login status
            if not await collector.check_login():
                logger.warning("未登录雪球，数据获取可能受限。请运行 'login' 命令先登录。")

            for author in authors:
                if author.platform != PortfolioSource.XUEQIU:
                    logger.info("Skipping non-Xueqiu author: %s", author.name)
                    continue

                # Warn on empty portfolio_codes
                if not author.portfolio_codes:
                    logger.warning(
                        "⚠️  Author '%s' (%s) has no portfolio_codes configured — skipping. "
                        "Please fill in portfolio_codes in configs/tracker/authors.yml.",
                        author.name,
                        author.author_id,
                    )
                    continue

                logger.info("=== Fetching: %s ===", author.name)

                for code in author.portfolio_codes:
                    snapshot = await collector.fetch_portfolio(
                        portfolio_code=code,
                        author_id=author.author_id,
                        author_name=author.name,
                    )
                    if snapshot is None:
                        logger.warning("Failed to fetch portfolio %s for %s", code, author.name)
                        _write_audit_log(
                            audit_config, author.author_id, "fetch_fail",
                            f"portfolio={code} reason=no_data",
                        )
                        continue

                    # Anomaly detection: empty holdings after successful fetch
                    if len(snapshot.holdings) == 0:
                        logger.error(
                            "⚠️  Fetched portfolio %s for %s returned 0 holdings! "
                            "Possible cookie expiry or page change. Skipping save.",
                            code,
                            author.name,
                        )
                        _write_audit_log(
                            audit_config, author.author_id, "anomaly",
                            f"portfolio={code} reason=zero_holdings",
                        )
                        continue

                    if dry_run:
                        print(
                            f"  [DRY-RUN] Would save snapshot for {author.name}/{code}: "
                            f"{len(snapshot.holdings)} holdings"
                        )
                        continue

                    # Get previous snapshot for diff
                    prev = storage.get_latest_snapshot(author.author_id)
                    storage.save_snapshot(snapshot)
                    logger.info(
                        "Saved snapshot %s: %d holdings",
                        snapshot.snapshot_id,
                        len(snapshot.holdings),
                    )
                    _write_audit_log(
                        audit_config, author.author_id, "fetch_ok",
                        f"portfolio={code} holdings={len(snapshot.holdings)} "
                        f"snapshot_id={snapshot.snapshot_id}",
                    )

                    # Compute and store diff
                    if prev:
                        diff = compute_diff(prev, snapshot)
                        storage.save_diff(diff)

                        if diff.has_changes:
                            print(diff.summary())
                            if notifier and _should_notify(diff, notify_level):
                                ok = notifier.notify_diff(diff)
                                if not ok:
                                    logger.error(
                                        "Failed to send notification for %s", author.name
                                    )
                                    _write_audit_log(
                                        audit_config, author.author_id, "notify_fail",
                                        f"portfolio={code}",
                                    )
                                else:
                                    _write_audit_log(
                                        audit_config, author.author_id, "notify_ok",
                                        f"portfolio={code}",
                                    )
                        else:
                            print(f"  {author.name}: 无变化")

                    await BrowserSession.random_delay(3, 6)

    finally:
        storage.close()
    print("\n✅ Fetch complete.")


async def cmd_login(args: argparse.Namespace, config: dict) -> None:
    """Interactive login to Xueqiu."""
    from .browser.base import BrowserSession
    from .browser.xueqiu import XueqiuCollector

    data_dir = config.get("data_dir", "data/tracker")

    async with BrowserSession(
        data_dir=f"{data_dir}/browser",
        headless=False,  # must be headed for interactive login
    ) as session:
        collector = XueqiuCollector(session)
        success = await collector.login_interactive(timeout=180)

    if success:
        print("✅ 登录成功！Cookie 已保存。")
    else:
        print("❌ 登录失败或超时。")
        sys.exit(1)


async def cmd_diff(args: argparse.Namespace, config: dict) -> None:
    """Show latest diffs for all tracked authors."""
    from .storage import TrackerStorage

    data_dir = config.get("data_dir", "data/tracker")
    with TrackerStorage(data_dir=data_dir) as storage:
        authors = _parse_authors(config.get("authors_raw", []))

        for author in authors:
            snapshots = storage.get_snapshots(author.author_id, limit=2)
            if len(snapshots) < 2:
                print(f"  {author.name}: 快照不足 (需要至少2个)")
                continue

            from .diff import compute_diff

            diff = compute_diff(snapshots[1], snapshots[0])  # older first
            print(diff.summary())
            print()


async def cmd_status(args: argparse.Namespace, config: dict) -> None:
    """Show storage stats and latest snapshots."""
    from .storage import TrackerStorage

    data_dir = config.get("data_dir", "data/tracker")
    with TrackerStorage(data_dir=data_dir) as storage:
        stats = storage.get_stats()
        print("=== Tracker Storage Stats ===")
        print(f"  快照数:     {stats['snapshot_count']}")
        print(f"  差异记录数:  {stats['diff_count']}")
        print(f"  交易计划数:  {stats['plan_count']}")
        print(f"  数据库:     {stats['db_path']}")

        if stats["tracked_authors"]:
            print("\n  追踪的大V:")
            for a in stats["tracked_authors"]:
                latest = storage.list_snapshot_meta(a["author_id"], limit=1)
                if latest:
                    meta = latest[0]
                    print(
                        f"    - {a['author_name']}: "
                        f"最新快照 {meta['snapshot_time']}, "
                        f"{meta['holding_count']} 只股票"
                    )
                else:
                    print(f"    - {a['author_name']}: 无快照")


async def cmd_plan(args: argparse.Namespace, config: dict) -> None:
    """Manage trade plans: create, list, approve, execute, cancel."""
    from .models import Confidence, TradePlan, TradeDirection
    from .storage import TrackerStorage

    data_dir = config.get("data_dir", "data/tracker")

    sub = args.plan_action

    # Validate reasons count before opening storage (avoid creating empty DB)
    if sub == "create":
        reasons = [r.strip() for r in args.reasons.split("|") if r.strip()]
        if len(reasons) < 3:
            print("❌ 交易计划必须至少填写 3 条理由（用 | 分隔）。强制你在交易前思考。")
            return

    with TrackerStorage(data_dir=data_dir) as storage:
        if sub == "create":
            symbol = args.symbol
            name = args.name or symbol
            direction = TradeDirection(args.direction.upper())
            reasons = [r.strip() for r in args.reasons.split("|") if r.strip()]

            confidence = Confidence(args.confidence)
            plan = TradePlan(
                plan_id=TradePlan.new_id(),
                symbol=symbol,
                name=name,
                direction=direction,
                reasons=reasons,
                stop_loss_price=args.stop_loss_price,
                stop_loss_pct=args.stop_loss_pct,
                target_position_pct=args.target_pct,
                confidence=confidence,
                vip_references=[r.strip() for r in (args.vip_refs or "").split(",") if r.strip()],
            )
            storage.save_trade_plan(plan)
            print(f"✅ 交易计划已创建: {plan.plan_id}")
            print(f"   {direction.value} {name}({symbol})")
            print(f"   止损价: {plan.stop_loss_price}  止损比例: {plan.stop_loss_pct:.1%}")
            print(f"   信心: {confidence.value}  理由: {len(reasons)} 条")

        elif sub == "list":
            status_filter = args.status or None
            plans = storage.get_trade_plans(status=status_filter, limit=20)
            if not plans:
                print("  无交易计划。")
            else:
                print(f"{'计划ID':<16} {'方向':<5} {'标的':<12} {'状态':<10} {'创建时间':<20}")
                print("-" * 65)
                for p in plans:
                    print(
                        f"{p.plan_id:<16} {p.direction.value:<5} "
                        f"{p.name:<12} {p.status.value:<10} "
                        f"{p.created_at:%Y-%m-%d %H:%M}"
                    )

        elif sub == "approve":
            plan = storage.get_trade_plan(args.plan_id)
            if not plan:
                print(f"❌ 未找到计划: {args.plan_id}")
            else:
                try:
                    plan.approve()
                    storage.save_trade_plan(plan)
                    print(f"✅ 计划 {plan.plan_id} 已审批通过 → APPROVED")
                except ValueError as e:
                    print(f"❌ {e}")

        elif sub == "execute":
            plan = storage.get_trade_plan(args.plan_id)
            if not plan:
                print(f"❌ 未找到计划: {args.plan_id}")
            else:
                try:
                    plan.execute()
                    storage.save_trade_plan(plan)
                    print(f"✅ 计划 {plan.plan_id} 已标记执行 → EXECUTED")
                except ValueError as e:
                    print(f"❌ {e}")

        elif sub == "cancel":
            plan = storage.get_trade_plan(args.plan_id)
            if not plan:
                print(f"❌ 未找到计划: {args.plan_id}")
            else:
                reason = args.reason or ""
                try:
                    plan.cancel(reason)
                    storage.save_trade_plan(plan)
                    print(f"✅ 计划 {plan.plan_id} 已取消")
                except ValueError as e:
                    print(f"❌ {e}")

        else:
            print("未知子命令。可用: create, list, approve, execute, cancel")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI main entry point."""
    parser = argparse.ArgumentParser(
        prog="aipha-tracker",
        description="Aipha-Q VIP Portfolio Tracker — 严谨决策辅助系统",
    )
    parser.add_argument(
        "--config-dir",
        default="configs/tracker",
        help="Path to tracker config directory (default: configs/tracker)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    fetch_parser = sub.add_parser("fetch", help="Fetch portfolio snapshots for all tracked VIPs")
    fetch_parser.add_argument(
        "--author-id",
        default=None,
        help="Only fetch for this author_id (e.g. 'xzlx')",
    )
    fetch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate fetch without saving or notifying",
    )

    sub.add_parser("login", help="Interactive login to Xueqiu (headed browser)")
    sub.add_parser("diff", help="Show latest portfolio diffs")
    sub.add_parser("status", help="Show storage stats")

    # plan subcommand with sub-actions
    plan_parser = sub.add_parser("plan", help="Manage trade plans")
    plan_sub = plan_parser.add_subparsers(dest="plan_action", help="Plan sub-commands")

    # plan create
    plan_create = plan_sub.add_parser("create", help="Create a new trade plan")
    plan_create.add_argument("--symbol", required=True, help="Stock symbol, e.g. 600519.SH")
    plan_create.add_argument("--name", default="", help="Stock name")
    plan_create.add_argument("--direction", required=True, choices=["BUY", "SELL", "buy", "sell"], help="Trade direction")
    plan_create.add_argument("--reasons", required=True, help="Trade reasons (>=3), separated by |")
    plan_create.add_argument("--stop-loss-price", type=float, required=True, help="Stop loss price")
    plan_create.add_argument("--stop-loss-pct", type=float, required=True, help="Stop loss pct (0-1)")
    plan_create.add_argument("--target-pct", type=float, required=True, help="Target position pct (0-1)")
    plan_create.add_argument("--confidence", default="中", choices=["高", "中", "低"], help="Confidence level")
    plan_create.add_argument("--vip-refs", default="", help="VIP references, comma-separated")

    # plan list
    plan_list = plan_sub.add_parser("list", help="List trade plans")
    plan_list.add_argument("--status", default=None, help="Filter by status: DRAFT/APPROVED/EXECUTED/CANCELLED")

    # plan approve
    plan_approve = plan_sub.add_parser("approve", help="Approve a draft plan")
    plan_approve.add_argument("plan_id", help="Plan ID to approve")

    # plan execute
    plan_execute = plan_sub.add_parser("execute", help="Mark plan as executed")
    plan_execute.add_argument("plan_id", help="Plan ID to execute")

    # plan cancel
    plan_cancel = plan_sub.add_parser("cancel", help="Cancel a plan")
    plan_cancel.add_argument("plan_id", help="Plan ID to cancel")
    plan_cancel.add_argument("--reason", default="", help="Cancellation reason")

    args = parser.parse_args()

    # Logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        sys.exit(0)

    config = _load_config(args.config_dir)

    # Dispatch
    cmd_map = {
        "fetch": cmd_fetch,
        "login": cmd_login,
        "diff": cmd_diff,
        "status": cmd_status,
        "plan": cmd_plan,
    }
    coro = cmd_map[args.command](args, config)
    asyncio.run(coro)


if __name__ == "__main__":
    main()
