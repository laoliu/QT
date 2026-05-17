"""
监控与告警模块

提供实时监控和告警功能：
- 性能监控：延迟、吞吐量、资源使用
- 交易监控：订单状态、持仓变化、风险指标
- 告警系统：阈值告警、异常检测、通知发送

使用示例：
    monitor = TradingMonitor()
    monitor.add_alert(MaxDrawdownAlert(threshold=0.1))
    monitor.update(portfolio_snapshot)
    alerts = monitor.check_alerts()
"""

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
    """告警级别"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(Enum):
    """告警状态"""

    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass
class Alert:
    """告警实例"""

    id: str
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    status: AlertStatus = AlertStatus.ACTIVE
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def acknowledge(self) -> None:
        """确认告警"""
        self.status = AlertStatus.ACKNOWLEDGED

    def resolve(self) -> None:
        """解决告警"""
        self.status = AlertStatus.RESOLVED


@dataclass
class PortfolioSnapshot:
    """组合快照"""

    timestamp: datetime
    total_value: float
    cash: float
    positions: dict[str, float]  # {symbol: value}
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    drawdown: float = 0.0
    leverage: float = 1.0
    turnover: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceMetrics:
    """性能指标"""

    latency_ms: float = 0.0
    orders_per_second: float = 0.0
    fills_per_second: float = 0.0
    cpu_usage_pct: float = 0.0
    memory_usage_mb: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


class AlertRule(ABC):
    """告警规则基类"""

    def __init__(self, name: str, level: AlertLevel = AlertLevel.WARNING):
        self.name = name
        self.level = level
        self._last_triggered: datetime | None = None
        self._cooldown_seconds: float = 60.0  # 冷却时间

    @abstractmethod
    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查告警条件

        Args:
            snapshot: 组合快照

        Returns:
            触发时返回 Alert，否则 None
        """
        pass

    def can_trigger(self) -> bool:
        """检查是否可以触发（考虑冷却时间）"""
        if self._last_triggered is None:
            return True
        elapsed = (datetime.now() - self._last_triggered).total_seconds()
        return elapsed >= self._cooldown_seconds

    def mark_triggered(self) -> None:
        """标记已触发"""
        self._last_triggered = datetime.now()


class MaxDrawdownAlert(AlertRule):
    """最大回撤告警"""

    def __init__(self, threshold: float = 0.1, level: AlertLevel = AlertLevel.CRITICAL):
        """
        Args:
            threshold: 回撤阈值（如 0.1 表示 10%）
            level: 告警级别
        """
        super().__init__(f"max_drawdown_{threshold:.0%}", level)
        self.threshold = threshold

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查当前回撤是否超过阈值，超过则触发告警。"""
        if snapshot.drawdown >= self.threshold and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="最大回撤告警",
                message=f"当前回撤 {snapshot.drawdown:.2%} 超过阈值 {self.threshold:.2%}",
                source=self.name,
                metadata={"drawdown": snapshot.drawdown, "threshold": self.threshold},
            )
        return None


class PositionConcentrationAlert(AlertRule):
    """持仓集中度告警"""

    def __init__(self, max_single_pct: float = 0.2, level: AlertLevel = AlertLevel.WARNING):
        """
        Args:
            max_single_pct: 单个持仓最大占比
            level: 告警级别
        """
        super().__init__(f"position_concentration_{max_single_pct:.0%}", level)
        self.max_single_pct = max_single_pct

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查单个持仓占比是否超过集中度阈值。"""
        if snapshot.total_value <= 0:
            return None

        for symbol, value in snapshot.positions.items():
            pct = abs(value) / snapshot.total_value
            if pct >= self.max_single_pct and self.can_trigger():
                self.mark_triggered()
                return Alert(
                    id=f"{self.name}_{int(time.time())}",
                    level=self.level,
                    title="持仓集中度告警",
                    message=f"{symbol} 持仓占比 {pct:.2%} 超过阈值 {self.max_single_pct:.2%}",
                    source=self.name,
                    metadata={"symbol": symbol, "pct": pct, "threshold": self.max_single_pct},
                )
        return None


class LeverageAlert(AlertRule):
    """杠杆率告警"""

    def __init__(self, max_leverage: float = 1.5, level: AlertLevel = AlertLevel.WARNING):
        super().__init__(f"leverage_{max_leverage:.1f}x", level)
        self.max_leverage = max_leverage

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查当前杠杆率是否超过阈值。"""
        if snapshot.leverage >= self.max_leverage and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="杠杆率告警",
                message=f"当前杠杆 {snapshot.leverage:.2f}x 超过阈值 {self.max_leverage:.2f}x",
                source=self.name,
                metadata={"leverage": snapshot.leverage, "threshold": self.max_leverage},
            )
        return None


class TurnoverAlert(AlertRule):
    """换手率告警"""

    def __init__(self, max_turnover: float = 0.5, level: AlertLevel = AlertLevel.INFO):
        super().__init__(f"turnover_{max_turnover:.0%}", level)
        self.max_turnover = max_turnover

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查当前换手率是否超过阈值。"""
        if snapshot.turnover >= self.max_turnover and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="换手率告警",
                message=f"当前换手率 {snapshot.turnover:.2%} 超过阈值 {self.max_turnover:.2%}",
                source=self.name,
                metadata={"turnover": snapshot.turnover, "threshold": self.max_turnover},
            )
        return None


class DailyLossAlert(AlertRule):
    """日内亏损告警"""

    def __init__(self, max_loss_pct: float = 0.03, level: AlertLevel = AlertLevel.CRITICAL):
        super().__init__(f"daily_loss_{max_loss_pct:.0%}", level)
        self.max_loss_pct = max_loss_pct
        self._day_start_value: float | None = None
        self._current_day: datetime | None = None

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查日内亏损是否超过阈值，新交易日自动重置基准。"""
        today = snapshot.timestamp.date()

        # 新的一天，重置基准
        if self._current_day is None or today != self._current_day:
            self._current_day = today
            self._day_start_value = snapshot.total_value
            return None

        if self._day_start_value is None or self._day_start_value <= 0:
            return None

        daily_return = (snapshot.total_value - self._day_start_value) / self._day_start_value

        if daily_return <= -self.max_loss_pct and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="日内亏损告警",
                message=f"日内亏损 {abs(daily_return):.2%} 超过阈值 {self.max_loss_pct:.2%}",
                source=self.name,
                metadata={
                    "daily_return": daily_return,
                    "threshold": self.max_loss_pct,
                    "start_value": self._day_start_value,
                    "current_value": snapshot.total_value,
                },
            )
        return None


class CustomAlert(AlertRule):
    """自定义告警"""

    def __init__(
        self,
        name: str,
        condition: Callable[[PortfolioSnapshot], bool],
        message_fn: Callable[[PortfolioSnapshot], str],
        level: AlertLevel = AlertLevel.WARNING,
    ):
        super().__init__(name, level)
        self._condition = condition
        self._message_fn = message_fn

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """根据自定义条件函数检查是否触发告警。"""
        if self._condition(snapshot) and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title=self.name,
                message=self._message_fn(snapshot),
                source=self.name,
            )
        return None


class DataStaleAlert(AlertRule):
    """数据中断告警

    当持续 N 秒未收到新 Bar 时触发（交易时段内）。
    通过 snapshot.metadata["last_bar_time"] 判断距今时长。
    """

    def __init__(
        self,
        stale_seconds: float = 120.0,
        level: AlertLevel = AlertLevel.CRITICAL,
    ):
        """
        Args:
            stale_seconds: 超过此秒数未收到数据视为中断
            level: 告警级别
        """
        super().__init__(f"data_stale_{stale_seconds:.0f}s", level)
        self.stale_seconds = stale_seconds
        self._cooldown_seconds = max(stale_seconds, 120.0)

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查距上次行情更新是否超过阈值秒数。"""
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
                level=self.level,
                title="数据中断告警",
                message=(
                    f"已 {elapsed:.0f}s 未收到行情数据 "
                    f"(阈值 {self.stale_seconds:.0f}s)"
                ),
                source=self.name,
                metadata={
                    "elapsed_seconds": elapsed,
                    "threshold": self.stale_seconds,
                    "last_bar_time": str(last_bar_time),
                },
            )
        return None


class StrategyInactivityAlert(AlertRule):
    """策略不活跃告警

    当策略在交易时段内持续 N 分钟无任何成交时触发。
    适用于频繁调仓策略的异常检测。
    通过 snapshot.metadata["last_fill_time"] 判断。
    """

    def __init__(
        self,
        inactive_minutes: float = 120.0,
        level: AlertLevel = AlertLevel.WARNING,
    ):
        """
        Args:
            inactive_minutes: 超过此分钟数无成交视为不活跃
            level: 告警级别
        """
        super().__init__(f"strategy_inactive_{inactive_minutes:.0f}m", level)
        self.inactive_minutes = inactive_minutes
        # 冷却时间 = 不活跃阈值的一半
        self._cooldown_seconds = max(inactive_minutes * 30, 300.0)

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查策略是否在交易时段内持续无成交。"""
        last_fill = snapshot.metadata.get("last_fill_time")
        if last_fill is None:
            # 尚无成交记录，不触发（可能刚启动）
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
                level=self.level,
                title="策略不活跃告警",
                message=(
                    f"已 {elapsed_min:.0f} 分钟无任何成交 "
                    f"(阈值 {self.inactive_minutes:.0f} 分钟)"
                ),
                source=self.name,
                metadata={
                    "elapsed_minutes": elapsed_min,
                    "threshold": self.inactive_minutes,
                    "last_fill_time": str(last_fill),
                },
            )
        return None


class StateStaleAlert(AlertRule):
    """模拟盘状态文件陈旧告警 (B2: R88)

    检查 ``state.json`` 的最后修改时间，若超过 ``stale_hours``
    小时未更新，触发告警。用于 cron 级别的运维监控。

    通过 snapshot.metadata["state_file_mtime"] 传入状态文件的
    修改时间（ISO 格式字符串或 datetime）。
    """

    def __init__(
        self,
        stale_hours: float = 24.0,
        level: AlertLevel = AlertLevel.CRITICAL,
    ):
        super().__init__(f"state_stale_{stale_hours:.0f}h", level)
        self.stale_hours = stale_hours
        self._cooldown_seconds = max(stale_hours * 3600, 3600.0)

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查状态文件是否超过指定小时未更新。"""
        mtime = snapshot.metadata.get("state_file_mtime")
        if mtime is None:
            return None

        if isinstance(mtime, str):
            try:
                mtime = datetime.fromisoformat(mtime)
            except (ValueError, TypeError):
                return None

        elapsed_h = (snapshot.timestamp - mtime).total_seconds() / 3600.0
        if elapsed_h >= self.stale_hours and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="模拟盘状态陈旧告警",
                message=(
                    f"state.json 已 {elapsed_h:.1f}h 未更新 "
                    f"(阈值 {self.stale_hours:.0f}h)"
                ),
                source=self.name,
                metadata={
                    "elapsed_hours": round(elapsed_h, 2),
                    "threshold_hours": self.stale_hours,
                    "last_modified": str(mtime),
                },
            )
        return None


class AlertNotifier(ABC):
    """告警通知器基类"""

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        """发送告警通知

        Args:
            alert: 告警

        Returns:
            是否发送成功
        """
        pass


class LogNotifier(AlertNotifier):
    """日志通知器"""

    def __init__(self, logger_name: str = "aipha.monitor"):
        self._logger = logging.getLogger(logger_name)

    def send(self, alert: Alert) -> bool:
        """通过日志输出告警信息。"""
        level_map = {
            AlertLevel.INFO: logging.INFO,
            AlertLevel.WARNING: logging.WARNING,
            AlertLevel.CRITICAL: logging.CRITICAL,
        }
        log_level = level_map.get(alert.level, logging.WARNING)
        self._logger.log(log_level, f"[{alert.level.value}] {alert.title}: {alert.message}")
        return True


class CallbackNotifier(AlertNotifier):
    """回调通知器"""

    def __init__(self, callback: Callable[[Alert], None]):
        self._callback = callback

    def send(self, alert: Alert) -> bool:
        """通过回调函数发送告警。"""
        try:
            self._callback(alert)
            return True
        except Exception as e:
            logger.error(f"Callback notifier error: {e}")
            return False


class FileAuditNotifier(AlertNotifier):
    """文件审计通知器 — 将告警写入 JSONL 审计文件（H5 fix）。

    默认写入 logs/audit/alerts.jsonl，持久化所有生产告警记录。
    """

    def __init__(self, audit_path: str | Path | None = None):
        if audit_path is None:
            # 默认: 项目根 / logs / audit / alerts.jsonl
            _root = Path(__file__).resolve().parent.parent.parent.parent
            audit_path = _root / "logs" / "audit" / "alerts.jsonl"
        self._path = Path(audit_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert) -> bool:
        """追加写入告警到 JSONL 审计文件。"""
        try:
            record = {
                "ts": alert.timestamp.isoformat(),
                "id": alert.id,
                "level": alert.level.value,
                "title": alert.title,
                "message": alert.message,
                "source": alert.source,
                "metadata": alert.metadata,
            }
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            logger.error(f"FileAuditNotifier write error: {e}")
            return False


# ============================================================================
# 可转债专用告警
# ============================================================================


class ForceRedeemExposureAlert(AlertRule):
    """强赎暴露告警

    当持仓中转股价值 >= 阈值的标的权重占比超过指定比例时触发。
    通过 snapshot.metadata["cb_values"] 获取各标的转股价值。
    """

    def __init__(
        self,
        cb_value_threshold: float = 125.0,
        max_exposure_pct: float = 0.30,
        level: AlertLevel = AlertLevel.WARNING,
    ):
        super().__init__(
            f"force_redeem_exposure_{cb_value_threshold:.0f}",
            level,
        )
        self.cb_value_threshold = cb_value_threshold
        self.max_exposure_pct = max_exposure_pct

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查持仓中转股价值超过强赎阈值的标的权重占比。"""
        if snapshot.total_value <= 0:
            return None

        cb_values = snapshot.metadata.get("cb_values", {})
        if not cb_values:
            return None

        exposure_value = 0.0
        exposed_symbols = []
        for symbol, pos_value in snapshot.positions.items():
            cv = cb_values.get(symbol, 0.0)
            if cv >= self.cb_value_threshold:
                exposure_value += abs(pos_value)
                exposed_symbols.append(symbol)

        exposure_pct = exposure_value / snapshot.total_value

        if exposure_pct >= self.max_exposure_pct and self.can_trigger():
            self.mark_triggered()
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="强赎暴露告警",
                message=(
                    f"强赎区间暴露 {exposure_pct:.2%} 超过阈值 {self.max_exposure_pct:.2%}，"
                    f"涉及 {len(exposed_symbols)} 只标的"
                ),
                source=self.name,
                metadata={
                    "exposure_pct": exposure_pct,
                    "threshold": self.max_exposure_pct,
                    "exposed_symbols": exposed_symbols[:10],
                    "cb_value_threshold": self.cb_value_threshold,
                },
            )
        return None


class CBLiquidityAlert(AlertRule):
    """可转债流动性告警

    当持仓中存在成交额低于阈值的标的时触发。
    通过 snapshot.metadata["daily_amounts"] 获取各标的当日成交额。
    """

    def __init__(
        self,
        min_amount: float = 50.0,  # 最低成交额（万元）
        level: AlertLevel = AlertLevel.WARNING,
    ):
        super().__init__(f"cb_liquidity_{min_amount:.0f}", level)
        self.min_amount = min_amount

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查持仓中是否存在成交额低于流动性阈值的标的。"""
        daily_amounts = snapshot.metadata.get("daily_amounts", {})
        if not daily_amounts:
            return None

        illiquid_symbols = []
        for symbol in snapshot.positions:
            amt = daily_amounts.get(symbol, float("inf"))
            if amt < self.min_amount and abs(snapshot.positions.get(symbol, 0)) > 0:
                illiquid_symbols.append((symbol, amt))

        if illiquid_symbols and self.can_trigger():
            self.mark_triggered()
            details = ", ".join(
                f"{s}({a:.0f}万)" for s, a in illiquid_symbols[:5]
            )
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="可转债流动性告警",
                message=(
                    f"{len(illiquid_symbols)} 只持仓标的成交额低于 "
                    f"{self.min_amount:.0f}万: {details}"
                ),
                source=self.name,
                metadata={
                    "illiquid_count": len(illiquid_symbols),
                    "threshold": self.min_amount,
                    "symbols": [s for s, _ in illiquid_symbols],
                },
            )
        return None


class PremiumDistributionAlert(AlertRule):
    """溢价率分布告警

    当持仓平均溢价率过高或过低时触发，提示策略可能暴露于
    溢价率回归风险或负溢价套利机会减少。
    通过 snapshot.metadata["cb_premiums"] 获取持仓的溢价率。
    """

    def __init__(
        self,
        max_avg_premium: float = 40.0,
        min_avg_premium: float = -5.0,
        level: AlertLevel = AlertLevel.INFO,
    ):
        super().__init__("premium_distribution", level)
        self.max_avg_premium = max_avg_premium
        self.min_avg_premium = min_avg_premium

    def check(self, snapshot: PortfolioSnapshot) -> Alert | None:
        """检查持仓平均溢价率是否超出合理区间。"""
        cb_premiums = snapshot.metadata.get("cb_premiums", {})
        if not cb_premiums:
            return None

        held_premiums = [
            cb_premiums[s]
            for s in snapshot.positions
            if s in cb_premiums and abs(snapshot.positions[s]) > 0
        ]
        if not held_premiums:
            return None

        avg_premium = sum(held_premiums) / len(held_premiums)

        if (avg_premium > self.max_avg_premium or avg_premium < self.min_avg_premium) and self.can_trigger():
            self.mark_triggered()
            direction = "过高" if avg_premium > self.max_avg_premium else "过低"
            return Alert(
                id=f"{self.name}_{int(time.time())}",
                level=self.level,
                title="溢价率分布告警",
                message=(
                    f"持仓平均溢价率 {direction}: {avg_premium:.1f}% "
                    f"(阈值: {self.min_avg_premium:.1f}% ~ {self.max_avg_premium:.1f}%)"
                ),
                source=self.name,
                metadata={
                    "avg_premium": avg_premium,
                    "max_threshold": self.max_avg_premium,
                    "min_threshold": self.min_avg_premium,
                    "count": len(held_premiums),
                },
            )
        return None


class TradingMonitor:
    """交易监控器"""

    def __init__(self, add_log_notifier: bool = True):
        """初始化交易监控器。

        Args:
            add_log_notifier: 是否自动添加 LogNotifier（默认 True）。
                设为 False 仅在测试中需要完全静默输出时使用。
        """
        self._rules: list[AlertRule] = []
        self._notifiers: list[AlertNotifier] = []
        self._alerts: list[Alert] = []
        self._snapshots: list[PortfolioSnapshot] = []
        self._max_history: int = 1000
        # H5 fix: 默认自动注册 LogNotifier，确保告警不被静默丢弃
        if add_log_notifier:
            self._notifiers.append(LogNotifier())

    def add_rule(self, rule: AlertRule) -> None:
        """添加告警规则"""
        self._rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        """移除告警规则"""
        for i, rule in enumerate(self._rules):
            if rule.name == name:
                self._rules.pop(i)
                return True
        return False

    def add_notifier(self, notifier: AlertNotifier) -> None:
        """添加通知器"""
        self._notifiers.append(notifier)

    def update(self, snapshot: PortfolioSnapshot) -> list[Alert]:
        """更新监控状态并检查告警

        Args:
            snapshot: 组合快照

        Returns:
            触发的告警列表
        """
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self._max_history:
            self._snapshots = self._snapshots[-self._max_history:]

        triggered_alerts = []
        for rule in self._rules:
            alert = rule.check(snapshot)
            if alert:
                self._alerts.append(alert)
                triggered_alerts.append(alert)
                self._notify(alert)

        return triggered_alerts

    def _notify(self, alert: Alert) -> None:
        """发送告警通知"""
        for notifier in self._notifiers:
            try:
                notifier.send(alert)
            except Exception as e:
                logger.error(f"Notifier error: {e}")

    def get_alerts(
        self,
        level: AlertLevel | None = None,
        status: AlertStatus | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """获取告警列表

        Args:
            level: 过滤告警级别
            status: 过滤告警状态
            limit: 最大返回数量

        Returns:
            告警列表
        """
        alerts = self._alerts

        if level is not None:
            alerts = [a for a in alerts if a.level == level]

        if status is not None:
            alerts = [a for a in alerts if a.status == status]

        return alerts[-limit:]

    def get_snapshots(self, limit: int = 100) -> list[PortfolioSnapshot]:
        """获取组合快照历史"""
        return self._snapshots[-limit:]

    def clear_resolved_alerts(self) -> int:
        """清理已解决的告警

        Returns:
            清理的数量
        """
        before = len(self._alerts)
        self._alerts = [a for a in self._alerts if a.status != AlertStatus.RESOLVED]
        return before - len(self._alerts)

    @property
    def active_alerts_count(self) -> int:
        """活跃告警数量"""
        return sum(1 for a in self._alerts if a.status == AlertStatus.ACTIVE)

    @property
    def rules_count(self) -> int:
        """告警规则数量"""
        return len(self._rules)
