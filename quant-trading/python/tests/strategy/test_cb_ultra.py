"""Tests for CBUltraStrategy and CBUltraNoAgeStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_ultra import (
    CBUltraConfig,
    CBUltraNoAgeConfig,
    CBUltraNoAgeStrategy,
    CBUltraStrategy,
    _rank_scores,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_bar(symbol: str, close: float, metadata: dict | None = None) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 3, 15, 9, 30),
        end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=1000, metadata=metadata,
    )


def _make_cb_bar(
    ts_code: str, close: float,
    amount: float = 100.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 100.0,
    maturity_date: str = "20280101",
    issue_size: float = 5000.0,
    stk_short_name: str = "正股",
    list_date: str = "20240101",
    **extra,
) -> Bar:
    meta = {
        "amount": amount,
        "cb_over_rate": cb_over_rate,
        "cb_value": cb_value,
        "maturity_date": maturity_date,
        "issue_size": issue_size,
        "stk_short_name": stk_short_name,
        "list_date": list_date,
        **extra,
    }
    return _make_bar(ts_code, close, metadata=meta)


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 500_000.0,
    current_time: datetime | None = None,
) -> StrategyContext:
    return StrategyContext(
        current_time=current_time or datetime(2024, 3, 15, 15, 0),
        positions=positions or {},
        cash=cash, nav=cash,
    )


def _make_candidate(
    symbol: str,
    close: float = 100.0,
    issue_size: float = 5000.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 100.0,
    list_date: str = "20240101",
    amount: float = 100.0,
) -> CandidateInfo:
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata={
            "issue_size": issue_size,
            "cb_over_rate": cb_over_rate,
            "cb_value": cb_value,
            "list_date": list_date,
            "amount": amount,
        },
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        list_date=list_date,
        amount=amount,
    )


# ============================================================
# _rank_scores 工具函数测试
# ============================================================

class TestRankScores:
    def test_ascending_basic(self):
        """升序排名：最小值排名 0.0，最大值排名 1.0"""
        result = _rank_scores([10, 20, 30], ascending=True)
        assert result[0] == 0.0  # 10 → rank 0
        assert result[2] == 1.0  # 30 → rank 2

    def test_descending_basic(self):
        """降序排名：最大值排名 0.0，最小值排名 1.0"""
        result = _rank_scores([10, 20, 30], ascending=False)
        assert result[2] == 0.0  # 30 → rank 0
        assert result[0] == 1.0  # 10 → rank 2

    def test_single_element(self):
        result = _rank_scores([42.0])
        assert result == [0.0]

    def test_empty_list(self):
        result = _rank_scores([])
        assert result == []

    def test_tied_values(self):
        """并列值取平均排名"""
        result = _rank_scores([1.0, 1.0, 3.0], ascending=True)
        # 两个 1.0 并列：rank 0 和 1，平均 0.5，归一化后 0.5 / 2 = 0.25
        assert abs(result[0] - result[1]) < 1e-10
        assert result[2] == 1.0

    def test_all_same_values(self):
        """全部相同值"""
        result = _rank_scores([5.0, 5.0, 5.0, 5.0], ascending=True)
        # 所有值排名相同
        assert all(abs(r - result[0]) < 1e-10 for r in result)

    def test_two_elements(self):
        result = _rank_scores([100.0, 50.0], ascending=True)
        assert result[1] == 0.0  # 50 最小
        assert result[0] == 1.0  # 100 最大

    def test_negative_values(self):
        result = _rank_scores([-10.0, 0.0, 10.0], ascending=True)
        assert result[0] == 0.0  # -10 最小
        assert result[2] == 1.0


# ============================================================
# CBUltraConfig 测试
# ============================================================

class TestCBUltraConfig:
    def test_defaults(self):
        cfg = CBUltraConfig()
        assert cfg.top_k == 5
        assert cfg.max_price == 150.0
        assert cfg.max_issue_size == 20000.0
        assert cfg.use_age_filter is True
        assert cfg.max_age_days == 90
        assert cfg.max_premium == 80.0

    def test_weights_default_sum(self):
        cfg = CBUltraConfig()
        total = cfg.weight_size + cfg.weight_premium + cfg.weight_price + cfg.weight_age
        assert abs(total - 1.0) < 1e-10

    def test_custom_weights(self):
        cfg = CBUltraConfig(weight_size=0.8, weight_premium=0.1, weight_price=0.05, weight_age=0.05)
        assert cfg.weight_size == 0.8

    def test_age_filter_off(self):
        cfg = CBUltraConfig(use_age_filter=False)
        assert cfg.use_age_filter is False


# ============================================================
# CBUltraStrategy 测试
# ============================================================

class TestCBUltraStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBUltraConfig(
            top_k=2, rebalance_days=1, max_issue_size=50000.0,
            min_turnover=10.0, lot_size=10,
            use_age_filter=True, max_age_days=180,
            max_premium=80.0,
        )
        s = CBUltraStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_ultra" in StrategyRegistry().list_strategies()

    def test_config_attribute(self, strategy):
        assert strategy.config.top_k == 2
        assert strategy.config.max_age_days == 180

    def test_name(self, strategy):
        assert strategy.name == "cb_ultra"

    # ---------- _extra_filter ----------

    def test_extra_filter_passes_normal(self, strategy):
        """正常候选通过过滤"""
        c = _make_candidate("A.SZ", cb_over_rate=20.0, list_date="20231215")
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        assert strategy._extra_filter(c, ctx) is True

    def test_extra_filter_rejects_high_premium(self, strategy):
        """高溢价率被过滤"""
        c = _make_candidate("A.SZ", cb_over_rate=90.0)
        ctx = _make_context()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_passes_none_premium(self, strategy):
        """溢价率为 None 时不被溢价率过滤"""
        c = _make_candidate("A.SZ", cb_over_rate=10.0)
        c = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            issue_size=5000.0, cb_over_rate=None, cb_value=100.0,
            list_date="20231215",
        )
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        assert strategy._extra_filter(c, ctx) is True

    def test_extra_filter_rejects_too_new(self, strategy):
        """上市不足 min_age_days 天被过滤"""
        c = _make_candidate("A.SZ", list_date="20240314")
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        # 1 天 < min_age_days=3
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_rejects_too_old(self, strategy):
        """超过 max_age_days 被过滤"""
        c = _make_candidate("A.SZ", list_date="20230101")
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        # ~440 天 > max_age_days=180
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_age_boundary(self, strategy):
        """刚好在 max_age_days 上"""
        # 180 天前
        c = _make_candidate("A.SZ", list_date="20230917")
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        assert strategy._extra_filter(c, ctx) is True

    def test_extra_filter_no_list_date(self, strategy):
        """无 list_date 时通过年龄过滤（无法计算）"""
        c = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0,
            list_date=None,
        )
        ctx = _make_context()
        assert strategy._extra_filter(c, ctx) is True

    # ---------- _score_candidates ----------

    def test_score_candidates_basic(self, strategy):
        """基本排序: 小规模 + 低溢价 + 低价格 + 新债优先"""
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            _make_candidate("A.SZ", close=120.0, issue_size=10000.0, cb_over_rate=30.0, list_date="20231215"),
            _make_candidate("B.SZ", close=100.0, issue_size=3000.0, cb_over_rate=5.0, list_date="20240115"),
            _make_candidate("C.SZ", close=110.0, issue_size=8000.0, cb_over_rate=20.0, list_date="20240101"),
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2
        # B.SZ 明显最优(最小规模 + 最低溢价 + 最低价 + 最新)
        assert scored[0] == "B.SZ"

    def test_score_candidates_empty(self, strategy):
        ctx = _make_context()
        assert strategy._score_candidates([], ctx) == []

    def test_score_candidates_single(self, strategy):
        ctx = _make_context()
        candidates = [_make_candidate("A.SZ")]
        assert strategy._score_candidates(candidates, ctx) == ["A.SZ"]

    def test_score_candidates_respects_top_k(self, strategy):
        """top_k=2 时只返回前 2 名"""
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            _make_candidate(f"S{i}.SZ", issue_size=float(i * 1000), list_date="20240101")
            for i in range(1, 6)
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2

    def test_score_candidates_size_weight_dominant(self):
        """规模权重极大时，输出应与 small_size 一致"""
        cfg = CBUltraConfig(
            top_k=3, weight_size=0.99, weight_premium=0.003,
            weight_price=0.003, weight_age=0.004,
            use_age_filter=False,
        )
        s = CBUltraStrategy(cfg)
        s.on_start()
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            _make_candidate("BIG.SZ", issue_size=20000.0, list_date="20240101"),
            _make_candidate("MED.SZ", issue_size=5000.0, list_date="20240101"),
            _make_candidate("TINY.SZ", issue_size=1000.0, list_date="20240101"),
        ]
        scored = s._score_candidates(candidates, ctx)
        assert scored[0] == "TINY.SZ"
        assert scored[1] == "MED.SZ"
        assert scored[2] == "BIG.SZ"

    def test_score_candidates_age_weight_dominant(self):
        """年龄权重极大时，最新的排前面"""
        cfg = CBUltraConfig(
            top_k=3, weight_size=0.003, weight_premium=0.003,
            weight_price=0.003, weight_age=0.991,
            use_age_filter=False,
        )
        s = CBUltraStrategy(cfg)
        s.on_start()
        ctx = _make_context(current_time=datetime(2024, 6, 15))
        candidates = [
            _make_candidate("OLD.SZ", issue_size=1000.0, list_date="20230101"),  # ~530 days
            _make_candidate("MID.SZ", issue_size=1000.0, list_date="20240301"),  # ~106 days
            _make_candidate("NEW.SZ", issue_size=1000.0, list_date="20240601"),  # ~14 days
        ]
        scored = s._score_candidates(candidates, ctx)
        assert scored[0] == "NEW.SZ"
        assert scored[1] == "MID.SZ"

    def test_score_candidates_none_premium_handled(self, strategy):
        """cb_over_rate 为 None 时不崩溃"""
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            CandidateInfo(
                symbol="X.SZ", close=100.0, metadata={},
                issue_size=3000.0, cb_over_rate=None, cb_value=100.0,
                list_date="20240101",
            ),
            _make_candidate("Y.SZ", issue_size=5000.0, list_date="20240101"),
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2

    def test_score_candidates_no_list_date(self, strategy):
        """无 list_date 的候选排在后面（赋 365 天）"""
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            CandidateInfo(
                symbol="NODATE.SZ", close=100.0, metadata={},
                issue_size=1000.0, cb_over_rate=5.0, cb_value=100.0,
                list_date=None,
            ),
            _make_candidate("NEW.SZ", issue_size=1000.0, cb_over_rate=5.0, list_date="20240301"),
        ]
        scored = strategy._score_candidates(candidates, ctx)
        # NEW.SZ 有明确近期 list_date, age 更小 → 排名更优
        assert scored[0] == "NEW.SZ"

    # ---------- 降级机制 ----------

    def test_adaptive_degradation_widens_age(self):
        """候选不足时 _relax_filters_one_step 放宽 max_age"""
        cfg = CBUltraConfig(
            top_k=3, min_candidates=5,
            max_age_days=30, fallback_max_age_days=365,
            use_age_filter=True,
        )
        s = CBUltraStrategy(cfg)
        s.on_start()
        changed = s._relax_filters_one_step()
        assert changed is True
        assert s._effective_max_age > 30  # 已被放宽

    def test_adaptive_degradation_recovers(self):
        """候选充足时 _maybe_tighten_filters 恢复 max_age"""
        cfg = CBUltraConfig(
            top_k=2, min_candidates=3,
            max_age_days=30, fallback_max_age_days=365,
            use_age_filter=True,
        )
        s = CBUltraStrategy(cfg)
        s.on_start()
        s._effective_max_age = 90  # 模拟已降级
        s._maybe_tighten_filters()
        assert s._effective_max_age < 90  # 已收紧

    # ---------- on_bar 集成 ----------

    def test_on_bar_generates_buy(self, strategy):
        """on_bar 应在调仓日产生买入信号"""
        bars = {
            "A.SZ": _make_cb_bar("A.SZ", 100.0, issue_size=3000.0, cb_over_rate=10.0, list_date="20240101"),
            "B.SZ": _make_cb_bar("B.SZ", 100.0, issue_size=5000.0, cb_over_rate=15.0, list_date="20240115"),
            "C.SZ": _make_cb_bar("C.SZ", 100.0, issue_size=8000.0, cb_over_rate=20.0, list_date="20240201"),
        }
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) >= 1

    def test_on_bar_stop_loss(self, strategy):
        """止损触发时产生卖出信号"""
        bar = _make_cb_bar("X.SZ", close=40.0, issue_size=2000.0, list_date="20240101")
        ctx = _make_context(positions={"X.SZ": 10}, current_time=datetime(2024, 3, 15))
        strategy._entry_prices["X.SZ"] = 100.0
        strategy.config.stop_loss_pct = 0.10
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) >= 1

    # ---------- 边界条件 ----------

    def test_all_candidates_same_values(self, strategy):
        """所有候选完全相同时不崩溃"""
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            _make_candidate("A.SZ", close=100.0, issue_size=5000.0, cb_over_rate=10.0, list_date="20240101"),
            _make_candidate("B.SZ", close=100.0, issue_size=5000.0, cb_over_rate=10.0, list_date="20240101"),
            _make_candidate("C.SZ", close=100.0, issue_size=5000.0, cb_over_rate=10.0, list_date="20240101"),
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2

    def test_extreme_values(self, strategy):
        """极端值不崩溃"""
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        candidates = [
            _make_candidate("TINY.SZ", close=0.01, issue_size=0.1, cb_over_rate=-50.0, list_date="20240314"),
            _make_candidate("HUGE.SZ", close=9999.0, issue_size=999999.0, cb_over_rate=500.0, list_date="20200101"),
        ]
        # 高溢价会被过滤，但我们直接调 _score_candidates
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2
        assert scored[0] == "TINY.SZ"

    def test_on_start_resets_effective_age(self):
        """on_start 重置 effective_max_age"""
        cfg = CBUltraConfig(max_age_days=60)
        s = CBUltraStrategy(cfg)
        s._effective_max_age = 999
        s.on_start()
        assert s._effective_max_age == 60


# ============================================================
# CBUltraNoAgeConfig 测试
# ============================================================

class TestCBUltraNoAgeConfig:
    def test_defaults(self):
        cfg = CBUltraNoAgeConfig()
        assert cfg.top_k == 5
        assert cfg.max_price == 200.0
        assert cfg.weight_size == 0.60

    def test_weights(self):
        cfg = CBUltraNoAgeConfig()
        total = cfg.weight_size + cfg.weight_premium + cfg.weight_price
        assert abs(total - 1.0) < 1e-10


# ============================================================
# CBUltraNoAgeStrategy 测试
# ============================================================

class TestCBUltraNoAgeStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBUltraNoAgeConfig(
            top_k=2, rebalance_days=1, max_issue_size=50000.0,
            min_turnover=5.0, lot_size=10,
            max_premium=80.0,
        )
        s = CBUltraNoAgeStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_ultra_noage" in StrategyRegistry().list_strategies()

    def test_name(self, strategy):
        assert strategy.name == "cb_ultra_noage"

    def test_extra_filter_high_premium(self, strategy):
        """高溢价率被过滤"""
        c = _make_candidate("A.SZ", cb_over_rate=90.0)
        ctx = _make_context()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_normal(self, strategy):
        """正常溢价率通过"""
        c = _make_candidate("A.SZ", cb_over_rate=30.0)
        ctx = _make_context()
        assert strategy._extra_filter(c, ctx) is True

    def test_score_candidates_basic(self, strategy):
        ctx = _make_context()
        candidates = [
            _make_candidate("BIG.SZ", close=150.0, issue_size=30000.0, cb_over_rate=40.0),
            _make_candidate("SMALL.SZ", close=100.0, issue_size=3000.0, cb_over_rate=5.0),
            _make_candidate("MID.SZ", close=120.0, issue_size=10000.0, cb_over_rate=20.0),
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2
        assert scored[0] == "SMALL.SZ"  # 最小规模+最低溢价+最低价

    def test_score_candidates_empty(self, strategy):
        assert strategy._score_candidates([], _make_context()) == []

    def test_score_candidates_single(self, strategy):
        ctx = _make_context()
        candidates = [_make_candidate("A.SZ")]
        assert strategy._score_candidates(candidates, ctx) == ["A.SZ"]

    def test_score_weight_size_dominant(self):
        """size 权重极大时，小规模优先"""
        cfg = CBUltraNoAgeConfig(
            top_k=3, weight_size=0.98, weight_premium=0.01, weight_price=0.01,
        )
        s = CBUltraNoAgeStrategy(cfg)
        s.on_start()
        ctx = _make_context()
        candidates = [
            _make_candidate("BIG.SZ", issue_size=50000.0, close=80.0, cb_over_rate=1.0),
            _make_candidate("SMALL.SZ", issue_size=1000.0, close=200.0, cb_over_rate=60.0),
        ]
        scored = s._score_candidates(candidates, ctx)
        assert scored[0] == "SMALL.SZ"

    def test_score_weight_premium_dominant(self):
        """premium 权重极大时，低溢价优先"""
        cfg = CBUltraNoAgeConfig(
            top_k=3, weight_size=0.01, weight_premium=0.98, weight_price=0.01,
        )
        s = CBUltraNoAgeStrategy(cfg)
        s.on_start()
        ctx = _make_context()
        candidates = [
            _make_candidate("HIPREM.SZ", issue_size=1000.0, cb_over_rate=50.0),
            _make_candidate("LOPREM.SZ", issue_size=50000.0, cb_over_rate=1.0),
        ]
        scored = s._score_candidates(candidates, ctx)
        assert scored[0] == "LOPREM.SZ"

    def test_on_bar_generates_signals(self, strategy):
        bars = {
            "A.SZ": _make_cb_bar("A.SZ", 100.0, issue_size=3000.0, cb_over_rate=10.0),
            "B.SZ": _make_cb_bar("B.SZ", 100.0, issue_size=5000.0, cb_over_rate=15.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) >= 1

    def test_none_premium_handled(self, strategy):
        """cb_over_rate 为 None 时不崩溃"""
        ctx = _make_context()
        candidates = [
            CandidateInfo(
                symbol="X.SZ", close=100.0, metadata={},
                issue_size=3000.0, cb_over_rate=None, cb_value=100.0,
            ),
            _make_candidate("Y.SZ", issue_size=5000.0),
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2

    def test_all_same_values(self, strategy):
        ctx = _make_context()
        candidates = [
            _make_candidate(f"S{i}.SZ", close=100.0, issue_size=5000.0, cb_over_rate=10.0)
            for i in range(3)
        ]
        scored = strategy._score_candidates(candidates, ctx)
        assert len(scored) == 2


# ============================================================
# 策略创建 / 注册 / 配置驱动测试
# ============================================================

class TestRegistryIntegration:
    def test_create_cb_ultra_from_registry(self):
        registry = StrategyRegistry()
        strategy = registry.create("cb_ultra", top_k=3)
        assert strategy.name == "cb_ultra"
        assert strategy.config.top_k == 3

    def test_create_cb_ultra_noage_from_registry(self):
        registry = StrategyRegistry()
        strategy = registry.create("cb_ultra_noage", top_k=4)
        assert strategy.name == "cb_ultra_noage"
        assert strategy.config.top_k == 4
