"""
券商网关接口预留。

本模块定义与真实券商/交易所对接的网关抽象，
为未来实盘交易做准备。当前仅提供接口定义和配置结构，
不包含任何真实的交易实现。

安全警告：
=========
实盘交易涉及真实资金风险，在启用任何券商网关前必须：
1. 充分测试回测与仿真模式
2. 理解并遵守相关法规
3. 做好风控与异常处理
4. 保护好敏感凭证信息
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from aipha.execution.gateway import TradingGateway

logger = logging.getLogger(__name__)


class BrokerType(Enum):
    """券商/接口类型。"""

    # 国内券商
    CTP = auto()  # 中金所/期货
    XTP = auto()  # 中泰证券 XTP
    OES = auto()  # 宽睿 OES
    HTS = auto()  # 恒生 HTS
    UFT = auto()  # 恒生 UFT

    # 量化平台接口
    EASYTRADER = auto()  # easytrader (同花顺/通达信客户端)
    QUANTAXIS = auto()  # QUANTAXIS 接口
    VNPY = auto()  # vnpy 网关适配

    # 模拟/测试
    SIMULATED = auto()  # 模拟（非 Paper Trading，用于接口测试）


@dataclass
class BrokerConfig:
    """券商网关配置。

    安全提示：
    - 请勿在代码中硬编码敏感信息
    - 使用环境变量或加密配置管理凭证
    - 生产环境使用专用配置服务
    """

    broker_type: BrokerType
    name: str  # 配置名称
    is_sandbox: bool = True  # 是否沙箱/测试环境

    # 连接配置（具体字段由各券商实现解释）
    host: str = ""
    port: int = 0

    # 账户信息（敏感，应从环境变量读取）
    # 这里只定义结构，不存储真实值
    account_id: str = ""
    # auth_token、password 等敏感信息不应在配置类中定义
    # 应通过 credentials_loader 在运行时安全获取

    # 扩展配置
    extra: dict[str, Any] = field(default_factory=dict)


class BrokerGateway(TradingGateway):
    """券商网关抽象基类。

    所有对接真实券商的网关都应继承此类。
    当前仅定义接口框架，不提供实现。
    """

    def __init__(self, config: BrokerConfig) -> None:
        """初始化券商网关。

        Args:
            config: 券商配置
        """
        self._config = config
        self._connected = False
        logger.info(
            "初始化券商网关: %s (sandbox=%s)",
            config.name,
            config.is_sandbox,
        )

    @property
    def config(self) -> BrokerConfig:
        """获取配置。"""
        return self._config

    @property
    def is_connected(self) -> bool:
        """是否已连接。"""
        return self._connected

    @abstractmethod
    def connect(self) -> None:
        """连接券商服务器。

        Raises:
            GatewayError: 连接失败
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接。"""
        ...

    @abstractmethod
    def login(self, credentials: dict[str, str]) -> bool:
        """登录账户。

        Args:
            credentials: 凭证信息（从安全存储获取）

        Returns:
            是否登录成功

        Raises:
            GatewayError: 登录失败
        """
        ...

    @abstractmethod
    def query_account(self) -> dict[str, Any]:
        """查询账户信息。

        Returns:
            账户信息字典

        Raises:
            GatewayError: 查询失败
        """
        ...

    @abstractmethod
    def query_positions(self) -> list[dict[str, Any]]:
        """查询持仓信息。

        Returns:
            持仓列表

        Raises:
            GatewayError: 查询失败
        """
        ...

    @abstractmethod
    def query_orders(self) -> list[dict[str, Any]]:
        """查询当日订单。

        Returns:
            订单列表

        Raises:
            GatewayError: 查询失败
        """
        ...

    @abstractmethod
    def query_trades(self) -> list[dict[str, Any]]:
        """查询当日成交。

        Returns:
            成交列表

        Raises:
            GatewayError: 查询失败
        """
        ...


class BrokerGatewayNotImplemented(BrokerGateway):
    """未实现的券商网关占位符。

    用于标记计划支持但尚未实现的券商接口。
    所有方法都会抛出 NotImplementedError。
    """

    def connect(self) -> None:
        """连接券商网关（未实现，招 NotImplementedError）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def disconnect(self) -> None:
        """断开券商网关连接（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    @property
    def mode(self) -> Any:
        """交易模式（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def login(self, credentials: dict[str, str]) -> bool:
        """网关登录认证（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def query_account(self) -> dict[str, Any]:
        """查询账户信息（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def query_positions(self) -> list[dict[str, Any]]:
        """查询持仓列表（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def query_orders(self) -> list[dict[str, Any]]:
        """查询委托列表（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def query_trades(self) -> list[dict[str, Any]]:
        """查询成交列表（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    # TradingGateway 抽象方法也需要占位实现
    def submit_order(self, order: Any) -> str:
        """提交订单（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def cancel_order(self, order_id: str) -> bool:
        """撤单（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def get_order(self, order_id: str) -> Any:
        """查询指定订单（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def get_account_snapshot(self) -> Any:
        """获取账户快照（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )

    def get_active_orders(self) -> list:
        """获取活跃委托列表（未实现）。"""
        raise NotImplementedError(
            f"券商网关 {self._config.broker_type.name} 尚未实现"
        )


# 券商工厂占位符
_BROKER_REGISTRY: dict[BrokerType, type[BrokerGateway]] = {}


def register_broker(broker_type: BrokerType) -> Callable[[type[BrokerGateway]], type[BrokerGateway]]:
    """注册券商网关实现。

    装饰器，用于将网关实现注册到工厂。

    Args:
        broker_type: 券商类型

    Example:
        @register_broker(BrokerType.XTP)
        class XTPGateway(BrokerGateway):
            ...
    """

    def decorator(cls: type[BrokerGateway]) -> type[BrokerGateway]:
        """将券商网关类注册到全局注册表。"""
        _BROKER_REGISTRY[broker_type] = cls
        logger.debug("注册券商网关: %s -> %s", broker_type.name, cls.__name__)
        return cls

    return decorator


def create_broker_gateway(config: BrokerConfig) -> BrokerGateway:
    """根据配置创建券商网关。

    Args:
        config: 券商配置

    Returns:
        券商网关实例

    Raises:
        GatewayError: 未知的券商类型或创建失败
    """
    gateway_cls = _BROKER_REGISTRY.get(config.broker_type)

    if gateway_cls is None:
        logger.warning(
            "券商 %s 尚未实现，返回占位网关",
            config.broker_type.name,
        )
        return BrokerGatewayNotImplemented(config)

    return gateway_cls(config)


def list_available_brokers() -> list[BrokerType]:
    """列出已注册的可用券商。

    Returns:
        已实现的券商类型列表
    """
    return list(_BROKER_REGISTRY.keys())


def list_planned_brokers() -> list[BrokerType]:
    """列出计划支持的券商（含已实现和未实现）。

    Returns:
        所有计划支持的券商类型列表
    """
    return list(BrokerType)
