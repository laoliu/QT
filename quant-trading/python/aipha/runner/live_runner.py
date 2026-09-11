"""
事件驱动实时运行器

将策略与交易网关集成，支持：
- 回测模式：历史数据驱动
- 仿真模式：Paper Trading
- 实盘模式：券商接口

设计原则：
- 统一的事件循环
- 可插拔的数据源和执行层
- 回测与实盘代码一致
- 集成审计日志和风控
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.execution.gateway import (
    AccountSnapshot,
    ExecutionReport,
    GatewayOrder,
    OrderStatus,
    OrderStatusChange,
    TickData,
    TradingGateway,
)
from aipha.execution.paper_gateway import PaperGateway
from aipha.execution.slippage import SlippageModel
from aipha.runner.bar_buffer import BarBuffer, group_bars_by_end_ts
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext
from aipha.utils.paths import KILL_SWITCH_PATH, project_path

# 可选依赖：风险约束和监控
try:
    from aipha.risk.constraints import (
        CompositeRiskManager,
        DrawdownLimit,
        PositionLimit,
        RiskConstraint,
    )
    HAS_RISK_CONSTRAINTS = True
except ImportError:
    HAS_RISK_CONSTRAINTS = False

try:
    from aipha.risk.risk_config_loader import build_risk_manager
    from aipha.backtest.config_models import RiskConfig
    HAS_RISK_FACTORY = True
except ImportError:
    HAS_RISK_FACTORY = False

try:
    from aipha.portfolio.compliance_engine import ComplianceEngine
    from aipha.execution.pretrade import run_pretrade_checks, run_compliance_checks
    HAS_COMPLIANCE = True
except ImportError:
    HAS_COMPLIANCE = False

try:
    from aipha.risk.position_reconciler import PositionReconciler
    HAS_RECONCILER = True
except ImportError:
    HAS_RECONCILER = False

try:
    from aipha.monitor.alerts import (
        AlertLevel,
        DailyLossAlert,
        MaxDrawdownAlert,
        PortfolioSnapshot,
        PositionConcentrationAlert,
        TradingMonitor,
        LogNotifier,
    )
    HAS_MONITORING = True
except ImportError:
    HAS_MONITORING = False

try:
    from aipha.execution.position_guard import (
        GuardConfig,
        OrderAction,
        PositionGuard,
        PositionSnapshot,
    )
    HAS_POSITION_GUARD = True
except ImportError:
    HAS_POSITION_GUARD = False


logger = logging.getLogger(__name__)


class RunnerMode(str, Enum):
    """运行模式"""

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass
class RunnerConfig:
    """运行器配置"""

    mode: RunnerMode = RunnerMode.PAPER

    # 资金
    initial_capital: Decimal = Decimal("1000000")

    # 交易规则
    lot_size: int = 100
    t_plus_n: int = 1

    # 费用 — §13.1: 默认值由 __post_init__ 从 MarketRulesCNEquity 派生
    commission_rate: Decimal = Decimal("-1")  # sentinel; 由 __post_init__ 覆盖
    commission_min: Decimal = Decimal("-1")
    stamp_tax_rate: Decimal = Decimal("-1")
    transfer_fee_rate: Decimal = Decimal("-1")

    # 滑点 — §13.1: 默认由 __post_init__ 从 MarketRulesCNEquity 派生
    slippage_bps: float = -1.0  # sentinel
    fill_model_name: str = "next_bar_open"
    slippage_model: SlippageModel | None = None  # §4.3 回测/模拟盘共享

    # 运行参数
    heartbeat_interval: float = 1.0  # 心跳间隔（秒）
    max_order_retry: int = 3  # 订单重试次数
    # §14.4 #9: 券商断连自动 Kill Switch 超时 (秒)
    broker_disconnect_kill_timeout: float = 120.0  # 2分钟无心跳 → kill switch

    # 审计日志配置
    audit_log_enabled: bool = True
    audit_log_dir: Path = field(default_factory=lambda: project_path("logs", "audit"))

    def __post_init__(self) -> None:
        """§13.1: 从 MarketRulesCNEquity 动态派生费率默认值."""
        if self.commission_rate == Decimal("-1"):
            from aipha.market.rules_cn_equity import MarketRulesCNEquity
            rules = MarketRulesCNEquity()
            object.__setattr__(self, "commission_rate", rules.commission_rate)
            object.__setattr__(self, "commission_min", rules.commission_min)
            object.__setattr__(self, "stamp_tax_rate", rules.stamp_tax_rate)
            object.__setattr__(self, "transfer_fee_rate", rules.transfer_fee_rate)
            object.__setattr__(self, "slippage_bps", rules.slippage_bps_default)

    # 止损配置
    stop_loss_enabled: bool = True
    max_drawdown_pct: float = 0.20  # 最大回撤 20%
    daily_loss_limit_pct: float = 0.05  # 日亏损限制 5%
    circuit_breaker_pct: float = 0.10  # 熔断阈值 10%

    # 风控约束配置
    risk_constraints_enabled: bool = True
    max_single_position_pct: float = 0.10  # 单票最大持仓 10%
    drawdown_reduce_at_pct: float = 0.10  # 10% 回撤开始减仓

    # 监控配置
    monitoring_enabled: bool = True
    alert_drawdown_threshold: float = 0.10  # 回撤告警阈值 10%
    alert_daily_loss_threshold: float = 0.03  # 日亏损告警阈值 3%
    alert_concentration_threshold: float = 0.20  # 持仓集中度阈值 20%

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "mode": self.mode.value,
            "initial_capital": float(self.initial_capital),
            "lot_size": self.lot_size,
            "t_plus_n": self.t_plus_n,
            "commission_rate": float(self.commission_rate),
            "stamp_tax_rate": float(self.stamp_tax_rate),
            "transfer_fee_rate": float(self.transfer_fee_rate),
            "slippage_bps": self.slippage_bps,
            "fill_model_name": self.fill_model_name,
            "audit_log_enabled": self.audit_log_enabled,
            "stop_loss_enabled": self.stop_loss_enabled,
            "max_drawdown_pct": self.max_drawdown_pct,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
        }

    # --- §13.1 asset-aware 工厂方法 ---

    @classmethod
    def _from_market_rules(cls, rules: Any, **overrides: Any) -> "RunnerConfig":
        """从 MarketRules 实例构建 RunnerConfig (§13.1 单一来源)."""
        defaults = {
            "lot_size": rules.lot_size,
            "t_plus_n": rules.t_plus,
            "commission_rate": rules.commission_rate,
            "commission_min": rules.commission_min,
            "stamp_tax_rate": rules.stamp_tax_rate,
            "transfer_fee_rate": getattr(rules, "transfer_fee_rate", Decimal("0")),
            "slippage_bps": rules.slippage_bps_default,
        }
        defaults.update(overrides)
        return cls(**defaults)

    @classmethod
    def for_cn_equity(cls, **overrides: Any) -> "RunnerConfig":
        """创建 A 股 RunnerConfig（§13.1 → MarketRulesCNEquity）"""
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        return cls._from_market_rules(MarketRulesCNEquity(), **overrides)

    @classmethod
    def for_convertible_bond(cls, **overrides: Any) -> "RunnerConfig":
        """创建可转债 RunnerConfig（§13.1 → MarketRulesConvertibleBond）"""
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
        return cls._from_market_rules(MarketRulesConvertibleBond(), **overrides)

    @classmethod
    def for_etf(cls, **overrides: Any) -> "RunnerConfig":
        """创建 ETF RunnerConfig（§13.1 → MarketRulesETF）"""
        from aipha.market.rules_etf import MarketRulesETF
        return cls._from_market_rules(MarketRulesETF(), **overrides)


@dataclass
class RunnerState:
    """运行器状态"""

    is_running: bool = False
    current_date: date | None = None
    strategy_name: str = ""
    total_bars_processed: int = 0
    total_orders_submitted: int = 0
    total_fills: int = 0
    last_heartbeat: datetime | None = None
    # P2-6 修复: 使用 deque 限制内存增长
    errors: deque = field(default_factory=lambda: deque(maxlen=500))


# 回调类型定义
BarCallback = Callable[[Bar], None]
FillCallback = Callable[[ExecutionReport], None]
ErrorCallback = Callable[[str, Exception], None]


class LiveRunner:
    """事件驱动实时运行器

    核心职责：
    1. 管理事件循环
    2. 协调策略与网关
    3. 处理订单生命周期
    4. 提供状态查询
    5. 审计日志记录
    6. 止损风控管理

    使用方式（仿真模式）：
        runner = LiveRunner(config, strategy, gateway)
        runner.start()
        for bar in data_stream:
            await runner.on_bar(bar)
        runner.stop()

    使用方式（数据回放）：
        runner = LiveRunner.from_paper_mode(config, strategy)
        await runner.run_replay(bars_by_date)
    """

    def __init__(
        self,
        config: RunnerConfig,
        strategy: Strategy,
        gateway: TradingGateway,
    ):
        """初始化运行器

        Args:
            config: 运行配置
            strategy: 策略实例
            gateway: 交易网关
        """
        self.config = config
        self.strategy = strategy
        self.gateway = gateway

        self._state = RunnerState(strategy_name=strategy.name)
        self._pending_intents: list[OrderIntent] = []
        self._order_map: dict[str, OrderIntent] = {}  # order_id -> intent

        # 动态峰值 NAV 跟踪（用于回撤计算）
        self._peak_nav: float = float(config.initial_capital)

        # 回调
        self._on_bar_callbacks: list[BarCallback] = []
        self._on_fill_callbacks: list[FillCallback] = []
        self._on_error_callbacks: list[ErrorCallback] = []

        # 异步控制
        self._stop_event: asyncio.Event | None = None
        self._loop_task: asyncio.Task | None = None

        # 审计日志
        self._audit_logger = None
        if config.audit_log_enabled:
            self._init_audit_logger()

        # 止损管理
        self._circuit_breaker = None
        self._daily_loss_rule = None
        self._is_trading_halted = False
        if config.stop_loss_enabled:
            self._init_stop_loss_manager()

        # 风控约束管理器
        self._risk_manager: CompositeRiskManager | None = None
        if config.risk_constraints_enabled and HAS_RISK_CONSTRAINTS:
            self._init_risk_constraints()

        # 交易监控器
        self._trading_monitor: TradingMonitor | None = None
        if config.monitoring_enabled and HAS_MONITORING:
            self._init_monitoring()

        # ML 风控共享模块仓位缩放 (R81 §4.3.1)
        # PaperEngine 通过设置此属性注入 UnifiedPositionSizer 的 final_scale
        self.risk_position_scale: float = 1.0

        # 持仓监控服务 (止盈止损 / 补仓 / 新股建仓)
        self._position_guard: PositionGuard | None = None
        if HAS_POSITION_GUARD:
            self._init_position_guard()

        # Tick 路径线程安全锁（on_tick 从 poll 线程调用）
        self._tick_lock = threading.Lock()

    def __del__(self) -> None:
        """析构时关闭 audit logger 防止文件泄漏。"""
        if getattr(self, "_audit_logger", None) is not None:
            try:
                self._audit_logger.close()
            except Exception:  # silent-pass-exempt: __del__ cleanup
                pass

    def _init_risk_constraints(self) -> None:
        """初始化风控约束管理器 (§13.15: 使用共享工厂)"""
        try:
            if HAS_RISK_FACTORY:
                # §13.15: 通过 build_risk_manager() 统一工厂构建
                risk_cfg = RiskConfig(
                    enabled=True,
                    max_position_weight=self.config.max_single_position_pct,
                    max_drawdown=self.config.max_drawdown_pct,
                    concentration_enabled=True,
                )
                # §4.3.2 / P0-6: DrawdownLimit 安全校验（防死亡螺旋）
                risk_cfg.validate_drawdown_safety()
                self._risk_manager = build_risk_manager(risk_cfg)
            elif HAS_RISK_CONSTRAINTS:
                # 降级: 直接构造（仅在缺少 risk_config_loader 时）
                # §4.3.2 + R163: max_drawdown<1.0 时必须开启三重防护
                _mdd = self.config.max_drawdown_pct
                _dl_kwargs = dict(
                    max_drawdown=_mdd,
                    reduce_position_at=self.config.drawdown_reduce_at_pct,
                )
                if _mdd < 1.0:
                    _dl_kwargs.update(
                        allow_rotation=True,
                        scale_factor_floor=0.5,
                        peak_reset_after_flat_days=60,
                    )
                constraints: list[RiskConstraint] = [
                    PositionLimit(max_weight=self.config.max_single_position_pct),
                    DrawdownLimit(**_dl_kwargs),
                ]
                self._risk_manager = CompositeRiskManager(constraints=constraints)

            # §13.15: 初始化 ComplianceEngine
            if HAS_COMPLIANCE:
                self._compliance_engine = ComplianceEngine()
            else:
                self._compliance_engine = None

            logger.info("Risk constraints manager initialized (factory=%s, compliance=%s)",
                        HAS_RISK_FACTORY, HAS_COMPLIANCE)
        except Exception as e:
            # §13.18 fail-closed + §14.1: 风控初始化失败必须阻止引擎启动
            # 实盘引擎无风控 = P0 级灾难风险 (Knight Capital $4.4亿教训)
            logger.critical(
                "FATAL: Risk constraints init failed — engine CANNOT start "
                "without risk controls (§14.1 rule 2): %s", e
            )
            raise RuntimeError(
                f"LiveRunner 风控初始化失败, 引擎拒绝启动 (§13.18 fail-closed): {e}"
            ) from e

    def _init_monitoring(self) -> None:
        """初始化交易监控器"""
        try:
            self._trading_monitor = TradingMonitor()

            # 添加告警规则
            self._trading_monitor.add_rule(
                MaxDrawdownAlert(
                    threshold=self.config.alert_drawdown_threshold,
                    level=AlertLevel.CRITICAL,
                )
            )
            self._trading_monitor.add_rule(
                DailyLossAlert(
                    max_loss_pct=self.config.alert_daily_loss_threshold,
                    level=AlertLevel.CRITICAL,
                )
            )
            self._trading_monitor.add_rule(
                PositionConcentrationAlert(
                    max_single_pct=self.config.alert_concentration_threshold,
                    level=AlertLevel.WARNING,
                )
            )

            # 添加日志通知器
            self._trading_monitor.add_notifier(LogNotifier())

            logger.info("Trading monitor initialized with default alerts")
        except Exception as e:
            logger.error(f"Failed to init monitoring: {e}")

    def _init_position_guard(self) -> None:
        """初始化持仓监控服务。"""
        try:
            guard_cfg = GuardConfig(
                stop_loss_pct=self.config.max_drawdown_pct,
                max_single_position_pct=self.config.max_single_position_pct,
                lot_size=self.config.lot_size,
            )
            self._position_guard = PositionGuard(
                config=guard_cfg,
                nav=float(self.config.initial_capital),
            )
            logger.info("PositionGuard initialized")
        except Exception as e:
            logger.error("Failed to init PositionGuard: %s", e)

    def _init_audit_logger(self) -> None:
        """初始化审计日志"""
        try:
            from aipha.risk.audit_log import AuditLogConfig, AuditLogger

            audit_config = AuditLogConfig(
                log_dir=self.config.audit_log_dir,
                file_prefix=f"live_{self.strategy.name}",
            )
            self._audit_logger = AuditLogger(audit_config)
            logger.info(f"Audit logger initialized: {self.config.audit_log_dir}")
        except ImportError:
            logger.warning("Audit log module not available")
        except Exception as e:
            logger.error(f"Failed to init audit logger: {e}")

    def _init_stop_loss_manager(self) -> None:
        """初始化止损管理器"""
        try:
            from aipha.risk.stop_loss import (
                CircuitBreaker,
                CircuitBreakerConfig,
                DailyLossLimit,
                StopLossConfig,
                StopLossType,
            )

            # 使用熔断器来管理止损
            circuit_config = CircuitBreakerConfig(
                enabled=True,
                max_drawdown=self.config.max_drawdown_pct,
                max_daily_loss=self.config.daily_loss_limit_pct,
                cooldown_minutes=30,
            )
            self._circuit_breaker = CircuitBreaker(circuit_config)

            # 创建日亏损止损规则
            daily_stop_config = StopLossConfig(
                enabled=True,
                stop_type=StopLossType.DAILY_LOSS,
                threshold=self.config.daily_loss_limit_pct,
            )
            self._daily_loss_rule = DailyLossLimit(
                config=daily_stop_config,
                initial_capital=self.config.initial_capital,
            )

            logger.info("Stop loss manager initialized")
        except ImportError:
            logger.warning("Stop loss module not available")
        except Exception as e:
            logger.error(f"Failed to init stop loss manager: {e}")

    @classmethod
    def from_paper_mode(
        cls,
        config: RunnerConfig,
        strategy: Strategy,
    ) -> "LiveRunner":
        """创建仿真模式运行器

        Args:
            config: 运行配置
            strategy: 策略实例

        Returns:
            配置好的运行器
        """
        gateway = PaperGateway.from_config(
            initial_capital=config.initial_capital,
            commission_rate=config.commission_rate,
            commission_min=config.commission_min,
            stamp_tax_rate=config.stamp_tax_rate,
            slippage_bps=config.slippage_bps,
            lot_size=config.lot_size,
            t_plus_n=config.t_plus_n,
            fill_model_name=config.fill_model_name,
            slippage_model=config.slippage_model,
            transfer_fee_rate=float(config.transfer_fee_rate),
        )
        return cls(config, strategy, gateway)

    @property
    def state(self) -> RunnerState:
        """获取运行器状态"""
        return self._state

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._state.is_running

    # ==========================================================================
    # 生命周期管理
    # ==========================================================================

    def start(self) -> None:
        """启动运行器"""
        if self._state.is_running:
            logger.warning("Runner already running")
            return

        logger.info(f"Starting runner in {self.config.mode.value} mode")

        # 审计日志：记录启动
        if self._audit_logger:
            self._audit_logger.log_system_start({
                "mode": self.config.mode.value,
                "strategy": self.strategy.name,
                "initial_capital": float(self.config.initial_capital),
            })

        # 连接网关
        self.gateway.connect()

        # 注册 tick 回调（用于 tick 级止损/止盈）
        if self._position_guard is not None:
            self.gateway.on_tick(self.on_tick)

        # 注册成交/状态回调（QMT 异步推送时回调）
        self.gateway.on_execution(self._on_execution_report)
        self.gateway.on_status_change(self._on_order_status_change)

        # 启动时持仓对账
        self._reconcile_positions_at_start()

        # 初始化策略
        self.strategy.on_start()

        self._state.is_running = True
        self._state.last_heartbeat = datetime.now()
        self._is_trading_halted = False

        # 注册 OS 信号处理（graceful shutdown）
        self._register_signal_handlers()

        logger.info(f"Runner started: {self.strategy.name}")

    def stop(self) -> None:
        """停止运行器"""
        if not self._state.is_running:
            return

        logger.info("Stopping runner...")

        # 取消所有挂单
        self._cancel_all_pending_orders()

        # 断开网关
        self.gateway.disconnect()

        # 审计日志：记录停止
        if self._audit_logger:
            snapshot = self.gateway.get_account_snapshot()
            self._audit_logger.log_system_stop(
                reason=f"normal_stop:nav={float(snapshot.total_equity):.2f}"
            )
            self._audit_logger.close()

        self._state.is_running = False

        logger.info("Runner stopped")

    def emergency_stop(self, reason: str) -> None:
        """紧急停止（Kill Switch）

        立即停止交易，取消所有挂单，记录审计日志。
        不会自动恢复，需人工介入。

        Args:
            reason: 紧急停止原因
        """
        logger.critical(f"EMERGENCY STOP: {reason}")

        # 激活 kill switch
        self._is_trading_halted = True

        if self._circuit_breaker:
            self._circuit_breaker.kill_switch(reason)

        # 取消所有挂单
        self._cancel_all_pending_orders()

        # 审计日志
        if self._audit_logger:
            snapshot = self.gateway.get_account_snapshot()
            self._audit_logger.log_system_stop(
                reason=f"EMERGENCY_STOP:{reason}:nav={float(snapshot.total_equity):.2f}"
            )

    def check_broker_heartbeat(self) -> None:
        """§14.4 #9: 检测券商断连, 超时触发 Kill Switch.

        应由外部 scheduler/watchdog 定期调用 (如每 30s).
        逻辑: 若 last_heartbeat 距今超过 broker_disconnect_kill_timeout,
        则调用 emergency_stop 并推送告警.
        """
        if not self._state.is_running:
            return
        if self._is_trading_halted:
            return
        last_hb = self._state.last_heartbeat
        if last_hb is None:
            return  # 尚未收到第一次心跳
        elapsed = (datetime.now() - last_hb).total_seconds()
        timeout = self.config.broker_disconnect_kill_timeout
        if elapsed > timeout:
            reason = (
                f"券商断连 Kill Switch: {elapsed:.0f}s 无心跳 "
                f"(阈值 {timeout:.0f}s, §14.4 #9)"
            )
            self.emergency_stop(reason)
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                send_feishu_alert(
                    f"🚨 KILL SWITCH 触发\n{reason}\n"
                    f"策略: {self.strategy.name}\n"
                    f"最后心跳: {last_hb.isoformat()}",
                    level="critical",
                )
            except Exception:
                logger.debug("Kill switch 飞书告警推送失败", exc_info=True)

    def _reconcile_positions_at_start(self) -> None:
        """启动时持仓对账

        若网关支持 get_position_details()，则与本地状态进行对账。
        对账失败时记录警告但不阻塞启动（仅在模拟/实盘模式下有意义）。
        """
        if not HAS_RECONCILER:
            return

        if not hasattr(self.gateway, 'get_position_details'):
            logger.debug("Gateway does not support position details, skipping reconciliation")
            return

        try:
            local_positions = self.gateway.get_position_details()
            # 对于 PaperGateway，broker == local；实盘模式下需要从券商获取
            # 此处仅记录启动时持仓快照，后续实盘集成时扩展
            reconciler = PositionReconciler()
            report = reconciler.reconcile(local_positions, local_positions)

            if report.is_consistent:
                logger.info(
                    f"Position reconciliation OK: {report.matched_count} positions matched"
                )
            else:
                logger.warning(f"Position reconciliation: {report.summary}")
                for diff in report.diffs:
                    logger.warning(f"  {diff.symbol}: {diff.diff_type.value}")

            if self._audit_logger:
                self._audit_logger.log_event(
                    "position_reconciliation",
                    {
                        "is_consistent": report.is_consistent,
                        "matched": report.matched_count,
                        "diffs": report.diff_count,
                    },
                )
        except Exception as e:
            logger.warning(f"Position reconciliation failed: {e}")

    def _register_signal_handlers(self) -> None:
        """注册 OS 信号处理器（SIGINT/SIGTERM）

        Windows 支持 SIGINT (Ctrl+C) 和 SIGBREAK。
        """
        def _handle_signal(signum: int, frame) -> None:
            sig_name = signal.Signals(signum).name
            logger.warning(f"Received signal {sig_name}, initiating emergency stop")
            self.emergency_stop(f"OS signal: {sig_name}")
            self.stop()

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            # SIGTERM 在 Windows 上可能不可用，静默忽略
            if hasattr(signal, 'SIGTERM'):
                signal.signal(signal.SIGTERM, _handle_signal)
        except (OSError, ValueError) as e:
            # 在非主线程中无法设置信号处理器
            logger.debug(f"Cannot set signal handlers: {e}")

    def _cancel_all_pending_orders(self) -> None:
        """取消所有挂单"""
        active_orders = self.gateway.get_active_orders()
        for order in active_orders:
            try:
                self.gateway.cancel_order(order.order_id)
            except Exception as e:
                logger.warning(f"Failed to cancel order {order.order_id}: {e}")

    # ==========================================================================
    # 事件处理
    # ==========================================================================

    def on_bar(self, bar: Bar) -> None:
        """处理新 Bar 事件（单标的）

        逐条 Bar 到达时调用。内部直接构建 {symbol: bar}
        并传递给策略。如需多标的聚合，使用 on_bars() 或配置 BarBuffer。

        Args:
            bar: K线数据
        """
        if not self._state.is_running:
            logger.warning("Runner not running, ignoring bar")
            return

        # Kill switch 文件检查（§14.2 L3）
        # 路径来自 aipha.utils.paths 的唯一常量。原先这里是
        # parents[3]/configs/kill_switch.flag —— 偏了一层，指向
        # quant-trading/configs/（该目录不存在），于是**紧急停止开关
        # 永远不会触发**：运维在项目根建好标志文件，引擎照旧下单。
        if KILL_SWITCH_PATH.exists():
            self.emergency_stop(f"Kill switch file detected: {KILL_SWITCH_PATH}")
            return

        bars_dict = {bar.symbol: bar}
        self._process_bars(bars_dict)

    def on_tick(self, tick: TickData) -> None:
        """处理 Tick 推送（低延迟止损/止盈路径）。

        仅运行 PositionGuard.check_tick() 快速检查，
        不调用策略 on_bar。产生的紧急订单立即提交。

        注意: 此方法从 QMTGateway 的 poll 线程调用，
        需要通过 _tick_lock 保护共享状态。
        """
        if not self._state.is_running:
            return
        if self._position_guard is None:
            return

        with self._tick_lock:
            # 查询持仓（含 T+1 可卖数量）
            qty, available = self._query_position_for_tick(tick.symbol)
            if qty <= 0:
                return

            action = self._position_guard.check_tick(
                symbol=tick.symbol,
                price=tick.price,
                qty=qty,
                available_to_sell=available,
            )
            if action is None:
                return

            side = Side.SELL if action.side == "SELL" else Side.BUY
            intent = OrderIntent(
                symbol=action.symbol, side=side, qty=action.qty,
                reason=f"[TICK] {action.reason}",
            )
            logger.warning(
                "Tick 级 %s 触发: %s %s x%d @ %.3f — %s",
                action.action_type.value, action.side,
                action.symbol, action.qty, tick.price, action.reason,
            )
            self._submit_intents_urgent([intent])

    def on_bars(self, bars_dict: dict[str, Bar]) -> None:
        """处理同一时间点的多标的 Bar

        用于多标的策略场景，bars_dict 为同一 end_ts 的所有标的。

        Args:
            bars_dict: {symbol: Bar} 字典
        """
        if not self._state.is_running:
            logger.warning("Runner not running, ignoring bars")
            return

        # Kill switch 文件检查（§14.2 L3）——同 on_bar，见那里的说明
        if KILL_SWITCH_PATH.exists():
            self.emergency_stop(f"Kill switch file detected: {KILL_SWITCH_PATH}")
            return

        self._process_bars(bars_dict)

    def _process_bars(self, bars_dict: dict[str, Bar]) -> None:
        """处理一组同时间点的 Bar（内部核心）

        Args:
            bars_dict: {symbol: Bar} 字典
        """
        if not bars_dict:
            return

        # 选一个 bar 作为时间参考
        ref_bar = next(iter(bars_dict.values()))

        try:
            # 更新日期
            bar_date = ref_bar.end_ts.date()
            if self._state.current_date != bar_date:
                self._on_date_change(bar_date)

            # 1. 先撮合待成交订单，消费成交回报（仅 Paper 模式需要）
            if hasattr(self.gateway, 'process_bar'):
                for bar in bars_dict.values():
                    execution_reports = self.gateway.process_bar(bar)
                    if execution_reports:
                        self._state.total_fills += len(execution_reports)
                        for report in execution_reports:
                            for callback in self._on_fill_callbacks:
                                try:
                                    callback(report)
                                except Exception as e:
                                    logger.error(f"Fill callback error: {e}")

            # 2. 构建策略上下文
            ctx = self._build_strategy_context(ref_bar)

            # 3. 调用策略生成信号
            intents = self.strategy.on_bar(bars_dict, ctx)

            # 3.5 持仓监控: 止盈止损 / 补仓 / 新股
            guard_intents = self._run_position_guard(ctx, bars_dict)
            if guard_intents:
                intents = list(intents or []) + guard_intents

            # 4. 提交订单
            if intents:
                self._submit_intents(intents, ref_bar)

            # 5. 更新状态
            self._state.total_bars_processed += len(bars_dict)
            self._state.last_heartbeat = datetime.now()

            # 6. 回调
            for bar in bars_dict.values():
                for callback in self._on_bar_callbacks:
                    callback(bar)

        except Exception as e:
            self._handle_error("on_bar", e)

    def _on_date_change(self, new_date: date) -> None:
        """处理日期变更

        Args:
            new_date: 新日期
        """
        old_date = self._state.current_date

        if old_date is not None:
            # 日终结算
            logger.debug(f"End of day: {old_date}")

        self._state.current_date = new_date
        if hasattr(self.gateway, 'set_trade_date'):
            self.gateway.set_trade_date(new_date)

        logger.debug(f"Date changed to: {new_date}")

    def _build_strategy_context(self, bar: Bar) -> StrategyContext:
        """构建策略上下文

        Args:
            bar: 当前 Bar

        Returns:
            策略上下文
        """
        snapshot = self.gateway.get_account_snapshot()

        return StrategyContext(
            current_time=bar.end_ts,
            cash=float(snapshot.cash),
            nav=float(snapshot.total_equity),
            positions=snapshot.positions.copy(),  # dict[str, int]
        )

    def _run_position_guard(
        self, ctx: StrategyContext, bars_dict: dict[str, Bar],
    ) -> list[OrderIntent]:
        """运行持仓监控，返回 Guard 产生的 OrderIntent 列表。"""
        if self._position_guard is None:
            return []
        try:
            self._position_guard.update_nav(ctx.nav)
            # 构建持仓快照
            snapshots: list[PositionSnapshot] = []
            for sym, qty in ctx.positions.items():
                bar = bars_dict.get(sym)
                price = Decimal(str(bar.close)) if bar else Decimal("0")
                if price <= 0:
                    continue
                # 从 tracker 获取真实入场价；首次见到的标的用当前价
                tracker = self._position_guard._trackers.get(sym)
                avg_cost = tracker.entry_price if tracker else price
                snapshots.append(PositionSnapshot(
                    symbol=sym, qty=qty, avg_cost=avg_cost,
                    market_value=price * qty,
                    current_price=price,
                    available_to_sell=qty,
                ))
            actions = self._position_guard.check_all(
                snapshots, self._state.total_bars_processed)
            # 转换为 OrderIntent
            intents: list[OrderIntent] = []
            for a in actions:
                if a.qty <= 0:
                    continue
                side = Side.SELL if a.side == "SELL" else Side.BUY
                intents.append(OrderIntent(
                    symbol=a.symbol, side=side, qty=a.qty,
                    reason=a.reason,
                ))
                logger.info("PositionGuard action: %s %s %s x%d — %s",
                            a.action_type.value, a.side, a.symbol,
                            a.qty, a.reason)
            return intents
        except Exception as e:
            logger.error("PositionGuard error: %s", e)
            return []

    def _state_positions(self) -> dict[str, int]:
        """获取当前持仓 dict[symbol, qty]（线程安全）。"""
        try:
            snap = self.gateway.get_account_snapshot()
            return dict(snap.positions)
        except Exception:
            return {}

    def _query_position_for_tick(self, symbol: str) -> tuple[int, int]:
        """查询单标的持仓及 T+1 可卖数量。

        优先使用 QMT query_positions() 获取 can_use_volume，
        降级时 available = qty（CB T+0 场景）。

        Returns:
            (total_qty, available_to_sell)
        """
        try:
            if hasattr(self.gateway, "query_positions"):
                for p in self.gateway.query_positions():
                    if p.get("stock_code") == symbol:
                        vol = int(p.get("volume", 0))
                        avail = int(p.get("can_use_volume", vol))
                        return vol, avail
            # 降级: AccountSnapshot 无 can_use_volume
            snap = self.gateway.get_account_snapshot()
            qty = snap.positions.get(symbol, 0)
            return qty, qty
        except Exception:
            return 0, 0

    def _submit_intents_urgent(self, intents: list[OrderIntent]) -> None:
        """提交紧急订单意图（tick 级止损/止盈使用）。

        跳过常规风控检查，直接按市价提交。止损级订单不应被
        MDD 缩放或持仓限制拦截。

        §14.1 安全护栏：仅允许 SELL 方向（止损），BUY 方向必须
        走常规 _submit_intents 路径以获得完整风控覆盖。

        注意: 调用方应已持有 _tick_lock。
        """
        if self._is_trading_halted:
            logger.warning("Trading halted, rejecting urgent orders")
            return
        for intent in intents:
            # §14.1 安全护栏: urgent 路径仅允许 SELL (止损)
            if intent.side != Side.SELL:
                logger.error(
                    "§14.1 BLOCKED: urgent path only allows SELL, "
                    "got %s %s x%d — routing to normal path",
                    intent.side.value, intent.symbol, intent.qty,
                )
                # 非 SELL 订单降级到常规路径（有完整风控）
                self._submit_intents([intent])
                continue
            try:
                order = GatewayOrder(
                    order_id=f"URG_{intent.symbol}_{int(datetime.now().timestamp()*1000)}",
                    symbol=intent.symbol,
                    side="SELL" if intent.side == Side.SELL else "BUY",
                    qty=intent.qty,
                    order_type="MARKET",
                    status=OrderStatus.PENDING,
                )
                oid = self.gateway.submit_order(order)
                # 检查是否被立即拒绝（broker_id <= 0）
                submitted_order = self.gateway.get_order(oid)
                if submitted_order and submitted_order.status == OrderStatus.REJECTED:
                    logger.error(
                        "Urgent order REJECTED immediately: %s %s x%d — %s",
                        intent.side.value, intent.symbol, intent.qty,
                        submitted_order.reject_reason,
                    )
                    continue
                self._order_map[oid] = intent
                self._state.total_orders_submitted += 1
                logger.info("Urgent order submitted: %s %s x%d → %s",
                            intent.side.value, intent.symbol, intent.qty, oid)
            except Exception as e:
                logger.error("Urgent submit failed: %s %s — %s",
                             intent.symbol, intent.side.value, e)

    # ==========================================================================
    # 成交/状态回调 (QMT 异步推送)
    # ==========================================================================

    def _on_execution_report(self, report: ExecutionReport) -> None:
        """处理成交回报（从 gateway poll 线程回调）。"""
        intent = self._order_map.get(report.order_id)
        tag = f"[{intent.reason}]" if intent else ""
        logger.info(
            "成交回报 %s: %s %s x%d @ %.3f %s",
            report.order_id, report.side, report.symbol,
            report.fill_qty, report.fill_price, tag,
        )
        # 触发 fill 回调给外部订阅者
        for cb in self._on_fill_callbacks:
            try:
                cb(report)
            except Exception as e:
                logger.error("Fill callback error: %s", e)
        # 订单已完成 → 清理 + 通知 PositionGuard
        if report.is_last:
            self._order_map.pop(report.order_id, None)
            if self._position_guard and report.side == "SELL":
                self._position_guard.clear_position(report.symbol)
            elif self._position_guard and report.side == "BUY":
                self._position_guard.register_entry(
                    report.symbol, Decimal(str(report.fill_price)),
                )
        # 审计日志
        if self._audit_logger:
            self._audit_logger.log_event("FILL", {
                "order_id": report.order_id,
                "symbol": report.symbol,
                "side": report.side,
                "fill_qty": report.fill_qty,
                "fill_price": report.fill_price,
            })

    def _on_order_status_change(self, change: OrderStatusChange) -> None:
        """处理订单状态变更（从 gateway poll 线程回调）。"""
        logger.info(
            "订单状态: %s %s → %s%s",
            change.order_id, change.old_status.value,
            change.new_status.value,
            f" ({change.reason})" if change.reason else "",
        )
        terminal = {OrderStatus.FILLED, OrderStatus.CANCELED,
                    OrderStatus.REJECTED, OrderStatus.EXPIRED}
        if change.new_status in terminal:
            intent = self._order_map.pop(change.order_id, None)
            # 被拒绝/撤销的止损单 → 回滚 PositionGuard 状态
            if (change.new_status in (OrderStatus.REJECTED, OrderStatus.CANCELED)
                    and intent and "[TICK]" in (intent.reason or "")):
                logger.warning(
                    "止损/止盈单被拒: %s %s — %s，将允许重新触发",
                    intent.symbol, change.new_status.value, change.reason,
                )
                # 不清除 tracker，使下一个 tick 可以重新触发
            # 审计日志
            if self._audit_logger:
                self._audit_logger.log_event("ORDER_TERMINAL", {
                    "order_id": change.order_id,
                    "status": change.new_status.value,
                    "reason": change.reason,
                })

    def _submit_intents(self, intents: list[OrderIntent], bar: Bar) -> None:
        """提交订单意图

        Args:
            intents: 订单意图列表
            bar: 当前 Bar
        """
        # 检查是否已被熔断
        if self._is_trading_halted:
            # 检查熔断器冷却恢复
            if self._circuit_breaker and not self._circuit_breaker.is_active:
                self._is_trading_halted = False
                logger.info("Circuit breaker cooldown ended, resuming trading")
            else:
                logger.warning("Trading halted, rejecting all orders")
                return

        # 检查熔断器
        snapshot = self.gateway.get_account_snapshot()
        if self._circuit_breaker:
            if self._circuit_breaker.check(snapshot.total_equity):
                self._is_trading_halted = True
                logger.error("Circuit breaker triggered, halting trading")
                return

        # 更新交易监控器
        if self._trading_monitor and HAS_MONITORING:
            self._update_monitoring(snapshot, bar)

        # 计算当前价格用于风控检查
        current_prices: dict[str, float] = {}
        for intent in intents:
            if hasattr(bar, 'close') and bar.symbol == intent.symbol:
                current_prices[intent.symbol] = float(bar.close)
            elif intent.limit_price:
                current_prices[intent.symbol] = float(intent.limit_price)

        # §13.15: 统一前交易管线 — 批量 pretrade 检查
        if self._risk_manager and HAS_COMPLIANCE and intents:
            batch = [
                (i.symbol, i.qty, "BUY" if i.side == Side.BUY else "SELL")
                for i in intents
            ]
            pt_result = run_pretrade_checks(
                intents=batch,
                current_positions=snapshot.positions,
                nav=float(snapshot.total_equity),
                prices=current_prices,
                risk_manager=self._risk_manager,
            )
            # 构建审批映射
            approved_set = {s for s, _, _ in pt_result.approved}
            adjusted_map = {s: new_qty for s, _, new_qty, _ in pt_result.adjusted}
            filtered_intents = []
            for intent in intents:
                if intent.symbol in approved_set:
                    filtered_intents.append(intent)
                elif intent.symbol in adjusted_map:
                    filtered_intents.append(OrderIntent(
                        symbol=intent.symbol,
                        side=intent.side,
                        qty=adjusted_map[intent.symbol],
                        order_type=intent.order_type,
                        limit_price=intent.limit_price,
                    ))
                else:
                    logger.warning(
                        "Pretrade rejected %s %s x%d",
                        intent.side, intent.symbol, intent.qty,
                    )
            if len(filtered_intents) < len(intents):
                logger.info(
                    "Pretrade filter: %d→%d intents",
                    len(intents), len(filtered_intents),
                )
            intents = filtered_intents

        for intent in intents:
            try:
                # ML 风控缩放 (R81 §4.3.1 共享路径)
                if self.risk_position_scale < 0.999 and intent.side == Side.BUY:
                    original_qty = intent.qty
                    scaled_qty = max(
                        self.config.lot_size,
                        int(intent.qty * self.risk_position_scale)
                        // self.config.lot_size * self.config.lot_size,
                    )
                    if scaled_qty != original_qty:
                        intent = OrderIntent(
                            symbol=intent.symbol,
                            side=intent.side,
                            qty=scaled_qty,
                            order_type=intent.order_type,
                            limit_price=intent.limit_price,
                        )
                        logger.info(
                            f"Risk scale {self.risk_position_scale:.2f}: "
                            f"{intent.symbol} qty {original_qty}→{scaled_qty}"
                        )

                # 风控约束检查
                if self._risk_manager and HAS_RISK_CONSTRAINTS:
                    side_str = "BUY" if intent.side == Side.BUY else "SELL"
                    risk_result = self._risk_manager.check(
                        symbol=intent.symbol,
                        qty=intent.qty,
                        side=side_str,
                        current_positions=snapshot.positions,
                        nav=float(snapshot.total_equity),
                        prices=current_prices,
                    )

                    if not risk_result.passed:
                        logger.warning(
                            f"Risk constraint violation for {intent.symbol}: "
                            f"{risk_result.message}"
                        )
                        # 如果有调整后的数量，使用调整后的数量
                        if risk_result.adjusted_qty and risk_result.adjusted_qty > 0:
                            intent = OrderIntent(
                                symbol=intent.symbol,
                                side=intent.side,
                                qty=risk_result.adjusted_qty,
                                order_type=intent.order_type,
                                limit_price=intent.limit_price,
                            )
                            logger.info(
                                f"Adjusted order qty to {risk_result.adjusted_qty} "
                                f"for {intent.symbol}"
                            )
                        else:
                            # 跳过该订单
                            continue

                # §13.15: 合规检查（停牌/涨跌停/参与率）
                if getattr(self, '_compliance_engine', None) and HAS_COMPLIANCE:
                    try:
                        from aipha.portfolio.compliance_engine import ComplianceOrderIntent
                        _price = current_prices.get(intent.symbol, 0.0)
                        comp_intent = ComplianceOrderIntent(
                            symbol=intent.symbol,
                            side=str(intent.side.value) if hasattr(intent.side, 'value') else str(intent.side),
                            quantity=float(intent.qty),
                            price=_price,
                        )
                        result = self._compliance_engine.check_rebalance([comp_intent])
                        if not result.passed:
                            for v in result.violations:
                                if v.severity == "error":
                                    logger.warning(
                                        "Compliance rejected %s [%s]: %s",
                                        v.symbol, v.rule, v.detail,
                                    )
                            if result.n_orders_blocked > 0:
                                continue
                    except Exception as e:
                        logger.error(
                            "§14.2 Compliance check FAILED — order REJECTED (安全优先): %s", e,
                        )
                        continue

                order = self._intent_to_order(intent, bar)
                order_id = self.gateway.submit_order(order)

                self._order_map[order_id] = intent
                self._state.total_orders_submitted += 1

                # 审计日志
                if self._audit_logger:
                    self._audit_logger.log_order_submit(
                        order_id=order_id,
                        symbol=intent.symbol,
                        side=str(intent.side),
                        qty=intent.qty,
                        price=float(intent.limit_price) if intent.limit_price else None,
                    )

                logger.debug(
                    f"Order submitted: {order_id} {intent.side} "
                    f"{intent.symbol} x{intent.qty}"
                )

            except Exception as e:
                logger.error(f"Failed to submit order: {e}")
                self._handle_error("submit_order", e)

    def _intent_to_order(self, intent: OrderIntent, bar: Bar) -> GatewayOrder:
        """将订单意图转换为网关订单

        Args:
            intent: 订单意图
            bar: 当前 Bar

        Returns:
            网关订单
        """
        import uuid

        # 对齐数量到手数
        aligned_qty = (intent.qty // self.config.lot_size) * self.config.lot_size
        if aligned_qty <= 0:
            aligned_qty = self.config.lot_size

        return GatewayOrder(
            order_id=f"R{uuid.uuid4().hex[:8]}",
            symbol=intent.symbol,
            side="BUY" if intent.side == Side.BUY else "SELL",
            qty=aligned_qty,
            order_type="LIMIT" if intent.limit_price else "MARKET",
            limit_price=intent.limit_price,
            created_at=bar.end_ts,
        )

    def _update_monitoring(
        self, snapshot: AccountSnapshot, bar: Bar
    ) -> None:
        """更新交易监控器

        Args:
            snapshot: 账户快照
            bar: 当前 Bar
        """
        if not self._trading_monitor or not HAS_MONITORING:
            return

        try:
            # 计算持仓市值
            positions_value: dict[str, float] = {}
            for symbol, qty in snapshot.positions.items():
                # 使用当前 bar 价格或估算
                if bar.symbol == symbol:
                    positions_value[symbol] = float(qty) * float(bar.close)
                else:
                    # 使用冻结资金作为估算（不精确，但足够触发告警）
                    positions_value[symbol] = float(qty) * float(bar.close)

            # 计算回撤（使用动态峰值 NAV）
            current = float(snapshot.total_equity)
            self._peak_nav = max(self._peak_nav, current)
            peak = self._peak_nav
            drawdown = max(0, (peak - current) / peak) if peak > 0 else 0.0

            # 构建组合快照
            portfolio_snapshot = PortfolioSnapshot(
                timestamp=bar.end_ts,
                total_value=current,
                cash=float(snapshot.cash),
                positions=positions_value,
                unrealized_pnl=0.0,  # 需要更精确的计算
                realized_pnl=0.0,
                drawdown=drawdown,
                leverage=1.0,
                turnover=0.0,
            )

            # 更新监控并检查告警
            triggered_alerts = self._trading_monitor.update(portfolio_snapshot)
            if triggered_alerts:
                for alert in triggered_alerts:
                    logger.warning(
                        f"Alert triggered: [{alert.level.value}] "
                        f"{alert.title} - {alert.message}"
                    )

        except Exception as e:
            logger.error(f"Failed to update monitoring: {e}")

    def _handle_error(self, context: str, error: Exception) -> None:
        """处理错误

        Args:
            context: 错误上下文
            error: 异常
        """
        error_msg = f"{context}: {error}"
        logger.error(error_msg)
        self._state.errors.append(error_msg)

        for callback in self._on_error_callbacks:
            callback(context, error)

    # ==========================================================================
    # 数据回放
    # ==========================================================================

    async def run_replay(
        self,
        bars_by_date: dict[date, list[Bar]],
        speed: float = 0.0,
    ) -> AccountSnapshot:
        """运行历史数据回放

        自动按 end_ts 聚合多标的 Bar。

        Args:
            bars_by_date: 按日期组织的 Bar 数据
            speed: 回放速度（0=最快，>0 为每组 bars 的间隔秒数）

        Returns:
            最终账户快照
        """
        self.start()

        try:
            sorted_dates = sorted(bars_by_date.keys())

            for trade_date in sorted_dates:
                bars = bars_by_date[trade_date]

                grouped = group_bars_by_end_ts(bars)
                for bars_dict in grouped:
                    self.on_bars(bars_dict)

                    if speed > 0:
                        await asyncio.sleep(speed)

            return self.gateway.get_account_snapshot()

        finally:
            self.stop()

    def run_replay_sync(
        self,
        bars_by_date: dict[date, list[Bar]],
    ) -> AccountSnapshot:
        """同步运行历史数据回放

        自动按 end_ts 聚合多标的 Bar，以 on_bars() 批量传入策略。

        Args:
            bars_by_date: 按日期组织的 Bar 数据

        Returns:
            最终账户快照
        """
        self.start()

        try:
            sorted_dates = sorted(bars_by_date.keys())

            for trade_date in sorted_dates:
                bars = bars_by_date[trade_date]

                # 按 end_ts 分组，同一时间点的多标的 Bar 一起传入策略
                grouped = group_bars_by_end_ts(bars)
                for bars_dict in grouped:
                    self.on_bars(bars_dict)

            return self.gateway.get_account_snapshot()

        finally:
            self.stop()

    # ==========================================================================
    # 回调注册
    # ==========================================================================

    def on_bar_callback(self, callback: BarCallback) -> None:
        """注册 Bar 回调"""
        self._on_bar_callbacks.append(callback)

    def on_fill_callback(self, callback: FillCallback) -> None:
        """注册成交回调"""
        self._on_fill_callbacks.append(callback)

    def on_error_callback(self, callback: ErrorCallback) -> None:
        """注册错误回调"""
        self._on_error_callbacks.append(callback)

    # ==========================================================================
    # 状态查询
    # ==========================================================================

    def get_account_snapshot(self) -> AccountSnapshot:
        """获取账户快照"""
        return self.gateway.get_account_snapshot()

    def get_active_orders(self) -> list[GatewayOrder]:
        """获取活跃订单"""
        return self.gateway.get_active_orders()

    def get_state_dict(self) -> dict[str, Any]:
        """获取状态字典（用于监控）"""
        snapshot = self.gateway.get_account_snapshot()

        return {
            "is_running": self._state.is_running,
            "mode": self.config.mode.value,
            "strategy_name": self._state.strategy_name,
            "current_date": (
                self._state.current_date.isoformat()
                if self._state.current_date
                else None
            ),
            "total_bars": self._state.total_bars_processed,
            "total_orders": self._state.total_orders_submitted,
            "total_fills": self._state.total_fills,
            "last_heartbeat": (
                self._state.last_heartbeat.isoformat()
                if self._state.last_heartbeat
                else None
            ),
            "account": {
                "cash": float(snapshot.cash),
                "frozen_cash": float(snapshot.frozen_cash),
                "total_equity": float(snapshot.total_equity),
                "positions": snapshot.positions.copy(),
            },
            "errors": list(self._state.errors)[-10:],  # 最近 10 条错误
        }
