"""Read-only operational evidence from the canonical Paper ledger.

Historical NAV replay and successful daily operation are different evidence.
Only same-day, post-close committed receipts count as operational sessions.
Local commit timestamps are observations, not broker/exchange attestations.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from pathlib import Path

from .paper_book import read_paper_book_evidence


SHANGHAI = timezone(timedelta(hours=8))


def _timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def inspect_paper_book(
    path: str | Path, *, now: datetime | None = None, required_days: int = 60,
    threshold_hours: float = 24, max_daily_change: float = .20,
) -> dict:
    """Verify accounting and count a trailing streak of timely daily commits.

    A naive injected ``now`` is interpreted in Shanghai time. Calendar gaps
    use the verified source sessions; holidays do not break a streak. Freshness
    uses elapsed hours and can be relaxed explicitly for long closures.
    """
    if isinstance(required_days, bool) or not isinstance(required_days, int) or required_days < 1:
        raise ValueError("required_days must be a positive integer")
    if not math.isfinite(threshold_hours) or threshold_hours <= 0:
        raise ValueError("threshold_hours must be positive and finite")
    if not math.isfinite(max_daily_change) or max_daily_change <= 0:
        raise ValueError("max_daily_change must be positive and finite")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=SHANGHAI)
    reference = reference.astimezone(timezone.utc)
    path = Path(path)
    result = {"portfolio_id": f"research/{path.stem}", "source": "canonical_paper_book", "state_file": str(path),
              "status": "invalid", "stale": True, "passed": False, "required_days": required_days,
              "total_valid_days": 0, "consecutive_valid_days": 0, "longest_streak": 0,
              "first_valid_date": "", "last_valid_date": "", "invalid_days": [], "gaps": [],
              "age_hours": None, "threshold_hours": threshold_hours, "mtime": None,
              "message": "", "nav_history": [], "latest_trade_date": None, "committed_at": None,
              "evidence_scope": "local_paper_accounting_and_commit_timestamps"}
    try:
        evidence = read_paper_book_evidence(path)
    except Exception as exc:
        result["message"] = f"{result['portfolio_id']}: 账本校验失败: {exc}"
        return result
    daily, receipts = evidence["replay"]["daily_returns"], evidence["receipts"]
    by_day = {row["trade_date"]: row for row in receipts}
    result["nav_history"] = daily
    result["latest_trade_date"] = daily[-1]["trade_date"]
    latest = _timestamp(receipts[-1].get("committed_at"))
    if latest is not None:
        age = (reference - latest).total_seconds() / 3600
        result.update(age_hours=round(age, 2), mtime=latest.isoformat(), committed_at=latest.isoformat())
        result["stale"] = age < 0 or age > threshold_hours
    previous_nav, previous_commit = None, None
    streak = 0
    for row in daily:
        day, nav = row["trade_date"], row["nav"]
        receipt = by_day.get(day)
        committed = _timestamp(receipt.get("committed_at")) if receipt else None
        reason = None
        finite_nav = isinstance(nav, (int, float)) and not isinstance(nav, bool) and math.isfinite(nav)
        if not finite_nav or nav <= 0:
            reason = "invalid_nav"
        elif previous_nav is not None and previous_nav > 0 and abs(nav / previous_nav - 1) > max_daily_change:
            reason = "excessive_nav_change"
        elif receipt is None:
            reason = "missing_daily_commit"
            result["gaps"].append({"from": day, "to": day, "days": 1})
        elif committed is None:
            reason = "missing_or_invalid_commit_timestamp"
        elif committed > reference:
            reason = "future_commit_timestamp"
        elif committed.astimezone(SHANGHAI).date().isoformat() != day:
            reason = "historical_backfill"
        elif committed.astimezone(SHANGHAI).time() < time(15):
            reason = "committed_before_signal_close"
        elif previous_commit is not None and committed <= previous_commit:
            reason = "non_increasing_commit_timestamp"
        if reason:
            result["invalid_days"].append({"date": day, "reason": reason})
            streak = 0
        else:
            streak += 1
            result["total_valid_days"] += 1
            result["first_valid_date"] = result["first_valid_date"] or day
            result["last_valid_date"] = day
            result["longest_streak"] = max(result["longest_streak"], streak)
        previous_nav = nav if finite_nav else None
        if committed is not None:
            previous_commit = committed
    result["consecutive_valid_days"] = streak
    result["status"] = "invalid" if not streak else "stale" if result["stale"] else "ok"
    result["passed"] = streak >= required_days and result["status"] == "ok"
    result["message"] = (f"{result['portfolio_id']}: {result['status']}, "
                         f"连续按日提交 {streak}/{required_days} 个交易日；"
                         f"已核验行情 {len(daily)} 日，回执 {len(receipts)} 日")
    return result


def inspect_paper_books(directory: str | Path, **kwargs) -> list[dict]:
    root = Path(directory)
    if root.is_file() or root.suffix.lower() == ".sqlite3":
        return [inspect_paper_book(root, **kwargs)]
    return [inspect_paper_book(path, **kwargs) for path in sorted(root.glob("*.sqlite3"))]
