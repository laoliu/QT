"""
easytrader 券商网关适配器

通过 easytrader 库连接同花顺/通达信等客户端进行实盘交易。

依赖：
    pip install easytrader

安全警告：
=========
实盘交易涉及真实资金，使用前请确保：
1. 充分理解交易风险
2. 先在仿真环境充分测试
3. 设置合理的风控参数
4. 保护好账户凭证

支持的客户端：
- 同花顺（universal_client）
- 通达信（tdx_client）
- 银河证券客户端
- 国金证券客户端
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from aipha.execution.broker_gateway import BrokerConfig, BrokerGateway, BrokerType
from aipha.execution.emergency_control import StopAgent
from aipha.utils.paths import KILL_SWITCH_PATH
from aipha.execution.gateway import (
    AccountSnapshot,
    ExecutionReport,
    GatewayNotConnectedError,
    GatewayOrder,
    InsufficientFundsError,
    InsufficientPositionError,
    OrderStatus,
    OrderStatusChange,
    OrderValidationError,
    TradingMode,
)


logger = logging.getLogger(__name__)


class ConnectionState(str, Enum):
    """连接状态机"""

    DISCONNECTED = "disconnected"     # 未连接
    CONNECTING = "connecting"         # 正在连接
    CONNECTED = "connected"           # 已连接
    LOGGED_IN = "logged_in"           # 已登录
    RECONNECTING = "reconnecting"     # 正在重连
    DEGRADED = "degraded"             # 降级（连接正常但部分功能异常）
    FATAL = "fatal"                   # 不可恢复错误


class EasytraderClientType(str, Enum):
    """easytrader 客户端类型"""

    UNIVERSAL = "universal"  # 同花顺通用版
    TDX = "tdx"  # 通达信
    YH = "yh"  # 银河证券
    GJ = "gj"  # 国金证券


@dataclass
class EasytraderConfig(BrokerConfig):
    """easytrader 配置"""

    client_type: EasytraderClientType = EasytraderClientType.UNIVERSAL

    # 客户端路径（同花顺等需要）
    client_path: str = ""

    # 通讯密码（部分券商需要）
    comm_password: str = ""

    # 轮询间隔（秒）
    poll_interval: float = 1.0

    # 自动重连
    auto_reconnect: bool = True
    reconnect_interval: float = 5.0

    # 调试模式（不实际下单）
    dry_run: bool = False

    # 最小交易单位（A股=100, 可转债=10）
    lot_size: int = 100


class EasytraderGateway(BrokerGateway):
    """easytrader 网关实现

    通过 easytrader 库连接各种券商客户端。

    使用方式：
        config = EasytraderConfig(
            broker_type=BrokerType.EASYTRADER,
            name="同花顺实盘",
            client_type=EasytraderClientType.UNIVERSAL,
            client_path=r"C:\\同花顺\\xiadan.exe",
        )
        gateway = EasytraderGateway(config)
        gateway.connect()
        gateway.login({"user": "xxx", "password": "yyy"})
    """

    def __init__(self, config: EasytraderConfig):
        """初始化网关

        Args:
            config: easytrader 配置
        """
        super().__init__(config)
        self._et_config = config
        self._user = None  # easytrader user 对象
        self._orders: dict[str, GatewayOrder] = {}
        self._order_entrust_map: dict[str, str] = {}  # order_id -> entrust_no
        self._lock = threading.Lock()
        self._submission_lock = threading.RLock()
        self._stop_agent: StopAgent | None = None
        # 最小交易单位（A股=100，可转债=10），可通过 config 设置
        self._lot_size: int = getattr(config, 'lot_size', 100)

        # 状态轮询线程
        self._poll_thread: threading.Thread | None = None
        self._stop_poll = threading.Event()

        # 重连状态机
        self._conn_state = ConnectionState.DISCONNECTED
        self._reconnect_count: int = 0
        self._max_reconnect_attempts: int = 10
        self._last_credentials: dict[str, str] | None = None
        self._reconnect_thread: threading.Thread | None = None
        self._consecutive_errors: int = 0
        self._error_threshold: int = 5  # 连续错误超过此阈值触发重连

        # 回调
        self._execution_callbacks: list = []
        self._status_callbacks: list = []

        logger.info(
            f"EasytraderGateway initialized: {config.client_type.value}"
        )

        # ── B3 P0 实盘保护 ──
        if not getattr(config, 'is_sandbox', True):
            logger.critical(
                "[LIVE MODE] EasytraderGateway 已配置为实盘模式！"
                "本网关依赖桌面客户端，不适合无人值守服务器部署。"
                "请确认已充分测试仿真环境，并在环境变量中设置 AIPHA_LIVE_TRADING=confirmed"
            )

    @property
    def mode(self) -> TradingMode:
        """返回交易模式（实盘）。"""
        return TradingMode.LIVE

    @property
    def connection_state(self) -> ConnectionState:
        """当前连接状态"""
        return self._conn_state

    @property
    def reconnect_count(self) -> int:
        """累计重连次数"""
        return self._reconnect_count

    def on_execution(self, callback) -> None:
        """注册成交回调"""
        self._execution_callbacks.append(callback)

    def on_status_change(self, callback) -> None:
        """注册状态变更回调"""
        self._status_callbacks.append(callback)

    # ==========================================================================
    # 连接管理
    # ==========================================================================

    def connect(self) -> None:
        """连接客户端"""
        self._conn_state = ConnectionState.CONNECTING

        try:
            import easytrader
        except ImportError:
            self._conn_state = ConnectionState.FATAL
            raise ImportError(
                "请安装 easytrader: pip install easytrader\n"
                "注意：easytrader 需要 Windows 环境和对应的券商客户端"
            )

        client_type = self._et_config.client_type.value

        try:
            self._user = easytrader.use(client_type)
            logger.info(f"Connected to {client_type} client")
        except Exception as e:
            self._conn_state = ConnectionState.FATAL
            logger.error(f"Failed to connect: {e}")
            raise

        # 如果指定了客户端路径
        if self._et_config.client_path:
            try:
                self._user.prepare(
                    config_path=self._et_config.client_path,
                    user=self._et_config.account_id,
                    password="",  # 密码在 login 时提供
                )
            except Exception as e:
                logger.warning(f"prepare() failed (may be okay): {e}")

        self._connected = True
        self._conn_state = ConnectionState.CONNECTED
        self._consecutive_errors = 0

    def disconnect(self) -> None:
        """断开连接"""
        if self._stop_agent is not None:
            self._stop_agent.close()
            self._stop_agent = None
        self._stop_poll.set()

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)

        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=5)

        self._user = None
        self._connected = False
        self._conn_state = ConnectionState.DISCONNECTED

        logger.info("EasytraderGateway disconnected")

    def login(self, credentials: dict[str, str]) -> bool:
        """登录账户

        Args:
            credentials: 登录凭证
                - user: 账号
                - password: 密码

        Returns:
            是否登录成功
        """
        if not self._connected or self._user is None:
            raise GatewayNotConnectedError("Gateway not connected")

        # 保存凭证用于重连
        self._last_credentials = credentials.copy()

        try:
            # easytrader 的登录方式取决于客户端类型
            if self._et_config.client_type == EasytraderClientType.UNIVERSAL:
                self._user.connect(self._et_config.client_path)
            else:
                # 其他客户端可能需要不同的登录方式
                pass

            self._conn_state = ConnectionState.LOGGED_IN
            self._consecutive_errors = 0
            if not self._et_config.dry_run and self._stop_agent is None:
                self._stop_agent = StopAgent(
                    KILL_SWITCH_PATH, self.query_open_order_ids, self._cancel_broker_order,
                    self._submission_lock, label=f"easytrader:{self._et_config.name}",
                )
                try:
                    self._stop_agent.start()
                except Exception:
                    self._stop_agent = None
                    raise

            # 启动状态轮询
            self._start_poll_thread()

            logger.info("Login successful")
            return True

        except Exception as e:
            logger.error(f"Login failed: {e}")
            self._conn_state = ConnectionState.FATAL
            return False

    def _start_poll_thread(self) -> None:
        """启动状态轮询线程"""
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="easytrader-poll",
        )
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        """轮询订单状态（含断线检测与自动重连）"""
        while not self._stop_poll.is_set():
            try:
                self._sync_order_status()
                self._consecutive_errors = 0  # 成功则重置计数
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(
                    f"Poll error ({self._consecutive_errors}/{self._error_threshold}): {e}"
                )

                # 连续错误超过阈值，触发重连
                if (
                    self._consecutive_errors >= self._error_threshold
                    and self._et_config.auto_reconnect
                    and self._conn_state != ConnectionState.RECONNECTING
                    and self._conn_state != ConnectionState.FATAL
                ):
                    self._trigger_reconnect()

            self._stop_poll.wait(self._et_config.poll_interval)

    def _trigger_reconnect(self) -> None:
        """触发异步重连流程"""
        if self._conn_state in (ConnectionState.RECONNECTING, ConnectionState.FATAL):
            return  # 已经在重连中或不可恢复

        self._conn_state = ConnectionState.RECONNECTING
        logger.warning(
            f"连续 {self._consecutive_errors} 次错误，触发自动重连 "
            f"(第 {self._reconnect_count + 1} 次)"
        )

        # 在新线程中执行重连，不阻塞轮询线程
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name="easytrader-reconnect",
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """重连循环（指数退避）"""
        for attempt in range(1, self._max_reconnect_attempts + 1):
            if self._stop_poll.is_set():
                return

            wait_time = min(
                self._et_config.reconnect_interval * (2 ** (attempt - 1)),
                300.0,  # 最大等待 5 分钟
            )
            logger.info(f"重连尝试 {attempt}/{self._max_reconnect_attempts}，等待 {wait_time:.1f}s ...")
            if self._stop_poll.wait(wait_time):
                return

            if self._stop_poll.is_set():
                return

            try:
                # 断开旧连接
                old_user = self._user
                self._user = None
                self._connected = False

                # 重新连接
                self.connect()

                # 重新登录（如果有保存的凭证）
                if self._last_credentials:
                    success = self.login(self._last_credentials)
                    if not success:
                        logger.warning(f"重连后登录失败，将重试")
                        continue

                self._reconnect_count += 1
                self._consecutive_errors = 0
                self._conn_state = ConnectionState.LOGGED_IN
                logger.info(
                    f"重连成功（第 {self._reconnect_count} 次），"
                    f"恢复正常服务"
                )
                return

            except Exception as e:
                logger.error(f"重连尝试 {attempt} 失败: {e}")

        # 重连全部失败
        self._conn_state = ConnectionState.FATAL
        logger.critical(
            f"重连 {self._max_reconnect_attempts} 次均失败，"
            f"进入 FATAL 状态。需要人工干预。"
        )

    def _sync_order_status(self) -> None:
        """同步订单状态"""
        if self._user is None:
            return

        try:
            # 查询当日委托
            today_entrusts = self._user.today_entrusts

            for entrust in today_entrusts:
                entrust_no = str(entrust.get("合同编号", ""))

                # 查找对应的订单
                order_id = None
                for oid, eno in self._order_entrust_map.items():
                    if eno == entrust_no:
                        order_id = oid
                        break

                if order_id is None:
                    continue

                order = self._orders.get(order_id)
                if order is None or order.is_done:
                    continue

                # 更新状态
                status_str = entrust.get("备注", "")
                filled_qty = int(entrust.get("成交数量", 0))

                if "已成" in status_str:
                    order.status = OrderStatus.FILLED
                    order.filled_qty = filled_qty
                    order.filled_at = datetime.now()
                elif "已撤" in status_str:
                    order.status = OrderStatus.CANCELED
                elif filled_qty > 0:
                    order.status = OrderStatus.PARTIAL_FILLED
                    order.filled_qty = filled_qty

        except Exception as e:
            logger.debug(f"Sync error: {e}")

    # ==========================================================================
    # 订单操作
    # ==========================================================================

    def submit_order(self, order: GatewayOrder) -> str:
        """提交订单"""
        with self._submission_lock:
            if KILL_SWITCH_PATH.exists():
                raise OrderValidationError("Persistent kill switch is active")
            return self._submit_order_impl(order)

    def _submit_order_impl(self, order: GatewayOrder) -> str:
        # B3 P0: 实盘确认守卫
        if not getattr(self._et_config, 'is_sandbox', True):
            import os
            if os.environ.get("AIPHA_LIVE_TRADING") != "confirmed":
                raise RuntimeError(
                    "实盘下单被阻止：请设置环境变量 AIPHA_LIVE_TRADING=confirmed "
                    "以显式确认已知晓资金风险。"
                )

        if not self._connected or self._user is None:
            raise GatewayNotConnectedError("Gateway not connected")

        if self._conn_state in (ConnectionState.RECONNECTING, ConnectionState.FATAL):
            raise GatewayNotConnectedError(
                f"网关处于 {self._conn_state.value} 状态，暂停接受订单"
            )

        self._validate_order(order)

        # Dry run 模式
        if self._et_config.dry_run:
            logger.info(f"[DRY RUN] Would submit: {order}")
            order.status = OrderStatus.SUBMITTED
            order.submitted_at = datetime.now()
            self._orders[order.order_id] = order
            return order.order_id

        try:
            if order.limit_price is None or order.limit_price <= 0:
                raise ValueError(
                    f"limit_price 必须为正数 (symbol={order.symbol}, "
                    f"limit_price={order.limit_price})。"
                    f"禁止发送 price=0 给券商 API — 行为未定义。"
                )
            if order.side == "BUY":
                result = self._user.buy(
                    security=order.symbol,
                    price=order.limit_price,
                    amount=order.qty,
                )
            else:
                result = self._user.sell(
                    security=order.symbol,
                    price=order.limit_price,
                    amount=order.qty,
                )

            # easytrader 返回委托编号
            entrust_no = str(result.get("entrust_no", ""))

            order.status = OrderStatus.SUBMITTED
            order.submitted_at = datetime.now()
            order.metadata["entrust_no"] = entrust_no

            self._orders[order.order_id] = order
            self._order_entrust_map[order.order_id] = entrust_no

            logger.info(
                f"Order submitted: {order.order_id} -> {entrust_no} "
                f"{order.side} {order.symbol} x{order.qty}"
            )

            return order.order_id

        except Exception as e:
            logger.error(f"Submit order failed: {e}")
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)
            self._orders[order.order_id] = order
            raise OrderValidationError(str(e))

    def _validate_order(self, order: GatewayOrder) -> None:
        """验证订单"""
        if order.qty <= 0:
            raise OrderValidationError("Order quantity must be positive")

        if order.qty % self._lot_size != 0:
            raise OrderValidationError(
                f"委托数量必须是{self._lot_size}的整数倍"
            )

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if not self._connected or self._user is None:
            raise GatewayNotConnectedError("Gateway not connected")

        order = self._orders.get(order_id)
        if order is None:
            return False

        if order.is_done:
            return False

        entrust_no = self._order_entrust_map.get(order_id)
        if not entrust_no:
            return False

        # Dry run 模式
        if self._et_config.dry_run:
            logger.info(f"[DRY RUN] Would cancel: {order_id}")
            order.status = OrderStatus.CANCELED
            return True

        try:
            self._user.cancel_entrust(entrust_no)
            order.status = OrderStatus.CANCELED
            order.cancel_reason = "User canceled"

            logger.info(f"Order canceled: {order_id}")
            return True

        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    def get_order(self, order_id: str) -> GatewayOrder | None:
        """获取订单"""
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[GatewayOrder]:
        """获取活跃订单"""
        return [o for o in self._orders.values() if o.is_active]

    # ==========================================================================
    # 账户查询
    # ==========================================================================

    def get_account_snapshot(self) -> AccountSnapshot:
        """获取账户快照"""
        if not self._connected or self._user is None:
            return AccountSnapshot(
                timestamp=datetime.now(),
                cash=Decimal("0"),
                frozen_cash=Decimal("0"),
                total_equity=Decimal("0"),
                positions={},
            )

        try:
            balance = self._user.balance

            cash = Decimal(str(balance.get("可用资金", 0)))
            frozen = Decimal(str(balance.get("冻结资金", 0)))
            total = Decimal(str(balance.get("总资产", cash + frozen)))

            # 持仓 - 转换为 symbol -> qty 格式
            positions_raw = self._user.position
            positions: dict[str, int] = {}

            for pos in positions_raw:
                symbol = pos.get("证券代码", "")
                if not symbol:
                    continue
                positions[symbol] = int(pos.get("证券数量", 0))

            return AccountSnapshot(
                timestamp=datetime.now(),
                cash=cash,
                frozen_cash=frozen,
                total_equity=total,
                positions=positions,
            )

        except Exception as e:
            logger.error(f"Get account snapshot failed: {e}")
            return AccountSnapshot(
                timestamp=datetime.now(),
                cash=Decimal("0"),
                frozen_cash=Decimal("0"),
                total_equity=Decimal("0"),
                positions={},
            )

    def query_account(self) -> dict[str, Any]:
        """查询账户信息"""
        if self._user is None:
            return {}
        return self._user.balance

    def query_positions(self) -> list[dict[str, Any]]:
        """查询持仓"""
        if self._user is None:
            return []
        return self._user.position

    def query_orders(self) -> list[dict[str, Any]]:
        """查询当日委托"""
        if self._user is None:
            return []
        return self._user.today_entrusts

    def query_open_order_ids(self) -> list[str]:
        if not self._connected or self._user is None or self._et_config.dry_run:
            raise GatewayNotConnectedError("A live broker query is required")
        orders = self._user.today_entrusts
        if not isinstance(orders, list):
            raise GatewayNotConnectedError("Broker order query did not return a list")
        active = []
        terminal = {"已成", "全部成交", "已撤", "部撤", "废单", "已拒绝"}
        pending = {"未报", "待报", "已报", "部成", "部分成交", "已报待撤", "部成待撤", "待撤"}
        for order in orders:
            status = str(order.get("委托状态", order.get("备注", ""))).strip()
            if status in terminal:
                continue
            order_id = str(order.get("合同编号", ""))
            if status not in pending or not order_id:
                raise OrderValidationError("Unrecognized broker order status/id")
            active.append(order_id)
        return active

    def _cancel_broker_order(self, order_id: str) -> None:
        if self._user is None or self._et_config.dry_run:
            raise GatewayNotConnectedError("No live broker session")
        self._user.cancel_entrust(order_id)

    def query_trades(self) -> list[dict[str, Any]]:
        """查询当日成交"""
        if self._user is None:
            return []
        return self._user.today_trades
