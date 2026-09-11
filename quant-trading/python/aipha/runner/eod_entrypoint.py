"""Unified EOD entrypoint helpers for paper/shadow/live commands.

This module keeps command files thin: argparse/logging stays in scripts, while
mode validation and orchestrator invocation live here. It is intentionally small
so paper/live command files do not grow independent execution logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)


class EodRunMode(str, Enum):
    """Supported EOD command modes."""

    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


@dataclass(frozen=True)
class EodRunResult:
    """Normalized result returned by thin paper/live command wrappers."""

    status: str
    nav: float | None = None
    fills: int | None = None
    message: str = ""


@dataclass(frozen=True)
class EodRunOptions:
    """Options shared by paper/shadow/live EOD commands."""

    config_path: Path
    mode: EodRunMode
    dry_run: bool = False
    trade_date: str | None = None
    confirm: bool = False


def parse_eod_mode(value: str | EodRunMode) -> EodRunMode:
    """Parse a string mode into ``EodRunMode``."""
    if isinstance(value, EodRunMode):
        return value
    try:
        return EodRunMode(str(value).lower())
    except ValueError as exc:
        allowed = ", ".join(m.value for m in EodRunMode)
        raise ValueError(f"unsupported EOD mode {value!r}; allowed: {allowed}") from exc


def run_eod_from_config(options: EodRunOptions) -> EodRunResult:
    """Run one EOD cycle from config using the unified command contract.

    Routes paper/shadow through ``ComboOrchestrator`` (PaperMode via
    ComboEODRunner) and live through ``ComboOrchestrator`` + ``LiveMode`` +
    ``LiveDelegate``. All three modes share the same orchestrator loop.
    """
    mode = parse_eod_mode(options.mode)
    config_path = Path(options.config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"EOD config not found: {config_path}")

    if mode in (EodRunMode.PAPER, EodRunMode.SHADOW):
        return _run_orchestrator_eod(options)

    # Live: bridge through LiveEngineEOD until ComboOrchestrator + LiveMode
    # is fully wired (§13.16 deprecation plan, target 2026 Q3).
    return _run_live_eod(options)


def _run_orchestrator_eod(options: EodRunOptions) -> EodRunResult:
    """Paper/shadow implementation via ComboOrchestrator."""
    from aipha.runner.combo_orchestrator import ComboOrchestrator

    orch = ComboOrchestrator.from_yaml(options.config_path)
    if options.dry_run:
        orch._cfg.dry_run = True
    try:
        orch.initialize()
        result = orch.run_daily(trade_date_override=options.trade_date)
        if result is None:
            return EodRunResult(status="skipped", message="non-trading or disabled")
        return EodRunResult(status="ok", nav=float(result.nav), fills=int(result.fills))
    finally:
        orch.shutdown()


def _run_live_eod(options: EodRunOptions) -> EodRunResult:
    """Live implementation — ComboOrchestrator + LiveMode + LiveDelegate.

    Replaces the deprecated LiveEngineEOD bridge (§13.16).
    Flow: load config → LiveDelegate → safety_preflight → load state →
          LiveMode → ComboOrchestrator → initialize → run_daily → shutdown.
    """
    import yaml
    from aipha.runner.combo_orchestrator import ComboOrchestrator
    from aipha.runner.execution_mode import LiveMode
    from aipha.runner.live_delegate import LiveDelegate
    from aipha.runner.daily_utils import load_live_state

    if not options.confirm and not options.dry_run:
        raise ValueError(
            "Live EOD requires --confirm or --dry-run for safety. "
            "Pass confirm=True or dry_run=True in EodRunOptions."
        )

    # 1. Load raw YAML for LiveDelegate
    config_path = Path(options.config_path)
    with open(config_path, encoding="utf-8") as f:
        raw_cfg = yaml.safe_load(f)

    profile = raw_cfg.get("profile", raw_cfg.get("profile_name", config_path.stem))
    initial_capital = float(raw_cfg.get("initial_capital", 1_000_000))

    # 2. LiveDelegate — QMT connection + safety preflight
    delegate = LiveDelegate(raw_cfg)
    if not options.dry_run:
        delegate.safety_preflight()

    # 3. Load live state (data/live_state/{profile}/state.json)
    state = load_live_state(profile, initial_capital)

    # 4. Build LiveMode
    live_mode = LiveMode(state, delegate, raw_cfg)

    # 5. ComboOrchestrator with LiveMode
    try:
        orch = ComboOrchestrator.from_yaml(
            config_path, execution_mode=live_mode,
        )
        # Override state_dir: live state lives in data/live_state (not paper_state)
        # from_yaml() re-reads the YAML file so raw_cfg mutations are ineffective.
        if not raw_cfg.get("state_dir"):
            orch._cfg.state_dir = "data/live_state"
            orch._state_dir = project_path("data", "live_state")
        if options.dry_run:
            orch._cfg.dry_run = True
        orch.initialize()
        result = orch.run_daily(trade_date_override=options.trade_date)
        if result is None:
            return EodRunResult(status="skipped", message="non-trading or disabled")
        return EodRunResult(
            status="ok", nav=float(result.nav), fills=int(result.fills),
        )
    finally:
        try:
            delegate.disconnect()
        except Exception as exc:
            logger.warning("LiveDelegate disconnect failed: %s", exc)
        try:
            orch.shutdown()  # type: ignore[possibly-undefined]
        except Exception as exc:
            logger.debug("Orchestrator shutdown cleanup: %s", exc)


__all__ = [
    "EodRunMode",
    "EodRunOptions",
    "EodRunResult",
    "parse_eod_mode",
    "run_eod_from_config",
]
