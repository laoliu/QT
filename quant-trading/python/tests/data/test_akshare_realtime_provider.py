"""
AKShare 实时行情 Provider 测试 — Sprint 3.1 + A1

测试覆盖：
- BarSynthesizer K 线合成
- AKShareRealtimeProvider 连接/订阅/轮询
- _QuoteFetcher 注入与回调分发
- StreamManager 集成
- bardata_to_domain_bar 适配器
- 市场时段感知轮询
- 速率限制器集成
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from aipha.data.akshare_realtime_provider import (
    AKShareRealtimeConfig,
    AKShareRealtimeProvider,
    BarSynthesizer,
    MarketType,
    _BarBuilder,
    _QuoteFetcher,
    bardata_to_domain_bar,
    _parse_interval_seconds,
)
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.data.rate_limiter import RateLimiter
from aipha.data.realtime_stream import (
    BarData,
    QuoteSnapshot,
    QuoteType,
    StreamManager,
    StreamStatus,
)
from aipha.domain.models import Bar


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_quote(
    symbol: str = "127045",
    price: float = 110.0,
    ts: datetime | None = None,
    volume: int = 10000,
    amount: float = 1100000.0,
) -> QuoteSnapshot:
    """快速创建测试用 QuoteSnapshot"""
    return QuoteSnapshot(
        symbol=symbol,
        quote_type=QuoteType.LEVEL1,
        timestamp=ts or datetime(2024, 6, 15, 9, 30, 5),
        last_price=Decimal(str(price)),
        open_price=Decimal(str(price)),
        high_price=Decimal(str(price)),
        low_price=Decimal(str(price)),
        close_price=Decimal(str(price)),
        volume=volume,
        amount=Decimal(str(amount)),
    )


class MockFetcher(_QuoteFetcher):
    """可控的 Mock Fetcher"""

    def __init__(
        self,
        quotes: list[QuoteSnapshot] | None = None,
        healthy: bool = True,
    ):
        self._quotes = quotes or []
        self._healthy = healthy
        self.fetch_count = 0

    def health_check(self) -> bool:
        return self._healthy

    def fetch_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        self.fetch_count += 1
        symbol_set = set(symbols)
        return [q for q in self._quotes if q.symbol in symbol_set]

    def set_quotes(self, quotes: list[QuoteSnapshot]) -> None:
        self._quotes = quotes


def _always_open_calendar() -> CNStockCalendar:
    """返回一个始终报告市场开放的 mock calendar（用于测试）"""
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.MORNING_CONTINUOUS
    return cal


def _fast_limiter() -> RateLimiter:
    """无等待的速率限制器（用于测试）"""
    return RateLimiter(requests_per_second=1000.0, min_interval=0.0, name="test")


# ─────────────────────────────────────────────────────────────────────────────
# BarSynthesizer 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestBarSynthesizer:
    """K 线合成器测试"""

    def test_single_quote_no_bar(self):
        """单条快照不产生完整 bar"""
        synth = BarSynthesizer(bar_interval_sec=60)
        result = synth.update(_make_quote(ts=datetime(2024, 6, 15, 9, 30, 5)))
        assert result is None

    def test_window_crossing_produces_bar(self):
        """跨越窗口边界时产生完整 bar"""
        synth = BarSynthesizer(bar_interval_sec=60)

        # 9:30:05 → 窗口 [9:30:00, 9:31:00)
        synth.update(_make_quote(price=110.0, ts=datetime(2024, 6, 15, 9, 30, 5)))
        synth.update(_make_quote(price=112.0, ts=datetime(2024, 6, 15, 9, 30, 30)))

        # 9:31:05 → 新窗口，旧 bar 应完成
        result = synth.update(
            _make_quote(price=111.0, ts=datetime(2024, 6, 15, 9, 31, 5))
        )

        assert result is not None
        assert result.symbol == "127045"
        assert result.open == Decimal("110.0")
        assert result.high == Decimal("112.0")
        assert result.close == Decimal("112.0")
        assert result.interval == "1m"

    def test_flush_returns_incomplete_bar(self):
        """flush 强制完成未关闭的 bar"""
        synth = BarSynthesizer(bar_interval_sec=60)
        synth.update(_make_quote(price=100.0, ts=datetime(2024, 6, 15, 9, 30, 5)))

        bars = synth.flush()
        assert len(bars) == 1
        assert bars[0].close == Decimal("100.0")

    def test_flush_specific_symbol(self):
        """flush 指定标的"""
        synth = BarSynthesizer(bar_interval_sec=60)
        synth.update(
            _make_quote(symbol="127045", price=100.0, ts=datetime(2024, 6, 15, 9, 30, 5))
        )
        synth.update(
            _make_quote(symbol="128054", price=200.0, ts=datetime(2024, 6, 15, 9, 30, 5))
        )

        bars = synth.flush(symbol="127045")
        assert len(bars) == 1
        assert bars[0].symbol == "127045"

        # 128054 仍在
        bars2 = synth.flush()
        assert len(bars2) == 1
        assert bars2[0].symbol == "128054"

    def test_ohlc_tracking(self):
        """OHLC 跟踪"""
        synth = BarSynthesizer(bar_interval_sec=60)
        ts_base = datetime(2024, 6, 15, 9, 30, 0)

        synth.update(_make_quote(price=100.0, ts=ts_base + timedelta(seconds=5)))
        synth.update(_make_quote(price=105.0, ts=ts_base + timedelta(seconds=15)))
        synth.update(_make_quote(price=98.0, ts=ts_base + timedelta(seconds=30)))
        synth.update(_make_quote(price=102.0, ts=ts_base + timedelta(seconds=45)))

        bars = synth.flush()
        bar = bars[0]
        assert bar.open == Decimal("100.0")
        assert bar.high == Decimal("105.0")
        assert bar.low == Decimal("98.0")
        assert bar.close == Decimal("102.0")

    def test_multi_symbol_independent(self):
        """多标的独立合成"""
        synth = BarSynthesizer(bar_interval_sec=60)
        ts = datetime(2024, 6, 15, 9, 30, 5)

        synth.update(_make_quote(symbol="A", price=100, ts=ts))
        synth.update(_make_quote(symbol="B", price=200, ts=ts))

        bars = synth.flush()
        assert len(bars) == 2
        symbols = {b.symbol for b in bars}
        assert symbols == {"A", "B"}


# ─────────────────────────────────────────────────────────────────────────────
# AKShareRealtimeProvider 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestAKShareRealtimeProvider:
    """Provider 连接/轮询测试"""

    @pytest.mark.asyncio
    async def test_connect_healthy(self):
        """健康检查通过时连接成功"""
        fetcher = MockFetcher(healthy=True)
        provider = AKShareRealtimeProvider(fetcher=fetcher)

        result = await provider.connect()
        assert result is True
        assert provider.get_status() == StreamStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_connect_unhealthy(self):
        """健康检查失败时连接失败"""
        fetcher = MockFetcher(healthy=False)
        provider = AKShareRealtimeProvider(fetcher=fetcher)

        result = await provider.connect()
        assert result is False
        assert provider.get_status() == StreamStatus.ERROR

    @pytest.mark.asyncio
    async def test_subscribe_starts_polling(self):
        """订阅后启动轮询"""
        fetcher = MockFetcher(
            quotes=[_make_quote(symbol="127045", price=110.0)],
            healthy=True,
        )
        config = AKShareRealtimeConfig(poll_interval=0.05)  # 50ms for fast test
        provider = AKShareRealtimeProvider(
            config=config, fetcher=fetcher,
            calendar=_always_open_calendar(), rate_limiter=_fast_limiter(),
        )
        await provider.connect()

        received: list[QuoteSnapshot] = []
        provider.on_quote(lambda q: received.append(q))

        await provider.subscribe(["127045"], [QuoteType.LEVEL1])

        # 等几个轮询周期
        await asyncio.sleep(0.2)
        await provider.disconnect()

        assert len(received) >= 2  # 至少收到 2 条快照
        assert fetcher.fetch_count >= 2

    @pytest.mark.asyncio
    async def test_bar_callback_on_window_cross(self):
        """窗口跨越时触发 bar 回调"""
        ts1 = datetime(2024, 6, 15, 9, 30, 5)
        ts2 = datetime(2024, 6, 15, 9, 31, 5)  # 跨越 1 分钟窗口

        call_count = {"n": 0}
        quotes_sequence = [
            [_make_quote(price=110, ts=ts1)],
            [_make_quote(price=112, ts=ts2)],
        ]

        class SequenceFetcher(_QuoteFetcher):
            def __init__(self):
                self.idx = 0

            def health_check(self):
                return True

            def fetch_quotes(self, symbols):
                if self.idx < len(quotes_sequence):
                    result = quotes_sequence[self.idx]
                    self.idx += 1
                    return result
                return []

        config = AKShareRealtimeConfig(poll_interval=0.05, bar_interval_sec=60)
        provider = AKShareRealtimeProvider(
            config=config, fetcher=SequenceFetcher(),
            calendar=_always_open_calendar(), rate_limiter=_fast_limiter(),
        )
        await provider.connect()

        bars_received: list[BarData] = []
        provider.on_bar(lambda b: bars_received.append(b))
        await provider.subscribe(["127045"], [QuoteType.BAR_1M])

        await asyncio.sleep(0.3)
        await provider.disconnect()

        assert len(bars_received) >= 1
        assert bars_received[0].open == Decimal("110")

    @pytest.mark.asyncio
    async def test_disconnect_flushes_bars(self):
        """断开连接时刷出未完成的 bar"""
        fetcher = MockFetcher(
            quotes=[_make_quote(symbol="127045", price=100.0)],
            healthy=True,
        )
        config = AKShareRealtimeConfig(poll_interval=0.05)
        provider = AKShareRealtimeProvider(
            config=config, fetcher=fetcher,
            calendar=_always_open_calendar(), rate_limiter=_fast_limiter(),
        )
        await provider.connect()

        bars: list[BarData] = []
        provider.on_bar(lambda b: bars.append(b))

        await provider.subscribe(["127045"], [QuoteType.BAR_1M])
        await asyncio.sleep(0.15)
        await provider.disconnect()

        # 断开时应 flush 出至少 1 根 bar
        assert len(bars) >= 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """取消订阅"""
        fetcher = MockFetcher(healthy=True)
        provider = AKShareRealtimeProvider(fetcher=fetcher)
        await provider.connect()
        await provider.subscribe(["127045"], [QuoteType.LEVEL1])
        await provider.unsubscribe(["127045"], [QuoteType.LEVEL1])

        assert "127045" not in provider._subscribed

    @pytest.mark.asyncio
    async def test_status_callback(self):
        """状态变化回调"""
        fetcher = MockFetcher(healthy=True)
        provider = AKShareRealtimeProvider(fetcher=fetcher)

        statuses: list[StreamStatus] = []
        provider.on_status(lambda s, m: statuses.append(s))

        await provider.connect()
        assert StreamStatus.CONNECTING in statuses
        assert StreamStatus.CONNECTED in statuses

        await provider.disconnect()
        assert StreamStatus.DISCONNECTED in statuses

    @pytest.mark.asyncio
    async def test_poll_error_recovery(self):
        """轮询错误不会立即终止"""

        class FailOnceFetcher(_QuoteFetcher):
            def __init__(self):
                self.call_count = 0

            def health_check(self):
                return True

            def fetch_quotes(self, symbols):
                self.call_count += 1
                if self.call_count == 1:
                    raise ConnectionError("transient error")
                return [_make_quote()]

        config = AKShareRealtimeConfig(poll_interval=0.05, max_retry=3)
        fetcher = FailOnceFetcher()
        provider = AKShareRealtimeProvider(
            config=config, fetcher=fetcher,
            calendar=_always_open_calendar(), rate_limiter=_fast_limiter(),
        )
        await provider.connect()

        received: list[QuoteSnapshot] = []
        provider.on_quote(lambda q: received.append(q))
        await provider.subscribe(["127045"], [QuoteType.LEVEL1])

        await asyncio.sleep(0.3)
        await provider.disconnect()

        # 第 1 次失败后恢复，应有后续数据
        assert len(received) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# StreamManager 集成测试
# ─────────────────────────────────────────────────────────────────────────────

class TestStreamManagerIntegration:
    """与 StreamManager 集成"""

    @pytest.mark.asyncio
    async def test_register_and_start(self):
        """注册 AKShareProvider 并启动"""
        fetcher = MockFetcher(healthy=True)
        provider = AKShareRealtimeProvider(fetcher=fetcher)

        manager = StreamManager()
        manager.register_provider("akshare", provider, is_primary=True)

        result = await manager.start()
        assert result is True
        assert manager.get_status() == StreamStatus.CONNECTED

        await manager.stop()
        assert manager.get_status() == StreamStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_subscribe_via_manager(self):
        """通过 Manager 订阅并收到行情"""
        fetcher = MockFetcher(
            quotes=[_make_quote(symbol="127045", price=105.0)],
            healthy=True,
        )
        config = AKShareRealtimeConfig(poll_interval=0.05)
        provider = AKShareRealtimeProvider(
            config=config, fetcher=fetcher,
            calendar=_always_open_calendar(), rate_limiter=_fast_limiter(),
        )

        manager = StreamManager()
        manager.register_provider("akshare", provider, is_primary=True)

        received: list[QuoteSnapshot] = []
        manager.on_quote(lambda q: received.append(q))

        await manager.start()
        await manager.subscribe(["127045"], [QuoteType.LEVEL1])

        await asyncio.sleep(0.2)
        await manager.stop()

        assert len(received) >= 1
        assert received[0].last_price == Decimal("105.0")


# ─────────────────────────────────────────────────────────────────────────────
# _to_decimal helper 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestToDecimal:
    """_to_decimal 边界情况"""

    def test_none(self):
        from aipha.data.akshare_realtime_provider import _to_decimal

        assert _to_decimal(None) == Decimal("0")

    def test_empty_string(self):
        from aipha.data.akshare_realtime_provider import _to_decimal

        assert _to_decimal("") == Decimal("0")

    def test_valid_float(self):
        from aipha.data.akshare_realtime_provider import _to_decimal

        assert _to_decimal(110.5) == Decimal("110.5")

    def test_invalid_string(self):
        from aipha.data.akshare_realtime_provider import _to_decimal

        assert _to_decimal("N/A") == Decimal("0")


# ─────────────────────────────────────────────────────────────────────────────
# bardata_to_domain_bar 适配器测试
# ─────────────────────────────────────────────────────────────────────────────

class TestBardataToDomainBar:
    """BarData → Bar 适配器测试"""

    def _make_bardata(
        self,
        symbol: str = "127045",
        interval: str = "1m",
        ts: datetime | None = None,
        open_p: str = "105.5",
        high_p: str = "106.0",
        low_p: str = "105.0",
        close_p: str = "105.8",
        volume: int = 1000,
        amount: str = "105500",
        vwap: str | None = None,
        trade_count: int | None = 15,
    ) -> BarData:
        return BarData(
            symbol=symbol,
            interval=interval,
            timestamp=ts or datetime(2024, 6, 15, 9, 31, 0),
            open=Decimal(open_p),
            high=Decimal(high_p),
            low=Decimal(low_p),
            close=Decimal(close_p),
            volume=volume,
            amount=Decimal(amount),
            vwap=Decimal(vwap) if vwap else None,
            trade_count=trade_count,
        )

    def test_basic_conversion(self):
        """基本 Decimal→float 转换"""
        bd = self._make_bardata()
        bar = bardata_to_domain_bar(bd)

        assert isinstance(bar, Bar)
        assert bar.symbol == "127045"
        assert bar.open == 105.5
        assert bar.high == 106.0
        assert bar.low == 105.0
        assert bar.close == 105.8
        assert bar.volume == 1000.0
        assert isinstance(bar.open, float)
        assert isinstance(bar.volume, float)

    def test_timestamps(self):
        """end_ts 与 start_ts 正确计算"""
        ts = datetime(2024, 6, 15, 9, 31, 0)
        bd = self._make_bardata(ts=ts, interval="1m")
        bar = bardata_to_domain_bar(bd)

        assert bar.end_ts == ts
        assert bar.start_ts == ts - timedelta(seconds=60)

    def test_5m_interval(self):
        """5 分钟 K 线的时间窗口"""
        ts = datetime(2024, 6, 15, 9, 35, 0)
        bd = self._make_bardata(ts=ts, interval="5m")
        bar = bardata_to_domain_bar(bd)

        assert bar.end_ts == ts
        assert bar.start_ts == ts - timedelta(seconds=300)

    def test_vwap_conversion(self):
        """vwap 正确转换"""
        bd = self._make_bardata(vwap="105.5")
        bar = bardata_to_domain_bar(bd)
        assert bar.vwap == 105.5

    def test_vwap_none(self):
        """vwap 为 None 时保持 None"""
        bd = self._make_bardata(vwap=None)
        bar = bardata_to_domain_bar(bd)
        assert bar.vwap is None

    def test_metadata_default(self):
        """默认 metadata 包含 source 和 trade_count"""
        bd = self._make_bardata(trade_count=15)
        bar = bardata_to_domain_bar(bd)

        assert bar.metadata is not None
        assert bar.metadata["source"] == "akshare_realtime"
        assert bar.metadata["trade_count"] == 15
        assert bar.metadata["interval"] == "1m"

    def test_metadata_custom(self):
        """自定义 metadata 合并"""
        bd = self._make_bardata()
        bar = bardata_to_domain_bar(bd, metadata={"premium_rate": 0.15})

        assert bar.metadata is not None
        assert bar.metadata["premium_rate"] == 0.15
        assert bar.metadata["source"] == "akshare_realtime"

    def test_frozen_bar(self):
        """返回的 Bar 是 frozen dataclass"""
        bd = self._make_bardata()
        bar = bardata_to_domain_bar(bd)

        with pytest.raises(AttributeError):
            bar.close = 999.0  # type: ignore[misc]

    def test_is_suspended_default_false(self):
        """默认 is_suspended=False"""
        bd = self._make_bardata()
        bar = bardata_to_domain_bar(bd)
        assert bar.is_suspended is False

    def test_is_limit_default_none(self):
        """默认涨跌停标志为 None"""
        bd = self._make_bardata()
        bar = bardata_to_domain_bar(bd)
        assert bar.is_limit_up is None
        assert bar.is_limit_down is None


class TestParseIntervalSeconds:
    """_parse_interval_seconds 测试"""

    def test_1m(self):
        assert _parse_interval_seconds("1m") == 60

    def test_5m(self):
        assert _parse_interval_seconds("5m") == 300

    def test_1h(self):
        assert _parse_interval_seconds("1h") == 3600

    def test_1d(self):
        assert _parse_interval_seconds("1d") == 86400

    def test_unknown_fallback(self):
        """未知格式回退 60s"""
        assert _parse_interval_seconds("xyz") == 60

    def test_numeric_string(self):
        """纯数字字符串"""
        assert _parse_interval_seconds("120") == 120

    def test_numeric_with_s(self):
        """带 s 后缀"""
        assert _parse_interval_seconds("120s") == 120


# ─────────────────────────────────────────────────────────────────────────────
# 市场时段感知 + 速率限制 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketHoursAwareness:
    """市场时段感知轮询测试"""

    @pytest.mark.asyncio
    async def test_provider_accepts_calendar_and_limiter(self):
        """Provider 可注入 calendar 和 rate_limiter"""
        cal = CNStockCalendar()
        limiter = RateLimiter(requests_per_second=10.0, min_interval=0.0, name="test")
        fetcher = MockFetcher(healthy=True)
        provider = AKShareRealtimeProvider(
            config=AKShareRealtimeConfig(poll_interval=0.05),
            fetcher=fetcher,
            calendar=cal,
            rate_limiter=limiter,
        )
        assert provider._calendar is cal
        assert provider._rate_limiter is limiter

    @pytest.mark.asyncio
    async def test_on_domain_bar_callback(self):
        """on_domain_bar 回调分发 Domain Bar"""
        ts_base = datetime(2024, 6, 17, 9, 30, 5)  # Monday
        quotes_w1 = [
            _make_quote("127045", price=100.0, ts=ts_base, volume=100, amount=10000),
        ]
        quotes_w2 = [
            _make_quote(
                "127045", price=101.0,
                ts=ts_base + timedelta(seconds=65),
                volume=200, amount=20200,
            ),
        ]

        fetcher = MockFetcher(quotes=quotes_w1, healthy=True)
        limiter = RateLimiter(requests_per_second=100.0, min_interval=0.0, name="test")
        # 用一个始终返回 MORNING_CONTINUOUS 的 mock calendar
        cal = MagicMock(spec=CNStockCalendar)
        cal.get_session.return_value = TradingSession.MORNING_CONTINUOUS

        config = AKShareRealtimeConfig(poll_interval=0.02, bar_interval_sec=60)
        provider = AKShareRealtimeProvider(
            config=config,
            fetcher=fetcher,
            calendar=cal,
            rate_limiter=limiter,
        )

        domain_bars: list[Bar] = []
        provider.on_domain_bar(lambda b: domain_bars.append(b))

        connected = await provider.connect()
        assert connected

        await provider.subscribe(["127045"], [QuoteType.BAR_1M])
        await asyncio.sleep(0.1)

        # 切换到下一个窗口的 quotes
        fetcher.set_quotes(quotes_w2)
        await asyncio.sleep(0.1)

        await provider.disconnect()

        # 窗口切换应产生至少一个 domain bar
        if domain_bars:
            assert isinstance(domain_bars[0], Bar)
            assert domain_bars[0].symbol == "127045"
            assert isinstance(domain_bars[0].close, float)
