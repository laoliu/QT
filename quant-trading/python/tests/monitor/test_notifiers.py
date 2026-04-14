"""通知渠道与告警管理器测试

覆盖：
- AlertMessage: to_dict / format_text / format_markdown
- AlertLevel / AlertCategory 枚举
- NotificationManager: 渠道管理 / 路由规则 / 消息分发
- AlertBuilder: 便捷 API
- NotificationChannel: filter_message 默认行为
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from aipha.monitor.notifiers import (
    AlertBuilder,
    AlertCategory,
    AlertLevel,
    AlertMessage,
    NotificationChannel,
    NotificationManager,
)


# ============================================================================
# Fake Channel
# ============================================================================


class FakeChannel(NotificationChannel):
    """测试用通知渠道"""

    def __init__(
        self,
        name: str = "fake",
        *,
        success: bool = True,
        filter_all: bool = False,
    ) -> None:
        self._name = name
        self._success = success
        self._filter_all = filter_all
        self.sent: list[AlertMessage] = []

    async def send(self, message: AlertMessage) -> bool:
        self.sent.append(message)
        return self._success

    def get_name(self) -> str:
        return self._name

    def filter_message(self, message: AlertMessage) -> bool:
        if self._filter_all:
            return False
        return True


class FailingChannel(NotificationChannel):
    """发送时抛异常的渠道"""

    async def send(self, message: AlertMessage) -> bool:
        raise ConnectionError("network error")

    def get_name(self) -> str:
        return "failing"


# ============================================================================
# TestAlertMessage
# ============================================================================


class TestAlertMessage:
    """AlertMessage 数据结构"""

    def test_to_dict(self) -> None:
        msg = AlertMessage(
            title="Test",
            content="Body",
            level=AlertLevel.WARNING,
            category=AlertCategory.RISK,
            source="unit_test",
        )
        d = msg.to_dict()
        assert d["title"] == "Test"
        assert d["content"] == "Body"
        assert d["level"] == "warning"
        assert d["category"] == "risk"
        assert d["source"] == "unit_test"
        assert "timestamp" in d

    def test_format_text(self) -> None:
        msg = AlertMessage(
            title="Alert Title",
            content="Some content",
            level=AlertLevel.ERROR,
            source="test_src",
        )
        text = msg.format_text()
        assert "[ERROR]" in text
        assert "Alert Title" in text
        assert "Some content" in text
        assert "test_src" in text

    def test_format_text_no_source(self) -> None:
        msg = AlertMessage(title="T", content="C", level=AlertLevel.INFO)
        text = msg.format_text()
        assert "来源" not in text

    def test_format_markdown(self) -> None:
        msg = AlertMessage(
            title="MD Alert",
            content="MD Body",
            level=AlertLevel.CRITICAL,
            category=AlertCategory.TRADE,
            source="md_test",
        )
        md = msg.format_markdown()
        assert "# 🚨" in md
        assert "MD Alert" in md
        assert "**级别**: CRITICAL" in md
        assert "**来源**: md_test" in md

    def test_format_markdown_warning_emoji(self) -> None:
        msg = AlertMessage(title="W", content="C", level=AlertLevel.WARNING)
        md = msg.format_markdown()
        assert "⚠️" in md

    def test_default_values(self) -> None:
        msg = AlertMessage(title="T", content="C")
        assert msg.level == AlertLevel.INFO
        assert msg.category == AlertCategory.SYSTEM
        assert msg.source == ""
        assert msg.metadata == {}
        assert isinstance(msg.timestamp, datetime)


# ============================================================================
# TestAlertLevel
# ============================================================================


class TestAlertLevel:
    """AlertLevel 枚举"""

    def test_values(self) -> None:
        assert AlertLevel.DEBUG.value == "debug"
        assert AlertLevel.INFO.value == "info"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.ERROR.value == "error"
        assert AlertLevel.CRITICAL.value == "critical"

    def test_string_type(self) -> None:
        assert isinstance(AlertLevel.INFO, str)
        assert AlertLevel.INFO == "info"


# ============================================================================
# TestAlertCategory
# ============================================================================


class TestAlertCategory:
    """AlertCategory 枚举"""

    def test_values(self) -> None:
        assert AlertCategory.TRADE.value == "trade"
        assert AlertCategory.RISK.value == "risk"
        assert AlertCategory.SYSTEM.value == "system"
        assert AlertCategory.MARKET.value == "market"
        assert AlertCategory.PERFORMANCE.value == "performance"


# ============================================================================
# TestNotificationManager — 渠道管理
# ============================================================================


class TestNotificationManagerChannels:
    """渠道增删"""

    def test_add_channel(self) -> None:
        mgr = NotificationManager()
        ch = FakeChannel("ch1")
        mgr.add_channel(ch)
        assert "ch1" in mgr._channels

    def test_remove_channel(self) -> None:
        mgr = NotificationManager()
        ch = FakeChannel("ch1")
        mgr.add_channel(ch)
        assert mgr.remove_channel("ch1") is True
        assert "ch1" not in mgr._channels

    def test_remove_nonexistent(self) -> None:
        mgr = NotificationManager()
        assert mgr.remove_channel("nope") is False


# ============================================================================
# TestNotificationManager — 路由规则
# ============================================================================


class TestNotificationManagerRouting:
    """路由规则"""

    def test_default_channels(self) -> None:
        mgr = NotificationManager()
        mgr.set_default_channels(["ch1", "ch2"])
        msg = AlertMessage(title="T", content="C")
        targets = mgr.get_target_channels(msg)
        assert set(targets) == {"ch1", "ch2"}

    def test_routing_by_level(self) -> None:
        mgr = NotificationManager()
        mgr.set_routing(AlertLevel.ERROR, None, ["error_ch"])
        # ERROR 级别 → 命中
        msg = AlertMessage(title="T", content="C", level=AlertLevel.ERROR)
        targets = mgr.get_target_channels(msg)
        assert "error_ch" in targets

    def test_routing_level_below_threshold(self) -> None:
        mgr = NotificationManager()
        mgr.set_routing(AlertLevel.ERROR, None, ["error_ch"])
        # INFO < ERROR → 不命中
        msg = AlertMessage(title="T", content="C", level=AlertLevel.INFO)
        targets = mgr.get_target_channels(msg)
        assert "error_ch" not in targets

    def test_routing_by_category(self) -> None:
        mgr = NotificationManager()
        mgr.set_routing(AlertLevel.INFO, AlertCategory.RISK, ["risk_ch"])
        msg = AlertMessage(
            title="T", content="C",
            level=AlertLevel.WARNING, category=AlertCategory.RISK,
        )
        targets = mgr.get_target_channels(msg)
        assert "risk_ch" in targets

    def test_routing_category_mismatch(self) -> None:
        mgr = NotificationManager()
        mgr.set_routing(AlertLevel.INFO, AlertCategory.RISK, ["risk_ch"])
        msg = AlertMessage(
            title="T", content="C",
            level=AlertLevel.ERROR, category=AlertCategory.TRADE,
        )
        targets = mgr.get_target_channels(msg)
        assert "risk_ch" not in targets

    def test_fallback_to_defaults(self) -> None:
        """无路由规则匹配时使用默认渠道"""
        mgr = NotificationManager()
        mgr.set_default_channels(["default"])
        mgr.set_routing(AlertLevel.CRITICAL, None, ["critical_ch"])
        msg = AlertMessage(title="T", content="C", level=AlertLevel.DEBUG)
        targets = mgr.get_target_channels(msg)
        assert targets == ["default"]

    def test_multiple_rules_union(self) -> None:
        mgr = NotificationManager()
        mgr.set_routing(AlertLevel.WARNING, None, ["ch1"])
        mgr.set_routing(AlertLevel.WARNING, AlertCategory.RISK, ["ch2"])
        msg = AlertMessage(
            title="T", content="C",
            level=AlertLevel.WARNING, category=AlertCategory.RISK,
        )
        targets = mgr.get_target_channels(msg)
        assert "ch1" in targets
        assert "ch2" in targets


# ============================================================================
# TestNotificationManager — 消息分发
# ============================================================================


class TestNotificationManagerSend:
    """消息分发"""

    def test_send_success(self) -> None:
        mgr = NotificationManager()
        ch = FakeChannel("ch1")
        mgr.add_channel(ch)
        mgr.set_default_channels(["ch1"])
        msg = AlertMessage(title="T", content="C")
        results = mgr.send_sync(msg)
        assert results["ch1"] is True
        assert len(ch.sent) == 1

    def test_send_failure(self) -> None:
        mgr = NotificationManager()
        ch = FakeChannel("ch1", success=False)
        mgr.add_channel(ch)
        mgr.set_default_channels(["ch1"])
        msg = AlertMessage(title="T", content="C")
        results = mgr.send_sync(msg)
        assert results["ch1"] is False

    def test_send_exception_handled(self) -> None:
        mgr = NotificationManager()
        ch = FailingChannel()
        mgr.add_channel(ch)
        mgr.set_default_channels(["failing"])
        msg = AlertMessage(title="T", content="C")
        results = mgr.send_sync(msg)
        assert results["failing"] is False

    def test_send_missing_channel(self) -> None:
        mgr = NotificationManager()
        mgr.set_default_channels(["nonexistent"])
        msg = AlertMessage(title="T", content="C")
        results = mgr.send_sync(msg)
        assert results["nonexistent"] is False

    def test_send_filtered_message(self) -> None:
        mgr = NotificationManager()
        ch = FakeChannel("ch1", filter_all=True)
        mgr.add_channel(ch)
        mgr.set_default_channels(["ch1"])
        msg = AlertMessage(title="T", content="C")
        results = mgr.send_sync(msg)
        assert results["ch1"] is False
        assert len(ch.sent) == 0

    def test_send_multiple_channels(self) -> None:
        mgr = NotificationManager()
        ch1 = FakeChannel("ch1")
        ch2 = FakeChannel("ch2")
        mgr.add_channel(ch1)
        mgr.add_channel(ch2)
        mgr.set_default_channels(["ch1", "ch2"])
        msg = AlertMessage(title="T", content="C")
        results = mgr.send_sync(msg)
        assert results["ch1"] is True
        assert results["ch2"] is True
        assert len(ch1.sent) == 1
        assert len(ch2.sent) == 1


# ============================================================================
# TestAlertBuilder
# ============================================================================


class TestAlertBuilder:
    """AlertBuilder 便捷 API"""

    def _make_builder(self) -> tuple[AlertBuilder, FakeChannel]:
        mgr = NotificationManager()
        ch = FakeChannel("test")
        mgr.add_channel(ch)
        mgr.set_default_channels(["test"])
        return AlertBuilder(mgr), ch

    def test_info(self) -> None:
        b, ch = self._make_builder()
        b.info("Info Title", "Info Content")
        assert len(ch.sent) == 1
        assert ch.sent[0].level == AlertLevel.INFO

    def test_warning(self) -> None:
        b, ch = self._make_builder()
        b.warning("Warn", "Content")
        assert ch.sent[0].level == AlertLevel.WARNING

    def test_error(self) -> None:
        b, ch = self._make_builder()
        b.error("Err", "Content")
        assert ch.sent[0].level == AlertLevel.ERROR

    def test_critical(self) -> None:
        b, ch = self._make_builder()
        b.critical("Crit", "Content")
        assert ch.sent[0].level == AlertLevel.CRITICAL

    def test_trade_alert(self) -> None:
        b, ch = self._make_builder()
        b.trade_alert("Trade", "Trade content")
        assert ch.sent[0].category == AlertCategory.TRADE
        assert ch.sent[0].level == AlertLevel.WARNING

    def test_risk_alert(self) -> None:
        b, ch = self._make_builder()
        b.risk_alert("Risk", "Risk content")
        assert ch.sent[0].category == AlertCategory.RISK
        assert ch.sent[0].level == AlertLevel.ERROR


# ============================================================================
# TestNotificationChannel
# ============================================================================


class TestNotificationChannel:
    """NotificationChannel 基类"""

    def test_filter_message_default_true(self) -> None:
        ch = FakeChannel()
        msg = AlertMessage(title="T", content="C")
        assert ch.filter_message(msg) is True
