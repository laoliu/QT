"""Tests for MultiFactorEnhanceStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.multifactor_enhance import (
    MultiFactorEnhanceConfig,
    MultiFactorEnhanceStrategy,
)


def _make_bar(
    symbol: str,
    close: float = 30.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    circ_mv: float = 500000.0,
    amount: float = 5000.0,
    name: str = "正常股",
    list_date: str = "20180101",
    is_suspended: bool = False,
) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=100000,
        is_suspended=is_suspended,
        metadata={
            "pe_ttm": pe_ttm,
            "pb": pb,
            "roe": roe,
            "circ_mv": circ_mv,
            "amount": amount,
            "name": name,
            "list_date": list_date,
        },
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=cash,
    )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestMultiFactorEnhanceConfig:

    def test_defaults(self):
        cfg = MultiFactorEnhanceConfig()
        assert cfg.top_k == 50
        assert cfg.rebalance_days == 20
        assert cfg.benchmark == "000300.SH"
        assert cfg.weight_value == 0.25
        assert cfg.weight_quality == 0.20
        assert cfg.weight_momentum == 0.20
        assert cfg.weight_volatility == 0.20
        assert cfg.weight_size == 0.15
        assert cfg.value_metric == "pe_ttm"
        assert cfg.max_single_pct == 0.05

    def test_custom(self):
        cfg = MultiFactorEnhanceConfig(
            top_k=30, benchmark="000905.SH",
            weight_value=0.30, value_metric="pb",
        )
        assert cfg.top_k == 30
        assert cfg.benchmark == "000905.SH"
        assert cfg.weight_value == 0.30
        assert cfg.value_metric == "pb"


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------

class TestMultiFactorEnhanceStrategy:

    @pytest.fixture
    def strategy(self):
        cfg = MultiFactorEnhanceConfig(
            top_k=3,
            rebalance_days=1,
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            lot_size=100,
            buffer_k=0,
            min_listed_days=10,
        )
        s = MultiFactorEnhanceStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "multifactor_enhance" in StrategyRegistry().list_strategies()

    def test_on_start_clears_state(self, strategy):
        strategy._entry_prices["X.SZ"] = 10.0
        strategy._bar_count = 5
        strategy.on_start()
        assert strategy._bar_count == 0
        assert len(strategy._entry_prices) == 0

    # -- Filter tests --

    def test_filter_excludes_st(self, strategy):
        bars = {
            "ST.SZ": _make_bar("ST.SZ", name="ST测试"),
            "OK.SZ": _make_bar("OK.SZ"),
        }
        candidates = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in candidates]
        assert "ST.SZ" not in syms
        assert "OK.SZ" in syms

    def test_filter_excludes_suspended(self, strategy):
        bars = {
            "HALT.SZ": _make_bar("HALT.SZ", is_suspended=True),
            "OK.SZ": _make_bar("OK.SZ"),
        }
        candidates = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in candidates]
        assert "HALT.SZ" not in syms

    def test_filter_excludes_low_turnover(self, strategy):
        bars = {
            "LOW.SZ": _make_bar("LOW.SZ", amount=5),
            "OK.SZ": _make_bar("OK.SZ", amount=5000),
        }
        candidates = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in candidates]
        assert "LOW.SZ" not in syms
        assert "OK.SZ" in syms

    def test_filter_excludes_new_listing(self, strategy):
        bars = {
            "NEW.SZ": _make_bar("NEW.SZ", list_date="20240610"),  # 5天前
            "OLD.SZ": _make_bar("OLD.SZ", list_date="20200101"),
        }
        candidates = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in candidates]
        assert "NEW.SZ" not in syms
        assert "OLD.SZ" in syms

    def test_filter_excludes_price_range(self, strategy):
        bars = {
            "CHEAP.SZ": _make_bar("CHEAP.SZ", close=0.5),
            "OK.SZ": _make_bar("OK.SZ", close=30.0),
        }
        candidates = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in candidates]
        assert "CHEAP.SZ" not in syms
        assert "OK.SZ" in syms

    # -- Factor scoring tests --

    def test_value_factor_low_pe_preferred(self, strategy):
        """低 PE 得分更高"""
        bars = {
            "LOW_PE.SZ": _make_bar("LOW_PE.SZ", pe_ttm=8.0, circ_mv=500000),
            "HIGH_PE.SZ": _make_bar("HIGH_PE.SZ", pe_ttm=60.0, circ_mv=500000),
        }
        for _ in range(65):
            strategy._update_history(bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        # 低 PE 应排前面（价值因子权重=0.25）
        assert ranked[0] == "LOW_PE.SZ"

    def test_quality_factor_high_roe_preferred(self, strategy):
        """高 ROE 得分更高"""
        # 控制其他因子一致，只让 ROE 变化
        bars = {
            "HI_ROE.SZ": _make_bar("HI_ROE.SZ", pe_ttm=15, roe=25.0, circ_mv=500000),
            "LO_ROE.SZ": _make_bar("LO_ROE.SZ", pe_ttm=15, roe=3.0, circ_mv=500000),
        }
        for _ in range(65):
            strategy._update_history(bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "HI_ROE.SZ"

    def test_size_factor_small_cap_preferred(self, strategy):
        """小市值得分更高"""
        bars = {
            "SMALL.SZ": _make_bar("SMALL.SZ", pe_ttm=15, roe=12, circ_mv=100000),
            "LARGE.SZ": _make_bar("LARGE.SZ", pe_ttm=15, roe=12, circ_mv=5000000),
        }
        for _ in range(65):
            strategy._update_history(bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "SMALL.SZ"

    # -- Momentum & volatility --

    def test_momentum_positive(self, strategy):
        for i in range(25):
            strategy._price_history.setdefault("UP.SZ", []).append(10.0 + i * 0.5)
        assert strategy.calc_momentum("UP.SZ") > 0

    def test_momentum_negative(self, strategy):
        for i in range(25):
            strategy._price_history.setdefault("DN.SZ", []).append(30.0 - i * 0.5)
        assert strategy.calc_momentum("DN.SZ") < 0

    def test_momentum_insufficient_history(self, strategy):
        strategy._price_history["SHORT.SZ"] = [10.0, 11.0]
        assert strategy.calc_momentum("SHORT.SZ") == 0.0

    def test_volatility_positive(self, strategy):
        import random
        random.seed(42)
        strategy._price_history["V.SZ"] = [10 + random.gauss(0, 0.5) for _ in range(65)]
        assert strategy.calc_volatility("V.SZ") > 0

    def test_volatility_insufficient_history(self, strategy):
        strategy._price_history["S.SZ"] = [10.0, 11.0]
        assert strategy.calc_volatility("S.SZ") == 0.0

    # -- Stop loss --

    def test_stop_loss_triggered(self, strategy):
        bars = {"X.SZ": _make_bar("X.SZ", close=7.5)}
        ctx = _make_ctx(positions={"X.SZ": 100})
        strategy._entry_prices["X.SZ"] = 10.0
        strategy._config.stop_loss_pct = 0.20

        intents = strategy._check_stop_loss(bars, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) == 1
        assert sells[0].symbol == "X.SZ"

    def test_stop_loss_not_triggered(self, strategy):
        bars = {"X.SZ": _make_bar("X.SZ", close=9.0)}
        ctx = _make_ctx(positions={"X.SZ": 100})
        strategy._entry_prices["X.SZ"] = 10.0
        strategy._config.stop_loss_pct = 0.20

        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    # -- on_fill --

    def test_on_fill_buy_tracks_entry(self, strategy):
        strategy.on_fill("A.SZ", Side.BUY, 100, 25.0)
        assert strategy._entry_prices["A.SZ"] == 25.0

    def test_on_fill_sell_clears_entry(self, strategy):
        strategy._entry_prices["A.SZ"] = 25.0
        strategy.on_fill("A.SZ", Side.SELL, 100, 30.0)
        assert "A.SZ" not in strategy._entry_prices

    # -- Rebalance --

    def test_rebalance_sells_old_positions(self, strategy):
        bars = {
            "NEW.SZ": _make_bar("NEW.SZ", close=20),
            "OLD.SZ": _make_bar("OLD.SZ", close=25),
        }
        ctx = _make_ctx(positions={"OLD.SZ": 200})
        intents = strategy._generate_rebalance(["NEW.SZ"], ctx, bars)
        sells = [i for i in intents if i.side == Side.SELL]
        assert any(i.symbol == "OLD.SZ" for i in sells)

    def test_rebalance_buys_new_targets(self, strategy):
        bars = {
            "A.SZ": _make_bar("A.SZ", close=20),
            "B.SZ": _make_bar("B.SZ", close=30),
        }
        ctx = _make_ctx()
        intents = strategy._generate_rebalance(["A.SZ", "B.SZ"], ctx, bars)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 2

    def test_on_bar_skips_non_rebalance_day(self, strategy):
        strategy._config.rebalance_days = 5
        strategy._bar_count = 2
        bars = {"A.SZ": _make_bar("A.SZ")}
        ctx = _make_ctx()
        intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0

    def test_on_bar_triggers_rebalance(self, strategy):
        """调仓日应生成买入指令"""
        bars = {
            f"S{i}.SZ": _make_bar(
                f"S{i}.SZ", close=20 + i, pe_ttm=10 + i*2,
                roe=15 - i, circ_mv=300000 + i*100000,
            )
            for i in range(5)
        }
        # 建立价格历史
        for _ in range(65):
            strategy._update_history(bars)
        strategy._bar_count = 0  # 重置以触发调仓
        ctx = _make_ctx()
        intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) <= 3  # top_k=3

    def test_value_metric_pb(self):
        """切换到 PB 指标"""
        cfg = MultiFactorEnhanceConfig(
            top_k=2, rebalance_days=1, value_metric="pb",
            min_price=1.0, max_price=500.0,
            min_turnover=10.0, buffer_k=0, min_listed_days=10,
        )
        s = MultiFactorEnhanceStrategy(cfg)
        s.on_start()

        bars = {
            "LO_PB.SZ": _make_bar("LO_PB.SZ", pb=0.8, pe_ttm=15, roe=12, circ_mv=500000),
            "HI_PB.SZ": _make_bar("HI_PB.SZ", pb=5.0, pe_ttm=15, roe=12, circ_mv=500000),
        }
        for _ in range(65):
            s._update_history(bars)
        ctx = _make_ctx()
        candidates = s._filter_universe(bars, ctx)
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "LO_PB.SZ"

    def test_max_single_pct_cap(self):
        """单票持仓不超过 max_single_pct"""
        cfg = MultiFactorEnhanceConfig(
            top_k=2, rebalance_days=1,
            max_single_pct=0.03,  # 3%
            min_price=1.0, max_price=500.0,
            min_turnover=10.0, buffer_k=0, min_listed_days=10,
        )
        s = MultiFactorEnhanceStrategy(cfg)
        s.on_start()

        bars = {
            "A.SZ": _make_bar("A.SZ", close=10, pe_ttm=10, roe=15, circ_mv=300000),
            "B.SZ": _make_bar("B.SZ", close=20, pe_ttm=12, roe=12, circ_mv=400000),
        }
        for _ in range(65):
            s._update_history(bars)
        s._bar_count = 0
        ctx = _make_ctx(cash=1_000_000)
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        for b in buys:
            bar = bars[b.symbol]
            value = b.qty * bar.close
            # max_single_pct * nav + 1 lot rounding tolerance
            assert value <= 1_000_000 * 0.03 + bar.close * 100

    def test_buffer_k_reduces_turnover(self):
        """buffer_k 保留已持有且在扩展池中的标的（不卖出）"""
        cfg = MultiFactorEnhanceConfig(
            top_k=2, rebalance_days=1,
            min_price=1.0, max_price=500.0,
            min_turnover=10.0, buffer_k=2, min_listed_days=10,
        )
        s = MultiFactorEnhanceStrategy(cfg)
        s.on_start()

        bars = {
            "A.SZ": _make_bar("A.SZ", pe_ttm=8, roe=20, circ_mv=200000),  # rank 1
            "B.SZ": _make_bar("B.SZ", pe_ttm=10, roe=15, circ_mv=300000),  # rank 2
            "C.SZ": _make_bar("C.SZ", pe_ttm=12, roe=12, circ_mv=400000),  # rank 3
            "D.SZ": _make_bar("D.SZ", pe_ttm=15, roe=10, circ_mv=500000),  # rank 4
        }
        for _ in range(65):
            s._update_history(bars)
        # C.SZ 已持有且在 top_k+buffer_k=4 范围内 → 不应被卖出
        s._bar_count = 0
        ctx = _make_ctx(positions={"C.SZ": 100})
        intents = s.on_bar(bars, ctx)
        sell_syms = {i.symbol for i in intents if i.side == Side.SELL}
        assert "C.SZ" not in sell_syms


# ============================================================
# Phase 2 — 边界 & 极端场景补充
# ============================================================


class TestMultiFactorBoundaryScenarios:
    """多因子策略边界条件与极端场景测试"""

    def _make_candidate(
        self,
        symbol: str = "000001.SZ",
        pe_ttm: float = 15.0,
        pb: float = 2.0,
        roe: float = 12.0,
        circ_mv: float = 500_000.0,
    ) -> EquityCandidateInfo:
        return EquityCandidateInfo(
            symbol=symbol,
            close=20.0,
            metadata={
                "pe_ttm": pe_ttm,
                "pb": pb,
                "roe": roe,
                "circ_mv": circ_mv,
            },
            circ_mv=circ_mv,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20200101",
            name="测试",
        )

    def test_negative_pe_treated_as_inf(self):
        """PE < 0 (亏损股) → 价值因子 = -inf → 排最后"""
        strategy = MultiFactorEnhanceStrategy(
            config=MultiFactorEnhanceConfig(
                top_k=10, buffer_k=0,
                weight_value=1.0,
                weight_quality=0.0,
                weight_momentum=0.0,
                weight_volatility=0.0,
                weight_size=0.0,
            )
        )
        strategy.on_start()
        ctx = _make_ctx()
        c_neg = self._make_candidate("NEG_PE", pe_ttm=-5.0)
        c_pos = self._make_candidate("POS_PE", pe_ttm=10.0)
        result = strategy._score_candidates([c_neg, c_pos], ctx)
        # 正 PE（低 PE 更好）排前
        assert result[0] == "POS_PE"

    def test_zero_pe_treated_as_inf(self):
        """PE = 0 → 价值因子 = -inf"""
        strategy = MultiFactorEnhanceStrategy(
            config=MultiFactorEnhanceConfig(
                top_k=10, buffer_k=0,
                weight_value=1.0, weight_quality=0.0,
                weight_momentum=0.0, weight_volatility=0.0, weight_size=0.0,
            )
        )
        strategy.on_start()
        c_zero = self._make_candidate("ZERO_PE", pe_ttm=0.0)
        c_pos = self._make_candidate("POS_PE", pe_ttm=10.0)
        result = strategy._score_candidates([c_zero, c_pos], _make_ctx())
        assert result[0] == "POS_PE"

    def test_zero_circ_mv_size_factor_inf(self):
        """circ_mv = 0 时 size 因子 = -inf（log(0) 不崩溃）"""
        strategy = MultiFactorEnhanceStrategy(
            config=MultiFactorEnhanceConfig(
                top_k=10, buffer_k=0,
                weight_value=0.0, weight_quality=0.0,
                weight_momentum=0.0, weight_volatility=0.0,
                weight_size=1.0,
            )
        )
        strategy.on_start()
        c_zero = self._make_candidate("ZERO_MV", circ_mv=0.0)
        c_normal = self._make_candidate("NORMAL", circ_mv=500_000.0)
        # 不应崩溃
        result = strategy._score_candidates([c_zero, c_normal], _make_ctx())
        assert isinstance(result, list)

    def test_all_factors_weighted_sum_one(self):
        """默认因子权重之和 = 1.0"""
        cfg = MultiFactorEnhanceConfig()
        total = (
            cfg.weight_value
            + cfg.weight_quality
            + cfg.weight_momentum
            + cfg.weight_volatility
            + cfg.weight_size
        )
        assert abs(total - 1.0) < 1e-9

    def test_config_consistency_with_yml(self):
        """代码默认值与 YAML 配置一致性验证"""
        cfg = MultiFactorEnhanceConfig()
        assert cfg.top_k == 50
        assert cfg.rebalance_days == 20
        assert cfg.benchmark == "000300.SH"
        assert cfg.weight_value == 0.25
        assert cfg.weight_quality == 0.20
        assert cfg.weight_momentum == 0.20
        assert cfg.weight_volatility == 0.20
        assert cfg.weight_size == 0.15
        assert cfg.value_metric == "pe_ttm"
        assert cfg.stop_loss_pct == 0.20
        assert cfg.max_single_pct == 0.05
        assert cfg.buffer_k == 10

    def test_empty_candidates_returns_empty(self):
        """空候选列表不崩溃"""
        strategy = MultiFactorEnhanceStrategy()
        strategy.on_start()
        result = strategy._score_candidates([], _make_ctx())
        assert result == []

    def test_single_candidate_returns_it(self):
        """单个候选返回该标的"""
        strategy = MultiFactorEnhanceStrategy(
            config=MultiFactorEnhanceConfig(top_k=10, buffer_k=0)
        )
        strategy.on_start()
        # 提供足够价格历史避免动量/波动率因子返回 NaN（volatility_lookback=60）
        strategy._price_history["ONLY"] = [10.0 + i * 0.1 for i in range(65)]
        c = self._make_candidate("ONLY")
        result = strategy._score_candidates([c], _make_ctx())
        assert result == ["ONLY"]

    def test_all_none_roe_quality_factor(self):
        """所有候选 ROE = None → 质量因子缺失 → median 模式下获中位排名分，候选保留"""
        strategy = MultiFactorEnhanceStrategy(
            config=MultiFactorEnhanceConfig(
                top_k=10, buffer_k=0,
                weight_value=0.0, weight_quality=1.0,
                weight_momentum=0.0, weight_volatility=0.0, weight_size=0.0,
            )
        )
        strategy.on_start()
        candidates = [
            self._make_candidate(f"S{i}", roe=None)
            for i in range(3)
        ]
        # 不应崩溃；默认 median 模式下缺失因子获中位排名分，候选保留
        result = strategy._score_candidates(candidates, _make_ctx())
        assert isinstance(result, list)
        assert len(result) == 3

    def test_registry_roundtrip(self):
        """通过注册表创建实例并验证可用"""
        from aipha.strategy.registry import create_strategy
        s = create_strategy("multifactor_enhance")
        assert s.name == "multifactor_enhance"
        assert hasattr(s, "_score_candidates")


# ---------------------------------------------------------------------------
# 新增：增强因子插槽测试
# ---------------------------------------------------------------------------


class TestMultiFactorEnhanceNewFactors:
    """验证 MultiFactorEnhanceStrategy 新增的因子插槽。"""

    def _make_candidate(
        self,
        symbol: str,
        close: float = 30.0,
        circ_mv: float = 500_000.0,
        pe_ttm: float = 15.0,
        roe: float = 12.0,
        dv_ratio: float = 3.0,
        # Piotroski 维度
        roa: float | None = None,
        roa_prev: float | None = None,
        cfps: float | None = None,
        eps: float | None = None,
        debt_to_assets: float | None = None,
        debt_to_assets_prev: float | None = None,
        current_ratio: float | None = None,
        current_ratio_prev: float | None = None,
        # 应计盈余
        net_profit: float | None = None,
        ocf: float | None = None,
        total_assets: float | None = None,
        # 估值分位
        pe_history: list[float] | None = None,
    ) -> EquityCandidateInfo:
        meta: dict = {
            "pe_ttm": pe_ttm,
            "circ_mv": circ_mv,
            "amount": 5000.0,
            "name": "测试",
            "list_date": "20180101",
            "roe": roe,
            "dv_ratio": dv_ratio,
        }
        for key, val in [
            ("roa", roa), ("roa_prev", roa_prev),
            ("cfps", cfps), ("eps", eps),
            ("debt_to_assets", debt_to_assets),
            ("debt_to_assets_prev", debt_to_assets_prev),
            ("current_ratio", current_ratio),
            ("current_ratio_prev", current_ratio_prev),
            ("net_profit", net_profit), ("ocf", ocf),
            ("total_assets", total_assets),
        ]:
            if val is not None:
                meta[key] = val
        if pe_history is not None:
            meta["pe_history"] = pe_history
        return EquityCandidateInfo(
            symbol=symbol, close=close, circ_mv=circ_mv,
            turnover_rate=3.0, amount=5000.0,
            list_date="20180101", name="测试",
            metadata=meta,
        )

    def test_default_weights_zero(self):
        """新因子权重默认为 0。"""
        cfg = MultiFactorEnhanceConfig()
        assert cfg.weight_piotroski == 0.0
        assert cfg.weight_valuation_pct == 0.0
        assert cfg.weight_accruals == 0.0
        assert cfg.weight_dividend == 0.0

    def test_piotroski_enabled(self):
        """启用 Piotroski 因子。"""
        cfg = MultiFactorEnhanceConfig(
            top_k=3, rebalance_days=1,
            weight_piotroski=0.20, piotroski_min_dims=3,
            weight_value=0.20, weight_quality=0.20,
            weight_momentum=0.0, weight_volatility=0.20,
            weight_size=0.10,
        )
        s = MultiFactorEnhanceStrategy(config=cfg)
        s.on_start()
        cands = [
            self._make_candidate(
                "A.SZ", roa=5.0, roa_prev=4.0, cfps=1.5, eps=1.0,
                debt_to_assets=40.0, debt_to_assets_prev=45.0,
            ),
            self._make_candidate(
                "B.SZ", roa=2.0, roa_prev=3.0, cfps=0.5, eps=0.8,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ranked = s._score_candidates(cands, _make_ctx())
        assert isinstance(ranked, list)
        assert len(ranked) >= 1

    def test_valuation_pct_enabled(self):
        """启用估值分位因子。"""
        cfg = MultiFactorEnhanceConfig(
            top_k=3, rebalance_days=1,
            weight_valuation_pct=0.15,
            weight_value=0.25, weight_quality=0.20,
            weight_momentum=0.0, weight_volatility=0.20,
            weight_size=0.10,
        )
        s = MultiFactorEnhanceStrategy(config=cfg)
        s.on_start()
        cands = [
            self._make_candidate(
                "A.SZ", pe_ttm=12.0,
                pe_history=[10.0, 11.0, 12.0, 15.0, 20.0] * 10,
            ),
            self._make_candidate(
                "B.SZ", pe_ttm=25.0,
                pe_history=[20.0, 22.0, 25.0, 30.0, 35.0] * 10,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ranked = s._score_candidates(cands, _make_ctx())
        assert isinstance(ranked, list)
        assert len(ranked) >= 1

    def test_accruals_enabled(self):
        """启用应计盈余因子。"""
        cfg = MultiFactorEnhanceConfig(
            top_k=3, rebalance_days=1,
            weight_accruals=0.10,
            weight_value=0.25, weight_quality=0.20,
            weight_momentum=0.0, weight_volatility=0.25,
            weight_size=0.10,
        )
        s = MultiFactorEnhanceStrategy(config=cfg)
        s.on_start()
        cands = [
            self._make_candidate(
                "A.SZ", net_profit=1.5, ocf=1.0, total_assets=10.0,
            ),
            self._make_candidate(
                "B.SZ", net_profit=1.0, ocf=0.3, total_assets=10.0,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ranked = s._score_candidates(cands, _make_ctx())
        assert isinstance(ranked, list)

    def test_dividend_enabled(self):
        """启用股息率因子。"""
        cfg = MultiFactorEnhanceConfig(
            top_k=3, rebalance_days=1,
            weight_dividend=0.15,
            weight_value=0.25, weight_quality=0.20,
            weight_momentum=0.0, weight_volatility=0.20,
            weight_size=0.10,
        )
        s = MultiFactorEnhanceStrategy(config=cfg)
        s.on_start()
        cands = [
            self._make_candidate("A.SZ", dv_ratio=6.0),
            self._make_candidate("B.SZ", dv_ratio=1.0),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ranked = s._score_candidates(cands, _make_ctx())
        assert isinstance(ranked, list)
        assert len(ranked) >= 1

    def test_all_new_factors_enabled(self):
        """同时启用全部 4 个新因子。"""
        cfg = MultiFactorEnhanceConfig(
            top_k=5, rebalance_days=1,
            weight_value=0.15, weight_quality=0.10,
            weight_momentum=0.0, weight_volatility=0.10,
            weight_size=0.05,
            weight_piotroski=0.20, piotroski_min_dims=3,
            weight_valuation_pct=0.15,
            weight_accruals=0.10,
            weight_dividend=0.15,
        )
        s = MultiFactorEnhanceStrategy(config=cfg)
        s.on_start()
        cands = [
            self._make_candidate(
                "A.SZ", roe=20.0, dv_ratio=5.0,
                roa=5.0, roa_prev=4.0, cfps=1.5, eps=1.0,
                debt_to_assets=40.0, debt_to_assets_prev=45.0,
                net_profit=1.5, ocf=1.0, total_assets=10.0,
                pe_ttm=12.0,
                pe_history=[10, 11, 12, 15, 20] * 10,
            ),
            self._make_candidate(
                "B.SZ", roe=10.0, dv_ratio=2.0,
                roa=3.0, roa_prev=3.5, cfps=0.5, eps=0.8,
                net_profit=0.5, ocf=0.3, total_assets=10.0,
                pe_ttm=25.0,
                pe_history=[20, 22, 25, 30, 35] * 10,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ranked = s._score_candidates(cands, _make_ctx())
        assert isinstance(ranked, list)
        assert len(ranked) >= 1
