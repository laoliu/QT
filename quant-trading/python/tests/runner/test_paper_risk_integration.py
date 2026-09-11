"""
PaperEngine <-> 统一仓位缩放器集成测试

验证 R81 §4.3.1 约束：
- PaperEngine 正确初始化 UnifiedPositionSizer
- NAV 历史追踪与缩放因子计算
- risk_position_scale 注入 LiveRunner 执行链
- YAML 配置加载 ML 风控字段

本文件原先针对 ``ml_risk_controls.CombinedPositionSizer`` 及其
``compute_scale(nav_series) -> dict`` 接口。该类已按 §4.3.1（回测/模拟/
实盘三路径共享同一个缩放器）并入 ``risk/position_sizing.py`` 的
``UnifiedPositionSizer``，NAV 序列入参也改成了「累积日收益率 + 当前 NAV」
的 ``compute_scale_detail()`` —— 与 PaperEngine 心跳的真实调用口径一致
（paper_engine.py:1061-1085）。这里跟着改测试而不是把旧名字加回去：
再加一个返回 dict 的 compute_scale 就等于给同一个概念留两套接口，
而 §4.3.1 正是为了消灭这种双轨。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from aipha.data.akshare_realtime_provider import MarketType
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.domain.models import Bar
from aipha.risk.ml_risk_controls import VolTargetScaler
from aipha.risk.position_sizing import UnifiedPositionSizer
from aipha.runner.live_runner import RunnerMode
from aipha.runner.paper_engine import PaperEngine, PaperEngineConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _replay_nav(
    sizer: UnifiedPositionSizer, nav_values: list[float]
) -> dict[str, object]:
    """按 PaperEngine 心跳的口径回放一段 NAV 序列，返回最后一次的明细。

    必须逐日喂：``UnifiedPositionSizer`` 的 MDD 峰值和波动率窗口都是
    **有状态**的（``_mdd_gradient_peak`` / ``VolTargetScaler._returns``），
    只把最后一个 NAV 传进去算不出回撤。这也正是 paper_engine.py:1067-1073
    的做法 —— 测的是生产路径本身，不是另写一遍算法。
    """
    detail: dict[str, object] = {}
    for i in range(1, len(nav_values)):
        daily_rets = [
            nav_values[j] / nav_values[j - 1] - 1 for j in range(1, i + 1)
        ]
        detail = sizer.compute_scale_detail(daily_rets, float(nav_values[i]))
    return detail


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


class _DummyStrategy:
    name = "test_risk"
    def on_start(self): pass
    def on_stop(self): pass
    def on_bars(self, context, bars): return []


@pytest.fixture
def closed_calendar():
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.CLOSED
    cal.is_trading_day.return_value = False
    return cal


@pytest.fixture
def base_config(tmp_path: Path) -> PaperEngineConfig:
    return PaperEngineConfig(
        strategy_name="test_risk",
        symbols=["127045"],
        market_type=MarketType.CB,
        initial_capital=1_000_000.0,
        state_dir=tmp_path / "state",
        audit_log_dir=tmp_path / "audit",
        monitoring_enabled=False,
        metrics_enabled=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperRiskConfig:
    """ML 风控配置字段测试"""

    def test_defaults_enabled(self):
        cfg = PaperEngineConfig()
        assert cfg.ml_risk_controls_enabled is True
        assert cfg.ml_risk_vol_target == 0.20
        assert cfg.ml_risk_vol_lookback == 60
        assert cfg.ml_risk_max_stock_pct == 0.03
        assert cfg.ml_risk_mdd_levels is None

    def test_from_yaml_ml_risk(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "cb_test"
  symbols: ["127045"]
  initial_capital: 500000
  ml_risk_controls_enabled: true
  ml_risk_vol_target: 0.15
  ml_risk_vol_lookback: 30
  ml_risk_max_stock_pct: 0.05
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(p)
        assert cfg.ml_risk_controls_enabled is True
        assert cfg.ml_risk_vol_target == 0.15
        assert cfg.ml_risk_vol_lookback == 30
        assert cfg.ml_risk_max_stock_pct == 0.05

    def test_from_yaml_disabled(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "cb_test"
  ml_risk_controls_enabled: false
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(p)
        assert cfg.ml_risk_controls_enabled is False


class TestPaperRiskInit:
    """PaperEngine 初始化 UnifiedPositionSizer"""

    def test_sizer_created_when_enabled(
        self, base_config, closed_calendar
    ):
        base_config.ml_risk_controls_enabled = True
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine._position_sizer is not None
        assert isinstance(engine._position_sizer, UnifiedPositionSizer)
        assert engine._risk_scale == 1.0
        assert len(engine._nav_history) == 0

    def test_sizer_not_created_when_disabled(
        self, base_config, closed_calendar
    ):
        base_config.ml_risk_controls_enabled = False
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine._position_sizer is None
        assert engine._risk_scale == 1.0

    def test_custom_vol_params(self, base_config, closed_calendar):
        base_config.ml_risk_vol_target = 0.10
        base_config.ml_risk_vol_lookback = 30
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        sizer = engine._position_sizer
        assert sizer is not None
        assert sizer.vol_scaler.vol_target == 0.10
        assert sizer.vol_scaler.lookback == 30

    def test_custom_mdd_levels(self, base_config, closed_calendar):
        custom = [(0.05, 1.0), (0.10, 0.5), (1.0, 0.0)]
        base_config.ml_risk_mdd_levels = custom
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        sizer = engine._position_sizer
        assert sizer is not None
        assert sizer.mdd_deleverager.levels == custom

    def test_risk_scale_property(self, base_config, closed_calendar):
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine.risk_scale == 1.0

    def test_runner_scale_default(self, base_config, closed_calendar):
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine.runner.risk_position_scale == 1.0


class TestScaleComputation:
    """UnifiedPositionSizer 缩放因子计算集成

    统一用 ``from_paper_config()`` 构造：裸 ``UnifiedPositionSizer()`` 的
    ``mdd_gradient_levels`` 为 None（回测路径按需注入），MDD 梯度整条是
    关闭的；Paper/Live 走的是 from_paper_config，默认带
    ``DEFAULT_MDD_LEVELS``。测哪条路径就构造哪条路径的实例。
    """

    def test_no_drawdown_scale_one(self):
        sizer = UnifiedPositionSizer.from_paper_config()
        nav = [100.0 + i * 0.1 for i in range(100)]
        result = _replay_nav(sizer, nav)
        assert result["final_scale"] >= 0.99
        assert result["mdd_triggered"] is False

    def test_large_drawdown_reduces_scale(self):
        sizer = UnifiedPositionSizer.from_paper_config()
        # 峰值 200，当前 160 → MDD = 20%
        nav_values = [float(v) for v in range(100, 201)] + [160.0]
        result = _replay_nav(sizer, nav_values)
        assert result["mdd"] == pytest.approx(0.20, abs=0.01)
        assert result["mdd_scale"] < 1.0
        assert result["mdd_triggered"] is True
        # 回撤减仓必须真的传导到最终缩放，而不是只体现在明细里
        assert result["final_scale"] <= result["mdd_scale"]

    def test_catastrophic_drawdown_minimal_scale(self):
        sizer = UnifiedPositionSizer.from_paper_config()
        # 峰值 200，当前 140 → MDD = 30%
        nav_values = [float(v) for v in range(100, 201)] + [140.0]
        result = _replay_nav(sizer, nav_values)
        assert result["mdd_scale"] <= 0.30

    def test_vol_scaler_high_vol(self):
        """高波动率降低仓位"""
        scaler = VolTargetScaler(vol_target=0.10, lookback=20)
        # 制造高波动 NAV
        import numpy as np
        np.random.seed(42)
        rets = np.random.normal(0, 0.05, 100)  # 日均 5% 波动
        nav = pd.Series((1 + rets).cumprod() * 100)
        scale = scaler.get_scale(nav)
        assert scale < 1.0  # 高波动 → 降仓

    def test_vol_scaler_low_vol(self):
        """低波动率可能加仓"""
        scaler = VolTargetScaler(vol_target=0.30, lookback=20)
        import numpy as np
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.003, 100)  # 日均 0.3% 波动
        nav = pd.Series((1 + rets).cumprod() * 100)
        scale = scaler.get_scale(nav)
        assert scale > 1.0  # 低波动 → 可加仓


class TestScaleInjection:
    """缩放因子注入 LiveRunner 执行链"""

    def test_scale_injected_via_heartbeat(
        self, base_config, closed_calendar
    ):
        """PaperEngine heartbeat 更新 runner.risk_position_scale"""
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        # 模拟 NAV 下跌 → MDD 15%
        nav_values = [1_000_000.0 + i * 1000 for i in range(50)]
        # 峰值 1049000, 跌到 891650 → MDD 15%
        nav_values.append(1_049_000 * 0.85)
        engine._nav_history.extend(nav_values)

        info = _replay_nav(engine._position_sizer, nav_values)
        engine._risk_scale = info["final_scale"]
        engine._runner.risk_position_scale = engine._risk_scale

        assert info["mdd"] == pytest.approx(0.15, abs=0.005)

        assert engine._runner.risk_position_scale < 1.0
        assert engine.risk_scale == engine._runner.risk_position_scale

    def test_scale_not_injected_when_disabled(
        self, base_config, closed_calendar
    ):
        """禁用时 risk_position_scale 保持 1.0"""
        base_config.ml_risk_controls_enabled = False
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine._runner.risk_position_scale == 1.0
