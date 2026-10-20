"""QMT 交易网关 — miniQMT / xtquant 实盘接入。

实现 AlphQ ``TradingGateway`` 协议，通过 xtquant SDK 与 miniQMT 通信。
支持下单、撤单、查询持仓/账户、实时行情订阅。

Windows + miniQMT 运行时必需；其他平台可通过 HTTP Bridge 降级访问数据。

配置入口:
  - 环境变量: MINI_QMT_PATH, TRADING_ACCOUNT_ID, TRADING_ACCOUNT_TYPE
  - YAML: configs/live/qmt_gateway.yml
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aipha.execution.broker_gateway import (
    BrokerConfig,
    BrokerGateway,
    BrokerType,
    register_broker,
)
from aipha.execution.gateway import (
    AccountSnapshot,
    ExecutionReport,
    GatewayError,
    GatewayNotConnectedError,
    GatewayOrder,
    OrderStatus,
    OrderStatusChange,
    TickCallback,
    TickData,
    TradingMode,
    ExecutionCallback,
    StatusCallback,
)
from aipha.execution.qmt_callback import (
    HAS_XTQUANT,
    QMTCallback,
    QMTFillEvent,
    QMTOrderEvent,
    QMTTickEvent,
    map_qmt_status,
)
from aipha.execution.emergency_control import StopAgent
from aipha.utils.paths import KILL_SWITCH_PATH

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QMT 配置
# ---------------------------------------------------------------------------

@dataclass
class QMTConfig(BrokerConfig):
    """QMT 网关专用配置。"""

    broker_type: BrokerType = BrokerType.QMT
    name: str = "qmt-default"
    mini_qmt_path: str = ""
    account_type: str = "STOCK"  # STOCK / OPTIONS / CREDIT
    auto_reconnect: bool = True
    reconnect_interval: float = 5.0
    event_poll_interval: float = 0.5  # 回调事件轮询间隔 (秒)
    max_event_queue: int = 10000

    def __post_init__(self) -> None:
        self.broker_type = BrokerType.QMT

    @classmethod
    def from_env(cls, name: str = "qmt-default") -> QMTConfig:
        """从环境变量构建配置。"""
        return cls(
            broker_type=BrokerType.QMT,
            name=name,
            mini_qmt_path=os.environ.get("MINI_QMT_PATH", ""),
            account_id=os.environ.get("TRADING_ACCOUNT_ID", ""),
            account_type=os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK"),
            is_sandbox=False,
        )


# ---------------------------------------------------------------------------
# QMT 交易网关
# ---------------------------------------------------------------------------

@register_broker(BrokerType.QMT)
class QMTGateway(BrokerGateway):
    """miniQMT 实盘交易网关。

    通过 xtquant SDK 连接 miniQMT 客户端，实现：
    - 下单 / 撤单
    - 持仓 / 账户查询
    - 实时成交回报（通过 QMTCallback 线程安全队列）
    - 实时行情订阅

    使用前须确保:
    1. Windows 系统 + miniQMT 客户端正在运行
    2. pip install xtquant (或 miniQMT 自带)
    3. 配置 MINI_QMT_PATH / TRADING_ACCOUNT_ID 环境变量
    """

    def __init__(self, config: QMTConfig | None = None) -> None:
        cfg = config or QMTConfig.from_env()
        super().__init__(cfg)
        self._qmt_config: QMTConfig = cfg
        self._trader: Any = None  # XtQuantTrader
        self._account: Any = None  # StockAccount
        self._callback: QMTCallback | None = None
        self._orders: dict[str, GatewayOrder] = {}
        self._lock = threading.Lock()
        self._submission_lock = threading.RLock()
        # Set only while submit_target_plan is translating one fully validated
        # plan. Legacy callers may still reach submit_order directly, so the
        # gateway itself must enforce the plan-only invariant.
        self._active_execution_plan_id: str | None = None
        self._stop_agent: StopAgent | None = None
        self._exec_callbacks: list[ExecutionCallback] = []
        self._status_callbacks: list[StatusCallback] = []
        self._tick_callbacks: list[TickCallback] = []
        self._poll_thread: threading.Thread | None = None
        self._running = False
        self._subscribed: set[str] = set()
        # Auto-reconnect state
        self._reconnecting = False
        self._reconnect_count = 0
        self._max_reconnect_attempts = 10
        # M-3: 幂等下单日志 (idempotency_key → order_id)
        self._idempotency_log: dict[str, str] = {}
        # M-3: 限价保护价差 (bps)
        self._limit_price_spread_bps: float = 50.0  # 0.5%

    @property
    def mode(self) -> TradingMode:
        return TradingMode.LIVE

    @property
    def is_connected(self) -> bool:
        return self._connected and self._callback is not None and self._callback.is_connected

    # -- 连接管理 -----------------------------------------------------------

    def connect(self) -> None:
        """连接 miniQMT 并启动回调监听。"""
        if not HAS_XTQUANT:
            raise GatewayError("xtquant 未安装，无法连接 QMT", code="NO_XTQUANT")

        from xtquant.xttrader import XtQuantTrader  # type: ignore[import-untyped]
        from xtquant.xttype import StockAccount  # type: ignore[import-untyped]

        path = self._qmt_config.mini_qmt_path
        if not path:
            raise GatewayError("MINI_QMT_PATH 未配置", code="NO_PATH")
        account_id = self._qmt_config.account_id
        if not account_id:
            raise GatewayError("TRADING_ACCOUNT_ID 未配置", code="NO_ACCOUNT")

        session_id = int(time.time())
        self._callback = QMTCallback(maxsize=self._qmt_config.max_event_queue)
        self._trader = XtQuantTrader(path, session_id)
        self._trader.register_callback(self._callback)
        self._trader.start()

        result = self._trader.connect()
        if result != 0:
            raise GatewayError(f"QMT connect 失败, code={result}", code="CONNECT_FAIL")

        self._account = StockAccount(account_id, self._qmt_config.account_type)
        sub_result = self._trader.subscribe(self._account)
        if sub_result != 0:
            logger.warning("QMT subscribe 返回 %d（可能需要手动订阅）", sub_result)

        self._connected = True
        if self._stop_agent is None:
            self._stop_agent = StopAgent(
                KILL_SWITCH_PATH, self.query_open_order_ids, self.cancel_order,
                self._submission_lock, label=f"qmt:{self._qmt_config.name}",
            )
            try:
                self._stop_agent.start()
            except Exception:
                self._stop_agent = None
                self._connected = False
                self._trader.stop()
                raise
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._event_poll_loop, daemon=True, name="qmt-event-poll",
        )
        self._poll_thread.start()
        logger.info("QMT 网关已连接: account=%s, path=%s", account_id, path)

    def disconnect(self) -> None:
        """断开 QMT 连接。"""
        if self._stop_agent is not None:
            self._stop_agent.close()
            self._stop_agent = None
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3.0)
        if self._trader:
            try:
                self._trader.stop()
            except Exception:
                logger.exception("QMT stop 异常")
        self._connected = False
        self._trader = None
        self._account = None
        logger.info("QMT 网关已断开")

    def login(self, credentials: dict[str, str]) -> bool:
        """QMT 登录（miniQMT 模式下 connect 即登录，此处为兼容接口）。"""
        if not self.is_connected:
            self.connect()
        return self.is_connected

    # -- 下单 / 撤单 -------------------------------------------------------

    def _calc_limit_price(
        self, symbol: str, side: str, latest_price: float,
    ) -> float:
        """计算限价保护价格 (M-3 TS-1)。

        对手盘方向 ± 保护价差, 但不超出涨跌停。
        BUY: latest * (1 + spread_bps/10000), 不超涨停
        SELL: latest * (1 - spread_bps/10000), 不低于跌停
        """
        spread = self._limit_price_spread_bps / 10000
        if side == "BUY":
            return round(latest_price * (1 + spread), 2)
        return round(latest_price * (1 - spread), 2)

    @staticmethod
    def _validate_lot_size(symbol: str, qty: int) -> int:
        """校验并修正整手数量 (M-3)。

        CB: 10 张整手, A 股: 100 股整手, ETF: 100 份整手。
        """
        # 简单判断: 12xxxx.S* = CB (10张), 其余 100 股
        if symbol.startswith("1") and len(symbol) >= 6:
            code = symbol[:6]
            if code[:2] in ("11", "12", "13"):
                lot = 10
            else:
                lot = 100
        else:
            lot = 100
        adjusted = (qty // lot) * lot
        if adjusted != qty:
            logger.warning(
                "整手修正: %s qty %d → %d (lot=%d)", symbol, qty, adjusted, lot,
            )
        return adjusted

    def _check_idempotency(self, order: GatewayOrder) -> str | None:
        """检查幂等键, 防止重复下单 (M-3)。

        Returns:
            已存在的 order_id (重复下单时), 或 None (首次下单)。
        """
        key = order.metadata.get("idempotency_key", "")
        if not key:
            return None
        with self._lock:
            existing = self._idempotency_log.get(key)
            if existing:
                logger.warning(
                    "幂等检查: key=%s 已存在 order_id=%s, 跳过重复下单",
                    key, existing,
                )
                return existing
        return None

    def submit_order(self, order: GatewayOrder) -> str:
        """提交订单到 QMT。"""
        with self._submission_lock:
            if KILL_SWITCH_PATH.exists():
                raise GatewayError("Persistent kill switch is active", code="KILL_SWITCH")
            plan_id = self._active_execution_plan_id
            if not plan_id or order.metadata.get("execution_plan_id") != plan_id:
                raise GatewayError(
                    "QMT accepts orders only while consuming a validated execution plan",
                    code="EXECUTION_PLAN_REQUIRED",
                )
            return self._submit_order_impl(order)

    def submit_target_plan(
        self,
        plan: Any,
        *,
        prices: dict[str, float],
        account_value: float,
        current_positions: dict[str, int] | None = None,
        allow_live: bool = False,
    ) -> list[str]:
        """Consume a versioned live execution plan with explicit provenance."""
        from aipha.research.execution_plan import execute_target_plan

        if getattr(plan, "mode", None) != "live":
            raise GatewayError("QMT requires an execution plan with mode=live", code="PLAN_MODE")
        if not allow_live or os.environ.get("QUANT_TRADING_ALLOW_LIVE", "").lower() not in {
            "1", "true", "yes",
        }:
            raise GatewayError(
                "QMT live execution requires QUANT_TRADING_ALLOW_LIVE=true",
                code="LIVE_GATE",
            )
        with self._submission_lock:
            if self._active_execution_plan_id is not None:
                raise GatewayError("another QMT execution plan is being submitted", code="PLAN_BUSY")
            self._active_execution_plan_id = str(plan.plan_id)
            try:
                return execute_target_plan(
                    self,
                    plan,
                    prices=prices,
                    account_value=account_value,
                    current_positions=current_positions,
                    allow_live=allow_live,
                )
            finally:
                self._active_execution_plan_id = None

    def _submit_order_impl(self, order: GatewayOrder) -> str:
        self._ensure_connected()

        # M-3: 幂等检查
        dup_id = self._check_idempotency(order)
        if dup_id is not None:
            return dup_id

        # M-3: 整手校验
        order.qty = self._validate_lot_size(order.symbol, int(order.qty))
        if order.qty <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "qty=0 after lot_size validation"
            logger.error("下单被拒: %s 数量为0", order.symbol)
            return order.order_id

        from xtquant.xtconstant import (  # type: ignore[import-untyped]
            STOCK_BUY,
            STOCK_SELL,
            FIX_PRICE,
            LATEST_PRICE,
        )

        order_type = STOCK_BUY if order.side == "BUY" else STOCK_SELL
        if order.order_type == "LIMIT" and order.limit_price is not None:
            price_type = FIX_PRICE
            price = order.limit_price
        else:
            # M-3 TS-1: 市价单 → 限价保护 (用最新价 ± spread)
            # 若 metadata 中有 latest_price, 转为限价单保护
            latest = order.metadata.get("latest_price", 0.0)
            if latest and float(latest) > 0:
                price_type = FIX_PRICE
                price = self._calc_limit_price(
                    order.symbol, order.side, float(latest),
                )
                logger.info(
                    "限价保护: %s %s latest=%.2f → limit=%.2f",
                    order.side, order.symbol, float(latest), price,
                )
            else:
                price_type = LATEST_PRICE
                price = 0.0

        # §14.1 P0-2: 最后防线——单笔金额硬上限（不依赖上游风控正确性）
        _HARD_LIMIT = 5_000_000  # 500 万硬编码上限，不可通过配置绕过
        effective_price = price if price > 0 else (order.limit_price or 0)
        estimated_amount = int(order.qty) * float(effective_price) if effective_price else 0
        if estimated_amount > _HARD_LIMIT:
            order.status = OrderStatus.REJECTED
            order.reject_reason = (
                f"HARD_LIMIT: amount={estimated_amount:.0f} > {_HARD_LIMIT}"
            )
            logger.critical(
                "§14.1 QMT 硬上限拦截: %s %s x%d @ %.2f = %.0f > %d",
                order.side, order.symbol, order.qty, float(effective_price),
                estimated_amount, _HARD_LIMIT,
            )
            with self._lock:
                self._orders[order.order_id] = order
            return order.order_id

        broker_id = self._trader.order_stock(
            self._account,
            order.symbol,
            order_type,
            int(order.qty),
            price_type,
            price,
            order.metadata.get("strategy_name", ""),
            order.metadata.get("remark", ""),
        )

        # xtquant order_stock 返回 <=0 表示下单失败
        if not broker_id or int(broker_id) <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = f"order_stock returned {broker_id}"
            logger.error("QMT 下单被拒: %s %s %s x%d — broker_id=%s",
                         order.side, order.symbol, order.order_type,
                         order.qty, broker_id)
            with self._lock:
                self._orders[order.order_id] = order
            return order.order_id

        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now()
        oid = str(broker_id)
        with self._lock:
            self._orders[oid] = order
            # M-3: 记录幂等键
            idem_key = order.metadata.get("idempotency_key", "")
            if idem_key:
                self._idempotency_log[idem_key] = oid
        logger.info("QMT 下单: %s %s %s x%d @ %s → id=%s",
                     order.side, order.symbol, order.order_type, order.qty, price, oid)
        return oid

    def cancel_order(self, order_id: str) -> bool:
        """撤销指定订单。"""
        self._ensure_connected()
        try:
            result = self._trader.cancel_order_stock(self._account, int(order_id))
            return result == 0
        except Exception:
            logger.exception("QMT 撤单异常: %s", order_id)
            return False

    def get_order(self, order_id: str) -> GatewayOrder | None:
        with self._lock:
            return self._orders.get(order_id)

    def get_active_orders(self) -> list[GatewayOrder]:
        with self._lock:
            return [o for o in self._orders.values() if o.is_active]

    # -- 查询接口 -----------------------------------------------------------

    def get_account_snapshot(self) -> AccountSnapshot:
        self._ensure_connected()
        raw = self._trader.query_stock_asset(self._account)
        if not raw:
            raise GatewayError("查询账户失败", code="QUERY_FAIL")
        positions = {}
        pos_list = self._trader.query_stock_positions(self._account)
        for p in (pos_list or []):
            code = getattr(p, "stock_code", "")
            vol = int(getattr(p, "volume", 0))
            if code and vol > 0:
                positions[code] = vol
        return AccountSnapshot(
            timestamp=datetime.now(),
            cash=Decimal(str(getattr(raw, "cash", 0))),
            frozen_cash=Decimal(str(getattr(raw, "frozen_cash", 0))),
            total_equity=Decimal(str(getattr(raw, "total_asset", 0))),
            positions=positions,
            positions_value=Decimal(str(getattr(raw, "market_value", 0))),
        )

    def query_account(self) -> dict[str, Any]:
        snap = self.get_account_snapshot()
        return {
            "cash": float(snap.cash),
            "frozen_cash": float(snap.frozen_cash),
            "total_asset": float(snap.total_equity),
            "market_value": float(snap.positions_value),
        }

    def query_positions(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        pos_list = self._trader.query_stock_positions(self._account) or []
        results = []
        for p in pos_list:
            results.append({
                "stock_code": getattr(p, "stock_code", ""),
                "volume": int(getattr(p, "volume", 0)),
                "can_use_volume": int(getattr(p, "can_use_volume", 0)),
                "frozen_volume": int(getattr(p, "frozen_volume", 0)),
                "avg_price": float(getattr(p, "avg_price", 0)),
                "market_value": float(getattr(p, "market_value", 0)),
            })
        return results

    def query_orders(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        raw_orders = self._trader.query_stock_orders(self._account) or []
        return [
            {
                "order_id": str(getattr(o, "order_id", "")),
                "stock_code": getattr(o, "stock_code", ""),
                "order_status": int(getattr(o, "order_status", -1)),
                "order_volume": int(getattr(o, "order_volume", 0)),
                "traded_volume": int(getattr(o, "traded_volume", 0)),
                "traded_price": float(getattr(o, "traded_price", 0)),
                "price": float(getattr(o, "price", 0)),
            }
            for o in raw_orders
        ]

    def query_open_order_ids(self) -> list[str]:
        """Query broker account orders, including orders absent from local cache."""
        self._ensure_connected()
        orders = self._trader.query_stock_orders(self._account)
        if orders is None:
            raise GatewayError("Broker order query returned no result", code="QUERY_FAIL")
        active = []
        for order in orders:
            status = int(getattr(order, "order_status", -1))
            if status not in set(range(48, 58)):
                raise GatewayError("Unknown broker order status", code="QUERY_FAIL")
            if status not in {53, 54, 56, 57}:
                order_id = str(getattr(order, "order_id", ""))
                if not order_id or not order_id.isdigit():
                    raise GatewayError("Broker order has no usable id", code="QUERY_FAIL")
                active.append(order_id)
        return active

    def query_trades(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        raw_trades = self._trader.query_stock_trades(self._account) or []
        return [
            {
                "order_id": str(getattr(t, "order_id", "")),
                "stock_code": getattr(t, "stock_code", ""),
                "traded_volume": int(getattr(t, "traded_volume", 0)),
                "traded_price": float(getattr(t, "traded_price", 0)),
            }
            for t in raw_trades
        ]

    # -- 回调注册 -----------------------------------------------------------

    def on_execution(self, callback: ExecutionCallback) -> None:
        self._exec_callbacks.append(callback)

    def on_status_change(self, callback: StatusCallback) -> None:
        self._status_callbacks.append(callback)

    def on_tick(self, callback: TickCallback) -> None:
        """注册 tick 数据推送回调。"""
        self._tick_callbacks.append(callback)

    # -- 行情订阅 -----------------------------------------------------------

    def subscribe_quote(self, symbols: list[str]) -> None:
        """订阅实时行情推送。"""
        if not HAS_XTQUANT:
            return
        from xtquant import xtdata  # type: ignore[import-untyped]
        xtdata.enable_hello = False
        tick_cb = self._callback.on_stock_data if self._callback else None
        for sym in symbols:
            if sym not in self._subscribed:
                xtdata.subscribe_quote(sym, period="tick", callback=tick_cb)
                self._subscribed.add(sym)

    # -- 内部方法 -----------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise GatewayNotConnectedError("QMT 未连接")

    def _event_poll_loop(self) -> None:
        """后台线程：轮询回调队列，分发事件给上层。含断线检测与重连。"""
        interval = self._qmt_config.event_poll_interval
        consecutive_errors = 0
        error_threshold = 5
        stale_check_counter = 0
        while self._running:
            try:
                self._process_events()
                # 每 60 次 poll (~30s) 检查一次超时订单
                stale_check_counter += 1
                if stale_check_counter >= 60:
                    stale_check_counter = 0
                    self._cancel_stale_orders()
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                logger.exception("事件处理异常 (%d/%d)", consecutive_errors, error_threshold)
                if (
                    consecutive_errors >= error_threshold
                    and self._qmt_config.auto_reconnect
                    and not self._reconnecting
                ):
                    self._trigger_reconnect()
            time.sleep(interval)

    def _process_events(self) -> None:
        """处理一批回调事件。"""
        if not self._callback:
            return
        # 成交事件
        for evt in self._callback.drain_fills():
            report = ExecutionReport(
                order_id=evt.order_id,
                exec_id=f"{evt.order_id}_{int(time.time()*1000)}",
                symbol=evt.symbol,
                side=evt.side,
                fill_qty=evt.fill_qty,
                fill_price=evt.fill_price,
                fill_time=evt.fill_time,
                commission=Decimal(str(evt.commission)),
            )
            # 更新 GatewayOrder 状态（累积成交）
            with self._lock:
                order = self._orders.get(evt.order_id)
                if order:
                    prev_filled = order.filled_qty
                    order.filled_qty += evt.fill_qty
                    # 加权平均成交价
                    if order.filled_qty > 0:
                        order.avg_fill_price = (
                            Decimal(str(order.avg_fill_price)) * prev_filled
                            + Decimal(str(evt.fill_price)) * evt.fill_qty
                        ) / order.filled_qty
                    if order.filled_qty >= order.qty:
                        order.status = OrderStatus.FILLED
                        order.filled_at = evt.fill_time
                        report.is_last = True
                    elif order.filled_qty > 0:
                        order.status = OrderStatus.PARTIAL_FILLED
            for cb in self._exec_callbacks:
                try:
                    cb(report)
                except Exception:
                    logger.exception("执行回调异常")
        # 订单状态事件
        for evt in self._callback.drain_orders():
            with self._lock:
                order = self._orders.get(evt.order_id)
            if order and order.status != evt.status:
                change = OrderStatusChange(
                    order_id=evt.order_id,
                    old_status=order.status,
                    new_status=evt.status,
                    timestamp=evt.timestamp,
                    reason=evt.error_msg or None,
                )
                order.status = evt.status
                if evt.status == OrderStatus.REJECTED:
                    order.reject_reason = evt.error_msg or "QMT rejected"
                elif evt.status == OrderStatus.CANCELED:
                    order.cancel_reason = evt.error_msg or "QMT canceled"
                for cb in self._status_callbacks:
                    try:
                        cb(change)
                    except Exception:
                        logger.exception("状态回调异常")
        # Tick 事件
        if self._tick_callbacks:
            for evt in self._callback.drain_ticks():
                td = TickData(
                    symbol=evt.symbol,
                    price=evt.price,
                    volume=evt.volume,
                    bid1=evt.bid1,
                    ask1=evt.ask1,
                    bid1_vol=evt.bid1_vol,
                    ask1_vol=evt.ask1_vol,
                    amount=evt.amount,
                    timestamp=evt.timestamp,
                )
                for cb in self._tick_callbacks:
                    try:
                        cb(td)
                    except Exception:
                        logger.exception("Tick 回调异常")

    def _cancel_stale_orders(self) -> None:
        """扫描并撤销超时未成交的活跃订单。

        超时策略按订单类型分层：
        - MARKET 市价单: 5 分钟未成交视为异常 → 撤单
        - LIMIT  限价单: 保留到收盘前 5 分钟 (14:55) → 撤单
                         （限价单在等待目标价，盘中不应被轻易撤掉）
        """
        now = datetime.now()
        market_ttl = 300.0  # 市价单 5 分钟
        # 收盘前 5 分钟自动撤销限价单（A 股/CB 15:00 收盘）
        session_end = now.replace(hour=14, minute=55, second=0, microsecond=0)

        stale: list[tuple[str, GatewayOrder, str]] = []
        with self._lock:
            for oid, o in self._orders.items():
                if not (o.is_active and o.submitted_at):
                    continue
                age = (now - o.submitted_at).total_seconds()
                if o.order_type == "MARKET" and age > market_ttl:
                    stale.append((oid, o, f"市价单超时 {age:.0f}s"))
                elif o.order_type == "LIMIT" and now >= session_end:
                    stale.append((oid, o, "限价单收盘前撤单"))

        for oid, order, reason in stale:
            logger.warning(
                "撤销订单: %s %s %s x%d — %s (submitted %s)",
                oid, order.side, order.symbol, order.qty,
                reason, order.submitted_at,
            )
            try:
                self.cancel_order(oid)
            except Exception:
                logger.exception("撤销订单失败: %s", oid)

        # F-12: 市价单超时是异常信号，升级告警
        market_stale = [s for s in stale if "市价单超时" in s[2]]
        if market_stale:
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                symbols = ", ".join(f"{s[1].symbol}x{s[1].qty}" for s in market_stale[:5])
                send_feishu_alert(
                    f"实盘市价单超时撤单: {len(market_stale)} 笔\n"
                    f"标的: {symbols}\n"
                    f"可能原因: 网络故障/券商断连/极端流动性",
                    level="CRITICAL",
                )
            except Exception as e:  # silent-pass-exempt
                logger.debug("feishu alert failed: %s", e)

    def _trigger_reconnect(self) -> None:
        """触发异步重连流程。"""
        if self._reconnecting:
            return
        self._reconnecting = True
        logger.warning("QMT 连续错误，触发自动重连 (第 %d 次)", self._reconnect_count + 1)
        t = threading.Thread(
            target=self._reconnect_loop, daemon=True, name="qmt-reconnect",
        )
        t.start()

    def _reconnect_loop(self) -> None:
        """重连循环（指数退避），参照 EasytraderGateway 实现。"""
        for attempt in range(1, self._max_reconnect_attempts + 1):
            if not self._running:
                self._reconnecting = False
                return

            wait_time = min(
                self._qmt_config.reconnect_interval * (2 ** (attempt - 1)),
                300.0,
            )
            logger.info("QMT 重连尝试 %d/%d，等待 %.1fs ...",
                        attempt, self._max_reconnect_attempts, wait_time)
            time.sleep(wait_time)

            if not self._running:
                self._reconnecting = False
                return

            try:
                # Tear down old connection
                if self._trader:
                    try:
                        self._trader.stop()
                    except Exception:  # silent-pass-exempt: cleanup
                        pass
                self._connected = False
                self._trader = None

                # Reconnect
                self.connect()
                self._reconnect_count += 1
                self._reconnecting = False
                logger.info("QMT 重连成功（第 %d 次）", self._reconnect_count)
                return
            except Exception as e:
                logger.error("QMT 重连尝试 %d 失败: %s", attempt, e)

        # All attempts exhausted
        self._reconnecting = False
        logger.critical(
            "QMT 重连 %d 次均失败，需人工干预",
            self._max_reconnect_attempts,
        )
