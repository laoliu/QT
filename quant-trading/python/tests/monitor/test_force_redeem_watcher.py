"""tests/monitor/test_force_redeem_watcher.py

ForceRedeemWatcher 单元测试（≥8 条）。

使用真实数据结构实例，不 Mock 核心业务对象。
对 TushareCbProvider 使用 Provider 接口隔离（stub 类）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pandas as pd
import pytest

from aipha.monitor.alerts import Alert, AlertLevel, AlertStatus
from aipha.monitor.force_redeem_watcher import (
    CONDITION_MET_KEYWORDS,
    IMPLEMENTED_KEYWORDS,
    NO_REDEEM_KEYWORDS,
    ForceRedeemWatcher,
    RedeemEvent,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _make_df(rows: list[dict]) -> pd.DataFrame:
    """构造 load_cb_call 返回格式的 DataFrame。"""
    return pd.DataFrame(rows)


def _make_provider(rows: list[dict]):
    """构造 Provider Stub，load_cb_call 返回指定 DataFrame。"""
    stub = MagicMock()
    stub.load_cb_call.return_value = _make_df(rows)
    return stub


def _capture_notifier():
    """构造一个捕获 send 调用的通知器 Stub。"""
    notifier = MagicMock()
    notifier.send = MagicMock()
    return notifier


_TODAY_STR = pd.Timestamp.today().strftime("%Y%m%d")
_YESTERDAY_STR = (pd.Timestamp.today() - pd.Timedelta(days=1)).strftime("%Y%m%d")


# ── 测试 RedeemEvent ──────────────────────────────────────────────────────────

class TestRedeemEvent:
    def test_dedup_key(self):
        ev = RedeemEvent(
            ts_code="110001.SH", call_type="强赎", is_call="公告实施强赎",
            ann_date="20240101", call_date="20240115",
        )
        assert ev.dedup_key == ("110001.SH", "20240101", "公告实施强赎")

    def test_is_implemented_true(self):
        ev = RedeemEvent(
            ts_code="X", call_type="强赎", is_call="公告实施强赎",
            ann_date="20240101", call_date="",
        )
        assert ev.is_implemented is True
        assert ev.is_condition_met is False

    def test_is_condition_met_true(self):
        ev = RedeemEvent(
            ts_code="X", call_type="强赎", is_call="已满足强赎条件",
            ann_date="20240101", call_date="",
        )
        assert ev.is_condition_met is True
        assert ev.is_implemented is False

    def test_is_no_redeem_true(self):
        ev = RedeemEvent(
            ts_code="X", call_type="强赎", is_call="公告不强赎",
            ann_date="20240101", call_date="",
        )
        assert ev.is_no_redeem is True

    def test_unknown_status_is_false_for_all(self):
        ev = RedeemEvent(
            ts_code="X", call_type="强赎", is_call="其他描述",
            ann_date="20240101", call_date="",
        )
        assert not ev.is_implemented
        assert not ev.is_condition_met
        assert not ev.is_no_redeem


# ── 测试 ForceRedeemWatcher.check_once ───────────────────────────────────────

class TestCheckOnce:
    def test_no_data_returns_empty(self, tmp_path):
        provider = _make_provider([])
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path)
        result = w.check_once()
        assert result == []
        notifier.send.assert_not_called()

    def test_new_event_fires_alert_and_returns_event(self, tmp_path):
        provider = _make_provider([{
            "ts_code": "110001.SH",
            "call_type": "强赎",
            "is_call": "公告实施强赎",
            "ann_date": _TODAY_STR,
            "call_date": "",
            "bond_short_name": "测债01",
        }])
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path)
        result = w.check_once()

        assert len(result) == 1
        assert result[0].ts_code == "110001.SH"
        notifier.send.assert_called_once()
        alert: Alert = notifier.send.call_args[0][0]
        assert alert.level == AlertLevel.CRITICAL

    def test_duplicate_event_not_fired_twice(self, tmp_path):
        rows = [{
            "ts_code": "110001.SH",
            "call_type": "强赎",
            "is_call": "公告实施强赎",
            "ann_date": _TODAY_STR,
            "call_date": "",
            "bond_short_name": "",
        }]
        provider = _make_provider(rows)
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path)

        w.check_once()
        w.check_once()  # 第二次不应再发 send

        assert notifier.send.call_count == 1

    def test_condition_met_fires_warning(self, tmp_path):
        provider = _make_provider([{
            "ts_code": "110002.SH",
            "call_type": "强赎",
            "is_call": "已满足强赎条件",
            "ann_date": _TODAY_STR,
            "call_date": "",
            "bond_short_name": "",
        }])
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path)
        w.check_once()

        alert: Alert = notifier.send.call_args[0][0]
        assert alert.level == AlertLevel.WARNING

    def test_no_redeem_ignored_by_default(self, tmp_path):
        provider = _make_provider([{
            "ts_code": "110003.SH",
            "call_type": "强赎",
            "is_call": "公告不强赎",
            "ann_date": _TODAY_STR,
            "call_date": "",
            "bond_short_name": "",
        }])
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path,
                               include_no_redeem=False)
        result = w.check_once()

        # 事件被记录在 seen 中（避免重复检查），但不触发通知
        assert len(result) == 1
        notifier.send.assert_not_called()

    def test_no_redeem_fires_info_when_included(self, tmp_path):
        provider = _make_provider([{
            "ts_code": "110004.SH",
            "call_type": "强赎",
            "is_call": "公告不强赎",
            "ann_date": _TODAY_STR,
            "call_date": "",
            "bond_short_name": "",
        }])
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path,
                               include_no_redeem=True)
        w.check_once()

        notifier.send.assert_called_once()
        alert: Alert = notifier.send.call_args[0][0]
        assert alert.level == AlertLevel.INFO

    def test_provider_error_returns_empty(self, tmp_path, caplog):
        provider = MagicMock()
        provider.load_cb_call.side_effect = RuntimeError("API timeout")
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path)

        with caplog.at_level(logging.ERROR):
            result = w.check_once()

        assert result == []
        assert "load_cb_call 失败" in caplog.text

    def test_lookback_filters_old_events(self, tmp_path):
        """lookback_days=1 时，三天前的公告应被过滤掉。"""
        old_date = (pd.Timestamp.today() - pd.Timedelta(days=3)).strftime("%Y%m%d")
        provider = _make_provider([{
            "ts_code": "110005.SH",
            "call_type": "强赎",
            "is_call": "公告实施强赎",
            "ann_date": old_date,
            "call_date": "",
            "bond_short_name": "",
        }])
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier],
                               lookback_days=1, audit_dir=tmp_path)
        result = w.check_once()
        assert result == []
        notifier.send.assert_not_called()

    def test_reset_seen_allows_re_fire(self, tmp_path):
        rows = [{
            "ts_code": "110006.SH",
            "call_type": "强赎",
            "is_call": "公告实施强赎",
            "ann_date": _TODAY_STR,
            "call_date": "",
            "bond_short_name": "",
        }]
        provider = _make_provider(rows)
        notifier = _capture_notifier()
        w = ForceRedeemWatcher(provider, notifiers=[notifier], audit_dir=tmp_path)
        w.check_once()
        w.reset_seen()
        w.check_once()

        assert notifier.send.call_count == 2


# ── 测试 start_polling ────────────────────────────────────────────────────────

class TestStartPolling:
    def test_max_iterations_stops(self, tmp_path):
        provider = _make_provider([])
        w = ForceRedeemWatcher(provider, notifiers=[], audit_dir=tmp_path,
                               poll_interval_sec=0)
        w.start_polling(max_iterations=3)
        # 调用 3 次
        assert provider.load_cb_call.call_count == 3
