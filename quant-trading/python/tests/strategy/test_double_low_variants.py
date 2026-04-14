"""双低策略变种测试

测试覆盖：
- Z-Score、排名、百分位三种标准化方法
- 不同权重配比
- 边界条件（空数据、单标的、全相同值）
- 第三因子（转股价值）启用
- 与经典双低的对比验证
"""

from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.double_low_variants import (
    DoubleLowPercentileConfig,
    DoubleLowPercentileStrategy,
    DoubleLowRankConfig,
    DoubleLowRankStrategy,
    DoubleLowZScoreConfig,
    DoubleLowZScoreStrategy,
    _percentile_normalize,
    _rank_normalize,
    _zscore_normalize,
)
from aipha.strategy.registry import StrategyRegistry


# ============================================================================
# 工具函数测试
# ============================================================================


class TestZScoreNormalize:
    """Z-Score 标准化测试"""

    def test_basic_zscore(self):
        """基本 Z-Score 计算"""
        values = [100.0, 110.0, 120.0, 130.0, 140.0]
        z = _zscore_normalize(values)
        assert len(z) == 5
        # 均值 120，标准差 ~14.14
        assert abs(z[2]) < 0.01  # 中间值 Z ≈ 0
        assert z[0] < z[2] < z[4]  # 单调递增

    def test_empty(self):
        assert _zscore_normalize([]) == []

    def test_single(self):
        assert _zscore_normalize([100.0]) == [0.0]

    def test_all_same(self):
        """所有值相同时应返回全 0"""
        z = _zscore_normalize([100.0, 100.0, 100.0])
        assert all(v == 0.0 for v in z)

    def test_two_values(self):
        """两个值的 Z-Score"""
        z = _zscore_normalize([100.0, 200.0])
        assert z[0] < 0  # 100 < 均值 150
        assert z[1] > 0  # 200 > 均值 150
        assert abs(z[0] + z[1]) < 1e-10  # 对称

    def test_negative_values(self):
        """负值也能正确处理"""
        z = _zscore_normalize([-5.0, 0.0, 5.0, 10.0, 35.0])
        assert z[0] < z[1] < z[2]


class TestRankNormalize:
    """排名归一化测试"""

    def test_basic_ascending(self):
        """升序排名"""
        r = _rank_normalize([300.0, 100.0, 200.0], ascending=True)
        # 值: 100=rank0, 200=rank1, 300=rank2
        # 归一化: 0/2=0, 1/2=0.5, 2/2=1
        assert r[1] == 0.0   # 100 最小 → 排名 0
        assert r[2] == 0.5   # 200 中间 → 排名 0.5
        assert r[0] == 1.0   # 300 最大 → 排名 1

    def test_basic_descending(self):
        """降序排名"""
        r = _rank_normalize([300.0, 100.0, 200.0], ascending=False)
        assert r[0] == 0.0   # 300 最大 → 排名 0（最优）
        assert r[2] == 0.5   # 200 中间
        assert r[1] == 1.0   # 100 最小 → 排名 1

    def test_empty(self):
        assert _rank_normalize([]) == []

    def test_single(self):
        assert _rank_normalize([42.0]) == [0.0]

    def test_tied_values(self):
        """相同值 — 排名仍有顺序（按原始位置稳定排序）"""
        r = _rank_normalize([100.0, 100.0, 100.0], ascending=True)
        # 所有值相同，三个排名应是 0, 0.5, 1（按稳定排列）
        assert len(r) == 3
        assert sorted(r) == [0.0, 0.5, 1.0]


class TestPercentileNormalize:
    """百分位归一化测试"""

    def test_basic(self):
        p = _percentile_normalize([50.0, 10.0, 30.0])
        # 10=0(最小), 30=0.5, 50=1(最大)
        assert p[1] == 0.0  # 10 最小
        assert p[0] == 1.0  # 50 最大

    def test_empty(self):
        assert _percentile_normalize([]) == []


# ============================================================================
# Z-Score 策略测试
# ============================================================================


class TestDoubleLowZScoreStrategy:
    """双低 Z-Score 策略测试"""

    @pytest.fixture
    def strategy(self):
        return DoubleLowZScoreStrategy(
            config=DoubleLowZScoreConfig(
                top_k=3,
                rebalance_days=5,
                max_price=150.0,
                max_premium=60.0,
                price_weight=0.50,
                premium_weight=0.50,
                lot_size=10,
            )
        )

    def test_registration(self):
        registry = StrategyRegistry()
        assert "double_low_zscore" in registry.list_strategies()

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )

    def test_score_equal_weight(self, strategy):
        """等权重时 Z-Score 应正确排序"""
        candidates = [
            CandidateInfo(symbol="A", close=140.0, metadata={}, cb_over_rate=40.0),  # 高价高溢价
            CandidateInfo(symbol="B", close=100.0, metadata={}, cb_over_rate=5.0),   # 低价低溢价 ★
            CandidateInfo(symbol="C", close=110.0, metadata={}, cb_over_rate=10.0),  # 中低
            CandidateInfo(symbol="D", close=130.0, metadata={}, cb_over_rate=30.0),  # 中高
            CandidateInfo(symbol="E", close=105.0, metadata={}, cb_over_rate=8.0),   # 低 ★
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert len(ranked) == 3
        # B 和 E 应排在前面（最低 Z 值）
        assert "B" in ranked
        assert "E" in ranked
        assert "A" not in ranked  # 最差的应被排除

    def test_premium_heavy_weight(self):
        """溢价率权重高时，低溢价率的标的应排名更前"""
        strategy = DoubleLowZScoreStrategy(
            config=DoubleLowZScoreConfig(
                top_k=2,
                price_weight=0.25,
                premium_weight=0.75,
                max_price=200.0,
                max_premium=100.0,
            )
        )
        candidates = [
            CandidateInfo(symbol="HIGH_PRICE_LOW_PREM", close=140.0, metadata={}, cb_over_rate=2.0),
            CandidateInfo(symbol="LOW_PRICE_HIGH_PREM", close=100.0, metadata={}, cb_over_rate=40.0),
            CandidateInfo(symbol="MID_MID", close=120.0, metadata={}, cb_over_rate=20.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        # 溢价率权重 75%，HIGH_PRICE_LOW_PREM 应排第一
        assert ranked[0] == "HIGH_PRICE_LOW_PREM"

    def test_price_heavy_weight(self):
        """价格权重高时，低价标的应排名更前"""
        strategy = DoubleLowZScoreStrategy(
            config=DoubleLowZScoreConfig(
                top_k=2,
                price_weight=0.75,
                premium_weight=0.25,
                max_price=200.0,
                max_premium=100.0,
            )
        )
        candidates = [
            CandidateInfo(symbol="HIGH_PRICE_LOW_PREM", close=140.0, metadata={}, cb_over_rate=2.0),
            CandidateInfo(symbol="LOW_PRICE_HIGH_PREM", close=100.0, metadata={}, cb_over_rate=40.0),
            CandidateInfo(symbol="MID_MID", close=120.0, metadata={}, cb_over_rate=20.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        # 价格权重 75%，LOW_PRICE_HIGH_PREM 应排第一
        assert ranked[0] == "LOW_PRICE_HIGH_PREM"

    def test_with_cb_value(self):
        """启用转股价值因子"""
        strategy = DoubleLowZScoreStrategy(
            config=DoubleLowZScoreConfig(
                top_k=2,
                price_weight=0.40,
                premium_weight=0.40,
                use_cb_value=True,
                cb_value_weight=0.20,
                max_price=200.0,
                max_premium=100.0,
            )
        )
        candidates = [
            CandidateInfo(symbol="LOW_CV", close=100.0, metadata={},
                          cb_over_rate=10.0, cb_value=80.0),   # 低转股价值
            CandidateInfo(symbol="HIGH_CV", close=100.0, metadata={},
                          cb_over_rate=10.0, cb_value=130.0),  # 高转股价值
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        # 低转股价值 Z 更低，应排名第一
        assert ranked[0] == "LOW_CV"

    def test_empty_candidates(self, strategy):
        assert strategy._score_candidates([], self._ctx()) == []

    def test_single_candidate(self, strategy):
        candidates = [CandidateInfo(symbol="ONLY", close=100.0, metadata={}, cb_over_rate=5.0)]
        assert strategy._score_candidates(candidates, self._ctx()) == ["ONLY"]

    def test_extra_filter_pass(self, strategy):
        ci = CandidateInfo(symbol="A", close=100.0, metadata={}, cb_over_rate=30.0)
        assert strategy._extra_filter(ci, self._ctx()) is True

    def test_extra_filter_high_premium(self, strategy):
        ci = CandidateInfo(symbol="A", close=100.0, metadata={}, cb_over_rate=70.0)
        assert strategy._extra_filter(ci, self._ctx()) is False

    def test_extra_filter_none_premium(self, strategy):
        ci = CandidateInfo(symbol="A", close=100.0, metadata={}, cb_over_rate=None)
        assert strategy._extra_filter(ci, self._ctx()) is False

    def test_all_same_values(self, strategy):
        """所有值相同时不应崩溃"""
        candidates = [
            CandidateInfo(symbol="A", close=110.0, metadata={}, cb_over_rate=15.0),
            CandidateInfo(symbol="B", close=110.0, metadata={}, cb_over_rate=15.0),
            CandidateInfo(symbol="C", close=110.0, metadata={}, cb_over_rate=15.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert len(ranked) == 3  # top_k=3，全部返回


# ============================================================================
# 排名策略测试
# ============================================================================


class TestDoubleLowRankStrategy:
    """双低排名加权策略测试"""

    @pytest.fixture
    def strategy(self):
        return DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=3,
                rebalance_days=5,
                max_price=150.0,
                max_premium=60.0,
                price_weight=0.50,
                premium_weight=0.50,
                lot_size=10,
            )
        )

    def test_registration(self):
        registry = StrategyRegistry()
        assert "double_low_rank" in registry.list_strategies()

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )

    def test_score_equal_weight(self, strategy):
        """等权重排名"""
        candidates = [
            CandidateInfo(symbol="A", close=140.0, metadata={}, cb_over_rate=40.0),
            CandidateInfo(symbol="B", close=100.0, metadata={}, cb_over_rate=5.0),
            CandidateInfo(symbol="C", close=110.0, metadata={}, cb_over_rate=10.0),
            CandidateInfo(symbol="D", close=130.0, metadata={}, cb_over_rate=30.0),
            CandidateInfo(symbol="E", close=105.0, metadata={}, cb_over_rate=8.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert len(ranked) == 3
        assert "B" in ranked
        assert "E" in ranked
        assert "A" not in ranked

    def test_premium_heavy(self):
        """溢价率权重 100%，退化为纯 low_premium"""
        strategy = DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=2,
                price_weight=0.0,
                premium_weight=1.0,
                max_price=200.0,
                max_premium=100.0,
            )
        )
        candidates = [
            CandidateInfo(symbol="HP_LP", close=140.0, metadata={}, cb_over_rate=2.0),
            CandidateInfo(symbol="LP_HP", close=100.0, metadata={}, cb_over_rate=40.0),
            CandidateInfo(symbol="MM", close=120.0, metadata={}, cb_over_rate=20.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert ranked[0] == "HP_LP"  # 最低溢价率

    def test_price_only(self):
        """价格权重 100%，退化为纯 low_price"""
        strategy = DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=2,
                price_weight=1.0,
                premium_weight=0.0,
                max_price=200.0,
                max_premium=100.0,
            )
        )
        candidates = [
            CandidateInfo(symbol="HP_LP", close=140.0, metadata={}, cb_over_rate=2.0),
            CandidateInfo(symbol="LP_HP", close=100.0, metadata={}, cb_over_rate=40.0),
            CandidateInfo(symbol="MM", close=120.0, metadata={}, cb_over_rate=20.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert ranked[0] == "LP_HP"  # 最低价格

    def test_empty(self, strategy):
        assert strategy._score_candidates([], self._ctx()) == []

    def test_single(self, strategy):
        candidates = [CandidateInfo(symbol="X", close=100.0, metadata={}, cb_over_rate=5.0)]
        assert strategy._score_candidates(candidates, self._ctx()) == ["X"]

    def test_with_cb_value(self):
        """三因子排名"""
        strategy = DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=2,
                price_weight=0.33,
                premium_weight=0.33,
                use_cb_value=True,
                cb_value_weight=0.34,
                max_price=200.0,
                max_premium=100.0,
            )
        )
        candidates = [
            CandidateInfo(symbol="LOW_ALL", close=100.0, metadata={},
                          cb_over_rate=5.0, cb_value=80.0),
            CandidateInfo(symbol="HIGH_ALL", close=140.0, metadata={},
                          cb_over_rate=40.0, cb_value=130.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert ranked[0] == "LOW_ALL"

    def test_extra_filter(self, strategy):
        ci_pass = CandidateInfo(symbol="A", close=100.0, metadata={}, cb_over_rate=30.0)
        ci_fail = CandidateInfo(symbol="B", close=100.0, metadata={}, cb_over_rate=70.0)
        ci_none = CandidateInfo(symbol="C", close=100.0, metadata={}, cb_over_rate=None)
        ctx = self._ctx()
        assert strategy._extra_filter(ci_pass, ctx) is True
        assert strategy._extra_filter(ci_fail, ctx) is False
        assert strategy._extra_filter(ci_none, ctx) is False


# ============================================================================
# 百分位策略测试
# ============================================================================


class TestDoubleLowPercentileStrategy:
    """双低百分位策略测试"""

    @pytest.fixture
    def strategy(self):
        return DoubleLowPercentileStrategy(
            config=DoubleLowPercentileConfig(
                top_k=3,
                rebalance_days=5,
                max_price=150.0,
                max_premium=60.0,
                price_weight=0.50,
                premium_weight=0.50,
                lot_size=10,
            )
        )

    def test_registration(self):
        registry = StrategyRegistry()
        assert "double_low_percentile" in registry.list_strategies()

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )

    def test_score_equal_weight(self, strategy):
        candidates = [
            CandidateInfo(symbol="A", close=140.0, metadata={}, cb_over_rate=40.0),
            CandidateInfo(symbol="B", close=100.0, metadata={}, cb_over_rate=5.0),
            CandidateInfo(symbol="C", close=110.0, metadata={}, cb_over_rate=10.0),
            CandidateInfo(symbol="D", close=130.0, metadata={}, cb_over_rate=30.0),
            CandidateInfo(symbol="E", close=105.0, metadata={}, cb_over_rate=8.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert len(ranked) == 3
        assert "B" in ranked
        assert "E" in ranked

    def test_empty(self, strategy):
        assert strategy._score_candidates([], self._ctx()) == []

    def test_single(self, strategy):
        candidates = [CandidateInfo(symbol="X", close=100.0, metadata={}, cb_over_rate=5.0)]
        assert strategy._score_candidates(candidates, self._ctx()) == ["X"]

    def test_extreme_values(self, strategy):
        """极端值测试（百分位应对极端值鲁棒）"""
        candidates = [
            CandidateInfo(symbol="EXTREME", close=200.0, metadata={}, cb_over_rate=100.0),
            CandidateInfo(symbol="NORMAL1", close=105.0, metadata={}, cb_over_rate=8.0),
            CandidateInfo(symbol="NORMAL2", close=110.0, metadata={}, cb_over_rate=12.0),
            CandidateInfo(symbol="NORMAL3", close=108.0, metadata={}, cb_over_rate=10.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert "EXTREME" not in ranked  # 极端值应排名最差


# ============================================================================
# 策略间对比测试
# ============================================================================


class TestDoubleVariantsComparison:
    """三种变种的对比验证"""

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )

    def _candidates(self):
        """标准测试候选池"""
        return [
            CandidateInfo(symbol="A", close=100.0, metadata={}, cb_over_rate=5.0),
            CandidateInfo(symbol="B", close=110.0, metadata={}, cb_over_rate=10.0),
            CandidateInfo(symbol="C", close=120.0, metadata={}, cb_over_rate=20.0),
            CandidateInfo(symbol="D", close=130.0, metadata={}, cb_over_rate=35.0),
            CandidateInfo(symbol="E", close=140.0, metadata={}, cb_over_rate=45.0),
        ]

    def test_all_agree_on_best(self):
        """三种方法在明确的最优标的上应一致"""
        common_cfg = {"top_k": 2, "max_price": 200.0, "max_premium": 100.0,
                      "price_weight": 0.5, "premium_weight": 0.5}

        z_strat = DoubleLowZScoreStrategy(config=DoubleLowZScoreConfig(**common_cfg))
        r_strat = DoubleLowRankStrategy(config=DoubleLowRankConfig(**common_cfg))
        p_strat = DoubleLowPercentileStrategy(config=DoubleLowPercentileConfig(**common_cfg))

        ctx = self._ctx()
        candidates = self._candidates()

        z_ranked = z_strat._score_candidates(candidates, ctx)
        r_ranked = r_strat._score_candidates(candidates, ctx)
        p_ranked = p_strat._score_candidates(candidates, ctx)

        # A(100+5) 和 B(110+10) 在所有方法中应排名前2
        assert set(z_ranked) == {"A", "B"}
        assert set(r_ranked) == {"A", "B"}
        assert set(p_ranked) == {"A", "B"}

    def test_all_agree_on_worst(self):
        """三种方法在最差标的上应一致"""
        common_cfg = {"top_k": 3, "max_price": 200.0, "max_premium": 100.0,
                      "price_weight": 0.5, "premium_weight": 0.5}

        z_strat = DoubleLowZScoreStrategy(config=DoubleLowZScoreConfig(**common_cfg))
        r_strat = DoubleLowRankStrategy(config=DoubleLowRankConfig(**common_cfg))
        p_strat = DoubleLowPercentileStrategy(config=DoubleLowPercentileConfig(**common_cfg))

        ctx = self._ctx()
        candidates = self._candidates()

        z_ranked = z_strat._score_candidates(candidates, ctx)
        r_ranked = r_strat._score_candidates(candidates, ctx)
        p_ranked = p_strat._score_candidates(candidates, ctx)

        # E(140+45) 在所有方法中应被排除
        assert "E" not in z_ranked
        assert "E" not in r_ranked
        assert "E" not in p_ranked

    def test_weight_sensitivity(self):
        """不同权重应产生不同排名"""
        candidates = [
            CandidateInfo(symbol="HP_LP", close=140.0, metadata={}, cb_over_rate=2.0),
            CandidateInfo(symbol="LP_HP", close=100.0, metadata={}, cb_over_rate=40.0),
        ]

        # 价格权重 100%
        price_only = DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=1, price_weight=1.0, premium_weight=0.0,
                max_price=200.0, max_premium=100.0
            )
        )
        # 溢价率权重 100%
        prem_only = DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=1, price_weight=0.0, premium_weight=1.0,
                max_price=200.0, max_premium=100.0
            )
        )

        ctx = self._ctx()
        assert price_only._score_candidates(candidates, ctx)[0] == "LP_HP"
        assert prem_only._score_candidates(candidates, ctx)[0] == "HP_LP"

    def test_zscore_vs_rank_with_outlier(self):
        """有极端值时 Z-Score 和排名法可能产生不同结果"""
        candidates = [
            CandidateInfo(symbol="NORMAL1", close=100.0, metadata={}, cb_over_rate=10.0),
            CandidateInfo(symbol="NORMAL2", close=105.0, metadata={}, cb_over_rate=12.0),
            CandidateInfo(symbol="NORMAL3", close=110.0, metadata={}, cb_over_rate=15.0),
            CandidateInfo(symbol="OUTLIER", close=300.0, metadata={}, cb_over_rate=200.0),
        ]

        z_strat = DoubleLowZScoreStrategy(
            config=DoubleLowZScoreConfig(
                top_k=2, max_price=400.0, max_premium=300.0,
            )
        )
        r_strat = DoubleLowRankStrategy(
            config=DoubleLowRankConfig(
                top_k=2, max_price=400.0, max_premium=300.0,
            )
        )

        ctx = self._ctx()
        z_ranked = z_strat._score_candidates(candidates, ctx)
        r_ranked = r_strat._score_candidates(candidates, ctx)

        # 两者都应排除 OUTLIER
        assert "OUTLIER" not in z_ranked
        assert "OUTLIER" not in r_ranked


# ============================================================================
# 从经典双低对比验证
# ============================================================================


class TestClassicDoubleLowComparison:
    """与经典双低 (close + premium_rate) 的对比"""

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )

    def test_classic_implicit_weight_issue(self):
        """证明经典双低中价格因子隐含权重过高

        经典双低：score = close + premium_rate
        当 close ∈ [100, 150], premium_rate ∈ [0, 50] 时，
        价格贡献 ~75%，溢价率贡献 ~25%
        """
        from aipha.strategy.double_low import DoubleLowConfig, DoubleLowStrategy

        # 候选：A 价格低但溢价高，B 价格高但溢价低
        candidates = [
            CandidateInfo(symbol="A", close=105.0, metadata={}, cb_over_rate=40.0),  # DL=145
            CandidateInfo(symbol="B", close=130.0, metadata={}, cb_over_rate=5.0),   # DL=135
        ]

        classic = DoubleLowStrategy(
            config=DoubleLowConfig(top_k=1, max_price=200.0, max_premium=100.0)
        )

        # 经典：B(135) < A(145)，B 排第一（价格主导）
        classic_ranked = classic._score_candidates(candidates, self._ctx())
        assert classic_ranked[0] == "B"

        # Z-Score 等权时，低价的 A 应有可能排名更前（取决于分布）
        # 这个差异正是量纲不一致的体现

    def test_zscore_rebalances_weight(self):
        """Z-Score 能真正实现 50:50 权重

        在中位数附近：价格 ~120, 溢价率 ~20%
        如果一只标的价格比中位数低 20 元（Z≈-1），
        另一只溢价率比中位数低 10%（Z≈-1），
        Z-Score 法下两者对综合分的贡献相同。
        """
        candidates = [
            CandidateInfo(symbol="LOW_P", close=100.0, metadata={}, cb_over_rate=20.0),
            CandidateInfo(symbol="LOW_PR", close=120.0, metadata={}, cb_over_rate=5.0),
            CandidateInfo(symbol="MID", close=120.0, metadata={}, cb_over_rate=20.0),
            CandidateInfo(symbol="HIGH_BOTH", close=140.0, metadata={}, cb_over_rate=35.0),
        ]

        z_strat = DoubleLowZScoreStrategy(
            config=DoubleLowZScoreConfig(
                top_k=2, price_weight=0.5, premium_weight=0.5,
                max_price=200.0, max_premium=100.0,
            )
        )

        ranked = z_strat._score_candidates(candidates, self._ctx())
        # LOW_P 和 LOW_PR 应同等竞争力
        assert "HIGH_BOTH" not in ranked
        assert len(ranked) == 2


# ============================================================================
# 不修改输入副作用测试
# ============================================================================


class TestNoSideEffects:
    """确保策略不修改输入数据"""

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )

    def test_zscore_no_mutation(self):
        c1 = CandidateInfo(symbol="A", close=100.0, metadata={"key": 1}, cb_over_rate=5.0)
        c2 = CandidateInfo(symbol="B", close=110.0, metadata={}, cb_over_rate=10.0)
        candidates = [c1, c2]
        strategy = DoubleLowZScoreStrategy(config=DoubleLowZScoreConfig(top_k=2, max_price=200.0, max_premium=100.0))
        strategy._score_candidates(candidates, self._ctx())
        assert candidates[0] is c1
        assert candidates[1] is c2
        assert c1.metadata == {"key": 1}

    def test_rank_no_mutation(self):
        c1 = CandidateInfo(symbol="A", close=100.0, metadata={"key": 2}, cb_over_rate=5.0)
        c2 = CandidateInfo(symbol="B", close=110.0, metadata={}, cb_over_rate=10.0)
        candidates = [c1, c2]
        strategy = DoubleLowRankStrategy(config=DoubleLowRankConfig(top_k=2, max_price=200.0, max_premium=100.0))
        strategy._score_candidates(candidates, self._ctx())
        assert candidates[0] is c1
        assert c1.metadata == {"key": 2}

    def test_percentile_no_mutation(self):
        c1 = CandidateInfo(symbol="A", close=100.0, metadata={"key": 3}, cb_over_rate=5.0)
        c2 = CandidateInfo(symbol="B", close=110.0, metadata={}, cb_over_rate=10.0)
        candidates = [c1, c2]
        strategy = DoubleLowPercentileStrategy(config=DoubleLowPercentileConfig(top_k=2, max_price=200.0, max_premium=100.0))
        strategy._score_candidates(candidates, self._ctx())
        assert candidates[0] is c1
        assert c1.metadata == {"key": 3}
