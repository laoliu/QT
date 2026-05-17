from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from aipha.reporting.combo_nav_backtest import run_combo_config


def _build_component_nav_config(raw: dict[str, Any], config_path: Path) -> dict[str, Any]:
    paper_engine = raw.get("paper_engine", {})
    profile_name = raw.get("profile_name", config_path.stem)
    initial_capital = float(
        raw.get("initial_capital", paper_engine.get("initial_capital", 1_000_000.0))
    )
    weights = paper_engine.get("portfolio_weights", raw.get("portfolio_weights", {}))
    pillars = raw.get("pillars", {})
    if not weights:
        raise ValueError("component_nav replay 缺少 portfolio_weights")
    if not pillars:
        raise ValueError("component_nav replay 缺少 pillars")

    components: dict[str, dict[str, Any]] = {}
    for name, weight in weights.items():
        pillar = dict(pillars.get(name, {}))
        nav_path = pillar.get("nav_path")
        if not nav_path:
            raise ValueError(f"component_nav replay 缺少 nav_path: {name}")
        components[name] = {
            "weight": float(weight),
            "nav_path": nav_path,
            "date_column": pillar.get("date_column", "date"),
            "nav_column": pillar.get("nav_column", "nav"),
        }

    output_dir = paper_engine.get(
        "replay_output_dir", raw.get("output_dir", f"output/{profile_name}_replay")
    )
    return {
        "combo_name": profile_name,
        "description": raw.get("description", ""),
        "initial_capital": initial_capital,
        "components": components,
        "segments": raw.get("replay_segments", {}),
        "output_dir": output_dir,
    }


def run_paper_replay(config_path: str | Path, *, days: int = 60) -> dict[str, Any]:
    """Run replay for component_nav paper configs.

    Notes:
    - Only the current component_nav replay mode is supported.
    - `days` is accepted for CLI compatibility but ignored because replay is
      built from full component NAV history.
    """
    del days

    path = Path(config_path)
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    replay_mode = (raw.get("paper_engine") or {}).get("replay_mode")
    if replay_mode != "component_nav":
        raise ValueError(
            "仅支持 component_nav replay 配置；旧 paper_replay 引擎已删除，"
            "实时/旧模式请走 orchestrator 路径。"
        )

    combo_cfg = _build_component_nav_config(raw, path)
    with tempfile.TemporaryDirectory(prefix="paper_replay_") as tmp_dir:
        temp_cfg = Path(tmp_dir) / "combo_nav_replay.yml"
        with open(temp_cfg, "w", encoding="utf-8") as handle:
            yaml.safe_dump(combo_cfg, handle, allow_unicode=True, sort_keys=False)
        summary = run_combo_config(temp_cfg)
    return {
        "success": True,
        "mode": "component_nav",
        "combo_name": combo_cfg["combo_name"],
        "output_dir": combo_cfg["output_dir"],
        "summary": summary,
    }