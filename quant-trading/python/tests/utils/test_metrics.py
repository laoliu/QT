"""§13.2 metrics.py 单元测试

验证 Sharpe/Sortino/MaxDrawdown/Calmar/AnnualizedReturn 的精确数值正确性。
确保作为唯一权威实现的 metrics.py 输出可信。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from aipha.utils.metrics import (
    DEFAULT_TRADING_DAYS,
    compute_annualized_return,
    compute_calmar_ratio,
    compute_max_drawdown,
    compute_max_drawdown_from_returns,
    compute_sharpe_ratio,
    compute_sortino_ratio,
)


class TestSharpeRatio:
    """Sharpe Ratio 精确值验证"""

    def test_known_value(self):
        """固定已知输入验证公式正确性"""
        # 每天 +0.1% 的恒定收益：std=0，按约定返回 0（不是 inf）
        rets = np.full(DEFAULT_TRADING_DAYS, 0.001)
        assert compute_sharpe_ratio(rets) == 0.0

        # 有波动的序列才能手算验证公式
        rets2 = np.array([0.01, -0.005, 0.008, -0.003, 0.006] * 50)
        r = compute_sharpe_ratio(rets2, risk_free_rate=0.0)
        mean_r = float(np.mean(rets2))
        std_v = float(np.std(rets2, ddof=1))
        # §13.8：年化因子取 DEFAULT_TRADING_DAYS（=244），不写死 252
        expected2 = (mean_r / std_v) * math.sqrt(DEFAULT_TRADING_DAYS)
        assert abs(r - expected2) < 1e-10

    def test_rf_deduction(self):
        """验证无风险利率正确扣除"""
        rets = np.array([0.01, -0.005, 0.008, -0.003, 0.006] * 50)
        sr_no_rf = compute_sharpe_ratio(rets, risk_free_rate=0.0)
        sr_with_rf = compute_sharpe_ratio(rets, risk_free_rate=0.05)
        assert sr_no_rf > sr_with_rf, "更高 rf 应降低 Sharpe"

    def test_empty_returns_zero(self):
        assert compute_sharpe_ratio([]) == 0.0
        assert compute_sharpe_ratio([0.01]) == 0.0

    def test_constant_returns_zero(self):
        """全零波动应返回 0"""
        assert compute_sharpe_ratio(np.full(100, 0.001)) == 0.0

    def test_negative_returns(self):
        """全负收益应为负 Sharpe"""
        rets = np.array([-0.01, -0.02, -0.005, -0.015, -0.008] * 50)
        assert compute_sharpe_ratio(rets) < 0


class TestSortinoRatio:
    """Sortino Ratio 精确值验证"""

    def test_all_positive_returns(self):
        """全正收益，下行偏差极小 → Sortino 应极大或 0"""
        rets = np.array([0.01, 0.02, 0.015, 0.008, 0.012] * 50)
        sr = compute_sortino_ratio(rets, risk_free_rate=0.0)
        # 全正收益下行偏差=0 → returns 0.0
        assert sr == 0.0 or sr > 5.0

    def test_greater_than_sharpe(self):
        """存在正偏收益时 Sortino 通常 > Sharpe"""
        rets = np.array([0.02, 0.01, -0.005, 0.015, 0.008] * 50)
        sharpe = compute_sharpe_ratio(rets, risk_free_rate=0.0)
        sortino = compute_sortino_ratio(rets, risk_free_rate=0.0)
        assert sortino > sharpe

    def test_empty(self):
        assert compute_sortino_ratio([]) == 0.0


class TestMaxDrawdown:
    """MaxDrawdown 精确值验证"""

    def test_known_drawdown(self):
        """100 → 120 → 90 → 110: MDD = (120-90)/120 = 25%"""
        nav = [100, 110, 120, 100, 90, 95, 110]
        mdd, peak, trough = compute_max_drawdown(nav)
        assert abs(mdd - 0.25) < 1e-10
        assert peak == 2  # index of 120
        assert trough == 4  # index of 90

    def test_monotonic_increase(self):
        """单调递增无回撤"""
        nav = list(range(100, 200))
        mdd, _, _ = compute_max_drawdown(nav)
        assert mdd == 0.0

    def test_from_returns(self):
        """从收益率计算 MDD"""
        rets = [0.1, 0.1, -0.25, -0.1, 0.05]
        mdd = compute_max_drawdown_from_returns(rets)
        assert mdd > 0.30  # 大幅下跌

    def test_empty(self):
        assert compute_max_drawdown([]) == (0.0, 0, 0)


class TestAnnualizedReturn:
    """年化收益精确值验证"""

    def test_one_year_double(self):
        """1 年翻倍 → AR = 100%"""
        nav = np.linspace(100, 200, 253)  # 252 trading days
        ar = compute_annualized_return(nav, trading_days=252)
        assert abs(ar - 1.0) < 0.01

    def test_empty(self):
        assert compute_annualized_return([]) == 0.0


class TestCalmarRatio:
    """Calmar Ratio 验证"""

    def test_basic(self):
        assert compute_calmar_ratio(0.20, 0.10) == pytest.approx(2.0)

    def test_zero_drawdown(self):
        assert compute_calmar_ratio(0.20, 0.0) == float("inf")

    def test_negative_return(self):
        assert compute_calmar_ratio(-0.10, 0.20) == pytest.approx(-0.5)
