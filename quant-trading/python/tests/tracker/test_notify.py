"""Tests for tracker.notify.wxpusher — WxPusher WeChat notification."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
import json

import pytest

from aipha.tracker.models import (
    ChangeType,
    Confidence,
    HoldingChange,
    PlanStatus,
    PortfolioDiff,
    TradePlan,
    TradeDirection,
)
from aipha.tracker.notify.wxpusher import (
    WxPusherNotifier,
    _diff_to_html,
    _diff_one_liner,
    _plan_to_html,
)


# =========================================================================
# WxPusherNotifier — initialization
# =========================================================================


class TestWxPusherInit:

    def test_valid_init(self):
        n = WxPusherNotifier(app_token="AT_test", uids=["UID_1"])
        assert n._enabled is True

    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="app_token"):
            WxPusherNotifier(app_token="", uids=["UID_1"])

    def test_empty_uids_raises(self):
        with pytest.raises(ValueError, match="uids"):
            WxPusherNotifier(app_token="AT_test", uids=[])

    def test_disabled(self):
        n = WxPusherNotifier(app_token="AT_test", uids=["UID_1"], enabled=False)
        assert n._enabled is False


# =========================================================================
# WxPusherNotifier — send
# =========================================================================


class TestWxPusherSend:

    @pytest.fixture
    def notifier(self):
        return WxPusherNotifier(app_token="AT_test123", uids=["UID_abc"])

    def test_send_disabled_returns_true(self):
        n = WxPusherNotifier(app_token="AT_test", uids=["UID_1"], enabled=False)
        assert n.send("hello") is True

    @patch("aipha.tracker.notify.wxpusher.urlopen")
    def test_send_success(self, mock_urlopen, notifier):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"code": 1000, "msg": "success"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = notifier.send("test content", summary="test")
        assert result is True
        mock_urlopen.assert_called_once()

    @patch("aipha.tracker.notify.wxpusher.urlopen")
    def test_send_api_error(self, mock_urlopen, notifier):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"code": 2000, "msg": "error"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = notifier.send("test content")
        assert result is False

    @patch("aipha.tracker.notify.wxpusher.urlopen")
    def test_send_network_error(self, mock_urlopen, notifier):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("network error")

        result = notifier.send("test content")
        assert result is False

    @patch("aipha.tracker.notify.wxpusher.urlopen")
    def test_send_truncates_long_summary(self, mock_urlopen, notifier):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"code": 1000}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        long_summary = "x" * 150
        notifier.send("content", summary=long_summary)

        # Verify the request was made with truncated summary
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode())
        assert len(body["summary"]) <= 100


# =========================================================================
# WxPusherNotifier — convenience methods
# =========================================================================


class TestWxPusherConvenience:

    @pytest.fixture
    def notifier(self):
        n = WxPusherNotifier(app_token="AT_test", uids=["UID_1"])
        n.send = MagicMock(return_value=True)
        return n

    def _make_diff(self, has_changes=True):
        changes = ()
        if has_changes:
            changes = (
                HoldingChange("A.SH", "A股", ChangeType.ADDED, new_weight=Decimal("0.2")),
                HoldingChange("B.SH", "B股", ChangeType.REMOVED, old_weight=Decimal("0.1")),
            )
        return PortfolioDiff(
            author_id="test", author_name="测试大V",
            from_snapshot_id="s1", to_snapshot_id="s2",
            from_time=datetime(2026, 2, 10), to_time=datetime(2026, 2, 15),
            changes=changes,
        )

    def _make_plan(self):
        return TradePlan(
            plan_id="TP-test001",
            symbol="600519.SH", name="贵州茅台",
            direction=TradeDirection.BUY,
            reasons=["估值合理", "行业龙头", "现金流优秀"],
            stop_loss_price=1500.0,
            stop_loss_pct=0.15,
            target_position_pct=0.10,
            confidence=Confidence.HIGH,
        )

    def test_notify_diff_with_changes(self, notifier):
        diff = self._make_diff(has_changes=True)
        result = notifier.notify_diff(diff)
        assert result is True
        notifier.send.assert_called_once()
        call_kwargs = notifier.send.call_args
        assert "持仓变化" in call_kwargs.kwargs.get("summary", call_kwargs[1].get("summary", ""))

    def test_notify_diff_no_changes(self, notifier):
        diff = self._make_diff(has_changes=False)
        result = notifier.notify_diff(diff)
        assert result is True
        notifier.send.assert_not_called()

    def test_notify_plan_reminder(self, notifier):
        plan = self._make_plan()
        result = notifier.notify_plan_reminder(plan)
        assert result is True
        notifier.send.assert_called_once()

    def test_notify_text(self, notifier):
        result = notifier.notify_text("标题", "正文")
        assert result is True
        notifier.send.assert_called_once()


# =========================================================================
# HTML formatters
# =========================================================================


class TestHTMLFormatters:

    def test_diff_to_html_with_all_changes(self):
        diff = PortfolioDiff(
            author_id="a", author_name="大V",
            from_snapshot_id="s1", to_snapshot_id="s2",
            from_time=datetime(2026, 2, 10), to_time=datetime(2026, 2, 15),
            changes=(
                HoldingChange("A.SH", "新增股", ChangeType.ADDED, new_weight=Decimal("0.2"), weight_delta=Decimal("0.2")),
                HoldingChange("B.SH", "清仓股", ChangeType.REMOVED, old_weight=Decimal("0.1"), weight_delta=Decimal("-0.1")),
                HoldingChange("C.SH", "加仓股", ChangeType.INCREASED, old_weight=Decimal("0.1"), new_weight=Decimal("0.3"), weight_delta=Decimal("0.2")),
                HoldingChange("D.SH", "减仓股", ChangeType.DECREASED, old_weight=Decimal("0.3"), new_weight=Decimal("0.1"), weight_delta=Decimal("-0.2")),
            ),
        )
        html = _diff_to_html(diff)
        assert "大V" in html
        assert "新增" in html
        assert "清仓" in html
        assert "加仓" in html
        assert "减仓" in html
        assert "A.SH" in html
        assert "Aipha-Q" in html

    def test_diff_one_liner(self):
        diff = PortfolioDiff(
            author_id="a", author_name="X",
            from_snapshot_id="s1", to_snapshot_id="s2",
            from_time=datetime.now(), to_time=datetime.now(),
            changes=(
                HoldingChange("A.SH", "A", ChangeType.ADDED, new_weight=Decimal("0.1")),
                HoldingChange("B.SH", "B", ChangeType.REMOVED, old_weight=Decimal("0.1")),
            ),
        )
        liner = _diff_one_liner(diff)
        assert "+1" in liner
        assert "-1" in liner

    def test_diff_one_liner_no_changes(self):
        diff = PortfolioDiff(
            author_id="a", author_name="X",
            from_snapshot_id="s1", to_snapshot_id="s2",
            from_time=datetime.now(), to_time=datetime.now(),
            changes=(),
        )
        assert "无变化" in _diff_one_liner(diff)

    def test_plan_to_html_buy(self):
        plan = TradePlan(
            plan_id="TP-test", symbol="600519.SH", name="贵州茅台",
            direction=TradeDirection.BUY,
            reasons=["R1", "R2", "R3"],
            stop_loss_price=1500, stop_loss_pct=0.15,
            target_position_pct=0.10, confidence=Confidence.HIGH,
            vip_references=["学知利行"],
        )
        html = _plan_to_html(plan)
        assert "买入" in html
        assert "贵州茅台" in html
        assert "600519.SH" in html
        assert "1500" in html
        assert "学知利行" in html
        assert "R1" in html

    def test_plan_to_html_sell(self):
        plan = TradePlan(
            plan_id="TP-test", symbol="000001.SZ", name="平安银行",
            direction=TradeDirection.SELL,
            reasons=["估值偏高", "业绩下滑", "行业景气度下降"],
            stop_loss_price=10, stop_loss_pct=0.10,
            target_position_pct=0.0, confidence=Confidence.LOW,
        )
        html = _plan_to_html(plan)
        assert "卖出" in html
        assert "平安银行" in html
