"""滑点模型边界条件测试

覆盖 slippage.py 中尚未测试的分支与边界场景。
"""

from datetime import datetime

import math
import pytest

from aipha.domain.models import Bar, Fill, Side
from aipha.execution.slippage import (
    FixedBpsSlippageModel,
    LinearImpactSlippageModel,
    SquareRootImpactSlippageModel,
    _estimate_bar_amount,
)


# ── helpers ──────────────────────────────────────────────────

_T = datetime(2024, 6, 1, 10, 0, 0)


def _fill(side=Side.BUY, qty=1000, price=10.0) -> Fill:
    return Fill(
        order_id="test_001",
        symbol="000001.SZ",
        side=side,
        qty=qty,
        price=price,
        t_fill=_T,
        model="test",
    )


def _bar(
    open_=10.0, high=10.5, low=9.5, close=10.0,
    volume=100000, vwap=None, metadata=None,
) -> Bar:
    return Bar(
        symbol="000001.SZ",
        start_ts=_T,
        end_ts=_T,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        vwap=vwap,
        metadata=metadata or {},
    )


# ══════════════════════════════════════════════════════════════
# 1. FixedBpsSlippageModel 边界
# ══════════════════════════════════════════════════════════════


class TestFixedBpsEdge:
    """FixedBps 极端 bps 值"""

    def test_negative_bps_price_improvement(self):
        """负 bps → 买入价格降低（价格改善）"""
        m = FixedBpsSlippageModel(bps=-5.0)
        f = m.apply(_fill(side=Side.BUY))
        # BUY: 1 + (-5/10000) = 0.9995
        assert f.price < 10.0
        assert f.price == pytest.approx(10.0 * 0.9995)

    def test_negative_bps_sell_price_increase(self):
        """负 bps → 卖出价格上升（价格改善）"""
        m = FixedBpsSlippageModel(bps=-5.0)
        f = m.apply(_fill(side=Side.SELL))
        # SELL: 1 - (-5/10000) = 1.0005
        assert f.price > 10.0

    def test_bps_10000_sell_price_zero(self):
        """bps=10000 → SELL 时 m=0 → 价格=0"""
        m = FixedBpsSlippageModel(bps=10000)
        f = m.apply(_fill(side=Side.SELL))
        assert f.price == pytest.approx(0.0)

    def test_bps_over_10000_sell_price_negative(self):
        """bps=20000 → SELL 时 m=-1 → 价格为负"""
        m = FixedBpsSlippageModel(bps=20000)
        f = m.apply(_fill(side=Side.SELL))
        assert f.price < 0.0


# ══════════════════════════════════════════════════════════════
# 2. LinearImpactSlippageModel 边界
# ══════════════════════════════════════════════════════════════


class TestLinearImpactEdge:
    """LinearImpact 未覆盖的分支"""

    def test_bar_amount_zero_fallback(self):
        """bar.volume > 0 但 close=0 且无 vwap/metadata → bar_amount=0 → fallback"""
        m = LinearImpactSlippageModel(fallback_bps=10.0)
        b = _bar(open_=0.0, high=0.0, low=0.0, close=0.0, volume=100)
        f = m.apply(_fill(), bar=b)
        # fallback: 10 bps
        expected = 10.0 * (1.0 + 10.0 / 10000.0)
        assert f.price == pytest.approx(expected)

    def test_zero_fill_price(self):
        """fill.price=0 → order_notional=0 → participation=0 → 只有 base_bps"""
        m = LinearImpactSlippageModel(base_bps=3.0, impact_coeff=50.0)
        b = _bar(volume=100000, close=10.0)
        f = m.apply(_fill(price=0.0), bar=b)
        # notional=0 → participation=0 → bps=3.0 → price = 0 * m = 0
        assert f.price == pytest.approx(0.0)

    def test_zero_fill_qty(self):
        """fill.qty=0 → order_notional=0 → 只有 base_bps"""
        m = LinearImpactSlippageModel(base_bps=5.0)
        b = _bar(volume=100000, close=10.0)
        f = m.apply(_fill(qty=0), bar=b)
        # bps = 5 + 50*0 = 5, price * (1 + 5/10000)
        assert f.price == pytest.approx(10.0 * (1.0 + 5.0 / 10000.0))

    def test_max_bps_cap(self):
        """超大参与率被 max_bps 截断"""
        m = LinearImpactSlippageModel(
            base_bps=0, impact_coeff=10000, max_bps=50.0,
        )
        b = _bar(volume=100000, close=10.0)
        f = m.apply(_fill(qty=100000, price=10.0), bar=b)
        # participation = 1e6 / (1e5 * 10) = 1.0 → bps=10000 → capped to 50
        expected = 10.0 * (1.0 + 50.0 / 10000.0)
        assert f.price == pytest.approx(expected)


# ══════════════════════════════════════════════════════════════
# 3. SquareRootImpactSlippageModel 边界
# ══════════════════════════════════════════════════════════════


class TestSquareRootImpactEdge:
    """SquareRootImpact 未覆盖的分支"""

    def test_intraday_range_zero(self):
        """high==low → intraday_range==0 → sigma 保持 sigma_daily"""
        m = SquareRootImpactSlippageModel(
            sigma_daily=0.02, impact_coeff=0.5, use_bar_volatility=True,
        )
        # high == low == close → range=0
        b = _bar(open_=10.0, high=10.0, low=10.0, close=10.0, volume=100000)
        f = m.apply(_fill(qty=1000), bar=b)
        # sigma = 0.02 (不变), participation = 1000/100000 = 0.01
        expected_bps = 0.02 * 0.5 * math.sqrt(0.01) * 10000
        expected_m = 1.0 + expected_bps / 10000.0
        assert f.price == pytest.approx(10.0 * expected_m, rel=1e-6)

    def test_bar_close_zero_skip_volatility(self):
        """bar.close=0 → 跳过日内波动率估算, 直接用 sigma_daily"""
        m = SquareRootImpactSlippageModel(
            sigma_daily=0.03, impact_coeff=1.0, use_bar_volatility=True,
        )
        b = _bar(open_=0.0, high=0.0, low=0.0, close=0.0, volume=50000)
        f = m.apply(_fill(qty=500, price=5.0), bar=b)
        # bar.close=0 → skip volatility → sigma=0.03
        participation = 500 / 50000
        expected_bps = 0.03 * 1.0 * math.sqrt(participation) * 10000
        expected_m = 1.0 + expected_bps / 10000.0
        assert f.price == pytest.approx(5.0 * expected_m, rel=1e-6)

    def test_participation_greater_than_one(self):
        """qty > bar.volume → participation > 1 → sqrt > 1"""
        m = SquareRootImpactSlippageModel(
            sigma_daily=0.02, impact_coeff=0.5,
        )
        b = _bar(volume=100)  # 很小的成交量
        f = m.apply(_fill(qty=10000), bar=b)
        # participation = 100 → sqrt(100) = 10
        expected_bps = 0.02 * 0.5 * 10.0 * 10000
        # 被 max_bps 截断
        capped = min(expected_bps, m.max_bps)
        expected_m = 1.0 + capped / 10000.0
        assert f.price == pytest.approx(10.0 * expected_m, rel=1e-6)

    def test_use_bar_volatility_false(self):
        """use_bar_volatility=False → 即使 bar 有波动也不更新 sigma"""
        m = SquareRootImpactSlippageModel(
            sigma_daily=0.01, impact_coeff=0.5, use_bar_volatility=False,
        )
        # 大范围 bar（如果 use_bar_volatility=True，sigma 会增大）
        b = _bar(high=15.0, low=5.0, close=10.0, volume=100000)
        f = m.apply(_fill(qty=1000), bar=b)
        participation = 1000 / 100000
        expected_bps = 0.01 * 0.5 * math.sqrt(participation) * 10000
        expected_m = 1.0 + expected_bps / 10000.0
        assert f.price == pytest.approx(10.0 * expected_m, rel=1e-6)


# ══════════════════════════════════════════════════════════════
# 4. _estimate_bar_amount 边界
# ══════════════════════════════════════════════════════════════


class TestEstimateBarAmountEdge:
    """_estimate_bar_amount 辅助函数边界"""

    def test_metadata_amount_zero(self):
        """metadata.amount=0 → 返回 0"""
        b = _bar(metadata={"amount": 0})
        assert _estimate_bar_amount(b) == 0.0

    def test_metadata_amount_negative(self):
        """metadata.amount=-100 → 原样返回"""
        b = _bar(metadata={"amount": -100.0})
        assert _estimate_bar_amount(b) == -100.0

    def test_vwap_positive_close_zero(self):
        """vwap > 0 但 close=0 → 使用 vwap"""
        b = _bar(close=0.0, volume=1000, vwap=8.0)
        assert _estimate_bar_amount(b) == pytest.approx(8000.0)

    def test_no_vwap_no_close(self):
        """vwap=None, close=0 → price_est=0 → amount=0"""
        b = _bar(close=0.0, volume=1000, vwap=None)
        assert _estimate_bar_amount(b) == 0.0

    def test_metadata_amount_string_float(self):
        """metadata.amount 为字符串数字 → float() 转换成功"""
        b = _bar(metadata={"amount": "12345.67"})
        assert _estimate_bar_amount(b) == pytest.approx(12345.67)
