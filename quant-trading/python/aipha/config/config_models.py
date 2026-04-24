"""Backtest configuration data models.

All dataclass definitions used by the config-driven backtest runner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class DataConfig:
    """数据配置"""

    source: str
    start_date: date
    end_date: date
    symbols: list[str] | None = None
    frequency: str = "1d"
    adjust: str = "qfq"
    cache_dir: str | None = None


@dataclass
class UniverseConfig:
    """股票池配置"""

    policy: str = "default"
    exclude_st: bool = True
    exclude_suspended: bool = True
    exclude_limit: bool = True
    exclude_list_path: str | None = None
    min_listing_days: int = 60
    base_index: str | None = None


@dataclass
class StrategyConfig:
    """策略配置"""

    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CostConfig:
    """成本配置

    默认值来自 A 股 MarketRules:
    - 佣金万 0.854, 最低 5 元
    - 印花税千分之 0.5 (仅卖，2023-08 起)
    - 过户费十万分之一
    - 滑点 10 bps
    """

    commission_rate: float = 8.54e-05
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    stamp_tax_auto_schedule: bool = True
    slippage_bps: float = 10.0
    slippage_model: str = "fixed"
    slippage_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FillModelConfig:
    """成交模型配置"""

    name: str = "next_bar_open"
    volume_constraint: dict[str, Any] | None = None


@dataclass
class RiskConfig:
    """风控配置"""

    enabled: bool = True
    max_position_weight: float = 0.10
    max_drawdown: float = 0.20
    drawdown_scale_factor_floor: float = 0.0
    drawdown_allow_rotation: bool = False
    drawdown_peak_reset_days: int = 0
    max_sector_weight: float | None = None
    max_daily_turnover: float | None = None
    daily_loss_limit: float | None = None
    stop_loss_pct: float | None = None
    trailing_stop_pct: float | None = None
    circuit_breaker_enabled: bool = False
    circuit_breaker_drawdown: float = 0.10
    enable_audit_log: bool = True
    market_regime_enabled: bool = False
    market_regime_bear_scale: float = 0.50
    market_regime_neutral_scale: float = 0.80
    benchmark_symbol: str = ""
    concentration_enabled: bool = False
    concentration_max_top_n_weight: float = 0.50
    concentration_top_n: int = 5
    concentration_min_positions: int = 10
    liquidity_enabled: bool = False
    liquidity_max_participation_rate: float = 0.10
    style_exposure_enabled: bool = False
    style_exposure_limits: dict = field(default_factory=lambda: {
        "size": 0.30, "value": 0.30, "momentum": 0.30,
    })
    force_redeem_enabled: bool = False
    force_redeem_max_weight: float = 0.30
    force_redeem_cb_value_threshold: float = 125.0
    industry_neutral_enabled: bool = False
    industry_max_deviation: float = 0.05
    degradation_enabled: bool = False
    degradation_auto_recover: bool = True
    degradation_recovery_cooldown: float = 300.0
    degradation_position_scale_warning: float = 0.5
    degradation_position_scale_degraded: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class PortfolioConfig:
    """组合优化器配置

    支持: equal, market_cap, risk_parity, min_variance,
    max_sharpe, mean_variance, black_litterman
    """

    method: str = "equal"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class TickBacktestConfig:
    """Tick 回测专用配置

    当 data.frequency = "tick" 时使用。
    """

    enabled: bool = False
    tick_source: str = "auto"          # "parquet" | "bridge" | "auto"
    tick_cache_dir: str | None = None  # tick Parquet 缓存目录
    bridge_url: str = "http://127.0.0.1:58088"
    fill_mode: str = "bbo"             # "immediate" | "bbo" | "next_tick"
    aggregate_interval: int = 60       # 聚合间隔（秒）
    feature_buffer_size: int = 256     # 特征计算缓冲区大小


@dataclass
class BacktestRunConfig:
    """完整回测配置"""

    name: str
    data: DataConfig
    universe: UniverseConfig
    strategy: StrategyConfig
    cost: CostConfig
    fill_model: FillModelConfig = field(default_factory=FillModelConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    tick: TickBacktestConfig = field(default_factory=TickBacktestConfig)
    description: str = ""
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    t_plus_n: int = 1
    random_seed: int | None = None
    output_dir: str = "output"
    quality_gates_path: str | None = None
