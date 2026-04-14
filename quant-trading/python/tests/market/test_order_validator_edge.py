"""订单校验器边界条件测试

覆盖 order_validator.py 中未被 41 个已有测试覆盖的分支。
"""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.market.order_validator import (
    OrderSide,
    OrderValidator,
    RejectLog,
    RejectReason,
    ValidationResult,
)
from aipha.portfolio.position import Position


# ── helpers ──────────────────────────────────────────────────


def _validator(lot_size: int = 100) -> OrderValidator:
    return OrderValidator(lot_size=lot_size)


# ══════════════════════════════════════════════════════════════
# 1. validate_quantity 边界
# ══════════════════════════════════════════════════════════════


class TestValidateQuantityEdge:
    """委托数量校验边界"""

    def test_qty_zero(self):
        """qty=0 → INVALID_QUANTITY"""
        v = _validator()
        r = v.validate_quantity(0)
        assert not r.is_valid
        assert r.reject_reason == RejectReason.INVALID_QUANTITY

    def test_qty_negative(self):
        """qty=-100 → INVALID_QUANTITY"""
        v = _validator()
        r = v.validate_quantity(-100)
        assert not r.is_valid
        assert r.reject_reason == RejectReason.INVALID_QUANTITY

    def test_qty_not_round_lot(self):
        """qty=150 → NOT_ROUND_LOT"""
        v = _validator(lot_size=100)
        r = v.validate_quantity(150)
        assert not r.is_valid
        assert r.reject_reason == RejectReason.NOT_ROUND_LOT

    def test_qty_exactly_max(self):
        """qty == MAX_ORDER_QTY → 允许"""
        v = _validator()
        r = v.validate_quantity(1_000_000)
        assert r.is_valid

    def test_qty_exceeds_max(self):
        """qty > MAX_ORDER_QTY → EXCEEDS_MAX_QTY"""
        v = _validator()
        r = v.validate_quantity(1_000_100)
        assert not r.is_valid
        assert r.reject_reason == RejectReason.EXCEEDS_MAX_QTY

    def test_cb_lot_size_10(self):
        """可转债 lot_size=10"""
        v = _validator(lot_size=10)
        assert v.validate_quantity(10).is_valid
        assert v.validate_quantity(15).is_valid is False


# ══════════════════════════════════════════════════════════════
# 2. validate_price 边界
# ══════════════════════════════════════════════════════════════


class TestValidatePriceEdge:
    """价格校验边界"""

    def test_price_exactly_at_limit_up(self):
        """价格恰好等于涨停价 → 允许"""
        v = _validator()
        # 主板 ±10%: prev=10 → limit_up=11.00
        r = v.validate_price("000001.SZ", Decimal("11.00"), Decimal("10.00"))
        assert r.is_valid

    def test_price_one_fen_above_limit(self):
        """价格比涨停价高 0.01 → 拒绝"""
        v = _validator()
        r = v.validate_price("000001.SZ", Decimal("11.01"), Decimal("10.00"))
        assert not r.is_valid
        assert r.reject_reason == RejectReason.PRICE_EXCEEDS_LIMIT

    def test_price_below_limit_down(self):
        """价格低于跌停价 → 拒绝"""
        v = _validator()
        r = v.validate_price("000001.SZ", Decimal("8.90"), Decimal("10.00"))
        assert not r.is_valid

    def test_st_price_range(self):
        """ST ±5%"""
        v = _validator()
        # ST: limit_up=10.50, limit_down=9.50
        r = v.validate_price("000001.SZ", Decimal("10.55"), Decimal("10.00"), is_st=True)
        assert not r.is_valid


# ══════════════════════════════════════════════════════════════
# 3. validate_sell_position 边界
# ══════════════════════════════════════════════════════════════


class TestValidateSellPositionEdge:
    """卖出持仓校验边界"""

    def test_t_plus_1_restriction(self):
        """今日买入不可卖 → T_PLUS_1_RESTRICTION"""
        v = _validator()
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            avg_cost=10.0,
            today_bought_qty=1000,  # 全部是今天买的
        )
        r = v.validate_sell_position(pos, 500)
        assert not r.is_valid
        assert r.reject_reason == RejectReason.T_PLUS_1_RESTRICTION

    def test_insufficient_no_today(self):
        """无今日买入但持仓不足 → INSUFFICIENT_POSITION"""
        v = _validator()
        pos = Position(
            symbol="000001.SZ",
            total_qty=200,
            avg_cost=10.0,
            today_bought_qty=0,
        )
        r = v.validate_sell_position(pos, 300)
        assert not r.is_valid
        assert r.reject_reason == RejectReason.INSUFFICIENT_POSITION

    def test_sell_exact_available(self):
        """卖出恰好等于可卖 → 允许"""
        v = _validator()
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            avg_cost=10.0,
            today_bought_qty=0,
        )
        r = v.validate_sell_position(pos, 1000)
        assert r.is_valid


# ══════════════════════════════════════════════════════════════
# 4. validate_buy_funds 边界
# ══════════════════════════════════════════════════════════════


class TestValidateBuyFundsEdge:
    """买入资金校验边界"""

    def test_just_enough(self):
        """资金恰好足够(含佣金) → 允许"""
        v = _validator()
        # order_amount=100*100=10000, commission=max(10000*0.0001,5)=5, total=10005
        r = v.validate_buy_funds(
            available_cash=Decimal("10005.00"),
            order_price=Decimal("100"),
            order_qty=100,
        )
        assert r.is_valid

    def test_one_fen_short(self):
        """差 0.01 元 → 拒绝"""
        v = _validator()
        r = v.validate_buy_funds(
            available_cash=Decimal("10004.99"),
            order_price=Decimal("100"),
            order_qty=100,
        )
        assert not r.is_valid
        assert r.reject_reason == RejectReason.INSUFFICIENT_FUNDS

    def test_commission_min_5_yuan(self):
        """小额交易佣金兜底 5 元"""
        v = _validator()
        # order_amount=10*10=100, commission=max(0.01,5)=5, total=105
        r = v.validate_buy_funds(
            available_cash=Decimal("105"),
            order_price=Decimal("10"),
            order_qty=10,
        )
        assert r.is_valid


# ══════════════════════════════════════════════════════════════
# 5. RejectLog & 日志管理
# ══════════════════════════════════════════════════════════════


class TestRejectLogEdge:
    """拒单日志边界"""

    def test_to_dict_all_fields(self):
        """to_dict 包含所有字段"""
        log = RejectLog(
            timestamp=datetime(2024, 6, 1, 10, 0, 0),
            order_id="ORD001",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("10.50"),
            quantity=100,
            reason=RejectReason.STOCK_HALTED,
            reason_detail="停牌",
            context={"key": "value"},
        )
        d = log.to_dict()
        assert d["order_id"] == "ORD001"
        assert d["reason"] == "stock_halted"
        assert d["side"] == "buy"
        assert d["context"]["key"] == "value"

    def test_get_reject_summary(self):
        """拒单统计"""
        v = _validator()
        # 触发几个拒绝
        v.validate_quantity(0)
        # 手动记录
        v._log_reject(None, "A", OrderSide.BUY, Decimal("0"), 0,
                       RejectReason.INVALID_QUANTITY, "test")
        v._log_reject(None, "B", OrderSide.SELL, Decimal("0"), 0,
                       RejectReason.STOCK_HALTED, "test2")
        v._log_reject(None, "C", OrderSide.BUY, Decimal("0"), 0,
                       RejectReason.INVALID_QUANTITY, "test3")
        summary = v.get_reject_summary()
        assert summary["invalid_quantity"] == 2
        assert summary["stock_halted"] == 1

    def test_clear_logs(self):
        """clear_logs 清空日志"""
        v = _validator()
        v._log_reject(None, "A", OrderSide.BUY, Decimal("0"), 0,
                       RejectReason.UNKNOWN, "test")
        assert len(v.get_reject_logs()) == 1
        v.clear_logs()
        assert len(v.get_reject_logs()) == 0

    def test_export_logs(self):
        """export_logs 返回字典列表"""
        v = _validator()
        v._log_reject("ID1", "000001.SZ", OrderSide.SELL, Decimal("10"), 100,
                       RejectReason.LIMIT_DOWN_NO_SELL, "跌停")
        logs = v.export_logs()
        assert len(logs) == 1
        assert logs[0]["reason"] == "limit_down_no_sell"
