"""Research direction lifecycle management.

Manages the state machine for research directions:
    active → sandbox → stopped (terminal)
    active → stopped (terminal)
    sandbox → active (graduation via admission checklist)

Backed by YAML registries:
    - configs/governance/stop_research_registry.yml
    - configs/governance/research_sandbox_registry.yml

Usage:
    from aipha.governance.research_lifecycle import ResearchLifecycleManager
    mgr = ResearchLifecycleManager.load()
    mgr.is_stopped("CB 双低/质量参数变体簇")  # True
    mgr.is_sandbox("RD-Agent 自动化因子生成")  # True
    mgr.get_active_sandbox_count()  # 4
    report = mgr.status_report()

References:
    - Plan Phase 1.1 / Phase 1.3 / Phase 4
    - Plan Phase 6 Kill criteria
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

ROOT = PROJECT_ROOT
STOP_REGISTRY = ROOT / "configs" / "governance" / "stop_research_registry.yml"
SANDBOX_REGISTRY = ROOT / "configs" / "governance" / "research_sandbox_registry.yml"


class DirectionState(Enum):
    """Research direction lifecycle state."""
    ACTIVE = "active"
    SANDBOX = "sandbox"
    STOPPED = "stopped"


@dataclass
class StoppedDirection:
    """A research direction that has been terminated."""
    direction_id: str
    name: str
    reason: str
    stopped_date: str
    cf_refs: list[str] = field(default_factory=list)
    dr_refs: list[str] = field(default_factory=list)
    evidence: str = ""


@dataclass
class SandboxDirection:
    """A research direction isolated in sandbox."""
    direction_id: str
    name: str
    purpose: str
    allowed_usage: list[str] = field(default_factory=list)
    forbidden_usage: list[str] = field(default_factory=list)
    entry_date: str = ""
    graduation_criteria: str = ""
    resource_cap: str = ""


@dataclass
class LifecycleReport:
    """Summary report of all direction states."""
    stopped_count: int = 0
    sandbox_count: int = 0
    stopped_directions: list[StoppedDirection] = field(default_factory=list)
    sandbox_directions: list[SandboxDirection] = field(default_factory=list)
    sandbox_at_capacity: bool = False
    max_sandbox: int = 5

    def summary(self) -> str:
        lines = [
            "=== Research Direction Lifecycle Report ===",
            f"Stopped: {self.stopped_count} directions",
            f"Sandbox: {self.sandbox_count}/{self.max_sandbox} "
            f"({'AT CAPACITY' if self.sandbox_at_capacity else 'ok'})",
            "",
            "--- Stopped ---",
        ]
        for d in self.stopped_directions:
            lines.append(f"  [{d.direction_id}] {d.name}")
            lines.append(f"    Reason: {d.reason}")
        lines.append("")
        lines.append("--- Sandbox ---")
        for d in self.sandbox_directions:
            lines.append(f"  [{d.direction_id}] {d.name}")
            lines.append(f"    Purpose: {d.purpose}")
            lines.append(f"    Graduation: {d.graduation_criteria}")
        return "\n".join(lines)


class ResearchLifecycleManager:
    """Manages research direction states from YAML registries."""

    def __init__(
        self,
        stopped: list[StoppedDirection],
        sandbox: list[SandboxDirection],
        sandbox_rules: dict[str, Any],
    ) -> None:
        self._stopped = {d.name.lower(): d for d in stopped}
        self._stopped_by_id = {d.direction_id: d for d in stopped}
        self._sandbox = {d.name.lower(): d for d in sandbox}
        self._sandbox_by_id = {d.direction_id: d for d in sandbox}
        self._sandbox_rules = sandbox_rules

    @classmethod
    def load(
        cls,
        stop_path: Path | str | None = None,
        sandbox_path: Path | str | None = None,
    ) -> "ResearchLifecycleManager":
        """Load from YAML registry files."""
        sp = Path(stop_path) if stop_path else STOP_REGISTRY
        sbp = Path(sandbox_path) if sandbox_path else SANDBOX_REGISTRY

        stopped: list[StoppedDirection] = []
        if sp.exists():
            with open(sp, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for item in data.get("stop_research_registry", []):
                stopped.append(StoppedDirection(
                    direction_id=item.get("direction_id", ""),
                    name=item.get("name", ""),
                    reason=item.get("reason", ""),
                    stopped_date=item.get("stopped_date", ""),
                    cf_refs=item.get("cf_refs", []),
                    dr_refs=item.get("dr_refs", []),
                    evidence=item.get("evidence", ""),
                ))

        sandbox: list[SandboxDirection] = []
        sandbox_rules: dict[str, Any] = {}
        if sbp.exists():
            with open(sbp, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for item in data.get("research_sandbox_registry", []):
                sandbox.append(SandboxDirection(
                    direction_id=item.get("direction_id", ""),
                    name=item.get("name", ""),
                    purpose=item.get("purpose", ""),
                    allowed_usage=item.get("allowed_usage", []),
                    forbidden_usage=item.get("forbidden_usage", []),
                    entry_date=item.get("entry_date", ""),
                    graduation_criteria=item.get("graduation_criteria", ""),
                    resource_cap=item.get("resource_cap", ""),
                ))
            sandbox_rules = data.get("sandbox_rules", {})

        return cls(stopped, sandbox, sandbox_rules)

    def is_stopped(self, name_or_id: str) -> bool:
        """Check if a direction is in the stop list."""
        key = name_or_id.lower()
        return key in self._stopped or name_or_id in self._stopped_by_id

    def is_sandbox(self, name_or_id: str) -> bool:
        """Check if a direction is sandbox-isolated."""
        key = name_or_id.lower()
        return key in self._sandbox or name_or_id in self._sandbox_by_id

    def get_state(self, name_or_id: str) -> DirectionState:
        """Get lifecycle state of a direction."""
        if self.is_stopped(name_or_id):
            return DirectionState.STOPPED
        if self.is_sandbox(name_or_id):
            return DirectionState.SANDBOX
        return DirectionState.ACTIVE

    def get_active_sandbox_count(self) -> int:
        """Number of active sandbox directions."""
        return len(self._sandbox)

    def sandbox_at_capacity(self) -> bool:
        """Check if sandbox is at max capacity."""
        max_dirs = self._sandbox_rules.get("max_active_directions", 5)
        return self.get_active_sandbox_count() >= max_dirs

    def get_stopped_direction(self, name_or_id: str) -> StoppedDirection | None:
        """Get details of a stopped direction."""
        key = name_or_id.lower()
        if key in self._stopped:
            return self._stopped[key]
        return self._stopped_by_id.get(name_or_id)

    def get_sandbox_direction(self, name_or_id: str) -> SandboxDirection | None:
        """Get details of a sandbox direction."""
        key = name_or_id.lower()
        if key in self._sandbox:
            return self._sandbox[key]
        return self._sandbox_by_id.get(name_or_id)

    def check_allowed_usage(self, direction_id: str, usage: str) -> bool:
        """Check if a usage is allowed for a sandbox direction."""
        d = self._sandbox_by_id.get(direction_id)
        if d is None:
            return True  # Not sandbox-managed, no restriction
        if usage in d.forbidden_usage:
            return False
        if d.allowed_usage and usage not in d.allowed_usage:
            return False
        return True

    def status_report(self) -> LifecycleReport:
        """Generate comprehensive lifecycle status report."""
        max_sb = self._sandbox_rules.get("max_active_directions", 5)
        return LifecycleReport(
            stopped_count=len(self._stopped),
            sandbox_count=len(self._sandbox),
            stopped_directions=list(self._stopped.values()),
            sandbox_directions=list(self._sandbox.values()),
            sandbox_at_capacity=self.get_active_sandbox_count() >= max_sb,
            max_sandbox=max_sb,
        )

    def validate_new_direction(self, name: str) -> tuple[bool, str]:
        """Validate if a new direction can be started (not stopped/at-capacity)."""
        if self.is_stopped(name):
            d = self.get_stopped_direction(name)
            reason = d.reason if d else "unknown"
            return False, f"Direction '{name}' is STOPPED: {reason}"
        if self.is_sandbox(name):
            return False, f"Direction '{name}' is in SANDBOX — use sandbox workflow"
        return True, "OK"
