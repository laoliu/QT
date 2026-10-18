"""Shared, side-effect-free resolution of canonical Paper deployment paths."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .config import load_research_config
from .pipeline import ResearchConfig


@dataclass(frozen=True)
class PaperProfile:
    research_path: Path
    snapshot_path: Path
    book_path: Path
    plan_path: Path | None
    policy: ResearchConfig


def _path(value: Any, root: Path, *, signal_date: date | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("profile paths must be nonempty strings")
    text = value.strip()
    if signal_date is not None:
        text = text.replace("{date}", signal_date.isoformat()).replace("{trade_date}", signal_date.strftime("%Y%m%d"))
    if "{" in text or "}" in text:
        raise ValueError("only snapshot/plan paths support {date} and {trade_date} templates")
    path = Path(text).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def resolve_paper_profile(raw: dict, *, project_root: Path, signal_date: date) -> PaperProfile:
    """Validate deployment shape and file paths; never create a book or order."""
    if not isinstance(raw, dict) or "research_paper" not in raw:
        raise ValueError("research_paper must be declared at the profile root")
    if set(raw) - {"research_paper", "profile_name", "description"}:
        raise ValueError("research_paper profile cannot mix legacy engine/strategy settings")
    profile = raw["research_paper"]
    known = {"schema_version", "research_config", "snapshot_path", "book_path", "plan_path", "plan_dir"}
    if not isinstance(profile, dict) or set(profile) - known:
        raise ValueError("invalid research_paper profile fields")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("research_paper schema_version=1 required")
    if "plan_path" in profile and "plan_dir" in profile:
        raise ValueError("declare plan_path or plan_dir, not both")
    research = _path(profile.get("research_config"), project_root)
    snapshot = _path(profile.get("snapshot_path"), project_root, signal_date=signal_date)
    book = _path(profile.get("book_path"), project_root)
    if not research.is_file() or not snapshot.is_file():
        raise FileNotFoundError(f"research config and signed snapshot must exist: {research}, {snapshot}")
    if snapshot.suffix.lower() != ".parquet" or book.suffix.lower() != ".sqlite3":
        raise ValueError("research_paper requires a Parquet snapshot and a .sqlite3 book")
    policy = load_research_config(research)
    plan = None
    if "plan_path" in profile:
        plan = _path(profile["plan_path"], project_root, signal_date=signal_date)
        if not plan.is_file():
            raise FileNotFoundError(f"explicit Paper plan not found: {plan}")
    elif "plan_dir" in profile:
        directory = _path(profile["plan_dir"], project_root)
        if not directory.is_dir():
            raise FileNotFoundError(f"Paper plan directory not found: {directory}")
        dated = directory / f"{signal_date.isoformat()}.json"
        if (dated.exists() or dated.is_symlink()) and not dated.is_file():
            raise ValueError(f"dated Paper plan is not a file: {dated}")
        if dated.is_file():
            plan = dated
    return PaperProfile(research, snapshot, book, plan, policy)
