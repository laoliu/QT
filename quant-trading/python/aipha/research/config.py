"""Loader for the canonical A-share research YAML configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .pipeline import ResearchConfig


def load_research_config(path: str | Path) -> ResearchConfig:
    """Load and validate the canonical nested YAML research config."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
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
    if execution.get("mode", "paper") != "paper":
        raise ValueError("research config execution.mode must be paper")
    if universe.get("exclude_st", True) is not True or universe.get("exclude_suspended", True) is not True:
        raise ValueError("canonical A-share universe must exclude ST and suspended stocks")
    if int(universe.get("min_listing_days", 252)) < 252:
        raise ValueError("min_listing_days must be at least 252")
    label_type = label.get("type", "forward_return_rank")
    if label_type != "forward_return_rank":
        raise ValueError("canonical pipeline currently requires label.type=forward_return_rank")
    configured_model = str(model.get("type", "lightgbm_ranker")).lower()
    model_aliases = {"lightgbm_ranker": "lightgbm", "lgbm": "lightgbm", "linear": "elasticnet"}
    if configured_model not in model_aliases:
        raise ValueError("canonical model.type must be lightgbm_ranker or linear")
    return ResearchConfig(
        data_provider=data["provider"],
        feature_version=features.get("version", "cn_equity_v1"),
        universe=universe.get("base_index", "CSI500").lower().replace("csi", "csi"),
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
        objective=model.get("objective", "lambdarank"),
        initial_capital=float(portfolio.get("initial_capital", 1_000_000)),
        n_estimators=int(model.get("n_estimators", 300)),
        commission_rate=float(cost.get("commission_rate", 8.54e-05)),
        commission_min=float(cost.get("commission_min", 5.0)),
        stamp_tax_rate=float(cost.get("stamp_tax_rate", 0.0005)),
        transfer_fee_rate=float(cost.get("transfer_fee_rate", 0.00001)),
        slippage_bps=float(cost.get("slippage_bps", 15.0)),
        feature_groups=tuple(features.get("groups", (
            "value", "quality", "momentum", "volatility", "liquidity", "technical",
        ))),
    )
