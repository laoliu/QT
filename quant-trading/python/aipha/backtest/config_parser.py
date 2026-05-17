"""Configuration parsing, serialization, and utility functions.

Extracted from config_runner.py for modularity. Handles:
- YAML/JSON loading and saving
- Config normalization (multiple format support)
- Config parsing into typed dataclasses
- Config serialization back to dict
- Config validation and template generation
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from aipha.backtest.config_models import (
    BacktestRunConfig,
    BacktestRunResult,
    CostConfig,
    DataConfig,
    FillModelConfig,
    PortfolioConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
)
from aipha.backtest.engine import BacktestConfig
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
from aipha.utils.reproducibility import save_fingerprint

logger = logging.getLogger(__name__)


# ============================================================================
# 配置加载与解析
# ============================================================================


def load_config(path: str | Path) -> BacktestRunConfig:
    """从 YAML/JSON 文件加载配置

    Args:
        path: 配置文件路径

    Returns:
        BacktestRunConfig
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(f)
        else:
            import json

            raw = json.load(f)

    # 展开 _preset 引用（§统一配置体系）
    from aipha.config_resolver import resolve_config_refs

    import os as _os

    _project_root = path.resolve().parent
    _found_marker = False
    while _project_root != _project_root.parent:
        if (_project_root / "pyproject.toml").exists():
            _found_marker = True
            break
        _project_root = _project_root.parent
    if not _found_marker:
        _fallback = Path(_os.environ.get("AIPHA_PROJECT_ROOT", "/app"))
        if (_fallback / "configs").exists():
            _project_root = _fallback
        else:
            # 最终 fallback: YAML 文件上推 2 级（configs/xxx/file.yml → 项目根）
            _project_root = path.resolve().parent.parent.parent
    raw = resolve_config_refs(raw, base_dir=_project_root)

    return parse_config(raw)


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    """将新格式配置归一化为 parse_config 标准 schema。

    支持的新格式字段映射:
    - run.start_date/end_date/seed → data.start_date/end_date + random_seed
    - data.provider → data.source
    - universe_policy.* → universe.*
    - cost_model.commission.rate/min_fee → cost.commission_rate/commission_min
    - cost_model.stamp_tax.sell_rate → cost.stamp_tax_rate
    - cost_model.transfer_fee.rate → cost.transfer_fee_rate
    - slippage_model.bps/name → cost.slippage_bps/slippage_model
    - slippage.bps/model → cost.slippage_bps/slippage_model (another variant)
    - strategy.parameters → strategy.params
    - capital.initial → initial_capital
    - trading_rules.t_plus/lot_size → t_plus_n/lot_size
    - cost.min_commission → cost.commission_min
    - report.output_dir → output_dir

    已经符合标准 schema 的字段不会被覆盖。
    """
    import copy

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
    if "source" not in data_sec and "provider" in data_sec:
        data_sec["source"] = data_sec.pop("provider")

    # --- universe_policy.* → universe.* ---
    up_raw = out.pop("universe_policy", None)
    if up_raw and isinstance(up_raw, dict) and "universe" not in out:
        uni: dict[str, Any] = {}
        if "name" in up_raw:
            uni["policy"] = up_raw["name"]
        auto_exc = up_raw.get("auto_exclude", [])
        if isinstance(auto_exc, list):
            uni["exclude_st"] = any(
                s.upper() in ("ST", "*ST") for s in auto_exc
            )
            uni["exclude_suspended"] = any(
                s.upper() in ("SUSPENDED", "SUSPENDED_LONG") for s in auto_exc
            )
        if "manual_exclude_list" in up_raw:
            uni["exclude_list_path"] = up_raw["manual_exclude_list"]
        if "base_index" in up_raw:
            uni["base_index"] = up_raw["base_index"]
        out["universe"] = uni

    # --- market.instruments.index → universe.base_index (fallback) ---
    market_raw = out.get("market", {})
    if isinstance(market_raw, dict):
        instruments = market_raw.get("instruments", {})
        if isinstance(instruments, dict) and "index" in instruments:
            uni_sec = out.setdefault("universe", {})
            if isinstance(uni_sec, dict):
                uni_sec.setdefault("base_index", instruments["index"])

    # --- data.base_index → universe.base_index (fallback) ---
    data_sec_bi = out.get("data", {})
    if isinstance(data_sec_bi, dict) and "base_index" in data_sec_bi:
        uni_sec = out.setdefault("universe", {})
        if isinstance(uni_sec, dict):
            uni_sec.setdefault("base_index", data_sec_bi["base_index"])

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

    # --- slippage_model.bps/name → cost.slippage_bps/slippage_model ---
    slip_raw = out.pop("slippage_model", None) or out.pop("slippage", None)
    if slip_raw and isinstance(slip_raw, dict):
        cost_sec = out.setdefault("cost", {})
        if "slippage_bps" not in cost_sec:
            # 支持 bps 和 base_bps 两种键名（base_bps 是 fixed 模型的常用键名）
            cost_sec["slippage_bps"] = slip_raw.get(
                "bps", slip_raw.get("base_bps", 0.0)
            )
        if "slippage_model" not in cost_sec:
            cost_sec["slippage_model"] = slip_raw.get(
                "name", slip_raw.get("model", "fixed")
            )
        # 将 slippage_model 块中除 name/model/bps 之外的参数传递给 slippage_params
        # 注意：base_bps 会同时映射到 slippage_bps（上面）和保留在 extra_params 中
        # （linear_impact 等模型从 slippage_params 读取 base_bps）
        _reserved_slip_keys = {"name", "model", "bps"}
        extra_params = {k: v for k, v in slip_raw.items() if k not in _reserved_slip_keys}
        if extra_params and "slippage_params" not in cost_sec:
            cost_sec["slippage_params"] = extra_params

    # --- cost.min_commission → cost.commission_min (alias) ---
    cost_sec = out.get("cost", {})
    if isinstance(cost_sec, dict):
        if "commission_min" not in cost_sec and "min_commission" in cost_sec:
            cost_sec["commission_min"] = cost_sec.pop("min_commission")

    # --- strategy.parameters / strategy.config → strategy.params ---
    strat = out.get("strategy", {})
    if isinstance(strat, dict):
        if "params" not in strat and "parameters" in strat:
            strat["params"] = strat.pop("parameters")
        if "params" not in strat and "config" in strat:
            strat["params"] = strat.pop("config")
        # strategy.type as fallback for name
        if "name" not in strat and "type" in strat:
            strat["name"] = strat.pop("type")

    # --- capital.initial → initial_capital ---
    cap_raw = out.pop("capital", None)
    if cap_raw and isinstance(cap_raw, dict) and "initial_capital" not in out:
        if "initial" in cap_raw:
            out["initial_capital"] = cap_raw["initial"]

    # --- trading_rules.t_plus / t_plus_n → t_plus_n, trading_rules.lot_size → lot_size ---
    tr_raw = out.pop("trading_rules", None)
    if tr_raw and isinstance(tr_raw, dict):
        if "t_plus_n" not in out:
            if "t_plus" in tr_raw:
                out["t_plus_n"] = tr_raw["t_plus"]
            elif "t_plus_n" in tr_raw:
                out["t_plus_n"] = tr_raw["t_plus_n"]
        if "lot_size" not in out and "lot_size" in tr_raw:
            out["lot_size"] = tr_raw["lot_size"]

    # --- report.output_dir → output_dir ---
    report_raw = out.get("report", {})
    if isinstance(report_raw, dict):
        if "output_dir" not in out and "output_dir" in report_raw:
            out["output_dir"] = report_raw["output_dir"]

    # --- risk_constraints / risk_control → risk ---
    # §13.1: risk_control 是 8 个生产 YAML 使用的别名，必须等同处理
    rc_raw = out.pop("risk_constraints", None)
    rc2_raw = out.pop("risk_control", None)
    if rc2_raw and isinstance(rc2_raw, dict):
        if rc_raw and isinstance(rc_raw, dict):
            # 两个 key 同时存在时合并，risk_constraints 优先
            merged = {**rc2_raw, **rc_raw}
            rc_raw = merged
        else:
            rc_raw = rc2_raw
    if rc_raw and isinstance(rc_raw, dict):
        risk_sec = out.setdefault("risk", {})
        # 重命名映射：risk_constraints 旧键 → risk 新键
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
        # 直通透传：risk_constraints 中其余字段直接传入 risk（不覆盖已有值）
        for key, val in rc_raw.items():
            if key not in _rc_mapping and key not in risk_sec:
                risk_sec[key] = val
        # 如果有 daily_loss_limit，同时启用熔断
        if "daily_loss_limit" in risk_sec:
            risk_sec.setdefault("circuit_breaker_enabled", True)

    # --- report.benchmark → risk.benchmark_symbol ---
    if isinstance(report_raw, dict) and "benchmark" in report_raw:
        risk_sec = out.setdefault("risk", {})
        risk_sec.setdefault("benchmark_symbol", report_raw["benchmark"])

    # --- strategy.params.stop_loss_pct → risk.stop_loss_pct ---
    strat_params = out.get("strategy", {}).get("params", {})
    if isinstance(strat_params, dict) and "stop_loss_pct" in strat_params:
        risk_sec = out.setdefault("risk", {})
        risk_sec.setdefault("stop_loss_pct", strat_params["stop_loss_pct"])

    # --- warn about unrecognized top-level keys ---
    known_keys = {
        "name", "description", "data", "universe", "strategy", "cost",
        "fill_model", "initial_capital", "lot_size", "t_plus_n",
        "random_seed", "output_dir", "quality_gates_path", "risk",
        "portfolio", "missing_quote_writeoff_enabled", "missing_quote_writeoff_days",
        # tolerated but not parsed:
        "market", "frequency", "report",
        # aliases (handled above):
        "risk_control", "risk_constraints",
    }
    unknown = set(out.keys()) - known_keys
    if unknown:
        logger.warning(
            "Config contains unrecognized top-level keys (ignored): %s",
            ", ".join(sorted(unknown)),
        )

    return out


def _detect_asset_type_is_cb(data_raw: dict, raw: dict) -> bool:
    """检测配置是否指向可转债资产 (§13.1 asset_type 自动检测)。

    检测逻辑（任一命中即为 CB）：
    1. data.source 包含 'cb'（如 tushare_cb）
    2. data.asset_type == 'cb' / 'convertible_bond'
    3. strategy.name 以 'cb_' 开头
    """
    source = str(data_raw.get("source", "")).lower()
    if "cb" in source:
        return True
    asset = str(data_raw.get("asset_type", "")).lower()
    if asset in ("cb", "convertible_bond"):
        return True
    strat_name = str(raw.get("strategy", {}).get("name", "")).lower()
    if strat_name.startswith("cb_"):
        return True
    return False


def parse_config(raw: dict[str, Any]) -> BacktestRunConfig:
    """解析配置字典

    支持两种配置格式:
    1. 标准格式: data.source / cost.commission_rate / universe.* / strategy.params
    2. 扩展格式: run.* / data.provider / cost_model.* / universe_policy.* / strategy.parameters

    扩展格式会自动归一化为标准格式后解析。

    Args:
        raw: 原始配置字典

    Returns:
        BacktestRunConfig
    """
    # 先归一化为标准 schema
    raw = _normalize_config(raw)

    # 解析数据配置
    data_raw = raw.get("data", {})
    data = DataConfig(
        source=data_raw.get("source", "synthetic"),
        start_date=_parse_date(data_raw.get("start_date")),
        end_date=_parse_date(data_raw.get("end_date")),
        symbols=data_raw.get("symbols"),
        frequency=data_raw.get("frequency", "1d"),
        adjust=data_raw.get("adjust", "qfq"),
        cache_dir=data_raw.get("cache_dir"),
    )

    # --- §13.1 asset_type 自动检测与 MarketRules 默认值注入 ---
    _is_cb = _detect_asset_type_is_cb(data_raw, raw)
    if _is_cb:
        _mr = MarketRulesConvertibleBond()
        _defaults = {
            "commission_rate": float(_mr.commission_rate),
            "commission_min": float(_mr.commission_min),
            "stamp_tax_rate": float(_mr.stamp_tax_rate),
            "transfer_fee_rate": 0.0,
            "slippage_bps": float(_mr.slippage_bps_default),  # §13.1 CB 5.0
            "lot_size": _mr.lot_size,
            "t_plus_n": _mr.t_plus,
        }
    else:
        _mr_eq = MarketRulesCNEquity()
        _defaults = {
            "commission_rate": float(_mr_eq.commission_rate),
            "commission_min": float(_mr_eq.commission_min),
            "stamp_tax_rate": float(_mr_eq.stamp_tax_rate),
            "transfer_fee_rate": float(_mr_eq.transfer_fee_rate),
            "slippage_bps": float(_mr_eq.slippage_bps_default),  # §13.1 A股 10.0
            "lot_size": _mr_eq.lot_size,
            "t_plus_n": _mr_eq.t_plus,
        }

    # 解析股票池配置
    universe_raw = raw.get("universe", {})
    if not universe_raw:
        logger.warning(
            "配置缺少 'universe' 或 'universe_policy' 节 (§3.2 违规). "
            "自动填充安全默认值 (exclude_st/suspended/limit=True, min_listing_days=60). "
            "建议在 YAML 中显式声明 universe_policy."
        )
    universe = UniverseConfig(
        policy=universe_raw.get("policy", "default"),
        exclude_st=universe_raw.get("exclude_st", True),
        exclude_suspended=universe_raw.get("exclude_suspended", True),
        exclude_limit=universe_raw.get("exclude_limit", True),
        exclude_list_path=universe_raw.get("exclude_list_path"),
        min_listing_days=universe_raw.get("min_listing_days", 60),
        base_index=universe_raw.get("base_index"),
    )

    # 解析策略配置
    strategy_raw = raw.get("strategy", {})
    strategy = StrategyConfig(
        name=strategy_raw.get("name", "buy_and_hold"),
        params=strategy_raw.get("params", {}),
    )

    # 解析成本配置 — §13.1 默认值从 MarketRules 派生
    cost_raw = raw.get("cost", {})
    cost = CostConfig(
        commission_rate=cost_raw.get("commission_rate", _defaults["commission_rate"]),
        commission_min=cost_raw.get("commission_min", _defaults["commission_min"]),
        stamp_tax_rate=cost_raw.get("stamp_tax_rate", _defaults["stamp_tax_rate"]),
        transfer_fee_rate=cost_raw.get("transfer_fee_rate", _defaults["transfer_fee_rate"]),
        stamp_tax_auto_schedule=cost_raw.get("stamp_tax_auto_schedule", True),
        slippage_bps=cost_raw.get("slippage_bps", _defaults["slippage_bps"]),
        slippage_model=cost_raw.get("slippage_model", "fixed"),
        slippage_params=cost_raw.get("slippage_params", {}),
        t_plus_n_discount=cost_raw.get("t_plus_n_discount", 1.0),
    )

    # 解析成交模型配置
    fill_raw = raw.get("fill_model", {})
    fill_model = FillModelConfig(
        name=fill_raw.get("name", "next_bar_open"),
        volume_constraint=fill_raw.get("volume_constraint"),
    )

    # 解析风控配置
    risk_raw = raw.get("risk", {})
    risk = RiskConfig(
        enabled=risk_raw.get("enabled", True),
        max_position_weight=risk_raw.get("max_position_weight", 0.10),
        max_drawdown=risk_raw.get("max_drawdown", 1.0),  # §4.3.2: 默认禁用渐进减仓
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
        # --- P0-C: 以下 13 字段之前被静默丢弃，现已显式解析 ---
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
        # --- end P0-C ---
        degradation_enabled=risk_raw.get("degradation_enabled", False),
        degradation_auto_recover=risk_raw.get("degradation_auto_recover", True),
        degradation_recovery_cooldown=risk_raw.get("degradation_recovery_cooldown", 300.0),
        degradation_position_scale_warning=risk_raw.get("degradation_position_scale_warning", 0.5),
        degradation_position_scale_degraded=risk_raw.get("degradation_position_scale_degraded", 0.0),
        extra={
            k: v for k, v in risk_raw.items()
            if k not in {
                "enabled", "max_position_weight", "max_drawdown",
                "drawdown_scale_factor_floor", "drawdown_allow_rotation",
                "drawdown_peak_reset_days",
                "max_sector_weight", "max_daily_turnover", "daily_loss_limit",
                "stop_loss_pct", "trailing_stop_pct",
                "circuit_breaker_enabled", "circuit_breaker_drawdown",
                "enable_audit_log", "market_regime_enabled",
                "market_regime_bear_scale", "market_regime_neutral_scale",
                "benchmark_symbol",
                # P0-C: 新增 13 字段排除
                "concentration_enabled", "concentration_max_top_n_weight",
                "concentration_top_n", "concentration_min_positions",
                "liquidity_enabled", "liquidity_max_participation_rate",
                "style_exposure_enabled", "style_exposure_limits",
                "force_redeem_enabled", "force_redeem_max_weight",
                "force_redeem_cb_value_threshold",
                "industry_neutral_enabled", "industry_max_deviation",
                # degradation
                "degradation_enabled", "degradation_auto_recover",
                "degradation_recovery_cooldown",
                "degradation_position_scale_warning",
                "degradation_position_scale_degraded",
            }
        },
    )

    # §4.3.2 规则 8+9: DrawdownLimit 防护参数验证 (R163: 传入 top_k 检测死亡螺旋高危组合)
    _top_k = strategy.params.get("top_k") if isinstance(strategy.params, dict) else None
    risk.validate_drawdown_safety(top_k=_top_k if isinstance(_top_k, int) else None)

    # 解析顶层 valuation_sizing 节并注入 risk.extra
    val_sizing_raw = raw.get("valuation_sizing")
    if val_sizing_raw and isinstance(val_sizing_raw, dict):
        risk.extra["valuation_sizing"] = val_sizing_raw
        logger.debug("valuation_sizing 配置已注入 risk.extra")

    return BacktestRunConfig(
        name=raw.get("name", "unnamed_backtest"),
        description=raw.get("description", ""),
        data=data,
        universe=universe,
        strategy=strategy,
        cost=cost,
        fill_model=fill_model,
        risk=risk,
        portfolio=_parse_portfolio(raw),
        initial_capital=raw.get("initial_capital", 1_000_000.0),
        lot_size=raw.get("lot_size", _defaults["lot_size"]),
        t_plus_n=raw.get("t_plus_n", _defaults["t_plus_n"]),
        random_seed=raw.get("random_seed"),
        output_dir=raw.get("output_dir", "output"),
        quality_gates_path=raw.get("quality_gates_path"),
        missing_quote_writeoff_enabled=raw.get("missing_quote_writeoff_enabled", False),
        missing_quote_writeoff_days=raw.get("missing_quote_writeoff_days", 20),
    )


def _parse_date(val: str | date | None) -> date:
    """解析日期"""
    if val is None:
        raise ValueError("Date is required")
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val))


def _parse_portfolio(raw: dict[str, Any]) -> PortfolioConfig:
    """解析组合优化器配置

    支持两种来源：
    1. 顶层 ``portfolio:`` 配置节
    2. ``strategy.params.weight_method`` 快捷字段（优先级低于 ``portfolio:``）

    Args:
        raw: 归一化后的配置字典

    Returns:
        PortfolioConfig
    """
    portfolio_raw = raw.get("portfolio", {})
    if portfolio_raw and isinstance(portfolio_raw, dict):
        return PortfolioConfig(
            method=portfolio_raw.get("method", "equal"),
            params={
                k: v for k, v in portfolio_raw.items() if k != "method"
            },
        )

    # fallback: strategy.params.weight_method
    strat_params = raw.get("strategy", {}).get("params", {})
    if isinstance(strat_params, dict) and "weight_method" in strat_params:
        return PortfolioConfig(method=strat_params["weight_method"])

    return PortfolioConfig()


# ============================================================================
# 配置序列化与保存
# ============================================================================


def config_to_dict(config: BacktestRunConfig) -> dict[str, Any]:
    """将配置转换为字典

    Args:
        config: 回测配置

    Returns:
        配置字典
    """
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
            "cache_dir": config.data.cache_dir,
        },
        "universe": {
            "policy": config.universe.policy,
            "exclude_st": config.universe.exclude_st,
            "exclude_suspended": config.universe.exclude_suspended,
            "exclude_limit": config.universe.exclude_limit,
            "exclude_list_path": config.universe.exclude_list_path,
            "min_listing_days": config.universe.min_listing_days,
        },
        "strategy": {
            "name": config.strategy.name,
            "params": config.strategy.params,
        },
        "cost": {
            "commission_rate": config.cost.commission_rate,
            "commission_min": config.cost.commission_min,
            "stamp_tax_rate": config.cost.stamp_tax_rate,
            "transfer_fee_rate": config.cost.transfer_fee_rate,
            "slippage_bps": config.cost.slippage_bps,
        },
        "fill_model": {
            "name": config.fill_model.name,
            **({"volume_constraint": config.fill_model.volume_constraint}
               if config.fill_model.volume_constraint else {}),
        },
        "risk": {
            "enabled": config.risk.enabled,
            "max_position_weight": config.risk.max_position_weight,
            "max_drawdown": config.risk.max_drawdown,
            "max_sector_weight": config.risk.max_sector_weight,
            "max_daily_turnover": config.risk.max_daily_turnover,
            "daily_loss_limit": config.risk.daily_loss_limit,
            "stop_loss_pct": config.risk.stop_loss_pct,
            "trailing_stop_pct": config.risk.trailing_stop_pct,
            "circuit_breaker_enabled": config.risk.circuit_breaker_enabled,
            "circuit_breaker_drawdown": config.risk.circuit_breaker_drawdown,
            "enable_audit_log": config.risk.enable_audit_log,
            "market_regime_enabled": config.risk.market_regime_enabled,
            "market_regime_bear_scale": config.risk.market_regime_bear_scale,
            "market_regime_neutral_scale": config.risk.market_regime_neutral_scale,
            "benchmark_symbol": config.risk.benchmark_symbol,
        },
        "initial_capital": config.initial_capital,
        "lot_size": config.lot_size,
        "t_plus_n": config.t_plus_n,
        "random_seed": config.random_seed,
        "output_dir": config.output_dir,
        "quality_gates_path": config.quality_gates_path,
        "missing_quote_writeoff_enabled": config.missing_quote_writeoff_enabled,
        "missing_quote_writeoff_days": config.missing_quote_writeoff_days,
        "portfolio": {
            "method": config.portfolio.method,
            **(config.portfolio.params if config.portfolio.params else {}),
        },
    }


def save_config(config: BacktestRunConfig, path: str | Path) -> None:
    """保存配置到文件

    Args:
        config: 回测配置
        path: 保存路径
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(
            config_to_dict(config),
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


# ============================================================================
# 配置验证与转换
# ============================================================================


def to_backtest_config(run_config: BacktestRunConfig) -> BacktestConfig:
    """从运行配置创建回测引擎配置

    Args:
        run_config: 运行配置

    Returns:
        BacktestConfig
    """
    return BacktestConfig(
        initial_capital=run_config.initial_capital,
        lot_size=run_config.lot_size,
        commission_rate=run_config.cost.commission_rate,
        commission_min=run_config.cost.commission_min,
        stamp_tax_rate=run_config.cost.stamp_tax_rate,
        transfer_fee_rate=run_config.cost.transfer_fee_rate,
        slippage_bps=run_config.cost.slippage_bps,
        t_plus_n=run_config.t_plus_n,
        missing_quote_writeoff_enabled=run_config.missing_quote_writeoff_enabled,
        missing_quote_writeoff_days=run_config.missing_quote_writeoff_days,
    )


def validate_config(config: BacktestRunConfig, *, strict_mode: bool = True) -> list[str]:
    """验证配置

    R87 增强: 添加滑点模型/费率合理性/资产类型一致性检查，
    防止 V7/V7.1 类型的配置格式不一致导致回测结果误诊。

    Phase 1 加固 (Plan v3): 新增 strict_mode 零容忍检查：
    - slippage_bps=0 → ERROR
    - commission_rate=0（无显式豁免）→ ERROR
    - fill_model.max_participation_rate > 0.30 → ERROR
    - cost 与 MarketRules 偏差 > 200% → ERROR

    Args:
        config: 回测配置
        strict_mode: 是否启用严格检查（默认 True）。
                     仅在显式压力测试/sensitivity analysis 时设为 False。

    Returns:
        错误消息列表（空列表表示验证通过）
    """
    errors: list[str] = []

    # 日期验证
    if config.data.start_date >= config.data.end_date:
        errors.append(
            f"start_date ({config.data.start_date}) must be before "
            f"end_date ({config.data.end_date})"
        )

    # 资金验证
    if config.initial_capital <= 0:
        errors.append(
            f"initial_capital must be positive, got {config.initial_capital}"
        )

    # 成本验证
    if config.cost.commission_rate < 0:
        errors.append(
            f"commission_rate cannot be negative, got {config.cost.commission_rate}"
        )
    if config.cost.stamp_tax_rate < 0:
        errors.append(
            f"stamp_tax_rate cannot be negative, got {config.cost.stamp_tax_rate}"
        )

    # R87: 滑点模型名称合法性检查 (防止 V7 类 silent-default 问题)
    _KNOWN_SLIPPAGE_MODELS = {
        "fixed", "fixed_bps", "linear_impact", "square_root_impact", "none",
    }
    slip_name = getattr(config.cost, "slippage_model", "fixed")
    if slip_name and slip_name not in _KNOWN_SLIPPAGE_MODELS:
        errors.append(
            f"Unknown slippage_model '{slip_name}'. "
            f"Known models: {sorted(_KNOWN_SLIPPAGE_MODELS)}"
        )

    # R87: 佣金率合理性 (0 < rate < 0.01, 即 0% ~ 1%)
    cr = config.cost.commission_rate
    if cr > 0.01:
        errors.append(
            f"commission_rate={cr} looks too high (>1%). "
            "Typical range: 0.00005 ~ 0.001"
        )

    # R87: 印花税率合理性 (0 ~ 0.003)
    st = config.cost.stamp_tax_rate
    if st > 0.003:
        errors.append(
            f"stamp_tax_rate={st} looks too high (>0.3%). "
            "Current A-stock: 0.0005 (since 2023-08)"
        )

    # 策略验证
    if not config.strategy.name:
        errors.append("strategy.name is required")

    # ========================================================================
    # Phase 1 strict_mode 加固检查（Plan v3, §13.18 Fail-Closed）
    # ========================================================================
    if strict_mode:
        # slippage_bps = 0 → 等于假设零交易冲击，虚高 AR
        slippage_bps = getattr(config.cost, "slippage_bps", None)
        if slippage_bps is not None and slippage_bps <= 0:
            errors.append(
                f"slippage_bps={slippage_bps} is invalid (must be > 0). "
                "Zero slippage produces unrealistically inflated returns. "
                "CB default=5.0, A-stock default=10.0."
            )

        # commission_rate = 0 → 除非显式标记 allow_zero_commission
        cr = config.cost.commission_rate
        allow_zero = getattr(config.cost, "allow_zero_commission", False)
        if cr == 0 and not allow_zero:
            errors.append(
                "commission_rate=0 is not allowed without explicit "
                "'allow_zero_commission: true'. "
                "Standard rate: 0.0000854 (万0.854)."
            )

        # max_participation_rate > 0.30 → 不现实的成交假设
        max_pr = getattr(config.fill_model, "max_participation_rate", None)
        if max_pr is not None and max_pr > 0.30:
            errors.append(
                f"fill_model.max_participation_rate={max_pr} exceeds 30% limit. "
                "High participation rates produce unrealistic fill assumptions."
            )

        # slippage_bps 与 MarketRules 偏差 > 200% → 可能是 typo
        if slippage_bps is not None and slippage_bps > 0:
            _asset = getattr(config, "asset_type", None) or "convertible_bond"
            if "equity" in str(_asset).lower() or "stock" in str(_asset).lower():
                _expected_bps = 10.0  # A-stock default
            else:
                _expected_bps = 5.0   # CB default
            if slippage_bps > _expected_bps * 3:
                errors.append(
                    f"slippage_bps={slippage_bps} is >3x the MarketRules default "
                    f"({_expected_bps}). If intentional, use strict_mode=False."
                )

    return errors


# ============================================================================
# 结果保存
# ============================================================================


def save_run_result(run_result: BacktestRunResult) -> dict[str, Path]:
    """保存运行结果

    保存配置、指纹和结果到输出目录。

    Args:
        run_result: 运行结果

    Returns:
        保存的文件路径字典
    """
    import json

    output_dir = run_result.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    # 保存配置
    config_path = output_dir / "config.yaml"
    save_config(run_result.config, config_path)
    paths["config"] = config_path

    # 保存指纹
    fingerprint_path = output_dir / "fingerprint.json"
    save_fingerprint(run_result.fingerprint, fingerprint_path)
    paths["fingerprint"] = fingerprint_path

    # 保存结果摘要（含完整结构化指标）
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        summary: dict[str, Any] = {
            "strategy_name": run_result.result.strategy_name,
            "start_date": run_result.result.start_date.isoformat(),
            "end_date": run_result.result.end_date.isoformat(),
            "initial_capital": run_result.result.initial_capital,
            "final_nav": run_result.result.final_nav,
            "total_trades": run_result.result.total_trades,
            "total_fees": run_result.result.total_fees.total,
        }
        # 追加完整结构化指标
        try:
            from aipha.backtest.report import (
                compute_cost_attribution,
                extract_metrics_dict,
            )
            metrics = extract_metrics_dict(run_result.result)
            summary["metrics"] = {
                k: round(v, 6) if isinstance(v, float) else v
                for k, v in metrics.items()
            }
            # 成本归因
            cost_attr = compute_cost_attribution(run_result.result)
            summary["cost_attribution"] = {
                "total_fees": round(cost_attr.total_fees, 2),
                "commission_total": round(cost_attr.commission_total, 2),
                "stamp_tax_total": round(cost_attr.stamp_tax_total, 2),
                "transfer_fee_total": round(cost_attr.transfer_fee_total, 2),
                "slippage_cost": round(cost_attr.slippage_cost, 2),
                "cost_vs_nav_pct": round(cost_attr.cost_vs_nav_pct, 6),
                "cost_drag_annualized": round(cost_attr.cost_drag_annualized, 6),
            }
            # T+1 折扣调整（原则D [CN]）
            discount = run_result.config.cost.t_plus_n_discount
            if discount < 1.0:
                sr = metrics.get("sharpe_ratio", 0.0)
                summary["metrics"]["sharpe_ratio_t1_adjusted"] = round(
                    sr * discount, 6
                )
                summary["metrics"]["t_plus_n_discount"] = discount
        except Exception as _e:
            logger.debug("summary.json 指标计算失败: %s", _e)
        json.dump(summary, f, indent=2, ensure_ascii=False)
    paths["summary"] = summary_path

    # ── 保存逐笔交易明细 CSV ──
    try:
        import csv

        trades = run_result.result.trades
        if trades:
            trades_path = output_dir / "trades.csv"
            with open(trades_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "trade_id", "order_id", "date", "time", "symbol", "side",
                    "qty", "price", "notional", "commission", "stamp_tax",
                    "transfer_fee", "total_fee", "reason",
                ])
                for t in trades:
                    writer.writerow([
                        t.trade_id,
                        t.order_id,
                        t.trade_date.isoformat(),
                        t.trade_time.isoformat() if t.trade_time else "",
                        t.symbol,
                        t.side.value if hasattr(t.side, "value") else str(t.side),
                        t.qty,
                        round(t.price, 4),
                        round(t.notional, 2),
                        round(t.fees.commission, 4),
                        round(t.fees.stamp_tax, 4),
                        round(t.fees.transfer_fee, 4),
                        round(t.fees.total, 4),
                        t.reason or "",
                    ])
            paths["trades"] = trades_path
            logger.debug("交易明细已保存: %s (%d 笔)", trades_path, len(trades))
    except Exception as _e:
        logger.warning("交易明细 CSV 保存失败: %s", _e)

    # ── 保存每日快照 CSV（委托 nav_artifacts §13.2 单一实现） ──
    try:
        from aipha.backtest.nav_artifacts import write_daily_snapshots_csv

        snapshots = run_result.result.daily_snapshots
        if snapshots:
            snapshots_path = output_dir / "daily_snapshots.csv"
            write_daily_snapshots_csv(snapshots, snapshots_path)
            paths["daily_snapshots"] = snapshots_path
            logger.debug("每日快照已保存: %s (%d 天)", snapshots_path, len(snapshots))
    except Exception as _e:
        logger.warning("每日快照 CSV 保存失败: %s", _e)

    # ── 写入 SQLite 索引 ──
    try:
        import tempfile as _tf

        from aipha.backtest.result_store import ResultStore, summary_json_to_run_dict

        # 跳过临时目录中的结果（测试环境产出），避免污染生产 DB
        _tmpdir = Path(_tf.gettempdir()).resolve()
        if summary_path.resolve().is_relative_to(_tmpdir):
            logger.debug("跳过临时目录结果的 SQLite 索引: %s", summary_path)
        else:
            run_dict = summary_json_to_run_dict(summary_path)
            store = ResultStore()
            store.upsert_backtest_run(run_dict)
            store.close()
            logger.info("结果已索引到 SQLite: %s", run_dict["run_id"])
    except Exception as _e:
        logger.warning("SQLite 索引写入失败: %s", _e)

    return paths


# ============================================================================
# 配置模板
# ============================================================================


def create_config_template(
    name: str = "my_backtest",
    start_date: date | None = None,
    end_date: date | None = None,
) -> BacktestRunConfig:
    """创建配置模板

    Args:
        name: 回测名称
        start_date: 开始日期（默认去年1月）
        end_date: 结束日期（默认今天）

    Returns:
        配置模板
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = date(end_date.year - 1, 1, 1)

    return BacktestRunConfig(
        name=name,
        description="回测配置模板",
        data=DataConfig(
            source="synthetic",
            start_date=start_date,
            end_date=end_date,
            symbols=["600000.SH", "000001.SZ"],
            frequency="1d",
            adjust="qfq",
        ),
        universe=UniverseConfig(
            policy="default",
            exclude_st=True,
            exclude_suspended=True,
            exclude_limit=True,
            min_listing_days=60,
        ),
        strategy=StrategyConfig(
            name="buy_and_hold",
            params={},
        ),
        cost=CostConfig(
            commission_rate=8.54e-05,  # §13.1 → MarketRulesCNEquity 万0.854
            commission_min=5.0,
            stamp_tax_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_bps=10.0,
        ),
        fill_model=FillModelConfig(name="next_bar_open"),
        initial_capital=1_000_000.0,
        lot_size=100,
        t_plus_n=1,
        random_seed=42,
        output_dir="output",
    )
