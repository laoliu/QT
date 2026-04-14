"""
E2E PaperEngine 生命周期测试 — Track C.5

验证 PaperEngine 完整生命周期：
1. YAML 配置加载 + strategy_params 传递
2. 连接重试（connect_with_retry）
3. 市场时段调度（开盘→轮询→午休→收盘）
4. Bar 路由：Provider → bardata_to_domain_bar → LiveRunner.on_bar → 策略
5. 状态持久化 → 引擎重启 → 状态恢复 → 持仓一致
6. 优雅停止（stop_event）
7. Provider 健康检查 + 自动重连
8. 心跳发射
9. EOD 日终结算
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipha.data.akshare_realtime_provider import (
    AKShareRealtimeConfig,
    AKShareRealtimeProvider,
    MarketType,
    bardata_to_domain_bar,
)
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.data.realtime_stream import BarData, QuoteType, StreamStatus
from aipha.domain.models import Bar, Side
from aipha.runner.live_runner import LiveRunner, RunnerMode
from aipha.runner.paper_engine import PaperEngine, PaperEngineConfig
from aipha.runner.state_manager import StateManager
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext


# ─────────────────────────────────────────────────────────────────────────────
# Mock Fetcher（不走网络，返回固定行情）
# ─────────────────────────────────────────────────────────────────────────────

class MockFetcher:
    """可编程的行情抓取器，返回预设快照"""

    def __init__(self, quotes: list | None = None):
        from aipha.data.realtime_stream import QuoteSnapshot

        self._quotes = quotes or []
        self._call_count = 0
        self._fail_after: int | None = None  # 第 N 次后抛异常

    def health_check(self) -> bool:
        return True

    def fetch_quotes(self, symbols: list[str]) -> list:
        from aipha.data.realtime_stream import QuoteSnapshot

        self._call_count += 1
        if self._fail_after is not None and self._call_count > self._fail_after:
            raise ConnectionError("Mock network failure")

        now = datetime.now()
        results = []
        for sym in symbols:
            results.append(
                QuoteSnapshot(
                    symbol=sym,
                    quote_type=QuoteType.LEVEL1,
                    timestamp=now,
                    last_price=Decimal("105.50"),
                    open_price=Decimal("105.00"),
                    high_price=Decimal("106.00"),
                    low_price=Decimal("104.80"),
                    close_price=Decimal("105.50"),
                    pre_close=Decimal("105.00"),
                    volume=10000,
                    amount=Decimal("1055000"),
                )
            )
        return results


class FailingFetcher:
    """总是失败的 fetcher"""

    def health_check(self) -> bool:
        return False

    def fetch_quotes(self, symbols):
        raise ConnectionError("always fail")


# ─────────────────────────────────────────────────────────────────────────────
# 测试策略
# ─────────────────────────────────────────────────────────────────────────────

class LifecycleStrategy(Strategy):
    """记录生命周期事件的策略"""

    name = "lifecycle_test"

    def __init__(self, target_qty: int = 10, **kwargs):
        self.target_qty = target_qty
        self.bars_seen: list[dict] = []
        self.bought = False
        self.extra_kwargs = kwargs

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        self.bars_seen.append({
            "symbols": list(bars.keys()),
            "timestamp": str(next(iter(bars.values())).end_ts) if bars else "",
        })

        # 首次看到 bar 时买入
        if not self.bought and bars:
            intents = []
            for sym, bar in bars.items():
                intents.append(
                    OrderIntent(
                        symbol=sym,
                        side=Side.BUY,
                        qty=self.target_qty,
                        reason="lifecycle_init_buy",
                    )
                )
            self.bought = True
            return intents

        return []


class NoOpStrategy(Strategy):
    """空策略，仅记录 bar 数"""

    name = "noop_test"

    def __init__(self, **kwargs):
        self.bar_count = 0

    def on_bar(self, bars, context):
        self.bar_count += 1
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_config(tmp_path: Path) -> PaperEngineConfig:
    """基础 PaperEngineConfig"""
    return PaperEngineConfig(
        strategy_name="lifecycle_test",
        strategy_params={"target_qty": 20},
        symbols=["127045", "128054"],
        market_type=MarketType.CB,
        poll_interval=0.05,
        bar_interval_sec=60,
        initial_capital=100_000.0,
        lot_size=10,
        t_plus_n=0,
        commission_rate=0.0001,
        commission_min=0.0,
        stamp_tax_rate=0.0,
        slippage_bps=0.0,
        state_dir=tmp_path / "state",
        state_file="test_state.json",
        audit_log_dir=tmp_path / "audit",
    )


@pytest.fixture
def always_open_calendar():
    """始终返回交易时段的假日历"""
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.MORNING_CONTINUOUS
    cal.is_trading_day.return_value = True
    return cal


@pytest.fixture
def always_closed_calendar():
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.CLOSED
    cal.is_trading_day.return_value = False
    return cal


@pytest.fixture
def lunch_then_close_calendar():
    """先返回午休、再返回收盘"""
    cal = MagicMock(spec=CNStockCalendar)
    call_count = {"n": 0}

    def get_session_fn(dt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return TradingSession.LUNCH_BREAK
        return TradingSession.CLOSED

    cal.get_session.side_effect = get_session_fn
    cal.is_trading_day.return_value = True
    return cal


def _make_engine(
    config: PaperEngineConfig,
    strategy,
    calendar,
    fetcher=None,
) -> PaperEngine:
    """构建 PaperEngine，注入 mock fetcher 避免网络调用"""
    engine = PaperEngine(config, strategy, calendar=calendar)
    # 替换 provider 的 fetcher
    if fetcher is not None:
        engine._provider._fetcher = fetcher
    else:
        engine._provider._fetcher = MockFetcher()
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# 1. 配置 + 参数传递
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigAndParams:
    """YAML 配置加载 / strategy_params 传递"""

    def test_config_from_yaml_with_strategy_params(self, tmp_path: Path):
        """YAML 中的 strategy_params 正确解析"""
        yaml_content = """
paper_engine:
  strategy_name: "small_size"
  symbols: ["127045", "128054"]
  market_type: "convertible_bond"
  poll_interval: 3.0
  initial_capital: 500000
  commission_rate: 0.00005
  slippage_bps: 5.0
  strategy_params:
    top_k: 15
    rebalance_days: 5
    max_price: 120
"""
        yaml_path = tmp_path / "config.yml"
        yaml_path.write_text(yaml_content)

        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.strategy_name == "small_size"
        assert cfg.strategy_params["top_k"] == 15
        assert cfg.strategy_params["rebalance_days"] == 5
        assert cfg.strategy_params["max_price"] == 120
        assert cfg.commission_rate == 0.00005
        assert cfg.slippage_bps == 5.0

    def test_strategy_params_passed_to_constructor(
        self, tmp_config, always_open_calendar
    ):
        """strategy_params 正确传给策略构造函数"""
        strategy = LifecycleStrategy(**tmp_config.strategy_params)
        assert strategy.target_qty == 20

    def test_empty_strategy_params_default(self, tmp_path: Path):
        """无 strategy_params 时默认空 dict"""
        yaml_content = """
paper_engine:
  strategy_name: "noop"
  symbols: ["127045"]
"""
        yaml_path = tmp_path / "config.yml"
        yaml_path.write_text(yaml_content)

        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.strategy_params == {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. 连接重试
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectWithRetry:
    """连接重试 + 指数退避"""

    @pytest.mark.asyncio
    async def test_connect_success_first_try(
        self, tmp_config, always_open_calendar
    ):
        """首次连接成功"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_open_calendar)

        result = await engine._connect_with_retry(max_retries=3, base_delay=0.01)
        assert result is True

    @pytest.mark.asyncio
    async def test_connect_fails_all_retries(
        self, tmp_config, always_open_calendar
    ):
        """全部重试失败返回 False"""
        strategy = NoOpStrategy()
        engine = _make_engine(
            tmp_config, strategy, always_open_calendar,
            fetcher=FailingFetcher(),
        )

        result = await engine._connect_with_retry(max_retries=2, base_delay=0.01)
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_retries_then_succeeds(
        self, tmp_config, always_open_calendar
    ):
        """前几次失败，最终成功"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_open_calendar)

        call_count = {"n": 0}
        original_connect = engine._provider.connect

        async def flaky_connect():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("flaky")
            return await original_connect()

        engine._provider.connect = flaky_connect

        result = await engine._connect_with_retry(max_retries=5, base_delay=0.01)
        assert result is True
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_connect_aborted_by_stop_event(
        self, tmp_config, always_open_calendar
    ):
        """stop_event 中断重试"""
        strategy = NoOpStrategy()
        engine = _make_engine(
            tmp_config, strategy, always_open_calendar,
            fetcher=FailingFetcher(),
        )

        # 在重试延迟期间触发 stop
        async def set_stop_soon():
            await asyncio.sleep(0.02)
            engine._stop_event.set()

        asyncio.create_task(set_stop_soon())
        result = await engine._connect_with_retry(max_retries=5, base_delay=0.05)
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Bar 路由 & 策略执行
# ─────────────────────────────────────────────────────────────────────────────

class TestBarRouting:
    """Bar 从 Provider → Engine → LiveRunner → Strategy"""

    def test_on_domain_bar_increments_counter(
        self, tmp_config, always_closed_calendar
    ):
        """domain bar 回调正确计数"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()

        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )

        engine._on_domain_bar(bar)
        assert engine._bars_received == 1

        engine._on_domain_bar(bar)
        assert engine._bars_received == 2

        engine._runner.stop()

    def test_bar_reaches_strategy(
        self, tmp_config, always_closed_calendar
    ):
        """Bar 送达策略并触发交易"""
        strategy = LifecycleStrategy(target_qty=10)
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()

        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )

        engine._on_domain_bar(bar)

        # 策略应收到 bar
        assert len(strategy.bars_seen) == 1
        assert "127045" in strategy.bars_seen[0]["symbols"]

        engine._runner.stop()

    def test_bardata_to_domain_bar_conversion(self):
        """BarData → Bar 转换正确"""
        bar_data = BarData(
            symbol="127045",
            interval="1m",
            timestamp=datetime(2024, 6, 15, 9, 31),
            open=Decimal("105.00"),
            high=Decimal("106.00"),
            low=Decimal("104.80"),
            close=Decimal("105.50"),
            volume=10000,
            amount=Decimal("1055000"),
        )

        bar = bardata_to_domain_bar(bar_data)

        assert bar.symbol == "127045"
        assert bar.close == 105.5
        assert bar.volume == 10000.0
        assert bar.end_ts == datetime(2024, 6, 15, 9, 31)
        # start_ts = end_ts - 60s (1m interval)
        assert bar.start_ts == datetime(2024, 6, 15, 9, 30)


# ─────────────────────────────────────────────────────────────────────────────
# 4. 状态持久化 & 恢复
# ─────────────────────────────────────────────────────────────────────────────

class TestStatePersistence:
    """状态保存/加载/恢复一致性"""

    def test_save_and_load_roundtrip(
        self, tmp_config, always_closed_calendar
    ):
        """保存后加载，关键字段一致"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()

        # 模拟收到几根 bar
        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )
        for _ in range(5):
            engine._on_domain_bar(bar)

        assert engine._bars_received == 5

        # 保存
        engine._save_state()
        state_path = tmp_config.state_dir / tmp_config.state_file
        assert state_path.exists()

        # 验证 JSON 结构
        with open(state_path, "r") as f:
            state = json.load(f)
        assert state["meta"]["strategy_name"] == "lifecycle_test"
        assert state["meta"]["bars_received"] == 5
        assert "account" in state
        assert "cash" in state["account"]

        engine._runner.stop()

    def test_state_restore_preserves_cash(
        self, tmp_config, always_closed_calendar
    ):
        """状态恢复后 cash 一致"""
        # 第一个引擎：买入产生交易，保存状态
        strategy1 = LifecycleStrategy(target_qty=10)
        engine1 = _make_engine(tmp_config, strategy1, always_closed_calendar)
        engine1._runner.start()

        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )
        engine1._on_domain_bar(bar)
        engine1._save_state()

        snapshot1 = engine1._runner.get_account_snapshot()
        cash1 = float(snapshot1.cash)
        engine1._runner.stop()

        # 第二个引擎：从状态恢复
        strategy2 = NoOpStrategy()
        engine2 = _make_engine(tmp_config, strategy2, always_closed_calendar)
        engine2._load_state()
        engine2._runner.start()

        snapshot2 = engine2._runner.get_account_snapshot()
        cash2 = float(snapshot2.cash)

        # 现金应一致
        assert abs(cash1 - cash2) < 0.01, f"Cash mismatch: {cash1} vs {cash2}"

        engine2._runner.stop()

    def test_state_restore_preserves_positions(
        self, tmp_config, always_closed_calendar
    ):
        """状态恢复后持仓一致"""
        # 第一个引擎：买入
        strategy1 = LifecycleStrategy(target_qty=10)
        engine1 = _make_engine(tmp_config, strategy1, always_closed_calendar)
        engine1._runner.start()

        # NBO 填充：bar_t1 提交订单，bar_t2 成交
        bar_t1 = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )
        bar_t2 = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 31),
            end_ts=datetime(2024, 6, 15, 9, 32),
            open=105.5, high=106.5, low=105.2, close=106.0,
            volume=9000.0,
        )
        engine1._on_domain_bar(bar_t1)  # 提交买单
        engine1._on_domain_bar(bar_t2)  # NBO 成交
        engine1._save_state()

        snapshot1 = engine1._runner.get_account_snapshot()
        positions1 = dict(snapshot1.positions)
        engine1._runner.stop()

        # 第二个引擎：恢复
        strategy2 = NoOpStrategy()
        engine2 = _make_engine(tmp_config, strategy2, always_closed_calendar)
        engine2._load_state()
        engine2._runner.start()

        snapshot2 = engine2._runner.get_account_snapshot()
        positions2 = dict(snapshot2.positions)

        # 先检查 positions1 有持仓
        assert len(positions1) > 0, "第一个引擎应有持仓"

        # 持仓 key 一致
        assert set(positions1.keys()) == set(positions2.keys()), (
            f"Position keys mismatch: {positions1.keys()} vs {positions2.keys()}"
        )

        engine2._runner.stop()

    def test_state_save_on_fill(
        self, tmp_config, always_closed_calendar
    ):
        """成交后立即保存状态"""
        strategy = LifecycleStrategy(target_qty=10)
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()

        # NBO 填充：bar_t1 提交订单，bar_t2 成交
        bar_t1 = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )
        bar_t2 = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 31),
            end_ts=datetime(2024, 6, 15, 9, 32),
            open=105.5, high=106.5, low=105.2, close=106.0,
            volume=9000.0,
        )
        engine._on_domain_bar(bar_t1)  # 提交买单
        engine._on_domain_bar(bar_t2)  # NBO 成交触发 fill → 立即保存

        # 应立即保存（因为有 fill）
        state_path = tmp_config.state_dir / tmp_config.state_file
        assert state_path.exists(), "Fill 后应立即保存状态"

        engine._runner.stop()


# ─────────────────────────────────────────────────────────────────────────────
# 5. 完整异步生命周期
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineLifecycle:
    """PaperEngine 完整 run → stop 生命周期"""

    @pytest.mark.asyncio
    async def test_run_and_stop_gracefully(
        self, tmp_config, always_closed_calendar
    ):
        """引擎启动后可优雅停止"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)

        async def stop_after_delay():
            await asyncio.sleep(0.2)
            await engine.stop()

        stop_task = asyncio.create_task(stop_after_delay())
        await engine.run()
        await stop_task

        assert engine._running is False

    @pytest.mark.asyncio
    async def test_run_connect_fail_exits_cleanly(
        self, tmp_config, always_closed_calendar
    ):
        """连接失败时引擎干净退出"""
        strategy = NoOpStrategy()
        engine = _make_engine(
            tmp_config, strategy, always_closed_calendar,
            fetcher=FailingFetcher(),
        )

        # 应该因连接失败直接返回
        await engine.run()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_market_session_transitions(
        self, tmp_config,
    ):
        """市场时段切换：开盘→午休→收盘"""
        session_sequence = [
            TradingSession.MORNING_CONTINUOUS,
            TradingSession.MORNING_CONTINUOUS,
            TradingSession.LUNCH_BREAK,
            TradingSession.CLOSED,
        ]
        call_idx = {"n": 0}

        cal = MagicMock(spec=CNStockCalendar)
        cal.is_trading_day.return_value = True

        def cycle_sessions(dt):
            idx = min(call_idx["n"], len(session_sequence) - 1)
            call_idx["n"] += 1
            return session_sequence[idx]

        cal.get_session.side_effect = cycle_sessions

        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, cal)

        async def stop_after_settle():
            # 等足够时间让 schedule loop 跑几轮
            await asyncio.sleep(0.5)
            await engine.stop()

        stop_task = asyncio.create_task(stop_after_settle())
        await engine.run()
        await stop_task

        # 引擎应正常停止
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_eod_settlement_saves_state(
        self, tmp_config, always_closed_calendar
    ):
        """EOD 结算会保存状态"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()

        # 手动触发 EOD
        await engine._eod_settlement()

        state_path = tmp_config.state_dir / tmp_config.state_file
        assert state_path.exists()

        engine._runner.stop()


# ─────────────────────────────────────────────────────────────────────────────
# 6. 心跳
# ─────────────────────────────────────────────────────────────────────────────

class TestHeartbeat:
    """心跳日志发射"""

    def test_heartbeat_emitted_at_interval(
        self, tmp_config, always_closed_calendar, caplog
    ):
        """心跳日志按间隔输出"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()
        engine._heartbeat_interval_sec = 0  # 强制每次都输出

        with caplog.at_level(logging.INFO):
            now = datetime.now()
            engine._emit_heartbeat(now)

        assert any("HEARTBEAT" in r.message for r in caplog.records)
        engine._runner.stop()

    def test_heartbeat_skipped_within_interval(
        self, tmp_config, always_closed_calendar, caplog
    ):
        """间隔内不重复输出"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine._runner.start()
        engine._heartbeat_interval_sec = 3600  # 1 小时
        engine._last_heartbeat = datetime.now()

        with caplog.at_level(logging.INFO):
            engine._emit_heartbeat(datetime.now())

        heartbeat_msgs = [r for r in caplog.records if "HEARTBEAT" in r.message]
        assert len(heartbeat_msgs) == 0
        engine._runner.stop()


# ─────────────────────────────────────────────────────────────────────────────
# 7. 重连逻辑
# ─────────────────────────────────────────────────────────────────────────────

class TestReconnect:
    """Provider 异常后自动重连"""

    @pytest.mark.asyncio
    async def test_reconnect_provider_success(
        self, tmp_config, always_closed_calendar
    ):
        """数据源重连成功"""
        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, always_closed_calendar)

        # 模拟重连
        await engine._reconnect_provider()

        # 重连成功后引擎仍在运行
        assert engine._provider.get_status() == StreamStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_reconnect_provider_fail_stops_engine(
        self, tmp_config, always_closed_calendar
    ):
        """数据源重连失败 → 触发 engine.stop()"""
        strategy = NoOpStrategy()
        engine = _make_engine(
            tmp_config, strategy, always_closed_calendar,
            fetcher=FailingFetcher(),
        )
        engine._running = True

        await engine._reconnect_provider()

        # 重连失败应触发 stop
        assert engine._stop_event.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# 8. 完整 E2E 场景（连接→Bar→交易→保存→恢复）
# ─────────────────────────────────────────────────────────────────────────────

class TestFullE2EScenario:
    """端到端完整场景"""

    def test_connect_bar_trade_save_restore(
        self, tmp_config, always_closed_calendar
    ):
        """完整闭环：初始化→Bar→交易→保存→恢复→验证"""
        # 1. 创建引擎 + 买入策略
        strategy1 = LifecycleStrategy(target_qty=10)
        engine1 = _make_engine(tmp_config, strategy1, always_closed_calendar)
        engine1._runner.start()

        # 2. 注入 2 根 bar
        bar_a = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )
        bar_b = Bar(
            symbol="128054",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=110.0, high=111.0, low=109.5, close=110.2,
            volume=8000.0,
        )

        engine1._on_domain_bar(bar_a)
        engine1._on_domain_bar(bar_b)

        # NBO 填充：需要第 2 根 bar 才能成交，发送 t2 bars
        bar_a2 = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 31),
            end_ts=datetime(2024, 6, 15, 9, 32),
            open=105.5, high=106.5, low=105.2, close=106.0,
            volume=9000.0,
        )
        bar_b2 = Bar(
            symbol="128054",
            start_ts=datetime(2024, 6, 15, 9, 31),
            end_ts=datetime(2024, 6, 15, 9, 32),
            open=110.2, high=111.5, low=110.0, close=111.0,
            volume=7000.0,
        )
        engine1._on_domain_bar(bar_a2)
        engine1._on_domain_bar(bar_b2)

        # 3. 验证策略收到 bar
        assert len(strategy1.bars_seen) >= 1

        # 4. 验证产生了交易（NBO：t1 提交 → t2 成交）
        assert engine1._runner._state.total_fills > 0

        # 5. 保存
        engine1._save_state()
        snap1 = engine1._runner.get_account_snapshot()
        nav1 = float(snap1.total_equity)
        cash1 = float(snap1.cash)
        fills1 = engine1._runner._state.total_fills
        engine1._runner.stop()

        # 6. 新引擎恢复
        strategy2 = NoOpStrategy()
        engine2 = _make_engine(tmp_config, strategy2, always_closed_calendar)
        engine2._load_state()
        engine2._runner.start()

        snap2 = engine2._runner.get_account_snapshot()
        cash2 = float(snap2.cash)

        # 7. cash 一致
        assert abs(cash1 - cash2) < 0.01, f"Cash: {cash1} vs {cash2}"

        # 8. 继续送 bar 不崩溃
        bar_c = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 31),
            end_ts=datetime(2024, 6, 15, 9, 32),
            open=105.5, high=106.5, low=105.2, close=106.0,
            volume=9000.0,
        )
        engine2._on_domain_bar(bar_c)
        assert engine2._bars_received > 0

        engine2._runner.stop()

    def test_bars_received_persisted_across_restart(
        self, tmp_config, always_closed_calendar
    ):
        """bars_received 计数跨重启保持"""
        strategy = NoOpStrategy()
        engine1 = _make_engine(tmp_config, strategy, always_closed_calendar)
        engine1._runner.start()

        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.0, high=106.0, low=104.8, close=105.5,
            volume=10000.0,
        )

        for _ in range(7):
            engine1._on_domain_bar(bar)

        assert engine1._bars_received == 7
        engine1._save_state()
        engine1._runner.stop()

        # 恢复
        strategy2 = NoOpStrategy()
        engine2 = _make_engine(tmp_config, strategy2, always_closed_calendar)
        engine2._load_state()

        assert engine2._bars_received == 7

    @pytest.mark.asyncio
    async def test_full_lifecycle_open_to_close(self, tmp_config):
        """完整生命周期：开盘 → 接收 Bar → 收盘 → 结算"""
        # 模拟 2 轮开盘(有 bar 到达) → 收盘
        session_sequence = [
            TradingSession.MORNING_CONTINUOUS,
            TradingSession.MORNING_CONTINUOUS,
            TradingSession.CLOSED,
        ]
        call_idx = {"n": 0}

        cal = MagicMock(spec=CNStockCalendar)
        cal.is_trading_day.return_value = True

        def cycle_sessions(dt):
            idx = min(call_idx["n"], len(session_sequence) - 1)
            call_idx["n"] += 1
            return session_sequence[idx]

        cal.get_session.side_effect = cycle_sessions

        strategy = NoOpStrategy()
        engine = _make_engine(tmp_config, strategy, cal)

        async def stop_after():
            await asyncio.sleep(0.5)
            await engine.stop()

        stop_task = asyncio.create_task(stop_after())
        await engine.run()
        await stop_task

        # 引擎正常停止
        assert engine._running is False

        # 状态文件应已保存（shutdown 会保存）
        state_path = tmp_config.state_dir / tmp_config.state_file
        assert state_path.exists()
