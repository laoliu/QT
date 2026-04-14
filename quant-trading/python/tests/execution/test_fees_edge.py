"""费用模型边界条件测试

覆盖 fees.py 中未测试的分支与边界场景。
"""

from datetime import datetime

import pytest

from aipha.domain.models import Fill, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
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


# ══════════════════════════════════════════════════════════════
# 1. CommissionModel 边界
# ══════════════════════════════════════════════════════════════


class TestCommissionModelEdge:
    """CommissionModel 边界"""

    def test_fee_slightly_above_min(self):
        """fee 恰好略大于 min_fee → 返回 fee 而非 min_fee"""
        # notional=10000, rate=0.0005 → fee=5.0, min_fee=4.999
        m = CommissionModel(rate=0.0005, min_fee=4.999)
        f = _fill(qty=1000, price=10.0)
        assert m.calc(f) == pytest.approx(5.0)  # fee > min_fee

    def test_fee_exactly_equals_min(self):
        """fee == min_fee → 走 else 分支（返回 fee）"""
        m = CommissionModel(rate=0.0005, min_fee=5.0)
        f = _fill(qty=1000, price=10.0)  # notional=10000 → fee=5.0
        result = m.calc(f)
        assert result == 5.0

    def test_negative_rate(self):
        """负 rate → fee 为负 → fee < min_fee → 返回 min_fee"""
        m = CommissionModel(rate=-0.001, min_fee=0.0)
        f = _fill()
        result = m.calc(f)
        # fee = 10000 * -0.001 = -10 < 0 = min_fee → 返回 0.0
        assert result == 0.0

    def test_zero_notional(self):
        """qty=0 → notional=0 → fee=0 < min_fee=5 → 返回 min_fee"""
        m = CommissionModel(rate=0.0003, min_fee=5.0)
        f = _fill(qty=0)
        assert m.calc(f) == 5.0

    def test_zero_price(self):
        """price=0 → notional=0 → fee=0"""
        m = CommissionModel(rate=0.0003, min_fee=0.0)
        f = _fill(price=0.0)
        assert m.calc(f) == 0.0


# ══════════════════════════════════════════════════════════════
# 2. StampTaxModel 边界
# ══════════════════════════════════════════════════════════════


class TestStampTaxModelEdge:
    """StampTaxModel 边界"""

    def test_buy_always_zero(self):
        """BUY → 0，无论 rate"""
        m = StampTaxModel(sell_rate=0.001)
        assert m.calc(_fill(side=Side.BUY)) == 0.0

    def test_sell_normal(self):
        """SELL → notional * rate"""
        m = StampTaxModel(sell_rate=0.001)
        f = _fill(side=Side.SELL, qty=1000, price=10.0)
        assert m.calc(f) == pytest.approx(10.0)

    def test_negative_sell_rate(self):
        """负 rate → 负税费（理论不应出现，但代码不防御）"""
        m = StampTaxModel(sell_rate=-0.001)
        f = _fill(side=Side.SELL)
        assert m.calc(f) < 0.0

    def test_zero_rate(self):
        """rate=0 → 免税"""
        m = StampTaxModel(sell_rate=0.0)
        f = _fill(side=Side.SELL)
        assert m.calc(f) == 0.0


# ══════════════════════════════════════════════════════════════
# 3. TransferFeeModel 边界
# ══════════════════════════════════════════════════════════════


class TestTransferFeeModelEdge:
    """TransferFeeModel 边界"""

    def test_normal(self):
        """正常计算"""
        m = TransferFeeModel(rate=0.00002)
        f = _fill(qty=1000, price=10.0)
        assert m.calc(f) == pytest.approx(0.2)

    def test_zero_notional(self):
        """notional=0 → 0"""
        m = TransferFeeModel(rate=0.00002)
        assert m.calc(_fill(qty=0)) == 0.0

    def test_negative_rate(self):
        """负 rate → 负费用"""
        m = TransferFeeModel(rate=-0.00002)
        assert m.calc(_fill()) < 0.0


# ══════════════════════════════════════════════════════════════
# 4. FeeBreakdown 边界
# ══════════════════════════════════════════════════════════════


class TestFeeBreakdownEdge:
    """FeeBreakdown 边界"""

    def test_all_zero(self):
        """全零"""
        fb = FeeBreakdown(commission=0.0, stamp_tax=0.0, transfer_fee=0.0)
        assert fb.total == 0.0

    def test_negative_component(self):
        """含负分量"""
        fb = FeeBreakdown(commission=5.0, stamp_tax=-1.0, transfer_fee=0.2)
        assert fb.total == pytest.approx(4.2)

    def test_large_values(self):
        """大额"""
        fb = FeeBreakdown(commission=1e8, stamp_tax=5e7, transfer_fee=2e6)
        assert fb.total == pytest.approx(1.52e8)


# ══════════════════════════════════════════════════════════════
# 5. FeeModelCNEquity 端到端边界
# ══════════════════════════════════════════════════════════════


class TestFeeModelCNEquityEdge:
    """FeeModelCNEquity 组合模型边界"""

    def test_zero_qty_fill(self):
        """qty=0 且 min_fee>0 → 全部费用只有 min_fee"""
        model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0003, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00002),
        )
        f = _fill(side=Side.SELL, qty=0)
        result = model.calc(f)
        assert result.commission == 5.0  # min_fee
        assert result.stamp_tax == 0.0
        assert result.transfer_fee == 0.0

    def test_large_notional_precision(self):
        """大额交易浮点精度"""
        model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0003, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00002),
        )
        # notional = 1e6 * 100 = 1e8
        f = _fill(side=Side.SELL, qty=1_000_000, price=100.0)
        result = model.calc(f)
        assert result.commission == pytest.approx(30000.0)  # 1e8 * 0.0003
        assert result.stamp_tax == pytest.approx(100000.0)  # 1e8 * 0.001
        assert result.transfer_fee == pytest.approx(2000.0)  # 1e8 * 0.00002
        assert result.total == pytest.approx(132000.0)

    def test_buy_no_stamp_tax(self):
        """BUY → 印花税=0"""
        model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0003, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00002),
        )
        f = _fill(side=Side.BUY, qty=1000, price=10.0)
        result = model.calc(f)
        assert result.stamp_tax == 0.0
        assert result.commission == pytest.approx(max(10000 * 0.0003, 5.0))
