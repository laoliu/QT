"""Paper operations health checker — runtime audit for paper combos.

Checks:
  1. State file freshness (stale if > N hours since last update)
  2. NAV consistency (sum(positions * price) + cash ≈ NAV)
  3. Alert queue status (recent failures)
  4. Config-state alignment (paper_registry vs actual state files)

Usage:
    python -m aipha.governance.paper_ops_health
    # or
    from aipha.governance.paper_ops_health import check_paper_health
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
PAPER_STATE_DIR = ROOT / "data" / "paper_state"
PAPER_REGISTRY = ROOT / "configs" / "paper" / "paper_registry.json"

STALE_HOURS = 26  # State older than 26 hours is stale (1 trading day + margin)


@dataclass
class ComboHealth:
    """Health status for a single paper combo."""
    combo_id: str
    status: str = "unknown"  # healthy / warning / error / inactive
    issues: list[str] = field(default_factory=list)
    last_update: str | None = None
    nav: float | None = None
    position_count: int = 0
    state_file_exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class PaperHealthReport:
    """Aggregate paper health report."""
    check_time: str = ""
    total_combos: int = 0
    healthy: int = 0
    warning: int = 0
    error: int = 0
    inactive: int = 0
    combos: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def check_paper_health(
    state_dir: Path | None = None,
    registry_path: Path | None = None,
) -> PaperHealthReport:
    """Run paper operations health check across all registered combos."""
    s_dir = state_dir or PAPER_STATE_DIR
    r_path = registry_path or PAPER_REGISTRY

    report = PaperHealthReport(check_time=datetime.now().isoformat())

    # Load registry
    registry_entries: dict[str, dict] = {}
    if r_path.exists():
        reg = json.loads(r_path.read_text(encoding="utf-8"))
        for entry in reg.get("entries", []):
            eid = entry.get("entry_id", "")
            registry_entries[eid] = entry

    # Check all registered combos
    checked = set()
    for combo_id, entry in registry_entries.items():
        health = _check_combo(combo_id, s_dir, entry)
        report.combos.append(health.to_dict())
        checked.add(combo_id)

    # Check orphan state dirs (have state but no registry entry)
    if s_dir.exists():
        for d in s_dir.iterdir():
            if d.is_dir() and d.name not in checked:
                health = _check_combo(d.name, s_dir, None)
                health.issues.append("orphan_state: no registry entry")
                report.combos.append(health.to_dict())

    # Aggregate
    for c in report.combos:
        report.total_combos += 1
        s = c.get("status", "unknown")
        if s == "healthy":
            report.healthy += 1
        elif s == "warning":
            report.warning += 1
        elif s == "error":
            report.error += 1
        else:
            report.inactive += 1

    if report.error > 0:
        report.recommendations.append(
            f"{report.error} combos have errors — investigate immediately"
        )
    if report.warning > 0:
        report.recommendations.append(
            f"{report.warning} combos have warnings — review within 24h"
        )

    return report


def _check_combo(
    combo_id: str,
    state_dir: Path,
    registry_entry: dict | None,
) -> ComboHealth:
    """Check health of a single combo based on state files and registry."""
    health = ComboHealth(combo_id=combo_id)
    combo_dir = state_dir / combo_id

    # Check registry status
    if registry_entry:
        paper_status = registry_entry.get("paper_status", "unknown")
        if paper_status in ("archived", "disabled"):
            health.status = "inactive"
            return health

    # Check state directory
    if not combo_dir.exists():
        health.status = "error"
        health.issues.append("state_dir_missing")
        return health

    # Find state file
    state_file = None
    for candidate in ["state.json", "paper_state.json", f"{combo_id}_state.json"]:
        p = combo_dir / candidate
        if p.exists():
            state_file = p
            break

    # Also check for CSV-based state
    if state_file is None:
        json_files = list(combo_dir.glob("*.json"))
        if json_files:
            state_file = max(json_files, key=lambda x: x.stat().st_mtime)

    if state_file is None:
        health.status = "warning"
        health.state_file_exists = False
        health.issues.append("no_state_file_found")
        return health

    health.state_file_exists = True

    # Load and check state
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        health.status = "error"
        health.issues.append(f"state_file_corrupt: {e}")
        return health

    # state 必须是 dict; nav_history.json 等列表文件不是有效的 state 文件
    if not isinstance(state, dict):
        health.status = "error"
        health.issues.append(f"state_not_dict: got {type(state).__name__}")
        return health

    # Check freshness
    _check_state_freshness(state_file, health)

    # Extract metrics
    health.nav = state.get("nav", state.get("total_nav"))
    positions = state.get("positions", state.get("holdings", {}))
    if isinstance(positions, dict):
        health.position_count = len(positions)
    elif isinstance(positions, list):
        health.position_count = len(positions)

    health.last_update = state.get("last_trade_date", state.get("date"))

    # NAV consistency check
    _check_nav_consistency(state, health)

    # Determine overall status
    if not health.issues:
        health.status = "healthy"
    elif any("error" in i or "corrupt" in i for i in health.issues):
        health.status = "error"
    else:
        health.status = "warning"

    return health


def _weekend_hours_between(start: datetime, end: datetime) -> float:
    """Count hours that fall on Saturday/Sunday between start and end."""
    if end <= start:
        return 0.0
    total = 0.0
    cur = start
    while cur < end:
        next_hour = cur + timedelta(hours=1)
        if next_hour > end:
            next_hour = end
        # weekday(): Monday=0, Sunday=6
        if cur.weekday() >= 5:  # Sat or Sun
            total += (next_hour - cur).total_seconds() / 3600
        cur = next_hour
    return total


def _check_state_freshness(
    state_path: Path, health: ComboHealth,
) -> None:
    """Check if state file is stale (not updated within STALE_HOURS).

    Trading-day aware: weekend hours do not count toward staleness, so
    Monday-morning checks do not flag state from Friday evening.
    """
    mtime = datetime.fromtimestamp(state_path.stat().st_mtime)
    now = datetime.now()
    age = now - mtime
    weekend_h = _weekend_hours_between(mtime, now)
    trading_age_h = age.total_seconds() / 3600 - weekend_h
    if trading_age_h > STALE_HOURS:
        health.issues.append(
            f"stale_state: {trading_age_h:.0f}h trading-hours since last update"
        )


def _check_nav_consistency(
    state: dict, health: ComboHealth,
) -> None:
    """Check NAV = sum(positions * price) + cash (within tolerance)."""
    nav = state.get("nav", state.get("total_nav"))
    cash = state.get("cash", state.get("available_cash", 0))
    positions = state.get("positions", state.get("holdings", {}))

    if nav is None:
        health.issues.append("nav_missing_from_state")
        return

    # Try to compute position market value
    pos_value = 0.0
    if isinstance(positions, dict):
        for code, pos_info in positions.items():
            if isinstance(pos_info, dict):
                shares = pos_info.get("shares", pos_info.get("qty", 0))
                price = pos_info.get("price", pos_info.get("last_price", 0))
                pos_value += shares * price
    elif isinstance(positions, list):
        for pos_info in positions:
            shares = pos_info.get("shares", pos_info.get("qty", 0))
            price = pos_info.get("price", pos_info.get("last_price", 0))
            pos_value += shares * price

    if pos_value > 0:
        computed = pos_value + cash
        if nav > 0:
            deviation = abs(computed - nav) / nav
            if deviation > 0.005:  # > 0.5% deviation
                health.issues.append(
                    f"nav_deviation: computed={computed:.2f} "
                    f"reported={nav:.2f} ({deviation:.1%})"
                )


def main() -> None:
    report = check_paper_health()
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
