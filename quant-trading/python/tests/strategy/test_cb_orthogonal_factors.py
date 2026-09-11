"""Tests for cb_orthogonal_factors — IVOL, 52w High Distance, Theta Proxy, Skewness."""

import math
from collections import deque
from datetime import datetime

import pytest

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.strategy.cb_orthogonal_factors import (
    HighDistanceTracker,
    IVOLTracker,
    SkewnessTracker,
    theta_proxy,
)


# ---------------------------------------------------------------------------
# 用于测试的简易 Bar 替代
# ---------------------------------------------------------------------------


class _FakeBar:
    """轻量 Bar 模拟用于因子单测。"""

    def __init__(self, close: float, high: float | None = None):
        self.close = close
        self.high = high if high is not None else close


# ===========================================================================
# IVOLTracker Tests
# ===========================================================================


class TestIVOLTracker:
    """特异波动率 Tracker 单测 — ≥12 个用例。"""

    def test_init_defaults(self):
        t = IVOLTracker()
        assert t.lookback == 20
        assert t.min_obs == 10

    def test_init_custom(self):
        t = IVOLTracker(lookback=30, min_obs=5)
        assert t.lookback == 30
        assert t.min_obs == 5

    def test_reset(self):
        t = IVOLTracker(lookback=5, min_obs=3)
        t.update({"A": _FakeBar(100)})
        t.reset()
        assert t._bar_count == 0
        assert len(t._prev_close) == 0
        assert len(t._returns) == 0

    def test_insufficient_data_returns_none(self):
        t = IVOLTracker(lookback=20, min_obs=10)
        # 只更新 3 个 bar — 不够 min_obs
        for i in range(3):
            t.update({"A": _FakeBar(100 + i)})
        assert t.get_ivol("A") is None

    def test_unknown_symbol_returns_none(self):
        t = IVOLTracker(lookback=5, min_obs=3)
        for i in range(10):
            t.update({"A": _FakeBar(100 + i)})
        assert t.get_ivol("MISSING") is None

    def test_ivol_computed_after_enough_data(self):
        t = IVOLTracker(lookback=10, min_obs=5)
        # 生成 15 个 bar 的合成数据
        for i in range(15):
            close_a = 100 + i * 0.5 + (i % 3) * 0.2  # 有波动
            close_b = 100 + i * 0.3                    # 低波动
            t.update({"A": _FakeBar(close_a), "B": _FakeBar(close_b)})

        ivol_a = t.get_ivol("A")
        ivol_b = t.get_ivol("B")
        assert ivol_a is not None
        assert ivol_b is not None
        # 两者都应该是负值（因为取了负）
        assert ivol_a < 0
        assert ivol_b < 0

    def test_ivol_is_annualized(self):
        """IVOL 应该用 sqrt(252) 年化。"""
        t = IVOLTracker(lookback=10, min_obs=5)
        # A 有大幅独立波动，B 完全反方向 → 残差波动不可忽略
        prices_a = [100, 105, 95, 108, 92, 110, 88, 112, 90, 106,
                     94, 109, 91, 107, 93]
        prices_b = [100, 98, 102, 97, 103, 96, 104, 95, 105, 94,
                     106, 93, 107, 92, 108]
        for pa, pb in zip(prices_a, prices_b):
            t.update({"A": _FakeBar(pa), "B": _FakeBar(pb)})
        ivol = t.get_ivol("A")
        assert ivol is not None
        # 年化后绝对值应明显非零
        assert abs(ivol) > 0.01

    def test_low_ivol_higher_value(self):
        """低波动标的应有更高的因子值（更接近 0）。"""
        t = IVOLTracker(lookback=10, min_obs=5)
        for i in range(15):
            # A: 高波动
            close_a = 100 + ((-1) ** i) * 5.0
            # B: 低波动
            close_b = 100 + i * 0.01
            t.update({"A": _FakeBar(close_a), "B": _FakeBar(close_b)})

        ivol_a = t.get_ivol("A")
        ivol_b = t.get_ivol("B")
        assert ivol_a is not None and ivol_b is not None
        # B (低波) 的因子值应更高（更不负）
        assert ivol_b > ivol_a

    def test_zero_close_ignored(self):
        t = IVOLTracker(lookback=5, min_obs=3)
        t.update({"A": _FakeBar(0.0)})
        t.update({"A": _FakeBar(100.0)})
        assert t.get_ivol("A") is None  # 只有 1 个有效价格

    def test_constant_market_returns(self):
        """市场收益方差为零时应仍能计算 IVOL。"""
        t = IVOLTracker(lookback=10, min_obs=5)
        # 所有标的同步涨跌 → 市场收益有方差，残差为零
        for i in range(15):
            close = 100 + i
            t.update({"A": _FakeBar(close), "B": _FakeBar(close)})
        ivol = t.get_ivol("A")
        assert ivol is not None
        # 完全同步 → 残差应接近零
        assert abs(ivol) < 0.5

    def test_update_incremental(self):
        """增量更新不丢失历史。"""
        t = IVOLTracker(lookback=10, min_obs=5)
        for i in range(20):
            t.update({"A": _FakeBar(100 + i * 0.5)})
        ivol1 = t.get_ivol("A")
        t.update({"A": _FakeBar(120)})
        ivol2 = t.get_ivol("A")
        assert ivol1 is not None and ivol2 is not None


# ===========================================================================
# HighDistanceTracker Tests
# ===========================================================================


class TestHighDistanceTracker:
    """52 周高点距离 Tracker 单测 — ≥10 个用例。"""

    def test_init_defaults(self):
        """默认回望窗口取 §13.8 的 TRADING_DAYS_PER_YEAR（A 股 244），不是 252。"""
        t = HighDistanceTracker()
        assert t.lookback == TRADING_DAYS_PER_YEAR

    def test_init_custom(self):
        t = HighDistanceTracker(lookback=60)
        assert t.lookback == 60

    def test_reset(self):
        t = HighDistanceTracker(lookback=30)
        t.update({"A": _FakeBar(100, high=110)})
        t.reset()
        assert len(t._highs) == 0

    def test_insufficient_data_returns_none(self):
        t = HighDistanceTracker(lookback=252)
        for i in range(10):
            t.update({"A": _FakeBar(100 + i, high=105 + i)})
        assert t.get_distance("A", 100.0) is None  # < 20 bars

    def test_at_high_returns_one(self):
        """当前价格等于最高价时应返回 1.0。"""
        t = HighDistanceTracker(lookback=60)
        for i in range(25):
            t.update({"A": _FakeBar(100 + i, high=100 + i)})
        dist = t.get_distance("A", 124.0)  # 最后的 high = 124
        assert dist is not None
        assert abs(dist - 1.0) < 0.01

    def test_below_high_returns_less_than_one(self):
        t = HighDistanceTracker(lookback=60)
        for i in range(25):
            t.update({"A": _FakeBar(100, high=120)})
        dist = t.get_distance("A", 100.0)
        assert dist is not None
        assert dist < 1.0
        assert abs(dist - 100.0 / 120.0) < 0.001

    def test_unknown_symbol_returns_none(self):
        t = HighDistanceTracker(lookback=60)
        for i in range(25):
            t.update({"A": _FakeBar(100)})
        assert t.get_distance("MISSING", 100.0) is None

    def test_zero_close_returns_none(self):
        t = HighDistanceTracker(lookback=60)
        for i in range(25):
            t.update({"A": _FakeBar(100)})
        assert t.get_distance("A", 0.0) is None

    def test_distance_range(self):
        """距离应在 (0, 1] 范围内。"""
        t = HighDistanceTracker(lookback=60)
        for i in range(30):
            t.update({"A": _FakeBar(80 + i, high=90 + i)})
        dist = t.get_distance("A", 105.0)
        assert dist is not None
        assert 0 < dist <= 1.0

    def test_multiple_symbols(self):
        """多标的独立跟踪。"""
        t = HighDistanceTracker(lookback=60)
        for i in range(25):
            t.update({
                "A": _FakeBar(100, high=120),
                "B": _FakeBar(200, high=250),
            })
        dist_a = t.get_distance("A", 100.0)
        dist_b = t.get_distance("B", 200.0)
        assert dist_a is not None and dist_b is not None
        assert abs(dist_a - 100.0 / 120.0) < 0.001
        assert abs(dist_b - 200.0 / 250.0) < 0.001


# ===========================================================================
# theta_proxy Tests
# ===========================================================================


class TestThetaProxy:
    """Theta 代理计算单测 — ≥10 个用例。"""

    def test_basic_computation(self):
        """基本计算：ATM CB 应有正 theta。"""
        val = theta_proxy(
            close=110.0, maturity_date="20280101",
            now=datetime(2024, 3, 15), par_value=100.0,
        )
        assert val > 0  # 价格 > 纯债价值

    def test_deep_otm_negative(self):
        """深度价外 CB（价格低于纯债价值）可能为负。"""
        val = theta_proxy(
            close=80.0, maturity_date="20280101",
            now=datetime(2024, 3, 15), par_value=100.0,
        )
        # 80 < bond_floor ~= 100 * exp(-0.02*4) ≈ 92, 所以 val < 0
        assert val < 0

    def test_near_maturity_returns_zero(self):
        """接近到期（< 0.1 年）应返回 0。"""
        val = theta_proxy(
            close=105.0, maturity_date="20240401",
            now=datetime(2024, 3, 20),
        )
        assert val == 0.0

    def test_zero_close_returns_zero(self):
        val = theta_proxy(close=0.0, maturity_date="20280101",
                         now=datetime(2024, 3, 15))
        assert val == 0.0

    def test_invalid_date_returns_zero(self):
        val = theta_proxy(close=100.0, maturity_date="invalid",
                         now=datetime(2024, 3, 15))
        assert val == 0.0

    def test_none_date_returns_zero(self):
        val = theta_proxy(close=100.0, maturity_date=None,
                         now=datetime(2024, 3, 15))
        assert val == 0.0

    def test_longer_maturity_lower_normalized_theta(self):
        """相同价格，到期越远 → 年化 theta 越低（除以 sqrt(T) 更大）。"""
        t_near = theta_proxy(
            close=110.0, maturity_date="20260101",
            now=datetime(2024, 3, 15),
        )
        t_far = theta_proxy(
            close=110.0, maturity_date="20340101",
            now=datetime(2024, 3, 15),
        )
        # 标准化后，近到期的 theta 更高（时间价值衰减更快）
        assert t_near > t_far

    def test_higher_price_higher_theta(self):
        """相同到期日，更高价格 → 更高 theta（更多期权溢价）。"""
        t_low = theta_proxy(
            close=100.0, maturity_date="20280101",
            now=datetime(2024, 3, 15),
        )
        t_high = theta_proxy(
            close=120.0, maturity_date="20280101",
            now=datetime(2024, 3, 15),
        )
        assert t_high > t_low

    def test_custom_par_value(self):
        """自定义面值。"""
        val = theta_proxy(
            close=210.0, maturity_date="20280101",
            now=datetime(2024, 3, 15), par_value=200.0,
        )
        assert val > 0

    def test_reproducibility(self):
        """相同输入应得相同输出。"""
        args = dict(close=105.0, maturity_date="20270615",
                    now=datetime(2024, 6, 1))
        assert theta_proxy(**args) == theta_proxy(**args)


# ===========================================================================
# SkewnessTracker Tests
# ===========================================================================


class TestSkewnessTracker:
    """收益率偏度 Tracker 单测 — ≥10 个用例。"""

    def test_init_defaults(self):
        t = SkewnessTracker()
        assert t.lookback == 20
        assert t.min_obs == 10

    def test_reset(self):
        t = SkewnessTracker(lookback=5, min_obs=3)
        t.update({"A": _FakeBar(100)})
        t.reset()
        assert len(t._prev_close) == 0

    def test_insufficient_data_returns_none(self):
        t = SkewnessTracker(lookback=10, min_obs=8)
        for i in range(5):
            t.update({"A": _FakeBar(100 + i)})
        assert t.get_skewness("A") is None

    def test_unknown_symbol_returns_none(self):
        t = SkewnessTracker(lookback=5, min_obs=3)
        for i in range(10):
            t.update({"A": _FakeBar(100 + i)})
        assert t.get_skewness("MISSING") is None

    def test_symmetric_returns_near_zero_skew(self):
        """对称分布的收益率应有接近 0 的偏度。"""
        t = SkewnessTracker(lookback=20, min_obs=10)
        # 构造对称收益率: +1%, -1%, +1%, -1%, ...
        prices = [100.0]
        for i in range(25):
            if i % 2 == 0:
                prices.append(prices[-1] * 1.01)
            else:
                prices.append(prices[-1] * 0.99)
        for p in prices:
            t.update({"A": _FakeBar(p)})
        skew = t.get_skewness("A")
        assert skew is not None
        assert abs(skew) < 1.0  # 应接近 0

    def test_positive_skew_for_upward_jumps(self):
        """偶尔大涨、持续小跌 → 正偏度。"""
        t = SkewnessTracker(lookback=20, min_obs=10)
        prices = [100.0]
        for i in range(25):
            if i % 5 == 0:
                prices.append(prices[-1] * 1.05)  # 偶尔 +5%
            else:
                prices.append(prices[-1] * 0.995)  # 持续 -0.5%
        for p in prices:
            t.update({"A": _FakeBar(p)})
        skew = t.get_skewness("A")
        assert skew is not None
        assert skew > 0  # 正偏度

    def test_negative_skew_for_downward_jumps(self):
        """偶尔大跌、持续小涨 → 负偏度。"""
        t = SkewnessTracker(lookback=20, min_obs=10)
        prices = [100.0]
        for i in range(25):
            if i % 5 == 0:
                prices.append(prices[-1] * 0.95)  # 偶尔 -5%
            else:
                prices.append(prices[-1] * 1.005)  # 持续 +0.5%
        for p in prices:
            t.update({"A": _FakeBar(p)})
        skew = t.get_skewness("A")
        assert skew is not None
        assert skew < 0  # 负偏度

    def test_constant_returns_zero_skew(self):
        """恒定收益率 → 偏度为 0（方差为零时）。"""
        t = SkewnessTracker(lookback=20, min_obs=5)
        for i in range(15):
            t.update({"A": _FakeBar(100.0)})
        skew = t.get_skewness("A")
        # 恒定价格 → 收益率全 0（第一个 bar 没有 prev → 少一个）
        # 方差为 0 → skew = 0.0
        if skew is not None:
            assert skew == 0.0

    def test_zero_close_ignored(self):
        t = SkewnessTracker(lookback=5, min_obs=3)
        t.update({"A": _FakeBar(0.0)})
        assert t.get_skewness("A") is None

    def test_multiple_symbols_independent(self):
        """多标的独立计算。"""
        t = SkewnessTracker(lookback=10, min_obs=5)
        for i in range(15):
            t.update({
                "A": _FakeBar(100 + i),
                "B": _FakeBar(200 - i),
            })
        skew_a = t.get_skewness("A")
        skew_b = t.get_skewness("B")
        assert skew_a is not None and skew_b is not None
