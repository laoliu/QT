"""
交易审计日志模块

记录所有交易决策与执行过程，支持：
- 订单流水留痕
- 敏感信息脱敏
- 合规审计追踪
- 可复现性保障
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from aipha.utils.paths import project_path


logger = logging.getLogger(__name__)


class AuditLevel(str, Enum):
    """审计级别"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    TRADE = "TRADE"
    RISK = "RISK"
    ALERT = "ALERT"
    ERROR = "ERROR"


class AuditEventType(str, Enum):
    """审计事件类型"""

    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    CONFIG_LOAD = "CONFIG_LOAD"
    GATEWAY_CONNECT = "GATEWAY_CONNECT"
    GATEWAY_DISCONNECT = "GATEWAY_DISCONNECT"
    GATEWAY_ERROR = "GATEWAY_ERROR"
    ORDER_INTENT = "ORDER_INTENT"
    ORDER_SUBMIT = "ORDER_SUBMIT"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIAL_FILLED = "ORDER_PARTIAL_FILLED"
    ORDER_CANCELED = "ORDER_CANCELED"
    RISK_CHECK_PASS = "RISK_CHECK_PASS"
    RISK_CHECK_FAIL = "RISK_CHECK_FAIL"
    RISK_ADJUSTED = "RISK_ADJUSTED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    ACCOUNT_SNAPSHOT = "ACCOUNT_SNAPSHOT"
    POSITION_CHANGE = "POSITION_CHANGE"
    CUSTOM = "CUSTOM"


@dataclass
class AuditEvent:
    """审计事件"""

    timestamp: datetime
    event_type: AuditEventType
    level: AuditLevel
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    order_id: str | None = None
    symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将审计事件序列化为字典。"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "level": self.level.value,
            "message": self.message,
            "data": self.data,
            "session_id": self.session_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
        }

    def to_json(self) -> str:
        """将审计事件序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class SensitiveDataMasker:
    """敏感信息脱敏器"""

    SENSITIVE_PATTERNS = [
        r"password", r"token", r"secret", r"api_key",
        r"account.*id", r"user.*name",
    ]

    @classmethod
    def mask_value(cls, value: str, keep_chars: int = 4) -> str:
        """对敏感字符串脱敏，保留首尾各 keep_chars//2 字符。"""
        if not value or len(value) <= keep_chars:
            return "*" * len(value) if value else ""
        half = keep_chars // 2
        return f"{value[:half]}{'*' * (len(value) - keep_chars)}{value[-half:]}"

    @classmethod
    def mask_dict(cls, data: dict[str, Any]) -> dict[str, Any]:
        """递归脱敏字典中匹配敏感模式的值。"""
        result = {}
        for key, value in data.items():
            key_lower = key.lower()
            is_sensitive = any(re.search(p, key_lower) for p in cls.SENSITIVE_PATTERNS)
            if is_sensitive and isinstance(value, str):
                result[key] = cls.mask_value(value)
            elif isinstance(value, dict):
                result[key] = cls.mask_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    cls.mask_dict(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result


@dataclass
class AuditLogConfig:
    """审计日志配置"""

    log_dir: Path = field(default_factory=lambda: project_path("logs", "audit"))
    file_prefix: str = "audit"
    enable_masking: bool = True
    console_output: bool = False
    rotate_daily: bool = True
    retention_days: int = 2555  # §14.4 合规要求: ≥7 年保留期 (2555 ≈ 7×365)
    log_account_snapshots: bool = True


class AuditLogger:
    """审计日志记录器"""

    def __init__(
        self,
        config: AuditLogConfig | None = None,
        session_id: str | None = None,
    ):
        self.config = config or AuditLogConfig()
        self.session_id = session_id or self._generate_session_id()
        self.masker = SensitiveDataMasker()
        self._events: list[AuditEvent] = []
        self._file_handle = None
        self._current_file: Path | None = None
        self._ensure_log_dir()
        self._open_log_file()

    def _generate_session_id(self) -> str:
        now = datetime.now()
        random_part = hashlib.md5(
            f"{now.isoformat()}{os.getpid()}".encode()
        ).hexdigest()[:8]
        return f"{now.strftime('%Y%m%d_%H%M%S')}_{random_part}"

    def _ensure_log_dir(self) -> None:
        self.config.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_filename(self) -> Path:
        date_str = datetime.now().strftime("%Y%m%d")
        return self.config.log_dir / f"{self.config.file_prefix}_{date_str}.jsonl"

    def _open_log_file(self) -> None:
        target_file = self._get_log_filename()
        if self._current_file == target_file and self._file_handle:
            return
        if self._file_handle:
            self._file_handle.close()
        self._current_file = target_file
        self._file_handle = open(target_file, "a", encoding="utf-8")

    def _write_event(self, event: AuditEvent) -> None:
        if self.config.rotate_daily:
            expected_file = self._get_log_filename()
            if self._current_file != expected_file:
                self._open_log_file()

        event_dict = event.to_dict()
        if self.config.enable_masking:
            event_dict["data"] = self.masker.mask_dict(event_dict.get("data", {}))

        if self._file_handle:
            self._file_handle.write(
                json.dumps(event_dict, ensure_ascii=False, default=str) + "\n"
            )
            self._file_handle.flush()

        if self.config.console_output:
            print(f"[AUDIT] {event.level.value} | {event.event_type.value} | {event.message}")

        self._events.append(event)

    def log(
        self,
        event_type: AuditEventType,
        level: AuditLevel,
        message: str,
        data: dict[str, Any] | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
    ) -> None:
        """记录一条审计事件。

        Args:
            event_type: 事件类型
            level: 审计级别
            message: 可读描述
            data: 结构化附加数据
            order_id: 关联订单 ID
            symbol: 关联标的代码
        """
        event = AuditEvent(
            timestamp=datetime.now(),
            event_type=event_type,
            level=level,
            message=message,
            data=data or {},
            session_id=self.session_id,
            order_id=order_id,
            symbol=symbol,
        )
        self._write_event(event)

    def log_system_start(self, config: dict[str, Any]) -> None:
        """记录系统启动事件（含脱敏后的配置快照）。"""
        self.log(AuditEventType.SYSTEM_START, AuditLevel.INFO,
                 "Trading system started", data={"config": config})

    def log_system_stop(self, reason: str = "normal") -> None:
        """记录系统停止事件。"""
        self.log(AuditEventType.SYSTEM_STOP, AuditLevel.INFO,
                 f"Trading system stopped: {reason}", data={"reason": reason})

    def log_gateway_connect(self, gateway_type: str, config: dict[str, Any]) -> None:
        """记录网关连接事件。"""
        self.log(AuditEventType.GATEWAY_CONNECT, AuditLevel.INFO,
                 f"Gateway connected: {gateway_type}",
                 data={"gateway_type": gateway_type, "config": config})

    def log_signal_generated(
        self,
        component: str,
        scorer: str,
        trade_date: str,
        picks: list[str],
        overlap_pct: float | None = None,
    ) -> None:
        """记录信号生成结果（Scorer 输出，仅标的列表，不含因子值）。"""
        self.log(
            AuditEventType.SIGNAL_GENERATED,
            AuditLevel.INFO,
            f"Signal: {component}/{scorer} → {len(picks)} picks",
            data={
                "component": component,
                "scorer": scorer,
                "trade_date": trade_date,
                "top_k": len(picks),
                "codes": picks,
                "overlap_pct": overlap_pct,
            },
        )

    def log_order_intent(self, symbol: str, side: str, qty: int,
                         strategy_name: str, reason: str | None = None) -> None:
        """记录策略层的订单意图（下单前）。"""
        self.log(AuditEventType.ORDER_INTENT, AuditLevel.TRADE,
                 f"Order intent: {side} {symbol} x{qty}",
                 data={"side": side, "qty": qty, "strategy": strategy_name, "reason": reason},
                 symbol=symbol)

    def log_order_submit(self, order_id: str, symbol: str, side: str, qty: int,
                         price: float | Decimal | None = None) -> None:
        """记录订单提交事件。"""
        self.log(AuditEventType.ORDER_SUBMIT, AuditLevel.TRADE,
                 f"Order submitted: {order_id} {side} {symbol} x{qty}",
                 data={"side": side, "qty": qty, "price": float(price) if price else None},
                 order_id=order_id, symbol=symbol)

    def log_order_filled(self, order_id: str, symbol: str, side: str,
                         filled_qty: int, fill_price: float | Decimal,
                         commission: float | Decimal = 0) -> None:
        """记录订单成交事件。"""
        self.log(AuditEventType.ORDER_FILLED, AuditLevel.TRADE,
                 f"Order filled: {order_id} {side} {symbol} x{filled_qty} @ {fill_price}",
                 data={"side": side, "filled_qty": filled_qty,
                       "fill_price": float(fill_price), "commission": float(commission)},
                 order_id=order_id, symbol=symbol)

    def log_order_rejected(self, order_id: str, symbol: str, reason: str) -> None:
        """记录订单拒绝事件（含拒绝原因）。"""
        self.log(AuditEventType.ORDER_REJECTED, AuditLevel.ALERT,
                 f"Order rejected: {order_id} - {reason}",
                 data={"reason": reason}, order_id=order_id, symbol=symbol)

    def log_order_canceled(self, order_id: str, symbol: str, reason: str = "user") -> None:
        """记录订单撤销事件。"""
        self.log(AuditEventType.ORDER_CANCELED, AuditLevel.TRADE,
                 f"Order canceled: {order_id}",
                 data={"reason": reason}, order_id=order_id, symbol=symbol)

    def log_risk_check(self, passed: bool, symbol: str, qty: int,
                       violation_type: str | None = None, adjusted_qty: int | None = None) -> None:
        """记录风控检查结果（通过/调整/拒绝）。"""
        if passed:
            self.log(AuditEventType.RISK_CHECK_PASS, AuditLevel.RISK,
                     f"Risk check passed: {symbol} x{qty}",
                     data={"original_qty": qty}, symbol=symbol)
        elif adjusted_qty is not None:
            self.log(AuditEventType.RISK_ADJUSTED, AuditLevel.RISK,
                     f"Risk adjusted: {symbol} {qty} -> {adjusted_qty}",
                     data={"original_qty": qty, "adjusted_qty": adjusted_qty,
                           "violation_type": violation_type}, symbol=symbol)
        else:
            self.log(AuditEventType.RISK_CHECK_FAIL, AuditLevel.RISK,
                     f"Risk check failed: {symbol} x{qty} - {violation_type}",
                     data={"qty": qty, "violation_type": violation_type}, symbol=symbol)

    def log_stop_loss(self, symbol: str, trigger_price: float | Decimal,
                      stop_price: float | Decimal, loss_pct: float) -> None:
        """记录止损触发事件。"""
        self.log(AuditEventType.STOP_LOSS_TRIGGERED, AuditLevel.ALERT,
                 f"Stop loss triggered: {symbol} @ {trigger_price}",
                 data={"trigger_price": float(trigger_price),
                       "stop_price": float(stop_price), "loss_pct": loss_pct},
                 symbol=symbol)

    def log_circuit_breaker(self, reason: str, duration_minutes: int) -> None:
        """记录熔断触发事件。"""
        self.log(AuditEventType.CIRCUIT_BREAKER, AuditLevel.ALERT,
                 f"Circuit breaker triggered: {reason}",
                 data={"reason": reason, "duration_minutes": duration_minutes})

    def log_event(self, event_name: str, data: dict[str, Any] | None = None) -> None:
        """通用事件记录便捷方法。

        将任意事件名称映射为 ``CUSTOM`` 类型的 INFO 级审计事件。
        供 ``live_runner`` 等外部模块以简化接口记录事件。

        Args:
            event_name: 事件名称（如 ``"position_reconciliation"``）。
            data: 结构化附加数据。
        """
        self.log(
            AuditEventType.CUSTOM,
            AuditLevel.INFO,
            event_name,
            data=data,
        )

    def log_account_snapshot(self, cash: float | Decimal,
                             total_equity: float | Decimal, positions: dict[str, int]) -> None:
        """记录账户快照（受 ``log_account_snapshots`` 配置控制）。"""
        if not self.config.log_account_snapshots:
            return
        self.log(AuditEventType.ACCOUNT_SNAPSHOT, AuditLevel.INFO,
                 f"Account snapshot: cash={cash:.2f}, equity={total_equity:.2f}",
                 data={"cash": float(cash), "total_equity": float(total_equity),
                       "position_count": len(positions), "positions": positions})

    def get_events(self, event_type: AuditEventType | None = None,
                   level: AuditLevel | None = None,
                   start_time: datetime | None = None,
                   end_time: datetime | None = None) -> list[AuditEvent]:
        """按条件过滤查询内存中的审计事件。"""
        result = []
        for event in self._events:
            if event_type and event.event_type != event_type:
                continue
            if level and event.level != level:
                continue
            if start_time and event.timestamp < start_time:
                continue
            if end_time and event.timestamp > end_time:
                continue
            result.append(event)
        return result

    def get_trade_summary(self) -> dict[str, Any]:
        """返回本次会话的交易统计摘要（提交/成交/拒绝/撤销/成交率）。"""
        trade_types = {AuditEventType.ORDER_SUBMIT, AuditEventType.ORDER_FILLED,
                       AuditEventType.ORDER_REJECTED, AuditEventType.ORDER_CANCELED}
        trade_events = [e for e in self._events if e.event_type in trade_types]
        submitted = len([e for e in trade_events if e.event_type == AuditEventType.ORDER_SUBMIT])
        filled = len([e for e in trade_events if e.event_type == AuditEventType.ORDER_FILLED])
        rejected = len([e for e in trade_events if e.event_type == AuditEventType.ORDER_REJECTED])
        canceled = len([e for e in trade_events if e.event_type == AuditEventType.ORDER_CANCELED])
        return {
            "session_id": self.session_id,
            "total_orders_submitted": submitted,
            "total_orders_filled": filled,
            "total_orders_rejected": rejected,
            "total_orders_canceled": canceled,
            "fill_rate": filled / submitted if submitted > 0 else 0,
            "reject_rate": rejected / submitted if submitted > 0 else 0,
        }

    def cleanup_old_logs(self) -> int:
        """删除超过 retention_days 的旧日志文件。

        扫描 log_dir 下 ``{file_prefix}_YYYYMMDD.jsonl`` 格式的文件，
        将日期超过保留期限的文件删除。

        Returns:
            int: 实际删除的文件数量
        """
        if self.config.retention_days <= 0:
            return 0
        cutoff = datetime.now() - timedelta(days=self.config.retention_days)
        cutoff_str = cutoff.strftime("%Y%m%d")
        deleted = 0
        prefix = self.config.file_prefix + "_"
        for f in self.config.log_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            if not (name.startswith(prefix) and name.endswith(".jsonl")):
                continue
            # 提取日期部分: prefix_YYYYMMDD.jsonl
            date_part = name[len(prefix) : -len(".jsonl")]
            if len(date_part) != 8 or not date_part.isdigit():
                continue
            if date_part < cutoff_str:
                try:
                    f.unlink()
                    deleted += 1
                except OSError:
                    pass
        return deleted

    def close(self) -> None:
        """关闭日志文件句柄，释放资源。"""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    def __del__(self) -> None:
        """析构时自动关闭文件句柄防止泄漏。"""
        try:
            self.close()
        except Exception:  # silent-pass-exempt: __del__ cleanup
            pass

    def __enter__(self) -> "AuditLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
