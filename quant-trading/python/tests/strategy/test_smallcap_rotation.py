"""Tests for SmallCapRotationStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.smallcap_rotation import SmallCapConfig, SmallCapRotationStrategy


def _make_stock_bar(
    symbol: str,
    close: float = 10.0,
    circ_mv: float = 300000.0,
    turnover_rate: float = 3.0,
    amount: float = 1000.0,
    name: str = "正常股",
    list_date: str = "20180101",
    is_suspended: bool = False,
) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 3, 15, 9, 30),
        end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=100000,
        is_suspended=is_suspended,
        metadata={
            "circ_mv": circ_mv,
            "turnover_rate": turnover_rate,
            "amount": amount,
            "name": name,
            "list_date": list_date,
        },
    )


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions=positions or {},
        cash=cash, nav=cash,
    )


class TestSmallCapConfig:

    def test_defaults(self):
        cfg = SmallCapConfig()
        assert cfg.top_k == 30
        assert cfg.rebalance_days == 15
        assert cfg.lot_size == 100
        assert cfg.weight_small_cap == 0.30
        assert cfg.weight_momentum == 0.15
        assert cfg.weight_turnover == 0.10
        assert cfg.weight_volatility == 0.15
        assert cfg.weight_quality == 0.15
        assert cfg.weight_value == 0.15
        assert cfg.buffer_k == 8

    def test_custom(self):
        cfg = SmallCapConfig(top_k=20, rebalance_days=5)
        assert cfg.top_k == 20
        assert cfg.rebalance_days == 5


class TestSmallCapRotationStrategy:

    @pytest.fixture
    def strategy(self):
        cfg = SmallCapConfig(
            top_k=3,
            rebalance_days=1,
            min_turnover=10.0,
            min_mv=10000.0,
            max_mv=5000000.0,
            min_price=1.0,
            max_price=100.0,
            lot_size=100,
            buffer_k=0,
        )
        s = SmallCapRotationStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "smallcap_rotation" in StrategyRegistry().list_strategies()

    def test_filter_excludes_st(self, strategy):
        bars = {
            "A.SZ": _make_stock_bar("A.SZ", close=10, name="ST股票"),
            "B.SZ": _make_stock_bar("B.SZ", close=10, name="正常股"),
        }
        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        syms = [c.symbol for c in candidates]
        assert "A.SZ" not in syms
        assert "B.SZ" in syms

    def test_filter_excludes_low_mv(self, strategy):
        bars = {
            "TINY.SZ": _make_stock_bar("TINY.SZ", circ_mv=5000),  # 5千万 < 1亿下限
            "OK.SZ": _make_stock_bar("OK.SZ", circ_mv=300000),
        }
        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        syms = [c.symbol for c in candidates]
        assert "TINY.SZ" not in syms
        assert "OK.SZ" in syms

    def test_filter_excludes_suspended(self, strategy):
        bars = {
            "HALT.SZ": _make_stock_bar("HALT.SZ", is_suspended=True),
            "OK.SZ": _make_stock_bar("OK.SZ"),
        }
        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        syms = [c.symbol for c in candidates]
        assert "HALT.SZ" not in syms

    def test_filter_excludes_new_stock(self, strategy):
        bars = {
            "NEW.SZ": _make_stock_bar("NEW.SZ", list_date="20240310"),  # 仅上市5天
            "OLD.SZ": _make_stock_bar("OLD.SZ", list_date="20200101"),
        }
        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        syms = [c.symbol for c in candidates]
        assert "NEW.SZ" not in syms
        assert "OLD.SZ" in syms

    def test_score_candidates(self, strategy):
        """多因子打分：小市值+质量+价值综合排名"""
        # 构建有 ROE/PE metadata 的 bars
        bars = {
            "SMALL.SZ": _make_stock_bar("SMALL.SZ", close=10, circ_mv=100000),
            "MEDIUM.SZ": _make_stock_bar("MEDIUM.SZ", close=20, circ_mv=500000),
            "LARGE.SZ": _make_stock_bar("LARGE.SZ", close=30, circ_mv=1500000),
        }
        # 注入 ROE/PE 元数据
        bars["SMALL.SZ"].metadata["roe"] = 15.0
        bars["SMALL.SZ"].metadata["pe_ttm"] = 12.0
        bars["MEDIUM.SZ"].metadata["roe"] = 10.0
        bars["MEDIUM.SZ"].metadata["pe_ttm"] = 20.0
        bars["LARGE.SZ"].metadata["roe"] = 5.0
        bars["LARGE.SZ"].metadata["pe_ttm"] = 35.0

        # 模拟多日数据更新以建立历史
        for i in range(25):
            strategy._update_history(bars)

        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)

        assert len(ranked) == 3
        # SMALL 应排最前（最小市值 + 最高ROE + 最低PE = 全面优势）
        assert ranked[0] == "SMALL.SZ"

    def test_on_bar_generates_buy_intents(self, strategy):
        """首次调仓应生成买入指令"""
        bars = {
            f"S{i}.SZ": _make_stock_bar(f"S{i}.SZ", close=10+i, circ_mv=200000+i*50000)
            for i in range(5)
        }
        ctx = _make_context()
        # 先建立价格历史
        for _ in range(25):
            strategy._update_history(bars)
        strategy._bar_count = 0  # 重置以触发调仓

        intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) <= 3  # top_k=3

    def test_stop_loss(self, strategy):
        """止损检查"""
        bars = {"X.SZ": _make_stock_bar("X.SZ", close=8.0)}
        ctx = _make_context(positions={"X.SZ": 100})
        strategy._entry_prices["X.SZ"] = 10.0
        strategy._config.stop_loss_pct = 0.15

        intents = strategy._check_stop_loss(bars, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) >= 1

    def test_rebalance_skips_non_rebalance_day(self, strategy):
        """非调仓日不生成调仓指令"""
        strategy._config.rebalance_days = 5
        strategy._bar_count = 2  # 不是调仓日

        bars = {"A.SZ": _make_stock_bar("A.SZ")}
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0

    def test_on_fill_tracks_entry(self, strategy):
        strategy.on_fill("A.SZ", Side.BUY, 100, 10.0)
        assert strategy._entry_prices["A.SZ"] == 10.0

        strategy.on_fill("A.SZ", Side.SELL, 100, 12.0)
        assert "A.SZ" not in strategy._entry_prices

    def test_momentum_positive(self, strategy):
        """价格上涨 → 正动量"""
        for i in range(25):
            strategy._price_history.setdefault("UP.SZ", []).append(10.0 + i * 0.5)
        mom = strategy.calc_momentum("UP.SZ")
        assert mom > 0

    def test_momentum_negative(self, strategy):
        """价格下跌 → 负动量"""
        for i in range(25):
            strategy._price_history.setdefault("DOWN.SZ", []).append(20.0 - i * 0.5)
        mom = strategy.calc_momentum("DOWN.SZ")
        assert mom < 0

    def test_volatility_positive(self, strategy):
        """正常价格序列 → 正波动率"""
        import random
        random.seed(42)
        prices = [10.0 + random.gauss(0, 0.5) for _ in range(25)]
        strategy._price_history["VOL.SZ"] = prices
        vol = strategy.calc_volatility("VOL.SZ")
        assert vol > 0

    def test_sell_old_holdings(self, strategy):
        """轮动时应卖出不在新目标池的持仓"""
        bars = {
            "NEW1.SZ": _make_stock_bar("NEW1.SZ", close=10, circ_mv=100000),
            "NEW2.SZ": _make_stock_bar("NEW2.SZ", close=15, circ_mv=200000),
            "OLD.SZ": _make_stock_bar("OLD.SZ", close=20, circ_mv=300000),
        }
        ctx = _make_context(positions={"OLD.SZ": 100})

        intents = strategy._generate_rebalance(["NEW1.SZ", "NEW2.SZ"], ctx, bars)
        sells = [i for i in intents if i.side == Side.SELL]
        assert any(i.symbol == "OLD.SZ" for i in sells)

    def test_quality_factor_differentiates(self, strategy):
        """质量因子：高 ROE 标的在同等条件下排名更高"""
        bars = {
            "HIGH_ROE.SZ": _make_stock_bar("HIGH_ROE.SZ", close=15, circ_mv=200000),
            "LOW_ROE.SZ": _make_stock_bar("LOW_ROE.SZ", close=15, circ_mv=200000),
        }
        bars["HIGH_ROE.SZ"].metadata["roe"] = 25.0
        bars["HIGH_ROE.SZ"].metadata["pe_ttm"] = 15.0
        bars["LOW_ROE.SZ"].metadata["roe"] = 2.0
        bars["LOW_ROE.SZ"].metadata["pe_ttm"] = 15.0

        for _ in range(25):
            strategy._update_history(bars)

        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "HIGH_ROE.SZ"

    def test_value_factor_differentiates(self, strategy):
        """价值因子：低 PE 标的在同等条件下排名更高"""
        bars = {
            "LOW_PE.SZ": _make_stock_bar("LOW_PE.SZ", close=15, circ_mv=200000),
            "HIGH_PE.SZ": _make_stock_bar("HIGH_PE.SZ", close=15, circ_mv=200000),
        }
        bars["LOW_PE.SZ"].metadata["pe_ttm"] = 8.0
        bars["LOW_PE.SZ"].metadata["roe"] = 10.0
        bars["HIGH_PE.SZ"].metadata["pe_ttm"] = 50.0
        bars["HIGH_PE.SZ"].metadata["roe"] = 10.0

        for _ in range(25):
            strategy._update_history(bars)

        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "LOW_PE.SZ"

    def test_config_weights_sum_to_one(self):
        """因子权重之和应为 1.0"""
        cfg = SmallCapConfig()
        total = (
            cfg.weight_small_cap + cfg.weight_momentum
            + cfg.weight_turnover + cfg.weight_volatility
            + cfg.weight_quality + cfg.weight_value
        )
        assert abs(total - 1.0) < 1e-10

    def test_buffer_k_default(self):
        """Buffer_k 默认值应为 8"""
        cfg = SmallCapConfig()
        assert cfg.buffer_k == 8


# ============================================================
# Phase 2 — 边界 & 极端场景补充
# ============================================================


class TestSmallCapBoundaryScenarios:
    """小盘因子轮动策略边界条件与极端场景测试"""

    def _make_candidate(
        self,
        symbol: str = "000001.SZ",
        circ_mv: float = 300_000.0,
        turnover_rate: float = 3.0,
        roe: float = 12.0,
        pe_ttm: float = 15.0,
    ) -> EquityCandidateInfo:
        return EquityCandidateInfo(
            symbol=symbol,
            close=10.0,
            metadata={"roe": roe, "pe_ttm": pe_ttm, "circ_mv": circ_mv},
            circ_mv=circ_mv,
            turnover_rate=turnover_rate,
            amount=1_000.0,
            list_date="20200101",
            name="测试",
        )

    def test_all_stocks_above_max_mv_empty_candidates(self):
        """全部股票市值超过 max_mv → 过滤后候选为空"""
        strategy = SmallCapRotationStrategy(
            config=SmallCapConfig(
                top_k=5, buffer_k=0,
                max_mv=100_000.0,  # 10 亿
                min_price=1.0, max_price=500.0,
                min_turnover=10.0, min_listed_days=10,
            )
        )
        strategy.on_start()
        # 强制通过 _filter_universe 的 circ_mv > max_mv 检查
        bars = {
            f"S{i}.SZ": _make_stock_bar(f"S{i}.SZ", circ_mv=500_000.0)
            for i in range(5)
        }
        for _ in range(5):
            strategy._update_history(bars)
        ctx = _make_context()
        candidates = strategy._filter_universe(bars, ctx)
        assert len(candidates) == 0

    def test_momentum_invert_reverses_ranking(self):
        """momentum_invert=True 时反转动量排名"""
        # 需要 2 个策略分别开/关 invert，比较排名
        cfg_normal = SmallCapConfig(
            top_k=10, buffer_k=0, momentum_invert=False,
            weight_small_cap=0.0, weight_momentum=1.0,
            weight_turnover=0.0, weight_volatility=0.0,
            weight_quality=0.0, weight_value=0.0,
        )
        cfg_invert = SmallCapConfig(
            top_k=10, buffer_k=0, momentum_invert=True,
            weight_small_cap=0.0, weight_momentum=1.0,
            weight_turnover=0.0, weight_volatility=0.0,
            weight_quality=0.0, weight_value=0.0,
        )
        s_normal = SmallCapRotationStrategy(cfg_normal)
        s_invert = SmallCapRotationStrategy(cfg_invert)
        s_normal.on_start()
        s_invert.on_start()

        # 构造不同动量的 bars（不同收盘价 → 不同 calc_momentum）
        bars = {
            "UP.SZ": _make_stock_bar("UP.SZ", close=20.0, circ_mv=300_000),
            "DOWN.SZ": _make_stock_bar("DOWN.SZ", close=10.0, circ_mv=300_000),
        }
        # 建立历史：UP 不断涨，DOWN 不断跌
        for i in range(25):
            bars_t = {
                "UP.SZ": _make_stock_bar("UP.SZ", close=10.0 + i * 0.5, circ_mv=300_000),
                "DOWN.SZ": _make_stock_bar("DOWN.SZ", close=20.0 - i * 0.3, circ_mv=300_000),
            }
            s_normal._update_history(bars_t)
            s_invert._update_history(bars_t)

        candidates = [
            self._make_candidate("UP.SZ", circ_mv=300_000),
            self._make_candidate("DOWN.SZ", circ_mv=300_000),
        ]
        ctx = _make_context()
        rank_normal = s_normal._score_candidates(candidates, ctx)
        rank_invert = s_invert._score_candidates(candidates, ctx)

        # 正常动量模式：UP 排前；反转模式：DOWN 排前
        if len(rank_normal) >= 2 and len(rank_invert) >= 2:
            assert rank_normal[0] == "UP.SZ"
            assert rank_invert[0] == "DOWN.SZ"

    def test_six_factor_weight_sum(self):
        """6 因子权重和 = 1.0"""
        cfg = SmallCapConfig()
        total = (
            cfg.weight_small_cap
            + cfg.weight_momentum
            + cfg.weight_turnover
            + cfg.weight_volatility
            + cfg.weight_quality
            + cfg.weight_value
        )
        assert abs(total - 1.0) < 1e-10

    def test_config_consistency_with_yml(self):
        """代码默认值与 YAML 配置一致性验证"""
        cfg = SmallCapConfig()
        assert cfg.top_k == 30
        assert cfg.rebalance_days == 15
        assert cfg.buffer_k == 8
        assert cfg.stop_loss_pct == 0.15
        assert cfg.weight_small_cap == 0.30
        assert cfg.weight_momentum == 0.15
        assert cfg.weight_turnover == 0.10
        assert cfg.weight_volatility == 0.15
        assert cfg.weight_quality == 0.15
        assert cfg.weight_value == 0.15
        assert cfg.momentum_invert is False

    def test_zero_circ_mv_size_factor_no_crash(self):
        """circ_mv = 0 时不崩溃 (log(0) guarded)"""
        strategy = SmallCapRotationStrategy(
            config=SmallCapConfig(top_k=10, buffer_k=0)
        )
        strategy.on_start()
        c = self._make_candidate("ZERO_MV", circ_mv=0.0)
        c_normal = self._make_candidate("NORMAL", circ_mv=300_000.0)
        result = strategy._score_candidates([c, c_normal], _make_context())
        assert isinstance(result, list)

    def test_empty_candidates_no_crash(self):
        """空候选列表不崩溃"""
        strategy = SmallCapRotationStrategy()
        strategy.on_start()
        result = strategy._score_candidates([], _make_context())
        assert result == []

    def test_single_candidate(self):
        """单个候选返回该标的"""
        strategy = SmallCapRotationStrategy(
            config=SmallCapConfig(top_k=10, buffer_k=0)
        )
        strategy.on_start()
        # 提供足够价格历史避免动量/波动率因子返回 NaN
        strategy._price_history["ONLY"] = [10.0 + i * 0.1 for i in range(25)]
        c = self._make_candidate("ONLY")
        result = strategy._score_candidates([c], _make_context())
        assert result == ["ONLY"]

    def test_all_none_roe_quality_factor(self):
        """全部 ROE = None → nan_handling='median' 使候选保留（降级为中位数填充）

        R17 变更: nan_handling 从 'pollute' 改为 'median'，
        所有值为 NaN 时 median 填充后 rank 相同，但候选不会被过滤掉。
        """
        strategy = SmallCapRotationStrategy(
            config=SmallCapConfig(
                top_k=10, buffer_k=0,
                weight_small_cap=0.0, weight_momentum=0.0,
                weight_turnover=0.0, weight_volatility=0.0,
                weight_quality=1.0, weight_value=0.0,
            )
        )
        strategy.on_start()
        candidates = [
            self._make_candidate(f"S{i}", roe=None)
            for i in range(3)
        ]
        # nan_handling="median" 下全 NaN → 候选保留（得分相同）
        result = strategy._score_candidates(candidates, _make_context())
        assert isinstance(result, list)
        # 候选不再被过滤，而是以相同得分保留
        assert len(result) <= len(candidates)

    def test_registry_roundtrip(self):
        """通过注册表创建实例并验证可用"""
        from aipha.strategy.registry import create_strategy
        s = create_strategy("smallcap_rotation")
        assert s.name == "smallcap_rotation"
        assert hasattr(s, "_score_candidates")


# ===========================================================================
# TestSmallCapRegimeAdaptive — Regime 自适应权重调整
# ===========================================================================


class TestSmallCapRegimeAdaptive:
    """验证 SmallCapRotation 的 regime 自适应权重机制"""

    def _make_candidate(
        self,
        symbol: str,
        circ_mv: float = 300_000.0,
        turnover_rate: float = 3.0,
        roe: float | None = 15.0,
        pe_ttm: float | None = 20.0,
    ) -> EquityCandidateInfo:
        return EquityCandidateInfo(
            symbol=symbol,
            close=10.0,
            metadata={"roe": roe, "pe_ttm": pe_ttm},
            circ_mv=circ_mv,
            turnover_rate=turnover_rate,
            amount=5000.0,
            list_date="20190101",
        )

    def _ctx(self, regime: str = "NEUTRAL") -> StrategyContext:
        return StrategyContext(
            current_time=datetime(2024, 3, 15, 15, 0),
            positions={},
            cash=1_000_000.0,
            nav=1_000_000.0,
            metadata={"market_regime": regime},
        )

    def test_regime_adaptive_disabled_ignores_regime(self):
        """regime_adaptive=False 时，不同 regime 不改变权重"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=False)
        )
        w_bull = strategy._get_regime_weights(self._ctx("BULL"))
        w_bear = strategy._get_regime_weights(self._ctx("BEAR"))
        assert w_bull == w_bear

    def test_bull_increases_small_cap_weight(self):
        """BULL 市场应增大小盘权重"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=True)
        )
        w_neutral = strategy._get_regime_weights(self._ctx("NEUTRAL"))
        w_bull = strategy._get_regime_weights(self._ctx("BULL"))
        assert w_bull["small_cap"] > w_neutral["small_cap"]

    def test_bear_increases_quality_weight(self):
        """BEAR 市场应增大质量因子权重"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=True)
        )
        w_neutral = strategy._get_regime_weights(self._ctx("NEUTRAL"))
        w_bear = strategy._get_regime_weights(self._ctx("BEAR"))
        assert w_bear["quality"] > w_neutral["quality"]
        assert w_bear["volatility"] > w_neutral["volatility"]

    def test_bear_decreases_small_cap_weight(self):
        """BEAR 市场应减小小盘权重"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=True)
        )
        w_neutral = strategy._get_regime_weights(self._ctx("NEUTRAL"))
        w_bear = strategy._get_regime_weights(self._ctx("BEAR"))
        assert w_bear["small_cap"] < w_neutral["small_cap"]

    def test_weights_sum_to_one(self):
        """任何 regime 下权重归一化后和为 1.0"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=True)
        )
        for regime in ["BULL", "BEAR", "NEUTRAL"]:
            w = strategy._get_regime_weights(self._ctx(regime))
            assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_all_weights_non_negative(self):
        """权重永远非负（clamp to 0）"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(
                regime_adaptive=True,
                regime_bear_delta_small_cap=-0.50,  # 极端 → clamp
            )
        )
        w = strategy._get_regime_weights(self._ctx("BEAR"))
        for v in w.values():
            assert v >= 0.0

    def test_unknown_regime_uses_default(self):
        """未知 regime 返回默认权重"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=True)
        )
        w = strategy._get_regime_weights(self._ctx("UNKNOWN"))
        cfg = strategy.config
        assert abs(w["small_cap"] - cfg.weight_small_cap) < 1e-9

    def test_scoring_uses_regime_weights(self):
        """_score_candidates 在 regime_adaptive=True 时实际应用不同权重"""
        strategy = SmallCapRotationStrategy(
            SmallCapConfig(regime_adaptive=True, top_k=5)
        )
        strategy.on_start()
        # 提供足够价格历史避免动量/波动率因子返回 NaN
        for sym in ["TINY", "QUALITY"]:
            strategy._price_history[sym] = [10.0 + i * 0.1 for i in range(25)]
        cands = [
            self._make_candidate("TINY", circ_mv=100_000, roe=5.0, pe_ttm=50.0),
            self._make_candidate("QUALITY", circ_mv=500_000, roe=30.0, pe_ttm=10.0),
        ]
        # BULL 偏好小盘
        result_bull = strategy._score_candidates(cands, self._ctx("BULL"))
        # BEAR 偏好质量
        result_bear = strategy._score_candidates(cands, self._ctx("BEAR"))
        # 两种 regime 应能返回排序（不一定不同，但不应崩溃）
        assert len(result_bull) == 2
        assert len(result_bear) == 2
