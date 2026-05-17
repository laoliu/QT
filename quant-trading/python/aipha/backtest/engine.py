"""
事件驱动回测引擎

基于事件驱动的回测框架，支持：
- 多种事件类型（Bar、Order、Fill、EOD）
- 可插拔的组件（策略、撮合、费用、滑点）
- 交易日历支持
- 完整的日志和报告
"""

import heapq
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Any, Optional

from aipha.domain.models import Bar, Fill, Order, OrderType, Side
from aipha.execution.fees import FeeBreakdown, FeeModel
from aipha.execution.fill_models import FillModel
from aipha.execution.slippage import SlippageModel
from aipha.execution.trading_ops import BUY_FREEZE_BUFFER
from aipha.market.base import MarketRules
from aipha.portfolio.account import Account
from aipha.portfolio.position_manager import PositionManager
from aipha.risk.audit_log import AuditEventType, AuditLevel, AuditLogger
from aipha.risk.constraints import CompositeRiskManager, MarketRegimeDetector
from aipha.risk.degradation import (
    DegradationConfig,
    DegradationLevel,
    DegradationManager,
    DegradationSignal,
)
from aipha.risk.position_sizing import UnifiedPositionSizer
from aipha.risk.stop_loss import StopLossManager, TriggerAction
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext
from aipha.universe.policy import CompositeUniversePolicy

logger = logging.getLogger(__name__)

# 卖出资金预估折扣系数：向后兼容默认值，实际值由 BacktestConfig.sell_credit_discount 控制
# 用于临时 sell_credit 和策略层可用资金估算
_SELL_CREDIT_DISCOUNT_DEFAULT = Decimal("0.995")

# ============================================================================
# 事件系统
# ============================================================================


class EventType(Enum):
    """事件类型"""

    # 市场事件
    BAR = auto()  # 新 Bar 数据
    EOD = auto()  # 日终事件

    # 订单事件
    ORDER_SUBMITTED = auto()  # 订单提交
    ORDER_FILLED = auto()  # 订单成交
    ORDER_REJECTED = auto()  # 订单拒绝
    ORDER_CANCELLED = auto()  # 订单取消

    # 系统事件
    BACKTEST_START = auto()  # 回测开始
    BACKTEST_END = auto()  # 回测结束


@dataclass(order=True)
class Event:
    """事件基类

    使用优先级队列，按 (timestamp, sequence) 排序
    """

    timestamp: datetime
    sequence: int = field(compare=True)
    event_type: EventType = field(compare=False)
    data: Any = field(compare=False, default=None)

    def __post_init__(self):
        if not isinstance(self.event_type, EventType):
            raise ValueError(f"Invalid event_type: {self.event_type}")


class EventQueue:
    """事件优先队列

    .. note::
        当前引擎使用命令式 for-bar 循环驱动，**不**通过 ``pop()`` 消费事件。
        ``push()`` / ``pop()`` / ``is_empty()`` 保留以备将来迁移至事件驱动架构。
        目前仅使用 ``next_sequence()`` 作为全局递增序列号生成器。
    """

    # TODO: 迁移为事件驱动范式后，run() 采用
    #   while not queue.is_empty(): event = queue.pop(); dispatch(event)

    def __init__(self):
        self._queue: list[Event] = []
        self._sequence: int = 0

    def push(self, event: Event) -> None:
        """添加事件到队列"""
        heapq.heappush(self._queue, event)

    def pop(self) -> Optional[Event]:
        """取出最早的事件"""
        if self._queue:
            return heapq.heappop(self._queue)
        return None

    def is_empty(self) -> bool:
        """队列是否为空"""
        return len(self._queue) == 0

    def next_sequence(self) -> int:
        """获取下一个序列号"""
        self._sequence += 1
        return self._sequence

    def clear(self) -> None:
        """清空队列"""
        self._queue.clear()
        self._sequence = 0


# ============================================================================
# 回测结果
# ============================================================================


@dataclass
class TradeRecord:
    """交易记录"""

    trade_id: str
    order_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    notional: float
    fees: FeeBreakdown
    trade_time: datetime
    trade_date: date
    reason: str | None = None  # 交易原因（从 OrderIntent.reason 传递）


@dataclass
class DailySnapshot:
    """每日日终快照。

    Attributes:
        trade_date: 交易日
        nav: 日终净资产（现金 + 持仓市值，含冻结资金）
        cash: 可用现金（= Account.cash，扣除冻结后的余额；
              在有 pending buy order 冻结资金时，cash 可能低于 0
              但 nav 仍包含 frozen_cash，因此 nav >= cash）
        positions: 持仓股数 {symbol: qty}
        position_values: 持仓市值 {symbol: market_value}
        daily_return: 日收益率 = (nav_today - nav_yesterday) / nav_yesterday
        cumulative_return: 累计收益率 = (nav_today - initial_capital) / initial_capital
        frozen_cash: 被 pending buy orders 冻结的资金
    """

    trade_date: date
    nav: float
    cash: float
    positions: dict[str, int]
    position_values: dict[str, float]
    daily_return: float
    cumulative_return: float
    frozen_cash: float = 0.0


@dataclass
class BacktestResult:
    """回测结果"""

    # 基本信息
    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float

    # 最终状态
    final_nav: float
    final_cash: float
    final_positions: dict[str, int]

    # 序列数据
    daily_snapshots: list[DailySnapshot]
    trades: list[TradeRecord]

    # 汇总统计
    total_trades: int
    total_fees: FeeBreakdown
    exclusion_stats: dict[str, int]

    # 元数据
    config: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 回测引擎配置
# ============================================================================


@dataclass
class BacktestConfig:
    """回测配置"""

    # 资金
    initial_capital: float = 1_000_000.0

    # 交易规则
    lot_size: int = 100  # 手数
    min_trade_value: float = 0.0  # 最小交易金额

    # 费用参数（§13.1 → MarketRulesCNEquity 默认值）
    commission_rate: float = 8.54e-05  # 佣金率（万0.854）
    commission_min: float = 5.0  # 最低佣金
    stamp_tax_rate: float = 0.0005  # 印花税（仅卖出）
    transfer_fee_rate: float = 0.00001  # 过户费

    # 滑点
    slippage_bps: float = 10.0  # 滑点（基点），与 CostConfig 保持一致

    # 其他
    allow_short: bool = False  # 是否允许做空
    t_plus_n: int = 1  # T+N 交易规则

    # 卖出预借折扣系数（0-1）：预估卖出所得时保留安全边际
    # 解释：策略在 T 日同时发出卖出+买入意图时，引擎用 close*discount 估算
    #   卖出所得并临时预借给买入冻结。实际卖出成交在 T+1 以 next-bar open 计算，
    #   此折扣覆盖 close→open 价差 + 费用 + 滑点。默认 0.995 (0.5% 安全边际)。
    sell_credit_discount: float = 0.995

    # --- 停牌折价 ---
    suspension_discount_enabled: bool = False   # 是否启用长停牌折价
    suspension_discount_start_days: int = 30    # 开始折价的连续停牌天数
    suspension_discount_rate: float = 0.001     # 每多停 1 天追加的折价率
    suspension_discount_max: float = 0.30       # 最大折价率（30%）

    # --- 未成交订单重试 ---
    # 完全未成交的订单（涨停/零量/无行情）在后续 bar 继续尝试成交，
    # 直到达到 max_retry_bars 或下一个调仓日（新目标集覆盖）。
    # 0 = 禁用重试（默认行为：未成交即取消）；>0 = 最大重试天数。
    retry_unfilled_bars: int = 0
    # 卖单是否无条件重试（不受价格偏离限制）。默认 True：
    # 卖单 = 降低风险暴露，应尽量成交。
    retry_sell_always: bool = True
    # 买单追涨上限：若当前价格已超过信号价 × (1 + max_chase_pct)，
    # 则放弃重试。0 = 不限制（原有行为）；建议 0.03~0.08。
    retry_max_chase_pct: float = 0.0

    # --- 永久缺行情持仓核销 ---
    # 对短期停牌/临时无行情，仍沿用 last_price + suspension_discount 估值；
    # 仅当持仓连续多个交易日完全无 bar 时，才视为永久失联并核销，
    # 避免回测尾部把已退市/摘牌标的一直按旧价挂账。
    missing_quote_writeoff_enabled: bool = False
    missing_quote_writeoff_days: int = 20

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "initial_capital": self.initial_capital,
            "lot_size": self.lot_size,
            "commission_rate": self.commission_rate,
            "commission_min": self.commission_min,
            "stamp_tax_rate": self.stamp_tax_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
            "allow_short": self.allow_short,
            "t_plus_n": self.t_plus_n,
            "sell_credit_discount": self.sell_credit_discount,
            "suspension_discount_enabled": self.suspension_discount_enabled,
            "suspension_discount_start_days": self.suspension_discount_start_days,
            "suspension_discount_rate": self.suspension_discount_rate,
            "suspension_discount_max": self.suspension_discount_max,
            "retry_unfilled_bars": self.retry_unfilled_bars,
            "retry_sell_always": self.retry_sell_always,
            "retry_max_chase_pct": self.retry_max_chase_pct,
            "missing_quote_writeoff_enabled": self.missing_quote_writeoff_enabled,
            "missing_quote_writeoff_days": self.missing_quote_writeoff_days,
        }


# ============================================================================
# 回测引擎
# ============================================================================


class BacktestEngine:
    """事件驱动回测引擎

    核心职责：
    1. 管理事件队列
    2. 协调各组件处理事件
    3. 记录回测结果

    使用方式：
        engine = BacktestEngine(config, strategy, ...)
        result = engine.run(bars_by_date)
    """

    def __init__(
        self,
        config: BacktestConfig,
        strategy: Strategy,
        fill_model: FillModel,
        slippage: SlippageModel,
        fee_model: FeeModel,
        rules: MarketRules,
        universe_policy: Optional[CompositeUniversePolicy] = None,
        risk_manager: Optional[CompositeRiskManager] = None,
        compliance_engine: Optional["ComplianceEngine"] = None,
        stop_loss_manager: Optional[StopLossManager] = None,
        audit_logger: Optional[AuditLogger] = None,
        market_regime_detector: Optional[MarketRegimeDetector] = None,
        position_sizing: Optional[UnifiedPositionSizer] = None,
        degradation_manager: Optional[DegradationManager] = None,
        benchmark_symbol: str = "",
        weight_optimizer: Optional[Any] = None,
    ):
        self.config = config
        self.strategy = strategy
        self.fill_model = fill_model
        self.slippage = slippage
        self.fee_model = fee_model
        self.rules = rules
        self.universe_policy = universe_policy
        self.risk_manager = risk_manager
        self.compliance_engine = compliance_engine
        self.stop_loss_manager = stop_loss_manager
        self.audit_logger = audit_logger
        self.market_regime_detector = market_regime_detector
        self.position_sizing = position_sizing
        self.degradation_manager = degradation_manager
        self.benchmark_symbol = benchmark_symbol
        self.weight_optimizer = weight_optimizer

        # 内部组件
        self._event_queue = EventQueue()
        self._account: Optional[Account] = None
        self._position_manager: Optional[PositionManager] = None

        # 回测状态
        self._pending_orders: list[Order] = []
        self._order_reasons: dict[str, str] = {}  # order_id → reason
        self._order_counter: int = 0
        self._trade_counter: int = 0
        self._trades: list[TradeRecord] = []
        self._daily_snapshots: list[DailySnapshot] = []
        self._total_fees = FeeBreakdown(commission=0.0, stamp_tax=0.0, transfer_fee=0.0)
        self._exclusion_stats: dict[str, int] = {}
        self._prev_nav: Optional[float] = None
        self._halted: bool = False  # HALT_TRADING 标志
        self._halt_date: date | None = None  # HALT 触发日期
        self._halt_cooldown_bars: int = 20  # HALT 后冷却期（交易日数）
        self._halt_elapsed: int = 0  # 已经过的冷却交易日
        self._day_start_nav: float = 0.0  # 每日开始 NAV（用于日内亏损计算）
        self._last_prices: dict[str, float] = {}  # 持仓最新已知价格（停牌时使用上一交易日收盘价）
        self._suspend_day_counts: dict[str, int] = {}  # 连续停牌天数计数器
        self._missing_quote_day_counts: dict[str, int] = {}  # 连续无 bar 天数（用于永久缺行情核销）
        self._order_retry_counts: dict[str, int] = {}  # order_id → 已重试次数（用于未成交重试）
        self._order_reference_prices: dict[str, float] = {}  # order_id → 信号日收盘价（用于追涨判断）

    def run(self, bars_by_date: dict[date, dict[str, Bar]]) -> BacktestResult:
        """执行回测

        Args:
            bars_by_date: date -> {symbol -> Bar} 的数据

        Returns:
            回测结果
        """
        # 回测模式：抑制飞书告警（pretrade compliance noise, §13.18 Fail-Closed 不受影响）
        try:
            from aipha.execution import pretrade as _pretrade_mod
            _pretrade_mod._suppress_alerts = True
        except ImportError:
            pass

        # 初始化
        self._initialize()
        sorted_dates = sorted(bars_by_date.keys())

        if not sorted_dates:
            raise ValueError("No data to backtest")

        # 发送回测开始事件
        self._emit_event(EventType.BACKTEST_START, datetime.min, None)
        if self.audit_logger:
            self.audit_logger.log_system_start(self.config.to_dict())
        self.strategy.on_start()

        # 遍历每个交易日
        for td in sorted_dates:
            bars = bars_by_date[td]

            # 0. 数据质量检查：零成交量 bar 告警（M-5）
            zero_vol_syms = [
                sym for sym, bar in bars.items()
                if bar.volume is not None and bar.volume == 0
            ]
            if zero_vol_syms:
                logger.warning(
                    "%s: %d 只标的成交量为 0: %s",
                    td, len(zero_vol_syms),
                    zero_vol_syms[:10] if len(zero_vol_syms) > 10 else zero_vol_syms,
                )

            # 1. 处理日初事件（解锁 T+1 持仓）
            if self._daily_snapshots:
                self._position_manager.day_end_process(td)

            # 2. 处理待成交订单（next bar open）
            self._process_pending_orders(bars, td)

            # 3. 发送 Bar 事件并处理
            self._process_bar_event(bars, td)

            # 3.5 同 bar 成交模式：信号 bar 当期即刻撮合
            if self.fill_model.is_same_bar and self._pending_orders:
                self._process_pending_orders(bars, td)

            # 4. 日终快照
            self._take_daily_snapshot(bars, td)

        # 回测结束
        self.strategy.on_end()
        self._emit_event(EventType.BACKTEST_END, datetime.max, None)
        if self.audit_logger:
            self.audit_logger.log_system_stop("backtest_complete")

        # 恢复 pretrade 告警标志
        try:
            _pretrade_mod._suppress_alerts = False
        except Exception:  # silent-pass-exempt — 非金融关键路径，仅恢复告警标志
            pass

        return self._build_result(sorted_dates[0], sorted_dates[-1])

    def _initialize(self) -> None:
        """初始化回测状态"""
        self._event_queue.clear()
        self._pending_orders.clear()
        self._order_counter = 0
        self._trade_counter = 0
        self._trades.clear()
        self._daily_snapshots.clear()
        self._total_fees = FeeBreakdown(commission=0.0, stamp_tax=0.0, transfer_fee=0.0)
        self._exclusion_stats.clear()
        self._prev_nav = None
        self._halted = False
        self._halt_date = None
        self._halt_elapsed = 0
        self._day_start_nav = float(self.config.initial_capital)
        self._daily_returns_history: list[float] = []  # UnifiedPositionSizer 用
        self._last_prices.clear()
        self._suspend_day_counts.clear()
        self._missing_quote_day_counts.clear()

        # 重置 UnifiedPositionSizer
        if self.position_sizing:
            self.position_sizing.reset()

        # 创建账户和持仓管理器
        self._account = Account(initial_cash=Decimal(str(self.config.initial_capital)))
        self._position_manager = PositionManager(default_t_plus_n=self.config.t_plus_n)

    def _emit_event(self, event_type: EventType, timestamp: datetime, data: Any) -> None:
        """发送事件到事件队列（用于审计、测试、日志）。

        .. note::
            当前引擎使用命令式 for-bar 循环驱动，事件队列仅用于审计/测试。
            事件在 ``_initialize()`` 时清空，回测期间累积的事件数量通常可控
            （日线 10 年 ≈ 万级），不会造成显著内存压力。
            迁移为事件驱动架构后，此处改为 pop→dispatch 模式。
        """
        event = Event(
            timestamp=timestamp,
            sequence=self._event_queue.next_sequence(),
            event_type=event_type,
            data=data,
        )
        self._event_queue.push(event)

    def _emit_order_cancelled(
        self, order: Order, reason: str, trade_date: date,
    ) -> None:
        """发送订单取消事件并记录审计日志。"""
        self._emit_event(
            EventType.ORDER_CANCELLED,
            datetime.combine(trade_date, datetime.min.time()),
            {"order_id": order.order_id, "symbol": order.symbol,
             "side": order.side.value, "qty": order.qty, "reason": reason},
        )
        if self.audit_logger:
            self.audit_logger.log(
                AuditEventType.ORDER_CANCELED,
                AuditLevel.ALERT,
                f"Cancel: {order.side.value} {order.symbol} x{order.qty} — {reason}",
                data={"symbol": order.symbol, "side": order.side.value,
                      "qty": order.qty, "reason": reason},
                order_id=order.order_id,
                symbol=order.symbol,
            )

    def _process_pending_orders(self, bars: dict[str, Bar], trade_date: date) -> None:
        """处理待成交订单（卖单优先结算，确保卖出资金先到账）

        重要：买单只有在 available_cash 足够时才能成交，防止因卖单
        未成交（标的无行情）导致的负现金。未成交的买单其冻结资金会被释放。

        部分成交重入队：当 FillModel 返回 remaining_qty > 0 时，
        剩余数量会生成新订单重新排队，在后续 bar 继续尝试成交。
        """
        # 分离卖单和买单，先结算卖单以释放资金
        sell_orders = [o for o in self._pending_orders if o.side == Side.SELL]
        buy_orders = [o for o in self._pending_orders if o.side == Side.BUY]
        requeue: list[Order] = []

        # 1. 先处理所有卖单
        for order in sell_orders:
            if order.symbol not in bars:
                # 无行情 → 检查是否需要重试
                if self._should_retry_order(order, bars):
                    requeue.append(order)
                    logger.debug(
                        f"卖单 {order.symbol} 无行情，保留重试 "
                        f"(attempt {self._order_retry_counts.get(order.order_id, 0)})"
                    )
                else:
                    self._position_manager.unfreeze_for_sell(
                        order.symbol, order.qty, order.order_id
                    )
                    self._emit_order_cancelled(order, "卖单无行情", trade_date)
                    self._cleanup_retry_count(order.order_id)
                    logger.debug(
                        f"卖单 {order.symbol} 无行情，释放冻结持仓 qty={order.qty}"
                    )
                continue

            bar = bars[order.symbol]
            result = self.fill_model.try_fill(order=order, next_bar=bar, rules=self.rules)

            if result.fill:
                slipped_fill = self.slippage.apply(result.fill, bar)
                fees = self.fee_model.calc(slipped_fill)
                self._process_fill(slipped_fill, fees, trade_date)
                self._cleanup_retry_count(order.order_id)
                # 部分成交：释放未成交部分的冻结持仓，然后重新排队
                if result.remaining_qty > 0:
                    self._position_manager.unfreeze_for_sell(
                        order.symbol, result.remaining_qty, order.order_id
                    )
                    # 创建新订单重新排队
                    new_order = Order(
                        order_id=self._make_order_id(),
                        symbol=order.symbol,
                        side=Side.SELL,
                        qty=result.remaining_qty,
                        order_type=order.order_type,
                        t_submit=datetime.combine(trade_date, datetime.min.time()),
                    )
                    if self._position_manager.freeze_for_sell(
                        new_order.symbol, new_order.qty, new_order.order_id
                    ):
                        requeue.append(new_order)
                    logger.info(
                        f"卖单 {order.symbol} 部分成交: "
                        f"filled={result.fill.qty}, remaining={result.remaining_qty}"
                    )
            else:
                # 成交失败（涨停/无效价格等）→ 检查是否重试
                if self._should_retry_order(order, bars):
                    requeue.append(order)
                    logger.debug(
                        f"卖单 {order.symbol} 未成交，保留重试 "
                        f"(attempt {self._order_retry_counts.get(order.order_id, 0)})"
                    )
                else:
                    self._position_manager.unfreeze_for_sell(
                        order.symbol, order.qty, order.order_id
                    )
                    self._emit_order_cancelled(order, "卖单未成交", trade_date)
                    self._cleanup_retry_count(order.order_id)
                    logger.debug(
                        f"卖单 {order.symbol} 未成交，释放冻结持仓 qty={order.qty}"
                    )

        # 2. 再处理买单（检查现金是否足够）
        for order in buy_orders:
            if order.symbol not in bars:
                # 无行情 → 检查是否需要重试
                if self._should_retry_order(order, bars):
                    requeue.append(order)
                    logger.debug(
                        f"买单 {order.symbol} 无行情，保留重试 "
                        f"(attempt {self._order_retry_counts.get(order.order_id, 0)})"
                    )
                else:
                    frozen = self._account.get_frozen_for_order(order.order_id)
                    if frozen > 0:
                        self._account.unfreeze_cash(frozen, order.order_id, "买单无行情取消")
                    self._emit_order_cancelled(order, "买单无行情", trade_date)
                    self._cleanup_retry_count(order.order_id)
                continue

            bar = bars[order.symbol]
            result = self.fill_model.try_fill(order=order, next_bar=bar, rules=self.rules)

            if result.fill:
                slipped_fill = self.slippage.apply(result.fill, bar)
                fees = self.fee_model.calc(slipped_fill)
                # 检查结算后现金是否为负：成本 = 价格 * 数量 + 费用
                estimated_cost = (
                    Decimal(str(slipped_fill.price)) * Decimal(str(slipped_fill.qty))
                    + Decimal(str(fees.total))
                )
                if self._account.cash - estimated_cost < Decimal("-0.01"):
                    # 现金不足 → 释放冻结并跳过此买单
                    frozen = self._account.get_frozen_for_order(order.order_id)
                    if frozen > 0:
                        self._account.unfreeze_cash(frozen, order.order_id, "现金不足取消")
                    self._emit_order_cancelled(order, "现金不足", trade_date)
                    self._cleanup_retry_count(order.order_id)
                    logger.debug(
                        f"买单 {order.symbol} 因现金不足跳过: "
                        f"cash={self._account.cash}, cost={estimated_cost}"
                    )
                    continue
                self._process_fill(slipped_fill, fees, trade_date)
                self._cleanup_retry_count(order.order_id)
                # 部分成交：释放未成交部分对应的冻结资金，然后重新排队
                if result.remaining_qty > 0:
                    # 估算未成交部分冻结金额（按比例）
                    total_frozen = self._account.get_frozen_for_order(
                        order.order_id
                    )
                    if total_frozen > 0:
                        unfrozen_ratio = Decimal(str(result.remaining_qty)) / Decimal(str(order.qty))
                        unfrozen_amount = total_frozen * unfrozen_ratio
                        self._account.unfreeze_cash(
                            unfrozen_amount, order.order_id, "买单部分成交释放"
                        )
                    # 创建新订单重新排队
                    new_order = Order(
                        order_id=self._make_order_id(),
                        symbol=order.symbol,
                        side=Side.BUY,
                        qty=result.remaining_qty,
                        order_type=order.order_type,
                        t_submit=datetime.combine(trade_date, datetime.min.time()),
                    )
                    # 冻结新订单所需资金（使用当前 bar 收盘价 + 缓冲）
                    est_cost = Decimal(str(bar.close * result.remaining_qty * BUY_FREEZE_BUFFER))
                    if self._account.freeze_cash(est_cost, new_order.order_id, "partial requeue"):
                        requeue.append(new_order)
                    logger.info(
                        f"买单 {order.symbol} 部分成交: "
                        f"filled={result.fill.qty}, remaining={result.remaining_qty}"
                    )
            else:
                # 成交失败（涨停/零量等）→ 检查是否重试
                if self._should_retry_order(order, bars):
                    requeue.append(order)
                    logger.debug(
                        f"买单 {order.symbol} 未成交，保留重试 "
                        f"(attempt {self._order_retry_counts.get(order.order_id, 0)})"
                    )
                else:
                    frozen = self._account.get_frozen_for_order(order.order_id)
                    if frozen > 0:
                        self._account.unfreeze_cash(frozen, order.order_id, "买单未成交取消")
                    self._emit_order_cancelled(order, "买单未成交", trade_date)
                    self._cleanup_retry_count(order.order_id)

        self._pending_orders.clear()
        # 将部分成交重新排队的订单加入下一轮待处理
        if requeue:
            self._pending_orders.extend(requeue)

    def _should_retry_order(self, order: Order, bars: dict[str, Bar] | None = None) -> bool:
        """判断未成交订单是否应保留重试。

        三层判断逻辑：
        1. retry_unfilled_bars == 0 → 禁用重试，直接返回 False
        2. 卖单 + retry_sell_always → 只检查次数上限（卖出=降风险，应尽量成交）
        3. 买单 + retry_max_chase_pct > 0 → 若当前价格超过信号价×(1+max_chase_pct)则放弃
           （防追涨：涨停股次日继续涨 = 已不值得追）
        4. 已重试次数 < retry_unfilled_bars → 递增计数器并返回 True
        """
        max_retries = self.config.retry_unfilled_bars
        if max_retries <= 0:
            return False

        # 卖单无条件重试（仅受次数限制）
        if order.side == Side.SELL and self.config.retry_sell_always:
            current_count = self._order_retry_counts.get(order.order_id, 0)
            if current_count < max_retries:
                self._order_retry_counts[order.order_id] = current_count + 1
                return True
            return False

        # 买单：检查价格偏离
        if order.side == Side.BUY and self.config.retry_max_chase_pct > 0 and bars:
            ref_price = self._order_reference_prices.get(order.order_id)
            if ref_price and ref_price > 0:
                bar = bars.get(order.symbol)
                if bar:
                    # 用当前 bar 的收盘价判断偏离（重试决策在当日 bar 可用时发生）
                    price_change = (bar.close - ref_price) / ref_price
                    if price_change > self.config.retry_max_chase_pct:
                        logger.debug(
                            f"买单 {order.symbol} 放弃重试: 价格已上涨 "
                            f"{price_change:.2%} > max_chase {self.config.retry_max_chase_pct:.2%} "
                            f"(ref={ref_price:.2f}, cur={bar.close:.2f})"
                        )
                        return False

        # 通用次数检查
        current_count = self._order_retry_counts.get(order.order_id, 0)
        if current_count < max_retries:
            self._order_retry_counts[order.order_id] = current_count + 1
            return True
        return False

    def _cleanup_retry_count(self, order_id: str) -> None:
        """清理已终结订单的重试计数器和参考价格条目。"""
        self._order_retry_counts.pop(order_id, None)
        self._order_reference_prices.pop(order_id, None)

    def _process_bar_event(self, bars: dict[str, Bar], trade_date: date) -> None:
        """处理 Bar 事件"""
        # Advance the backtest clock so CircuitBreaker cooldown is measured
        # in simulated time (fixes permanent halt in fast backtests).
        if self.stop_loss_manager and hasattr(self.stop_loss_manager, '_backtest_clock'):
            bt_clock = self.stop_loss_manager._backtest_clock
            if bt_clock is not None:
                bt_clock.set_time(
                    datetime.combine(trade_date, datetime.min.time().replace(hour=15))
                )

        # 应用 Universe 过滤
        tradable_codes = self._apply_universe_filter(bars, trade_date)

        # 构建策略上下文
        prices = {s: b.close for s, b in bars.items()}
        # 更新最新已知价格（停牌股使用上一交易日价格）
        self._last_prices.update(prices)

        # 对连续多个交易日完全无行情的持仓执行引擎级核销，
        # 避免已退市/摘牌标的长期按旧价挂账在 final_positions / NAV 中。
        self._writeoff_missing_quote_positions(bars, trade_date)

        # 更新连续停牌天数计数器
        if self.config.suspension_discount_enabled:
            self._update_suspend_counters(bars)

        nav = self._calculate_nav(prices)

        # ---- 每日开盘：重置日内状态、喂养风控 ----
        if not self._daily_snapshots or self._daily_snapshots[-1].trade_date != trade_date:
            # 使用前一日收盘 NAV 作为日初基准（而非当日 close 计算的 NAV，
            # 否则日频下 day_start_nav == nav，日内亏损永远为 0）
            if self._prev_nav is not None:
                self._day_start_nav = self._prev_nav
            # else: 首个交易日保持 initial_capital
            # 重置 TurnoverLimit 每日计数
            if self.risk_manager:
                for c in self.risk_manager.constraints:
                    if hasattr(c, 'reset_daily'):
                        c.reset_daily(str(trade_date))

        # 喂养 DrawdownLimit NAV
        if self.risk_manager:
            for c in self.risk_manager.constraints:
                if hasattr(c, 'update_nav'):
                    c.update_nav(nav)

        # 喂养 DrawdownLimit 持仓状态（用于 peak_nav 重置判断）
        if self.risk_manager:
            has_pos = any(
                qty > 0
                for qty in self._position_manager.get_positions_dict().values()
            )
            for c in self.risk_manager.constraints:
                if hasattr(c, 'update_position_state'):
                    c.update_position_state(has_pos)

        # 喂养 ForceRedeemExposureLimit 转股价值（可转债场景）
        if self.risk_manager:
            for c in self.risk_manager.constraints:
                if hasattr(c, 'update_cb_values'):
                    # 使用当日价格作为 cb_value 的近似
                    c.update_cb_values(prices)

        # ---- CircuitBreaker 检查 ----
        if self.stop_loss_manager and self.stop_loss_manager.is_halted:
            logger.info(f"[{trade_date}] 熔断生效中，跳过交易")
            if self.audit_logger:
                self.audit_logger.log(
                    AuditEventType.RISK_CHECK_FAIL,
                    AuditLevel.RISK,
                    f"Circuit breaker active on {trade_date}",
                    data={"trade_date": str(trade_date)},
                )
            # 向 DegradationManager 上报熔断信号
            if self.degradation_manager:
                self.degradation_manager.report_signal(DegradationSignal(
                    source="circuit_breaker",
                    severity=DegradationLevel.HALTED,
                    message=f"CircuitBreaker halted on {trade_date}",
                ))
            return

        if self.stop_loss_manager:
            daily_loss_pct = max(0.0, (self._day_start_nav - nav) / self._day_start_nav) if self._day_start_nav > 0 else 0.0
            cb_triggered = self.stop_loss_manager.check_circuit_breaker(
                current_equity=Decimal(str(nav)),
                daily_loss_pct=daily_loss_pct,
            )
            if cb_triggered:
                logger.warning(f"[{trade_date}] 熔断触发，跳过当日交易")
                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.RISK_CHECK_FAIL,
                        AuditLevel.RISK,
                        f"Circuit breaker triggered on {trade_date}",
                        data={"trade_date": str(trade_date), "nav": nav,
                              "daily_loss_pct": daily_loss_pct},
                    )
                # 向 DegradationManager 上报熔断触发信号
                if self.degradation_manager:
                    self.degradation_manager.report_signal(DegradationSignal(
                        source="circuit_breaker",
                        severity=DegradationLevel.HALTED,
                        message=f"CircuitBreaker triggered: daily_loss={daily_loss_pct:.2%}",
                    ))
                return
            else:
                # 熔断未触发 → 清除可能存在的熔断信号
                if self.degradation_manager:
                    self.degradation_manager.clear_signal("circuit_breaker")

        # ---- HALT_TRADING 检查（含冷却恢复机制） ----
        if self._halted:
            self._halt_elapsed += 1
            if self._halt_elapsed >= self._halt_cooldown_bars:
                # 冷却期结束：检查回撤是否恢复到减仓阈值以下
                dd_recovered = True
                if self.risk_manager:
                    for c in self.risk_manager.constraints:
                        if hasattr(c, 'get_current_drawdown') and hasattr(c, 'reduce_position_at'):
                            if c.get_current_drawdown() >= c.reduce_position_at:
                                dd_recovered = False
                                break
                if dd_recovered:
                    logger.warning(
                        f"[{trade_date}] HALT_TRADING 冷却期({self._halt_cooldown_bars}日)结束"
                        f"且回撤已恢复，恢复交易"
                    )
                    self._halted = False
                    self._halt_date = None
                    self._halt_elapsed = 0
                else:
                    logger.info(
                        f"[{trade_date}] HALT冷却期已满但回撤未恢复，继续暂停"
                    )
                    return
            else:
                logger.info(
                    f"[{trade_date}] 交易已暂停(HALT_TRADING)，"
                    f"冷却中({self._halt_elapsed}/{self._halt_cooldown_bars})，跳过"
                )
                return

        # 更新市场状态检测器
        market_metadata: dict[str, Any] = {}
        position_scale = 1.0
        regime_scale = 1.0  # regime 缩放单独追踪（只影响BUY，不触发强制卖出）
        if self.market_regime_detector:
            idx_bar = bars.get(self.benchmark_symbol)
            if idx_bar:
                self.market_regime_detector.update(idx_bar.close)
            regime = self.market_regime_detector.detect()
            regime_scale = self.market_regime_detector.position_scale()
            market_metadata["market_regime"] = regime
            market_metadata["position_scale"] = regime_scale
            market_metadata["market_volatility"] = self.market_regime_detector.current_volatility()
            market_metadata["signal_detail"] = self.market_regime_detector.signal_detail()

        # UnifiedPositionSizer：波动率目标 + 回撤控制 + 估值择时 → 仓位缩减
        if self.position_sizing:
            # 收集当日所有标的收盘价作为估值指标输入
            market_values = [b.close for b in bars.values() if not b.is_suspended]
            ps_scale = self.position_sizing.compute_scale(
                self._daily_returns_history, nav,
                market_values=market_values,
            )
            position_scale = min(position_scale, ps_scale)
            market_metadata["position_sizing_scale"] = ps_scale

        # ---- DegradationManager 聚合信号 & 仓位缩减 ----
        if self.degradation_manager:
            # 将市场状态作为信号喂给降级管理器
            if self.market_regime_detector:
                regime = market_metadata.get("market_regime")
                if regime and hasattr(regime, "value"):
                    regime_name = regime.value if hasattr(regime, "value") else str(regime)
                else:
                    regime_name = str(regime) if regime else "UNKNOWN"
                # BEAR → WARNING, 其他 → 清除
                if regime_name in ("BEAR", "MarketRegime.BEAR"):
                    self.degradation_manager.report_signal(DegradationSignal(
                        source="market_regime",
                        severity=DegradationLevel.WARNING,
                        message=f"Market regime: {regime_name}",
                    ))
                else:
                    self.degradation_manager.clear_signal("market_regime")

            # DegradationManager 级别检查
            deg_level = self.degradation_manager.current_level
            market_metadata["degradation_level"] = deg_level.name
            market_metadata["degradation_position_scale"] = self.degradation_manager.position_scale

            if not self.degradation_manager.is_trading_allowed:
                logger.warning(
                    f"[{trade_date}] DegradationManager: HALTED, 跳过交易"
                )
                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.RISK_CHECK_FAIL,
                        AuditLevel.RISK,
                        f"DegradationManager HALTED on {trade_date}",
                        data={"trade_date": str(trade_date),
                              "degradation_level": deg_level.name},
                    )
                return

            # DegradationManager 的 position_scale 叠加到总 position_scale
            deg_scale = self.degradation_manager.position_scale
            if deg_scale < 1.0:
                position_scale = min(position_scale, deg_scale)

        # U-4: 统一 position_scale — 合并后的最终值写入 metadata
        # 确保策略层读到的 position_scale 包含所有 overlay 的贡献
        market_metadata["position_scale"] = position_scale

        context = StrategyContext(
            current_time=datetime.combine(trade_date, datetime.min.time().replace(hour=15)),
            positions=self._position_manager.get_positions_dict(),
            cash=float(self._account.available_cash),
            nav=nav,
            metadata={
                **market_metadata,
                **({"weight_optimizer": self.weight_optimizer}
                   if self.weight_optimizer is not None else {}),
            },
        )

        # ---- P1-1: 退市/ST 预警强制退出 ----
        delist_exit_intents = self._check_delist_force_exit(bars, context, trade_date)

        # 引擎级止损检查（在策略 on_bar 之前）
        stop_loss_intents: list[OrderIntent] = []
        if self.stop_loss_manager:
            stop_loss_intents = self._engine_stop_loss_check(bars, context, trade_date)

        # 调用策略
        intents = self.strategy.on_bar(bars, context)

        # 合并退市强制退出意图（最高优先级，不可被策略覆盖）
        all_forced_sell_symbols: set[str] = set()
        for di in delist_exit_intents:
            all_forced_sell_symbols.add(di.symbol)
            intents.append(di)

        # 合并引擎止损意图（去重：如果策略已产生同标的卖出则跳过）
        strategy_sell_symbols = {i.symbol for i in intents if i.side == Side.SELL}
        for sl_intent in stop_loss_intents:
            if sl_intent.symbol not in strategy_sell_symbols:
                intents.append(sl_intent)

        # ---- Position Sizing 主动减持：当 position_scale 较低时减持已有仓位 ----
        # R161 §4.1 修复：默认禁用(reduce_threshold=0.0)以避免死亡螺旋。
        # 启用时通过 UnifiedPositionSizer 的 reduce_threshold 属性配置。
        # 增加 cooldown（调仓日间隔才减持）+ scale floor（最低不低于 0.3）防止清仓。
        _ps_reduce_threshold = getattr(self.position_sizing, 'reduce_threshold', 0.0) if self.position_sizing else 0.0
        _ps_reduce_cooldown = getattr(self.position_sizing, 'reduce_cooldown_days', 5) if self.position_sizing else 5
        _ps_scale_floor = getattr(self.position_sizing, 'scale_floor', 0.3) if self.position_sizing else 0.3

        # 应用 floor：position_scale 不低于 scale_floor（防止接近清仓）
        if _ps_scale_floor > 0 and position_scale < _ps_scale_floor:
            position_scale = _ps_scale_floor

        if (_ps_reduce_threshold > 0
                and position_scale < _ps_reduce_threshold):
            # cooldown 检查：距上次主动减持需间隔 N 个交易日
            if not hasattr(self, '_ps_last_reduce_date'):
                self._ps_last_reduce_date = None
            days_since_reduce = (
                (trade_date - self._ps_last_reduce_date).days
                if self._ps_last_reduce_date else _ps_reduce_cooldown + 1
            )
            if days_since_reduce >= _ps_reduce_cooldown:
                sell_symbols_in_intents = {
                    i.symbol for i in intents if i.side == Side.SELL
                }
                current_positions = self._position_manager.get_positions_dict()
                reduced_any = False
                for symbol, qty in current_positions.items():
                    if qty <= 0 or symbol not in bars:
                        continue
                    if symbol in sell_symbols_in_intents:
                        continue  # 已有卖出意图，不重复
                    # 计算需要减持的数量 = 当前持仓 × (1 - position_scale)
                    reduce_qty = int(
                        qty * (1.0 - position_scale) / self.config.lot_size
                    ) * self.config.lot_size
                    if reduce_qty >= self.config.lot_size:
                        # 检查 T+1 可卖数量
                        pos = self._position_manager.get_position(symbol)
                        if pos and pos.available_to_sell >= reduce_qty:
                            intents.append(OrderIntent(
                                symbol=symbol,
                                side=Side.SELL,
                                qty=reduce_qty,
                                reason=f"PS主动减持 [scale={position_scale:.2f}]",
                            ))
                            reduced_any = True
                            logger.info(
                                f"[{trade_date}] PS主动减持: {symbol} "
                                f"减{reduce_qty}股 (scale={position_scale:.2f})"
                            )
                if reduced_any:
                    self._ps_last_reduce_date = trade_date

        # ---- 引擎级 MarketRegime position_scale 强制执行 ----
        # 对所有买入意图按 combined_scale 缩减数量
        # 策略层可能已通过 context.metadata["position_scale"] 调整了买入资金分配
        # （如 EquityRotationStrategy），此时引擎只需应用 regime_scale 避免双重缩减。
        # 未处理 position_scale 的策略（如 CBRotationStrategy）仍由引擎统一缩减。
        strategy_handles_ps = getattr(self.strategy, 'handles_position_scale', False)
        if strategy_handles_ps:
            combined_scale = regime_scale  # 策略已处理 position_scale，仅补充 regime
        else:
            combined_scale = position_scale * regime_scale
        if combined_scale < 1.0:
            scaled_intents = []
            for intent in intents:
                if intent.side == Side.BUY and combined_scale < 1.0:
                    scaled_qty = int(intent.qty * combined_scale / self.config.lot_size) * self.config.lot_size
                    if scaled_qty >= self.config.lot_size:
                        scaled_intents.append(OrderIntent(
                            symbol=intent.symbol,
                            side=intent.side,
                            qty=scaled_qty,
                            reason=f"{intent.reason or ''} [regime_scale={regime_scale:.2f} ps_scale={position_scale:.2f}]",
                        ))
                    # else: 缩减后不足一手，跳过
                else:
                    scaled_intents.append(intent)
            intents = scaled_intents

        # 分离卖出/买入意图，先卖后买以释放资金
        # 卖出意图不受 Universe 过滤限制：被剔除（ST/退市/停牌/强赎）的持仓
        # 必须允许止损/清仓操作，否则资金锁死（P0-3 安全修复）
        sell_intents = []
        buy_intents = []
        for intent in intents:
            if intent.side == Side.SELL:
                # 卖出：始终放行（止损/强赎/清仓不受 Universe 约束）
                sell_intents.append(intent)
            elif intent.symbol in tradable_codes:
                # 买入：必须在可交易池内
                buy_intents.append(intent)

        # 1. 先处理所有卖出意图
        estimated_sell_proceeds = Decimal(0)
        for intent in sell_intents:
            self._process_intent(intent, bars, context)
            if intent.symbol in bars:
                estimated_sell_proceeds += Decimal(str(
                    bars[intent.symbol].close * intent.qty
                ))

        # 2. 临时将预估卖出所得计入现金，使同批次买单可以冻结资金
        #    理由：A 股 T+1(可转债 T+0) 卖出资金当日可用于买入
        #    实际结算在 T+1 fill 时完成（先 settle_sell 再 settle_buy）
        sell_credit = Decimal(0)
        if estimated_sell_proceeds > 0 and buy_intents:
            sell_credit = estimated_sell_proceeds * Decimal(str(self.config.sell_credit_discount))
            self._account.credit_sell_proceeds(sell_credit, "同批次买卖预借")

        # 3. 处理买入意图（try-finally 确保 credit 一定被回收）
        try:
            for intent in buy_intents:
                self._process_intent(intent, bars, context)
        finally:
            # 4. 移除临时 credit（frozen_cash 已锁定买入份额，
            #    T+1 结算时 sell settle 先到账，再 buy settle 扣款）
            if sell_credit > 0:
                self._account.revoke_sell_credit(sell_credit, "同批次买卖回收预借")

    def _apply_universe_filter(
        self, bars: dict[str, Bar], trade_date: date
    ) -> set[str]:
        """应用 Universe 过滤"""
        if not self.universe_policy:
            return set(bars.keys())

        stocks_for_filter = [
            {
                "ts_code": symbol,
                "name": bars[symbol].metadata.get("name", symbol) if bars[symbol].metadata else symbol,
                "is_st": bars[symbol].metadata.get("is_st", False) if bars[symbol].metadata else False,
                "list_status": bars[symbol].metadata.get("list_status", "L") if bars[symbol].metadata else "L",
                "is_suspended": bars[symbol].metadata.get("is_suspended", False) if bars[symbol].metadata else False,
                "suspend_days": bars[symbol].metadata.get("suspend_days", 0) if bars[symbol].metadata else 0,
            }
            for symbol in bars.keys()
        ]
        filter_result = self.universe_policy.filter(stocks_for_filter, trade_date)

        # 统计剔除原因
        for reason, count in filter_result.get_exclusion_stats().items():
            key = reason.value if hasattr(reason, "value") else str(reason)
            self._exclusion_stats[key] = self._exclusion_stats.get(key, 0) + count

        return {s["ts_code"] for s in filter_result.tradable}

    def _engine_stop_loss_check(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
        trade_date: date | None = None,
    ) -> list[OrderIntent]:
        """引擎级止损检查：遍历持仓，用 StopLossManager 检查。

        **双止损机制说明**

        系统存在两层独立的止损机制，互不冲突、各司其职：

        1. **策略级止损** (EquityRotationStrategy._check_stop_loss):
           - 由策略基类实现，使用简单的 entry_price × (1 - stop_loss_pct) 判定。
           - 逻辑运行在 on_bar() 内，止损意图与调仓意图一起返回。
           - 止损标的会被写入 stop_loss_symbols 集合，防止同一 bar 被调仓买回。
           - 适合快速、无状态的价格跌幅止损。

        2. **引擎级止损** (本方法，通过 StopLossManager):
           - 由回测引擎在策略 on_bar() 之后独立执行。
           - 支持多种 TriggerAction（清仓、减仓、告警、停止交易）。
           - 支持组合级别熔断（circuit breaker：日内亏损超限 → 停止交易）。
           - 适合复杂、有状态的风控规则。

        两层止损按顺序执行：策略级先产出 stop_loss intents → 引擎级再补充检查。
        如果同一标的被两层都触发，引擎层的意图会在策略层已产出卖出意图后
        进入 _process_intent()，此时持仓可能已为 0，不会重复卖出。

        处理全部 TriggerAction：
        - CLOSE_POSITION: 全平
        - REDUCE_POSITION: 减仓至 50%
        - ALERT_ONLY: 仅记录审计日志
        - HALT_TRADING: 设 _halted 标志，后续 bar 全部跳过
        """
        intents: list[OrderIntent] = []
        if not self.stop_loss_manager:
            return intents

        for symbol, qty in list(context.positions.items()):
            if qty <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue

            # 更新价格
            self.stop_loss_manager.update_price(symbol, Decimal(str(bar.close)))

            # 检查止损
            results = self.stop_loss_manager.check(
                symbol=symbol,
                current_price=Decimal(str(bar.close)),
                position_qty=qty,
            )
            for result in results:
                if not result.triggered:
                    continue

                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.STOP_LOSS_TRIGGERED,
                        AuditLevel.RISK,
                        f"Stop loss triggered: {symbol} action={result.action.value}",
                        data={"symbol": symbol, "price": float(result.current_price),
                              "stop_price": float(result.stop_price),
                              "loss_pct": result.loss_pct,
                              "action": result.action.value},
                        symbol=symbol,
                    )

                if result.action == TriggerAction.CLOSE_POSITION:
                    intents.append(OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        reason=f"引擎止损(全平): {result.message}",
                    ))
                    break  # 一个标的只需一次止损

                elif result.action == TriggerAction.REDUCE_POSITION:
                    # 减仓至当前持仓的 50%
                    reduce_qty = max(
                        self.config.lot_size,
                        int(qty * 0.5 / self.config.lot_size) * self.config.lot_size,
                    )
                    if reduce_qty > 0 and reduce_qty <= qty:
                        intents.append(OrderIntent(
                            symbol=symbol,
                            side=Side.SELL,
                            qty=reduce_qty,
                            reason=f"引擎止损(减仓): {result.message}",
                        ))
                    break

                elif result.action == TriggerAction.ALERT_ONLY:
                    logger.warning(
                        f"止损告警(不平仓): {symbol} loss={result.loss_pct:.2%} — {result.message}"
                    )
                    # 不生成卖出意图，仅日志

                elif result.action == TriggerAction.HALT_TRADING:
                    logger.critical(
                        f"HALT_TRADING triggered by {symbol}: {result.message}"
                    )
                    self._halted = True
                    self._halt_date = trade_date
                    self._halt_elapsed = 0
                    # 暂停后也产生全平意图
                    intents.append(OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        reason=f"引擎止损(暂停交易): {result.message}",
                    ))
                    break

        return intents

    def _check_delist_force_exit(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
        trade_date: date,
    ) -> list[OrderIntent]:
        """P1-1: 退市/*ST 预警强制退出。

        检查当前持仓中是否存在以下情况：
        1. 标的 list_status 为 'D'(退市) 或 'P'(暂停上市) → 立即全量卖出
        2. 标的名称含 '*ST' → 立即全量卖出（*ST 比 ST 更严重）
        3. 标的当日无行情（从 bars 中消失）且有持仓 → 标记损失

        此检查在策略 on_bar 之前执行，优先级最高。
        """
        intents: list[OrderIntent] = []
        positions = context.positions

        for symbol, qty in positions.items():
            if qty <= 0:
                continue

            bar = bars.get(symbol)
            if bar is None:
                # 标的从行情中消失 → 无法成交，记录警告
                logger.warning(
                    "[%s] 持仓 %s (%d股) 无行情数据，"
                    "可能已退市/长期停牌，无法生成卖出订单",
                    trade_date, symbol, qty,
                )
                continue

            meta = bar.metadata or {}
            list_status = meta.get("list_status", "L")
            name = meta.get("name", "") or meta.get("stk_short_name", "")

            # 退市/暂停上市 → 强制清仓
            if list_status in ("D", "P"):
                reason = (
                    f"退市预警强制退出: list_status={list_status}, "
                    f"name={name}"
                )
                intents.append(OrderIntent(
                    symbol=symbol,
                    side=Side.SELL,
                    qty=qty,
                    reason=reason,
                ))
                logger.warning("[%s] %s", trade_date, reason)
                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.RISK_CHECK_FAIL,
                        AuditLevel.RISK,
                        reason,
                        data={
                            "symbol": symbol,
                            "qty": qty,
                            "list_status": list_status,
                            "trade_date": str(trade_date),
                        },
                        symbol=symbol,
                    )
                continue

            # *ST 标记 → 强制清仓（比普通 ST 更严重）
            if "*ST" in name.upper():
                reason = (
                    f"*ST预警强制退出: name={name}"
                )
                intents.append(OrderIntent(
                    symbol=symbol,
                    side=Side.SELL,
                    qty=qty,
                    reason=reason,
                ))
                logger.warning("[%s] %s", trade_date, reason)
                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.RISK_CHECK_FAIL,
                        AuditLevel.RISK,
                        reason,
                        data={
                            "symbol": symbol,
                            "qty": qty,
                            "name": name,
                            "trade_date": str(trade_date),
                        },
                        symbol=symbol,
                    )

        return intents

    def _process_intent(
        self,
        intent: OrderIntent,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> None:
        """处理订单意图"""
        # §13.15 / §R-2: 风控检查 — 三路径共享 pretrade.run_pretrade_checks()
        if self.risk_manager:
            from aipha.execution.pretrade import run_pretrade_checks

            live_positions = self._position_manager.get_positions_dict()
            live_nav = self._calculate_nav({s: bars[s].close for s in bars})
            prices_snap = {s: bars[s].close for s in bars}
            pt_result = run_pretrade_checks(
                intents=[(intent.symbol, intent.qty, intent.side.value)],
                current_positions=live_positions,
                nav=live_nav,
                prices=prices_snap,
                risk_manager=self.risk_manager,
            )
            if pt_result.rejected:
                reason = pt_result.rejected[0][3]
                logger.info(
                    "风控拦截: %s %s %d — %s",
                    intent.symbol, intent.side.value, intent.qty, reason,
                )
                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.RISK_CHECK_FAIL,
                        AuditLevel.RISK,
                        f"Risk check failed: {intent.symbol}",
                        data={"symbol": intent.symbol, "qty": intent.qty,
                              "violation": reason,
                              "message": reason},
                        symbol=intent.symbol,
                    )
                return  # 订单被拒绝

            # 如果有调整后数量，使用调整后的
            if pt_result.adjusted:
                new_qty = pt_result.adjusted[0][2]  # (symbol, orig, new, side)
                intent = OrderIntent(
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=new_qty,
                    reason=f"{intent.reason or ''} [风控调整: {intent.qty}->{new_qty}]",
                )

        # §R-3: 合规检查（停牌/涨跌停/参与率）— 与 Paper/Live 共享 pretrade 管线
        if self.compliance_engine and intent.symbol in bars:
            from aipha.execution.pretrade import run_compliance_checks

            bar = bars[intent.symbol]
            market_state = {
                intent.symbol: {
                    "is_halted": bool(bar.is_suspended),
                    "is_limit_up": bar.is_limit_up is True,
                    "is_limit_down": bar.is_limit_down is True,
                    "volume": float(bar.volume) if bar.volume else 0.0,
                },
            }
            prices_snap = {s: bars[s].close for s in bars}
            nav_snap = self._calculate_nav(prices_snap)
            comp_result = run_compliance_checks(
                intents=[(intent.symbol, intent.qty, intent.side.value)],
                prices=prices_snap,
                compliance_engine=self.compliance_engine,
                market_state=market_state,
                portfolio_total=float(nav_snap),
            )
            if comp_result.rejected:
                reason = comp_result.rejected[0][3] if comp_result.rejected else "compliance"
                logger.info(
                    "合规拦截: %s %s %d — %s",
                    intent.symbol, intent.side.value, intent.qty, reason,
                )
                if self.audit_logger:
                    self.audit_logger.log(
                        AuditEventType.RISK_CHECK_FAIL,
                        AuditLevel.RISK,
                        f"Compliance blocked: {intent.symbol}",
                        data={"symbol": intent.symbol, "qty": intent.qty,
                              "reason": reason},
                        symbol=intent.symbol,
                    )
                return  # 被合规引擎拒绝

        order = Order(
            order_id=self._make_order_id(),
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=OrderType.MARKET,
            t_submit=context.current_time,
        )
        # 保存交易原因映射
        if intent.reason:
            self._order_reasons[order.order_id] = intent.reason

        if intent.side == Side.BUY:
            # 买入需要冻结资金（缓冲覆盖 next-bar-open 与 close 的价差 + 滑点 + 费用）
            est_cost = Decimal(str(bars[intent.symbol].close * intent.qty * BUY_FREEZE_BUFFER))
            if self._account.freeze_cash(est_cost, order.order_id, "buy order"):
                self._pending_orders.append(order)
                # 记录信号日收盘价，用于条件重试时的价格偏离判断
                self._order_reference_prices[order.order_id] = bars[intent.symbol].close
        elif intent.side == Side.SELL:
            # 卖出需要冻结持仓
            if self._position_manager.freeze_for_sell(intent.symbol, intent.qty, order.order_id):
                self._pending_orders.append(order)

    def _process_fill(self, fill: Fill, fees: FeeBreakdown, trade_date: date) -> None:
        """处理成交"""
        if fill.side == Side.BUY:
            cost = Decimal(str(fill.notional))
            fee = Decimal(str(fees.total))
            self._account.settle_buy(
                order_id=fill.order_id,
                cost=cost,
                fee=fee,
                trade_date=trade_date,
            )
        else:
            proceeds = Decimal(str(fill.notional))
            fee = Decimal(str(fees.total))
            self._account.settle_sell(
                order_id=fill.order_id,
                proceeds=proceeds,
                fee=fee,
                trade_date=trade_date,
            )

        # 更新持仓
        self._position_manager.process_fill(fill, trade_date)

        # 记录交易
        self._trade_counter += 1
        reason = self._order_reasons.pop(fill.order_id, None)
        self._trades.append(
            TradeRecord(
                trade_id=f"TRD-{self._trade_counter:06d}",
                order_id=fill.order_id,
                symbol=fill.symbol,
                side=fill.side,
                qty=fill.qty,
                price=fill.price,
                notional=fill.notional,
                fees=fees,
                trade_time=fill.t_fill,
                trade_date=trade_date,
                reason=reason,
            )
        )

        # 累计费用
        self._total_fees = FeeBreakdown(
            commission=self._total_fees.commission + fees.commission,
            stamp_tax=self._total_fees.stamp_tax + fees.stamp_tax,
            transfer_fee=self._total_fees.transfer_fee + fees.transfer_fee,
        )

        # 喂养 TurnoverLimit（记录交易金额）
        if self.risk_manager:
            for c in self.risk_manager.constraints:
                if hasattr(c, 'record_trade'):
                    c.record_trade(fill.notional)

        # 通知策略实际成交价（用于止损等需要真实入场价的逻辑）
        self.strategy.on_fill(
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
        )

        # 引擎级止损管理器：注册/清除入场价
        if self.stop_loss_manager:
            if fill.side == Side.BUY:
                self.stop_loss_manager.register_entry(
                    fill.symbol, Decimal(str(fill.price))
                )
            elif fill.side == Side.SELL:
                self.stop_loss_manager.clear_position(fill.symbol)

        # 审计日志
        if self.audit_logger:
            self.audit_logger.log(
                AuditEventType.ORDER_FILLED,
                AuditLevel.TRADE,
                f"Fill: {fill.side.value} {fill.symbol} x{fill.qty} @{fill.price:.4f}",
                data={"symbol": fill.symbol, "side": fill.side.value,
                      "qty": fill.qty, "price": fill.price,
                      "notional": fill.notional,
                      "fees": fees.total},
                order_id=fill.order_id,
                symbol=fill.symbol,
            )

        # 事件队列
        self._emit_event(
            EventType.ORDER_FILLED,
            fill.t_fill,
            {"order_id": fill.order_id, "symbol": fill.symbol,
             "side": fill.side.value, "qty": fill.qty, "price": fill.price},
        )

    # ------------------------------------------------------------------
    # 停牌折价
    # ------------------------------------------------------------------

    def _update_suspend_counters(self, bars: dict[str, Bar]) -> None:
        """更新连续停牌天数计数器。

        当天有交易（is_suspended=False）的标的清零；
        停牌（is_suspended=True）或当日无 bar 的持仓标的累加 1。
        不再持有的标的从计数器中移除。
        """
        held_symbols = set(self._position_manager.get_positions_dict().keys())

        for symbol in held_symbols:
            bar = bars.get(symbol)
            if bar is None or bar.is_suspended:
                self._suspend_day_counts[symbol] = self._suspend_day_counts.get(symbol, 0) + 1
            else:
                self._suspend_day_counts[symbol] = 0

        # 清理不再持有的标的
        for symbol in list(self._suspend_day_counts.keys()):
            if symbol not in held_symbols:
                del self._suspend_day_counts[symbol]

    def _writeoff_missing_quote_positions(self, bars: dict[str, Bar], trade_date: date) -> None:
        """核销连续缺行情的持仓。

        语义：
        - 短期无行情：继续保留持仓，并由 _calculate_nav 使用 last_price 估值
        - 连续缺行情达到阈值：视为永久失联/已摘牌，直接从持仓中核销

        核销不会生成虚假成交记录，也不会回补现金；其效果是该持仓市值
        从 NAV 中一次性移除，使损失在核销日显式确认。
        """
        if not self.config.missing_quote_writeoff_enabled:
            return
        if self.config.missing_quote_writeoff_days <= 0:
            return

        held_positions = self._position_manager.get_positions_dict()
        for symbol, qty in list(held_positions.items()):
            if qty <= 0:
                self._missing_quote_day_counts.pop(symbol, None)
                continue

            if symbol in bars:
                self._missing_quote_day_counts[symbol] = 0
                continue

            missing_days = self._missing_quote_day_counts.get(symbol, 0) + 1
            self._missing_quote_day_counts[symbol] = missing_days
            if missing_days < self.config.missing_quote_writeoff_days:
                continue

            stale_price = self._last_prices.get(symbol)
            if stale_price is None:
                logger.warning(
                    "[%s] 核销持仓 %s: stale_price=None, 以零价值核销",
                    trade_date, symbol,
                )
            stale_value = float(stale_price) * qty if stale_price is not None else 0.0
            self._position_manager.positions.pop(symbol, None)
            self._last_prices.pop(symbol, None)
            self._suspend_day_counts.pop(symbol, None)
            self._missing_quote_day_counts.pop(symbol, None)

            logger.warning(
                "[%s] 持仓 %s 连续 %d 个交易日无行情，执行引擎级核销: qty=%d, stale_price=%s, stale_value=%.2f",
                trade_date,
                symbol,
                missing_days,
                qty,
                f"{stale_price:.4f}" if stale_price is not None else "None",
                stale_value,
            )
            if self.audit_logger:
                self.audit_logger.log(
                    AuditEventType.RISK_CHECK_FAIL,
                    AuditLevel.RISK,
                    f"Missing quote writeoff: {symbol}",
                    data={
                        "symbol": symbol,
                        "qty": qty,
                        "trade_date": str(trade_date),
                        "missing_quote_days": missing_days,
                        "stale_price": stale_price,
                        "stale_value": stale_value,
                    },
                    symbol=symbol,
                )

    def _suspension_discount(self, symbol: str) -> float:
        """计算某标的的停牌折价率。

        Returns:
            折价率 [0, max_discount]。0 = 无折价。
        """
        cfg = self.config
        if not cfg.suspension_discount_enabled:
            return 0.0
        days = self._suspend_day_counts.get(symbol, 0)
        if days <= cfg.suspension_discount_start_days:
            return 0.0
        excess = days - cfg.suspension_discount_start_days
        return min(excess * cfg.suspension_discount_rate, cfg.suspension_discount_max)

    def _apply_suspension_discounts(self, merged_prices: dict[str, float]) -> dict[str, float]:
        """对长停牌持仓价格应用折价。就地修改并返回 merged_prices。"""
        if not self.config.suspension_discount_enabled:
            return merged_prices
        for symbol in self._position_manager.get_positions_dict():
            discount = self._suspension_discount(symbol)
            if discount > 0 and symbol in merged_prices:
                merged_prices[symbol] = merged_prices[symbol] * (1 - discount)
        return merged_prices

    def _calculate_nav(self, prices: dict[str, float]) -> float:
        """计算 NAV

        对于当天有 bar 数据的持仓使用当天 close;
        对于停牌/无 bar 的持仓使用最后已知价格（避免将停牌股市值计为 0）。
        长停牌持仓在启用 suspension_discount_enabled 时自动折价。
        """
        cash = float(self._account.available_cash + self._account.frozen_cash)
        # 合并价格：当天价格优先，缺失时回退到最后已知价格
        merged_prices = {**self._last_prices, **prices}
        # 应用长停牌折价
        self._apply_suspension_discounts(merged_prices)
        position_value = sum(
            merged_prices.get(symbol, 0) * qty
            for symbol, qty in self._position_manager.get_positions_dict().items()
        )
        return cash + position_value

    def _take_daily_snapshot(self, bars: dict[str, Bar], trade_date: date) -> None:
        """记录每日快照"""
        prices = {s: b.close for s, b in bars.items()}
        # 更新最新已知价格（确保快照也使用停牌前价格）
        self._last_prices.update(prices)
        nav = self._calculate_nav(prices)

        # 计算收益率
        if self._prev_nav is not None and self._prev_nav > 0:
            daily_return = (nav - self._prev_nav) / self._prev_nav
        else:
            daily_return = 0.0

        initial_nav = self.config.initial_capital
        cumulative_return = (nav - initial_nav) / initial_nav if initial_nav > 0 else 0.0

        positions = self._position_manager.get_positions_dict()
        # 快照中的持仓市值也需应用停牌折价（与 NAV 一致）
        snapshot_prices = {**self._last_prices, **prices}
        self._apply_suspension_discounts(snapshot_prices)
        position_values = {
            symbol: snapshot_prices.get(symbol, 0) * qty for symbol, qty in positions.items()
        }

        snapshot = DailySnapshot(
            trade_date=trade_date,
            nav=nav,
            # 记录总现金（含冻结），避免 pending 买单导致的负 available_cash 伪影；
            # T+1 结算前 frozen_cash 中的资金仍属于账户
            cash=float(self._account.cash),
            frozen_cash=float(self._account.frozen_cash),
            positions=dict(positions),
            position_values=position_values,
            daily_return=daily_return,
            cumulative_return=cumulative_return,
        )
        self._daily_snapshots.append(snapshot)
        self._prev_nav = nav

        # 追踪日收益率序列供 UnifiedPositionSizer 使用
        self._daily_returns_history.append(daily_return)

    def _make_order_id(self) -> str:
        """生成订单 ID"""
        self._order_counter += 1
        return f"ORD-{self._order_counter:06d}"

    def _build_result(self, start_date: date, end_date: date) -> BacktestResult:
        """构建回测结果"""
        final_snapshot = self._daily_snapshots[-1] if self._daily_snapshots else None

        return BacktestResult(
            strategy_name=self.strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.config.initial_capital,
            final_nav=final_snapshot.nav if final_snapshot else self.config.initial_capital,
            final_cash=float(self._account.available_cash) if self._account else 0.0,
            final_positions=final_snapshot.positions if final_snapshot else {},
            daily_snapshots=list(self._daily_snapshots),
            trades=list(self._trades),
            total_trades=len(self._trades),
            total_fees=self._total_fees,
            exclusion_stats=dict(self._exclusion_stats),
            config={**self.config.to_dict(), "fill_model_name": self.fill_model.name},
        )
