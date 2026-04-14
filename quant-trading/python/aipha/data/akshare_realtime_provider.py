"""
AKShare 实时行情数据源

基于 AKShare 的轮询式实时行情接口。

实现 RealtimeStreamProvider ABC，通过定时轮询
ak.bond_zh_cov_spot_em()（可转债）或 ak.stock_zh_a_spot_em()（A 股）
获取行情快照，并合成 1 分钟 K 线。

特点：
- 轮询间隔可配置（默认 3s）
- 自动合成 1min Bar（按 end_ts 切分）
- 支持 A 股与可转债两种标的
- 可与 StreamManager 集成实现多数据源管理
- 市场时段感知：非交易时段自动暂停轮询
- 集成速率限制器，避免接口封禁

限制：
- 非 WebSocket 推送，延迟取决于轮询间隔
- 免费接口有频率限制，建议 ≥3s 间隔
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.data.rate_limiter import RateLimiter, get_akshare_limiter
from aipha.data.realtime_stream import (
    BarCallback,
    BarData,
    QuoteCallback,
    QuoteSnapshot,
    QuoteType,
    RealtimeStreamProvider,
    StatusCallback,
    StreamStatus,
)
from aipha.domain.models import Bar


logger = logging.getLogger(__name__)


class MarketType(str, Enum):
    """标的类型"""

    A_STOCK = "a_stock"          # A 股
    CB = "convertible_bond"      # 可转债
    MULTI_ASSET = "multi_asset"  # 多资产组合（plan_a_5comp 使用）


@dataclass
class AKShareRealtimeConfig:
    """AKShare 实时行情配置"""

    market_type: MarketType = MarketType.CB
    poll_interval: float = 3.0        # 轮询间隔（秒）
    bar_interval_sec: int = 60        # K 线周期（秒），默认 1min
    max_retry: int = 3                # 单次轮询失败最大重试次数
    retry_delay: float = 1.0          # 重试间隔（秒）


# ─────────────────────────────────────────────────────────────────────────────
# Bar 合成器
# ─────────────────────────────────────────────────────────────────────────────

class BarSynthesizer:
    """从逐笔快照合成 K 线

    按 bar_interval_sec 时间窗口聚合 QuoteSnapshot，
    生成 BarData。
    """

    def __init__(self, bar_interval_sec: int = 60):
        self._interval = bar_interval_sec
        # symbol → 当前构建中的 bar 状态
        self._building: dict[str, _BarBuilder] = {}

    def update(self, quote: QuoteSnapshot) -> BarData | None:
        """更新快照，若窗口完成则返回 BarData

        Returns:
            完成的 BarData 或 None（仍在窗口内）
        """
        symbol = quote.symbol
        ts = quote.timestamp
        # 当前窗口起始时间（按 interval 对齐）
        window_start = self._align_ts(ts)
        window_end = window_start + timedelta(seconds=self._interval)

        builder = self._building.get(symbol)

        # 如果没有 builder 或窗口已过期→完成旧 bar
        completed: BarData | None = None
        if builder is not None and builder.window_start != window_start:
            completed = builder.finish()
            builder = None

        if builder is None:
            builder = _BarBuilder(
                symbol=symbol,
                window_start=window_start,
                window_end=window_end,
            )
            self._building[symbol] = builder

        builder.update(quote)
        return completed

    def flush(self, symbol: str | None = None) -> list[BarData]:
        """强制完成当前窗口（用于收盘/断线时）"""
        result: list[BarData] = []
        if symbol:
            builder = self._building.pop(symbol, None)
            if builder:
                result.append(builder.finish())
        else:
            for sym in list(self._building):
                builder = self._building.pop(sym)
                result.append(builder.finish())
        return result

    def _align_ts(self, ts: datetime) -> datetime:
        """将时间戳对齐到 interval 边界"""
        epoch_sec = int(ts.timestamp())
        aligned_sec = (epoch_sec // self._interval) * self._interval
        return datetime.fromtimestamp(aligned_sec)


@dataclass
class _BarBuilder:
    """单根 Bar 构建器"""

    symbol: str
    window_start: datetime
    window_end: datetime
    open_price: Decimal | None = None
    high_price: Decimal = Decimal("-Infinity")
    low_price: Decimal = Decimal("Infinity")
    close_price: Decimal = Decimal("0")
    volume: int = 0
    amount: Decimal = Decimal("0")
    _prev_volume: int | None = None   # 用于增量计算
    _prev_amount: Decimal | None = None
    tick_count: int = 0

    def update(self, quote: QuoteSnapshot) -> None:
        """用新的行情快照更新当前Bar聚合数据。"""
        price = quote.last_price
        if self.open_price is None:
            self.open_price = price
        if price > self.high_price:
            self.high_price = price
        if price < self.low_price:
            self.low_price = price
        self.close_price = price

        # 累计成交量/金额（AKShare 返回的是累计值，需增量处理）
        if self._prev_volume is not None:
            delta_vol = quote.volume - self._prev_volume
            delta_amt = quote.amount - self._prev_amount  # type: ignore[operator]
            if delta_vol > 0:
                self.volume += delta_vol
                self.amount += delta_amt
        else:
            # 首次：无法区分增量，先不计入
            pass
        self._prev_volume = quote.volume
        self._prev_amount = quote.amount
        self.tick_count += 1

    def finish(self) -> BarData:
        """完成当前Bar聚合并返回BarData对象。"""
        vwap = None
        if self.volume > 0:
            vwap = self.amount / Decimal(self.volume)

        return BarData(
            symbol=self.symbol,
            interval="1m",
            timestamp=self.window_end,
            open=self.open_price or self.close_price,
            high=self.high_price if self.high_price != Decimal("-Infinity") else self.close_price,
            low=self.low_price if self.low_price != Decimal("Infinity") else self.close_price,
            close=self.close_price,
            volume=self.volume,
            amount=self.amount,
            vwap=vwap,
            trade_count=self.tick_count,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AKShare Realtime Provider
# ─────────────────────────────────────────────────────────────────────────────

class AKShareRealtimeProvider(RealtimeStreamProvider):
    """AKShare 轮询式实时行情源

    通过定时调用 AKShare HTTP 接口获取行情快照。

    特性：
    - 市场时段感知：仅在交易时段（连续竞价+集合竞价）内轮询
    - 集成速率限制：通过 RateLimiter 控制调用频率
    - 自动合成 1min Bar 并转为领域模型 Bar

    Usage::

        provider = AKShareRealtimeProvider(config=AKShareRealtimeConfig(
            market_type=MarketType.CB,
            poll_interval=3.0,
        ))

        manager = StreamManager()
        manager.register_provider("akshare", provider, is_primary=True)
        await manager.start()
        await manager.subscribe(["127045", "128054"])
    """

    def __init__(
        self,
        config: AKShareRealtimeConfig | None = None,
        *,
        fetcher: _QuoteFetcher | None = None,
        calendar: CNStockCalendar | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self._config = config or AKShareRealtimeConfig()
        self._status = StreamStatus.DISCONNECTED
        self._subscribed: dict[str, set[QuoteType]] = {}

        self._quote_callback: QuoteCallback | None = None
        self._bar_callback: BarCallback | None = None
        self._bar_domain_callback: Any | None = None  # Callable[[Bar], None]
        self._status_callback: StatusCallback | None = None

        self._poll_task: asyncio.Task | None = None
        self._bar_synth = BarSynthesizer(bar_interval_sec=self._config.bar_interval_sec)

        # 允许注入自定义 fetcher（测试/mock 友好）
        self._fetcher = fetcher or _AKShareDefaultFetcher(self._config.market_type)

        # 市场时段感知（可注入，默认使用 fallback 日历）
        self._calendar = calendar or CNStockCalendar()

        # 速率限制器（可注入，默认使用 AKShare 限流配置）
        self._rate_limiter = rate_limiter or get_akshare_limiter()

    # ── RealtimeStreamProvider ABC ──

    async def connect(self) -> bool:
        """连接 AKShare 实时数据源，验证可用性并更新状态。"""
        try:
            self._status = StreamStatus.CONNECTING
            self._notify_status(StreamStatus.CONNECTING, "connecting")

            # 验证 akshare 可用
            ok = await asyncio.get_event_loop().run_in_executor(
                None, self._fetcher.health_check
            )
            if not ok:
                self._status = StreamStatus.ERROR
                self._notify_status(StreamStatus.ERROR, "akshare health check failed")
                return False

            self._status = StreamStatus.CONNECTED
            self._notify_status(StreamStatus.CONNECTED, "connected")
            logger.info("AKShare realtime provider connected")
            return True

        except Exception as e:
            logger.error(f"AKShare connect failed: {e}")
            self._status = StreamStatus.ERROR
            self._notify_status(StreamStatus.ERROR, str(e))
            return False

    async def disconnect(self) -> None:
        """断开连接，取消轮询任务并刷出未完成的 bars。"""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        # 刷出未完成的 bars
        remaining = self._bar_synth.flush()
        for bar in remaining:
            self._dispatch_bar(bar)

        self._subscribed.clear()
        self._status = StreamStatus.DISCONNECTED
        self._notify_status(StreamStatus.DISCONNECTED, "disconnected")
        logger.info("AKShare realtime provider disconnected")

    async def subscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType],
    ) -> bool:
        """订阅指定标的的行情推送，自动启动轮询循环。"""
        for s in symbols:
            if s not in self._subscribed:
                self._subscribed[s] = set()
            self._subscribed[s].update(quote_types)

        # 启动轮询
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info(f"Subscribed: {symbols}")
        return True

    async def unsubscribe(
        self,
        symbols: list[str],
        quote_types: list[QuoteType],
    ) -> bool:
        """取消订阅指定标的。"""
        for s in symbols:
            if s in self._subscribed:
                self._subscribed[s] -= set(quote_types)
                if not self._subscribed[s]:
                    del self._subscribed[s]
        return True

    def on_quote(self, callback: QuoteCallback) -> None:
        """注册行情报价回调。"""
        self._quote_callback = callback

    def on_bar(self, callback: BarCallback) -> None:
        """注册 K 线回调（原始 BarData）。"""
        self._bar_callback = callback

    def on_domain_bar(self, callback: Any) -> None:
        """注册领域模型 Bar 回调（自动从 BarData 转换）

        与 on_bar 的区别：on_bar 分发原始 BarData（Decimal），
        on_domain_bar 分发转换后的 Bar（float），适合直接对接 LiveRunner。

        Args:
            callback: Callable[[Bar], None]
        """
        self._bar_domain_callback = callback

    def on_status(self, callback: StatusCallback) -> None:
        """注册连接状态变更回调。"""
        self._status_callback = callback

    def get_status(self) -> StreamStatus:
        """返回当前连接状态。"""
        return self._status

    # ── 内部轮询循环 ──

    async def _poll_loop(self) -> None:
        """主轮询循环（含市场时段判断与速率限制）"""
        logger.info(f"Poll loop started, interval={self._config.poll_interval}s")
        consecutive_errors = 0

        while self._subscribed and self._status == StreamStatus.CONNECTED:
            try:
                # ── 市场时段检查 ──
                now = datetime.now()
                session = self._calendar.get_session(now)

                if session == TradingSession.CLOSED:
                    # 闭市：长间隔等待（60s），不轮询
                    logger.debug("市场已收盘，暂停轮询")
                    await asyncio.sleep(60.0)
                    continue

                if session == TradingSession.LUNCH_BREAK:
                    # 午休：中等间隔等待（30s）
                    logger.debug("午休时段，暂停轮询")
                    await asyncio.sleep(30.0)
                    continue

                # 交易/集合竞价时段 → 执行轮询
                # ── 速率限制 ──
                acquired = await asyncio.get_event_loop().run_in_executor(
                    None, self._rate_limiter.acquire, self._config.poll_interval
                )
                if not acquired:
                    logger.debug("Rate limiter timeout, skipping this poll cycle")
                    await asyncio.sleep(self._config.poll_interval)
                    continue

                symbols = list(self._subscribed.keys())
                quotes = await asyncio.get_event_loop().run_in_executor(
                    None, self._fetcher.fetch_quotes, symbols
                )

                for quote in quotes:
                    self._dispatch_quote(quote)

                    # 若订阅了 BAR_1M，合成 K 线
                    sub_types = self._subscribed.get(quote.symbol, set())
                    if QuoteType.BAR_1M in sub_types:
                        completed = self._bar_synth.update(quote)
                        if completed:
                            self._dispatch_bar(completed)

                consecutive_errors = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                logger.warning(
                    f"Poll error ({consecutive_errors}): {e}"
                )
                if consecutive_errors >= self._config.max_retry:
                    logger.error("Max poll errors reached, stopping poll loop")
                    self._status = StreamStatus.ERROR
                    self._notify_status(StreamStatus.ERROR, "too many poll errors")
                    break

            await asyncio.sleep(self._config.poll_interval)

        logger.info("Poll loop ended")

    # ── 回调分发 ──

    def _dispatch_quote(self, quote: QuoteSnapshot) -> None:
        if self._quote_callback:
            try:
                self._quote_callback(quote)
            except Exception as e:
                logger.error(f"Quote callback error: {e}")

    def _dispatch_bar(self, bar_data: BarData) -> None:
        """分发 BarData 及自动转换的 Domain Bar"""
        if self._bar_callback:
            try:
                self._bar_callback(bar_data)
            except Exception as e:
                logger.error(f"Bar callback error: {e}")

        if self._bar_domain_callback:
            try:
                domain_bar = bardata_to_domain_bar(bar_data)
                self._bar_domain_callback(domain_bar)
            except Exception as e:
                logger.error(f"Domain bar callback error: {e}")

    def _notify_status(self, status: StreamStatus, msg: str) -> None:
        if self._status_callback:
            try:
                self._status_callback(status, msg)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Fetcher 接口（可插拔，便于测试和扩展）
# ─────────────────────────────────────────────────────────────────────────────

class _QuoteFetcher:
    """行情抓取器接口（非 ABC，用于类型提示与鸭子类型）"""

    def health_check(self) -> bool:
        """检查数据源是否可用"""
        raise NotImplementedError

    def fetch_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        """批量获取行情快照"""
        raise NotImplementedError


class _AKShareDefaultFetcher(_QuoteFetcher):
    """AKShare 默认 Fetcher

    调用 ak.bond_zh_cov_spot_em() 或 ak.stock_zh_a_spot_em()
    获取全市场快照后筛选目标标的。
    """

    def __init__(self, market_type: MarketType):
        self._market_type = market_type

    def health_check(self) -> bool:
        """检查AKShare库是否可用。"""
        try:
            import akshare  # noqa: F401
            return True
        except ImportError:
            logger.error("akshare not installed: pip install akshare")
            return False

    def fetch_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        """从 AKShare 获取指定标的的实时行情快照。"""
        import akshare as ak

        now = datetime.now()
        symbol_set = set(symbols)
        results: list[QuoteSnapshot] = []

        try:
            if self._market_type == MarketType.CB:
                df = ak.bond_zh_cov_spot_em()
                results = self._parse_cb_df(df, symbol_set, now)
            else:
                df = ak.stock_zh_a_spot_em()
                results = self._parse_stock_df(df, symbol_set, now)
        except Exception as e:
            logger.error(f"AKShare fetch failed: {e}")
            raise

        return results

    def _parse_cb_df(
        self, df, symbol_set: set[str], now: datetime
    ) -> list[QuoteSnapshot]:
        """解析可转债 DataFrame → QuoteSnapshot"""
        results: list[QuoteSnapshot] = []

        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if code not in symbol_set:
                continue

            try:
                quote = QuoteSnapshot(
                    symbol=code,
                    quote_type=QuoteType.LEVEL1,
                    timestamp=now,
                    last_price=_to_decimal(row.get("最新价", 0)),
                    open_price=_to_decimal(row.get("今开", 0)),
                    high_price=_to_decimal(row.get("最高", 0)),
                    low_price=_to_decimal(row.get("最低", 0)),
                    close_price=_to_decimal(row.get("最新价", 0)),
                    pre_close=_to_decimal(row.get("昨收", 0)),
                    volume=int(row.get("成交量", 0)),
                    amount=_to_decimal(row.get("成交额", 0)),
                )
                results.append(quote)
            except Exception as e:
                logger.debug(f"Parse CB {code} failed: {e}")
                continue

        return results

    def _parse_stock_df(
        self, df, symbol_set: set[str], now: datetime
    ) -> list[QuoteSnapshot]:
        """解析 A 股 DataFrame → QuoteSnapshot"""
        results: list[QuoteSnapshot] = []

        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if code not in symbol_set:
                continue

            try:
                quote = QuoteSnapshot(
                    symbol=code,
                    quote_type=QuoteType.LEVEL1,
                    timestamp=now,
                    last_price=_to_decimal(row.get("最新价", 0)),
                    open_price=_to_decimal(row.get("今开", 0)),
                    high_price=_to_decimal(row.get("最高", 0)),
                    low_price=_to_decimal(row.get("最低", 0)),
                    close_price=_to_decimal(row.get("最新价", 0)),
                    pre_close=_to_decimal(row.get("昨收", 0)),
                    volume=int(row.get("成交量", 0)),
                    amount=_to_decimal(row.get("成交额", 0)),
                )
                results.append(quote)
            except Exception as e:
                logger.debug(f"Parse stock {code} failed: {e}")
                continue

        return results


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_decimal(value: Any) -> Decimal:
    """安全转换为 Decimal"""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


# ─────────────────────────────────────────────────────────────────────────────
# BarData → Domain Bar 适配器
# ─────────────────────────────────────────────────────────────────────────────

def bardata_to_domain_bar(
    bar_data: BarData,
    metadata: dict[str, Any] | None = None,
) -> Bar:
    """将实时数据流 BarData（Decimal）转换为领域模型 Bar（float）

    BarData（realtime_stream 模块）使用 Decimal 类型保证精度，
    Bar（domain/models 模块）使用 float 适配策略层与回测引擎。
    此函数是两者之间的唯一桥梁。

    Args:
        bar_data: 实时数据流产生的 BarData
        metadata: 附加元数据，如 { "source": "akshare", "ticks": 15 }

    Returns:
        领域模型 Bar 实例

    Examples:
        >>> from decimal import Decimal
        >>> from datetime import datetime
        >>> bd = BarData(
        ...     symbol="127045", interval="1m",
        ...     timestamp=datetime(2024, 1, 5, 9, 31),
        ...     open=Decimal("105.5"), high=Decimal("106.0"),
        ...     low=Decimal("105.0"), close=Decimal("105.8"),
        ...     volume=1000, amount=Decimal("105500"),
        ... )
        >>> bar = bardata_to_domain_bar(bd)
        >>> bar.close
        105.8
    """
    # BarData.timestamp 是 bar 窗口的 end_ts
    end_ts = bar_data.timestamp
    # start_ts = end_ts - interval
    interval_sec = _parse_interval_seconds(bar_data.interval)
    start_ts = end_ts - timedelta(seconds=interval_sec)

    vwap: float | None = None
    if bar_data.vwap is not None:
        vwap = float(bar_data.vwap)

    extra_meta = metadata.copy() if metadata else {}
    if bar_data.trade_count is not None:
        extra_meta["trade_count"] = bar_data.trade_count
    extra_meta["source"] = "akshare_realtime"
    extra_meta["interval"] = bar_data.interval

    return Bar(
        symbol=bar_data.symbol,
        start_ts=start_ts,
        end_ts=end_ts,
        open=float(bar_data.open),
        high=float(bar_data.high),
        low=float(bar_data.low),
        close=float(bar_data.close),
        volume=float(bar_data.volume),
        vwap=vwap,
        is_suspended=False,
        is_limit_up=None,
        is_limit_down=None,
        metadata=extra_meta if extra_meta else None,
    )


def _parse_interval_seconds(interval: str) -> int:
    """解析 K 线周期字符串为秒数

    Args:
        interval: 如 '1m', '5m', '15m', '30m', '1h', '1d'

    Returns:
        秒数
    """
    mapping = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "1d": 86400,
    }
    result = mapping.get(interval)
    if result is not None:
        return result
    # fallback: 尝试解析 "60s" / "60" 格式
    cleaned = interval.rstrip("s")
    try:
        return int(cleaned)
    except ValueError:
        logger.warning(f"Unknown interval '{interval}', defaulting to 60s")
        return 60
