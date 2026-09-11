"""
PEAD 事件驱动策略测试

测试覆盖：
- PEADConfig 初始化/自定义
- PEADStrategy 构造
- _extra_filter: roe/sue 门槛
- _score_candidates: 5因子打分
- _compute_sue_values: 从 metadata 获取 SUE
- rank_normalize: 排名归一化
"""

import math
from datetime import datetime

import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.pead_event import PEADConfig, PEADStrategy


def _make_context(dt=None, positions=None, cash=1_000_000.0, nav=1_000_000.0):
    return StrategyContext(
        current_time=dt or datetime(2024, 6, 1),
        positions=positions or {},
        cash=cash,
        nav=nav,
        metadata={},
    )


def _make_candidate(
    symbol="000001.SZ",
    close=20.0,
    circ_mv=500_000.0,
    roe=10.0,
    sue=1.5,
    earnings_growth=15.0,
    amount=5_000.0,
    list_date="20200101",
    **extra_meta,
):
    meta = {
        "roe": roe,
        "sue": sue,
        "dt_netprofit_yoy": earnings_growth,
        "circ_mv": circ_mv,
        **extra_meta,
    }
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        circ_mv=circ_mv,
        turnover_rate=3.0,
        amount=amount,
        list_date=list_date,
        name="测试股票",
    )


# ============================================================
# Config
# ============================================================


class TestPEADConfig:
    def test_default_values(self):
        cfg = PEADConfig()
        assert cfg.top_k == 20
        assert cfg.weight_sue == 0.45
        assert cfg.weight_earnings_growth == 0.20
        assert cfg.weight_quality == 0.15
        assert cfg.weight_momentum == 0.10
        assert cfg.weight_volatility == 0.10
        assert cfg.min_sue == 0.0
        assert cfg.min_roe == 3.0

    def test_custom_values(self):
        cfg = PEADConfig(
            top_k=10,
            weight_sue=0.5,
            weight_quality=0.3,
            min_roe=5.0,
            min_sue=0.5,
        )
        assert cfg.top_k == 10
        assert cfg.weight_sue == 0.5
        assert cfg.min_roe == 5.0

    def test_weights_sum_to_one(self):
        cfg = PEADConfig()
        total = (
            cfg.weight_sue
            + cfg.weight_earnings_growth
            + cfg.weight_quality
            + cfg.weight_momentum
            + cfg.weight_volatility
        )
        assert abs(total - 1.0) < 1e-9


# ============================================================
# Strategy construction
# ============================================================


class TestPEADStrategyConstruction:
    def test_default_construction(self):
        s = PEADStrategy()
        assert s.name == "pead_event"
        assert isinstance(s.config, PEADConfig)

    def test_construction_with_config(self):
        cfg = PEADConfig(top_k=5, min_roe=8.0)
        s = PEADStrategy(config=cfg)
        assert s.config.top_k == 5
        assert s.config.min_roe == 8.0

    def test_construction_with_kwargs(self):
        s = PEADStrategy(top_k=15, min_sue=0.5)
        assert s.config.top_k == 15
        assert s.config.min_sue == 0.5


# ============================================================
# _extra_filter
# ============================================================


class TestPEADExtraFilter:
    def setup_method(self):
        self.strategy = PEADStrategy(config=PEADConfig(min_roe=5.0, min_sue=0.5))
        self.ctx = _make_context()

    def test_pass_all_criteria(self):
        c = _make_candidate(roe=10.0, sue=1.0)
        assert self.strategy._extra_filter(c, self.ctx) is True

    def test_reject_low_roe(self):
        c = _make_candidate(roe=3.0, sue=1.0)
        assert self.strategy._extra_filter(c, self.ctx) is False

    def test_reject_low_sue(self):
        c = _make_candidate(roe=10.0, sue=0.3)
        assert self.strategy._extra_filter(c, self.ctx) is False

    def test_reject_both_low(self):
        c = _make_candidate(roe=2.0, sue=0.1)
        assert self.strategy._extra_filter(c, self.ctx) is False

    def test_borderline_roe_exact(self):
        c = _make_candidate(roe=5.0, sue=1.0)
        result = self.strategy._extra_filter(c, self.ctx)
        assert isinstance(result, bool)

    def test_borderline_sue_exact(self):
        c = _make_candidate(roe=10.0, sue=0.5)
        result = self.strategy._extra_filter(c, self.ctx)
        assert isinstance(result, bool)

    def test_missing_roe_metadata_passes(self):
        """ROE 缺失（None）时不过滤（只过滤存在但低于阈值的情况）"""
        meta = {"sue": 1.0, "dt_netprofit_yoy": 15.0}
        c = EquityCandidateInfo(
            symbol="000001.SZ",
            close=20.0,
            metadata=meta,
            circ_mv=500_000.0,
        )
        result = self.strategy._extra_filter(c, self.ctx)
        assert result is True

    def test_missing_sue_metadata_passes(self):
        """SUE 缺失（None）时应被过滤（PEAD 核心依赖 SUE）"""
        meta = {"roe": 10.0, "dt_netprofit_yoy": 15.0}
        c = EquityCandidateInfo(
            symbol="000002.SZ",
            close=20.0,
            metadata=meta,
            circ_mv=500_000.0,
        )
        result = self.strategy._extra_filter(c, self.ctx)
        assert result is False  # SUE 缺失时应拒绝


# ============================================================
# _compute_sue_values
# ============================================================


class TestComputeSUEValues:
    def setup_method(self):
        self.strategy = PEADStrategy()

    def test_from_metadata(self):
        candidates = [
            _make_candidate("A", sue=2.0),
            _make_candidate("B", sue=-1.5),
        ]
        result = self.strategy._compute_sue_values(candidates)
        assert result["A"] == 2.0
        assert result["B"] == -1.5

    def test_missing_sue_is_nan(self):
        """SUE 缺失时返回 NaN（"无数据"），不是 -inf（"有数据、排最后"）。

        原用例断言 -inf。但在 rank_normalize 里这两个值语义不同：
        -inf = 有数据、排最末、**保留**；NaN = 无数据、**剔除**。
        用 -inf 表示缺失，等于让没有超预期数据的股票靠动量/质量
        照样排进买入名单 —— 而 PEAD 的整个立论就是 SUE。
        """
        c = EquityCandidateInfo(
            symbol="X",
            close=20.0,
            metadata={"roe": 5.0},
            circ_mv=100_000.0,
        )
        result = self.strategy._compute_sue_values([c])
        assert math.isnan(result["X"])


# ============================================================
# _score_candidates
# ============================================================


class TestPEADScoreCandidates:
    def setup_method(self):
        self.strategy = PEADStrategy(
            config=PEADConfig(
                top_k=3,
                buffer_k=0,
                min_sue=0.0,
                min_roe=0.0,
                momentum_lookback=5,
                volatility_lookback=5,
            )
        )
        self.strategy.on_start()
        self.ctx = _make_context()

    def test_returns_list_of_symbols(self):
        candidates = [
            _make_candidate("A", sue=3.0, earnings_growth=20.0, roe=15.0),
            _make_candidate("B", sue=2.0, earnings_growth=15.0, roe=12.0),
            _make_candidate("C", sue=1.0, earnings_growth=10.0, roe=8.0),
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert isinstance(result, list)
        assert len(result) <= 3 + self.strategy.config.buffer_k

    def test_high_sue_ranks_first(self):
        """纯 SUE 权重时，高 SUE 应排前面"""
        strategy = PEADStrategy(
            config=PEADConfig(
                top_k=3,
                buffer_k=0,
                weight_sue=1.0,
                weight_earnings_growth=0.0,
                weight_quality=0.0,
                weight_momentum=0.0,
                weight_volatility=0.0,
            )
        )
        strategy.on_start()
        candidates = [
            _make_candidate("LOW_SUE", sue=0.1, earnings_growth=5.0, roe=5.0),
            _make_candidate("HIGH_SUE", sue=5.0, earnings_growth=5.0, roe=5.0),
        ]
        result = strategy._score_candidates(candidates, self.ctx)
        assert result[0] == "HIGH_SUE"

    def test_empty_candidates(self):
        result = self.strategy._score_candidates([], self.ctx)
        assert result == []

    def test_single_candidate(self):
        candidates = [
            _make_candidate("ONLY", sue=1.0, earnings_growth=10.0, roe=8.0),
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert result == ["ONLY"]

    def test_all_symbols_included_with_large_topk(self):
        """top_k 大于候选数量时全部返回"""
        strategy = PEADStrategy(
            config=PEADConfig(top_k=100, buffer_k=0)
        )
        strategy.on_start()
        candidates = [
            _make_candidate(f"S{i}", sue=float(i), earnings_growth=10.0, roe=5.0)
            for i in range(5)
        ]
        result = strategy._score_candidates(candidates, self.ctx)
        assert len(result) == 5


# ============================================================
# Phase 2 — 边界 & 极端场景补充
# ============================================================


class TestPEADBoundaryScenarios:
    """PEAD 策略边界条件与极端场景测试"""

    def setup_method(self):
        self.strategy = PEADStrategy(
            config=PEADConfig(
                top_k=5,
                buffer_k=0,
                min_sue=0.0,
                min_roe=0.0,
                momentum_lookback=5,
                volatility_lookback=5,
            )
        )
        self.strategy.on_start()
        self.ctx = _make_context()

    def test_all_candidates_negative_sue_empty_after_filter(self):
        """全部 SUE < 0 且 min_sue=0.5 → extra_filter 全部拒绝"""
        strategy = PEADStrategy(
            config=PEADConfig(
                top_k=5, buffer_k=0,
                min_sue=0.5, min_roe=0.0,
            )
        )
        strategy.on_start()
        candidates = [
            _make_candidate(f"S{i}", sue=-float(i + 1), roe=10.0)
            for i in range(5)
        ]
        # 全部候选 SUE < 0.5 → extra_filter 返回 False
        for c in candidates:
            assert strategy._extra_filter(c, self.ctx) is False

    def test_all_none_financial_data_no_crash(self):
        """所有财务 metadata 字段均为 None 时不崩溃，且一个都不推。

        原用例断言"全部返回，只是分数低"。分数低不等于不买 ——
        top_k 是按排名取的，一组全缺数据的候选里排第一的照样会被买。
        sue 全 None 时 PEAD 没有任何选股依据，正确结果是空名单
        （与 TestPEADSueNoneFiltered 同一契约）。
        """
        candidates = [
            EquityCandidateInfo(
                symbol=f"S{i}",
                close=20.0,
                metadata={"roe": None, "sue": None, "dt_netprofit_yoy": None},
                circ_mv=500_000.0,
                turnover_rate=3.0,
                amount=5_000.0,
                list_date="20200101",
            )
            for i in range(3)
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert isinstance(result, list)
        assert result == []

    def test_sue_online_computation_path(self):
        """eps/eps_prev/eps_std 在线计算 SUE 路径"""
        c = EquityCandidateInfo(
            symbol="ONLINE",
            close=20.0,
            metadata={
                "eps": 1.5,
                "eps_yoy_prev": 1.0,
                "eps_std": 0.2,
            },
            circ_mv=500_000.0,
        )
        result = self.strategy._compute_sue_values([c])
        expected = (1.5 - 1.0) / 0.2  # = 2.5
        assert abs(result["ONLINE"] - expected) < 1e-9

    def test_sue_online_zero_std_fallback(self):
        """eps_std = 0 时无法计算 SUE → NaN（缺失），随后被剔除。

        除以 0 标准差没有意义，属于"算不出来"而不是"算出来很差"，
        所以和 metadata 里压根没有 sue 走同一条路（见
        test_missing_sue_is_nan 的理由）。
        """
        c = EquityCandidateInfo(
            symbol="ZERO_STD",
            close=20.0,
            metadata={"eps": 1.5, "eps_yoy_prev": 1.0, "eps_std": 0.0},
            circ_mv=500_000.0,
        )
        result = self.strategy._compute_sue_values([c])
        assert math.isnan(result["ZERO_STD"])
        # 端到端：算不出 SUE 的候选不进买入名单
        assert self.strategy._score_candidates([c], _make_context()) == []

    def test_sue_extreme_values_ranking(self):
        """SUE 极高 vs 极低的排名正确（纯 SUE 权重）"""
        strategy = PEADStrategy(
            config=PEADConfig(
                top_k=10, buffer_k=0,
                weight_sue=1.0,
                weight_earnings_growth=0.0,
                weight_quality=0.0,
                weight_momentum=0.0,
                weight_volatility=0.0,
            )
        )
        strategy.on_start()
        candidates = [
            _make_candidate("TINY_SUE", sue=0.01, roe=10.0),
            _make_candidate("HUGE_SUE", sue=100.0, roe=10.0),
            _make_candidate("MID_SUE", sue=5.0, roe=10.0),
        ]
        result = strategy._score_candidates(candidates, self.ctx)
        assert result[0] == "HUGE_SUE"
        assert result[-1] == "TINY_SUE"

    def test_score_with_mixed_none_and_valid(self):
        """部分标的有财务数据、部分无 → 不崩溃且有数据的排更前"""
        strategy = PEADStrategy(
            config=PEADConfig(
                top_k=10, buffer_k=0,
                weight_sue=0.5,
                weight_quality=0.5,
                weight_earnings_growth=0.0,
                weight_momentum=0.0,
                weight_volatility=0.0,
            )
        )
        strategy.on_start()
        c_good = _make_candidate("GOOD", sue=3.0, roe=15.0)
        c_none = EquityCandidateInfo(
            symbol="NONE_DATA",
            close=20.0,
            metadata={"roe": None, "sue": None},
            circ_mv=500_000.0,
        )
        result = strategy._score_candidates([c_good, c_none], self.ctx)
        assert result[0] == "GOOD"

    def test_config_consistency_with_yml(self):
        """代码默认值与 YAML 配置一致性验证"""
        cfg = PEADConfig()
        # 与 configs/backtest/pead_event.yml 的 strategy.parameters 对齐
        assert cfg.top_k == 20
        assert cfg.rebalance_days == 20
        assert cfg.min_sue == 0.0
        assert cfg.min_roe == 3.0
        assert cfg.weight_sue == 0.45
        assert cfg.weight_earnings_growth == 0.20
        assert cfg.weight_quality == 0.15
        assert cfg.weight_momentum == 0.10
        assert cfg.weight_volatility == 0.10
        assert cfg.stop_loss_pct == 0.15
        assert cfg.earnings_growth_field == "dt_netprofit_yoy"

    def test_registry_roundtrip(self):
        """通过注册表创建 PEAD 实例并验证可用"""
        from aipha.strategy.registry import create_strategy
        s = create_strategy("pead_event")
        assert s.name == "pead_event"
        assert hasattr(s, "_score_candidates")
        assert hasattr(s, "_extra_filter")


# ============================================================
# Phase 3 — 因子权重敏感性验证（P0-2 评审整改）
# ============================================================


class TestPEADFactorWeightSensitivity:
    """验证 PEAD 因子权重变化确实会改变排名顺序。

    评审发现：真实回测中 SUE/growth/quality 权重扰动对排名无影响。
    原因是 SUE 覆盖率低导致排名退化为仅由动量+波动率决定。
    本测试在所有因子数据完备的条件下验证权重敏感性。
    """

    def _run_scoring(self, weights: dict, candidates: list) -> list[str]:
        """指定权重运行打分，返回排名列表"""
        cfg = PEADConfig(
            top_k=10, buffer_k=0,
            weight_sue=weights.get("sue", 0.0),
            weight_earnings_growth=weights.get("growth", 0.0),
            weight_quality=weights.get("quality", 0.0),
            weight_momentum=weights.get("momentum", 0.0),
            weight_volatility=weights.get("volatility", 0.0),
            momentum_lookback=5,
            volatility_lookback=5,
        )
        strategy = PEADStrategy(config=cfg)
        strategy.on_start()
        ctx = _make_context()
        return strategy._score_candidates(candidates, ctx)

    def test_sue_dominant_vs_quality_dominant(self):
        """SUE 主导与 Quality 主导产生不同排名"""
        # A: 高 SUE, 低 ROE  vs  B: 低 SUE, 高 ROE
        candidates = [
            _make_candidate("A", sue=5.0, roe=5.0, earnings_growth=10.0),
            _make_candidate("B", sue=1.0, roe=30.0, earnings_growth=10.0),
        ]
        rank_sue = self._run_scoring({"sue": 1.0}, candidates)
        rank_quality = self._run_scoring({"quality": 1.0}, candidates)
        assert rank_sue[0] == "A", "SUE 主导时高 SUE 排首"
        assert rank_quality[0] == "B", "Quality 主导时高 ROE 排首"

    def test_sue_dominant_vs_growth_dominant(self):
        """SUE 主导与 Growth 主导产生不同排名"""
        candidates = [
            _make_candidate("A", sue=5.0, roe=10.0, earnings_growth=5.0),
            _make_candidate("B", sue=1.0, roe=10.0, earnings_growth=50.0),
        ]
        rank_sue = self._run_scoring({"sue": 1.0}, candidates)
        rank_growth = self._run_scoring({"growth": 1.0}, candidates)
        assert rank_sue[0] == "A"
        assert rank_growth[0] == "B"

    def test_mixed_weights_shift_ranking(self):
        """混合权重变化确实移动排名"""
        candidates = [
            _make_candidate("HI_SUE", sue=10.0, roe=5.0, earnings_growth=5.0),
            _make_candidate("HI_ROE", sue=1.0, roe=25.0, earnings_growth=5.0),
            _make_candidate("HI_GRO", sue=1.0, roe=5.0, earnings_growth=80.0),
        ]
        # SUE 高权重 → HI_SUE 排首
        rank_a = self._run_scoring({"sue": 0.8, "quality": 0.1, "growth": 0.1}, candidates)
        assert rank_a[0] == "HI_SUE"

        # Quality 高权重 → HI_ROE 排首
        rank_b = self._run_scoring({"sue": 0.1, "quality": 0.8, "growth": 0.1}, candidates)
        assert rank_b[0] == "HI_ROE"

        # Growth 高权重 → HI_GRO 排首
        rank_c = self._run_scoring({"sue": 0.1, "quality": 0.1, "growth": 0.8}, candidates)
        assert rank_c[0] == "HI_GRO"

    def test_equal_weights_balanced_ranking(self):
        """等权时综合得分决定排名"""
        candidates = [
            _make_candidate("BALANCED", sue=3.0, roe=15.0, earnings_growth=30.0),
            _make_candidate("ONE_DIM", sue=10.0, roe=2.0, earnings_growth=2.0),
        ]
        rank = self._run_scoring(
            {"sue": 0.33, "quality": 0.33, "growth": 0.34}, candidates
        )
        # BALANCED 在三个维度都不错，综合应排更前
        assert rank[0] == "BALANCED"

    def test_many_candidates_weight_shift(self):
        """较多候选时权重变化仍能影响 top rank"""
        candidates = [
            _make_candidate(f"S{i}", sue=float(i), roe=float(20 - i), earnings_growth=float(i * 3))
            for i in range(1, 11)
        ]
        # SUE 主导 → S10 排首（最高 SUE=10）
        rank_sue = self._run_scoring({"sue": 1.0}, candidates)
        assert rank_sue[0] == "S10"

        # Quality 主导 → S1 排首（最高 ROE=19）
        rank_q = self._run_scoring({"quality": 1.0}, candidates)
        assert rank_q[0] == "S1"
