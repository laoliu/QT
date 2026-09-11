"""Portfolio lifecycle state machine for paper/live combo governance.

Defines portfolio lifecycle states, transition rules, and scheduling policies.
Used by paper_daily_runner.py to determine which combos to run and at what frequency.

References:
- §13.13 生产级运行可靠性
- Phase C 组合治理设计 (2026-05-06)
"""
from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROJ_ROOT = PROJECT_ROOT
_SYSTEM_SETTINGS = _PROJ_ROOT / "configs" / "paper" / "system_settings.json"


class LifecycleStatus(str, enum.Enum):
    """Portfolio lifecycle states."""

    PRIMARY = "primary"          # 主线组合，必跑，最高优先级
    PARALLEL = "parallel"        # 平行主线，必跑
    CANARY = "canary"            # 小规模验证，必跑
    CANDIDATE = "candidate"      # 候选池，shadow 模式自动运行
    OBSERVATION = "observation"  # 观察基线，降频运行
    SUSPENDED = "suspended"      # 暂停，不跑但保留状态
    ARCHIVED = "archived"        # 归档，不跑不保留

    @property
    def should_run_daily(self) -> bool:
        """Whether this status requires daily execution."""
        return self in (
            LifecycleStatus.PRIMARY,
            LifecycleStatus.PARALLEL,
            LifecycleStatus.CANARY,
            LifecycleStatus.CANDIDATE,
        )

    @property
    def is_active(self) -> bool:
        """Whether this status is considered active (not suspended/archived)."""
        return self not in (LifecycleStatus.SUSPENDED, LifecycleStatus.ARCHIVED)


class ScheduleFrequency(str, enum.Enum):
    """Execution frequency for portfolios."""

    DAILY = "daily"
    WEEKLY = "weekly"      # every 5 trading days
    BIWEEKLY = "biweekly"  # every 10 trading days
    MANUAL = "manual"      # only on explicit trigger


@dataclass
class PortfolioLifecycleEntry:
    """Lifecycle metadata for a single portfolio."""

    portfolio_id: str
    status: LifecycleStatus
    tier: str = "S"  # "S" or "L"
    schedule: ScheduleFrequency = ScheduleFrequency.DAILY
    promoted_date: Optional[str] = None      # YYYY-MM-DD
    last_reviewed: Optional[str] = None      # YYYY-MM-DD
    next_review: Optional[str] = None        # YYYY-MM-DD
    grace_period_end: Optional[str] = None   # YYYY-MM-DD (for demotions)
    review_reason: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        d = asdict(self)
        d["status"] = self.status.value
        d["schedule"] = self.schedule.value
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "PortfolioLifecycleEntry":
        """Deserialize from dict."""
        data = dict(data)
        data["status"] = LifecycleStatus(data.get("status", "observation"))
        data["schedule"] = ScheduleFrequency(data.get("schedule", "daily"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ── Default lifecycle assignments (from R171 promotion decision) ──
_DEFAULT_LIFECYCLE: dict[str, dict] = {
    "v8b_combo_1000w": {"status": "primary", "tier": "S", "notes": "R171 PRIMARY"},
    "v5a": {"status": "parallel", "tier": "S", "notes": "R171 PARALLEL"},
    "v7c_conservative_100w": {"status": "canary", "tier": "S", "notes": "R171 CANARY 100w"},
    "tiered_combo_v3": {"status": "candidate", "tier": "S", "notes": "R169 DEC-3 candidate"},
    "r115": {"status": "observation", "tier": "S", "schedule": "weekly"},
    "r116": {"status": "observation", "tier": "S", "schedule": "weekly"},
    "v2": {"status": "observation", "tier": "S", "schedule": "weekly"},
    "e1_shadow": {"status": "observation", "tier": "S", "schedule": "weekly"},
    "v6a_combo": {"status": "observation", "tier": "S", "schedule": "weekly"},
    "cb_v4e_eq": {"status": "observation", "tier": "S", "notes": "CB standalone monitor"},
    "plan_a_5comp_eod": {"status": "observation", "tier": "S", "schedule": "weekly"},
    "cb_v4c_a": {"status": "suspended", "tier": "S", "notes": "RE-EVAL P22<13%"},
}


def load_lifecycle_registry() -> dict[str, PortfolioLifecycleEntry]:
    """Load portfolio lifecycle registry from system_settings.json.

    Falls back to _DEFAULT_LIFECYCLE if 'portfolio_lifecycle' key is absent.
    """
    lifecycle_data = _DEFAULT_LIFECYCLE.copy()

    if _SYSTEM_SETTINGS.exists():
        try:
            with open(_SYSTEM_SETTINGS, encoding="utf-8") as f:
                settings = json.load(f)
            stored = settings.get("portfolio_lifecycle", {})
            if stored:
                lifecycle_data.update(stored)
        except Exception:
            logger.warning("Failed to load system_settings.json, using defaults", exc_info=True)

    registry: dict[str, PortfolioLifecycleEntry] = {}
    for pid, entry_data in lifecycle_data.items():
        if pid.startswith("_"):
            continue
        try:
            entry_data_copy = dict(entry_data)
            entry_data_copy["portfolio_id"] = pid
            registry[pid] = PortfolioLifecycleEntry.from_dict(entry_data_copy)
        except Exception as exc:
            logger.warning("Skipping invalid lifecycle entry %s: %s", pid, exc)
    return registry

def get_daily_run_list(
    registry: dict[str, PortfolioLifecycleEntry],
    *,
    trading_day_index: int = 0,
) -> list[str]:
    """Determine which portfolios should run today based on lifecycle status and schedule.

    Args:
        registry: Loaded lifecycle registry.
        trading_day_index: 0-based trading day count within current period (for weekly/biweekly).

    Returns:
        List of portfolio_ids that should execute today.
    """
    run_list: list[str] = []
    for pid, entry in registry.items():
        if not entry.status.is_active:
            continue
        if entry.schedule == ScheduleFrequency.MANUAL:
            continue
        if entry.schedule == ScheduleFrequency.DAILY:
            if entry.status.should_run_daily:
                run_list.append(pid)
        elif entry.schedule == ScheduleFrequency.WEEKLY:
            if trading_day_index % 5 == 0:
                run_list.append(pid)
        elif entry.schedule == ScheduleFrequency.BIWEEKLY:
            if trading_day_index % 10 == 0:
                run_list.append(pid)
    return run_list

def transition_status(
    portfolio_id: str,
    new_status: LifecycleStatus,
    *,
    reason: str,
    reviewer: str = "system",
    grace_days: int = 0,
) -> PortfolioLifecycleEntry:
    """Transition a portfolio to a new lifecycle status with audit trail.

    Args:
        portfolio_id: The portfolio to transition.
        new_status: Target lifecycle status.
        reason: Human-readable reason for the transition.
        reviewer: Who initiated the transition.
        grace_days: Days of grace period before full enforcement.

    Returns:
        Updated PortfolioLifecycleEntry.

    Raises:
        ValueError: If transition is invalid.
    """
    registry = load_lifecycle_registry()
    entry = registry.get(portfolio_id)
    if entry is None:
        entry = PortfolioLifecycleEntry(portfolio_id=portfolio_id, status=new_status)

    # Validate transition: archived cannot be reactivated without explicit override
    if entry.status == LifecycleStatus.ARCHIVED and new_status != LifecycleStatus.ARCHIVED:
        raise ValueError(
            f"Cannot transition {portfolio_id} from ARCHIVED to {new_status.value}. "
            "Archived portfolios require manual re-creation."
        )

    today_str = date.today().isoformat()
    entry.status = new_status
    entry.last_reviewed = today_str
    entry.review_reason = f"[{reviewer}] {reason}"

    if grace_days > 0:
        from datetime import timedelta
        grace_end = date.today() + timedelta(days=grace_days)
        entry.grace_period_end = grace_end.isoformat()
    else:
        entry.grace_period_end = None

    # Update schedule based on new status
    if new_status in (LifecycleStatus.OBSERVATION,):
        entry.schedule = ScheduleFrequency.WEEKLY
    elif new_status in (LifecycleStatus.SUSPENDED, LifecycleStatus.ARCHIVED):
        entry.schedule = ScheduleFrequency.MANUAL
    elif new_status.should_run_daily:
        entry.schedule = ScheduleFrequency.DAILY

    # Persist
    registry[portfolio_id] = entry
    save_lifecycle_registry(registry)

    # Audit log
    _write_audit_log(portfolio_id, new_status, reason, reviewer)
    logger.info(
        "Lifecycle transition: %s → %s (reason: %s, reviewer: %s)",
        portfolio_id, new_status.value, reason, reviewer,
    )
    return entry


def _write_audit_log(
    portfolio_id: str,
    new_status: LifecycleStatus,
    reason: str,
    reviewer: str,
) -> None:
    """Append lifecycle transition to audit log."""
    audit_dir = _PROJ_ROOT / "logs" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_file = audit_dir / "lifecycle_transitions.jsonl"
    record = {
        "ts": datetime.now().isoformat(),
        "portfolio_id": portfolio_id,
        "new_status": new_status.value,
        "reason": reason,
        "reviewer": reviewer,
    }
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("Failed to write lifecycle audit log", exc_info=True)

def save_lifecycle_registry(registry: dict[str, PortfolioLifecycleEntry]) -> None:
    """Persist lifecycle registry back to system_settings.json."""
    if not _SYSTEM_SETTINGS.exists():
        logger.error("system_settings.json not found, cannot save lifecycle registry")
        return

    try:
        with open(_SYSTEM_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        logger.error("Failed to read system_settings.json for lifecycle save", exc_info=True)
        return

    lifecycle_dict: dict[str, dict] = {}
    for pid, entry in registry.items():
        lifecycle_dict[pid] = entry.to_dict()
        lifecycle_dict[pid].pop("portfolio_id", None)

    settings["portfolio_lifecycle"] = lifecycle_dict
    settings["updated_at"] = datetime.now().isoformat(timespec="seconds")

    # Atomic write: tmp → rename
    tmp_path = _SYSTEM_SETTINGS.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp_path.replace(_SYSTEM_SETTINGS)
    except Exception:
        logger.error("Failed to save lifecycle registry", exc_info=True)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
