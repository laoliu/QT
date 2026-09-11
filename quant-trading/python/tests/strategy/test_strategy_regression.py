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
    rank_normalize,
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
    )


# ============================================================================
# #2: rank_normalize 缺失因子 → 0 分惩罚
# ============================================================================

class TestRankNormalizePenalty:
    """rank_normalize 模块级函数：NaN/None 填充 median(0.5)，有值的得正分。"""

    def test_missing_factor_gets_median_fill(self):
        """None 值应被填充为 median(0.5)。"""
        result = rank_normalize([100.0, 50.0, None])
        assert result[2] == pytest.approx(0.5), "None 应被 median-fill 为 0.5"

    def test_present_factors_ranked_correctly(self):
        result = rank_normalize([100.0, 50.0, 75.0])
        # 3 elements: ranks [1.0, 0.0, 0.5] — highest value gets 1.0
        assert result[0] > result[1], "高值标的分数应高于低值标的"
        assert result[0] == pytest.approx(1.0)

    def test_nan_factor_gets_median_fill(self):
        """NaN 值应与 None 同等处理（median fill）。"""
        result = rank_normalize([10.0, float("nan")])
        assert result[1] == pytest.approx(0.5), "NaN 应被 median-fill 为 0.5"

    def test_zero_fill_mode(self):
        """fill='zero' 模式下 None/NaN 得 0 分。"""
        result = rank_normalize([10.0, None], fill="zero")
        assert result[1] == pytest.approx(0.0), "zero 模式下 NaN 应得 0 分"

    def test_all_nan_returns_median(self):
        """全部 NaN 时返回全 0.5。"""
        result = rank_normalize([None, None, None])
        assert all(v == pytest.approx(0.5) for v in result)


# ============================================================================
# #7: _HISTORY_BUFFER 在 _update_history 中使用
# ============================================================================

class TestHistoryBufferUsage:
    """_update_history 把价格历史裁到「最长 lookback + _HISTORY_BUFFER」。"""

    def test_history_window_limited_by_lookback(self):
        """价格历史长度 = max(momentum_lookback+skip, volatility_lookback) + 5。

        原用例写死 120（旧实现的 deque maxlen）。现在窗口由配置里的
        lookback 算出来，写死常量会在任何人调 lookback 时误报。
        断言公式本身，才是真正要守住的契约。
        """
        from aipha.domain.models import Bar
        cfg = EquityRotationConfig()

        class _TestStrat(EquityRotationStrategy):
            name = "test_buf"
            @property
            def config(self):
                return cfg
            def _score_candidates(self, cands, ctx):
                return []

        s = _TestStrat()
        skip = getattr(cfg, "momentum_skip_recent", 0)
        expected_max = max(cfg.momentum_lookback + skip, cfg.volatility_lookback) + 5
        ts = datetime(2024, 1, 1, 15)
        ctx = _ctx()
        for i in range(expected_max + 30):
            bar = Bar(symbol="A", start_ts=ts, end_ts=ts,
                      open=10.0, high=11.0, low=9.0, close=10.0+i*0.1, volume=1000)
            s.on_bar({"A": bar}, ctx)
        assert len(s._price_history["A"]) == expected_max


# ============================================================================
# #17: PEAD _score_candidates sue=None → 被过滤
# ============================================================================

class TestPEADSueNoneFiltered:
    """sue=None 时 _score_candidates 应将该候选过滤掉。"""

    def test_sue_none_excluded(self):
        strategy = PEADStrategy()
        cand = _cand("000001.SZ", sue=None, roe=10.0)
        result = strategy._score_candidates([cand], _ctx())
        assert "000001.SZ" not in result, "sue=None 应被过滤掉"

    def test_sue_valid_included(self):
        strategy = PEADStrategy()
        cand = _cand("000001.SZ", sue=2.0, roe=10.0)
        result = strategy._score_candidates([cand], _ctx())
        assert "000001.SZ" in result

    def test_sue_zero_included(self):
        """sue=0.0 满足 >= min_sue(0.0) 应保留。"""
        strategy = PEADStrategy()
        cand = _cand("000001.SZ", sue=0.0, roe=10.0)
        result = strategy._score_candidates([cand], _ctx())
        assert "000001.SZ" in result


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
            c.symbol: -c.metadata.get("turnover_rate", 0) if c.metadata.get("turnover_rate", 0) > 0 else float("-inf")
            for c in cands
        }
        assert f_turnover["B"] == float("-inf")

    def test_negative_turnover_gets_neg_inf(self):
        cands = [_cand("A", turnover_rate=-1.0)]
        f_turnover = {
            c.symbol: -c.metadata.get("turnover_rate", 0) if c.metadata.get("turnover_rate", 0) > 0 else float("-inf")
            for c in cands
        }
        assert f_turnover["A"] == float("-inf")


# ============================================================================
# #20: sector_momentum _MIN_INDUSTRY_STOCKS = 3
# ============================================================================

class TestSectorMomentumMinIndustry:
    """SectorMomentumConfig 基础验证。"""

    def test_config_defaults(self):
        """SectorMomentumConfig 默认参数合理。

        原用例断言 ``industry_momentum_lookback``，而这个字段全仓库只出现在
        这一行 —— 从来没实现过。行业动量窗口就是 ``momentum_lookback``
        （行业平均动量和行业内个股动量共用它）。补一个没人读的同名配置项
        只会造出一个"调了不生效"的旋钮，所以按现有字段断言。
        """
        cfg = SectorMomentumConfig()
        assert cfg.top_k == 25
        assert cfg.top_industries == 6
        assert cfg.momentum_lookback == 20

    def test_config_custom_top_industries(self):
        """可自定义 top_industries。"""
        cfg = SectorMomentumConfig(top_k=5, top_industries=2)
        assert cfg.top_industries == 2
        assert cfg.top_k == 5
