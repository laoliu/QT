"""监控与告警系统 — 交易/风控/性能实时监控"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass
class Alert:
    id: str
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    status: AlertStatus = AlertStatus.ACTIVE
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def acknowledge(self):
        self.status = AlertStatus.ACKNOWLEDGED

    def resolve(self):
        self.status = AlertStatus.RESOLVED


@dataclass
class PortfolioSnapshot:
    timestamp: datetime
    total_value: float
    cash: float
    positions: dict[str, float]
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    drawdown: float = 0.0
    leverage: float = 1.0
    turnover: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceMetrics:
    latency_ms: float = 0.0
    orders_per_second: float = 0.0
    fills_per_second: float = 0.0
    cpu_usage_pct: float = 0.0
    memory_usage_mb: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


# ============================================================================
# 告警规则基类
# ============================================================================

class AlertRule(ABC):
    def __init__(self, name: str, level: AlertLevel = AlertLevel.WARNING):
        self.name = name
        self.level = level
        self._last_triggered: datetime | None = None
        self._cooldown_seconds: float = 60.0

    @abstractmethod
    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        ...

    def can_trigger(self) -> bool:
        if self._last_triggered is None:
            return True
        return (datetime.now() - self._last_triggered).total_seconds() >= self._cooldown_seconds

    def mark_triggered(self):
        self._last_triggered = datetime.now()


# ============================================================================
# 内置告警规则
# ============================================================================

class MaxDrawdownAlert(AlertRule):
    def __init__(self, threshold: float = 0.1, level: AlertLevel = AlertLevel.CRITICAL):
        super().__init__(f"max_drawdown_{threshold:.0%}", level)
        self.threshold = threshold

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        if snapshot.drawdown >= self.threshold and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title="最大回撤告警",
                message=f"当前回撤 {snapshot.drawdown:.2%} 超过阈值 {self.threshold:.2%}",
                source=self.name,
                metadata={"drawdown": snapshot.drawdown, "threshold": self.threshold},
            )
        return None


class PositionConcentrationAlert(AlertRule):
    def __init__(self, max_single_pct: float = 0.2, level: AlertLevel = AlertLevel.WARNING):
        super().__init__(f"position_concentration_{max_single_pct:.0%}", level)
        self.max_single_pct = max_single_pct

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        if snapshot.total_value <= 0:
            return None
        for symbol, value in snapshot.positions.items():
            pct = abs(value) / snapshot.total_value
            if pct >= self.max_single_pct and self.can_trigger():
                self.mark_triggered()
                return Alert(
                    id=f"{self.name}_{int(time.time())}",
                    level=self.level, title="持仓集中度告警",
                    message=f"{symbol} 占比 {pct:.2%} 超过阈值 {self.max_single_pct:.2%}",
                    source=self.name,
                    metadata={"symbol": symbol, "pct": pct},
                )
        return None


class LeverageAlert(AlertRule):
    def __init__(self, max_leverage: float = 1.5, level: AlertLevel = AlertLevel.WARNING):
        super().__init__(f"leverage_{max_leverage:.1f}x", level)
        self.max_leverage = max_leverage

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        if snapshot.leverage >= self.max_leverage and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title="杠杆率告警",
                message=f"杠杆 {snapshot.leverage:.2f}x 超过阈值 {self.max_leverage:.2f}x",
                source=self.name,
            )
        return None


class TurnoverAlert(AlertRule):
    def __init__(self, max_turnover: float = 0.5, level: AlertLevel = AlertLevel.INFO):
        super().__init__(f"turnover_{max_turnover:.0%}", level)
        self.max_turnover = max_turnover

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        if snapshot.turnover >= self.max_turnover and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title="换手率告警",
                message=f"换手率 {snapshot.turnover:.2%} 超过阈值 {self.max_turnover:.2%}",
                source=self.name,
            )
        return None


class DailyLossAlert(AlertRule):
    def __init__(self, max_loss_pct: float = 0.03, level: AlertLevel = AlertLevel.CRITICAL):
        super().__init__(f"daily_loss_{max_loss_pct:.0%}", level)
        self.max_loss_pct = max_loss_pct
        self._day_start_value: float | None = None
        self._current_day: datetime | None = None

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        today = snapshot.timestamp.date()
        if self._current_day is None or today != self._current_day:
            self._current_day = today
            self._day_start_value = snapshot.total_value
            return None
        if not self._day_start_value or self._day_start_value <= 0:
            return None
        daily_return = (snapshot.total_value - self._day_start_value) / self._day_start_value
        if daily_return <= -self.max_loss_pct and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title="日内亏损告警",
                message=f"日内亏损 {abs(daily_return):.2%} 超过阈值 {self.max_loss_pct:.2%}",
                source=self.name,
                metadata={"daily_return": daily_return, "start_value": self._day_start_value},
            )
        return None


class DataStaleAlert(AlertRule):
    def __init__(self, stale_seconds: float = 120.0, level: AlertLevel = AlertLevel.CRITICAL):
        super().__init__(f"data_stale_{stale_seconds:.0f}s", level)
        self.stale_seconds = stale_seconds
        self._cooldown_seconds = max(stale_seconds, 120.0)

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        last_bar_time = snapshot.metadata.get("last_bar_time")
        if last_bar_time is None:
            return None
        if isinstance(last_bar_time, str):
            try:
                last_bar_time = datetime.fromisoformat(last_bar_time)
            except (ValueError, TypeError):
                return None
        elapsed = (snapshot.timestamp - last_bar_time).total_seconds()
        if elapsed >= self.stale_seconds and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title="数据中断告警",
                message=f"已 {elapsed:.0f}s 未收到行情 (阈值 {self.stale_seconds:.0f}s)",
                source=self.name,
            )
        return None


class StrategyInactivityAlert(AlertRule):
    def __init__(self, inactive_minutes: float = 120.0, level: AlertLevel = AlertLevel.WARNING):
        super().__init__(f"strategy_inactive_{inactive_minutes:.0f}m", level)
        self.inactive_minutes = inactive_minutes
        self._cooldown_seconds = max(inactive_minutes * 30, 300.0)

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        last_fill = snapshot.metadata.get("last_fill_time")
        if last_fill is None:
            return None
        if isinstance(last_fill, str):
            try:
                last_fill = datetime.fromisoformat(last_fill)
            except (ValueError, TypeError):
                return None
        elapsed_min = (snapshot.timestamp - last_fill).total_seconds() / 60.0
        if elapsed_min >= self.inactive_minutes and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title="策略不活跃告警",
                message=f"已 {elapsed_min:.0f} 分钟无成交 (阈值 {self.inactive_minutes:.0f}m)",
                source=self.name,
            )
        return None


class CustomAlert(AlertRule):
    def __init__(self, name: str, condition: Callable[[PortfolioSnapshot], bool],
                 message_fn: Callable[[PortfolioSnapshot], str],
                 level: AlertLevel = AlertLevel.WARNING):
        super().__init__(name, level)
        self._condition = condition
        self._message_fn = message_fn

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        if self._condition(snapshot) and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level, title=self.name,
                message=self._message_fn(snapshot), source=self.name,
            )
        return None


# ============================================================================
# 通知器
# ============================================================================

class AlertNotifier(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> bool:
        ...


class LogNotifier(AlertNotifier):
    def __init__(self, logger_name: str = "aipha.monitor"):
        self._logger = logging.getLogger(logger_name)

    def send(self, alert: Alert) -> bool:
        level_map = {AlertLevel.INFO: logging.INFO, AlertLevel.WARNING: logging.WARNING,
                     AlertLevel.CRITICAL: logging.CRITICAL}
        self._logger.log(level_map.get(alert.level, logging.WARNING),
                         "[%s] %s: %s", alert.level.value, alert.title, alert.message)
        return True


class CallbackNotifier(AlertNotifier):
    def __init__(self, callback: Callable[[Alert], None]):
        self._callback = callback

    def send(self, alert: Alert) -> bool:
        try:
            self._callback(alert)
            return True
        except Exception as e:
            logger.error("CallbackNotifier error: %s", e)
            return False


class FileAuditNotifier(AlertNotifier):
    def __init__(self, audit_path: str | Path | None = None):
        if audit_path is None:
            audit_path = Path("logs/audit/alerts.jsonl")
        self._path = Path(audit_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert) -> bool:
        try:
            record = {
                "ts": alert.timestamp.isoformat(),
                "id": alert.id, "level": alert.level.value,
                "title": alert.title, "message": alert.message,
                "source": alert.source, "metadata": alert.metadata,
            }
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            logger.error("FileAuditNotifier error: %s", e)
            return False


# ============================================================================
# 交易监控器
# ============================================================================

class TradingMonitor:
    """交易监控器 — 集成告警规则 + 通知器"""

    def __init__(self):
        self._rules: list[AlertRule] = []
        self._notifiers: list[AlertNotifier] = []
        self._alert_history: list[Alert] = []

    def add_rule(self, rule: AlertRule):
        self._rules.append(rule)

    def add_notifier(self, notifier: AlertNotifier):
        self._notifiers.append(notifier)

    def check(self, snapshot: PortfolioSnapshot) -> list[Alert]:
        fired = []
        for rule in self._rules:
            alert = rule.check(snapshot)
            if alert:
                fired.append(alert)
                self._alert_history.append(alert)
                for notifier in self._notifiers:
                    try:
                        notifier.send(alert)
                    except Exception as e:
                        logger.error("通知发送失败: %s", e)
        return fired

    @property
    def alert_history(self) -> list[Alert]:
        return self._alert_history

    def clear_history(self):
        self._alert_history.clear()

    def create_default_monitor(
        max_drawdown: float = 0.1,
        daily_loss: float = 0.03,
        concentration: float = 0.2,
    ) -> "TradingMonitor":
        m = TradingMonitor()
        m.add_rule(MaxDrawdownAlert(max_drawdown))
        m.add_rule(DailyLossAlert(daily_loss))
        m.add_rule(PositionConcentrationAlert(concentration))
        m.add_notifier(LogNotifier())
        return m
