"""Consecutive failure counter with auto-freeze for paper/live combos.

Tracks consecutive run failures per portfolio and triggers automatic FREEZE
when the threshold is breached. Implements §13.13 #6 (state backup/recovery)
and §14.2 (MDD three-level response).

References:
- §13.13 生产级运行可靠性
- §14.1 前交易防护铁律
- Phase A 生产可靠性 P0 设计 (2026-05-06)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJ_ROOT = Path(__file__).resolve().parents[3]

# Default thresholds
DEFAULT_FREEZE_THRESHOLD = 5      # consecutive failures before auto-freeze
DEFAULT_WARNING_THRESHOLD = 3     # consecutive failures before WARNING alert


@dataclass
class FailureState:
    """Tracks consecutive failure state for a single portfolio."""

    portfolio_id: str
    consecutive_failures: int = 0
    last_failure_time: Optional[str] = None
    last_failure_reason: Optional[str] = None
    last_success_time: Optional[str] = None
    total_failures_today: int = 0
    frozen_by_counter: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "FailureState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class FailureCounter:
    """Manages consecutive failure tracking and auto-freeze for portfolios.

    Usage:
        counter = FailureCounter(state_dir=Path("data/paper_state"))
        counter.record_success("v8b_combo_1000w")
        counter.record_failure("v8b_combo_1000w", reason="price fetch timeout")
        # Auto-freeze triggers at threshold
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        freeze_threshold: int = DEFAULT_FREEZE_THRESHOLD,
        warning_threshold: int = DEFAULT_WARNING_THRESHOLD,
    ):
        self._state_dir = state_dir or (_PROJ_ROOT / "data" / "paper_state")
        self._freeze_threshold = freeze_threshold
        self._warning_threshold = warning_threshold
        self._states: dict[str, FailureState] = {}

    def _state_file(self, portfolio_id: str) -> Path:
        return self._state_dir / portfolio_id / "failure_counter.json"

    def _load_state(self, portfolio_id: str) -> FailureState:
        if portfolio_id in self._states:
            return self._states[portfolio_id]
        state_file = self._state_file(portfolio_id)
        if state_file.exists():
            try:
                with open(state_file, encoding="utf-8") as f:
                    data = json.load(f)
                state = FailureState.from_dict(data)
            except Exception:
                logger.warning("Corrupt failure_counter.json for %s, resetting", portfolio_id)
                state = FailureState(portfolio_id=portfolio_id)
        else:
            state = FailureState(portfolio_id=portfolio_id)
        self._states[portfolio_id] = state
        return state

    def _save_state(self, state: FailureState) -> None:
        state_file = self._state_file(state.portfolio_id)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_file.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
            tmp_path.replace(state_file)
        except Exception:
            logger.error("Failed to save failure counter for %s", state.portfolio_id, exc_info=True)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def record_success(self, portfolio_id: str) -> None:
        """Record a successful run, resetting the consecutive failure counter."""
        state = self._load_state(portfolio_id)
        state.consecutive_failures = 0
        state.last_success_time = datetime.now().isoformat(timespec="seconds")
        state.frozen_by_counter = False
        self._save_state(state)

    def record_failure(self, portfolio_id: str, *, reason: str = "") -> bool:
        """Record a failure and check if auto-freeze should trigger.

        Returns:
            True if auto-freeze was triggered, False otherwise.
        """
        state = self._load_state(portfolio_id)
        state.consecutive_failures += 1
        state.total_failures_today += 1
        state.last_failure_time = datetime.now().isoformat(timespec="seconds")
        state.last_failure_reason = reason

        triggered_freeze = False

        if state.consecutive_failures >= self._freeze_threshold:
            triggered_freeze = self._trigger_auto_freeze(state, reason)
        elif state.consecutive_failures >= self._warning_threshold:
            self._send_warning(state)

        self._save_state(state)
        return triggered_freeze

    def get_consecutive_failures(self, portfolio_id: str) -> int:
        """Get current consecutive failure count."""
        state = self._load_state(portfolio_id)
        return state.consecutive_failures

    def is_failure_frozen(self, portfolio_id: str) -> bool:
        """Check if portfolio was frozen by failure counter."""
        state = self._load_state(portfolio_id)
        return state.frozen_by_counter

    def reset(self, portfolio_id: str) -> None:
        """Manually reset failure counter (e.g., after investigation)."""
        state = self._load_state(portfolio_id)
        state.consecutive_failures = 0
        state.total_failures_today = 0
        state.frozen_by_counter = False
        state.last_failure_reason = None
        self._save_state(state)
        logger.info("Failure counter reset for %s", portfolio_id)

    def _trigger_auto_freeze(self, state: FailureState, reason: str) -> bool:
        """Trigger FREEZE via emergency_stop and alert."""
        if state.frozen_by_counter:
            return False  # Already frozen, idempotent

        state.frozen_by_counter = True
        freeze_reason = (
            f"Auto-freeze: {state.consecutive_failures} consecutive failures. "
            f"Last: {reason}"
        )

        # Write FREEZE marker to state_dir
        freeze_dir = self._state_dir / state.portfolio_id
        freeze_dir.mkdir(parents=True, exist_ok=True)
        freeze_file = freeze_dir / "FREEZE"
        freeze_data = {
            "frozen_at": datetime.now().isoformat(),
            "combo_id": state.portfolio_id,
            "reason": freeze_reason,
            "operator": "failure_counter",
        }
        try:
            tmp = freeze_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(freeze_data, f, indent=2, ensure_ascii=False)
            tmp.replace(freeze_file)
        except Exception:
            logger.error("Failed to write FREEZE file for %s", state.portfolio_id, exc_info=True)
            return False

        # Alert
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                f"[CRITICAL] 自动冻结: {state.portfolio_id}",
                (
                    f"连续失败 {state.consecutive_failures} 次 (阈值={self._freeze_threshold})\n"
                    f"最后失败: {reason}\n"
                    f"时间: {state.last_failure_time}\n\n"
                    f"需人工排查后执行: emergency_stop.py --unfreeze {state.portfolio_id}"
                ),
                level="CRITICAL",
            )
        except Exception:
            logger.error("Auto-freeze alert failed for %s", state.portfolio_id, exc_info=True)

        logger.critical(
            "AUTO-FREEZE triggered for %s: %d consecutive failures",
            state.portfolio_id, state.consecutive_failures,
        )
        return True

    def _send_warning(self, state: FailureState) -> None:
        """Send warning alert for approaching freeze threshold."""
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                f"[WARNING] 连续失败: {state.portfolio_id}",
                (
                    f"连续失败 {state.consecutive_failures} 次 "
                    f"(冻结阈值={self._freeze_threshold})\n"
                    f"最后失败: {state.last_failure_reason}\n"
                    f"请关注并排查原因"
                ),
                level="WARNING",
            )
        except Exception:
            logger.debug("Warning alert failed", exc_info=True)
