"""Configuration parsing — YAML/JSON → BacktestRunConfig。

支持两种配置格式:
1. 标准格式: data.source / cost.commission_rate / universe.* / strategy.params
2. 扩展格式: run.* / data.provider / cost_model.* / universe_policy.* / strategy.parameters
"""
from __future__ import annotations

import copy
import logging
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from aipha.config.config_models import (
    BacktestRunConfig,
    CostConfig,
    DataConfig,
    FillModelConfig,
    PortfolioConfig,
    RiskConfig,
    StrategyConfig,
    TickBacktestConfig,
    UniverseConfig,
)

logger = logging.getLogger(__name__)


def load_config(path: str | Path) -> BacktestRunConfig:
    """从 YAML/JSON 文件加载配置。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(f)
        else:
            import json
            raw = json.load(f)
    return parse_config(raw)


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    """将多种 YAML 格式归一化为标准 schema。"""
    out = copy.deepcopy(raw)

    # --- run.* → data.start_date/end_date + random_seed ---
    run_raw = out.pop("run", None)
    if run_raw and isinstance(run_raw, dict):
        data_sec = out.setdefault("data", {})
        if "start_date" not in data_sec and "start_date" in run_raw:
            data_sec["start_date"] = run_raw["start_date"]
        if "end_date" not in data_sec and "end_date" in run_raw:
            data_sec["end_date"] = run_raw["end_date"]
        if "random_seed" not in out and "seed" in run_raw:
            out["random_seed"] = run_raw["seed"]

    # --- data.provider → data.source ---
    data_sec = out.get("data", {})
    if isinstance(data_sec, dict) and "source" not in data_sec and "provider" in data_sec:
        data_sec["source"] = data_sec.pop("provider")

    # --- universe_policy.* → universe.* ---
    up_raw = out.pop("universe_policy", None)
    if up_raw and isinstance(up_raw, dict) and "universe" not in out:
        uni: dict[str, Any] = {}
        if "name" in up_raw:
            uni["policy"] = up_raw["name"]
        auto_exc = up_raw.get("auto_exclude", [])
        if isinstance(auto_exc, list):
            uni["exclude_st"] = any(s.upper() in ("ST", "*ST") for s in auto_exc)
            uni["exclude_suspended"] = any(s.upper() in ("SUSPENDED",) for s in auto_exc)
        if "base_index" in up_raw:
            uni["base_index"] = up_raw["base_index"]
        out["universe"] = uni

    # --- cost_model.* → cost.* ---
    cm_raw = out.pop("cost_model", None)
    if cm_raw and isinstance(cm_raw, dict) and "cost" not in out:
        cost_sec: dict[str, Any] = {}
        comm = cm_raw.get("commission", {})
        if isinstance(comm, dict):
            if "rate" in comm:
                cost_sec["commission_rate"] = comm["rate"]
            if "min_fee" in comm:
                cost_sec["commission_min"] = comm["min_fee"]
        stamp = cm_raw.get("stamp_tax", {})
        if isinstance(stamp, dict) and "sell_rate" in stamp:
            cost_sec["stamp_tax_rate"] = stamp["sell_rate"]
        transfer = cm_raw.get("transfer_fee", {})
        if isinstance(transfer, dict) and "rate" in transfer:
            cost_sec["transfer_fee_rate"] = transfer["rate"]
        out["cost"] = cost_sec

    # --- slippage_model → cost.slippage_* ---
    slip_raw = out.pop("slippage_model", None) or out.pop("slippage", None)
    if slip_raw and isinstance(slip_raw, dict):
        cost_sec = out.setdefault("cost", {})
        if "slippage_bps" not in cost_sec:
            cost_sec["slippage_bps"] = slip_raw.get("bps", slip_raw.get("base_bps", 0.0))
        if "slippage_model" not in cost_sec:
            cost_sec["slippage_model"] = slip_raw.get("name", slip_raw.get("model", "fixed"))
        _reserved = {"name", "model", "bps"}
        extra_params = {k: v for k, v in slip_raw.items() if k not in _reserved}
        if extra_params and "slippage_params" not in cost_sec:
            cost_sec["slippage_params"] = extra_params

    # --- cost.min_commission → cost.commission_min ---
    cost_sec = out.get("cost", {})
    if isinstance(cost_sec, dict):
        if "commission_min" not in cost_sec and "min_commission" in cost_sec:
            cost_sec["commission_min"] = cost_sec.pop("min_commission")

    # --- strategy.parameters → strategy.params ---
    strat = out.get("strategy", {})
    if isinstance(strat, dict):
        if "params" not in strat and "parameters" in strat:
            strat["params"] = strat.pop("parameters")
        if "params" not in strat and "config" in strat:
            strat["params"] = strat.pop("config")
        if "name" not in strat and "type" in strat:
            strat["name"] = strat.pop("type")

    # --- capital.initial → initial_capital ---
    cap_raw = out.pop("capital", None)
    if cap_raw and isinstance(cap_raw, dict) and "initial_capital" not in out:
        if "initial" in cap_raw:
            out["initial_capital"] = cap_raw["initial"]

    # --- trading_rules → t_plus_n, lot_size ---
    tr_raw = out.pop("trading_rules", None)
    if tr_raw and isinstance(tr_raw, dict):
        if "t_plus_n" not in out:
            out["t_plus_n"] = tr_raw.get("t_plus", tr_raw.get("t_plus_n"))
        if "lot_size" not in out and "lot_size" in tr_raw:
            out["lot_size"] = tr_raw["lot_size"]

    # --- risk_constraints / risk_control → risk ---
    rc_raw = out.pop("risk_constraints", None)
    rc2_raw = out.pop("risk_control", None)
    if rc2_raw and isinstance(rc2_raw, dict):
        rc_raw = {**(rc2_raw), **(rc_raw or {})} if rc_raw else rc2_raw
    if rc_raw and isinstance(rc_raw, dict):
        risk_sec = out.setdefault("risk", {})
        _rc_mapping = {
            "max_single_position_pct": "max_position_weight",
            "max_drawdown_pct": "max_drawdown",
            "max_sector_pct": "max_sector_weight",
            "daily_loss_limit_pct": "daily_loss_limit",
            "max_daily_turnover_pct": "max_daily_turnover",
        }
        for old_key, new_key in _rc_mapping.items():
            if old_key in rc_raw and new_key not in risk_sec:
                risk_sec[new_key] = rc_raw[old_key]
        for key, val in rc_raw.items():
            if key not in _rc_mapping and key not in risk_sec:
                risk_sec[key] = val

    return out


def parse_config(raw: dict[str, Any]) -> BacktestRunConfig:
    """解析配置字典 → BacktestRunConfig。"""
    raw = _normalize_config(raw)

    data_raw = raw.get("data", {})
    data = DataConfig(
        source=data_raw.get("source", "akshare"),
        start_date=_parse_date(data_raw.get("start_date")),
        end_date=_parse_date(data_raw.get("end_date")),
        symbols=data_raw.get("symbols"),
        frequency=data_raw.get("frequency", "1d"),
        adjust=data_raw.get("adjust", "qfq"),
        cache_dir=data_raw.get("cache_dir"),
    )

    universe_raw = raw.get("universe", {})
    universe = UniverseConfig(
        policy=universe_raw.get("policy", "default"),
        exclude_st=universe_raw.get("exclude_st", True),
        exclude_suspended=universe_raw.get("exclude_suspended", True),
        exclude_limit=universe_raw.get("exclude_limit", True),
        exclude_list_path=universe_raw.get("exclude_list_path"),
        min_listing_days=universe_raw.get("min_listing_days", 60),
        base_index=universe_raw.get("base_index"),
    )

    strategy_raw = raw.get("strategy", {})
    strategy = StrategyConfig(
        name=strategy_raw.get("name", "buy_and_hold"),
        params=strategy_raw.get("params", {}),
    )

    cost_raw = raw.get("cost", {})
    cost = CostConfig(
        commission_rate=cost_raw.get("commission_rate", 8.54e-05),
        commission_min=cost_raw.get("commission_min", 5.0),
        stamp_tax_rate=cost_raw.get("stamp_tax_rate", 0.0005),
        transfer_fee_rate=cost_raw.get("transfer_fee_rate", 0.00001),
        stamp_tax_auto_schedule=cost_raw.get("stamp_tax_auto_schedule", True),
        slippage_bps=cost_raw.get("slippage_bps", 10.0),
        slippage_model=cost_raw.get("slippage_model", "fixed"),
        slippage_params=cost_raw.get("slippage_params", {}),
    )

    fill_raw = raw.get("fill_model", {})
    fill_model = FillModelConfig(
        name=fill_raw.get("name", "next_bar_open"),
        volume_constraint=fill_raw.get("volume_constraint"),
    )

    risk_raw = raw.get("risk", {})
    _risk_known = {
        "enabled", "max_position_weight", "max_drawdown",
        "drawdown_scale_factor_floor", "drawdown_allow_rotation",
        "drawdown_peak_reset_days", "max_sector_weight",
        "max_daily_turnover", "daily_loss_limit", "stop_loss_pct",
        "trailing_stop_pct", "circuit_breaker_enabled",
        "circuit_breaker_drawdown", "enable_audit_log",
        "market_regime_enabled", "market_regime_bear_scale",
        "market_regime_neutral_scale", "benchmark_symbol",
        "concentration_enabled", "concentration_max_top_n_weight",
        "concentration_top_n", "concentration_min_positions",
        "liquidity_enabled", "liquidity_max_participation_rate",
        "style_exposure_enabled", "style_exposure_limits",
        "force_redeem_enabled", "force_redeem_max_weight",
        "force_redeem_cb_value_threshold",
        "industry_neutral_enabled", "industry_max_deviation",
        "degradation_enabled", "degradation_auto_recover",
        "degradation_recovery_cooldown",
        "degradation_position_scale_warning",
        "degradation_position_scale_degraded",
    }
    risk = RiskConfig(
        enabled=risk_raw.get("enabled", True),
        max_position_weight=risk_raw.get("max_position_weight", 0.10),
        max_drawdown=risk_raw.get("max_drawdown", 0.20),
        drawdown_scale_factor_floor=risk_raw.get("drawdown_scale_factor_floor", 0.0),
        drawdown_allow_rotation=risk_raw.get("drawdown_allow_rotation", False),
        drawdown_peak_reset_days=risk_raw.get("drawdown_peak_reset_days", 0),
        max_sector_weight=risk_raw.get("max_sector_weight"),
        max_daily_turnover=risk_raw.get("max_daily_turnover"),
        daily_loss_limit=risk_raw.get("daily_loss_limit"),
        stop_loss_pct=risk_raw.get("stop_loss_pct"),
        trailing_stop_pct=risk_raw.get("trailing_stop_pct"),
        circuit_breaker_enabled=risk_raw.get("circuit_breaker_enabled", False),
        circuit_breaker_drawdown=risk_raw.get("circuit_breaker_drawdown", 0.10),
        enable_audit_log=risk_raw.get("enable_audit_log", True),
        market_regime_enabled=risk_raw.get("market_regime_enabled", False),
        market_regime_bear_scale=risk_raw.get("market_regime_bear_scale", 0.50),
        market_regime_neutral_scale=risk_raw.get("market_regime_neutral_scale", 0.80),
        benchmark_symbol=risk_raw.get("benchmark_symbol", ""),
        concentration_enabled=risk_raw.get("concentration_enabled", False),
        concentration_max_top_n_weight=risk_raw.get("concentration_max_top_n_weight", 0.50),
        concentration_top_n=risk_raw.get("concentration_top_n", 5),
        concentration_min_positions=risk_raw.get("concentration_min_positions", 10),
        liquidity_enabled=risk_raw.get("liquidity_enabled", False),
        liquidity_max_participation_rate=risk_raw.get("liquidity_max_participation_rate", 0.10),
        style_exposure_enabled=risk_raw.get("style_exposure_enabled", False),
        style_exposure_limits=risk_raw.get("style_exposure_limits", {}),
        force_redeem_enabled=risk_raw.get("force_redeem_enabled", False),
        force_redeem_max_weight=risk_raw.get("force_redeem_max_weight", 0.30),
        force_redeem_cb_value_threshold=risk_raw.get("force_redeem_cb_value_threshold", 125.0),
        industry_neutral_enabled=risk_raw.get("industry_neutral_enabled", False),
        industry_max_deviation=risk_raw.get("industry_max_deviation", 0.05),
        degradation_enabled=risk_raw.get("degradation_enabled", False),
        degradation_auto_recover=risk_raw.get("degradation_auto_recover", True),
        degradation_recovery_cooldown=risk_raw.get("degradation_recovery_cooldown", 300.0),
        degradation_position_scale_warning=risk_raw.get("degradation_position_scale_warning", 0.5),
        degradation_position_scale_degraded=risk_raw.get("degradation_position_scale_degraded", 0.0),
        extra={k: v for k, v in risk_raw.items() if k not in _risk_known},
    )

    portfolio_raw = raw.get("portfolio", {})
    if portfolio_raw and isinstance(portfolio_raw, dict):
        portfolio = PortfolioConfig(
            method=portfolio_raw.get("method", "equal"),
            params={k: v for k, v in portfolio_raw.items() if k != "method"},
        )
    else:
        portfolio = PortfolioConfig()

    # --- tick 回测配置 ---
    tick_raw = raw.get("tick", {})
    is_tick_mode = data.frequency == "tick"
    tick_config = TickBacktestConfig(
        enabled=tick_raw.get("enabled", is_tick_mode),
        tick_source=tick_raw.get("tick_source", tick_raw.get("source", "auto")),
        tick_cache_dir=tick_raw.get("tick_cache_dir", tick_raw.get("cache_dir")),
        bridge_url=tick_raw.get("bridge_url", "http://127.0.0.1:58088"),
        fill_mode=tick_raw.get("fill_mode", "bbo"),
        aggregate_interval=tick_raw.get("aggregate_interval", 60),
        feature_buffer_size=tick_raw.get("feature_buffer_size", 256),
    )

    return BacktestRunConfig(
        name=raw.get("name", "unnamed_backtest"),
        description=raw.get("description", ""),
        data=data,
        universe=universe,
        strategy=strategy,
        cost=cost,
        fill_model=fill_model,
        risk=risk,
        portfolio=portfolio,
        tick=tick_config,
        initial_capital=raw.get("initial_capital", 1_000_000.0),
        lot_size=raw.get("lot_size", 100),
        t_plus_n=raw.get("t_plus_n", 1),
        random_seed=raw.get("random_seed"),
        output_dir=raw.get("output_dir", "output"),
        quality_gates_path=raw.get("quality_gates_path"),
    )


def config_to_dict(config: BacktestRunConfig) -> dict[str, Any]:
    """将配置转换为字典（可序列化为 YAML）。"""
    return {
        "name": config.name,
        "description": config.description,
        "data": {
            "source": config.data.source,
            "start_date": config.data.start_date.isoformat(),
            "end_date": config.data.end_date.isoformat(),
            "symbols": config.data.symbols,
            "frequency": config.data.frequency,
            "adjust": config.data.adjust,
        },
        "universe": {
            "policy": config.universe.policy,
            "exclude_st": config.universe.exclude_st,
            "exclude_suspended": config.universe.exclude_suspended,
            "base_index": config.universe.base_index,
        },
        "strategy": {
            "name": config.strategy.name,
            "params": config.strategy.params,
        },
        "cost": {
            "commission_rate": config.cost.commission_rate,
            "commission_min": config.cost.commission_min,
            "stamp_tax_rate": config.cost.stamp_tax_rate,
            "slippage_bps": config.cost.slippage_bps,
            "slippage_model": config.cost.slippage_model,
        },
        "fill_model": {"name": config.fill_model.name},
        "risk": {
            "enabled": config.risk.enabled,
            "max_position_weight": config.risk.max_position_weight,
            "max_drawdown": config.risk.max_drawdown,
            "stop_loss_pct": config.risk.stop_loss_pct,
        },
        "portfolio": {"method": config.portfolio.method},
        "initial_capital": config.initial_capital,
        "lot_size": config.lot_size,
        "t_plus_n": config.t_plus_n,
    }


def save_config(config: BacktestRunConfig, path: str | Path) -> None:
    """保存配置到 YAML 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config_to_dict(config), f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def validate_config(config: BacktestRunConfig) -> list[str]:
    """验证配置合理性，返回错误列表（空=通过）。"""
    errors: list[str] = []
    if config.data.start_date >= config.data.end_date:
        errors.append(f"start_date ({config.data.start_date}) must be before end_date ({config.data.end_date})")
    if config.initial_capital <= 0:
        errors.append(f"initial_capital must be positive, got {config.initial_capital}")
    if config.cost.commission_rate < 0:
        errors.append(f"commission_rate cannot be negative, got {config.cost.commission_rate}")
    if config.cost.commission_rate > 0.01:
        errors.append(f"commission_rate={config.cost.commission_rate} looks too high (>1%)")
    if config.cost.stamp_tax_rate > 0.003:
        errors.append(f"stamp_tax_rate={config.cost.stamp_tax_rate} looks too high (>0.3%)")
    _known_slip = {"fixed", "fixed_bps", "linear_impact", "square_root_impact", "none"}
    if config.cost.slippage_model not in _known_slip:
        errors.append(f"Unknown slippage_model '{config.cost.slippage_model}'")
    if not config.strategy.name:
        errors.append("strategy.name is required")
    return errors


def _parse_date(val: str | date | None) -> date:
    if val is None:
        raise ValueError("Date is required")
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val))
