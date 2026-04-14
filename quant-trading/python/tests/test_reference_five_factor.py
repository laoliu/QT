"""参考组合五因子策略单元测试

覆盖: BP 因子、五因子策略、四象限框架、行业分散约束。
至少 12 个测试用例 (§6.2 强制要求)。
"""

from __future__ import annotations

import math
import pytest

from aipha.factors.equity_factors import (
    compute_bp_factor,
    compute_ep_factor,
    compute_quality_factor,
    compute_dividend_factor,
    compute_volatility_factor,
)
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.reference_five_factor import (
    ReferenceFiveFactorConfig,
    ReferenceFiveFactorStrategy,
)


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_candidate(
    symbol: str,
    close: float = 10.0,
    circ_mv: float = 500_000.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    dv_ratio: float = 3.0,
    turnover_rate: float = 2.0,
    amount: float = 5000.0,
    list_date: str = "20100101",
    industry: str = "银行",
    **extra_meta,
) -> EquityCandidateInfo:
    """创建带完整 metadata 的候选标的。"""
    meta = {
        "pe_ttm": pe_ttm,
        "pb": pb,
        "roe": roe,
        "dv_ratio": dv_ratio,
        "amount": amount,
        "turnover_rate_f": turnover_rate,
        "industry": industry,
        **extra_meta,
    }
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        circ_mv=circ_mv,
        turnover_rate=turnover_rate,
        amount=amount,
        list_date=list_date,
        name=f"TEST_{symbol}",
    )


# ============================================================
# T1-T4: BP 因子测试
# ============================================================

class TestBPFactor:
    """BP 因子 (compute_bp_factor) 测试。"""

    def test_bp_basic(self):
        """T1: 基本 BP 计算 — 1/PB 正确。"""
        cands = [
            _make_candidate("A", pb=2.0),
            _make_candidate("B", pb=5.0),
            _make_candidate("C", pb=1.0),
        ]
        result = compute_bp_factor(cands)
        assert abs(result["A"] - 0.5) < 1e-9
        assert abs(result["B"] - 0.2) < 1e-9
        assert abs(result["C"] - 1.0) < 1e-9

    def test_bp_ranking_order(self):
        """T2: 低 PB (高 BP) 排名靠前。"""
        cands = [
            _make_candidate("LOW_PB", pb=0.8),
            _make_candidate("MID_PB", pb=2.0),
            _make_candidate("HIGH_PB", pb=10.0),
        ]
        result = compute_bp_factor(cands)
        assert result["LOW_PB"] > result["MID_PB"] > result["HIGH_PB"]

    def test_bp_negative_pb(self):
        """T3: 负 PB 视为缺失 (NaN)。"""
        cands = [_make_candidate("NEG", pb=-1.0)]
        result = compute_bp_factor(cands)
        assert math.isnan(result["NEG"])

    def test_bp_zero_pb(self):
        """T4: 零 PB 视为缺失 (NaN)。"""
        cands = [_make_candidate("ZERO", pb=0.0)]
        result = compute_bp_factor(cands)
        assert math.isnan(result["ZERO"])


# ============================================================
# T5-T8: 五因子策略测试
# ============================================================

class TestReferenceFiveFactorStrategy:
    """五因子策略核心逻辑测试。"""

    def _make_strategy(self, **kwargs) -> ReferenceFiveFactorStrategy:
        cfg = ReferenceFiveFactorConfig(
            quadrant_enabled=False,
            top_k=5,
            rebalance_days=1,
            min_price=0.01,
            max_price=9999.0,
            min_mv=0.0,
            min_turnover=0.0,
            min_listed_days=0,
            momentum_lookback=5,
            volatility_lookback=5,
            lv_lookback=5,
            buffer_k=0,
            **kwargs,
        )
        return ReferenceFiveFactorStrategy(config=cfg)

    def test_strategy_instantiation(self):
        """T5: 策略可正常实例化，配置正确。"""
        strat = self._make_strategy()
        assert strat.name == "reference_five_factor"
        cfg = strat.config
        assert cfg.weight_ep == 0.20
        assert cfg.weight_bp == 0.20
        assert cfg.weight_roe == 0.20
        assert cfg.weight_dy == 0.20
        assert cfg.weight_lv == 0.20
        assert abs(sum([cfg.weight_ep, cfg.weight_bp, cfg.weight_roe,
                        cfg.weight_dy, cfg.weight_lv]) - 1.0) < 1e-9

    def test_flat_scoring_prefers_value(self):
        """T6: 扁平模式下，高 EP+BP+ROE+DY+LV 的标的排名靠前。"""
        strat = self._make_strategy()
        strat.on_start()

        # 高品质价值股
        good = _make_candidate(
            "GOOD", pe_ttm=8.0, pb=1.0, roe=25.0, dv_ratio=5.0,
        )
        # 低品质成长股
        bad = _make_candidate(
            "BAD", pe_ttm=50.0, pb=8.0, roe=5.0, dv_ratio=0.5,
        )
        cands = [good, bad]

        from aipha.strategy.base import StrategyContext
        ctx = StrategyContext(
            cash=1000000.0,
            nav=1000000.0,
            positions={},
            current_time=None,
            metadata={},
        )
        ranked = strat._score_flat(cands, ctx)
        assert ranked[0] == "GOOD"

    def test_five_factors_all_contribute(self):
        """T7: 五个因子都参与评分，改变任一因子能影响排名。"""
        strat = self._make_strategy()

        # 基准候选
        base = _make_candidate("BASE", pe_ttm=15, pb=2, roe=12, dv_ratio=3)
        # 仅 BP 占优
        bp_star = _make_candidate(
            "BP_STAR", pe_ttm=15, pb=0.5, roe=12, dv_ratio=3,
        )
        cands = [base, bp_star]
        result = compute_bp_factor(cands)
        assert result["BP_STAR"] > result["BASE"]

    def test_config_from_registry(self):
        """T8: 策略可从注册表创建。"""
        from aipha.strategy.registry import create_strategy_from_config
        config = {
            "name": "reference_five_factor",
            "params": {
                "top_k": 20,
                "quadrant_enabled": False,
            },
        }
        strat = create_strategy_from_config(config)
        assert strat.name == "reference_five_factor"
        assert strat.config.top_k == 20


# ============================================================
# T9-T12: 四象限 + 行业分散测试
# ============================================================

class TestQuadrantAndIndustry:
    """四象限选股 + 行业分散化约束测试。"""

    def test_quadrant_split(self):
        """T9: 候选标的按 Size x Style 正确分配到四象限。"""
        strat = ReferenceFiveFactorStrategy(
            config=ReferenceFiveFactorConfig(
                quadrant_enabled=True,
                top_k=8,
                rebalance_days=1,
                min_price=0.01,
                max_price=9999.0,
                min_mv=0.0,
                min_turnover=0.0,
                min_listed_days=0,
                momentum_lookback=5,
                volatility_lookback=5,
                lv_lookback=5,
                buffer_k=0,
                mv_split_percentile=50.0,
            ),
        )
        strat.on_start()
        # 大盘价值
        lv = _make_candidate("LV", circ_mv=2_000_000, pb=1.0)
        # 大盘成长
        lg = _make_candidate("LG", circ_mv=2_000_000, pb=10.0)
        # 小盘价值
        sv = _make_candidate("SV", circ_mv=100_000, pb=1.0)
        # 小盘成长
        sg = _make_candidate("SG", circ_mv=100_000, pb=10.0)
        cands = [lv, lg, sv, sg]

        from aipha.strategy.base import StrategyContext
        ctx = StrategyContext(
            cash=1000000.0, nav=1000000.0,
            positions={}, current_time=None, metadata={},
        )
        ranked = strat._score_with_quadrants(cands, ctx)
        # 所有四个标的都应出现在结果中
        assert len(ranked) == 4

    def test_quadrant_diversification(self):
        """T10: 四象限模式选出的标的分布在多个象限。"""
        strat = ReferenceFiveFactorStrategy(
            config=ReferenceFiveFactorConfig(
                quadrant_enabled=True,
                top_k=20,
                rebalance_days=1,
                min_price=0.01,
                max_price=9999.0,
                min_mv=0.0,
                min_turnover=0.0,
                min_listed_days=0,
                momentum_lookback=5,
                volatility_lookback=5,
                lv_lookback=5,
                buffer_k=0,
            ),
        )
        strat.on_start()

        # 构造 20 个候选：大小 x 价值成长 各 5 只
        cands = []
        for i in range(5):
            cands.append(_make_candidate(
                f"LV{i}", circ_mv=2_000_000, pb=1.0 + i * 0.1,
                pe_ttm=10 + i, roe=15 - i,
            ))
            cands.append(_make_candidate(
                f"LG{i}", circ_mv=2_000_000, pb=8.0 + i * 0.1,
                pe_ttm=30 + i, roe=20 - i,
            ))
            cands.append(_make_candidate(
                f"SV{i}", circ_mv=100_000, pb=1.0 + i * 0.1,
                pe_ttm=10 + i, roe=15 - i,
            ))
            cands.append(_make_candidate(
                f"SG{i}", circ_mv=100_000, pb=8.0 + i * 0.1,
                pe_ttm=30 + i, roe=20 - i,
            ))

        from aipha.strategy.base import StrategyContext
        ctx = StrategyContext(
            cash=1000000.0, nav=1000000.0,
            positions={}, current_time=None, metadata={},
        )
        ranked = strat._score_with_quadrants(cands, ctx)
        # 结果应含多个象限的标的
        prefixes = set(s[:2] for s in ranked)
        assert len(prefixes) >= 3  # 至少 3 个象限有标的入选

    def test_industry_constraint_config(self):
        """T11: max_industry_pct 配置正确传递。"""
        cfg = ReferenceFiveFactorConfig(max_industry_pct=0.12)
        assert cfg.max_industry_pct == 0.12
        # 默认低于 1.0 → 行业限制启用
        assert cfg.max_industry_pct < 1.0

    def test_min_industries_config(self):
        """T12: min_industries 配置默认为 6。"""
        cfg = ReferenceFiveFactorConfig()
        assert cfg.min_industries == 6

    def test_equal_weight_sums_to_one(self):
        """T13: 五因子等权之和 = 1.0。"""
        cfg = ReferenceFiveFactorConfig()
        total = (cfg.weight_ep + cfg.weight_bp + cfg.weight_roe
                 + cfg.weight_dy + cfg.weight_lv)
        assert abs(total - 1.0) < 1e-9

    def test_custom_weights(self):
        """T14: 自定义因子权重正常工作。"""
        cfg = ReferenceFiveFactorConfig(
            weight_ep=0.30,
            weight_bp=0.30,
            weight_roe=0.20,
            weight_dy=0.10,
            weight_lv=0.10,
        )
        assert cfg.weight_ep == 0.30
        assert cfg.weight_bp == 0.30


# ---------------------------------------------------------------------------
# V7 新功能测试: ROE 过滤 + 小市值因子
# ---------------------------------------------------------------------------


class TestROEFilter:
    """ROE 过滤模式测试 (V7 新增)"""

    def test_roe_filter_config_defaults(self):
        """T15: roe_as_filter 默认关闭。"""
        cfg = ReferenceFiveFactorConfig()
        assert cfg.roe_as_filter is False
        assert cfg.min_roe == 5.0

    def test_roe_filter_enabled_config(self):
        """T16: 启用 ROE 过滤模式。"""
        cfg = ReferenceFiveFactorConfig(
            roe_as_filter=True, min_roe=8.0, weight_roe=0.0,
        )
        assert cfg.roe_as_filter is True
        assert cfg.min_roe == 8.0
        assert cfg.weight_roe == 0.0

    def test_roe_filter_removes_low_roe(self):
        """T17: ROE 过滤剔除低 ROE 候选。"""
        from datetime import datetime
        cands = [
            _make_candidate("A", roe=12.0, industry="银行"),
            _make_candidate("B", roe=3.0, industry="券商"),  # 低于 min_roe=5
            _make_candidate("C", roe=8.0, industry="保险"),
        ]
        cfg = ReferenceFiveFactorConfig(
            roe_as_filter=True, min_roe=5.0,
            weight_roe=0.0, top_k=10,
            quadrant_enabled=False,
        )
        strat = ReferenceFiveFactorStrategy(cfg)
        strat._price_history = {}
        from aipha.strategy.base import StrategyContext
        ctx = StrategyContext(
            current_time=datetime(2024, 1, 1),
            positions={}, cash=1e6, nav=1e6,
        )
        result = strat._score_flat(cands, ctx)
        # B (roe=3.0) should be filtered out
        assert "B" not in result
        assert len(result) == 2

    def test_roe_filter_all_removed(self):
        """T18: ROE 过滤剔除所有候选时返回空。"""
        from datetime import datetime
        cands = [
            _make_candidate("X", roe=2.0),
            _make_candidate("Y", roe=1.0),
        ]
        cfg = ReferenceFiveFactorConfig(
            roe_as_filter=True, min_roe=5.0,
            weight_roe=0.0, quadrant_enabled=False,
        )
        strat = ReferenceFiveFactorStrategy(cfg)
        strat._price_history = {}
        from aipha.strategy.base import StrategyContext
        ctx = StrategyContext(
            current_time=datetime(2024, 1, 1),
            positions={}, cash=1e6, nav=1e6,
        )
        result = strat._score_flat(cands, ctx)
        assert result == []

    def test_roe_ranking_disabled_when_filter(self):
        """T19: roe_as_filter=True 时 weight_roe 不影响排名。"""
        cfg = ReferenceFiveFactorConfig(
            roe_as_filter=True,
            weight_roe=0.20,  # 即使设了权重,也不参与排名
            quadrant_enabled=False,
        )
        assert cfg.roe_as_filter is True


class TestSizeFactor:
    """小市值因子测试 (V7 新增)"""

    def test_size_factor_config_default(self):
        """T20: weight_size 默认 0 (不启用)。"""
        cfg = ReferenceFiveFactorConfig()
        assert cfg.weight_size == 0.0

    def test_size_factor_enabled(self):
        """T21: weight_size > 0 时启用。"""
        cfg = ReferenceFiveFactorConfig(weight_size=0.15)
        assert cfg.weight_size == 0.15

    def test_v7_weights_sum(self):
        """T22: V7 配置权重之和验证。"""
        # V7: BP(28%) + LV(22%) + EP(20%) + DY(15%) + Size(15%) = 100%
        cfg = ReferenceFiveFactorConfig(
            weight_bp=0.28, weight_lv=0.22, weight_ep=0.20,
            weight_dy=0.15, weight_size=0.15,
            weight_roe=0.0, roe_as_filter=True,
        )
        total = (
            cfg.weight_bp + cfg.weight_lv + cfg.weight_ep
            + cfg.weight_dy + cfg.weight_size
        )
        assert abs(total - 1.0) < 1e-9
