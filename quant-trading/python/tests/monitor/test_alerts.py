"""监控与告警模块测试"""

from datetime import datetime, timedelta

import pytest

from aipha.monitor.alerts import (
    Alert,
    AlertLevel,
    AlertStatus,
    CallbackNotifier,
    CustomAlert,
    DailyLossAlert,
    LeverageAlert,
    LogNotifier,
    MaxDrawdownAlert,
    PerformanceMetrics,
    PortfolioSnapshot,
    PositionConcentrationAlert,
    StateStaleAlert,
    TradingMonitor,
    TurnoverAlert,
)


class TestAlert:
    """Alert 数据类测试"""

    def test_alert_creation(self):
        """测试告警创建"""
        alert = Alert(
            id="test_001",
            level=AlertLevel.WARNING,
            title="Test Alert",
            message="This is a test",
        )

        assert alert.id == "test_001"
        assert alert.level == AlertLevel.WARNING
        assert alert.status == AlertStatus.ACTIVE

    def test_alert_acknowledge(self):
        """测试确认告警"""
        alert = Alert(
            id="test_001",
            level=AlertLevel.WARNING,
            title="Test",
            message="Test",
        )

        alert.acknowledge()
        assert alert.status == AlertStatus.ACKNOWLEDGED

    def test_alert_resolve(self):
        """测试解决告警"""
        alert = Alert(
            id="test_001",
            level=AlertLevel.WARNING,
            title="Test",
            message="Test",
        )

        alert.resolve()
        assert alert.status == AlertStatus.RESOLVED


class TestPortfolioSnapshot:
    """PortfolioSnapshot 数据类测试"""

    def test_snapshot_creation(self):
        """测试快照创建"""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=200000.0,
            positions={"000001.SZ": 300000.0, "600000.SH": 500000.0},
            drawdown=0.05,
        )

        assert snapshot.total_value == 1000000.0
        assert snapshot.cash == 200000.0
        assert len(snapshot.positions) == 2


class TestMaxDrawdownAlert:
    """MaxDrawdownAlert 测试"""

    def test_trigger_on_high_drawdown(self):
        """测试高回撤触发告警"""
        rule = MaxDrawdownAlert(threshold=0.1)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=900000.0,
            cash=100000.0,
            positions={},
            drawdown=0.15,  # 15% > 10%
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert alert.level == AlertLevel.CRITICAL
        assert "回撤" in alert.title

    def test_no_trigger_below_threshold(self):
        """测试低回撤不触发"""
        rule = MaxDrawdownAlert(threshold=0.1)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=950000.0,
            cash=100000.0,
            positions={},
            drawdown=0.05,  # 5% < 10%
        )

        alert = rule.check(snapshot)
        assert alert is None

    def test_cooldown(self):
        """测试冷却时间"""
        rule = MaxDrawdownAlert(threshold=0.1)
        rule._cooldown_seconds = 0  # 禁用冷却

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=900000.0,
            cash=100000.0,
            positions={},
            drawdown=0.15,
        )

        # 第一次触发
        alert1 = rule.check(snapshot)
        assert alert1 is not None

        # 恢复冷却
        rule._cooldown_seconds = 60

        # 冷却期内不触发
        alert2 = rule.check(snapshot)
        assert alert2 is None


class TestPositionConcentrationAlert:
    """PositionConcentrationAlert 测试"""

    def test_trigger_on_concentration(self):
        """测试集中度过高触发"""
        rule = PositionConcentrationAlert(max_single_pct=0.2)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=100000.0,
            positions={"000001.SZ": 300000.0},  # 30% > 20%
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert "集中度" in alert.title

    def test_no_trigger_balanced(self):
        """测试分散持仓不触发"""
        rule = PositionConcentrationAlert(max_single_pct=0.2)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=100000.0,
            positions={
                "000001.SZ": 150000.0,  # 15%
                "600000.SH": 150000.0,  # 15%
            },
        )

        alert = rule.check(snapshot)
        assert alert is None


class TestLeverageAlert:
    """LeverageAlert 测试"""

    def test_trigger_on_high_leverage(self):
        """测试高杠杆触发"""
        rule = LeverageAlert(max_leverage=1.5)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=100000.0,
            positions={},
            leverage=2.0,  # 2x > 1.5x
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert "杠杆" in alert.title


class TestTurnoverAlert:
    """TurnoverAlert 测试"""

    def test_trigger_on_high_turnover(self):
        """测试高换手率触发"""
        rule = TurnoverAlert(max_turnover=0.5)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=100000.0,
            positions={},
            turnover=0.8,  # 80% > 50%
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert "换手" in alert.title


class TestDailyLossAlert:
    """DailyLossAlert 测试"""

    def test_trigger_on_daily_loss(self):
        """测试日内亏损触发"""
        rule = DailyLossAlert(max_loss_pct=0.03)
        # 使用固定时间戳确保同一天（上午10点，避免跨天问题）
        today = datetime(2024, 6, 15, 10, 0, 0)

        # 第一次更新，设置基准
        snapshot1 = PortfolioSnapshot(
            timestamp=today,
            total_value=1000000.0,
            cash=100000.0,
            positions={},
        )
        alert1 = rule.check(snapshot1)
        assert alert1 is None

        # 第二次更新，亏损超过阈值（同一天下午）
        snapshot2 = PortfolioSnapshot(
            timestamp=today + timedelta(hours=2),
            total_value=950000.0,  # -5% > -3%
            cash=100000.0,
            positions={},
        )
        alert2 = rule.check(snapshot2)
        assert alert2 is not None
        assert "日内亏损" in alert2.title

    def test_reset_on_new_day(self):
        """测试新的一天重置基准"""
        rule = DailyLossAlert(max_loss_pct=0.03)
        today = datetime.now()
        tomorrow = today + timedelta(days=1)

        # 今天
        snapshot1 = PortfolioSnapshot(
            timestamp=today,
            total_value=1000000.0,
            cash=100000.0,
            positions={},
        )
        rule.check(snapshot1)

        # 明天
        snapshot2 = PortfolioSnapshot(
            timestamp=tomorrow,
            total_value=950000.0,
            cash=100000.0,
            positions={},
        )
        alert = rule.check(snapshot2)
        # 新的一天，应该重置基准，不触发告警
        assert alert is None


class TestCustomAlert:
    """CustomAlert 测试"""

    def test_custom_condition(self):
        """测试自定义条件"""
        rule = CustomAlert(
            name="low_cash",
            condition=lambda s: s.cash < 100000,
            message_fn=lambda s: f"现金不足: {s.cash:.0f}",
        )

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=50000.0,  # < 100000
            positions={},
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert "现金不足" in alert.message


class TestNotifiers:
    """通知器测试"""

    def test_log_notifier(self, caplog):
        """测试日志通知器"""
        notifier = LogNotifier()
        alert = Alert(
            id="test_001",
            level=AlertLevel.WARNING,
            title="Test Alert",
            message="Test message",
        )

        result = notifier.send(alert)
        assert result is True

    def test_callback_notifier(self):
        """测试回调通知器"""
        received_alerts = []
        notifier = CallbackNotifier(callback=lambda a: received_alerts.append(a))

        alert = Alert(
            id="test_001",
            level=AlertLevel.INFO,
            title="Test",
            message="Test",
        )

        result = notifier.send(alert)
        assert result is True
        assert len(received_alerts) == 1
        assert received_alerts[0].id == "test_001"


class TestTradingMonitor:
    """TradingMonitor 测试"""

    def test_add_remove_rule(self):
        """测试添加/移除规则"""
        monitor = TradingMonitor()
        rule = MaxDrawdownAlert(threshold=0.1)

        monitor.add_rule(rule)
        assert monitor.rules_count == 1

        removed = monitor.remove_rule(rule.name)
        assert removed is True
        assert monitor.rules_count == 0

    def test_update_triggers_alert(self):
        """测试更新触发告警"""
        monitor = TradingMonitor()
        monitor.add_rule(MaxDrawdownAlert(threshold=0.1))

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=900000.0,
            cash=100000.0,
            positions={},
            drawdown=0.15,
        )

        alerts = monitor.update(snapshot)
        assert len(alerts) == 1
        assert monitor.active_alerts_count == 1

    def test_notifier_called(self):
        """测试通知器被调用"""
        monitor = TradingMonitor()
        monitor.add_rule(MaxDrawdownAlert(threshold=0.1))

        received = []
        monitor.add_notifier(CallbackNotifier(callback=lambda a: received.append(a)))

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=900000.0,
            cash=100000.0,
            positions={},
            drawdown=0.15,
        )

        monitor.update(snapshot)
        assert len(received) == 1

    def test_get_alerts_filter(self):
        """测试过滤告警"""
        monitor = TradingMonitor()
        monitor.add_rule(MaxDrawdownAlert(threshold=0.1))
        monitor.add_rule(TurnoverAlert(max_turnover=0.3))

        # 禁用冷却以便测试
        for rule in monitor._rules:
            rule._cooldown_seconds = 0

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=900000.0,
            cash=100000.0,
            positions={},
            drawdown=0.15,
            turnover=0.5,
        )

        monitor.update(snapshot)

        critical_alerts = monitor.get_alerts(level=AlertLevel.CRITICAL)
        info_alerts = monitor.get_alerts(level=AlertLevel.INFO)

        assert len(critical_alerts) == 1
        assert len(info_alerts) == 1

    def test_clear_resolved_alerts(self):
        """测试清理已解决告警"""
        monitor = TradingMonitor()
        monitor.add_rule(MaxDrawdownAlert(threshold=0.1))

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=900000.0,
            cash=100000.0,
            positions={},
            drawdown=0.15,
        )

        monitor.update(snapshot)
        assert monitor.active_alerts_count == 1

        # 解决告警
        for alert in monitor.get_alerts():
            alert.resolve()

        cleared = monitor.clear_resolved_alerts()
        assert cleared == 1
        assert len(monitor.get_alerts()) == 0

    def test_snapshot_history(self):
        """测试快照历史"""
        monitor = TradingMonitor()

        for i in range(5):
            snapshot = PortfolioSnapshot(
                timestamp=datetime.now(),
                total_value=1000000.0 + i * 1000,
                cash=100000.0,
                positions={},
            )
            monitor.update(snapshot)

        snapshots = monitor.get_snapshots()
        assert len(snapshots) == 5


# ─────────────────────────────────────────────────────────────────────────────
# 新增告警类型测试
# ─────────────────────────────────────────────────────────────────────────────


class TestDataStaleAlert:
    """数据中断告警测试"""

    def test_trigger_when_stale(self):
        """数据超时触发告警"""
        from aipha.monitor.alerts import DataStaleAlert

        rule = DataStaleAlert(stale_seconds=60.0)
        now = datetime.now()

        snapshot = PortfolioSnapshot(
            timestamp=now,
            total_value=1000000.0,
            cash=100000.0,
            positions={},
            metadata={
                "last_bar_time": (now - timedelta(seconds=120)).isoformat()
            },
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert "数据中断" in alert.title
        assert "120" in alert.message  # 120s elapsed

    def test_no_trigger_when_fresh(self):
        """数据新鲜时不触发"""
        from aipha.monitor.alerts import DataStaleAlert

        rule = DataStaleAlert(stale_seconds=60.0)
        now = datetime.now()

        snapshot = PortfolioSnapshot(
            timestamp=now,
            total_value=1000000.0,
            cash=100000.0,
            positions={},
            metadata={
                "last_bar_time": (now - timedelta(seconds=10)).isoformat()
            },
        )

        alert = rule.check(snapshot)
        assert alert is None

    def test_no_trigger_without_metadata(self):
        """无 last_bar_time 时不触发"""
        from aipha.monitor.alerts import DataStaleAlert

        rule = DataStaleAlert(stale_seconds=60.0)
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=1000000.0,
            cash=100000.0,
            positions={},
        )

        alert = rule.check(snapshot)
        assert alert is None


class TestStrategyInactivityAlert:
    """策略不活跃告警测试"""

    def test_trigger_when_inactive(self):
        """长时间无成交触发"""
        from aipha.monitor.alerts import StrategyInactivityAlert

        rule = StrategyInactivityAlert(inactive_minutes=30.0)
        now = datetime.now()

        snapshot = PortfolioSnapshot(
            timestamp=now,
            total_value=1000000.0,
            cash=100000.0,
            positions={},
            metadata={
                "last_fill_time": (now - timedelta(minutes=60)).isoformat()
            },
        )

        alert = rule.check(snapshot)
        assert alert is not None
        assert "不活跃" in alert.title

    def test_no_trigger_when_active(self):
        """近期有成交不触发"""
        from aipha.monitor.alerts import StrategyInactivityAlert

        rule = StrategyInactivityAlert(inactive_minutes=30.0)
        now = datetime.now()

        snapshot = PortfolioSnapshot(
            timestamp=now,
            total_value=1000000.0,
            cash=100000.0,
            positions={},
            metadata={
                "last_fill_time": (now - timedelta(minutes=5)).isoformat()
            },
        )

        alert = rule.check(snapshot)
        assert alert is None

        alert = rule.check(snapshot)
        assert alert is None


# ─────────────────────────────────────────────────────────────────────────────
# H5 回归测试: TradingMonitor 自动 LogNotifier + FileAuditNotifier
# ─────────────────────────────────────────────────────────────────────────────

class TestTradingMonitorH5Fix:
    """测试 TradingMonitor 默认自动注册 LogNotifier（H5 fix）。"""

    def test_default_has_log_notifier(self):
        """TradingMonitor() 默认自动组加 LogNotifier。"""
        monitor = TradingMonitor()
        from aipha.monitor.alerts import LogNotifier
        assert any(isinstance(n, LogNotifier) for n in monitor._notifiers)

    def test_no_log_notifier_when_disabled(self):
        """add_log_notifier=False 时不添加。"""
        monitor = TradingMonitor(add_log_notifier=False)
        assert len(monitor._notifiers) == 0

    def test_file_audit_notifier_writes_jsonl(self, tmp_path):
        """FileAuditNotifier 将告警写入 JSONL 文件。"""
        import json as _json
        from aipha.monitor.alerts import FileAuditNotifier, Alert, AlertLevel

        audit_file = tmp_path / "alerts.jsonl"
        notifier = FileAuditNotifier(audit_path=audit_file)
        alert = Alert(
            id="test_h5_001",
            level=AlertLevel.WARNING,
            title="H5 测试告警",
            message="测试消息",
        )
        result = notifier.send(alert)
        assert result is True
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = _json.loads(lines[0])
        assert record["id"] == "test_h5_001"
        assert record["level"] == "warning"

    def test_file_audit_notifier_appends_multiple(self, tmp_path):
        """FileAuditNotifier 将多条告警追加到同一文件。"""
        import json as _json
        from aipha.monitor.alerts import FileAuditNotifier, Alert, AlertLevel

        audit_file = tmp_path / "alerts.jsonl"
        notifier = FileAuditNotifier(audit_path=audit_file)
        for i in range(3):
            notifier.send(Alert(
                id=f"test_{i}",
                level=AlertLevel.INFO,
                title=f"Alert {i}",
                message="msg",
            ))
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3


# ============================================================================
# StateStaleAlert (B2: R88)
# ============================================================================


class TestStateStaleAlert:
    """模拟盘状态文件陈旧告警测试。"""

    def _make_snapshot(self, mtime_hours_ago: float | None) -> PortfolioSnapshot:
        now = datetime.now()
        metadata = {}
        if mtime_hours_ago is not None:
            mtime = now - timedelta(hours=mtime_hours_ago)
            metadata["state_file_mtime"] = mtime.isoformat()
        return PortfolioSnapshot(
            timestamp=now,
            cash=100_000,
            positions={"test": 50_000},
            total_value=150_000,
            metadata=metadata,
        )

    def test_no_mtime_no_trigger(self):
        """无 mtime 元数据时不触发。"""
        alert_rule = StateStaleAlert(stale_hours=24.0)
        snap = self._make_snapshot(None)
        assert alert_rule.check(snap) is None

    def test_fresh_state_no_trigger(self):
        """状态文件刚刚更新，不触发。"""
        alert_rule = StateStaleAlert(stale_hours=24.0)
        snap = self._make_snapshot(mtime_hours_ago=1.0)
        assert alert_rule.check(snap) is None

    def test_stale_state_triggers(self):
        """状态文件超过 24h 未更新，触发告警。"""
        alert_rule = StateStaleAlert(stale_hours=24.0)
        snap = self._make_snapshot(mtime_hours_ago=25.0)
        result = alert_rule.check(snap)
        assert result is not None
        assert result.level == AlertLevel.CRITICAL
        assert "25.0h" in result.message
        assert result.metadata["elapsed_hours"] > 24.0

    def test_custom_threshold(self):
        """自定义阈值（12h）。"""
        alert_rule = StateStaleAlert(stale_hours=12.0)
        snap = self._make_snapshot(mtime_hours_ago=13.0)
        result = alert_rule.check(snap)
        assert result is not None
        assert "13.0h" in result.message

    def test_exactly_at_threshold_triggers(self):
        """恰好等于阈值时触发。"""
        alert_rule = StateStaleAlert(stale_hours=24.0)
        snap = self._make_snapshot(mtime_hours_ago=24.0)
        result = alert_rule.check(snap)
        assert result is not None

    def test_just_under_threshold_no_trigger(self):
        """略低于阈值不触发。"""
        alert_rule = StateStaleAlert(stale_hours=24.0)
        snap = self._make_snapshot(mtime_hours_ago=23.9)
        assert alert_rule.check(snap) is None

    def test_datetime_mtime_string_parsing(self):
        """ISO 格式字符串正确解析。"""
        alert_rule = StateStaleAlert(stale_hours=1.0)
        now = datetime.now()
        old = now - timedelta(hours=2)
        snap = PortfolioSnapshot(
            timestamp=now,
            cash=100_000,
            positions={},
            total_value=100_000,
            metadata={"state_file_mtime": old.isoformat()},
        )
        result = alert_rule.check(snap)
        assert result is not None

    def test_invalid_mtime_string(self):
        """无效 mtime 字符串不崩溃。"""
        alert_rule = StateStaleAlert(stale_hours=24.0)
        snap = PortfolioSnapshot(
            timestamp=datetime.now(),
            cash=100_000,
            positions={},
            total_value=100_000,
            metadata={"state_file_mtime": "not-a-date"},
        )
        assert alert_rule.check(snap) is None

