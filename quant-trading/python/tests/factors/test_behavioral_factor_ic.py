"""行为因子 IC 相关属性验证测试。

补充测试: 验证因子在截面排序中的单调性、相关性、以及
与 FactorAnalyzer 的集成（IC 计算 + 分层回测正确性）。

与 test_equity_factors.py 中的基础单元测试互补。
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from aipha.factors.equity_factors import (
    compute_abnormal_turnover_factor,
    compute_max_daily_return_factor,
    compute_overreaction_composite,
    compute_str_factor,
)
from aipha.factors.factor_analyzer import (
    FactorAnalyzer,
    compute_ic,
    compute_quantile_returns,
)
from aipha.strategy.equity_rotation_base import EquityCandidateInfo

# ── helpers ───────────────────────────────────────────────────────

def _c(symbol: str, close: float = 10.0) -> EquityCandidateInfo:
    return EquityCandidateInfo(symbol=symbol, close=close, metadata={})


def _make_candidates(n: int) -> list[EquityCandidateInfo]:
    return [_c(f"S{i:04d}", close=10.0 + i * 0.1) for i in range(n)]


# ── ABT 截面测试 ─────────────────────────────────────────────────


class TestABTCrossSectional:
    """ABT 因子截面特性验证。"""

    def test_monotonic_ranking_50_stocks(self):
        """50 股截面: 低 ABT 股票应排名更高。"""
        n = 50
        cands = _make_candidates(n)
        volume_hist = {}
        for i, c in enumerate(cands):
            long_avg = 100.0
            short_avg = 50.0 + i * 5.0
            volume_hist[c.symbol] = [long_avg] * 230 + [short_avg] * 20

        r = compute_abnormal_turnover_factor(volume_hist, cands)
        vals = [r[c.symbol] for c in cands if math.isfinite(r[c.symbol])]
        assert len(vals) >= 40
        mid = len(vals) // 2
        assert sum(vals[:mid]) / mid > sum(vals[mid:]) / (len(vals) - mid)

    def test_abt_with_forward_returns_ic(self):
        """模拟: 低 ABT 股票未来收益更高 → 正 IC。"""
        random.seed(42)
        n = 100
        cands = _make_candidates(n)
        volume_hist = {}
        fwd_ret = {}
        for i, c in enumerate(cands):
            abt_level = 0.5 + i * 0.03
            volume_hist[c.symbol] = [100.0] * 230 + [100.0 * abt_level] * 20
            fwd_ret[c.symbol] = 0.05 - i * 0.001 + random.gauss(0, 0.005)

        factor_vals = compute_abnormal_turnover_factor(volume_hist, cands)
        ic = compute_ic(factor_vals, fwd_ret)
        assert math.isfinite(ic)
        assert ic > 0

    def test_abt_coverage_with_partial_data(self):
        """部分标的数据不足 → NaN 而非报错。"""
        cands = _make_candidates(10)
        volume_hist = {}
        for i, c in enumerate(cands):
            if i < 5:
                volume_hist[c.symbol] = [100.0] * 250
            else:
                volume_hist[c.symbol] = [100.0] * 10
        r = compute_abnormal_turnover_factor(volume_hist, cands)
        valid = sum(1 for v in r.values() if math.isfinite(v))
        assert valid == 5


# ── MAX 截面测试 ─────────────────────────────────────────────────


class TestMAXCrossSectional:
    """MAX 因子截面特性验证。"""

    def test_low_max_better_score(self):
        """30 股截面: 低 MAX (无极端收益) 得分更高。"""
        cands = _make_candidates(30)
        price_hist = {}
        for i, c in enumerate(cands):
            base = [10.0] * 20
            spike = 10.0 * (1 + 0.01 * (i + 1))  # 最后一天涨 1%~30%
            price_hist[c.symbol] = base + [spike]
        r = compute_max_daily_return_factor(price_hist, cands)
        vals = [(c.symbol, r[c.symbol]) for c in cands if math.isfinite(r[c.symbol])]
        # 低 spike (低 MAX) → 更高分 (因子取负)
        assert vals[0][1] > vals[-1][1]

    def test_max_ic_positive_when_low_max_outperform(self):
        """低 MAX 股票未来表现更好 → IC > 0。"""
        random.seed(123)
        n = 80
        cands = _make_candidates(n)
        price_hist, fwd_ret = {}, {}
        for i, c in enumerate(cands):
            spike_pct = 0.02 + i * 0.005
            base = [10.0] * 20
            price_hist[c.symbol] = base + [10.0 * (1 + spike_pct)]
            fwd_ret[c.symbol] = 0.03 - i * 0.0005 + random.gauss(0, 0.003)
        factor_vals = compute_max_daily_return_factor(price_hist, cands)
        ic = compute_ic(factor_vals, fwd_ret)
        assert ic > 0

    def test_max_quantile_monotonic(self):
        """分层回测: Q5 (低 MAX) 收益 > Q1 (高 MAX)。"""
        random.seed(456)
        n = 100
        cands = _make_candidates(n)
        price_hist, fwd_ret = {}, {}
        for i, c in enumerate(cands):
            spike = 0.01 + i * 0.003
            base = [10.0] * 20
            price_hist[c.symbol] = base + [10.0 * (1 + spike)]
            fwd_ret[c.symbol] = 0.04 - i * 0.0008 + random.gauss(0, 0.002)
        fv = compute_max_daily_return_factor(price_hist, cands)
        qr = compute_quantile_returns(fv, fwd_ret, n_quantiles=5)
        assert qr[5] > qr[1]  # Q5 > Q1


# ── STR 截面测试 ─────────────────────────────────────────────────


class TestSTRCrossSectional:
    """STR 因子截面特性验证。"""

    def test_reversal_signal_correct_direction(self):
        """下跌股 STR > 0, 上涨股 STR < 0。"""
        cands = _make_candidates(20)
        price_hist = {}
        for i, c in enumerate(cands):
            ret_5d = -0.10 + i * 0.01  # -10% ~ +10%
            price_hist[c.symbol] = [10.0] * 5 + [10.0 * (1 + ret_5d)]
        r = compute_str_factor(price_hist, cands, lookback=5)
        # 下跌最多 (i=0) → STR 最高
        assert r["S0000"] > r["S0019"]

    def test_str_with_reversal_returns(self):
        """模拟反转行情: 跌幅大→未来反弹 → IC > 0。"""
        random.seed(789)
        n = 60
        cands = _make_candidates(n)
        price_hist, fwd_ret = {}, {}
        for i, c in enumerate(cands):
            past_ret = -0.15 + i * 0.005
            price_hist[c.symbol] = [10.0] * 5 + [10.0 * (1 + past_ret)]
            # 反转: 跌幅大的反弹多
            fwd_ret[c.symbol] = -past_ret * 0.3 + random.gauss(0, 0.005)
        fv = compute_str_factor(price_hist, cands, lookback=5)
        ic = compute_ic(fv, fwd_ret)
        assert ic > 0


# ── Overreaction Composite 集成测试 ──────────────────────────────


class TestOverreactionCompositeIntegration:
    """OverreactionComposite 与子因子集成验证。"""

    def test_composite_outperforms_individual(self):
        """组合因子 IC 应不低于最弱子因子。"""
        random.seed(2024)
        n = 100
        cands = _make_candidates(n)
        volume_hist, price_hist, fwd_ret = {}, {}, {}
        for i, c in enumerate(cands):
            volume_hist[c.symbol] = [100.0] * 230 + [100.0 * (0.5 + i * 0.01)] * 20
            price_hist[c.symbol] = [10.0] * 20 + [10.0 * (1 + 0.01 * i * 0.1)]
            fwd_ret[c.symbol] = 0.03 - i * 0.0005 + random.gauss(0, 0.005)

        abt = compute_abnormal_turnover_factor(volume_hist, cands)
        mx = compute_max_daily_return_factor(price_hist, cands)
        composite = compute_overreaction_composite(
            {"ABT": abt, "MAX": mx}, weights={"ABT": 0.5, "MAX": 0.5}
        )
        ic_abt = compute_ic(abt, fwd_ret)
        ic_mx = compute_ic(mx, fwd_ret)
        ic_comp = compute_ic(composite, fwd_ret)
        # 组合 IC 应 >= min(子因子 IC) (分散化效应)
        assert ic_comp >= min(ic_abt, ic_mx) - 0.05  # 留 5% 容差

    def test_composite_weight_impact(self):
        """权重偏移应影响组合方向。"""
        subfactors = {
            "f1": {f"S{i}": float(i) for i in range(20)},
            "f2": {f"S{i}": float(19 - i) for i in range(20)},
        }
        r_f1_heavy = compute_overreaction_composite(
            subfactors, weights={"f1": 0.9, "f2": 0.1}
        )
        r_f2_heavy = compute_overreaction_composite(
            subfactors, weights={"f1": 0.1, "f2": 0.9}
        )
        # f1 排序与 f2 排序相反, 所以偏权后 top 股票应不同
        top_f1 = max(r_f1_heavy, key=r_f1_heavy.get)
        top_f2 = max(r_f2_heavy, key=r_f2_heavy.get)
        assert top_f1 != top_f2


# ── FactorAnalyzer 集成测试 ──────────────────────────────────────


class TestFactorAnalyzerIntegration:
    """行为因子 + FactorAnalyzer 流水线集成。"""

    def test_multi_period_ic_analysis(self):
        """多期 IC 分析流水线完整性。"""
        random.seed(2025)
        fa = FactorAnalyzer("ABT_test")
        for period in range(10):
            n = 50
            cands = _make_candidates(n)
            vol_hist = {}
            fwd_ret = {}
            for i, c in enumerate(cands):
                vol_hist[c.symbol] = [100.0] * 230 + [100 * (0.8 + i * 0.01)] * 20
                fwd_ret[c.symbol] = 0.02 - i * 0.0004 + random.gauss(0, 0.005)
            fv = compute_abnormal_turnover_factor(vol_hist, cands)
            fa.add_period(fv, fwd_ret)

        report = fa.generate_report()
        assert report.n_periods == 10
        assert math.isfinite(report.mean_ic)
        assert math.isfinite(report.icir)
        assert math.isfinite(report.valid_coverage)
        assert len(report.quantile_returns) == 5
        assert report.factor_name == "ABT_test"

    def test_factor_analyzer_turnover(self):
        """因子换手率合理 (有变化时 > 0)。"""
        random.seed(3000)
        fa = FactorAnalyzer("MAX_turnover")
        for period in range(5):
            n = 40
            cands = _make_candidates(n)
            price_hist = {}
            fwd_ret = {}
            for i, c in enumerate(cands):
                # 每期 spike 排序有显著变化
                spike = 0.01 + random.gauss(0, 0.05)
                price_hist[c.symbol] = [10.0] * 20 + [10.0 * (1 + abs(spike))]
                fwd_ret[c.symbol] = random.gauss(0.01, 0.02)
            fv = compute_max_daily_return_factor(price_hist, cands)
            fa.add_period(fv, fwd_ret)

        report = fa.generate_report()
        assert math.isfinite(report.mean_turnover)
        # 由于每期排序随机变化，换手率应 > 0
        assert report.mean_turnover > 0
