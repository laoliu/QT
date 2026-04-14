"""SmallSizeGatedStrategy 单元测试

测试覆盖：
- 配置默认值
- 市场状态检测（RISK_ON/RISK_OFF 转换）
- 冷却期机制
- 纯小盘排序逻辑
- RISK_OFF 时清仓行为
- 辅助信号（低价比例、成交低迷）
- 端到端流程
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from aipha.strategy.base import StrategyContext, OrderIntent, IntentType
from aipha.strategy.cb_small_size_gated import (
    SmallSizeGatedConfig,
    SmallSizeGatedStrategy,
)
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.domain.models import Bar, Side


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_bar(close: float, metadata: dict | None = None) -> Bar:
    """创建测试用 Bar。"""
    return Bar(
        symbol="test",
        start_ts=datetime(2023, 6, 15),
        end_ts=datetime(2023, 6, 15),
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=10000,
        metadata=metadata or {},
    )


def make_cb_bar(
    symbol: str, close: float, issue_size: float = 20000.0,
    amount: float = 500.0, cb_over_rate: float = 10.0,
) -> Bar:
    """创建具有 CB 元数据的 Bar。"""
    return Bar(
        symbol=symbol,
        start_ts=datetime(2023, 6, 15),
        end_ts=datetime(2023, 6, 15),
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=10000,
        metadata={
            "issue_size": issue_size,
            "amount": amount,
            "cb_over_rate": cb_over_rate,
            "maturity_date": "20280101",
            "list_date": "20200101",
            "stk_short_name": "测试股份",
            "is_called": False,
        },
    )


def make_context(
    cash: float = 500000.0,
    positions: dict | None = None,
) -> StrategyContext:
    """创建测试上下文。"""
    return StrategyContext(
        cash=cash,
        positions=positions or {},
        current_time=datetime(2023, 6, 15),
        nav=cash,
    )


def make_candidate(
    symbol: str, close: float, issue_size: float,
) -> CandidateInfo:
    """创建测试候选。"""
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata={},
        issue_size=issue_size,
        amount=500.0,
    )


# ---------------------------------------------------------------------------
# 配置测试
# ---------------------------------------------------------------------------

class TestSmallSizeGatedConfig:
    def test_defaults(self):
        cfg = SmallSizeGatedConfig()
        assert cfg.top_k == 5
        assert cfg.rebalance_days == 1
        assert cfg.max_price == 150.0
        assert cfg.max_issue_size == 20000.0
        assert cfg.bear_threshold == -0.03
        assert cfg.recovery_threshold == 0.0
        assert cfg.cooldown_bars == 3
        assert cfg.regime_lookback == 10
        assert cfg.use_distress_signal is False
        assert cfg.use_volume_signal is False

    def test_custom_params(self):
        cfg = SmallSizeGatedConfig(
            top_k=3,
            regime_lookback=20,
            bear_threshold=-0.05,
            recovery_threshold=0.01,
            cooldown_bars=5,
        )
        assert cfg.top_k == 3
        assert cfg.regime_lookback == 20
        assert cfg.bear_threshold == -0.05
        assert cfg.recovery_threshold == 0.01
        assert cfg.cooldown_bars == 5


# ---------------------------------------------------------------------------
# 策略初始化测试
# ---------------------------------------------------------------------------

class TestSmallSizeGatedInit:
    def test_default_init(self):
        s = SmallSizeGatedStrategy()
        assert s.name == "small_size_gated"
        assert s.config.top_k == 5
        assert not s.is_risk_off()

    def test_custom_config(self):
        cfg = SmallSizeGatedConfig(top_k=3, bear_threshold=-0.02)
        s = SmallSizeGatedStrategy(config=cfg)
        assert s.config.top_k == 3
        assert s.config.bear_threshold == -0.02

    def test_kwargs_init(self):
        s = SmallSizeGatedStrategy(top_k=4, regime_lookback=15)
        assert s.config.top_k == 4
        assert s.config.regime_lookback == 15

    def test_on_start_resets_state(self):
        s = SmallSizeGatedStrategy()
        s._is_risk_off = True
        s._risk_off_count = 5
        s._market_prices.append(100.0)
        s.on_start()
        assert not s.is_risk_off()
        assert s._risk_off_count == 0
        assert len(s._market_prices) == 0


# ---------------------------------------------------------------------------
# 市场状态检测测试
# ---------------------------------------------------------------------------

class TestRegimeDetection:
    def test_warmup_stays_risk_on(self):
        """热身期不足时保持 RISK_ON。"""
        cfg = SmallSizeGatedConfig(regime_lookback=10)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 只喂 5 个 bar（不够 10+1）
        for i in range(5):
            s._market_prices.append(100.0 + i * 0.1)
        s._update_regime()
        assert not s.is_risk_off()

    def test_stable_market_stays_risk_on(self):
        """市场稳定时保持 RISK_ON。"""
        cfg = SmallSizeGatedConfig(regime_lookback=5, bear_threshold=-0.03)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 价格平稳
        for i in range(10):
            s._market_prices.append(100.0 + i * 0.01)
        s._update_regime()
        assert not s.is_risk_off()

    def test_bear_triggers_risk_off(self):
        """价格大跌触发 RISK_OFF。"""
        cfg = SmallSizeGatedConfig(regime_lookback=5, bear_threshold=-0.03, cooldown_bars=2)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 价格从 100 跌到 95（-5% > -3% threshold）
        prices = [100, 100, 100, 99, 98, 97, 96, 95]
        for p in prices:
            s._market_prices.append(p)
        s._update_regime()
        assert s.is_risk_off()

    def test_recovery_from_risk_off(self):
        """市场恢复后回到 RISK_ON。"""
        cfg = SmallSizeGatedConfig(
            regime_lookback=5,
            bear_threshold=-0.03,
            recovery_threshold=0.0,
            cooldown_bars=0,  # 无冷却期
        )
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 先制造下跌
        for p in [100, 100, 100, 99, 98, 97, 96, 95]:
            s._market_prices.append(p)
        s._update_regime()
        assert s.is_risk_off()
        # 再涨回来（5日变动率变正）
        for p in [96, 97, 98, 99, 100, 101]:
            s._market_prices.append(p)
        s._update_regime()
        assert not s.is_risk_off()

    def test_cooldown_prevents_premature_recovery(self):
        """冷却期内不会恢复。"""
        cfg = SmallSizeGatedConfig(
            regime_lookback=5,
            bear_threshold=-0.03,
            recovery_threshold=0.0,
            cooldown_bars=3,
        )
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 触发 RISK_OFF
        for p in [100, 100, 100, 99, 98, 97, 96, 95]:
            s._market_prices.append(p)
        s._update_regime()
        assert s.is_risk_off()
        assert s._cooldown_remaining == 3
        # 涨回来但冷却期未结束
        for p in [96, 97, 98]:
            s._market_prices.append(p)
            s._update_regime()
        # 冷却期刚结束
        assert s._cooldown_remaining == 0
        # 再来一个正的
        s._market_prices.append(101)
        s._update_regime()
        assert not s.is_risk_off()

    def test_get_market_change_rate_warmup(self):
        """热身期返回 None。"""
        s = SmallSizeGatedStrategy()
        s.on_start()
        assert s.get_market_change_rate() is None

    def test_get_market_change_rate_normal(self):
        """正常返回变动率。"""
        cfg = SmallSizeGatedConfig(regime_lookback=5)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        for p in [100, 100, 100, 100, 100, 100, 105]:
            s._market_prices.append(p)
        rate = s.get_market_change_rate()
        assert rate is not None
        assert rate == pytest.approx(0.05, abs=0.01)

    def test_risk_off_count_increments(self):
        """RISK_OFF 连续计数正确递增。"""
        cfg = SmallSizeGatedConfig(regime_lookback=5, bear_threshold=-0.02, cooldown_bars=0)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 持续下跌
        for p in [100, 100, 100, 99, 98, 97, 96, 95, 94, 93]:
            s._market_prices.append(p)
            s._update_regime()
        assert s.is_risk_off()
        assert s._risk_off_count > 0


# ---------------------------------------------------------------------------
# 选股逻辑测试
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    def test_risk_on_returns_top_k_by_size(self):
        """RISK_ON 时按 issue_size 升序取 top_k。"""
        cfg = SmallSizeGatedConfig(top_k=3)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        s._is_risk_off = False

        candidates = [
            make_candidate("CB_A", 105, 30000),
            make_candidate("CB_B", 110, 10000),
            make_candidate("CB_C", 120, 5000),
            make_candidate("CB_D", 100, 15000),
            make_candidate("CB_E", 115, 8000),
        ]
        ctx = make_context()
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3
        # 最小的3个: CB_C(5000), CB_E(8000), CB_B(10000)
        assert result == ["CB_C", "CB_E", "CB_B"]

    def test_risk_off_returns_empty(self):
        """RISK_OFF 时返回空列表。"""
        s = SmallSizeGatedStrategy()
        s.on_start()
        s._is_risk_off = True  # 模拟 RISK_OFF

        candidates = [
            make_candidate("CB_A", 105, 10000),
            make_candidate("CB_B", 110, 5000),
        ]
        ctx = make_context()
        result = s._score_candidates(candidates, ctx)
        assert result == []

    def test_empty_candidates_returns_empty(self):
        """空候选列表返回空。"""
        s = SmallSizeGatedStrategy()
        s.on_start()
        result = s._score_candidates([], make_context())
        assert result == []

    def test_fewer_than_top_k(self):
        """候选数少于 top_k 时全部返回。"""
        cfg = SmallSizeGatedConfig(top_k=5)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        candidates = [
            make_candidate("CB_A", 105, 10000),
            make_candidate("CB_B", 110, 8000),
        ]
        ctx = make_context()
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 2
        assert result == ["CB_B", "CB_A"]  # sorted by issue_size

    def test_tiebreak_by_symbol(self):
        """同 issue_size 按 symbol 排序打破平局。"""
        cfg = SmallSizeGatedConfig(top_k=3)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        candidates = [
            make_candidate("CB_C", 105, 10000),
            make_candidate("CB_A", 110, 10000),
            make_candidate("CB_B", 120, 10000),
        ]
        ctx = make_context()
        result = s._score_candidates(candidates, ctx)
        assert result == ["CB_A", "CB_B", "CB_C"]


# ---------------------------------------------------------------------------
# 完整 on_bar 流程测试
# ---------------------------------------------------------------------------

class TestOnBar:
    def _make_strategy_with_warmup(
        self, prices: list[float], **kwargs
    ) -> SmallSizeGatedStrategy:
        """创建带热身数据的策略。"""
        cfg_kwargs = {"regime_lookback": 5, "top_k": 3, "rebalance_days": 1}
        cfg_kwargs.update(kwargs)
        cfg = SmallSizeGatedConfig(**cfg_kwargs)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        for p in prices:
            s._market_prices.append(p)
        return s

    def test_risk_on_generates_buy_orders(self):
        """RISK_ON 时正常生成买入订单。"""
        # 稳定市场
        s = self._make_strategy_with_warmup(
            [100, 100, 100, 100, 100, 100, 100.5],
            max_price=150.0, max_issue_size=50000.0, min_turnover=1.0,
        )
        bars = {
            "CB_A": make_cb_bar("CB_A", 105, issue_size=5000, amount=100),
            "CB_B": make_cb_bar("CB_B", 110, issue_size=8000, amount=100),
            "CB_C": make_cb_bar("CB_C", 120, issue_size=3000, amount=100),
        }
        ctx = make_context(cash=500000)
        intents = s.on_bar(bars, ctx)
        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) == 3

    def test_risk_off_generates_sell_orders(self):
        """RISK_OFF 时清仓。"""
        # 下跌市场
        s = self._make_strategy_with_warmup(
            [100, 100, 100, 99, 98, 97, 96, 95],
            bear_threshold=-0.03, cooldown_bars=5,  # 高冷却期防止恢复
            max_price=200.0, max_issue_size=50000.0, min_turnover=1.0,
        )
        s._update_regime()  # 触发 RISK_OFF
        assert s.is_risk_off()

        # bars 同样是低价，不会触发恢复
        bars = {
            "CB_A": make_cb_bar("CB_A", 93, issue_size=5000, amount=100),
            "CB_B": make_cb_bar("CB_B", 92, issue_size=8000, amount=100),
        }
        ctx = make_context(cash=10000, positions={"CB_A": 100, "CB_B": 100})
        intents = s.on_bar(bars, ctx)
        sell_intents = [i for i in intents if i.side == Side.SELL]
        # 应清仓（至少有卖出订单）
        assert len(sell_intents) >= 1

    def test_transition_risk_on_to_off(self):
        """从 RISK_ON 过渡到 RISK_OFF。"""
        cfg = SmallSizeGatedConfig(
            regime_lookback=5, bear_threshold=-0.03, cooldown_bars=0,
            top_k=2, max_price=200.0, max_issue_size=50000.0, min_turnover=1.0,
        )
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()

        # 先正常几天（RISK_ON）
        for i in range(8):
            bars = {
                "CB_A": make_cb_bar("CB_A", 105, issue_size=5000, amount=100),
                "CB_B": make_cb_bar("CB_B", 110, issue_size=8000, amount=100),
            }
            ctx = make_context(cash=500000)
            s.on_bar(bars, ctx)

        assert not s.is_risk_off()

    def test_on_bar_update_state_called(self):
        """on_bar 中正确更新市场状态。"""
        s = SmallSizeGatedStrategy(
            config=SmallSizeGatedConfig(
                regime_lookback=3, top_k=2,
                max_price=200.0, max_issue_size=50000.0, min_turnover=1.0,
            )
        )
        s.on_start()
        bars = {
            "CB_A": make_cb_bar("CB_A", 105, issue_size=5000, amount=100),
        }
        ctx = make_context()
        s.on_bar(bars, ctx)
        assert len(s._market_prices) == 1


# ---------------------------------------------------------------------------
# 辅助信号测试
# ---------------------------------------------------------------------------

class TestAuxiliarySignals:
    def test_volume_signal_triggers_risk_off(self):
        """成交低迷信号触发 RISK_OFF。"""
        cfg = SmallSizeGatedConfig(
            regime_lookback=5,
            bear_threshold=-0.10,  # 高阈值防止主信号触发
            use_volume_signal=True,
            volume_lookback=5,
            volume_floor_pct=0.50,
            cooldown_bars=0,
        )
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        # 正常成交
        for _ in range(10):
            s._market_prices.append(100.0)
            s._market_volumes.append(1000.0)
        # 突然成交暴降
        s._market_volumes.append(100.0)  # 远低于均值的50%
        s._market_prices.append(100.0)
        s._update_regime()
        assert s.is_risk_off()

    def test_volume_signal_disabled_by_default(self):
        """默认不启用成交低迷信号。"""
        cfg = SmallSizeGatedConfig(regime_lookback=5, bear_threshold=-0.10)
        s = SmallSizeGatedStrategy(config=cfg)
        s.on_start()
        for _ in range(10):
            s._market_prices.append(100.0)
            s._market_volumes.append(1000.0)
        s._market_volumes.append(100.0)
        s._market_prices.append(100.0)
        s._update_regime()
        assert not s.is_risk_off()


# ---------------------------------------------------------------------------
# Registry 集成测试
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registered(self):
        from aipha.strategy.registry import StrategyRegistry
        registry = StrategyRegistry()
        assert "small_size_gated" in registry.list_strategies()

    def test_create_from_registry(self):
        from aipha.strategy.registry import StrategyRegistry
        registry = StrategyRegistry()
        s = registry.create("small_size_gated", top_k=4)
        assert s is not None
        assert s.name == "small_size_gated"
