"""daily_utils._check_mdd — paper/live 唯一的回撤保护入口.

这条路径此前没有任何测试，但它是 paper/live 上**实际生效**的回撤保护：
`RiskConfig.max_drawdown`（渐进降杠杆）默认 1.0 = 关闭，且按 r115 评审
结论就不该在这条路上开；真正一直在跑的是 MddAlertChecker 的
15% 告警 / 30% 熔断（熔断会让当日不再调仓）。

这两个阈值在现有 36 份 paper 配置里一份都没写过，全靠 `_check_mdd` 里的
`cfg.get(..., 0.15/0.30)` 兜底 —— 所以默认值本身就是生产契约，
必须有测试钉住，改动默认值不能是一次静默的编辑。
"""
from __future__ import annotations

import logging

import pytest

from aipha.runner.daily_utils import ComboState, _check_mdd


def _state(peak_nav: float, *, profile: str = "test_profile") -> ComboState:
    return ComboState(
        profile_name=profile,
        initial_capital=1_000_000.0,
        cash=0.0,
        peak_nav=peak_nav,
    )


def _log_text(caplog) -> str:
    """拼接实际生成的日志文本。

    用 ``getMessage()`` 而不是 ``record.message % record.args``：
    文案里有 ``15%`` 这类字面百分号，二次格式化会 ValueError。
    """
    return "\n".join(r.getMessage() for r in caplog.records)


class TestCheckMddThresholds:
    """默认阈值即生产契约：15% 告警 / 30% 熔断。"""

    @pytest.mark.parametrize(
        ("nav", "expected"),
        [
            (100.0, "green"),   # 无回撤
            (86.0, "green"),    # -14%，未到告警线
            (85.0, "alert"),    # -15%，告警线取等号
            (75.0, "alert"),    # -25%
            (70.0, "halt"),     # -30%，熔断线取等号
            (50.0, "halt"),     # -50%
        ],
    )
    def test_default_thresholds(self, nav, expected):
        assert _check_mdd(_state(100.0), nav, {}) == expected

    def test_config_overrides_defaults(self):
        cfg = {"mdd_alert_threshold": 0.05, "mdd_halt_threshold": 0.10}
        # -6%：默认下是 green，收紧后是 alert
        assert _check_mdd(_state(100.0), 94.0, {}) == "green"
        assert _check_mdd(_state(100.0), 94.0, cfg) == "alert"

    def test_zero_peak_is_green(self):
        """peak_nav 尚未初始化时不判定 —— 不能把 0 当成 -100% 回撤。"""
        assert _check_mdd(_state(0.0), 1_000_000.0, {}) == "green"


class TestCheckMddObservability:
    """绿灯也要留痕：运维从配置文件看不出在用什么阈值。"""

    def test_logs_effective_thresholds_when_green(self, caplog):
        with caplog.at_level(logging.INFO, logger="daily_utils"):
            _check_mdd(_state(100.0, profile="combo_a"), 98.0, {})
        text = _log_text(caplog)
        assert "combo_a" in text
        assert "15" in text and "30" in text     # 兜底阈值可见
        assert "green" in text

    def test_logs_custom_thresholds(self, caplog):
        cfg = {"mdd_alert_threshold": 0.08, "mdd_halt_threshold": 0.20}
        with caplog.at_level(logging.INFO, logger="daily_utils"):
            _check_mdd(_state(100.0), 99.0, cfg)
        text = _log_text(caplog)
        assert "8" in text and "20" in text

    def test_alert_still_warns(self, caplog):
        """INFO 那行是新增的，原有的 WARNING/ERROR 告警不能被它顶掉。"""
        with caplog.at_level(logging.INFO, logger="daily_utils"):
            assert _check_mdd(_state(100.0), 80.0, {}) == "alert"
        levels = {r.levelno for r in caplog.records}
        assert logging.WARNING in levels

    def test_halt_still_errors(self, caplog):
        with caplog.at_level(logging.INFO, logger="daily_utils"):
            assert _check_mdd(_state(100.0), 60.0, {}) == "halt"
        levels = {r.levelno for r in caplog.records}
        assert logging.ERROR in levels
