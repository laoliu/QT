"""持仓对账机制

对比本地（引擎/策略）持仓与券商（网关/broker）持仓，
检测不一致并生成对账报告。

功能：
- 发现遗漏持仓（本地没有但券商有）
- 发现幻象持仓（本地有但券商没有）
- 检测数量偏差（双方都有但数量不同）
- 可配置告警阈值
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aipha.execution.broker_gateway import BrokerGateway
    from aipha.execution.gateway import TradingGateway

logger = logging.getLogger(__name__)


class DiffType(str, Enum):
    """差异类型"""

    MISSING_LOCAL = "missing_local"  # 券商有、本地没有
    MISSING_BROKER = "missing_broker"  # 本地有、券商没有
    QTY_MISMATCH = "qty_mismatch"  # 双方数量不一致


@dataclass(frozen=True, slots=True)
class PositionDetail:
    """持仓明细"""

    symbol: str
    qty: int
    avg_cost: float = 0.0
    market_value: float = 0.0
    frozen_qty: int = 0


@dataclass(frozen=True, slots=True)
class PositionDiff:
    """单个标的的持仓差异"""

    symbol: str
    diff_type: DiffType
    local_qty: int
    broker_qty: int
    qty_delta: int  # broker_qty - local_qty
    message: str


@dataclass
class ReconciliationReport:
    """对账报告"""

    timestamp: datetime
    total_symbols: int = 0
    matched_count: int = 0
    diff_count: int = 0
    diffs: list[PositionDiff] = field(default_factory=list)
    is_consistent: bool = True

    @property
    def summary(self) -> str:
        """对账摘要"""
        if self.is_consistent:
            return f"对账通过: {self.matched_count}/{self.total_symbols} 标的一致"
        else:
            return (
                f"对账失败: {self.diff_count} 处差异 "
                f"({self.matched_count}/{self.total_symbols} 一致)"
            )


class PositionReconciler:
    """持仓对账器

    对比本地持仓与券商持仓，生成差异报告。

    Example:
        reconciler = PositionReconciler()
        report = reconciler.reconcile(
            local_positions={"000001.SZ": PositionDetail("000001.SZ", 100)},
            broker_positions={"000001.SZ": PositionDetail("000001.SZ", 200)},
        )
        if not report.is_consistent:
            for diff in report.diffs:
                logger.critical(f"Position mismatch: {diff}")
    """

    def __init__(
        self,
        qty_tolerance: int = 0,
    ):
        """初始化对账器

        Args:
            qty_tolerance: 数量容差（允许的最大偏差量），默认 0 即严格一致
        """
        self._qty_tolerance = qty_tolerance

    def reconcile(
        self,
        local_positions: dict[str, PositionDetail],
        broker_positions: dict[str, PositionDetail],
    ) -> ReconciliationReport:
        """执行对账

        Args:
            local_positions: 本地持仓（引擎/策略视角）
            broker_positions: 券商持仓（broker 视角）

        Returns:
            对账报告
        """
        all_symbols = set(local_positions.keys()) | set(broker_positions.keys())
        diffs: list[PositionDiff] = []
        matched = 0

        for symbol in sorted(all_symbols):
            local = local_positions.get(symbol)
            broker = broker_positions.get(symbol)

            local_qty = local.qty if local else 0
            broker_qty = broker.qty if broker else 0

            if local_qty == 0 and broker_qty == 0:
                # 双方都无持仓，忽略
                continue

            if local is None or local_qty == 0:
                diffs.append(PositionDiff(
                    symbol=symbol,
                    diff_type=DiffType.MISSING_LOCAL,
                    local_qty=0,
                    broker_qty=broker_qty,
                    qty_delta=broker_qty,
                    message=f"券商有 {broker_qty} 股，本地无持仓",
                ))
            elif broker is None or broker_qty == 0:
                diffs.append(PositionDiff(
                    symbol=symbol,
                    diff_type=DiffType.MISSING_BROKER,
                    local_qty=local_qty,
                    broker_qty=0,
                    qty_delta=-local_qty,
                    message=f"本地有 {local_qty} 股，券商无持仓",
                ))
            elif abs(local_qty - broker_qty) > self._qty_tolerance:
                diffs.append(PositionDiff(
                    symbol=symbol,
                    diff_type=DiffType.QTY_MISMATCH,
                    local_qty=local_qty,
                    broker_qty=broker_qty,
                    qty_delta=broker_qty - local_qty,
                    message=(
                        f"数量不一致: 本地={local_qty}, 券商={broker_qty}, "
                        f"差异={broker_qty - local_qty}"
                    ),
                ))
            else:
                matched += 1

        report = ReconciliationReport(
            timestamp=datetime.now(),
            total_symbols=len(all_symbols),
            matched_count=matched,
            diff_count=len(diffs),
            diffs=diffs,
            is_consistent=len(diffs) == 0,
        )

        # 日志记录
        if report.is_consistent:
            logger.info(f"Position reconciliation passed: {report.summary}")
        else:
            logger.critical(f"Position reconciliation FAILED: {report.summary}")
            for diff in diffs:
                logger.critical(f"  [{diff.diff_type.value}] {diff.symbol}: {diff.message}")

        return report

    def reconcile_with_gateway(
        self,
        local_positions: dict[str, PositionDetail],
        gateway: "TradingGateway",
    ) -> ReconciliationReport:
        """从网关获取券商持仓并对账。

        这是 reconcile() 的便捷包装，自动从 TradingGateway（含 BrokerGateway）
        获取账户快照，转化为 PositionDetail 字典后进行对账。

        Args:
            local_positions: 本地（引擎/策略层）的持仓视图
            gateway: 交易网关实例（PaperGateway 或 BrokerGateway 均可）

        Returns:
            对账报告

        Raises:
            RuntimeError: 网关未连接或查询失败
        """
        if not gateway.is_connected:
            raise RuntimeError("网关未连接，无法执行持仓对账")

        snapshot = gateway.get_account_snapshot()

        broker_positions: dict[str, PositionDetail] = {}
        for symbol, qty in snapshot.positions.items():
            if qty > 0:
                broker_positions[symbol] = PositionDetail(
                    symbol=symbol,
                    qty=qty,
                )

        logger.info(
            f"网关持仓快照: {len(broker_positions)} 个标的, "
            f"现金={float(snapshot.cash):,.2f}"
        )

        return self.reconcile(local_positions, broker_positions)

    def reconcile_with_broker_gateway(
        self,
        local_positions: dict[str, PositionDetail],
        gateway: "BrokerGateway",
    ) -> ReconciliationReport:
        """从券商网关的 query_positions() 获取更详细的持仓数据并对账。

        与 reconcile_with_gateway() 的区别：
        - 使用 BrokerGateway.query_positions()（返回原始券商数据）
        - 可获取冻结数量等更多信息

        Args:
            local_positions: 本地持仓视图
            gateway: 券商网关实例（必须是 BrokerGateway 子类）

        Returns:
            对账报告
        """
        if not gateway.is_connected:
            raise RuntimeError("券商网关未连接，无法执行持仓对账")

        raw_positions = gateway.query_positions()

        broker_positions: dict[str, PositionDetail] = {}
        for pos in raw_positions:
            # 兼容不同券商返回格式
            symbol = (
                pos.get("证券代码", "")
                or pos.get("symbol", "")
                or pos.get("ts_code", "")
            )
            if not symbol:
                continue

            qty = int(pos.get("证券数量", 0) or pos.get("qty", 0) or pos.get("volume", 0))
            if qty <= 0:
                continue

            avg_cost = float(
                pos.get("成本价", 0) or pos.get("avg_cost", 0) or pos.get("cost_price", 0)
            )
            market_value = float(
                pos.get("市值", 0) or pos.get("market_value", 0)
            )
            frozen = int(
                pos.get("冻结数量", 0) or pos.get("frozen", 0) or pos.get("frozen_volume", 0)
            )

            broker_positions[symbol] = PositionDetail(
                symbol=symbol,
                qty=qty,
                avg_cost=avg_cost,
                market_value=market_value,
                frozen_qty=frozen,
            )

        logger.info(
            f"券商持仓查询: {len(broker_positions)} 个标的 "
            f"(原始记录 {len(raw_positions)} 条)"
        )

        return self.reconcile(local_positions, broker_positions)
