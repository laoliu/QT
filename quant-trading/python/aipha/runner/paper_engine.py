"""
Paper Trading 引擎

提供异步事件循环驱动的模拟交易入口，编排：
1. 实时数据源（AKShareRealtimeProvider）→ BarData → Domain Bar
2. LiveRunner 策略执行
3. 市场时段调度（开盘等待、午休、收盘结算）
4. 状态持久化与恢复
5. 动态标的池（CBUniverseProvider）
6. 交易监控与告警（TradingMonitor）
7. 实时指标收集（MetricsCollector）

设计原则：
- 回测与仿真共享同一套接口（LiveRunner / Gateway / FeeModel）
- 配置驱动（所有交易参数来自 configs/）
- 可插拔数据源（通过 RealtimeStreamProvider ABC）
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from aipha.data.akshare_realtime_provider import (
    AKShareRealtimeConfig,
    AKShareRealtimeProvider,
    MarketType,
    bardata_to_domain_bar,
)
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.data.cb_universe import CBUniverseConfig, CBUniverseProvider
from aipha.data.rate_limiter import get_akshare_limiter
from aipha.data.realtime_stream import BarData, QuoteType, StreamStatus
from aipha.domain.models import Bar
from aipha.monitor.alerts import (
    AlertLevel,
    DataStaleAlert,
    DailyLossAlert,
    LogNotifier,
    MaxDrawdownAlert,
    PortfolioSnapshot,
    StrategyInactivityAlert,
    TradingMonitor,
)
from aipha.monitor.metrics_collector import MetricsCollector, MetricsConfig
from aipha.execution.slippage import (
    SlippageModel,
    create_slippage_model as _shared_create_slippage,
)
from aipha.risk.barra_style_monitor import BarraStyleMonitor, StyleMonitorConfig
from aipha.risk.ml_risk_controls import (
    MddGradientDeleverager,
    SharpeDownweightTrigger,
    VolTargetScaler,
)
from aipha.risk.position_sizing import UnifiedPositionSizer
from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode
from aipha.runner.state_manager import StateManager
from aipha.utils.paths import project_path, resolve_path

logger = logging.getLogger(__name__)


@dataclass
class BondSymbolConfig:
    """债券多久期配置中的单个标的"""
    code: str
    weight: float = 1.0
    duration_bucket: str = ""


@dataclass
class PillarConfig:
    """子组件（Pillar）配置

    解析 plan_a_5comp.yml 的 pillars: 节，
    为 PillarOrchestrator 提供结构化数据。
    """
    name: str
    weight: float
    market_type: str = "convertible_bond"
    # 静态标的列表（bond/gold 有固定 symbol）
    static_symbols: list[str] = field(default_factory=list)
    # 债券多久期标的（O9: symbols list with weight/duration_bucket）
    bond_symbols: list[BondSymbolConfig] = field(default_factory=list)
    # 策略名或模型路径
    strategy_name: str = ""
    model_dir: str = ""
    # 子策略（CB pillar 内的多策略混合）
    sub_strategies: list[dict] = field(default_factory=list)
    # 原始配置（保留用于后续扩展）
    raw: dict = field(default_factory=dict)


@dataclass
class PaperEngineConfig:
    """Paper Trading 引擎配置"""

    # 策略名称（需已注册到 StrategyRegistry）
    strategy_name: str = ""

    # 策略参数（传给策略构造函数的 kwargs）
    strategy_params: dict = field(default_factory=dict)

    # 标的列表（静态，universe 未启用时使用）
    symbols: list[str] = field(default_factory=list)

    # --- 多 Pillar 配置 (plan_a_5comp 使用) ---
    pillar_configs: dict[str, PillarConfig] = field(default_factory=dict)
    portfolio_weights: dict[str, float] = field(default_factory=dict)

    # 数据源配置
    market_type: MarketType = MarketType.CB
    poll_interval: float = 3.0
    bar_interval_sec: int = 60

    # 运行器配置
    initial_capital: float = 1_000_000.0
    lot_size: int = 10  # 可转债默认 10 张（§13.1 → MarketRulesConvertibleBond）
    t_plus_n: int = 0   # 可转债 T+0
    # §13.1: 费率默认值由 __post_init__ 从 MarketRulesConvertibleBond 派生
    commission_rate: float = -1.0  # sentinel
    commission_min: float = -1.0
    stamp_tax_rate: float = -1.0
    transfer_fee_rate: float = -1.0
    slippage_bps: float = -1.0
    fill_model_name: str = "next_bar_open"  # 与回测 fill_model.name 对齐
    fill_model_volume_constraint: dict[str, Any] | None = None
    # §4.3 滑点模型：回测/模拟盘共享同一接口
    slippage_model_name: str = "fixed"  # fixed | linear_impact | square_root_impact
    slippage_params: dict = field(default_factory=dict)  # 传给模型的参数

    # 状态持久化
    state_dir: Path = field(default_factory=lambda: project_path("data", "paper_state"))
    state_file: str = "paper_state.json"

    # 日志
    audit_log_dir: Path = field(default_factory=lambda: project_path("logs", "paper"))

    # EOD 自动结算时间（收盘后5分钟执行结算）
    eod_settlement_delay_sec: int = 300

    # --- 动态标的池 ---
    universe_config: CBUniverseConfig = field(default_factory=lambda: CBUniverseConfig(enabled=False))

    # --- 监控告警 ---
    monitoring_enabled: bool = True
    alert_drawdown_threshold: float = 0.08     # 8% 回撤告警
    alert_daily_loss_threshold: float = 0.03   # 3% 日内亏损告警
    alert_data_stale_seconds: float = 120.0    # 120s 数据中断告警
    alert_inactivity_minutes: float = 120.0    # 120 分钟无成交告警

    # --- 指标收集 ---
    metrics_enabled: bool = True
    metrics_config: MetricsConfig = field(default_factory=MetricsConfig)

    # --- ML 风控共享模块 (R81 §4.3.1 P0 约束) ---
    ml_risk_controls_enabled: bool = True
    ml_risk_mdd_levels: list[tuple[float, float]] | None = None  # None = 使用默认
    ml_risk_vol_target: float = 0.20
    ml_risk_vol_lookback: int = 60
    ml_risk_max_stock_pct: float = 0.03

    # --- 行业中性约束 (R92 T2-2) ---
    max_industry_pct: float = 0.30  # 单行业最大持仓占比

    # --- B1: ML Rolling SR 降权触发器 (R88) ---
    ml_sr_downweight_enabled: bool = False
    ml_sr_downweight_lookback: int = 60
    ml_sr_downweight_threshold: float = 0.0
    ml_sr_downweight_normal: float = 0.30
    ml_sr_downweight_reduced: float = 0.20

    # --- C2: CB Pillar 动态降权 (R89) ---
    cb_pillar_downweight_enabled: bool = False
    cb_pillar_downweight_sr_threshold: float = 0.5
    cb_pillar_downweight_consecutive_months: int = 4
    cb_pillar_downweight_reduction: float = 0.333
    cb_pillar_downweight_recovery_months: int = 3
    cb_pillar_downweight_rolling_window: int = 252
    # 降权时多余权重重分配目标（默认：Bond + Gold）
    cb_pillar_downweight_redistribute: list[str] = field(
        default_factory=lambda: ["bond", "gold"]
    )

    # --- C3: ML 模型切换器 CSI2000/CSI1000 (R89) ---
    ml_model_switcher_enabled: bool = False
    ml_model_switcher_sr_threshold: float = 0.5
    ml_model_switcher_consecutive_months: int = 2
    ml_model_switcher_recovery_sr: float = 1.0
    ml_model_switcher_recovery_months: int = 2
    ml_model_switcher_transition_days: int = 21
    ml_model_switcher_rolling_window: int = 60
    ml_model_switcher_primary_model: str = "models/ml_csi2000_lgb_fund"
    ml_model_switcher_backup_model: str = "models/ml_csi1000_lgb_fund"

    # --- Barra 风格暴露监控 (O6) ---
    barra_monitor_enabled: bool = False
    barra_alert_thresholds: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """§13.1: 从 MarketRulesConvertibleBond 动态派生费率默认值."""
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )
        rules = MarketRulesConvertibleBond()
        if self.commission_rate < 0:
            self.commission_rate = float(rules.commission_rate)
        if self.commission_min < 0:
            self.commission_min = float(rules.commission_min)
        if self.stamp_tax_rate < 0:
            self.stamp_tax_rate = float(rules.stamp_tax_rate)
        if self.transfer_fee_rate < 0:
            self.transfer_fee_rate = 0.0
        if self.slippage_bps < 0:
            self.slippage_bps = rules.slippage_bps_default

    def create_slippage_model(self) -> SlippageModel:
        """委托共享工厂创建滑点模型（§4.3 / §13.2）。"""
        return _shared_create_slippage(
            model_name=self.slippage_model_name,
            slippage_bps=self.slippage_bps,
            slippage_params=self.slippage_params,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "PaperEngineConfig":
        """从 YAML 配置文件加载"""
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        engine_data = data.get("paper_engine", data)

        # 解析 universe 配置
        universe_data = engine_data.get("universe", {})
        universe_config = CBUniverseConfig.from_dict(universe_data) if universe_data else CBUniverseConfig(enabled=False)

        # 解析 metrics 配置
        metrics_data = engine_data.get("metrics", {})
        metrics_config = MetricsConfig.from_dict(metrics_data) if metrics_data else MetricsConfig()

        # --- B1/B3: 解析 pillars 配置 ---
        raw_pillars = data.get("pillars", {})
        pillar_configs: dict[str, PillarConfig] = {}
        pillar_static_symbols: list[str] = []
        raw_weights = engine_data.get("portfolio_weights", {})

        for key, pdata in raw_pillars.items():
            if not isinstance(pdata, dict):
                continue
            # 解析静态标的
            static_syms: list[str] = []
            bond_syms: list[BondSymbolConfig] = []
            sub_strats: list[dict] = pdata.get("sub_strategies", [])

            # B3: bond pillar 的多久期 symbols 列表
            raw_sym_list = pdata.get("symbols", [])
            if isinstance(raw_sym_list, list):
                for item in raw_sym_list:
                    if isinstance(item, dict) and "code" in item:
                        code = item["code"]
                        static_syms.append(code)
                        bond_syms.append(BondSymbolConfig(
                            code=code,
                            weight=item.get("weight", 1.0),
                            duration_bucket=item.get("duration_bucket", ""),
                        ))
                    elif isinstance(item, str):
                        static_syms.append(item)

            # 单标的 symbol 字段 (gold pillar)
            single_sym = pdata.get("symbol", "")
            if single_sym:
                static_syms.append(single_sym)

            pillar_configs[key] = PillarConfig(
                name=pdata.get("name", key),
                weight=pdata.get("weight", 0.0),
                market_type=pdata.get("market_type", ""),
                static_symbols=static_syms,
                bond_symbols=bond_syms,
                strategy_name=pdata.get("strategy_name", ""),
                model_dir=pdata.get("model_dir", ""),
                sub_strategies=sub_strats,
                raw=pdata,
            )
            pillar_static_symbols.extend(static_syms)

        # 如果 YAML 没有顶层 symbols 但有 pillar 静态标的，自动合并
        explicit_symbols = engine_data.get("symbols", [])
        if not explicit_symbols and pillar_static_symbols:
            explicit_symbols = pillar_static_symbols

        fill_model_data = engine_data.get("fill_model", {}) or {}
        fill_model_name = fill_model_data.get(
            "name",
            engine_data.get("fill_model_name", "next_bar_open"),
        )
        fill_model_volume_constraint = fill_model_data.get("volume_constraint")

        return cls(
            strategy_name=engine_data.get("strategy_name", ""),
            strategy_params=engine_data.get("strategy_params", {}),
            symbols=explicit_symbols,
            pillar_configs=pillar_configs,
            portfolio_weights=raw_weights,
            market_type=MarketType(engine_data.get("market_type", "convertible_bond")),
            poll_interval=engine_data.get("poll_interval", 3.0),
            bar_interval_sec=engine_data.get("bar_interval_sec", 60),
            initial_capital=engine_data.get("initial_capital", 1_000_000.0),
            lot_size=engine_data.get("lot_size", 10),
            t_plus_n=engine_data.get("t_plus_n", 0),
            commission_rate=engine_data.get("commission_rate", -1.0),  # §13.1: -1 → __post_init__ 从 MarketRules 派生
            commission_min=engine_data.get("commission_min", -1.0),
            stamp_tax_rate=engine_data.get("stamp_tax_rate", -1.0),
            slippage_bps=engine_data.get("slippage_bps", -1.0),
            fill_model_name=fill_model_name,
            fill_model_volume_constraint=fill_model_volume_constraint,
            slippage_model_name=engine_data.get("slippage_model", "fixed"),
            slippage_params=engine_data.get("slippage_params", {}),
            # 与上面 dataclass 的默认值同源：配置里写的相对路径也锚到项目根，
            # 否则模拟盘状态会随启动目录散到不同的树里 —— 换个目录重启就是
            # 「持仓凭空清零」。
            state_dir=resolve_path(
                engine_data.get("state_dir"), "data", "paper_state"
            ),
            audit_log_dir=resolve_path(
                engine_data.get("audit_log_dir"), "logs", "paper"
            ),
            universe_config=universe_config,
            monitoring_enabled=engine_data.get("monitoring_enabled", True),
            alert_drawdown_threshold=engine_data.get("alert_drawdown_threshold", 0.08),
            alert_daily_loss_threshold=engine_data.get("alert_daily_loss_threshold", 0.03),
            alert_data_stale_seconds=engine_data.get("alert_data_stale_seconds", 120.0),
            alert_inactivity_minutes=engine_data.get("alert_inactivity_minutes", 120.0),
            metrics_enabled=engine_data.get("metrics_enabled", True),
            metrics_config=metrics_config,
            # ML 风控共享模块
            ml_risk_controls_enabled=engine_data.get("ml_risk_controls_enabled", True),
            ml_risk_mdd_levels=engine_data.get("ml_risk_mdd_levels", None),
            ml_risk_vol_target=engine_data.get("ml_risk_vol_target", 0.20),
            ml_risk_vol_lookback=engine_data.get("ml_risk_vol_lookback", 60),
            ml_risk_max_stock_pct=engine_data.get("ml_risk_max_stock_pct", 0.03),
            # R92 T2-2: 行业中性约束
            max_industry_pct=engine_data.get("max_industry_pct", 0.30),
            # B1: ML Rolling SR 降权触发器 (R88)
            ml_sr_downweight_enabled=engine_data.get("ml_sr_downweight_enabled", False),
            ml_sr_downweight_lookback=engine_data.get("ml_sr_downweight_lookback", 60),
            ml_sr_downweight_threshold=engine_data.get("ml_sr_downweight_threshold", 0.0),
            ml_sr_downweight_normal=engine_data.get("ml_sr_downweight_normal", 0.30),
            ml_sr_downweight_reduced=engine_data.get("ml_sr_downweight_reduced", 0.20),
            # C2: CB Pillar 动态降权 (R89)
            cb_pillar_downweight_enabled=engine_data.get("cb_pillar_downweight_enabled", False),
            cb_pillar_downweight_sr_threshold=engine_data.get("cb_pillar_downweight_sr_threshold", 0.5),
            cb_pillar_downweight_consecutive_months=engine_data.get("cb_pillar_downweight_consecutive_months", 4),
            cb_pillar_downweight_reduction=engine_data.get("cb_pillar_downweight_reduction", 0.333),
            cb_pillar_downweight_recovery_months=engine_data.get("cb_pillar_downweight_recovery_months", 3),
            cb_pillar_downweight_rolling_window=engine_data.get("cb_pillar_downweight_rolling_window", 252),
            cb_pillar_downweight_redistribute=engine_data.get("cb_pillar_downweight_redistribute", ["bond", "gold"]),
            # C3: ML 模型切换器 (R89)
            ml_model_switcher_enabled=engine_data.get("ml_model_switcher_enabled", False),
            ml_model_switcher_sr_threshold=engine_data.get("ml_model_switcher_sr_threshold", 0.5),
            ml_model_switcher_consecutive_months=engine_data.get("ml_model_switcher_consecutive_months", 2),
            ml_model_switcher_recovery_sr=engine_data.get("ml_model_switcher_recovery_sr", 1.0),
            ml_model_switcher_recovery_months=engine_data.get("ml_model_switcher_recovery_months", 2),
            ml_model_switcher_transition_days=engine_data.get("ml_model_switcher_transition_days", 21),
            ml_model_switcher_rolling_window=engine_data.get("ml_model_switcher_rolling_window", 60),
            ml_model_switcher_primary_model=engine_data.get("ml_model_switcher_primary_model", "models/ml_csi2000_lgb_fund"),
            ml_model_switcher_backup_model=engine_data.get("ml_model_switcher_backup_model", "models/ml_csi1000_lgb_fund"),
            # Barra 风格监控
            barra_monitor_enabled=engine_data.get("barra_monitor_enabled", False),
            barra_alert_thresholds=engine_data.get("barra_alert_thresholds", {}),
        )

    @classmethod
    def from_db(cls, portfolio_id: str, db_path: str | None = None) -> "PaperEngineConfig":
        """从数据库 paper_portfolios 表加载配置。

        Args:
            portfolio_id: 组合 ID
            db_path: 数据库路径（缺省使用默认路径）

        Returns:
            PaperEngineConfig 实例

        Raises:
            ValueError: 组合 ID 不存在或已停用
        """
        from aipha.backtest.result_store import ResultStore

        store = ResultStore(db_path)
        portfolio = store.get_paper_portfolio(portfolio_id)
        if not portfolio:
            available = [p["portfolio_id"] for p in store.list_paper_portfolios()]
            raise ValueError(
                f"组合 '{portfolio_id}' 不存在或已停用。"
                f"可用组合: {available}"
            )

        strategies = portfolio.get("strategies", [])
        weights = portfolio.get("weights", {})

        # 使用第一个策略作为主策略
        primary_strategy = strategies[0] if strategies else ""

        return cls(
            strategy_name=primary_strategy,
            strategy_params={"strategies": strategies, "weights": weights},
            initial_capital=portfolio.get("initial_capital", 1_000_000.0),
            state_dir=resolve_path(
                portfolio.get("state_dir"), "data", "paper_state", portfolio_id
            ),
            # P1-4: 从 MarketRules 动态派生，不硬编码 (§13.1)
        )


class PaperEngine:
    """Paper Trading 引擎

    生命周期：
    1. init: 创建引擎实例
    2. run(): 启动异步事件循环（阻塞直到停止）
       - 等待开盘 → 开始轮询 → 午休暂停 → 下午继续 → 收盘结算
    3. stop(): 优雅停止

    示例::

        engine = PaperEngine(config, strategy)
        await engine.run()  # 阻塞到收盘或手动停止
    """

    def __init__(
        self,
        config: PaperEngineConfig,
        strategy: Any,  # Strategy 实例
        *,
        calendar: CNStockCalendar | None = None,
    ):
        self._config = config
        self._strategy = strategy
        self._initial_portfolio_weights = dict(config.portfolio_weights)
        self._calendar = calendar or CNStockCalendar()
        self._running = False
        self._stop_event = asyncio.Event()
        self._pillar_orchestrator = (
            strategy if hasattr(strategy, "get_pillar_daily_return") else None
        )

        # 构建 RunnerConfig — §4.3 滑点模型与回测共享
        _slippage_model = config.create_slippage_model()
        runner_config = RunnerConfig(
            mode=RunnerMode.PAPER,
            initial_capital=Decimal(str(config.initial_capital)),
            lot_size=config.lot_size,
            t_plus_n=config.t_plus_n,
            commission_rate=Decimal(str(config.commission_rate)),
            commission_min=Decimal(str(config.commission_min)),
            stamp_tax_rate=Decimal(str(config.stamp_tax_rate)),
            transfer_fee_rate=Decimal(str(config.transfer_fee_rate)),
            slippage_bps=config.slippage_bps,
            fill_model_name=config.fill_model_name,
            slippage_model=_slippage_model,
            audit_log_enabled=True,
            audit_log_dir=config.audit_log_dir,
        )

        # 创建 LiveRunner（paper mode）
        self._runner = LiveRunner.from_paper_mode(runner_config, strategy)
        self._runner.on_fill_callback(self._handle_fill_report)

        # 构建实时数据源
        ak_config = AKShareRealtimeConfig(
            market_type=config.market_type,
            poll_interval=config.poll_interval,
            bar_interval_sec=config.bar_interval_sec,
        )
        self._provider = AKShareRealtimeProvider(
            config=ak_config,
            calendar=self._calendar,
            rate_limiter=get_akshare_limiter(),
        )

        # 状态持久化管理器
        self._state_manager = StateManager(
            state_dir=config.state_dir,
            state_file=config.state_file,
        )
        # B7: 确保状态/审计目录在启动时就存在
        config.state_dir.mkdir(parents=True, exist_ok=True)
        config.audit_log_dir.mkdir(parents=True, exist_ok=True)

        # --- 动态标的池 ---
        self._universe: CBUniverseProvider | None = None
        if config.universe_config.enabled:
            self._universe = CBUniverseProvider(config.universe_config)
            logger.info(
                f"动态标的池已启用: max_price={config.universe_config.max_price}, "
                f"min_amount={config.universe_config.min_amount}, "
                f"refresh_interval={config.universe_config.refresh_interval_sec}s"
            )

        # --- 交易监控 ---
        self._monitor: TradingMonitor | None = None
        if config.monitoring_enabled:
            self._monitor = TradingMonitor()
            self._init_monitoring()

        # --- 指标收集 ---
        self._metrics: MetricsCollector | None = None
        if config.metrics_enabled:
            self._metrics = MetricsCollector(
                config=config.metrics_config,
                initial_capital=config.initial_capital,
                strategy_name=config.strategy_name,
            )

        # R92 T2-2: 行业中性约束（需要行业数据支持）
        from aipha.risk.ml_risk_controls import IndustryConstraint
        self._industry_constraint = IndustryConstraint(
            max_pct=config.max_industry_pct,
        )
        logger.info(
            f"行业中性约束已初始化: max_pct={config.max_industry_pct:.0%} "
            f"(需接入行业数据管道方可生效)"
        )

        # 统计
        self._bars_received = 0
        self._session_start: datetime | None = None
        self._last_heartbeat: datetime | None = None
        self._heartbeat_interval_sec: int = 60
        self._last_bar_time: datetime | None = None
        self._last_fill_time: datetime | None = None
        self._peak_nav: float = config.initial_capital

        # --- 统一仓位缩放器 (§4.3.1 三路径共享) ---
        self._position_sizer: UnifiedPositionSizer | None = None
        self._risk_scale: float = 1.0  # 当前仓位缩放因子
        # P2-5 修复: 使用 deque 限制内存增长
        from collections import deque as _deque
        self._nav_history: deque[float] = _deque(maxlen=2000)
        if config.ml_risk_controls_enabled:
            self._position_sizer = UnifiedPositionSizer.from_paper_config(
                vol_target=config.ml_risk_vol_target,
                vol_lookback=config.ml_risk_vol_lookback,
                mdd_levels=config.ml_risk_mdd_levels,
            )
            logger.info(
                f"UnifiedPositionSizer 已启用: vol_target={config.ml_risk_vol_target}, "
                f"vol_lookback={config.ml_risk_vol_lookback}"
            )

        # --- B1: ML Rolling SR 降权触发器 (R88) ---
        self._sr_downweight: SharpeDownweightTrigger | None = None
        self._ml_nav_history: list[float] = []  # R92 T2-1: ML pillar 独立 NAV 序列
        if config.ml_sr_downweight_enabled:
            self._sr_downweight = SharpeDownweightTrigger(
                lookback=config.ml_sr_downweight_lookback,
                sr_threshold=config.ml_sr_downweight_threshold,
                normal_weight=config.ml_sr_downweight_normal,
                reduced_weight=config.ml_sr_downweight_reduced,
            )
            logger.info(
                f"ML SR 降权触发器已启用: lookback={config.ml_sr_downweight_lookback}, "
                f"threshold={config.ml_sr_downweight_threshold}, "
                f"normal={config.ml_sr_downweight_normal}, "
                f"reduced={config.ml_sr_downweight_reduced}"
            )

        # --- C2: CB Pillar 动态降权 (R89) ---
        self._cb_pillar_monitor: PillarDownweightMonitor | None = None
        self._cb_nav_history: list[float] = []  # CB pillar 独立 NAV 序列
        if config.cb_pillar_downweight_enabled:
            from aipha.portfolio.pillar_monitor import (
                DownweightRule,
                PillarDownweightMonitor,
            )

            rule = DownweightRule(
                sr_threshold=config.cb_pillar_downweight_sr_threshold,
                consecutive_months=config.cb_pillar_downweight_consecutive_months,
                weight_reduction=config.cb_pillar_downweight_reduction,
                recovery_months=config.cb_pillar_downweight_recovery_months,
                rolling_window_days=config.cb_pillar_downweight_rolling_window,
            )
            self._cb_pillar_monitor = PillarDownweightMonitor(rule=rule)
            # 用初始权重初始化 CB NAV（假设 NAV=1.0 起步）
            self._cb_nav_history.append(1.0)
            logger.info(
                f"CB Pillar 动态降权已启用: SR阈值={rule.sr_threshold}, "
                f"连续月数={rule.consecutive_months}, "
                f"降权幅度={rule.weight_reduction:.0%}, "
                f"恢复月数={rule.recovery_months}"
            )

        # --- Barra 风格暴露监控 (O6) ---
        self._barra_monitor: BarraStyleMonitor | None = None
        if config.barra_monitor_enabled:
            barra_cfg = StyleMonitorConfig(
                alert_thresholds=config.barra_alert_thresholds,
            ) if config.barra_alert_thresholds else StyleMonitorConfig()
            self._barra_monitor = BarraStyleMonitor(config=barra_cfg)
            logger.info("Barra 风格暴露监控已启用")

        # --- C3: ML 模型切换器 (R89) ---
        self._ml_model_switcher: MLModelSwitcher | None = None
        if config.ml_model_switcher_enabled:
            from aipha.risk.ml_model_switcher import (
                MLModelSwitcher,
                MLModelSwitcherConfig,
            )

            switcher_cfg = MLModelSwitcherConfig(
                sr_threshold=config.ml_model_switcher_sr_threshold,
                consecutive_months=config.ml_model_switcher_consecutive_months,
                recovery_sr_threshold=config.ml_model_switcher_recovery_sr,
                recovery_months=config.ml_model_switcher_recovery_months,
                transition_days=config.ml_model_switcher_transition_days,
                rolling_window_days=config.ml_model_switcher_rolling_window,
                primary_model_dir=config.ml_model_switcher_primary_model,
                backup_model_dir=config.ml_model_switcher_backup_model,
            )
            self._ml_model_switcher = MLModelSwitcher(switcher_cfg)
            logger.info(
                f"ML 模型切换器已启用: "
                f"SR阈值={switcher_cfg.sr_threshold}, "
                f"primary={switcher_cfg.primary_model_dir}, "
                f"backup={switcher_cfg.backup_model_dir}"
            )

    @property
    def runner(self) -> LiveRunner:
        """获取 LiveRunner 实例（用于查询状态）"""
        return self._runner

    @property
    def universe(self) -> CBUniverseProvider | None:
        """获取标的池管理器"""
        return self._universe

    @property
    def monitor(self) -> TradingMonitor | None:
        """获取交易监控器"""
        return self._monitor

    @property
    def barra_monitor(self) -> BarraStyleMonitor | None:
        """获取 Barra 风格暴露监控器"""
        return self._barra_monitor

    @property
    def metrics(self) -> MetricsCollector | None:
        """获取指标收集器"""
        return self._metrics

    @property
    def risk_scale(self) -> float:
        """当前 ML 风控仓位缩放因子 (0~1.5)"""
        return self._risk_scale

    def _handle_fill_report(self, report: Any) -> None:
        """处理成交回报，转发给组合编排层（含交易费用）。

        R92 修复:
        - commission=None 防护 (T3-3)
        - 传递 market_date 而非依赖 date.today() (T1-3)
        """
        fill_time = getattr(report, "fill_time", datetime.now())
        self._last_fill_time = fill_time
        if self._pillar_orchestrator is None:
            return
        try:
            raw_commission = getattr(report, "commission", None)
            commission = float(raw_commission) if raw_commission is not None else 0.0
            # 从 fill_time 提取市场日期，避免 date.today() 偏差
            if hasattr(fill_time, "date"):
                market_date = fill_time.date()
            else:
                market_date = date.today()
            self._pillar_orchestrator.on_fill(
                report.symbol,
                report.side,
                report.fill_qty,
                report.fill_price,
                commission=commission,
                market_date=market_date,
            )
        except Exception as e:
            logger.error(f"Pillar fill 回调异常: {e}")

    def _init_monitoring(self) -> None:
        """初始化交易监控告警规则"""
        if not self._monitor:
            return

        cfg = self._config

        # 回撤告警
        self._monitor.add_rule(MaxDrawdownAlert(
            threshold=cfg.alert_drawdown_threshold,
            level=AlertLevel.CRITICAL,
        ))

        # 日内亏损告警
        self._monitor.add_rule(DailyLossAlert(
            max_loss_pct=cfg.alert_daily_loss_threshold,
            level=AlertLevel.CRITICAL,
        ))

        # 数据中断告警
        self._monitor.add_rule(DataStaleAlert(
            stale_seconds=cfg.alert_data_stale_seconds,
            level=AlertLevel.CRITICAL,
        ))

        # 策略不活跃告警
        self._monitor.add_rule(StrategyInactivityAlert(
            inactive_minutes=cfg.alert_inactivity_minutes,
            level=AlertLevel.WARNING,
        ))

        # 日志通知器
        self._monitor.add_notifier(LogNotifier())

        # 飞书通知器（如配置了 FEISHU_WEBHOOK 环境变量）
        feishu_url = os.environ.get("FEISHU_WEBHOOK", "").strip()
        if feishu_url:
            from aipha.monitor.notifiers import FeishuConfig, FeishuChannel
            feishu_ch = FeishuChannel(FeishuConfig(webhook_url=feishu_url))
            # 将飞书渠道包装为 AlertNotifier 适配器
            from aipha.monitor.alerts import CallbackNotifier
            import asyncio as _asyncio

            def _feishu_callback(alert):
                from aipha.monitor.notifiers import AlertMessage, AlertCategory
                msg = AlertMessage(
                    title=alert.title,
                    content=alert.message,
                    level=alert.level,
                    category=AlertCategory.SYSTEM,
                    source=alert.source,
                )
                _send = _asyncio.wait_for(feishu_ch.send(msg), timeout=5.0)
                try:
                    loop = _asyncio.get_running_loop()
                    loop.create_task(_send)
                except RuntimeError:
                    try:
                        _asyncio.run(_send)
                    except Exception:
                        logger.warning("飞书通知发送失败(非事件循环)", exc_info=True)

            self._monitor.add_notifier(CallbackNotifier(_feishu_callback))
            logger.info("飞书通知已启用（FEISHU_WEBHOOK）")

        logger.info(
            f"交易监控已初始化: {self._monitor.rules_count} 条规则, "
            f"drawdown>{cfg.alert_drawdown_threshold:.0%}, "
            f"data_stale>{cfg.alert_data_stale_seconds:.0f}s"
        )

    async def run(self) -> None:
        """主事件循环

        流程：
        0. 检查 FREEZE 标记（emergency_stop 集成）
        1. 尝试恢复上次状态
        2. 连接数据源
        3. 启动 LiveRunner
        4. 等待开盘
        5. 订阅行情 → 轮询 → 策略处理
        6. 收盘后执行 EOD 结算
        7. 保存状态
        """
        # §13.13 H5: FREEZE marker check
        if _check_freeze(self._config.state_dir, self._config.strategy_name):
            return

        self._running = True
        self._stop_event.clear()

        logger.info(
            f"Paper Engine 启动: strategy={self._config.strategy_name}, "
            f"symbols={len(self._config.symbols)}, "
            f"capital={self._config.initial_capital:,.0f}"
        )

        # 恢复状态（如有）
        self._load_state()

        # 注册信号处理
        self._register_signal_handlers()

        try:
            # 连接数据源（含重试）
            connected = await self._connect_with_retry(max_retries=5, base_delay=2.0)
            if not connected:
                logger.error("数据源连接失败（已重试），退出")
                return

            # 启动 LiveRunner
            self._runner.start()
            self._session_start = datetime.now()

            # 注册 domain bar 回调 → LiveRunner.on_bar()
            self._provider.on_domain_bar(self._on_domain_bar)

            # 市场调度循环
            await self._market_schedule_loop()

        except asyncio.CancelledError:
            logger.info("Paper Engine 被取消")
        except Exception as e:
            logger.error(f"Paper Engine 异常: {e}", exc_info=True)
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """优雅停止"""
        logger.info("Paper Engine 停止中...")
        self._running = False
        self._stop_event.set()

    async def _connect_with_retry(
        self,
        max_retries: int = 5,
        base_delay: float = 2.0,
    ) -> bool:
        """带指数退避的连接重试

        Args:
            max_retries: 最大重试次数
            base_delay: 首次重试延迟（秒），后续指数递增

        Returns:
            是否成功连接
        """
        for attempt in range(1, max_retries + 1):
            try:
                connected = await self._provider.connect()
                if connected:
                    logger.info(f"数据源连接成功 (第 {attempt} 次尝试)")
                    return True
            except Exception as e:
                logger.warning(f"数据源连接异常 (第 {attempt} 次): {e}")

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))  # 2, 4, 8, 16s
                logger.info(f"  {delay:.0f}s 后重试...")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=delay
                    )
                    if self._stop_event.is_set():
                        return False
                except asyncio.TimeoutError:
                    pass

        logger.error(f"数据源连接失败，已重试 {max_retries} 次")
        return False

    async def _market_schedule_loop(self) -> None:
        """市场时段调度循环

        持续运行直到停止信号，根据当前市场时段决定行为：
        - CLOSED: 等待下个交易日
        - MORNING_CALL_AUCTION: 准备开盘
        - MORNING_CONTINUOUS / AFTERNOON_CONTINUOUS: 订阅行情
        - LUNCH_BREAK: 暂停轮询
        - CLOSING_CALL_AUCTION: 准备收盘
        """
        subscribed = False

        while self._running and not self._stop_event.is_set():
            now = datetime.now()
            session = self._calendar.get_session(now)

            if session in (
                TradingSession.MORNING_CONTINUOUS,
                TradingSession.AFTERNOON_CONTINUOUS,
                TradingSession.MORNING_CALL_AUCTION,
                TradingSession.CLOSING_CALL_AUCTION,
            ):
                # 交易时段：确保已订阅
                if not subscribed:
                    # 盘前刷新标的池
                    await self._refresh_universe()
                    await self._subscribe_symbols()
                    subscribed = True
                    logger.info(f"交易时段开始: {session.value}")

                # 检查 provider 是否异常停止（poll loop died）
                if (
                    self._provider._poll_task is not None
                    and self._provider._poll_task.done()
                    and self._provider.get_status() == StreamStatus.ERROR
                ):
                    logger.warning("数据源 poll loop 异常停止，执行重连...")
                    await self._reconnect_provider()
                    subscribed = False  # 重新订阅

                # 心跳日志 + 监控 + 指标
                self._emit_heartbeat(now)

            elif session == TradingSession.LUNCH_BREAK:
                # 午休：取消订阅，暂停轮询
                if subscribed:
                    await self._unsubscribe_symbols()
                    subscribed = False
                    logger.info("午休，暂停行情轮询")

                    # 午休时刷新标的池（若需要）
                    if self._universe and self._universe.needs_refresh():
                        await self._refresh_universe()

            elif session == TradingSession.CLOSED:
                # 收盘：执行 EOD 结算
                if subscribed:
                    await self._unsubscribe_symbols()
                    subscribed = False
                    await self._eod_settlement()

                # 检查是否应该继续等到第二天
                # 如果当前时间晚于 15:30，等到明天 9:00
                if now.time() > time(15, 30):
                    next_open = self._next_market_open(now)
                    wait_seconds = (next_open - now).total_seconds()
                    if wait_seconds > 0:
                        logger.info(
                            f"等待下一个交易日: {next_open.strftime('%Y-%m-%d %H:%M')}, "
                            f"等待 {wait_seconds/3600:.1f} 小时"
                        )
                        try:
                            await asyncio.wait_for(
                                self._stop_event.wait(),
                                timeout=min(wait_seconds, 300),  # 最多等 5 分钟检查一次
                            )
                            if self._stop_event.is_set():
                                break
                        except asyncio.TimeoutError:
                            pass
                        continue

            # 短间隔检查（10s）
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=10.0)
                if self._stop_event.is_set():
                    break
            except asyncio.TimeoutError:
                pass

    def _on_domain_bar(self, bar: Bar) -> None:
        """处理从 Provider 转换后的 Domain Bar"""
        self._bars_received += 1
        self._last_bar_time = datetime.now()

        fills_before = self._runner._state.total_fills
        try:
            self._runner.on_bar(bar)
        except Exception as e:
            logger.error(f"处理 Bar 异常: {bar.symbol} @ {bar.end_ts}: {e}")

        # 检测是否有新成交
        fills_after = self._runner._state.total_fills
        if fills_after > fills_before:
            self._last_fill_time = datetime.now()

        # 每笔成交后立即保存（成交是低频事件）
        if fills_after > getattr(self, "_last_fills_count", 0):
            self._last_fills_count = fills_after
            self._save_state()
        # 兜底：定期保存
        elif self._bars_received % 60 == 0:
            self._save_state()

    def _emit_heartbeat(self, now: datetime) -> None:
        """周期性心跳日志（默认 60s）+ 监控更新 + 指标收集"""
        if (
            self._last_heartbeat is not None
            and (now - self._last_heartbeat).total_seconds()
            < self._heartbeat_interval_sec
        ):
            return

        self._last_heartbeat = now
        try:
            snapshot = self._runner.get_account_snapshot()
            nav = float(snapshot.total_equity)
            n_pos = len(snapshot.positions)
            cash = float(snapshot.cash)
            # P1-5: 缓存最近有效 NAV，避免异常时回退到 0
            self._last_valid_nav = nav
            self._last_valid_pos_count = n_pos
            self._last_valid_cash = cash
        except Exception as e:
            # P1-5 修复: 使用上次有效 NAV 而非 0，避免 MDD 误判
            nav = getattr(self, '_last_valid_nav', 0.0)
            n_pos = getattr(self, '_last_valid_pos_count', 0)
            cash = getattr(self, '_last_valid_cash', 0.0)
            logger.warning("NAV 快照异常，使用上次有效值 %.2f: %s", nav, e)

        logger.info(
            f"♥ HEARTBEAT bars={self._bars_received} "
            f"NAV={nav:,.2f} positions={n_pos} "
            f"provider={self._provider.get_status().value}"
        )

        # --- 更新监控 ---
        if self._monitor and nav > 0:
            self._peak_nav = max(self._peak_nav, nav)
            drawdown = (
                (self._peak_nav - nav) / self._peak_nav
                if self._peak_nav > 0 else 0.0
            )

            # 构建带持仓市值的 positions dict
            positions_value: dict[str, float] = {}
            for sym, qty in snapshot.positions.items():
                # R92 T3-2: 使用 Gateway 最新行情价格，而非硬编码 ¥100
                price = self._runner.gateway._latest_prices.get(sym, 100.0)
                positions_value[sym] = float(qty) * price

            portfolio_snap = PortfolioSnapshot(
                timestamp=now,
                total_value=nav,
                cash=cash,
                positions=positions_value,
                drawdown=drawdown,
                metadata={
                    "last_bar_time": (
                        self._last_bar_time.isoformat()
                        if self._last_bar_time else None
                    ),
                    "last_fill_time": (
                        self._last_fill_time.isoformat()
                        if self._last_fill_time else None
                    ),
                },
            )

            triggered = self._monitor.update(portfolio_snap)
            if triggered:
                for alert in triggered:
                    logger.warning(
                        f"🚨 [{alert.level.value}] {alert.title}: {alert.message}"
                    )

        # --- 更新指标 ---
        if self._metrics and nav > 0:
            self._metrics.update(
                nav=nav,
                cash=cash,
                n_positions=n_pos,
                n_fills=self._runner._state.total_fills,
                n_orders=self._runner._state.total_orders_submitted,
                n_bars=self._bars_received,
                timestamp=now,
            )

        # --- 统一仓位缩放 (§4.3.1 三路径共享) ---
        if self._position_sizer and nav > 0:
            self._nav_history.append(nav)
            if len(self._nav_history) >= 2:
                # 转换 NAV 序列为日收益率序列
                nav_arr = list(self._nav_history)
                daily_rets = [
                    nav_arr[i] / nav_arr[i - 1] - 1
                    for i in range(1, len(nav_arr))
                ]
                scale_info = self._position_sizer.compute_scale_detail(
                    daily_rets, nav,
                )
                old_scale = self._risk_scale
                self._risk_scale = scale_info["final_scale"]
                if abs(self._risk_scale - old_scale) > 0.01:
                    logger.info(
                        f"📉 风控缩放更新: {old_scale:.2f}→"
                        f"{self._risk_scale:.2f} "
                        f"(MDD={scale_info['mdd']:.1%}, "
                        f"mdd_s={scale_info['mdd_scale']:.2f}, "
                        f"vol_s={scale_info['vol_scale']:.2f})"
                    )
                # 注入到 LiveRunner 执行链
                self._runner.risk_position_scale = self._risk_scale

        # --- B1: ML Rolling SR 降权触发器 (R88) ---
        # R92 T2-1: 使用 ML pillar 独立 NAV 序列，而非整体组合 NAV
        if self._pillar_orchestrator is not None:
            ml_nav = self._pillar_orchestrator.get_pillar_nav("ml")
            if ml_nav > 0:
                self._ml_nav_history.append(ml_nav)
        if self._sr_downweight and len(self._ml_nav_history) >= 2:
            nav_s = pd.Series(self._ml_nav_history)
            ml_weight, sr_info = self._sr_downweight.evaluate(nav_s)
            if sr_info["triggered"]:
                logger.warning(
                    f"ML SR 降权触发: rolling_sr_60d={sr_info['rolling_sr_60d']}, "
                    f"ML权重 {sr_info['normal_weight']:.0%}→{sr_info['reduced_weight']:.0%}"
                )
            # 更新 portfolio_weights 中的 ml 权重
            if self._config.portfolio_weights.get("ml") is not None:
                self._config.portfolio_weights["ml"] = ml_weight
                if self._pillar_orchestrator is not None:
                    self._pillar_orchestrator.update_pillar_weight("ml", ml_weight)

        # --- C2: CB Pillar 动态降权 (R89) ---
        if self._cb_pillar_monitor and nav > 0:
            cb_weight = self._config.portfolio_weights.get("cb", 0)
            if cb_weight > 0 and len(self._nav_history) >= 2:
                if self._pillar_orchestrator is not None:
                    cb_daily_ret = self._pillar_orchestrator.get_pillar_daily_return("cb")
                else:
                    total_ret = (
                        self._nav_history[-1] / self._nav_history[-2] - 1.0
                    )
                    cb_daily_ret = total_ret
                scale = self._cb_pillar_monitor.update(cb_daily_ret)
                if scale < 1.0 and not getattr(self, "_cb_downweight_logged", False):
                    self._apply_cb_downweight(scale)
                    self._cb_downweight_logged = True
                elif scale >= 1.0 and getattr(self, "_cb_downweight_logged", False):
                    self._apply_cb_recovery()
                    self._cb_downweight_logged = False

        # --- C3: ML 模型切换器 (R89) ---
        self._update_ml_model_switcher(nav)

        if self._pillar_orchestrator is not None:
            self._pillar_orchestrator.snapshot_prev_navs()

    def _apply_cb_downweight(self, scale: float) -> None:
        """CB 降权后将多余权重重分配给防御性资产。"""
        weights = self._config.portfolio_weights
        old_cb = weights.get("cb", 0)
        new_cb = old_cb * scale
        excess = old_cb - new_cb
        targets = self._config.cb_pillar_downweight_redistribute
        valid_targets = [t for t in targets if t in weights and t != "cb"]
        if valid_targets and excess > 0:
            per_target = excess / len(valid_targets)
            for t in valid_targets:
                weights[t] = weights[t] + per_target
                if self._pillar_orchestrator is not None:
                    self._pillar_orchestrator.update_pillar_weight(t, weights[t])
        weights["cb"] = new_cb
        if self._pillar_orchestrator is not None:
            self._pillar_orchestrator.update_pillar_weight("cb", new_cb)
        logger.warning(
            f"CB Pillar 降权: {old_cb:.1%}→{new_cb:.1%}, "
            f"多余 {excess:.1%} 分配给 {valid_targets}"
        )

    def _apply_cb_recovery(self) -> None:
        """CB 恢复权重至原始配置。"""
        weights = self._config.portfolio_weights
        old_cb = weights.get("cb", 0)
        original_cb = self._initial_portfolio_weights.get("cb", 0.30)
        if old_cb >= original_cb:
            return
        excess = original_cb - old_cb
        targets = self._config.cb_pillar_downweight_redistribute
        valid_targets = [t for t in targets if t in weights and t != "cb"]
        if valid_targets and excess > 0:
            per_target = excess / len(valid_targets)
            for t in valid_targets:
                weights[t] = max(0, weights[t] - per_target)
                if self._pillar_orchestrator is not None:
                    self._pillar_orchestrator.update_pillar_weight(t, weights[t])
        weights["cb"] = original_cb
        if self._pillar_orchestrator is not None:
            self._pillar_orchestrator.update_pillar_weight("cb", original_cb)
        logger.info(
            f"CB Pillar 恢复: {old_cb:.1%}→{original_cb:.1%}"
        )

        # --- C3: ML 模型切换器 (R89) ---
    def _update_ml_model_switcher(self, nav: float) -> None:
        """更新 ML 模型切换器状态。"""
        if not self._ml_model_switcher or nav <= 0:
            return
        # R92 T2-1: 使用 ML pillar 日收益率，而非整体组合 NAV
        if self._pillar_orchestrator is not None:
            ml_daily_ret = self._pillar_orchestrator.get_pillar_daily_return("ml")
        elif len(self._nav_history) >= 2:
            ml_daily_ret = self._nav_history[-1] / self._nav_history[-2] - 1.0
        else:
            return
        from aipha.risk.ml_model_switcher import SwitcherState

        old_state = self._ml_model_switcher.status.state
        new_state = self._ml_model_switcher.update(ml_daily_ret)
        if new_state != old_state:
            pw, bw = self._ml_model_switcher.get_model_weights()
            logger.warning(
                f"ML Model Switcher: {old_state.value}→{new_state.value}, "
                f"primary={pw:.0%}, backup={bw:.0%}"
            )
            if self._pillar_orchestrator is not None:
                target_model = (
                    self._config.ml_model_switcher_backup_model
                    if bw >= pw else self._config.ml_model_switcher_primary_model
                )
                self._pillar_orchestrator.switch_ml_model(target_model)

    async def _reconnect_provider(self) -> None:
        """数据源异常后重连"""
        logger.info("执行数据源重连...")
        try:
            await self._provider.disconnect()
        except Exception:  # silent-pass-exempt: cleanup before reconnect
            pass

        connected = await self._connect_with_retry(max_retries=3, base_delay=5.0)
        if connected:
            logger.info("数据源重连成功")
        else:
            logger.error("数据源重连失败，引擎将停止")
            await self.stop()

    async def _subscribe_symbols(self) -> None:
        """订阅标的行情"""
        symbols = self._get_active_symbols()
        if not symbols:
            logger.warning("未配置标的列表，跳过订阅")
            return

        await self._provider.subscribe(
            symbols,
            [QuoteType.LEVEL1, QuoteType.BAR_1M],
        )
        logger.info(f"已订阅 {len(symbols)} 个标的")

    async def _unsubscribe_symbols(self) -> None:
        """取消订阅"""
        symbols = self._get_active_symbols()
        if symbols:
            await self._provider.unsubscribe(
                symbols,
                [QuoteType.LEVEL1, QuoteType.BAR_1M],
            )

    def _get_active_symbols(self) -> list[str]:
        """获取当前活跃标的列表

        优先使用动态标的池，降级到静态配置。
        """
        if self._universe and self._universe.symbol_count > 0:
            return self._universe.current_symbols
        return self._config.symbols

    async def _refresh_universe(self) -> None:
        """刷新动态标的池（异步包装同步调用）"""
        if not self._universe:
            return

        try:
            # 在线程池中执行（避免阻塞事件循环）
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._universe.refresh)

            if result.error:
                logger.error(f"标的池刷新失败: {result.error}")
                return

            if result.added or result.removed:
                logger.info(
                    f"标的池已更新: +{len(result.added)} -{len(result.removed)} "
                    f"(当前 {len(result.symbols)} 只, 耗时 {result.duration_sec:.1f}s)"
                )

                # 更新 config.symbols 保持一致
                self._config.symbols = result.symbols
            else:
                logger.debug(
                    f"标的池无变更 (维持 {len(result.symbols)} 只)"
                )

        except Exception as e:
            logger.error(f"标的池刷新异常: {e}", exc_info=True)

    async def _eod_settlement(self) -> None:
        """日终结算

        - 刷出未完成 Bar
        - 输出 EOD 日报
        - 记录日终快照
        - 输出监控/告警统计
        - 保存状态
        """
        logger.info("执行日终结算...")

        # 刷出 provider 中未完成的 bars
        remaining = self._provider._bar_synth.flush()
        for bar_data in remaining:
            domain_bar = bardata_to_domain_bar(bar_data)
            self._on_domain_bar(domain_bar)

        # 获取账户快照
        snapshot = self._runner.get_account_snapshot()
        nav = float(snapshot.total_equity)
        cash = float(snapshot.cash)
        n_pos = len(snapshot.positions)

        # 计算日收益率
        initial = self._config.initial_capital
        total_return_pct = (nav - initial) / initial * 100 if initial > 0 else 0

        # 持仓明细
        positions_str = ""
        if snapshot.positions:
            pos_lines = []
            for sym, pos in sorted(snapshot.positions.items()):
                qty = pos.get("qty", pos.get("quantity", 0))
                pos_lines.append(f"    {sym}: {qty}")
            positions_str = "\n".join(pos_lines)

        # 告警统计
        alert_summary = ""
        if self._monitor:
            n_active = self._monitor.active_alerts_count
            n_total = len(self._monitor.get_alerts())
            alert_summary = f"\n  今日告警:   {n_total:>14d} (活跃 {n_active})"

        # 标的池状态
        universe_summary = ""
        if self._universe:
            u_status = self._universe.get_status()
            universe_summary = (
                f"\n  标的池:     {u_status['symbol_count']:>14d} 只 "
                f"(刷新 {u_status['refresh_count']} 次)"
            )

        logger.info(
            f"\n{'='*60}\n"
            f"  EOD 日报 — {datetime.now().strftime('%Y-%m-%d')}\n"
            f"{'='*60}\n"
            f"  NAV:        {nav:>14,.2f}\n"
            f"  现金:       {cash:>14,.2f}\n"
            f"  持仓数:     {n_pos:>14d}\n"
            f"  总收益:     {total_return_pct:>13.2f}%\n"
            f"  今日 Bars:  {self._bars_received:>14d}\n"
            f"  总成交:     {self._runner._state.total_fills:>14d}"
            f"{alert_summary}"
            f"{universe_summary}\n"
            f"{'='*60}"
        )
        if positions_str:
            logger.info(f"  持仓明细:\n{positions_str}")

        # 保存状态
        self._save_state()

    def _next_market_open(self, now: datetime) -> datetime:
        """计算下一个交易日开盘时间"""
        d = now.date()
        for i in range(1, 15):
            next_d = d + timedelta(days=i)
            if self._calendar.is_trading_day(next_d):
                return datetime.combine(next_d, time(9, 10))
        # fallback: 明天 9:10
        return datetime.combine(d + timedelta(days=1), time(9, 10))

    async def _shutdown(self) -> None:
        """关闭引擎"""
        logger.info("Paper Engine 关闭中...")

        # 断开数据源
        try:
            await self._provider.disconnect()
        except Exception as e:
            logger.error(f"断开数据源失败: {e}")

        # 停止 LiveRunner
        try:
            self._runner.stop()
        except Exception as e:
            logger.error(f"停止 Runner 失败: {e}")

        # 最终指标写入
        if self._metrics:
            try:
                summary = self._metrics.get_summary()
                logger.info(
                    f"最终指标: NAV={summary['nav']['current']:,.2f}, "
                    f"收益={summary['nav']['total_return_pct']:.2f}%, "
                    f"最大回撤={summary['risk']['max_drawdown_pct']:.2f}%, "
                    f"Sharpe={summary['risk']['rolling_sharpe']:.3f}"
                )
            except Exception as e:
                logger.error(f"输出最终指标失败: {e}")

        # 保存最终状态
        self._save_state()

        self._running = False
        elapsed = ""
        if self._session_start:
            elapsed_sec = (datetime.now() - self._session_start).total_seconds()
            elapsed = f", 运行时长 {elapsed_sec/60:.1f} 分钟"

        logger.info(
            f"Paper Engine 已关闭: "
            f"处理 {self._bars_received} 根 Bar{elapsed}"
        )

    # ─── 状态持久化 ───

    def _save_state(self) -> None:
        """通过 StateManager 保存 Gateway 状态"""
        self._state_manager.save_from_gateway(
            self._runner.gateway,
            strategy_name=self._config.strategy_name,
            peak_nav=self._peak_nav,
            extra_meta={
                "symbols": self._config.symbols,
                "bars_received": self._bars_received,
            },
        )

    def _load_state(self) -> None:
        """通过 StateManager 恢复 Gateway 状态"""
        snapshot = self._state_manager.load()
        if snapshot is not None:
            self._state_manager.restore_to_gateway(
                self._runner.gateway, snapshot
            )
            self._bars_received = snapshot.meta.get("bars_received", 0)
            # P2-8: 从快照恢复 peak_nav，确保回撤计算连续性
            if snapshot.account.peak_nav is not None:
                self._peak_nav = snapshot.account.peak_nav

    # ─── 信号处理 ───

    def _register_signal_handlers(self) -> None:
        """注册 OS 信号处理（优雅停止）"""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._handle_signal(s)),
                )
        except (NotImplementedError, RuntimeError):
            # Windows 不支持 add_signal_handler — P1-4 修复: 记录而非静默跳过
            try:
                signal.signal(
                    signal.SIGINT,
                    lambda s, f: self._stop_event.set(),
                )
                logger.info("Windows 信号处理: SIGINT 已注册 (signal.signal)")
            except Exception as e:
                logger.warning("Windows 信号处理注册失败: %s — Ctrl+C 强制退出", e)

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """处理 OS 信号"""
        logger.info(f"收到信号 {sig.name}，优雅停止...")
        await self.stop()


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口辅助函数
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_strategy_fallback(
    config: PaperEngineConfig,
    config_path: Path,
) -> str:
    """多策略/多Pillar 模式下，找到首选注册子策略名。

    R88 降级模式：当顶层 strategy_name（如 "plan_a_5comp"、"multi_strategy"）
    未在 StrategyRegistry 中注册时，从配置中提取权重最高的子策略。

    Returns:
        子策略注册名（如 "small_size"），或空字符串表示无法解析
    """
    from aipha.strategy.registry import StrategyRegistry
    registry = StrategyRegistry()

    # 1) 多 Pillar 模式：从 CB pillar 的 sub_strategies 中取最高权重的
    if config.pillar_configs:
        cb_pillar = config.pillar_configs.get("cb")
        if cb_pillar and cb_pillar.sub_strategies:
            best = max(cb_pillar.sub_strategies, key=lambda s: s.get("weight", 0))
            name = best.get("name", "")
            if registry.get(name):
                return name
        # Fallback: 任意 pillar 的 strategy_name
        for _key, pc in config.pillar_configs.items():
            if pc.strategy_name and registry.get(pc.strategy_name):
                return pc.strategy_name

    # 2) multi_strategy 模式：读取 YAML 中的 multi_strategy.strategies
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    ms = raw.get("multi_strategy", {})
    strats = ms.get("strategies", [])
    if strats:
        # 按 weight 降序，找第一个已注册的
        sorted_strats = sorted(strats, key=lambda s: s.get("weight", 0), reverse=True)
        for s in sorted_strats:
            name = s.get("name", "")
            if registry.get(name):
                return name

    return ""


def _extract_sub_params(
    config: PaperEngineConfig,
    config_path: Path,
    fallback_name: str,
) -> dict:
    """从配置中提取指定子策略的参数。"""
    # 1) Pillar sub_strategies
    if config.pillar_configs:
        cb_pillar = config.pillar_configs.get("cb")
        if cb_pillar:
            for s in cb_pillar.sub_strategies:
                if s.get("name") == fallback_name:
                    return s.get("params", {})

    # 2) multi_strategy.strategies
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    ms = raw.get("multi_strategy", {})
    for s in ms.get("strategies", []):
        if s.get("name") == fallback_name:
            return s.get("params", {})

    return {}


def _check_freeze(state_dir: Path, combo_name: str) -> bool:
    """Check if a FREEZE marker exists (§13.13 emergency_stop integration).

    Returns True if frozen (caller must abort).
    """
    freeze_path = state_dir / "FREEZE"
    if freeze_path.exists():
        logger.error(
            "FREEZE marker detected at %s — combo '%s' is emergency-stopped. "
            "Use 'emergency_stop.py --unfreeze' to lift. Aborting.",
            freeze_path, combo_name,
        )
        return True
    return False


def run_paper_trading(config_path: Path) -> None:
    """从命令行启动 Paper Trading

    支持两种模式：
    1. 单策略模式：strategy_name + symbols（原有逻辑）
    2. 多 Pillar 模式：pillars: 配置（plan_a_5comp 使用）

    Args:
        config_path: YAML 配置文件路径
    """
    from aipha.strategy.registry import StrategyRegistry

    config = PaperEngineConfig.from_yaml(config_path)

    # §13.13 H5: FREEZE marker check — prevent restart of emergency-stopped combo
    if _check_freeze(config.state_dir, config.strategy_name):
        return

    registry = StrategyRegistry()

    if not config.strategy_name:
        raise ValueError("配置文件缺少 strategy_name")

    # B4: 多 Pillar 模式 — pillars 提供标的，无需顶层 symbols
    has_pillars = bool(config.pillar_configs)
    has_symbols = bool(config.symbols)
    has_universe = config.universe_config.enabled

    if not has_symbols and not has_universe and not has_pillars:
        raise ValueError(
            "配置文件缺少 symbols 列表（或启用 universe 动态标的池，"
            "或配置 pillars 子组件）"
        )

    if has_pillars:
        logger.info(
            f"多 Pillar 模式: {len(config.pillar_configs)} 个子组件 "
            f"({', '.join(config.pillar_configs.keys())})"
        )
        for key, pc in config.pillar_configs.items():
            logger.info(f"  {key}: weight={pc.weight:.0%}, "
                        f"market={pc.market_type}, "
                        f"symbols={pc.static_symbols or '(dynamic)'}")

    # 多 Pillar 模式：直接使用 PillarOrchestrator，禁止降级为单策略
    if has_pillars:
        from aipha.runner.pillar_orchestrator import PillarOrchestrator

        strategy = PillarOrchestrator(
            pillar_configs=config.pillar_configs,
            portfolio_weights=config.portfolio_weights,
            total_capital=config.initial_capital,
        )
        engine = PaperEngine(config, strategy)

        logger.info(f"启动 Paper Trading: {config.strategy_name} (PillarOrchestrator)")
        logger.info(f"静态标的（pillar 汇总）: {config.symbols}")
        logger.info(f"初始资金: {config.initial_capital:,.0f}")

        _run_engine_sync(engine)
        return

    # 从注册表获取策略
    strategy_cls = registry.get(config.strategy_name)

    # R88: 多策略/多 Pillar 降级模式 — 策略名非注册名时自动回退到首选子策略
    fallback_params: dict = {}
    if strategy_cls is None:
        fallback_name = _resolve_strategy_fallback(config, config_path)
        if fallback_name:
            strategy_cls = registry.get(fallback_name)
            if strategy_cls is not None:
                logger.warning(
                    f"策略 '{config.strategy_name}' 非注册策略名, "
                    f"降级为子策略 '{fallback_name}'。"
                    "TODO(B-level): 实现 CompositeStrategy / PillarOrchestrator"
                )
                # 从 pillar/multi_strategy 中提取对应 params
                fallback_params = _extract_sub_params(config, config_path, fallback_name)

    if strategy_cls is None:
        available = registry.list_strategies()
        raise ValueError(
            f"策略 '{config.strategy_name}' 未注册。"
            f"可用策略: {available}"
        )

    # 使用配置中的策略参数实例化（降级模式使用 fallback_params）
    effective_params = fallback_params or config.strategy_params
    if effective_params:
        strategy = strategy_cls(**effective_params)
        logger.info(f"策略参数: {effective_params}")
    else:
        strategy = strategy_cls()

    engine = PaperEngine(config, strategy)

    logger.info(f"启动 Paper Trading: {config.strategy_name}")
    if has_pillars:
        logger.info(f"静态标的（pillar 汇总）: {config.symbols}")
    else:
        logger.info(f"标的: {config.symbols}")
    logger.info(f"初始资金: {config.initial_capital:,.0f}")

    _run_engine_sync(engine)


def _run_engine_sync(engine: PaperEngine) -> None:
    """Run the async engine from a sync entry point without leaking a coroutine."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_paper_trading() cannot be called inside a running event loop; "
            "await PaperEngine.run() instead"
        )
    asyncio.run(engine.run())
