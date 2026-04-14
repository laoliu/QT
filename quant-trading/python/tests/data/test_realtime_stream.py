"""tests/data/test_realtime_stream.py - 实时行情流测试

覆盖 QuoteSnapshot / BarData / StreamManager / SyntheticRealtimeProvider / QuoteBuffer
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from aipha.data.realtime_stream import (
    BarData,
    SyntheticRealtimeProvider,
    QuoteBuffer,
    QuoteSnapshot,
    QuoteType,
    RealtimeStreamConfig,
    StreamManager,
    StreamStatus,
)


# 鈹€鈹€ QuoteSnapshot 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestQuoteSnapshot:
    def _make(self, **kw):
        defaults = dict(
            symbol="000001.SZ",
            quote_type=QuoteType.LEVEL1,
            timestamp=datetime(2023, 9, 1, 10, 0, 0),
            last_price=Decimal("10.50"),
        )
        defaults.update(kw)
        return QuoteSnapshot(**defaults)

    def test_creation(self):
        q = self._make()
        assert q.symbol == "000001.SZ"
        assert q.last_price == Decimal("10.50")

    def test_spread(self):
        q = self._make(
            bid_prices=[Decimal("10.49")],
            ask_prices=[Decimal("10.51")],
        )
        assert q.spread == Decimal("0.02")

    def test_spread_none(self):
        q = self._make()
        assert q.spread is None

    def test_mid_price(self):
        q = self._make(
            bid_prices=[Decimal("10.00")],
            ask_prices=[Decimal("10.10")],
        )
        assert q.mid_price == Decimal("10.05")

    def test_mid_price_none(self):
        q = self._make()
        assert q.mid_price is None

    def test_default_status(self):
        q = self._make()
        assert q.status == "trading"


# 鈹€鈹€ BarData 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestBarData:
    def test_creation(self):
        b = BarData(
            symbol="000001.SZ",
            interval="1m",
            timestamp=datetime(2023, 9, 1, 10, 1),
            open=Decimal("10.0"),
            high=Decimal("10.5"),
            low=Decimal("9.8"),
            close=Decimal("10.3"),
            volume=5000,
            amount=Decimal("51500"),
        )
        assert b.interval == "1m"
        assert b.vwap is None


# 鈹€鈹€ RealtimeStreamConfig 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestRealtimeStreamConfig:
    def test_defaults(self):
        c = RealtimeStreamConfig()
        assert c.reconnect_enabled is True
        assert c.reconnect_max_attempts == 10
        assert c.heartbeat_interval == 30.0

    def test_custom(self):
        c = RealtimeStreamConfig(reconnect_max_attempts=5)
        assert c.reconnect_max_attempts == 5


# 鈹€鈹€ QuoteBuffer 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestQuoteBuffer:
    def _quote(self, sym="000001.SZ", price="10.0"):
        return QuoteSnapshot(
            symbol=sym,
            quote_type=QuoteType.LEVEL1,
            timestamp=datetime.now(),
            last_price=Decimal(price),
        )

    def test_add_and_get_latest(self):
        buf = QuoteBuffer()
        q = self._quote()
        buf.add(q)
        assert buf.get_latest("000001.SZ") is q

    def test_latest_replaces(self):
        buf = QuoteBuffer()
        q1 = self._quote(price="10.0")
        q2 = self._quote(price="10.5")
        buf.add(q1)
        buf.add(q2)
        assert buf.get_latest("000001.SZ").last_price == Decimal("10.5")

    def test_get_latest_none(self):
        buf = QuoteBuffer()
        assert buf.get_latest("NOTHING") is None

    def test_history(self):
        buf = QuoteBuffer()
        for i in range(5):
            buf.add(self._quote(price=str(10 + i)))
        h = buf.get_history("000001.SZ", count=3)
        assert len(h) == 3

    def test_history_empty(self):
        buf = QuoteBuffer()
        assert buf.get_history("NOTHING") == []

    def test_overflow_trimmed(self):
        buf = QuoteBuffer(max_size=5)
        for i in range(10):
            buf.add(self._quote(price=str(i)))
        assert len(buf.get_history("000001.SZ", count=100)) == 5

    def test_clear_symbol(self):
        buf = QuoteBuffer()
        buf.add(self._quote("A"))
        buf.add(self._quote("B"))
        buf.clear("A")
        assert buf.get_latest("A") is None
        assert buf.get_latest("B") is not None

    def test_clear_all(self):
        buf = QuoteBuffer()
        buf.add(self._quote("A"))
        buf.add(self._quote("B"))
        buf.clear()
        assert buf.get_latest("A") is None
        assert buf.get_latest("B") is None


# 鈹€鈹€ SyntheticRealtimeProvider 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestSyntheticRealtimeProvider:
    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        mp = SyntheticRealtimeProvider()
        assert mp.get_status() == StreamStatus.DISCONNECTED
        await mp.connect()
        assert mp.get_status() == StreamStatus.CONNECTED
        await mp.disconnect()
        assert mp.get_status() == StreamStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe(self):
        mp = SyntheticRealtimeProvider()
        await mp.connect()
        await mp.subscribe(["000001.SZ"], [QuoteType.LEVEL1])
        assert "000001.SZ" in mp._subscribed
        await mp.unsubscribe(["000001.SZ"], [QuoteType.LEVEL1])
        assert "000001.SZ" not in mp._subscribed
        await mp.disconnect()


# 鈹€鈹€ StreamManager 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestStreamManager:
    def test_register_provider(self):
        mgr = StreamManager()
        mp = SyntheticRealtimeProvider()
        mgr.register_provider("test", mp, is_primary=True)
        assert mgr.get_primary_provider() is mp

    def test_no_provider(self):
        mgr = StreamManager()
        assert mgr.get_primary_provider() is None

    def test_initial_status(self):
        mgr = StreamManager()
        assert mgr.get_status() == StreamStatus.DISCONNECTED

    def test_subscriptions_empty(self):
        mgr = StreamManager()
        assert mgr.get_subscriptions() == {}

    @pytest.mark.asyncio
    async def test_start_stop(self):
        mgr = StreamManager()
        mp = SyntheticRealtimeProvider()
        mgr.register_provider("test", mp, is_primary=True)

        ok = await mgr.start()
        assert ok is True
        assert mgr.get_status() == StreamStatus.CONNECTED

        await mgr.stop()
        assert mgr.get_status() == StreamStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_start_no_provider(self):
        mgr = StreamManager()
        assert await mgr.start() is False

    @pytest.mark.asyncio
    async def test_subscribe(self):
        mgr = StreamManager()
        mp = SyntheticRealtimeProvider()
        mgr.register_provider("test", mp, is_primary=True)
        await mgr.start()

        ok = await mgr.subscribe(["000001.SZ"])
        assert ok is True
        subs = mgr.get_subscriptions()
        assert "000001.SZ" in subs

        await mgr.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        mgr = StreamManager()
        mp = SyntheticRealtimeProvider()
        mgr.register_provider("test", mp, is_primary=True)
        await mgr.start()

        await mgr.subscribe(["000001.SZ"])
        await mgr.unsubscribe(["000001.SZ"])
        assert "000001.SZ" not in mgr.get_subscriptions()

        await mgr.stop()

    def test_quote_callback(self):
        mgr = StreamManager()
        received = []
        mgr.on_quote(lambda q: received.append(q))
        q = QuoteSnapshot(
            symbol="X", quote_type=QuoteType.LEVEL1,
            timestamp=datetime.now(), last_price=Decimal("10"),
        )
        mgr._on_quote_received(q)
        assert len(received) == 1

    def test_bar_callback(self):
        mgr = StreamManager()
        received = []
        mgr.on_bar(lambda b: received.append(b))
        b = BarData(
            symbol="X", interval="1m", timestamp=datetime.now(),
            open=Decimal("10"), high=Decimal("11"), low=Decimal("9"),
            close=Decimal("10.5"), volume=100, amount=Decimal("1050"),
        )
        mgr._on_bar_received(b)
        assert len(received) == 1

    def test_status_callback(self):
        mgr = StreamManager()
        statuses = []
        mgr.on_status(lambda s, m: statuses.append(s))
        mgr._update_status(StreamStatus.CONNECTING)
        assert StreamStatus.CONNECTING in statuses
