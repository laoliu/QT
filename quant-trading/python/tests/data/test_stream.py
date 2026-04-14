"""实时数据流模拟模块测试"""

from datetime import date, datetime, timedelta
import time
import pytest

from aipha.data.stream import (
    Bar,
    Quote,
    BarFrequency,
    MarketEvent,
    DataStreamSimulator,
    HistoricalDataPlayer,
    QuoteGenerator,
    AsyncDataStreamSimulator,
    create_simulator_from_dict,
)


class TestBar:
    """Bar测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        bar = Bar(
            symbol="600519.SH",
            timestamp=datetime(2023, 1, 1, 9, 30),
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=10000,
        )

        assert bar.symbol == "600519.SH"
        assert bar.open == 100.0
        assert bar.close == 103.0

    def test_repr(self):
        """测试字符串表示"""
        bar = Bar(
            symbol="600519.SH",
            timestamp=datetime(2023, 1, 1, 9, 30),
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=10000,
        )

        repr_str = repr(bar)
        assert "600519.SH" in repr_str
        assert "103.00" in repr_str


class TestQuote:
    """Quote测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        quote = Quote(
            symbol="600519.SH",
            timestamp=datetime(2023, 1, 1, 9, 30),
            last_price=100.0,
            bid_price=99.9,
            ask_price=100.1,
            bid_volume=1000,
            ask_volume=2000,
        )

        assert quote.symbol == "600519.SH"
        assert quote.bid_price < quote.ask_price


class TestDataStreamSimulator:
    """DataStreamSimulator测试"""

    def _create_sample_bars(self, n: int = 10) -> list[Bar]:
        """创建示例K线"""
        bars = []
        base_time = datetime(2023, 1, 1, 9, 30)

        for i in range(n):
            bar = Bar(
                symbol="600519.SH",
                timestamp=base_time + timedelta(minutes=i),
                open=100.0 + i,
                high=105.0 + i,
                low=99.0 + i,
                close=103.0 + i,
                volume=10000 + i * 100,
            )
            bars.append(bar)

        return bars

    def test_basic_stream(self):
        """测试基本数据流"""
        bars = self._create_sample_bars(5)
        simulator = DataStreamSimulator(bars=bars)

        received = []
        simulator.on_bar(lambda bar: received.append(bar))

        simulator.start(speed=0, blocking=True)  # 无延迟

        assert len(received) == 5
        assert received[0].symbol == "600519.SH"

    def test_callback_order(self):
        """测试回调顺序"""
        bars = self._create_sample_bars(10)
        simulator = DataStreamSimulator(bars=bars)

        timestamps = []
        simulator.on_bar(lambda bar: timestamps.append(bar.timestamp))

        simulator.start(speed=0, blocking=True)

        # 应该按时间顺序
        assert timestamps == sorted(timestamps)

    def test_multiple_callbacks(self):
        """测试多个回调"""
        bars = self._create_sample_bars(5)
        simulator = DataStreamSimulator(bars=bars)

        count1 = [0]
        count2 = [0]

        simulator.on_bar(lambda bar: count1.__setitem__(0, count1[0] + 1))
        simulator.on_bar(lambda bar: count2.__setitem__(0, count2[0] + 1))

        simulator.start(speed=0, blocking=True)

        assert count1[0] == 5
        assert count2[0] == 5

    def test_stats(self):
        """测试统计"""
        bars = self._create_sample_bars(10)
        simulator = DataStreamSimulator(bars=bars)

        simulator.on_bar(lambda bar: None)
        simulator.start(speed=0, blocking=True)

        stats = simulator.stats
        assert stats["bars_sent"] == 10

    def test_empty_data(self):
        """测试空数据"""
        simulator = DataStreamSimulator()
        simulator.start(speed=0, blocking=True)

        assert simulator.stats["bars_sent"] == 0

    def test_is_running(self):
        """测试运行状态"""
        simulator = DataStreamSimulator()
        assert not simulator.is_running

    def test_set_speed(self):
        """测试设置速度"""
        simulator = DataStreamSimulator()
        simulator.set_speed(10.0)
        assert simulator._speed == 10.0


class TestHistoricalDataPlayer:
    """HistoricalDataPlayer测试"""

    def test_generate_bars(self):
        """测试生成K线"""
        data = {
            date(2023, 1, 1): {
                "600519.SH": {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 10000},
                "000858.SZ": {"open": 50, "high": 52, "low": 49, "close": 51, "volume": 5000},
            },
            date(2023, 1, 2): {
                "600519.SH": {"open": 103, "high": 108, "low": 102, "close": 106, "volume": 12000},
            },
        }

        player = HistoricalDataPlayer(data)
        bars = player.generate_bars()

        assert len(bars) == 3
        assert all(isinstance(b, Bar) for b in bars)

    def test_create_simulator(self):
        """测试创建模拟器"""
        data = {
            date(2023, 1, 1): {
                "600519.SH": {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 10000},
            },
        }

        player = HistoricalDataPlayer(data)
        simulator = player.create_simulator()

        assert isinstance(simulator, DataStreamSimulator)

    def test_incomplete_data(self):
        """测试不完整数据被跳过"""
        data = {
            date(2023, 1, 1): {
                "600519.SH": {"open": 100, "close": 103},  # 缺少字段
                "000858.SZ": {"open": 50, "high": 52, "low": 49, "close": 51, "volume": 5000},
            },
        }

        player = HistoricalDataPlayer(data)
        bars = player.generate_bars()

        assert len(bars) == 1
        assert bars[0].symbol == "000858.SZ"


class TestQuoteGenerator:
    """QuoteGenerator测试"""

    def test_from_bar(self):
        """测试从K线生成盘口"""
        bar = Bar(
            symbol="600519.SH",
            timestamp=datetime(2023, 1, 1, 9, 30),
            open=100.0,
            high=105.0,
            low=99.0,
            close=100.0,
            volume=10000,
        )

        generator = QuoteGenerator(spread_pct=0.002)
        quote = generator.from_bar(bar)

        assert quote.symbol == bar.symbol
        assert quote.last_price == bar.close
        assert quote.bid_price < quote.ask_price
        assert quote.bid_price <= bar.close
        assert quote.ask_price >= bar.close

    def test_tick_alignment(self):
        """测试价格对齐到tick"""
        bar = Bar(
            symbol="600519.SH",
            timestamp=datetime(2023, 1, 1, 9, 30),
            open=100.0,
            high=105.0,
            low=99.0,
            close=100.123,  # 不是整tick
            volume=10000,
        )

        generator = QuoteGenerator(tick_size=0.01)
        quote = generator.from_bar(bar)

        # 应该对齐到0.01 (使用approx处理浮点精度)
        assert quote.bid_price == pytest.approx(round(quote.bid_price, 2), rel=1e-9)
        assert quote.ask_price == pytest.approx(round(quote.ask_price, 2), rel=1e-9)


class TestAsyncDataStreamSimulator:
    """AsyncDataStreamSimulator测试"""

    def _create_sample_bars(self, n: int = 5) -> list[Bar]:
        """创建示例K线"""
        bars = []
        base_time = datetime(2023, 1, 1, 9, 30)

        for i in range(n):
            bar = Bar(
                symbol="600519.SH",
                timestamp=base_time + timedelta(minutes=i),
                open=100.0 + i,
                high=105.0 + i,
                low=99.0 + i,
                close=103.0 + i,
                volume=10000,
            )
            bars.append(bar)

        return bars

    @pytest.mark.asyncio
    async def test_async_stream(self):
        """测试异步数据流"""
        bars = self._create_sample_bars(5)
        simulator = AsyncDataStreamSimulator(bars=bars)

        received = []
        simulator.on_bar(lambda bar: received.append(bar))

        await simulator.start(speed=0)

        assert len(received) == 5

    @pytest.mark.asyncio
    async def test_async_callback(self):
        """测试异步回调"""
        bars = self._create_sample_bars(3)
        simulator = AsyncDataStreamSimulator(bars=bars)

        received = []

        async def async_handler(bar: Bar):
            received.append(bar)

        simulator.on_bar(async_handler)
        await simulator.start(speed=0)

        assert len(received) == 3


class TestConvenienceFunction:
    """便捷函数测试"""

    def test_create_simulator_from_dict(self):
        """测试create_simulator_from_dict"""
        data = {
            date(2023, 1, 1): {
                "600519.SH": {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 10000},
            },
        }

        simulator = create_simulator_from_dict(data, speed=10)

        assert isinstance(simulator, DataStreamSimulator)
        assert simulator._speed == 10


class TestMarketEvent:
    """MarketEvent测试"""

    def test_event_types(self):
        """测试事件类型"""
        event = MarketEvent("bar", datetime.now(), {"symbol": "600519.SH"})

        assert event.event_type == "bar"
        assert event.data["symbol"] == "600519.SH"
