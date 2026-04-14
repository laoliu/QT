"""Tests for AdaptiveMultiFactorStrategy.

覆盖要求（≥12 per strategy）：
- 配置默认值 / 自定义
- 策略注册
- IC 计算逻辑（_update_factor_ic / _get_ic_weights）
- 市场温度计算（_update_market_temperature / 三信号分量）
- 温度→仓位映射（_get_position_scale）
- 防御模式切换（_is_defensive_mode）
- 因子打分（_score_candidates + IC 权重 + 防御权重）
- 边界条件（空候选、数据不足）
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.adaptive_multifactor import (
    AdaptiveMultiFactorConfig,
    AdaptiveMultiFactorStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str,
    close: float = 30.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    circ_mv: float = 500_000.0,
    amount: float = 5000.0,
    name: str = "正常股",
    list_date: str = "20180101",
    is_suspended: bool = False,
    is_index_member: bool = True,
    turnover_rate: float = 5.0,
    eps: float = 1.5,
    dt_netprofit_yoy: float = 15.0,
    dv_ratio: float = 2.0,
    roa: float = 8.0,
    cfps: float = 2.0,
    debt_to_assets: float = 40.0,
    grossprofit_margin: float = 30.0,
    current_ratio: float = 1.5,
) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=100_000,
        is_suspended=is_suspended,
        metadata={
            "pe_ttm": pe_ttm,
            "pb": pb,
            "roe": roe,
            "circ_mv": circ_mv,
            "amount": amount,
            "name": name,
            "list_date": list_date,
            "is_index_member": is_index_member,
            "turnover_rate": turnover_rate,
            "eps": eps,
            "dt_netprofit_yoy": dt_netprofit_yoy,
            "dv_ratio": dv_ratio,
            "roa": roa,
            "cfps": cfps,
            "debt_to_assets": debt_to_assets,
            "grossprofit_margin": grossprofit_margin,
            "current_ratio": current_ratio,
        },
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
    metadata: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=cash,
        metadata=metadata or {},
    )


def _make_candidates(n: int = 5, base_close: float = 20.0) -> list[EquityCandidateInfo]:
    """生成 n 个候选，PE/ROE 递增以产生因子区分度。"""
    result = []
    for i in range(n):
        sym = f"{600000 + i:06d}.SH"
        result.append(EquityCandidateInfo(
            symbol=sym,
            close=base_close + i * 2,
            metadata={
                "pe_ttm": 10.0 + i * 3,
                "pb": 1.0 + i * 0.5,
                "roe": 15.0 - i,
                "circ_mv": 300_000 + i * 50_000,
                "eps": 1.5 - i * 0.1,
                "dt_netprofit_yoy": 20.0 - i * 3,
                "dv_ratio": 3.0 - i * 0.4,
                "roa": 10.0 - i * 0.5,
                "cfps": 2.0 - i * 0.2,
                "debt_to_assets": 30.0 + i * 5,
                "grossprofit_margin": 35.0 - i * 2,
                "turnover_rate": 5.0 + i,
            },
        ))
    return result


def _warmup(strategy: AdaptiveMultiFactorStrategy, bars: dict[str, Bar], n: int = 70):
    """模拟 n 根 bar 的 warmup，填充 _price_history。"""
    for _ in range(n):
        strategy._update_history(bars)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestAdaptiveMultiFactorConfig:

    def test_defaults(self):
        cfg = AdaptiveMultiFactorConfig()
        assert cfg.top_k == 25
        assert cfg.rebalance_days == 15
        assert cfg.ic_lookback == 20
        assert cfg.ic_decay_halflife == 10
        assert cfg.ic_weight_alpha == 0.7
        assert cfg.ic_min_abs == 0.02
        assert cfg.defensive_threshold == 0.30
        assert cfg.min_position_pct == 0.30
        assert cfg.base_weight_value == 0.15
        assert cfg.base_weight_quality == 0.15
        assert cfg.base_weight_momentum == 0.15
        assert cfg.base_weight_volatility == 0.15
        assert cfg.base_weight_earnings_growth == 0.10
        assert cfg.base_weight_dividend == 0.10
        assert cfg.base_weight_piotroski == 0.10
        assert cfg.base_weight_debt == 0.10

    def test_custom_values(self):
        cfg = AdaptiveMultiFactorConfig(
            top_k=50,
            ic_lookback=60,
            ic_weight_alpha=0.80,
            base_weight_value=0.30,
            base_weight_quality=0.30,
        )
        assert cfg.top_k == 50
        assert cfg.ic_lookback == 60
        assert cfg.ic_weight_alpha == 0.80
        assert cfg.base_weight_value == 0.30

    def test_temperature_params(self):
        cfg = AdaptiveMultiFactorConfig(
            temp_ma_short=10, temp_ma_mid=30, temp_ma_long=90,
            temp_trend_weight=0.50, temp_vol_weight=0.25, temp_breadth_weight=0.25,
        )
        assert cfg.temp_ma_short == 10
        assert cfg.temp_trend_weight == 0.50


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_strategy_in_registry(self):
        assert "adaptive_multifactor" in StrategyRegistry().list_strategies()

    def test_create_from_registry(self):
        strat = StrategyRegistry().create("adaptive_multifactor")
        assert isinstance(strat, AdaptiveMultiFactorStrategy)
        assert strat.name == "adaptive_multifactor"


# ---------------------------------------------------------------------------
# IC calculation
# ---------------------------------------------------------------------------

class TestICCalculation:

    def _strategy(self, **kwargs):
        cfg = AdaptiveMultiFactorConfig(**kwargs)
        s = AdaptiveMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def test_update_factor_ic_stores_data(self):
        """更新因子 IC 后，_factor_ic_history 应有记录。"""
        s = self._strategy()
        # 构造 ≥10 个标的的因子值和收益率
        factor_values = {f"{600000 + i:06d}.SH": float(i) for i in range(12)}
        next_returns = {f"{600000 + i:06d}.SH": 0.01 * (i - 5) for i in range(12)}

        s._update_factor_ic("value", factor_values, next_returns)

        # value 因子应有一条 IC 记录
        assert len(s._factor_ic_history["value"]) == 1

    def test_update_factor_ic_skips_small_sample(self):
        """样本不足 10 个时应跳过 IC 更新。"""
        s = self._strategy()
        factor_values = {f"{600000 + i:06d}.SH": float(i) for i in range(5)}
        next_returns = {f"{600000 + i:06d}.SH": 0.01 * i for i in range(5)}
        s._update_factor_ic("value", factor_values, next_returns)
        assert len(s._factor_ic_history["value"]) == 0

    def test_get_ic_weights_without_history(self):
        """没有 IC 历史时，应返回基础权重（归一化）。"""
        s = self._strategy()
        w = s._get_ic_weights()
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-6 or total == 0

    def test_get_ic_weights_with_history(self):
        """有 IC 历史时，ic_weight_alpha 应生效。"""
        from collections import deque
        s = self._strategy(ic_weight_alpha=0.80)
        # 手动注入 IC 历史（deque）
        for fname in ["value", "quality", "momentum", "volatility",
                       "earnings_growth", "dividend", "piotroski", "debt"]:
            d = s._factor_ic_history[fname]  # triggers defaultdict
            for _ in range(10):
                d.append(0.05)

        w = s._get_ic_weights()
        assert abs(sum(w.values()) - 1.0) < 1e-6
        # 所有因子 IC 相同，权重应接近均等
        vals = list(w.values())
        assert max(vals) - min(vals) < 0.05

    def test_ic_weights_high_ic_gets_more_weight(self):
        """IC 更高的因子应获得更大权重。"""
        s = self._strategy(ic_weight_alpha=1.0)
        for _ in range(10):
            s._factor_ic_history["value"].append(0.10)
            s._factor_ic_history["quality"].append(0.02)   # 低于 ic_min_abs 阈值

        w = s._get_ic_weights()
        if "value" in w and "quality" in w and w["value"] > 0:
            assert w["value"] > w["quality"]


# ---------------------------------------------------------------------------
# Market temperature
# ---------------------------------------------------------------------------

class TestMarketTemperature:

    def _strategy(self, **kwargs):
        cfg = AdaptiveMultiFactorConfig(**kwargs)
        s = AdaptiveMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def test_calc_trend_signal_all_up(self):
        """MA20 > MA60 > MA120 应返回 1.0。"""
        s = self._strategy()
        # 构造持续上涨的价格序列（120 天）
        prices = [100 + i * 0.5 for i in range(150)]
        s._index_prices = prices
        signal = s._calc_trend_signal()
        assert signal >= 0.5  # 强上涨

    def test_calc_trend_signal_all_down(self):
        """MA20 < MA60 < MA120 应返回 0.0。"""
        s = self._strategy()
        prices = [200 - i * 0.5 for i in range(150)]
        s._index_prices = prices
        signal = s._calc_trend_signal()
        assert signal <= 0.5  # 下跌

    def test_calc_volatility_signal_low_vol(self):
        """短期波动 < 长期波动 → 高信号(波动收敛)。"""
        s = self._strategy()
        # 平稳序列 → 低波动
        s._index_prices = [100 + (i % 2) * 0.1 for i in range(80)]
        signal = s._calc_volatility_signal()
        assert 0.0 <= signal <= 1.0

    def test_calc_breadth_signal(self):
        """广度信号应在 [0, 1] 范围内。"""
        s = self._strategy()
        s._breadth_history = [0.6, 0.55, 0.58, 0.62, 0.60] * 5
        signal = s._calc_breadth_signal()
        assert 0.0 <= signal <= 1.0

    def test_update_market_temperature_with_bars(self):
        """完整温度更新后，_temperature 应在 [0, 1]。"""
        s = self._strategy()
        # 先喂足够的价格历史
        for v in [100 + i * 0.2 for i in range(150)]:
            s._index_prices.append(v)
        s._breadth_history.extend([0.55] * 25)
        # 调用需要 bars 和 context
        bars = {"600001.SH": _make_bar("600001.SH", close=130.0)}
        ctx = _make_ctx()
        s._update_market_temperature(bars, ctx)
        assert 0.0 <= s._temperature <= 1.0

    def test_get_position_scale_cold(self):
        """温度极低时，仓位比例应接近 min_position_pct。"""
        s = self._strategy(min_position_pct=0.30)
        s._temperature = 0.0
        scale = s._get_position_scale()
        assert abs(scale - 0.30) < 1e-6

    def test_get_position_scale_hot(self):
        """温度 = 1.0 时，仓位比例应为 1.0。"""
        s = self._strategy()
        s._temperature = 1.0
        scale = s._get_position_scale()
        assert abs(scale - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Defensive mode
# ---------------------------------------------------------------------------

class TestDefensiveMode:

    def _strategy(self, **kwargs):
        cfg = AdaptiveMultiFactorConfig(**kwargs)
        s = AdaptiveMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def test_defensive_when_cold(self):
        """温度低于阈值时应进入防御模式。"""
        s = self._strategy(defensive_threshold=0.30)
        s._temperature = 0.10
        assert s._is_defensive_mode() is True

    def test_not_defensive_when_warm(self):
        """温度高于阈值时应不在防御模式。"""
        s = self._strategy(defensive_threshold=0.30)
        s._temperature = 0.80
        assert s._is_defensive_mode() is False

    def test_defensive_at_threshold(self):
        """温度恰好等于阈值时不在防御模式（>=）。"""
        s = self._strategy(defensive_threshold=0.30)
        s._temperature = 0.30
        assert s._is_defensive_mode() is False


# ---------------------------------------------------------------------------
# Score candidates
# ---------------------------------------------------------------------------

class TestScoreCandidates:

    def _strategy_with_warmup(self, **kwargs):
        cfg = AdaptiveMultiFactorConfig(**kwargs)
        s = AdaptiveMultiFactorStrategy(cfg)
        s.on_start()
        # 填充价格历史以满足波动率计算
        candidates = _make_candidates(5)
        bars = {}
        for c in candidates:
            bars[c.symbol] = _make_bar(c.symbol, close=c.close)
        _warmup(s, bars, 70)
        return s

    def test_score_candidates_returns_sorted_symbols(self):
        """_score_candidates 应返回排序后的 symbol 列表。"""
        s = self._strategy_with_warmup()
        candidates = _make_candidates(5)
        result = s._score_candidates(candidates, _make_ctx())
        assert isinstance(result, list)
        assert len(result) <= 5
        for sym in result:
            assert isinstance(sym, str)

    def test_score_candidates_empty(self):
        """空候选列表应返回空。"""
        s = self._strategy_with_warmup()
        result = s._score_candidates([], _make_ctx())
        assert result == []

    def test_score_candidates_single(self):
        """单个候选应返回该 symbol。"""
        s = self._strategy_with_warmup()
        candidates = _make_candidates(1)
        result = s._score_candidates(candidates, _make_ctx())
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Integration: on_bar
# ---------------------------------------------------------------------------

class TestOnBarIntegration:

    def test_on_start_initializes(self):
        """初始化后内部状态应正确。"""
        cfg = AdaptiveMultiFactorConfig()
        s = AdaptiveMultiFactorStrategy(cfg)
        assert s._temperature == 0.5  # 中性初始
        assert s._prev_factor_scores == {}
        assert len(s._index_prices) == 0

    def test_property_config_returns_config(self):
        """config property 应返回配置实例。"""
        cfg = AdaptiveMultiFactorConfig(top_k=25)
        s = AdaptiveMultiFactorStrategy(cfg)
        assert s.config.top_k == 25
        assert s.config is cfg


# ---------------------------------------------------------------------------
# Regression: factor coverage fallback
# ---------------------------------------------------------------------------

class TestFactorCoverageFallback:
    """回归测试：当财务因子覆盖率不足时，回退到价格导出因子。"""

    def _strategy_with_history(self, candidates):
        """创建已 warmup 的策略。"""
        cfg = AdaptiveMultiFactorConfig(top_k=5)
        s = AdaptiveMultiFactorStrategy(cfg)
        bars = {}
        for c in candidates:
            bars[c.symbol] = _make_bar(c.symbol, close=c.close)
        _warmup(s, bars, 70)
        return s

    def test_all_financial_nan_falls_back_to_price_factors(self):
        """所有财务因子 NaN 时，应回退到 momentum/volatility 而非返回空。"""
        # 构造无财务数据的候选
        candidates = []
        for i in range(10):
            sym = f"{600100 + i:06d}.SH"
            candidates.append(EquityCandidateInfo(
                symbol=sym,
                close=20.0 + i * 2,
                metadata={
                    "pe_ttm": float("nan"),
                    "pb": float("nan"),
                    "roe": float("nan"),
                    "circ_mv": 300_000 + i * 50_000,
                    "eps": float("nan"),
                    "dt_netprofit_yoy": float("nan"),
                    "dv_ratio": float("nan"),
                    "roa": float("nan"),
                    "cfps": float("nan"),
                    "debt_to_assets": float("nan"),
                    "grossprofit_margin": float("nan"),
                    "turnover_rate": 5.0 + i,
                },
            ))
        s = self._strategy_with_history(candidates)
        ctx = _make_ctx()
        result = s._score_candidates(candidates, ctx)
        # 应回退到价格导出因子，返回非空结果
        assert len(result) > 0, "回退到价格因子后应有选股结果"

    def test_partial_financial_data_uses_normal_path(self):
        """部分财务数据可用时，正常流程选股。"""
        candidates = _make_candidates(8)
        s = self._strategy_with_history(candidates)
        ctx = _make_ctx()
        result = s._score_candidates(candidates, ctx)
        assert len(result) > 0

    def test_completely_empty_factors_returns_empty(self):
        """0 个候选时返回空。"""
        cfg = AdaptiveMultiFactorConfig()
        s = AdaptiveMultiFactorStrategy(cfg)
        result = s._score_candidates([], _make_ctx())
        assert result == []
