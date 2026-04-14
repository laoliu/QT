"""
AccountManager 单元测试

TDD: 先写测试，再实现

测试覆盖：
1. 初始化 - 初始资金、币种
2. 资金冻结/解冻 - 下单时冻结、撤单时解冻
3. 买入结算 - 扣除资金、支付费用
4. 卖出结算 - 收入资金、扣除费用
5. NAV 计算 - 结合持仓市值
6. 日终清算 - 资金可用性重置
7. 边界条件 - 资金不足、非法操作
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.portfolio.account import Account


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def account() -> Account:
    """Create a test account with 1M initial cash."""
    return Account(initial_cash=Decimal("1000000.00"), currency="CNY")


# ============================================================================
# Initialization Tests
# ============================================================================


class TestInitialization:
    """Tests for account initialization."""

    def test_init_with_cash(self):
        """Account initializes with correct cash."""
        account = Account(initial_cash=Decimal("500000.00"))
        assert account.cash == Decimal("500000.00")
        assert account.frozen_cash == Decimal("0.00")
        assert account.available_cash == Decimal("500000.00")

    def test_init_with_currency(self):
        """Account stores currency."""
        account = Account(initial_cash=Decimal("100000.00"), currency="USD")
        assert account.currency == "USD"

    def test_init_default_currency(self):
        """Default currency is CNY."""
        account = Account(initial_cash=Decimal("100000.00"))
        assert account.currency == "CNY"

    def test_init_zero_cash_fails(self):
        """Zero initial cash fails."""
        with pytest.raises(ValueError, match="初始资金.*正数"):
            Account(initial_cash=Decimal("0.00"))

    def test_init_negative_cash_fails(self):
        """Negative initial cash fails."""
        with pytest.raises(ValueError, match="初始资金.*正数"):
            Account(initial_cash=Decimal("-100.00"))


# ============================================================================
# Freeze/Unfreeze Tests
# ============================================================================


class TestFreezeUnfreeze:
    """Tests for cash freeze/unfreeze operations."""

    def test_freeze_cash(self, account: Account):
        """Freezing cash reduces available."""
        result = account.freeze_cash(
            amount=Decimal("100000.00"), order_id="ORD001", reason="买入下单"
        )
        assert result is True
        assert account.frozen_cash == Decimal("100000.00")
        assert account.available_cash == Decimal("900000.00")
        assert account.cash == Decimal("1000000.00")  # Total unchanged

    def test_freeze_multiple_orders(self, account: Account):
        """Multiple freezes accumulate."""
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入")
        account.freeze_cash(Decimal("200000.00"), "ORD002", "买入")
        assert account.frozen_cash == Decimal("300000.00")
        assert account.available_cash == Decimal("700000.00")

    def test_freeze_exceeds_available_fails(self, account: Account):
        """Freeze exceeding available fails."""
        result = account.freeze_cash(
            Decimal("1100000.00"), "ORD001", "买入"
        )
        assert result is False
        assert account.frozen_cash == Decimal("0.00")

    def test_unfreeze_cash(self, account: Account):
        """Unfreeze restores available."""
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入")
        result = account.unfreeze_cash(
            amount=Decimal("100000.00"), order_id="ORD001", reason="撤单"
        )
        assert result is True
        assert account.frozen_cash == Decimal("0.00")
        assert account.available_cash == Decimal("1000000.00")

    def test_unfreeze_partial(self, account: Account):
        """Partial unfreeze works."""
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入")
        account.unfreeze_cash(Decimal("30000.00"), "ORD001", "部分撤单")
        assert account.frozen_cash == Decimal("70000.00")
        assert account.available_cash == Decimal("930000.00")

    def test_unfreeze_exceeds_frozen_fails(self, account: Account):
        """Unfreeze exceeding frozen fails."""
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入")
        result = account.unfreeze_cash(Decimal("150000.00"), "ORD001", "撤单")
        assert result is False
        assert account.frozen_cash == Decimal("100000.00")


# ============================================================================
# Settlement Tests
# ============================================================================


class TestSettlement:
    """Tests for buy/sell settlement."""

    def test_settle_buy(self, account: Account):
        """Buy settlement reduces cash and frozen."""
        # Freeze for order
        account.freeze_cash(Decimal("102000.00"), "ORD001", "买入冻结")

        # Buy: 1000 shares @ 10.00 = 10000, plus 5.00 fee
        cost = Decimal("10000.00")
        fee = Decimal("5.00")

        account.settle_buy(
            order_id="ORD001",
            cost=cost,
            fee=fee,
            trade_date=date(2025, 1, 6),
        )

        expected_cash = Decimal("1000000.00") - cost - fee
        assert account.cash == expected_cash
        assert account.frozen_cash == Decimal("0.00")  # Unfrozen after settle

    def test_settle_buy_releases_excess_frozen(self, account: Account):
        """Buy settlement releases excess frozen cash."""
        # Freeze more than needed (estimate)
        account.freeze_cash(Decimal("15000.00"), "ORD001", "买入冻结")

        # Actual cost is less
        account.settle_buy(
            order_id="ORD001",
            cost=Decimal("10000.00"),
            fee=Decimal("5.00"),
            trade_date=date(2025, 1, 6),
        )

        # Should have: 1M - 10005 = 989995
        assert account.cash == Decimal("989995.00")
        assert account.frozen_cash == Decimal("0.00")

    def test_settle_sell(self, account: Account):
        """Sell settlement increases cash."""
        # Sell: 1000 shares @ 10.50 = 10500, minus 15.00 fee
        proceeds = Decimal("10500.00")
        fee = Decimal("15.00")

        account.settle_sell(
            order_id="ORD002",
            proceeds=proceeds,
            fee=fee,
            trade_date=date(2025, 1, 6),
        )

        expected_cash = Decimal("1000000.00") + proceeds - fee
        assert account.cash == expected_cash

    def test_settle_buy_without_freeze_warns(self, account: Account):
        """Settle buy without prior freeze logs warning but proceeds (resilient mode)."""
        initial_cash = account.cash
        # Should NOT raise; proceeds with zero frozen amount
        account.settle_buy(
            order_id="ORD999",
            cost=Decimal("10000.00"),
            fee=Decimal("5.00"),
            trade_date=date(2025, 1, 6),
        )
        # Cash should be reduced by cost + fee
        assert account.cash == initial_cash - Decimal("10005.00")


# ============================================================================
# NAV Calculation Tests
# ============================================================================


class TestNAVCalculation:
    """Tests for Net Asset Value calculation."""

    def test_nav_cash_only(self, account: Account):
        """NAV with cash only."""
        nav = account.calculate_nav(positions_market_value=Decimal("0.00"))
        assert nav == Decimal("1000000.00")

    def test_nav_with_positions(self, account: Account):
        """NAV includes positions market value."""
        # After buying: cash reduced, but positions have value
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入")
        account.settle_buy("ORD001", Decimal("100000.00"), Decimal("50.00"), date(2025, 1, 6))

        # Cash: 1M - 100050 = 899950
        # Positions worth: 105000 (appreciated)
        nav = account.calculate_nav(positions_market_value=Decimal("105000.00"))
        expected = Decimal("899950.00") + Decimal("105000.00")
        assert nav == expected

    def test_nav_includes_frozen(self, account: Account):
        """Frozen cash is still part of NAV."""
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入")
        nav = account.calculate_nav(positions_market_value=Decimal("0.00"))
        assert nav == Decimal("1000000.00")  # Frozen is still cash


# ============================================================================
# Daily Limit Tests
# ============================================================================


class TestDailyLimits:
    """Tests for daily trading limits."""

    def test_get_max_buy_amount(self, account: Account):
        """Max buy amount considers available cash."""
        max_buy = account.get_max_buy_amount()
        assert max_buy == Decimal("1000000.00")

    def test_get_max_buy_after_freeze(self, account: Account):
        """Max buy reduces after freeze."""
        account.freeze_cash(Decimal("300000.00"), "ORD001", "买入")
        max_buy = account.get_max_buy_amount()
        assert max_buy == Decimal("700000.00")


# ============================================================================
# Log and Audit Tests
# ============================================================================


class TestAuditLog:
    """Tests for transaction logging."""

    def test_transaction_log_created(self, account: Account):
        """Transactions are logged."""
        account.freeze_cash(Decimal("100000.00"), "ORD001", "买入冻结")
        logs = account.get_transaction_logs()
        assert len(logs) == 1
        assert logs[0]["order_id"] == "ORD001"
        assert logs[0]["type"] == "FREEZE"

    def test_log_contains_settlement(self, account: Account):
        """Settlement is logged."""
        account.freeze_cash(Decimal("15000.00"), "ORD001", "买入")
        account.settle_buy("ORD001", Decimal("10000.00"), Decimal("5.00"), date(2025, 1, 6))

        logs = account.get_transaction_logs()
        assert len(logs) >= 2
        settle_log = [l for l in logs if l["type"] == "SETTLE_BUY"][0]
        assert settle_log["cost"] == Decimal("10000.00")
        assert settle_log["fee"] == Decimal("5.00")


# ============================================================================
# Snapshot Tests
# ============================================================================


class TestSnapshot:
    """Tests for account snapshots."""

    def test_to_dict(self, account: Account):
        """to_dict returns complete state."""
        account.freeze_cash(Decimal("50000.00"), "ORD001", "买入")
        snapshot = account.to_dict()

        assert snapshot["cash"] == Decimal("1000000.00")
        assert snapshot["frozen_cash"] == Decimal("50000.00")
        assert snapshot["available_cash"] == Decimal("950000.00")
        assert snapshot["currency"] == "CNY"


# ============================================================================
# Sell Credit Guard Tests
# ============================================================================


class TestSellCreditGuard:
    """卖出信用预借双重计算保护测试"""

    def test_credit_and_revoke_normal(self, account: Account):
        """正常流程：credit → revoke 成对使用"""
        initial = account.cash
        account.credit_sell_proceeds(Decimal("10000"), "预借")
        assert account.cash == initial + Decimal("10000")
        account.revoke_sell_credit(Decimal("10000"), "回收")
        assert account.cash == initial

    def test_double_credit_raises(self, account: Account):
        """未 revoke 就再次 credit 应抛 RuntimeError"""
        account.credit_sell_proceeds(Decimal("10000"), "预借1")
        with pytest.raises(RuntimeError, match="双重预借"):
            account.credit_sell_proceeds(Decimal("5000"), "预借2")

    def test_credit_after_revoke_ok(self, account: Account):
        """revoke 后可以再次 credit"""
        account.credit_sell_proceeds(Decimal("10000"), "预借1")
        account.revoke_sell_credit(Decimal("10000"), "回收1")
        # 第二次应该成功
        account.credit_sell_proceeds(Decimal("5000"), "预借2")
        assert account._sell_credit_outstanding == Decimal("5000")
        account.revoke_sell_credit(Decimal("5000"), "回收2")

    def test_get_frozen_for_order(self, account: Account):
        """get_frozen_for_order 查询冻结金额"""
        assert account.get_frozen_for_order("ORD_NONE") == Decimal("0.00")
        account.freeze_cash(Decimal("50000"), "ORD001", "买入")
        assert account.get_frozen_for_order("ORD001") == Decimal("50000")

    def test_sell_credit_logged(self, account: Account):
        """sell credit 操作应有事务日志"""
        account.credit_sell_proceeds(Decimal("10000"), "预借")
        account.revoke_sell_credit(Decimal("10000"), "回收")
        logs = account.get_transaction_logs()
        types = [l["type"] for l in logs]
        assert "SELL_CREDIT" in types
        assert "REVOKE_SELL_CREDIT" in types
