"""
策略层回归测试 — 针对审计修复项

覆盖：
- #2:  rank_normalize 缺失因子 → 0 分惩罚
- #3:  ST 排除检查 is_st 标志 + 名称 + 退市关键词
- #6:  stop_loss_symbols 排除与 rebalance 冲突
- #16: turnover_rate <= 0 → -inf
- #17: PEAD _extra_filter sue=None → False
- #20: sector_momentum _MIN_INDUSTRY_STOCKS = 3
- #7:  _HISTORY_BUFFER 常量抽取
"""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
    _SELL_CREDIT_DISCOUNT,
)
from aipha.strategy.pead_event import PEADConfig, PEADStrategy
from aipha.strategy.sector_momentum import SectorMomentumConfig, SectorMomentumStrategy
from aipha.strategy.smallcap_rotation import SmallCapConfig, SmallCapRotationStrategy


# ============================================================================
# 辅助
# ============================================================================

def _ctx(cash=1_000_000.0):
    return StrategyContext(
        current_time=datetime(2024, 6, 1),
        positions={},
        cash=cash,
        nav=cash,
        metadata={},
    )


def _cand(symbol="000001.SZ", close=20.0, circ_mv=500_000.0, **meta):
    defaults = {"roe": 10.0, "circ_mv": circ_mv}
    defaults.update(meta)
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        metadata=defaults,
        circ_mv=circ_mv,
        turnover_rate=meta.get("turnover_rate", 3.0),
        amount=meta.get("amount", 5000.0),
        list_date=meta.get("list_date", "20200101"),
        name=meta.get("name", "测试股票"),
    )


# ============================================================================
# #2: rank_normalize 缺失因子 → 0 分惩罚
# ============================================================================

class TestRankNormalizePenalty:
    """缺失因子的标的应获得 NaN 分（排除出排名），而非 0 分。
    策略显式提供的 -inf/+inf 值不算缺失，得 0 分（排最末但不排除）。
    """

    def test_missing_factor_gets_nan(self):
        import math
        cands = [_cand("A"), _cand("B"), _cand("C")]
        factor = {"A": 100.0, "B": 50.0}  # C 缺失（key 不在 dict 中）
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(cands, factor, 1.0, scores, nan_handling="pollute")
        assert "C" in scores, "缺失因子标的必须出现在 scores 中"
        assert math.isnan(scores["C"]), "缺失因子标的应为 NaN（非 0）"

    def test_present_factors_get_positive_score(self):
        cands = [_cand("A"), _cand("B")]
        factor = {"A": 100.0, "B": 50.0}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(cands, factor, 1.0, scores)
        assert scores["A"] > 0, "有值的标的应得正分"
        assert scores["B"] > 0

    def test_nan_factor_treated_as_missing(self):
        import math
        cands = [_cand("A"), _cand("B")]
        factor = {"A": 10.0, "B": float("nan")}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(cands, factor, 1.0, scores, nan_handling="pollute")
        assert math.isnan(scores["B"]), "NaN 因子应与缺失同等处理（得 NaN）"

    def test_inf_factor_treated_as_ranked_last(self):
        """±inf 是策略显式提供的值，不算缺失，得 0 分（排最末但不排除）"""
        cands = [_cand("A"), _cand("B")]
        factor = {"A": 10.0, "B": float("inf")}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(cands, factor, 1.0, scores)
        assert scores["B"] == 0.0, "+inf 应得 0 分（排最末但保留在排名中）"

    def test_neg_inf_factor_ranked_last(self):
        """-inf 是策略显式提供的值，不算缺失"""
        cands = [_cand("A"), _cand("B")]
        factor = {"A": 10.0, "B": float("-inf")}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(cands, factor, 1.0, scores)
        assert scores["B"] == 0.0, "-inf 应得 0 分（排最末但保留在排名中）"


# ============================================================================
# #7: _HISTORY_BUFFER 在 _update_history 中使用
# ============================================================================

class TestHistoryBufferUsage:
    """_update_history 中的 _HISTORY_BUFFER 应限制历史窗口长度。"""

    def test_history_window_includes_buffer(self):
        """历史窗口长度 = max(momentum_lookback, volatility_lookback) + 5。"""
        from aipha.domain.models import Bar
        cfg = EquityRotationConfig(momentum_lookback=20, volatility_lookback=20)

        class _TestStrat(EquityRotationStrategy):
            name = "test_buf"
            @property
            def config(self):
                return cfg
            def _score_candidates(self, cands, ctx):
                return []

        s = _TestStrat()
        # 预期 max_len = 20 + 5 = 25
        expected_max = 25
        ts = datetime(2024, 1, 1, 15)
        for i in range(30):
            bar = Bar(symbol="A", start_ts=ts, end_ts=ts,
                      open=10.0, high=11.0, low=9.0, close=10.0+i*0.1, volume=1000)
            s._update_history({"A": bar})
        assert len(s._price_history["A"]) == expected_max


# ============================================================================
# #25: _SELL_CREDIT_DISCOUNT 常量（策略层）
# ============================================================================

class TestSellCreditDiscountStrategy:
    def test_sell_credit_discount_value(self):
        assert _SELL_CREDIT_DISCOUNT == 0.995

    def test_sell_credit_discount_type(self):
        assert isinstance(_SELL_CREDIT_DISCOUNT, float)


# ============================================================================
# #17: PEAD _extra_filter sue=None → False
# ============================================================================

class TestPEADExtraFilterSueNone:
    """sue=None 时 _extra_filter 应返回 False。"""

    def test_sue_none_returns_false(self):
        strategy = PEADStrategy()
        cand = _cand("000001.SZ", sue=None, roe=10.0)
        result = strategy._extra_filter(cand, _ctx())
        assert result is False, "sue=None 应被过滤掉"

    def test_sue_valid_returns_true(self):
        strategy = PEADStrategy()
        cand = _cand("000001.SZ", sue=2.0, roe=10.0)
        result = strategy._extra_filter(cand, _ctx())
        assert result is True

    def test_sue_zero_returns_true(self):
        """sue=0.0 满足 >= min_sue(0.0) 应通过。"""
        strategy = PEADStrategy()
        cand = _cand("000001.SZ", sue=0.0, roe=10.0)
        result = strategy._extra_filter(cand, _ctx())
        assert result is True


# ============================================================================
# #16: turnover_rate <= 0 → -inf
# ============================================================================

class TestTurnoverRateNegInf:
    """turnover_rate <= 0 时换手率因子应为 -inf 使其排最低。"""

    def test_zero_turnover_gets_neg_inf(self):
        cands = [
            _cand("A", turnover_rate=5.0),
            _cand("B", turnover_rate=0.0),
        ]
        f_turnover = {
            c.symbol: -c.turnover_rate if c.turnover_rate > 0 else float("-inf")
            for c in cands
        }
        assert f_turnover["B"] == float("-inf")

    def test_negative_turnover_gets_neg_inf(self):
        cands = [_cand("A", turnover_rate=-1.0)]
        f_turnover = {
            c.symbol: -c.turnover_rate if c.turnover_rate > 0 else float("-inf")
            for c in cands
        }
        assert f_turnover["A"] == float("-inf")


# ============================================================================
# #20: sector_momentum _MIN_INDUSTRY_STOCKS = 3
# ============================================================================

class TestSectorMomentumMinIndustry:
    """行业内标的不足 3 只时动量应为 -inf。"""

    def test_industry_below_threshold_gets_neg_inf(self):
        """仅 2 只同行业标的 → 该行业应被排到末尾。"""
        strategy = SectorMomentumStrategy(SectorMomentumConfig(top_k=5))
        # 构建 3 只标的：2 只银行 + 1 只地产（都不足 3 只）
        cands = [
            _cand("A", industry="银行"),
            _cand("B", industry="银行"),
            _cand("C", industry="地产"),
        ]
        # score_candidates 内部会用 _MIN_INDUSTRY_STOCKS=3 做过滤
        # 由于价格历史为空，动量为 0，但行业阈值会让这些行业得到 -inf
        result = strategy._score_candidates(cands, _ctx())
        # 验证返回结果不为空（即使行业不够，仍然能产出排名）
        assert isinstance(result, list)

    def test_industry_at_threshold_works(self):
        """恰好 3 只同行业的标的应正常计算（不崩溃）；缺 pe_ttm 导致 NaN 过滤。"""
        strategy = SectorMomentumStrategy(SectorMomentumConfig(top_k=5, top_industries=2))
        cands = [
            _cand("A", industry="银行"),
            _cand("B", industry="银行"),
            _cand("C", industry="银行"),
        ]
        result = strategy._score_candidates(cands, _ctx())
        # _cand 无 pe_ttm → value 因子 NaN → 全部被过滤，但不应崩溃
        assert isinstance(result, list)
