"""
运行时监控与告警模块 (Production Runtime)

§2.1: 生产运行时告警、强赎监控、仪表盘推送。
与 aipha.monitoring (研究阶段信号质量监控) 区分。

功能:
- feishu_alert: 飞书告警推送 (§13.14 唯一交易日报入口)
- force_redeem_watcher: CB 强赎实时监控
- alerts: 告警规则引擎
- mdd_alert_checker: MDD 告警 (生产首选风控)
"""

from aipha.monitor.force_redeem_watcher import ForceRedeemWatcher, RedeemEvent
from aipha.monitor.alerts import (
    Alert,
    AlertLevel,
    AlertNotifier,
    AlertRule,
    AlertStatus,
    CallbackNotifier,
    CustomAlert,
    DailyLossAlert,
    DataStaleAlert,
    FileAuditNotifier,
    LeverageAlert,
    LogNotifier,
    MaxDrawdownAlert,
    PerformanceMetrics,
    PortfolioSnapshot,
    PositionConcentrationAlert,
    StrategyInactivityAlert,
    TradingMonitor,
    TurnoverAlert,
)
from aipha.monitor.agent_audit import AgentAudit, AuditEventType, AuditRecord

__all__ = [
    # 数据类
    "Alert",
    "AlertLevel",
    "AlertStatus",
    "PortfolioSnapshot",
    "PerformanceMetrics",
    # 基类
    "AlertRule",
    "AlertNotifier",
    # 告警规则
    "MaxDrawdownAlert",
    "PositionConcentrationAlert",
    "LeverageAlert",
    "TurnoverAlert",
    "DailyLossAlert",
    "CustomAlert",
    "DataStaleAlert",
    "StrategyInactivityAlert",
    # 通知器
    "LogNotifier",
    "CallbackNotifier",
    "FileAuditNotifier",
    # 监控器
    "TradingMonitor",
    # 强赎监控
    "ForceRedeemWatcher",
    "RedeemEvent",
    # Agent 审计
    "AgentAudit",
    "AuditRecord",
    "AuditEventType",
]
