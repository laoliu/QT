"""Loader for the canonical A-share research YAML configuration."""
from __future__ import annotations

from pathlib import Path
import os
import tempfile
from typing import Any

import yaml

from .pipeline import ResearchConfig


_LEGACY_MODEL_TYPES = {
    "lgbm": "lightgbm_ranker",
    "lightgbm": "lightgbm_ranker",
    "lightgbm_ranker": "lightgbm_ranker",
    "elasticnet": "linear",
    "linear": "linear",
}


def migrate_legacy_ml_config(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Convert the old ``sample_ml_factor`` shape to canonical YAML data.

    The old file mixed a generic backtest (often AKShare) with an ML model.
    It is therefore never loaded directly by the canonical pipeline.  The
    converter emits a safe TuShare-PIT configuration and records warnings for
    values that cannot be carried across the research contract.
    """
    if not isinstance(raw, dict) or not any(key in raw for key in ("ml_model", "factors", "strategy")):
        raise ValueError("input does not look like the legacy ML factor config")
    warnings: list[str] = []
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    universe = raw.get("universe") if isinstance(raw.get("universe"), dict) else {}
    model = raw.get("ml_model") if isinstance(raw.get("ml_model"), dict) else {}
    strategy = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
    strategy_params = strategy.get("params") if isinstance(strategy.get("params"), dict) else {}
    old_source = str(data.get("source", "akshare")).lower()
    if old_source not in {"tushare", "tushare_pit"}:
        warnings.append(
            f"legacy data.source={old_source!r} cannot be used for PIT backtests; "
            "the generated config requires a TuShare PIT snapshot"
        )
    old_model = str(model.get("type", "lgbm")).lower()
    canonical_model = _LEGACY_MODEL_TYPES.get(old_model)
    if canonical_model is None:
        raise ValueError(
            f"legacy ml_model.type={old_model!r} has no canonical migration; "
            "only lgbm/lightgbm/elasticnet/linear are supported"
        )
    factors = raw.get("factors") if isinstance(raw.get("factors"), list) else []
    groups = {"value", "quality", "momentum", "volatility", "liquidity", "technical"}
    for factor in factors:
        if isinstance(factor, dict):
            name = str(factor.get("name", "")).lower()
            if name in {"pe", "pb", "value", "ep"}:
                groups.add("value")
            elif name in {"roe", "roa", "quality"}:
                groups.add("quality")
            elif name in {"momentum", "reversal"}:
                groups.add("momentum")
            elif name in {"turnover_rate", "liquidity", "amount"}:
                groups.add("liquidity")
    top_k = int(strategy_params.get("top_n", model.get("top_n", 30)))
    canonical = {
        "data": {
            "provider": "tushare_pit",
            "snapshot_dir": "data/snapshots",
            "frequency": "1d",
            "adjust": "point_in_time",
        },
        "universe": {
            "base_index": str(universe.get("preset", "csi500")).upper().replace("ZZ500", "CSI500"),
            "exclude_st": True,
            "exclude_suspended": True,
            "min_listing_days": max(252, int(universe.get("exclude_new", 252))),
        },
        "features": {
            "version": "cn_equity_v1",
            "groups": sorted(groups),
        },
        "label": {
            "type": "forward_return_rank",
            "horizon_days": int(raw.get("label", {}).get("horizon_days", 20))
            if isinstance(raw.get("label"), dict) else 20,
            "skip_days": 1,
            "price_field": "close",
        },
        "model": {
            "type": canonical_model,
            "objective": "lambdarank" if canonical_model == "lightgbm_ranker" else "regression",
            "train_years": int(model.get("train_years", 3)),
            "test_months": int(model.get("predict_months", 1)),
            "purge_days": 20,
            "n_estimators": int(model.get("n_estimators", 300)),
        },
        "portfolio": {
            "rebalance": "monthly",
            "top_k": max(1, top_k),
            "max_position_weight": float(raw.get("risk", {}).get("max_position_pct", 0.05))
            if isinstance(raw.get("risk"), dict) else 0.05,
            "max_industry_weight": float(raw.get("risk", {}).get("max_sector_pct", 0.20))
            if isinstance(raw.get("risk"), dict) else 0.20,
            "max_daily_turnover": float(raw.get("risk", {}).get("max_daily_turnover", 0.30))
            if isinstance(raw.get("risk"), dict) else 0.30,
            "initial_capital": float(raw.get("initial_capital", 1_000_000)),
        },
        "cost": {
            "commission_rate": float(raw.get("cost", {}).get("commission", 8.54e-05))
            if isinstance(raw.get("cost"), dict) else 8.54e-05,
            "commission_min": 5.0,
            "stamp_tax_rate": float(raw.get("cost", {}).get("stamp_tax", 0.0005))
            if isinstance(raw.get("cost"), dict) else 0.0005,
            "transfer_fee_rate": 0.00001,
            "slippage_bps": float(raw.get("cost", {}).get("slippage_bps", 15.0))
            if isinstance(raw.get("cost"), dict) else 15.0,
        },
        "execution": {"mode": "paper"},
    }
    warnings.append("legacy strategy/model parameters not represented in the canonical contract were dropped")
    return canonical, warnings


def migrate_legacy_ml_config_file(
    source: str | Path,
    destination: str | Path,
) -> tuple[Path, list[str]]:
    """Validate and publish a new config; never replace any existing file."""
    source_path = Path(source)
    destination_path = Path(destination)
    if destination_path.exists() or destination_path.is_symlink():
        raise FileExistsError(f"migration destination already exists: {destination_path}")
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    canonical, warnings = migrate_legacy_ml_config(raw)
    research_config_from_mapping(canonical)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination_path.parent,
            prefix=".research-config-", suffix=".yaml", delete=False,
        ) as staged:
            staged_path = Path(staged.name)
            staged.write(yaml.safe_dump(canonical, allow_unicode=True, sort_keys=False))
            staged.flush()
            os.fsync(staged.fileno())
        # Atomic, exclusive publication also protects against a concurrent writer.
        os.link(staged_path, destination_path)
    finally:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
    return destination_path, warnings


def load_research_config(path: str | Path) -> ResearchConfig:
    """Load and validate the canonical nested YAML research config."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return research_config_from_mapping(raw)


def research_config_from_mapping(raw: dict[str, Any]) -> ResearchConfig:
    """Apply the same configuration contract to in-memory migration output."""
    if not isinstance(raw, dict):
        raise ValueError("research config root must be a mapping")
    known = {"data", "universe", "features", "label", "model", "portfolio", "cost", "execution"}
    extra = set(raw) - known
    if extra:
        raise ValueError(f"unknown research config sections: {', '.join(sorted(extra))}")
    sections = {}
    for key in known:
        value = raw.get(key, {})
        if not isinstance(value, dict):
            raise ValueError(f"research config section {key!r} must be a mapping")
        sections[key] = value
    data = sections["data"]
    universe = sections["universe"]
    features = sections["features"]
    label = sections["label"]
    model = sections["model"]
    portfolio = sections["portfolio"]
    cost = sections["cost"]
    execution = sections["execution"]
    if data.get("provider", "tushare_pit") != "tushare_pit":
        raise ValueError("canonical historical research requires data.provider=tushare_pit")
    if data.get("adjust", "point_in_time") != "point_in_time":
        raise ValueError("canonical historical research requires data.adjust=point_in_time")
    if execution.get("mode", "paper") != "paper":
        raise ValueError("research config execution.mode must be paper")
    if universe.get("exclude_st", True) is not True or universe.get("exclude_suspended", True) is not True:
        raise ValueError("canonical A-share universe must exclude ST and suspended stocks")
    from aipha.universe.historical import validate_listing_threshold

    min_listing_days = validate_listing_threshold(universe.get("min_listing_days", 252))
    label_type = label.get("type", "forward_return_rank")
    if label_type != "forward_return_rank":
        raise ValueError("canonical pipeline currently requires label.type=forward_return_rank")
    configured_model = str(model.get("type", "lightgbm_ranker")).lower()
    model_aliases = {"lightgbm_ranker": "lightgbm", "lgbm": "lightgbm", "linear": "elasticnet"}
    if configured_model not in model_aliases:
        raise ValueError("canonical model.type must be lightgbm_ranker or linear")
    return ResearchConfig(
        data_provider=data.get("provider", "tushare_pit"),
        adjust=data.get("adjust", "point_in_time"),
        feature_version=features.get("version", "cn_equity_v1"),
        universe=universe.get("base_index", "CSI500"),
        min_listing_days=min_listing_days,
        rebalance=portfolio.get("rebalance", "monthly"),
        train_years=int(model.get("train_years", 3)),
        test_months=int(model.get("test_months", 1)),
        forward_days=int(label.get("horizon_days", 20)),
        label_skip_days=int(label.get("skip_days", 1)),
        purge_days=int(model.get("purge_days", 20)),
        top_k=int(portfolio.get("top_k", 30)),
        max_position_weight=float(portfolio.get("max_position_weight", 0.05)),
        max_industry_weight=(
            None if portfolio.get("max_industry_weight", 0.20) is None
            else float(portfolio.get("max_industry_weight", 0.20))
        ),
        max_daily_turnover=float(portfolio.get("max_daily_turnover", 0.30)),
        model_type=model_aliases[configured_model],
        objective=model.get("objective", "regression" if configured_model == "linear" else "lambdarank"),
        initial_capital=float(portfolio.get("initial_capital", 1_000_000)),
        n_estimators=int(model.get("n_estimators", 300)),
        commission_rate=float(cost.get("commission_rate", 8.54e-05)),
        commission_min=float(cost.get("commission_min", 5.0)),
        stamp_tax_rate=float(cost.get("stamp_tax_rate", 0.0005)),
        transfer_fee_rate=float(cost.get("transfer_fee_rate", 0.00001)),
        slippage_bps=float(cost.get("slippage_bps", 15.0)),
        feature_groups=features.get("groups", (
            "value", "quality", "momentum", "volatility", "liquidity", "technical",
        )),
        feature_column_groups=features.get("column_groups", {}),
    )
