"""
Paper Trading 引擎测试

测试覆盖：
- PaperEngineConfig YAML 加载
- PaperEngine 初始化与组件连接
- 市场调度循环逻辑
- 状态持久化/恢复
- Domain Bar 回调链路
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipha.data.akshare_realtime_provider import MarketType
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.domain.models import Bar
from aipha.runner.paper_engine import PaperEngine, PaperEngineConfig
from aipha.monitor.metrics_collector import MetricsConfig


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

class DummyStrategy:
    """用于测试的极简策略"""

    name = "test_strategy"

    def on_start(self):
        pass

    def on_stop(self):
        pass

    def on_bars(self, context, bars):
        return []  # 不产生交易信号


@pytest.fixture
def config(tmp_path: Path) -> PaperEngineConfig:
    return PaperEngineConfig(
        strategy_name="test_strategy",
        symbols=["127045", "128054"],
        market_type=MarketType.CB,
        poll_interval=0.05,
        bar_interval_sec=60,
        initial_capital=100_000.0,
        lot_size=10,
        t_plus_n=0,
        commission_rate=0.0005,
        stamp_tax_rate=0.0,
        slippage_bps=2.0,
        state_dir=tmp_path / "state",
        audit_log_dir=tmp_path / "audit",
        metrics_config=MetricsConfig(output_dir=tmp_path / "metrics"),
    )


@pytest.fixture
def always_closed_calendar():
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.CLOSED
    cal.is_trading_day.return_value = False
    return cal


@pytest.fixture
def always_open_calendar():
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.MORNING_CONTINUOUS
    cal.is_trading_day.return_value = True
    return cal


# ─────────────────────────────────────────────────────────────────────────────
# PaperEngineConfig 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperEngineConfig:
    """配置测试"""

    def test_defaults(self):
        cfg = PaperEngineConfig()
        assert cfg.poll_interval == 3.0
        assert cfg.lot_size == 10
        assert cfg.t_plus_n == 0
        assert cfg.stamp_tax_rate == 0.0

    def test_from_yaml(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "cb_small_size"
  symbols: ["127045", "128054"]
  market_type: "convertible_bond"
  poll_interval: 5.0
  bar_interval_sec: 120
  initial_capital: 500000.0
  lot_size: 10
  t_plus_n: 0
  commission_rate: 0.001
  stamp_tax_rate: 0.0
  slippage_bps: 3.0
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)

        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.strategy_name == "cb_small_size"
        assert cfg.symbols == ["127045", "128054"]
        assert cfg.poll_interval == 5.0
        assert cfg.bar_interval_sec == 120
        assert cfg.initial_capital == 500_000.0
        assert cfg.commission_rate == 0.001
        assert cfg.slippage_bps == 3.0

        def test_from_yaml_parses_fill_model(self, tmp_path: Path):
                yaml_content = """
paper_engine:
    strategy_name: "cb_small_size"
    fill_model:
        name: "vwap"
        volume_constraint:
            max_participation_rate: 0.10
            lot_size: 10
"""
                yaml_path = tmp_path / "test_fill_model.yml"
                yaml_path.write_text(yaml_content, encoding="utf-8")

                cfg = PaperEngineConfig.from_yaml(yaml_path)
                assert cfg.fill_model_name == "vwap"
                assert cfg.fill_model_volume_constraint is not None
                assert cfg.fill_model_volume_constraint["lot_size"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# PaperEngine 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperEngine:
    """引擎测试"""

    def test_init(self, config: PaperEngineConfig, always_closed_calendar):
        """引擎可正常初始化"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)

        assert engine._config is config
        assert engine._strategy is strategy
        assert engine.runner is not None
        assert engine._bars_received == 0

    def test_runner_is_paper_mode(self, config: PaperEngineConfig, always_closed_calendar):
        """Runner 为 paper mode"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)

        from aipha.runner.live_runner import RunnerMode
        assert engine.runner.config.mode == RunnerMode.PAPER

    def test_state_save_and_load(self, config: PaperEngineConfig, always_closed_calendar):
        """状态保存/恢复"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)

        # 启动 runner 以初始化状态
        engine._runner.start()

        # 保存状态
        engine._save_state()

        state_path = config.state_dir / config.state_file
        assert state_path.exists()

        with open(state_path, "r") as f:
            state = json.load(f)

        assert "meta" in state
        assert state["meta"]["strategy_name"] == "test_strategy"
        assert state["meta"]["symbols"] == ["127045", "128054"]
        assert "account" in state
        assert "cash" in state["account"]

        engine._runner.stop()

    def test_on_domain_bar_increments_counter(
        self, config: PaperEngineConfig, always_closed_calendar
    ):
        """domain bar 回调计数"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        engine._runner.start()

        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.5,
            high=106.0,
            low=105.0,
            close=105.8,
            volume=1000.0,
        )

        engine._on_domain_bar(bar)
        assert engine._bars_received == 1

        engine._on_domain_bar(bar)
        assert engine._bars_received == 2

        engine._runner.stop()

    @pytest.mark.asyncio
    async def test_run_closed_market_exits(
        self, config: PaperEngineConfig, always_closed_calendar
    ):
        """闭市时 engine 应该正常退出"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)

        # 5秒后强制停止
        async def stop_after_delay():
            await asyncio.sleep(0.3)
            await engine.stop()

        # provider.connect 需要 mock（不实际调用 akshare）
        engine._provider.connect = AsyncMock(return_value=True)
        engine._provider.disconnect = AsyncMock()

        stop_task = asyncio.create_task(stop_after_delay())
        await engine.run()
        await stop_task

        assert engine._running is False

    def test_next_market_open(self, config: PaperEngineConfig):
        """计算下一个交易日开盘时间"""
        cal = MagicMock(spec=CNStockCalendar)
        # 明天是交易日
        cal.is_trading_day.return_value = True

        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=cal)

        now = datetime(2024, 6, 14, 16, 0)  # Friday 16:00
        result = engine._next_market_open(now)

        # 应该是下一个交易日 9:10
        assert result.hour == 9
        assert result.minute == 10


# ─────────────────────────────────────────────────────────────────────────────
# PaperEngine 新功能测试（动态标的池/监控/指标）
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperEngineUniverse:
    """动态标的池集成测试"""

    def test_universe_disabled_by_default(self, config, always_closed_calendar):
        """默认禁用动态标的池"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.universe is None

    def test_universe_enabled(self, config, always_closed_calendar):
        """启用动态标的池"""
        from aipha.data.cb_universe import CBUniverseConfig
        config.universe_config = CBUniverseConfig(enabled=True, max_price=150.0)
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.universe is not None

    def test_get_active_symbols_static(self, config, always_closed_calendar):
        """无 universe 时使用静态 symbols"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine._get_active_symbols() == ["127045", "128054"]

    def test_get_active_symbols_universe(self, config, always_closed_calendar):
        """有 universe 时使用动态 symbols"""
        from aipha.data.cb_universe import CBUniverseConfig
        config.universe_config = CBUniverseConfig(enabled=True)
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        engine.universe.set_symbols_override(["A", "B", "C"])
        assert engine._get_active_symbols() == ["A", "B", "C"]


class TestPaperEngineMonitoring:
    """监控集成测试"""

    def test_monitoring_enabled_by_default(self, config, always_closed_calendar):
        """默认启用监控"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.monitor is not None
        assert engine.monitor.rules_count == 4  # drawdown + daily_loss + data_stale + inactivity

    def test_monitoring_disabled(self, config, always_closed_calendar):
        """可以禁用监控"""
        config.monitoring_enabled = False
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.monitor is None

    def test_heartbeat_feeds_monitor(self, config, always_closed_calendar):
        """心跳更新监控"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        engine._runner.start()

        # 强制心跳
        engine._last_heartbeat = None
        engine._emit_heartbeat(datetime.now())

        # 监控应收到至少一个快照
        assert len(engine.monitor.get_snapshots()) >= 1

        engine._runner.stop()

    def test_on_domain_bar_tracks_bar_time(self, config, always_closed_calendar):
        """on_domain_bar 更新 last_bar_time"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        engine._runner.start()

        bar = Bar(
            symbol="127045",
            start_ts=datetime(2024, 6, 15, 9, 30),
            end_ts=datetime(2024, 6, 15, 9, 31),
            open=105.5, high=106.0, low=105.0, close=105.8,
            volume=1000.0,
        )
        engine._on_domain_bar(bar)

        assert engine._last_bar_time is not None
        engine._runner.stop()


class TestPaperEngineMetrics:
    """指标收集集成测试"""

    def test_metrics_enabled_by_default(self, config, always_closed_calendar):
        """默认启用指标收集"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.metrics is not None

    def test_metrics_disabled(self, config, always_closed_calendar):
        """可以禁用指标"""
        config.metrics_enabled = False
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.metrics is None

    def test_heartbeat_feeds_metrics(self, config, always_closed_calendar):
        """心跳更新指标"""
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        engine._runner.start()

        engine._last_heartbeat = None
        engine._emit_heartbeat(datetime.now())

        # 指标应有 NAV 记录
        assert engine.metrics.nav_count >= 1

        engine._runner.stop()


class TestPaperEngineConfigExtended:
    """扩展配置解析测试"""

    def test_from_yaml_with_universe(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: ["127045"]
  universe:
    enabled: true
    max_price: 130.0
    min_amount: 1.0
    refresh_interval_sec: 1800
  monitoring_enabled: true
  alert_drawdown_threshold: 0.10
  metrics_enabled: true
  metrics:
    output_dir: "/tmp/metrics"
    write_interval_sec: 30
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)

        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.universe_config.enabled is True
        assert cfg.universe_config.max_price == 130.0
        assert cfg.universe_config.min_amount == 1.0
        assert cfg.monitoring_enabled is True
        assert cfg.alert_drawdown_threshold == 0.10
        assert cfg.metrics_enabled is True
        assert cfg.metrics_config.write_interval_sec == 30

    def test_from_yaml_without_universe(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: ["127045"]
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)

        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.universe_config.enabled is False  # 默认禁用

    def test_run_paper_trading_validates_universe_or_symbols(self, tmp_path: Path):
        """没有 symbols 且 universe 未启用时报错"""
        from aipha.runner.paper_engine import run_paper_trading

        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: []
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)

        with pytest.raises(ValueError, match="symbols.*universe"):
            run_paper_trading(yaml_path)


# ============================================================================
# B5: SlippageModel 配置解析测试
# ============================================================================


class TestSlippageModelConfig:
    """验证 PaperEngineConfig 正确解析滑点模型配置"""

    def test_default_is_fixed(self):
        """默认滑点模型为 fixed"""
        cfg = PaperEngineConfig()
        assert cfg.slippage_model_name == "fixed"
        model = cfg.create_slippage_model()
        from aipha.execution.slippage import FixedBpsSlippageModel
        assert isinstance(model, FixedBpsSlippageModel)

    def test_parse_square_root_from_yaml(self, tmp_path: Path):
        """从 YAML 解析 square_root_impact 模型"""
        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: ["000001"]
  slippage_bps: 3.0
  slippage_model: "square_root_impact"
  slippage_params:
    impact_coeff: 0.1
    sigma_daily: 0.02
    max_bps: 200.0
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.slippage_model_name == "square_root_impact"
        model = cfg.create_slippage_model()
        from aipha.execution.slippage import SquareRootImpactSlippageModel
        assert isinstance(model, SquareRootImpactSlippageModel)
        assert model.impact_coeff == 0.1

    def test_parse_linear_impact_from_yaml(self, tmp_path: Path):
        """从 YAML 解析 linear_impact 模型"""
        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: ["000001"]
  slippage_bps: 3.0
  slippage_model: "linear_impact"
  slippage_params:
    impact_coeff: 50.0
    max_bps: 100.0
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        model = cfg.create_slippage_model()
        from aipha.execution.slippage import LinearImpactSlippageModel
        assert isinstance(model, LinearImpactSlippageModel)
        assert model.impact_coeff == 50.0
        # base_bps 应从 slippage_bps 回退
        assert model.base_bps == 3.0

    def test_invalid_model_name_raises(self):
        """无效模型名称抛出 ValueError"""
        cfg = PaperEngineConfig(slippage_model_name="unknown_model")
        with pytest.raises(ValueError, match="不支持的滑点模型"):
            cfg.create_slippage_model()


# ============================================================================
# B6: Barra 风格监控集成测试
# ============================================================================


class TestBarraMonitorIntegration:
    """验证 Barra 风格暴露监控正确集成到 PaperEngine"""

    def test_barra_disabled_by_default(self):
        """默认不启用 Barra 监控"""
        cfg = PaperEngineConfig()
        assert cfg.barra_monitor_enabled is False

    def test_barra_from_yaml(self, tmp_path: Path):
        """从 YAML 解析 Barra 监控配置"""
        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: ["000001"]
  barra_monitor_enabled: true
  barra_alert_thresholds:
    SIZE: 2.0
    VOL: 3.0
"""
        yaml_path = tmp_path / "test_config.yml"
        yaml_path.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.barra_monitor_enabled is True
        assert cfg.barra_alert_thresholds["SIZE"] == 2.0
        assert cfg.barra_alert_thresholds["VOL"] == 3.0

    def test_barra_engine_init(self, config, always_closed_calendar):
        """Barra 监控器在 PaperEngine 中正确初始化"""
        config.barra_monitor_enabled = True
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.barra_monitor is not None
        from aipha.risk.barra_style_monitor import BarraStyleMonitor
        assert isinstance(engine.barra_monitor, BarraStyleMonitor)

    def test_barra_disabled_engine_init(self, config, always_closed_calendar):
        """Barra 监控器默认不创建"""
        config.barra_monitor_enabled = False
        strategy = DummyStrategy()
        engine = PaperEngine(config, strategy, calendar=always_closed_calendar)
        assert engine.barra_monitor is None


# ============================================================================
# B1+B3+B4: Pillar 配置解析测试
# ============================================================================


class TestPillarConfig:
    """验证 pillar 配置从 YAML 正确解析"""

    def test_plan_a_5comp_pillar_parse(self, tmp_path: Path):
        """解析 plan_a_5comp 风格的多 Pillar 配置"""
        yaml_content = """
paper_engine:
  strategy_name: "plan_a_5comp"
  market_type: "multi_asset"
  initial_capital: 1000000.0
  portfolio_weights:
    cb: 0.300
    ml: 0.300
    gold: 0.150

pillars:
  bond:
    name: "多久期国债"
    market_type: "bond"
    weight: 0.100
    symbols:
      - code: "511010.SH"
        weight: 0.34
        duration_bucket: "short"
      - code: "511260.SH"
        weight: 0.33
        duration_bucket: "mid"
  gold:
    name: "黄金ETF"
    market_type: "etf"
    weight: 0.150
    symbol: "518880.SH"
  cb:
    name: "CB 三策略"
    market_type: "convertible_bond"
    weight: 0.300
    sub_strategies:
      - name: triple_low
        weight: 0.48
"""
        yaml_path = tmp_path / "test_pillars.yml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        cfg = PaperEngineConfig.from_yaml(yaml_path)

        # B1: pillar_configs 正确解析
        assert len(cfg.pillar_configs) == 3
        assert "bond" in cfg.pillar_configs
        assert "gold" in cfg.pillar_configs
        assert "cb" in cfg.pillar_configs

        # B3: 债券多久期 symbols 解析
        bond_pc = cfg.pillar_configs["bond"]
        assert len(bond_pc.bond_symbols) == 2
        assert bond_pc.bond_symbols[0].code == "511010.SH"
        assert bond_pc.bond_symbols[0].duration_bucket == "short"
        assert bond_pc.bond_symbols[1].weight == 0.33

        # 静态标的合并
        gold_pc = cfg.pillar_configs["gold"]
        assert "518880.SH" in gold_pc.static_symbols

        # CB sub_strategies
        cb_pc = cfg.pillar_configs["cb"]
        assert len(cb_pc.sub_strategies) == 1
        assert cb_pc.sub_strategies[0]["name"] == "triple_low"

    def test_pillar_static_symbols_auto_merged(self, tmp_path: Path):
        """pillar 静态标的自动合并到 symbols"""
        yaml_content = """
paper_engine:
  strategy_name: "test"

pillars:
  bond:
    name: "债券"
    weight: 0.5
    symbols:
      - code: "511010.SH"
        weight: 1.0
  gold:
    name: "黄金"
    weight: 0.5
    symbol: "518880.SH"
"""
        yaml_path = tmp_path / "test_merge.yml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        # symbols 应自动从 pillar 收集
        assert "511010.SH" in cfg.symbols
        assert "518880.SH" in cfg.symbols

    def test_run_paper_trading_no_crash_with_pillars(self, tmp_path: Path):
        """B4: 有 pillars 时 run_paper_trading 进入 Pillar 模式，不再抛 ValueError"""
        from aipha.runner.paper_engine import run_paper_trading

        yaml_content = """
paper_engine:
  strategy_name: "nonexistent_but_has_pillars"

pillars:
  gold:
    name: "黄金"
    weight: 1.0
    symbol: "518880.SH"
"""
        yaml_path = tmp_path / "test_no_crash.yml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        # 有 pillars 时进入 Pillar 模式（PillarOrchestrator），
        # 不再抛 "缺少 symbols" 的 ValueError；
        # mock asyncio.run 阻止引擎无限事件循环
        def close_without_running(coro):
            coro.close()

        with patch("asyncio.run", side_effect=close_without_running):
            run_paper_trading(yaml_path)  # 不应抛出 ValueError

    @pytest.mark.asyncio
    async def test_sync_entry_rejects_running_loop_before_creating_coroutine(self):
        from unittest.mock import Mock
        from aipha.runner.paper_engine import _run_engine_sync

        engine = type("Engine", (), {"run": Mock()})()
        with pytest.raises(RuntimeError, match="await PaperEngine.run"):
            _run_engine_sync(engine)
        engine.run.assert_not_called()

    def test_portfolio_weights_parsed(self, tmp_path: Path):
        """portfolio_weights 正确解析"""
        yaml_content = """
paper_engine:
  strategy_name: "test"
  symbols: ["000001"]
  portfolio_weights:
    cb: 0.3
    ml: 0.3
    etf: 0.4
"""
        yaml_path = tmp_path / "test_weights.yml"
        yaml_path.write_text(yaml_content, encoding="utf-8")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.portfolio_weights["cb"] == 0.3
        assert cfg.portfolio_weights["ml"] == 0.3


class TestPaperEngineE2EIntegration:
    """端到端集成测试：验证从 plan_a_5comp.yml 加载到引擎初始化的完整链路。"""

    def test_plan_a_5comp_yaml_loads(self):
        """Plan A 5comp YAML 可加载且所有核心字段正确"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        # 基础字段
        assert cfg.initial_capital > 0
        assert cfg.strategy_name != ""
        assert len(cfg.symbols) > 0 or len(cfg.pillar_configs) > 0
        # 滑点模型
        assert cfg.slippage_model_name in ("fixed", "linear_impact", "square_root_impact")
        sm = cfg.create_slippage_model()
        assert sm is not None

    def test_plan_a_5comp_pillar_coverage(self):
        """Plan A 5comp 的五个支柱都被解析"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert len(cfg.pillar_configs) >= 3, f"Expected >=3 pillars, got {len(cfg.pillar_configs)}"
        # 权重之和应 ~1.0
        if cfg.portfolio_weights:
            total_w = sum(cfg.portfolio_weights.values())
            assert 0.95 <= total_w <= 1.05, f"Weights sum {total_w} not ~1.0"

    def test_plan_a_5comp_barra_config(self):
        """Plan A 5comp 的 Barra 监控配置正确加载"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.barra_monitor_enabled is True
        assert "SIZE" in cfg.barra_alert_thresholds
        assert cfg.barra_alert_thresholds["SIZE"] > 0

    def test_plan_a_5comp_slippage_shared(self):
        """Plan A 5comp 的滑点模型与回测引擎共享同一实现"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        from aipha.execution.slippage import SquareRootImpactSlippageModel
        sm = cfg.create_slippage_model()
        assert isinstance(sm, SquareRootImpactSlippageModel)

    def test_plan_a_5comp_fill_model_declared(self):
        """Plan A 5comp 显式声明 fill_model，避免隐式开盘价撮合。"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.fill_model_name == "next_bar_open"

    def test_r107_v19_cb40_yaml_loads(self):
        """R107 激进型组合配置存在且可按标准 multi_asset 模式解析。"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "r107_v19_cb40.yml"
        if not yaml_path.exists():
            pytest.skip("r107_v19_cb40.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.strategy_name == "r107_v19_cb40"
        assert cfg.fill_model_name == "next_bar_open"
        assert set(cfg.pillar_configs) == {"equity", "cb"}
        assert cfg.portfolio_weights["equity"] == pytest.approx(0.60)

    @pytest.mark.parametrize(
        ("config_name", "expected_pillars"),
        [
            ("r106_attack.yml", {"baseline_smallcap", "cb", "bond"}),
            (
                "r106_steady.yml",
                {"baseline_smallcap", "largecap_dividend", "midcap_value", "cb", "bond"},
            ),
            ("r106_defend.yml", {"baseline_smallcap", "largecap_dividend", "cb", "bond"}),
        ],
    )
    def test_r106_profiles_yaml_loads(self, config_name: str, expected_pillars: set[str]):
        """R106 三套组合已迁移到标准 multi_asset paper_engine 契约。"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / config_name
        if not yaml_path.exists():
            pytest.skip(f"{config_name} not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        assert cfg.market_type.value == "multi_asset"
        assert cfg.fill_model_name == "next_bar_open"
        assert set(cfg.pillar_configs) == expected_pillars
        assert pytest.approx(sum(cfg.portfolio_weights.values()), rel=1e-6) == 1.0

    def test_plan_a_5comp_etf_v5_pool(self):
        """Plan A 5comp ETF 组件使用 V5 池 (含海外+红利低波)"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        etf_pc = cfg.pillar_configs.get("etf")
        assert etf_pc is not None, "ETF pillar missing"
        # V5 应引用 etf_cross_v5 策略
        raw_strategy = etf_pc.raw.get("strategy", etf_pc.strategy_name)
        assert "v5" in str(raw_strategy).lower() or "etf_cross_v5" in str(raw_strategy).lower(), (
            f"Expected V5 ETF strategy, got: {raw_strategy}"
        )

    def test_plan_a_5comp_bond_5_instruments(self):
        """Plan A 5comp Bond 组件含 5 只多久期标的"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yaml_path)
        bond_pc = cfg.pillar_configs.get("bond")
        assert bond_pc is not None, "Bond pillar missing"
        assert len(bond_pc.bond_symbols) >= 5, (
            f"Expected >=5 bond instruments, got {len(bond_pc.bond_symbols)}"
        )
        # 验证 5 个久期段完整覆盖
        buckets = {bs.duration_bucket for bs in bond_pc.bond_symbols}
        for expected in ("short", "mid", "mid_long", "policy_bank", "ultra_long"):
            assert expected in buckets, f"Missing duration bucket: {expected}"
        # 权重之和 ~1.0
        total_w = sum(bs.weight for bs in bond_pc.bond_symbols)
        assert 0.95 <= total_w <= 1.05, f"Bond weights sum {total_w} not ~1.0"

    def test_plan_a_5comp_cb_no_comprehensive(self):
        """R87: CB 子策略不含 cb_comprehensive (Jaccard>0.30 修复)"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        import yaml
        with open(yaml_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        cb_subs = raw["pillars"]["cb"]["sub_strategies"]
        names = [s["name"] for s in cb_subs]
        assert "cb_comprehensive" not in names, (
            "cb_comprehensive must be removed (Jaccard=0.401>0.30)"
        )
        # 权重之和 ~1.0
        total_w = sum(s["weight"] for s in cb_subs)
        assert 0.95 <= total_w <= 1.05, f"CB sub-strategy weights sum {total_w}"

    def test_plan_a_5comp_cb_jaccard_compliance(self):
        """R87: CB 子策略只含低重叠策略对 (原则E [CN])"""
        yaml_path = Path(__file__).parent.parent.parent / "configs" / "paper" / "plan_a_5comp.yml"
        if not yaml_path.exists():
            pytest.skip("plan_a_5comp.yml not found")
        import yaml
        with open(yaml_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        cb_subs = raw["pillars"]["cb"]["sub_strategies"]
        names = {s["name"] for s in cb_subs}
        # 已知高重叠组合: cb_comprehensive 与任何小盘策略
        high_overlap_pairs = [
            {"cb_comprehensive", "small_size"},
            {"cb_comprehensive", "triple_low"},
        ]
        for pair in high_overlap_pairs:
            assert not pair.issubset(names), (
                f"High-overlap pair {pair} found in CB sub-strategies"
            )
