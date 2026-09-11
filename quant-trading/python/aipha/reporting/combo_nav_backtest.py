from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from aipha.utils.reproducibility import create_fingerprint, save_fingerprint

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.utils.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def _load_nav_series(spec: dict[str, Any]) -> pd.Series:
    path = _resolve_path(spec["nav_path"])
    date_col = spec.get("date_column", "date")
    nav_col = spec.get("nav_column", "nav")
    df = pd.read_csv(path, usecols=[date_col, nav_col])
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).drop_duplicates(date_col)
    return df.set_index(date_col)[nav_col].astype(float)


def compute_metrics(nav: pd.Series) -> dict[str, Any]:
    rets = nav.pct_change().dropna()
    years = len(rets) / float(TRADING_DAYS_PER_YEAR)
    total_return = nav.iloc[-1] / nav.iloc[0] - 1
    annualized_return = (nav.iloc[-1] / nav.iloc[0]) ** (1 / max(years, 0.25)) - 1
    volatility = float(rets.std()) * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (float(rets.mean()) * TRADING_DAYS_PER_YEAR - 0.02) / volatility if volatility > 0 else 0.0
    drawdown = (nav.cummax() - nav) / nav.cummax()
    max_drawdown = float(drawdown.max())
    yearly_returns = {
        str(year): round(float((1 + group).prod() - 1), 4)
        for year, group in rets.groupby(rets.index.year)
    }
    return {
        "annualized_return": round(float(annualized_return), 4),
        "volatility": round(volatility, 4),
        "sharpe_ratio": round(float(sharpe), 3),
        "max_drawdown": round(max_drawdown, 4),
        "calmar_ratio": round(float(annualized_return / max_drawdown) if max_drawdown > 0 else 0.0, 3),
        "total_return": round(float(total_return), 4),
        "n_years": round(years, 2),
        "yearly_returns": yearly_returns,
    }


def build_combo_nav(config: dict[str, Any]) -> tuple[pd.Series, dict[str, float]]:
    initial_capital = float(config["initial_capital"])
    components = config["components"]
    navs = {name: _load_nav_series(spec) for name, spec in components.items()}
    common_dates = sorted(set.intersection(*[set(series.index) for series in navs.values()]))
    if not common_dates:
        raise ValueError("组件 NAV 没有公共日期，无法合成组合")

    combo_nav = None
    weights: dict[str, float] = {}
    for name, spec in components.items():
        weight = float(spec["weight"])
        weights[name] = weight
        aligned = navs[name].loc[common_dates]
        scaled = aligned / aligned.iloc[0] * weight * initial_capital
        combo_nav = scaled if combo_nav is None else combo_nav + scaled
    return combo_nav, weights


def _write_combo_artifacts(
    config_file: Path,
    config: dict[str, Any],
    summary: dict[str, Any],
    output_dir: Path,
) -> None:
    with open(output_dir / "config.yaml", "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)

    fingerprint = create_fingerprint(
        config=config,
        config_path=config_file,
        random_seed=0,
        set_seed=False,
    )
    save_fingerprint(fingerprint, output_dir / "fingerprint.json")

    audit_dir = output_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_payload = {
        "event": "combo_nav_build",
        "combo_name": config["combo_name"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "component_count": len(config.get("components", {})),
        "components": {
            name: spec.get("nav_path") for name, spec in (config.get("components") or {}).items()
        },
        "output_dir": str(output_dir),
        "summary_metrics": summary.get("metrics", {}),
    }
    with open(audit_dir / "combo_nav_build.json", "w", encoding="utf-8") as handle:
        json.dump(audit_payload, handle, ensure_ascii=False, indent=2)


def run_combo_config(config_path: str | Path) -> dict[str, Any]:
    config_file = Path(config_path)
    with open(config_file, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    combo_nav, weights = build_combo_nav(config)
    summary: dict[str, Any] = {
        "combo_name": config["combo_name"],
        "description": config.get("description", ""),
        "initial_capital": float(config["initial_capital"]),
        "weights": weights,
        "metrics": compute_metrics(combo_nav),
    }

    for segment_name, start_date in (config.get("segments") or {}).items():
        segment_nav = combo_nav[combo_nav.index >= pd.Timestamp(start_date)]
        if len(segment_nav) >= 2:
            summary[segment_name] = compute_metrics(segment_nav)

    output_dir = _resolve_path(config.get("output_dir", f"output/{config['combo_name']}"))
    output_dir.mkdir(parents=True, exist_ok=True)
    nav_df = pd.DataFrame({"date": combo_nav.index.strftime("%Y-%m-%d"), "nav": combo_nav.values})
    nav_df.to_csv(output_dir / "daily_nav.csv", index=False)
    with open(output_dir / "summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    _write_combo_artifacts(config_file, config, summary, output_dir)
    return summary