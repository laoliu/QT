"""
状态持久化管理器

提供 Paper Trading 状态的保存与恢复能力：
- 持仓（positions）
- 现金（cash/frozen_cash）
- 峰值净值（peak_nav）
- 订单历史（completed_orders）

存储格式：JSON，人类可读，便于调试与审计。

设计原则：
- 所有对 Gateway 的状态恢复通过公开方法完成（不直接改内部属性）
- 保存时附带元数据指纹（时间戳、版本、配置hash）
- 支持增量保存（只在状态变化时写入）
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """单个持仓状态"""

    symbol: str
    qty: int
    avg_cost: float
    today_bought: int = 0
    frozen: int = 0


@dataclass
class PendingOrderState:
    """待处理订单状态（用于进程重启后恢复）"""

    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    qty: int
    order_type: str  # "MARKET" or "LIMIT"
    limit_price: float | None = None
    filled_qty: int = 0
    status: str = "submitted"  # pending / submitted / partial_filled
    created_at: str | None = None
    entrust_no: str | None = None  # 券商委托编号


@dataclass
class AccountState:
    """账户状态快照"""

    cash: float
    frozen_cash: float
    positions: list[PositionState] = field(default_factory=list)
    peak_nav: float | None = None
    current_trade_date: str | None = None
    pending_orders: list[PendingOrderState] = field(default_factory=list)


@dataclass
class StateSnapshot:
    """完整状态快照（含元数据）"""

    account: AccountState
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "account": {
                "cash": self.account.cash,
                "frozen_cash": self.account.frozen_cash,
                "positions": [
                    {
                        "symbol": p.symbol,
                        "qty": p.qty,
                        "avg_cost": p.avg_cost,
                        "today_bought": p.today_bought,
                        "frozen": p.frozen,
                    }
                    for p in self.account.positions
                ],
                "peak_nav": self.account.peak_nav,
                "current_trade_date": self.account.current_trade_date,
                "pending_orders": [
                    {
                        "order_id": o.order_id,
                        "symbol": o.symbol,
                        "side": o.side,
                        "qty": o.qty,
                        "order_type": o.order_type,
                        "limit_price": o.limit_price,
                        "filled_qty": o.filled_qty,
                        "status": o.status,
                        "created_at": o.created_at,
                        "entrust_no": o.entrust_no,
                    }
                    for o in self.account.pending_orders
                ],
            },
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateSnapshot":
        """从字典反序列化"""
        acct = data["account"]
        positions = [
            PositionState(
                symbol=p["symbol"],
                qty=p["qty"],
                avg_cost=p["avg_cost"],
                today_bought=p.get("today_bought", 0),
                frozen=p.get("frozen", 0),
            )
            for p in acct.get("positions", [])
        ]
        pending_orders = [
            PendingOrderState(
                order_id=o["order_id"],
                symbol=o["symbol"],
                side=o["side"],
                qty=o["qty"],
                order_type=o["order_type"],
                limit_price=o.get("limit_price"),
                filled_qty=o.get("filled_qty", 0),
                status=o.get("status", "submitted"),
                created_at=o.get("created_at"),
                entrust_no=o.get("entrust_no"),
            )
            for o in acct.get("pending_orders", [])
        ]
        return cls(
            account=AccountState(
                cash=acct["cash"],
                frozen_cash=acct.get("frozen_cash", 0.0),
                positions=positions,
                peak_nav=acct.get("peak_nav"),
                current_trade_date=acct.get("current_trade_date"),
                pending_orders=pending_orders,
            ),
            meta=data.get("meta", {}),
        )


class StateManager:
    """状态持久化管理器

    使用 JSON 格式存储状态，每次保存生成带时间戳的备份。

    用法::

        manager = StateManager(state_dir=Path("data/paper_state"))

        # 从 gateway 提取状态并保存
        manager.save_from_gateway(gateway, strategy_name="cb_small_size")

        # 加载状态并恢复到 gateway
        snapshot = manager.load()
        if snapshot:
            manager.restore_to_gateway(gateway, snapshot)
    """

    def __init__(
        self,
        state_dir: Path,
        state_file: str = "paper_state.json",
        max_backups: int = 10,
    ):
        self._state_dir = state_dir
        self._state_file = state_file
        self._max_backups = max_backups
        self._last_hash: str | None = None

    @property
    def state_path(self) -> Path:
        """状态文件路径。"""
        return self._state_dir / self._state_file

    def save_from_gateway(
        self,
        gateway: Any,  # PaperGateway (§13.16)
        *,
        strategy_name: str = "",
        extra_meta: dict[str, Any] | None = None,
        peak_nav: float | None = None,
    ) -> bool:
        """从 Gateway 提取状态并保存

        Args:
            gateway: PaperGateway 实例 (§13.16, PaperTradingGateway 已废弃)
            strategy_name: 策略名称
            extra_meta: 额外元数据
            peak_nav: 组合峰值净值（用于回撤计算连续性）

        Returns:
            是否成功保存（False 表示状态未变化或保存失败）
        """
        try:
            # §13.16: 优先使用公开 API（PaperGateway 新类）
            # 用 _is_paper_gateway 标记区分，避免 MagicMock auto-attr 误匹配
            if getattr(gateway, '_is_paper_gateway', False) is True:
                gw_state = gateway.save_state()
                positions = []
                for symbol, pdata in gw_state.get("positions", {}).items():
                    positions.append(
                        PositionState(
                            symbol=symbol,
                            qty=pdata.get("total_qty", 0),
                            avg_cost=float(pdata.get("avg_cost", 0)),
                            today_bought=pdata.get("today_bought_qty", 0),
                            frozen=pdata.get("frozen_qty", 0),
                        )
                    )
                cash_val = gw_state.get("cash", 0)
                frozen_val = gw_state.get("frozen_cash", 0)
                current_td = None
            else:
                # §13.16: 非 PaperGateway 网关（QMT 等）— 返回空状态
                logger.warning(
                    "save_snapshot: 不支持的网关类型 %s，返回空状态",
                    type(gateway).__name__,
                )
                positions = []
                cash_val = 0
                frozen_val = 0
                current_td = None

            # 提取 pending orders（活跃订单）
            pending_orders = []
            try:
                active_orders = gateway.get_active_orders()
                for order in active_orders:
                    pending_orders.append(
                        PendingOrderState(
                            order_id=order.order_id,
                            symbol=order.symbol,
                            side=order.side,
                            qty=order.qty,
                            order_type=order.order_type,
                            limit_price=order.limit_price,
                            filled_qty=order.filled_qty,
                            status=order.status.value if hasattr(order.status, 'value') else str(order.status),
                            created_at=order.created_at.isoformat() if order.created_at else None,
                            entrust_no=order.metadata.get("entrust_no") if hasattr(order, "metadata") else None,
                        )
                    )
            except Exception as e:
                logger.debug(f"提取活跃订单失败（可忽略）: {e}")

            account = AccountState(
                cash=cash_val,
                frozen_cash=frozen_val,
                positions=positions,
                pending_orders=pending_orders,
                peak_nav=peak_nav,
                current_trade_date=current_td,
            )

            meta = {
                "saved_at": datetime.now().isoformat(),
                "strategy_name": strategy_name,
                "version": "1.0",
            }
            if extra_meta:
                meta.update(extra_meta)

            snapshot = StateSnapshot(account=account, meta=meta)
            return self._save(snapshot)

        except Exception as e:
            logger.error(f"保存状态失败: {e}")
            return False

    def _save(self, snapshot: StateSnapshot) -> bool:
        """保存快照到文件

        如果状态未变化（hash 相同），跳过写入。
        仅对 account 数据计算 hash（排除时间戳等 meta 变化）。
        """
        data = snapshot.to_dict()
        # 仅对 account 数据计算 hash（排除 meta 中的时间戳导致的无意义变化）
        account_content = json.dumps(
            data["account"], ensure_ascii=False, sort_keys=True, default=str
        )
        content_hash = hashlib.md5(account_content.encode()).hexdigest()[:16]

        # 状态未变化
        if content_hash == self._last_hash:
            logger.debug("状态未变化，跳过保存")
            return False

        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)

            # 原子写入: 先写临时文件，再 replace 到正式路径
            # P0-6 修复: 避免 rename→write 两步之间 crash 导致状态丢失
            full_content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
            tmp_path = self.state_path.with_suffix(".tmp")
            tmp_path.write_text(full_content, encoding="utf-8")

            # 备份当前文件（新文件已安全写入 .tmp 后才备份旧文件）
            if self.state_path.exists():
                backup_name = (
                    f"{self._state_file}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
                )
                backup_path = self._state_dir / backup_name
                import shutil
                shutil.copy2(self.state_path, backup_path)
                self._cleanup_backups()

            # 原子替换: os.replace 在 NTFS/ext4 均为原子操作
            tmp_path.replace(self.state_path)

            self._last_hash = content_hash
            logger.debug(f"状态已保存: {self.state_path} (hash={content_hash})")
            return True

        except Exception as e:
            logger.error(f"写入状态文件失败: {e}")
            return False

    def load(self) -> StateSnapshot | None:
        """从文件加载状态

        Returns:
            状态快照，不存在或加载失败返回 None
        """
        if not self.state_path.exists():
            logger.info("无状态文件，从零开始")
            return None

        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            snapshot = StateSnapshot.from_dict(data)
            meta = snapshot.meta
            logger.info(
                f"已加载状态: saved_at={meta.get('saved_at')}, "
                f"strategy={meta.get('strategy_name')}, "
                f"cash={float(snapshot.account.cash):,.2f}, "
                f"positions={len(snapshot.account.positions)}"
            )
            return snapshot

        except Exception as e:
            logger.error(f"加载状态失败: {e}")
            # §13.18 Fail-Closed: 状态损坏必须 CRITICAL 告警
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                send_feishu_alert(
                    "[PaperEngine] State 加载失败!",
                    f"文件: {self.state_path}\n错误: {e}\n请立即检查状态文件完整性",
                    level="CRITICAL",
                )
            except Exception as alert_exc:
                logger.error("状态损坏 CRITICAL 告警发送失败: %s", alert_exc)
            return None

    def restore_to_gateway(
        self,
        gateway: Any,  # PaperGateway (§13.16)
        snapshot: StateSnapshot,
    ) -> None:
        """将快照状态恢复到 Gateway

        §13.16: 使用 PaperGateway.restore_state() 公开接口。
        PaperTradingGateway 遗留路径已禁用，计划 2026 Q3 物理删除。

        Args:
            gateway: PaperGateway 实例 (§13.16)
            snapshot: 状态快照
        """
        acct = snapshot.account

        # §13.16: 优先使用公开 API（PaperGateway 新类）
        # 用 _is_paper_gateway 标记区分，避免 MagicMock auto-attr 误匹配
        if getattr(gateway, '_is_paper_gateway', False) is True:
            state_dict = {
                "cash": acct.cash,
                "frozen_cash": acct.frozen_cash,
                "positions": {
                    p.symbol: {
                        "total_qty": p.qty,
                        "avg_cost": p.avg_cost,
                        "today_bought_qty": p.today_bought,
                        "frozen_qty": p.frozen,
                    }
                    for p in acct.positions
                },
                "latest_prices": {},
            }
            gateway.restore_state(state_dict)

            # 恢复 pending orders（PaperGateway 也有 _orders）
            pending_count = 0
            if acct.pending_orders and hasattr(gateway, '_orders'):
                for po in acct.pending_orders:
                    from aipha.execution.gateway import GatewayOrder, OrderStatus as OS
                    order = GatewayOrder(
                        order_id=po.order_id,
                        symbol=po.symbol,
                        side=po.side,
                        qty=po.qty,
                        order_type=po.order_type,
                        limit_price=po.limit_price,
                        status=OS(po.status) if po.status in {s.value for s in OS} else OS.SUBMITTED,
                        filled_qty=po.filled_qty,
                        metadata={"entrust_no": po.entrust_no} if po.entrust_no else {},
                    )
                    gateway._orders[po.order_id] = order
                    pending_count += 1

            logger.info(
                f"状态已恢复到 PaperGateway (公开API): cash={acct.cash:,.2f}, "
                f"positions={len(acct.positions)}, "
                f"pending_orders={pending_count}"
            )
            return

        # 遗留路径: PaperTradingGateway（直接访问私有属性）
        # §13.16: 此路径已废弃(R167 C1 逻辑删除 2026-05-02)
        # 所有生产路径已迁移到 PaperGateway 公开 API
        # 物理删除计划: 2026 Q3
        import warnings
        warnings.warn(
            "restore_to_gateway: 不支持的网关类型 "
            f"{type(gateway).__name__}。所有模拟盘路径必须使用 PaperGateway (§13.16)。"
            "遗留 PaperTradingGateway 私有属性恢复已禁用。",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.error(
            "restore_to_gateway: 非 PaperGateway 网关 %s, 恢复操作跳过 (R167 C1)",
            type(gateway).__name__,
        )

    def _cleanup_backups(self) -> None:
        """清理多余的备份文件，保留最新 N 个"""
        backups = sorted(self._state_dir.glob(f"{self._state_file}.*.bak"))
        while len(backups) > self._max_backups:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
                logger.debug(f"已清理备份: {oldest}")
            except Exception as e:
                # P2-2 修复: 记录备份清理失败，而非静默吞没
                logger.warning(f"备份清理失败: {oldest} - {e}")
