"""Paper Trading 网关测试

测试覆盖：
- 订单提交与验证
- 订单撮合（限价/市价）
- 订单取消
- 资金冻结/解冻
- 持仓冻结/解冻
- T+0/T+1 规则
- 账户快照

设计原则：
- 使用真实数据实例，不使用 Mock
- 测试完整的订单生命周期
"""

from datetime import date, datetime
from decimal import Decimal
import uuid

import pytest

from aipha.domain.models import Bar
from aipha.execution.gateway import (
    GatewayOrder,
    OrderStatus,
    TradingMode,
    OrderValidationError,
    InsufficientFundsError,
    InsufficientPositionError,
)
from aipha.execution.paper_gateway import PaperTradingGateway


def make_order(
    symbol: str = "000001.SZ",
    side: str = "BUY",
    qty: int = 100,
    order_type: str = "LIMIT",
    limit_price: float | None = 10.0,
) -> GatewayOrder:
    """创建测试订单"""
    return GatewayOrder(
        order_id=f"T{uuid.uuid4().hex[:8]}",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        created_at=datetime.now(),
    )


@pytest.fixture
def gateway() -> PaperTradingGateway:
    """创建标准的仿真网关"""
    gw = PaperTradingGateway(
        initial_capital=Decimal("1000000"),
        commission_rate=Decimal("0.0003"),
        commission_min=Decimal("5"),
        stamp_tax_rate=Decimal("0.001"),
    )
    gw.connect()
    return gw


@pytest.fixture
def sample_bar() -> Bar:
    """示例 Bar 数据"""
    return Bar(
        symbol="000001.SZ",
        start_ts=datetime(2024, 1, 15, 9, 30),
        end_ts=datetime(2024, 1, 15, 15, 0),
        open=10.5,
        high=11.0,
        low=10.3,
        close=10.8,
        volume=1000000.0,
    )


class TestConnection:
    """连接测试"""

    def test_connect(self):
        """测试连接"""
        gw = PaperTradingGateway()
        assert not gw.is_connected
        gw.connect()
        assert gw.is_connected

    def test_disconnect(self):
        """测试断开"""
        gw = PaperTradingGateway()
        gw.connect()
        gw.disconnect()
        assert not gw.is_connected

    def test_mode(self):
        """测试交易模式"""
        gw = PaperTradingGateway()
        assert gw.mode == TradingMode.PAPER


class TestOrderSubmission:
    """订单提交测试"""

    def test_submit_buy_order_success(self, gateway: PaperTradingGateway):
        """测试成功提交买入订单"""
        order = make_order(side="BUY", qty=100, limit_price=10.0)
        order_id = gateway.submit_order(order)

        assert order_id is not None
        assert order.status == OrderStatus.SUBMITTED

    def test_submit_market_order(self, gateway: PaperTradingGateway):
        """测试提交市价订单"""
        order = make_order(
            side="BUY",
            qty=100,
            order_type="MARKET",
            limit_price=None,
        )
        order_id = gateway.submit_order(order)
        assert order_id is not None
        assert order.order_type == "MARKET"

    def test_invalid_qty_rejected(self, gateway: PaperTradingGateway):
        """测试无效数量被拒"""
        order = make_order(qty=0)

        with pytest.raises(OrderValidationError, match="qty"):
            gateway.submit_order(order)

    def test_non_lot_size_rejected(self, gateway: PaperTradingGateway):
        """测试非整手数量被拒"""
        order = make_order(qty=50)  # 不是 100 的倍数

        with pytest.raises(OrderValidationError, match="multiple"):
            gateway.submit_order(order)

    def test_insufficient_funds(self, gateway: PaperTradingGateway):
        """测试资金不足"""
        order = make_order(
            side="BUY",
            qty=10000000,  # 巨额
            limit_price=1000.0,
        )

        with pytest.raises(InsufficientFundsError):
            gateway.submit_order(order)


class TestOrderCancellation:
    """订单取消测试"""

    def test_cancel_pending_order(self, gateway: PaperTradingGateway):
        """测试取消待成交订单"""
        order = make_order(side="BUY", qty=100, limit_price=9.0)
        order_id = gateway.submit_order(order)

        result = gateway.cancel_order(order_id)

        assert result is True
        assert order.status == OrderStatus.CANCELED

    def test_cancel_nonexistent_order(self, gateway: PaperTradingGateway):
        """测试取消不存在的订单"""
        result = gateway.cancel_order("nonexistent")
        assert result is False


class TestOrderMatching:
    """订单撮合测试"""

    def test_limit_buy_order_matches(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试限价买单撮合

        限价 >= 开盘价 成交
        """
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,  # 高于开盘价 10.5
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        assert order.status == OrderStatus.FILLED
        assert order.filled_qty == 100

    def test_limit_buy_order_no_match(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试限价买单未撮合（价格过低）"""
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=9.0,  # 低于开盘价 10.5
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        # 仍然挂单
        assert order.status == OrderStatus.SUBMITTED

    def test_market_buy_order_matches(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试市价买单以开盘价成交（含滑点）"""
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
            limit_price=None,
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        assert order.status == OrderStatus.FILLED
        # 成交价基于开盘价 10.5，含滑点（约 2bps）
        assert Decimal("10.4") < order.avg_fill_price < Decimal("10.6")

    def test_sell_order_requires_position(
        self, gateway: PaperTradingGateway, sample_bar: Bar
    ):
        """测试卖出需要持仓"""
        # 设置初始交易日
        gateway.set_trade_date(date(2024, 1, 15))

        # 先买入建仓
        buy_order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        gateway.submit_order(buy_order)
        gateway.process_bar(sample_bar)

        # 模拟日终结算（解锁 T+1）
        gateway.set_trade_date(date(2024, 1, 16))

        # 卖出
        sell_order = make_order(
            symbol="000001.SZ",
            side="SELL",
            qty=100,
            limit_price=10.0,
        )
        gateway.submit_order(sell_order)

        next_day_bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 16, 9, 30),
            end_ts=datetime(2024, 1, 16, 15, 0),
            open=10.5,
            high=11.0,
            low=10.3,
            close=10.8,
            volume=1000000.0,
        )
        gateway.process_bar(next_day_bar)

        assert sell_order.status == OrderStatus.FILLED

    def test_sell_insufficient_position(self, gateway: PaperTradingGateway):
        """测试卖出持仓不足"""
        sell_order = make_order(
            symbol="000001.SZ",
            side="SELL",
            qty=100,
            limit_price=10.0,
        )

        with pytest.raises(InsufficientPositionError):
            gateway.submit_order(sell_order)


class TestSettlement:
    """结算测试"""

    def test_buy_settlement_updates_cash(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试买入后资金减少"""
        initial_cash = gateway._cash

        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        # 成交后资金减少
        assert gateway._cash < initial_cash

    def test_buy_settlement_increases_position(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试买入后持仓增加"""
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        pos = gateway._positions.get("000001.SZ")
        assert pos is not None
        assert pos["qty"] == 100


class TestTradingRules:
    """交易规则测试"""

    def test_t_plus_1_cannot_sell_today(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试 T+1 当日买入当日不可卖"""
        # 买入
        buy_order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        gateway.submit_order(buy_order)
        gateway.process_bar(sample_bar)

        # 当天尝试卖出 - 应该失败
        sell_order = make_order(
            symbol="000001.SZ",
            side="SELL",
            qty=100,
            limit_price=10.0,
        )

        with pytest.raises(InsufficientPositionError):
            gateway.submit_order(sell_order)

    def test_t_plus_1_can_sell_next_day(
        self, gateway: PaperTradingGateway, sample_bar: Bar
    ):
        """测试 T+1 次日可卖"""
        # 设置初始交易日
        gateway.set_trade_date(date(2024, 1, 15))

        # 买入
        buy_order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        gateway.submit_order(buy_order)
        gateway.process_bar(sample_bar)

        # 切换到次日
        gateway.set_trade_date(date(2024, 1, 16))

        # 卖出
        sell_order = make_order(
            symbol="000001.SZ",
            side="SELL",
            qty=100,
            limit_price=10.0,
        )
        order_id = gateway.submit_order(sell_order)
        assert order_id is not None

    def test_t_plus_0_can_sell_today(self):
        """测试 T+0 当日可卖"""
        gateway = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            t_plus_n=0,  # T+0 模式
        )
        gateway.connect()

        # 买入
        buy_order = make_order(
            symbol="123456.SZ",
            side="BUY",
            qty=100,
            limit_price=101.0,
        )
        gateway.submit_order(buy_order)

        cb_bar = Bar(
            symbol="123456.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=500000.0,
        )
        gateway.process_bar(cb_bar)

        # T+0 当天可直接卖出
        sell_order = make_order(
            symbol="123456.SZ",
            side="SELL",
            qty=100,
            limit_price=99.0,
        )
        order_id = gateway.submit_order(sell_order)
        assert order_id is not None


class TestAccountSnapshot:
    """账户快照测试"""

    def test_initial_snapshot(self, gateway: PaperTradingGateway):
        """测试初始快照"""
        snapshot = gateway.get_account_snapshot()

        assert snapshot.cash == Decimal("1000000")
        assert snapshot.frozen_cash == Decimal("0")
        assert len(snapshot.positions) == 0

    def test_snapshot_with_orders(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试有订单时的快照"""
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        snapshot = gateway.get_account_snapshot()

        assert "000001.SZ" in snapshot.positions


class TestMultipleOrders:
    """多订单测试"""

    def test_multiple_orders_same_symbol(
        self, gateway: PaperTradingGateway, sample_bar: dict
    ):
        """测试同标的多订单"""
        order1 = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        order2 = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=200,
            limit_price=11.0,
        )

        gateway.submit_order(order1)
        gateway.submit_order(order2)
        gateway.process_bar(sample_bar)

        assert order1.status == OrderStatus.FILLED
        assert order2.status == OrderStatus.FILLED

        # 总持仓 300
        pos = gateway._positions["000001.SZ"]
        assert pos["qty"] == 300

    def test_multiple_symbols(
        self, gateway: PaperTradingGateway, sample_bar: Bar
    ):
        """测试多标的订单"""
        order1 = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            limit_price=11.0,
        )
        order2 = make_order(
            symbol="600000.SH",
            side="BUY",
            qty=100,
            limit_price=8.0,
        )

        gateway.submit_order(order1)
        gateway.submit_order(order2)

        # 处理两个 bar
        gateway.process_bar(sample_bar)
        bar2 = Bar(
            symbol="600000.SH",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=7.5,
            high=8.0,
            low=7.3,
            close=7.8,
            volume=500000.0,
        )
        gateway.process_bar(bar2)

        assert order1.status == OrderStatus.FILLED
        assert order2.status == OrderStatus.FILLED
        assert "000001.SZ" in gateway._positions
        assert "600000.SH" in gateway._positions


class TestNAVCalculation:
    """NAV（净值）计算测试 — Sprint 1.1

    验证 total_equity = cash + 持仓市值
    """

    def test_nav_includes_position_value(
        self, gateway: PaperTradingGateway, sample_bar: Bar
    ):
        """买入后 total_equity = cash + 持仓市值"""
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
            limit_price=None,
        )
        gateway.submit_order(order)
        reports = gateway.process_bar(sample_bar)
        assert len(reports) == 1

        snapshot = gateway.get_account_snapshot()
        fill_price = reports[0].fill_price
        commission = reports[0].commission

        # 持仓市值 = qty × bar.close（最新价）
        expected_pos_value = Decimal(str(sample_bar.close)) * 100
        assert snapshot.positions_value == expected_pos_value

        # total_equity = cash + positions_value
        expected_cost = Decimal(str(fill_price * 100)) + commission
        expected_equity = Decimal("1000000") - expected_cost + expected_pos_value
        assert snapshot.total_equity == expected_equity

        # total_equity 不应等于 cash（除非持仓为空）
        assert snapshot.total_equity != snapshot.cash

    def test_nav_updates_with_price_change(self, gateway: PaperTradingGateway):
        """价格变动后 NAV 应随之更新"""
        # 第一根 bar：买入
        bar1 = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0,
            high=11.0,
            low=9.5,
            close=10.5,
            volume=1000000.0,
        )
        order = make_order(
            symbol="000001.SZ",
            side="BUY",
            qty=1000,
            order_type="MARKET",
            limit_price=None,
        )
        gateway.submit_order(order)
        gateway.process_bar(bar1)
        snap1 = gateway.get_account_snapshot()

        # 第二根 bar：价格上涨，无新订单
        bar2 = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 16, 9, 30),
            end_ts=datetime(2024, 1, 16, 15, 0),
            open=10.8,
            high=12.0,
            low=10.5,
            close=11.5,
            volume=800000.0,
        )
        gateway.process_bar(bar2)
        snap2 = gateway.get_account_snapshot()

        # cash 不变（无新交易），但持仓市值增加
        assert snap2.cash == snap1.cash
        assert snap2.positions_value > snap1.positions_value
        assert snap2.total_equity > snap1.total_equity

    def test_nav_multi_symbol(self, gateway: PaperTradingGateway):
        """多标的持仓市值正确累加"""
        bar_a = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0,
            high=11.0,
            low=9.5,
            close=10.5,
            volume=1000000.0,
        )
        bar_b = Bar(
            symbol="600000.SH",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=8.0,
            high=8.5,
            low=7.8,
            close=8.2,
            volume=500000.0,
        )

        order_a = make_order(
            symbol="000001.SZ", side="BUY", qty=100,
            order_type="MARKET", limit_price=None,
        )
        order_b = make_order(
            symbol="600000.SH", side="BUY", qty=200,
            order_type="MARKET", limit_price=None,
        )

        gateway.submit_order(order_a)
        gateway.submit_order(order_b)

        gateway.process_bar(bar_a)
        gateway.process_bar(bar_b)

        snapshot = gateway.get_account_snapshot()

        expected_value = (
            Decimal(str(bar_a.close)) * 100
            + Decimal(str(bar_b.close)) * 200
        )
        assert snapshot.positions_value == expected_value
        assert snapshot.total_equity == snapshot.cash + expected_value

    def test_nav_empty_positions(self, gateway: PaperTradingGateway):
        """空仓时 total_equity = cash"""
        snapshot = gateway.get_account_snapshot()
        assert snapshot.total_equity == snapshot.cash
        assert snapshot.positions_value == Decimal("0")

    def test_avg_cost_tracking(
        self, gateway: PaperTradingGateway, sample_bar: Bar
    ):
        """验证加权平均成本跟踪"""
        # 第一次买入
        order1 = make_order(
            symbol="000001.SZ", side="BUY", qty=100,
            order_type="MARKET", limit_price=None,
        )
        gateway.submit_order(order1)
        reports1 = gateway.process_bar(sample_bar)
        price1 = reports1[0].fill_price

        # 第二次买入（不同价格的 bar）
        bar2 = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 16, 9, 30),
            end_ts=datetime(2024, 1, 16, 15, 0),
            open=12.0,
            high=13.0,
            low=11.5,
            close=12.5,
            volume=1000000.0,
        )
        order2 = make_order(
            symbol="000001.SZ", side="BUY", qty=200,
            order_type="MARKET", limit_price=None,
        )
        gateway.submit_order(order2)
        reports2 = gateway.process_bar(bar2)
        price2 = reports2[0].fill_price

        # 验证加权平均成本
        pos = gateway._positions["000001.SZ"]
        expected_avg = (
            Decimal(str(price1)) * 100
            + Decimal(str(price2)) * 200
        ) / 300
        assert abs(pos["avg_cost"] - expected_avg) < Decimal("0.0001")

    def test_nav_after_reset(self, gateway: PaperTradingGateway, sample_bar: Bar):
        """reset 后 NAV 和 latest_prices 都被清空"""
        order = make_order(
            symbol="000001.SZ", side="BUY", qty=100,
            order_type="MARKET", limit_price=None,
        )
        gateway.submit_order(order)
        gateway.process_bar(sample_bar)

        gateway.reset()

        snapshot = gateway.get_account_snapshot()
        assert snapshot.total_equity == Decimal("1000000")
        assert snapshot.positions_value == Decimal("0")
        assert len(gateway._latest_prices) == 0


class TestFeeModelConsistency:
    """费用模型一致性测试 — Sprint 2.1

    验证 PaperGateway 与 BacktestEngine 使用同一套 FeeModelCNEquity，
    确保费用计算结果一致。
    """

    def test_fee_includes_transfer_fee(self):
        """验证 PaperGateway 费用包含过户费"""
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            commission_rate=Decimal("0.0003"),
            commission_min=Decimal("5"),
            stamp_tax_rate=Decimal("0.001"),
        )
        gw.connect()

        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0,
            high=11.0,
            low=9.5,
            close=10.5,
            volume=1000000.0,
        )
        order = make_order(
            symbol="000001.SZ", side="BUY", qty=1000,
            order_type="MARKET", limit_price=None,
        )
        gw.submit_order(order)
        reports = gw.process_bar(bar)

        assert len(reports) == 1
        commission = reports[0].commission
        # 费用 > 0 且应包含过户费分量
        assert commission > Decimal("0")

    def test_fee_model_matches_direct_calc(self):
        """验证 PaperGateway 的费用与直接调用 FeeModelCNEquity 一致"""
        from aipha.domain.models import Fill, Side
        from aipha.execution.fees import (
            CommissionModel,
            FeeModelCNEquity,
            StampTaxModel,
            TransferFeeModel,
        )

        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0003, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00001),
        )

        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            commission_rate=Decimal("0.0003"),
            commission_min=Decimal("5"),
            stamp_tax_rate=Decimal("0.001"),
            fee_model=fee_model,
        )
        gw.connect()

        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0,
            high=11.0,
            low=9.5,
            close=10.5,
            volume=1000000.0,
        )
        order = make_order(
            symbol="000001.SZ", side="BUY", qty=1000,
            order_type="MARKET", limit_price=None,
        )
        gw.submit_order(order)
        reports = gw.process_bar(bar)
        gateway_commission = float(reports[0].commission)

        # 直接用 fee_model 计算：同样的成交价和数量
        fill_price = reports[0].fill_price
        fill = Fill(
            order_id="test", symbol="000001.SZ", side=Side.BUY,
            qty=1000, price=fill_price, t_fill=bar.end_ts, model="test",
        )
        direct = fee_model.calc(fill)

        assert abs(gateway_commission - direct.total) < 0.01

    def test_sell_first_ordering(self):
        """验证卖出订单优先于买入订单撮合"""
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            commission_rate=Decimal("0.0001"),
            commission_min=Decimal("0"),
            stamp_tax_rate=Decimal("0"),
            t_plus_n=0,  # T+0 允许当日卖出
        )
        gw.connect()

        # 先买入建仓
        bar1 = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=11.0, low=9.5, close=10.5,
            volume=1000000.0,
        )
        buy_order = make_order(
            symbol="000001.SZ", side="BUY", qty=200,
            order_type="MARKET", limit_price=None,
        )
        gw.submit_order(buy_order)
        gw.process_bar(bar1)

        # 同时提交卖出+买入订单
        sell_order = make_order(
            symbol="000001.SZ", side="SELL", qty=100,
            order_type="MARKET", limit_price=None,
        )
        buy_order2 = make_order(
            symbol="000001.SZ", side="BUY", qty=100,
            order_type="MARKET", limit_price=None,
        )
        gw.submit_order(sell_order)
        gw.submit_order(buy_order2)

        bar2 = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 16, 9, 30),
            end_ts=datetime(2024, 1, 16, 15, 0),
            open=10.5, high=11.0, low=10.0, close=10.8,
            volume=1000000.0,
        )
        reports = gw.process_bar(bar2)

        # 两个订单都应成交
        assert len(reports) == 2
        # 卖出应先于买入
        assert reports[0].side == "SELL"
        assert reports[1].side == "BUY"


# ============================================================================
# B5: SlippageModel 集成测试 (§4.3 回测/模拟盘共享接口)
# ============================================================================


class TestSlippageModelIntegration:
    """验证 PaperTradingGateway 正确使用 SlippageModel Protocol"""

    def test_fixed_bps_model_via_protocol(self):
        """通过 SlippageModel 接口使用 FixedBpsSlippageModel"""
        from aipha.execution.slippage import FixedBpsSlippageModel

        model = FixedBpsSlippageModel(bps=5.0)
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            slippage_model=model,
        )
        gw.connect()
        order = make_order(qty=100, limit_price=10.0)
        gw.submit_order(order)
        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.5, low=9.8, close=10.2,
            volume=500000.0,
        )
        reports = gw.process_bar(bar)
        assert len(reports) == 1
        # 买入价 = 开盘价 + 5bps 滑点
        fill_price = float(reports[0].fill_price)
        assert fill_price > 10.0  # 滑点后价格高于基准

    def test_sqrt_impact_model_via_protocol(self):
        """通过 SlippageModel 接口使用 SquareRootImpactSlippageModel"""
        from aipha.execution.slippage import SquareRootImpactSlippageModel

        model = SquareRootImpactSlippageModel(
            impact_coeff=0.5,
            sigma_daily=0.02,
        )
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            slippage_model=model,
        )
        gw.connect()
        order = make_order(qty=100, limit_price=10.0)
        gw.submit_order(order)
        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.5, low=9.8, close=10.2,
            volume=500000.0,
        )
        reports = gw.process_bar(bar)
        assert len(reports) == 1
        fill_price = float(reports[0].fill_price)
        # sqrt impact: 价格应高于基准（买入）
        assert fill_price > 10.0

    def test_no_model_fallback_to_fixed_bps(self):
        """未指定 slippage_model 时回退到固定 bps"""
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            slippage_bps=3.0,
            # 不传 slippage_model
        )
        gw.connect()
        order = make_order(qty=100, limit_price=10.0)
        gw.submit_order(order)
        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.5, low=9.8, close=10.2,
            volume=500000.0,
        )
        reports = gw.process_bar(bar)
        assert len(reports) == 1
        fill_price = float(reports[0].fill_price)
        expected = 10.0 * (1 + 3.0 / 10000)
        assert abs(fill_price - expected) < 0.001

    def test_model_overrides_slippage_bps(self):
        """指定 slippage_model 时，slippage_bps 被忽略"""
        from aipha.execution.slippage import FixedBpsSlippageModel

        model = FixedBpsSlippageModel(bps=10.0)
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            slippage_bps=3.0,  # 应被 model 覆盖
            slippage_model=model,
        )
        gw.connect()
        order = make_order(qty=100, limit_price=10.0)
        gw.submit_order(order)
        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.5, low=9.8, close=10.2,
            volume=500000.0,
        )
        reports = gw.process_bar(bar)
        fill_price = float(reports[0].fill_price)
        # 10bps 应用，不是 3bps
        expected_10bps = 10.0 * (1 + 10.0 / 10000)
        expected_3bps = 10.0 * (1 + 3.0 / 10000)
        assert abs(fill_price - expected_10bps) < 0.001
        assert abs(fill_price - expected_3bps) > 0.0001


class TestFillModelSelection:
    """验证模拟盘显式 fill_model 配置生效。"""

    def test_next_bar_close_uses_close_price(self):
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            fill_model_name="next_bar_close",
            slippage_bps=0.0,
        )
        gw.connect()
        order = make_order(qty=100, order_type="MARKET", limit_price=None)
        gw.submit_order(order)
        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.5, low=9.8, close=10.2,
            volume=500000.0,
        )
        gw.process_bar(bar)
        assert order.avg_fill_price == Decimal("10.2")

    def test_vwap_uses_bar_vwap(self):
        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            fill_model_name="vwap",
            slippage_bps=0.0,
        )
        gw.connect()
        order = make_order(qty=100, order_type="MARKET", limit_price=None)
        gw.submit_order(order)
        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.5, low=9.8, close=10.2,
            volume=500000.0, vwap=10.12,
        )
        gw.process_bar(bar)
        assert order.avg_fill_price == Decimal("10.12")
