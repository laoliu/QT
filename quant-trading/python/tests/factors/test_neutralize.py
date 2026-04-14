"""因子中性化适配器测试

覆盖：
- neutralize_factor_panel: 市值中性化、行业中性化、双重中性化
- neutralize_multi_factors: 多因子批量
- NeutralizationConfig: 参数控制
- 边界条件: 空数据、少于 min_samples、缺失值
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from aipha.factors.neutralize import (
    NeutralizationConfig,
    neutralize_factor_panel,
    neutralize_multi_factors,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_panels(
    n_dates: int = 10,
    n_symbols: int = 50,
    seed: int = 42,
) -> tuple[
    dict[date, dict[str, float]],   # factor_panel
    dict[date, dict[str, float]],   # market_cap_panel
    dict[date, dict[str, str]],     # industry_panel
]:
    """生成测试用 factor/market_cap/industry 面板"""
    rng = np.random.RandomState(seed)
    base = date(2024, 1, 1)
    industries = ["银行", "地产", "科技", "消费", "医药"]

    factor_panel: dict[date, dict[str, float]] = {}
    cap_panel: dict[date, dict[str, float]] = {}
    ind_panel: dict[date, dict[str, str]] = {}

    for d in range(n_dates):
        dt = base + timedelta(days=d)
        symbols = [f"S{i:04d}" for i in range(n_symbols)]
        caps = np.exp(rng.uniform(18, 24, n_symbols))  # ln(market_cap)
        # 因子值 = 0.5 * log_cap + noise（有意引入市值相关性）
        factor_vals = 0.5 * np.log(caps) + rng.randn(n_symbols)

        factor_panel[dt] = dict(zip(symbols, factor_vals.tolist()))
        cap_panel[dt] = dict(zip(symbols, caps.tolist()))
        ind_panel[dt] = {
            s: industries[i % len(industries)]
            for i, s in enumerate(symbols)
        }

    return factor_panel, cap_panel, ind_panel


# ============================================================================
# TestNeutralizeFactorPanel
# ============================================================================


class TestNeutralizeFactorPanel:
    """neutralize_factor_panel 测试"""

    def test_market_neutralization(self) -> None:
        fp, cp, ip = _make_panels()
        config = NeutralizationConfig(
            neutralize_market=True, neutralize_industry=False,
        )
        result = neutralize_factor_panel(
            fp, market_cap_panel=cp, config=config,
        )
        assert set(result.keys()) == set(fp.keys())
        # 每日标的数不变
        for d in fp:
            assert set(result[d].keys()) == set(fp[d].keys())

    def test_industry_neutralization(self) -> None:
        fp, cp, ip = _make_panels()
        config = NeutralizationConfig(
            neutralize_market=False, neutralize_industry=True,
        )
        result = neutralize_factor_panel(
            fp, industry_panel=ip, config=config,
        )
        assert len(result) == len(fp)

    def test_dual_neutralization(self) -> None:
        fp, cp, ip = _make_panels()
        result = neutralize_factor_panel(
            fp, market_cap_panel=cp, industry_panel=ip,
        )
        assert len(result) == len(fp)

    def test_reduces_market_cap_correlation(self) -> None:
        """中性化后因子与市值的相关性应降低"""
        from scipy import stats as sp_stats

        fp, cp, ip = _make_panels(n_dates=1, n_symbols=200, seed=77)
        d = list(fp.keys())[0]

        # 原始相关性
        symbols = sorted(fp[d].keys())
        orig_vals = [fp[d][s] for s in symbols]
        cap_vals = [np.log(cp[d][s]) for s in symbols]
        orig_corr, _ = sp_stats.spearmanr(orig_vals, cap_vals)

        # 中性化
        config = NeutralizationConfig(
            neutralize_market=True, neutralize_industry=False,
        )
        result = neutralize_factor_panel(
            fp, market_cap_panel=cp, config=config,
        )
        neut_vals = [result[d][s] for s in symbols]
        neut_corr, _ = sp_stats.spearmanr(neut_vals, cap_vals)

        assert abs(neut_corr) < abs(orig_corr), (
            f"中性化后相关性 {abs(neut_corr):.3f} 应低于原始 {abs(orig_corr):.3f}"
        )

    def test_missing_cap_raises(self) -> None:
        fp, _, _ = _make_panels()
        config = NeutralizationConfig(
            neutralize_market=True, neutralize_industry=False,
        )
        with pytest.raises(ValueError, match="market_cap_panel"):
            neutralize_factor_panel(fp, config=config)

    def test_missing_industry_raises(self) -> None:
        fp, _, _ = _make_panels()
        config = NeutralizationConfig(
            neutralize_market=False, neutralize_industry=True,
        )
        with pytest.raises(ValueError, match="industry_panel"):
            neutralize_factor_panel(fp, config=config)

    def test_min_samples_skip(self) -> None:
        """标的数不足时保留原值"""
        fp, cp, _ = _make_panels(n_symbols=5)
        config = NeutralizationConfig(
            neutralize_market=True,
            neutralize_industry=False,
            min_samples=10,
        )
        result = neutralize_factor_panel(
            fp, market_cap_panel=cp, config=config,
        )
        # 标的数 < 10，应保留原值
        for d in fp:
            for s in fp[d]:
                assert result[d][s] == fp[d][s]

    def test_empty_panel(self) -> None:
        config = NeutralizationConfig(
            neutralize_market=False, neutralize_industry=False,
        )
        result = neutralize_factor_panel(
            {}, market_cap_panel={}, industry_panel={}, config=config,
        )
        assert result == {}

    def test_preserves_symbols(self) -> None:
        fp, cp, ip = _make_panels()
        result = neutralize_factor_panel(
            fp, market_cap_panel=cp, industry_panel=ip,
        )
        for d in fp:
            assert set(result[d].keys()) == set(fp[d].keys())


# ============================================================================
# TestNeutralizeMultiFactors
# ============================================================================


class TestNeutralizeMultiFactors:
    """neutralize_multi_factors 测试"""

    def test_basic(self) -> None:
        fp1, cp, ip = _make_panels(seed=1)
        fp2, _, _ = _make_panels(seed=2)
        results = neutralize_multi_factors(
            {"f1": fp1, "f2": fp2},
            market_cap_panel=cp,
            industry_panel=ip,
        )
        assert "f1" in results
        assert "f2" in results
        assert len(results["f1"]) == len(fp1)
        assert len(results["f2"]) == len(fp2)

    def test_empty_input(self) -> None:
        results = neutralize_multi_factors(
            {},
            market_cap_panel={},
            industry_panel={},
        )
        assert results == {}

    def test_config_passed_through(self) -> None:
        fp, cp, _ = _make_panels(n_symbols=5)
        config = NeutralizationConfig(
            neutralize_market=True,
            neutralize_industry=False,
            min_samples=10,
        )
        results = neutralize_multi_factors(
            {"f": fp}, market_cap_panel=cp, config=config,
        )
        # min_samples=10, 只有 5 个标的 → 保留原值
        for d in fp:
            for s in fp[d]:
                assert results["f"][d][s] == fp[d][s]


# ============================================================================
# TestNeutralizationConfig
# ============================================================================


class TestNeutralizationConfig:
    """NeutralizationConfig 数据结构"""

    def test_defaults(self) -> None:
        cfg = NeutralizationConfig()
        assert cfg.neutralize_market is True
        assert cfg.neutralize_industry is True
        assert cfg.min_samples == 10

    def test_custom(self) -> None:
        cfg = NeutralizationConfig(
            neutralize_market=False,
            neutralize_industry=True,
            min_samples=20,
        )
        assert cfg.neutralize_market is False
        assert cfg.min_samples == 20
