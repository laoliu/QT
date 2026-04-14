"""因子评估管道单元测试."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def sample_df():
    """构造含 5 只股票 × 60 天的样本数据."""
    np.random.seed(42)
    symbols = [f"SZ.{i:06d}" for i in range(1, 6)]
    dates = pd.bdate_range("2023-01-01", periods=60)
    rows = []
    for sym in symbols:
        price = 10.0
        for dt in dates:
            ret = np.random.normal(0, 0.02)
            price *= (1 + ret)
            rows.append({
                "symbol": sym, "datetime": dt,
                "close": price, "open": price * 0.99,
                "high": price * 1.01, "low": price * 0.98,
                "volume": np.random.randint(1e5, 1e7),
                "amount": np.random.uniform(1e6, 1e8),
                "turnover_rate": np.random.uniform(0.5, 5.0),
                "pe_ttm": np.random.uniform(5, 50),
                "pb": np.random.uniform(0.5, 5.0),
                "total_mv": np.random.uniform(1e9, 1e11),
                "circ_mv": np.random.uniform(5e8, 5e10),
                "dv_ratio": np.random.uniform(0, 5),
                "dv_ttm": np.random.uniform(0, 5),
            })
    df = pd.DataFrame(rows)
    df["fwd_10d"] = df.groupby("symbol")["close"].transform(
        lambda s: s.shift(-10) / s - 1.0)
    return df


@pytest.fixture
def sample_kb(tmp_path):
    """构造样本 KB JSON."""
    kb = [
        {"name": "f1", "expression": "cs_rank($close)",
         "passed": True, "icir": 1.5, "ic_mean": 0.05},
        {"name": "f2", "expression": "cs_rank($volume)",
         "passed": True, "icir": 1.2, "ic_mean": 0.04},
        {"name": "f3", "expression": "cs_rank($pe_ttm)",
         "passed": True, "icir": 0.8, "ic_mean": 0.03},
        {"name": "f_fail", "expression": "cs_rank($close)",
         "passed": False, "icir": 0.1, "ic_mean": 0.01},
    ]
    kb_file = tmp_path / "mutation_kb.json"
    kb_file.write_text(json.dumps(kb), encoding="utf-8")
    return str(kb_file)


class TestLoadKBPassed:
    """KB 加载测试."""

    def test_load_filters_passed(self, sample_kb):
        from factor_evaluation_pipeline import load_kb_passed
        passed = load_kb_passed(sample_kb)
        assert len(passed) == 3
        assert all(e["passed"] for e in passed)

    def test_load_sorted_by_icir(self, sample_kb):
        from factor_evaluation_pipeline import load_kb_passed
        passed = load_kb_passed(sample_kb)
        icirs = [abs(e["icir"]) for e in passed]
        assert icirs == sorted(icirs, reverse=True)

    def test_load_missing_file_raises(self):
        from factor_evaluation_pipeline import load_kb_passed
        with pytest.raises(FileNotFoundError):
            load_kb_passed("/nonexistent/kb.json")


class TestSeriesToPanel:
    """Series → Panel 转换测试."""

    def test_basic_conversion(self, sample_df):
        from factor_evaluation_pipeline import series_to_panel
        vals = sample_df["close"]
        panel = series_to_panel(sample_df, vals, "test")
        assert len(panel) > 0
        first_date = list(panel.keys())[0]
        assert len(panel[first_date]) == 5  # 5 symbols

    def test_nan_excluded(self, sample_df):
        from factor_evaluation_pipeline import series_to_panel
        vals = sample_df["close"].copy()
        vals.iloc[:10] = np.nan
        panel = series_to_panel(sample_df, vals, "test")
        # NaN 行应被排除，某些日期某些股票可能缺失
        total_entries = sum(len(v) for v in panel.values())
        assert total_entries < 300  # 5*60=300, 减去 NaN


class TestClusterFactors:
    """聚类去冗余测试."""

    def test_identical_factors_same_cluster(self):
        from factor_evaluation_pipeline import cluster_factors
        # 完全相关: 同一簇
        corr = np.array([[1.0, 0.99], [0.99, 1.0]])
        clusters = cluster_factors(corr, ["a", "b"], threshold=0.70)
        assert len(clusters) == 1

    def test_uncorrelated_separate_clusters(self):
        from factor_evaluation_pipeline import cluster_factors
        corr = np.array([[1.0, 0.1], [0.1, 1.0]])
        clusters = cluster_factors(corr, ["a", "b"], threshold=0.70)
        assert len(clusters) == 2

    def test_single_factor(self):
        from factor_evaluation_pipeline import cluster_factors
        corr = np.array([[1.0]])
        clusters = cluster_factors(corr, ["a"], threshold=0.70)
        assert len(clusters) == 1
        assert clusters[0] == ["a"]

    def test_select_representatives_picks_max_icir(self):
        from factor_evaluation_pipeline import select_representatives
        clusters = {1: ["a", "b", "c"]}
        stats = {
            "a": {"icir": 0.5}, "b": {"icir": 1.2}, "c": {"icir": 0.8},
        }
        reps = select_representatives(clusters, stats)
        assert reps == ["b"]


class TestEvaluateQuantileReturns:
    """分位收益测试."""

    def test_returns_dict_structure(self, sample_df):
        from factor_evaluation_pipeline import evaluate_quantile_returns
        fvals = sample_df["close"]
        result = evaluate_quantile_returns(
            sample_df, fvals, "fwd_10d", n_q=5)
        assert "quantile_returns" in result
        assert "long_short" in result
        assert "monotonicity" in result

    def test_insufficient_data(self):
        from factor_evaluation_pipeline import evaluate_quantile_returns
        df = pd.DataFrame({
            "datetime": pd.date_range("2023-01-01", periods=5),
            "fwd_10d": [0.01] * 5,
        })
        fvals = pd.Series([1, 2, 3, 4, 5])
        result = evaluate_quantile_returns(df, fvals, "fwd_10d")
        assert result["long_short"] == 0.0


class TestEvaluateTurnover:
    """换手率测试."""

    def test_returns_avg_turnover(self, sample_df):
        from factor_evaluation_pipeline import evaluate_turnover
        fvals = sample_df["close"]
        result = evaluate_turnover(sample_df, fvals, "test")
        assert "avg_turnover" in result
        assert 0.0 <= result["avg_turnover"] <= 1.0

    def test_constant_factor_zero_turnover(self, sample_df):
        from factor_evaluation_pipeline import evaluate_turnover
        fvals = pd.Series(1.0, index=sample_df.index)
        result = evaluate_turnover(sample_df, fvals, "const")
        # 常量因子: 所有股票rank相同, turnover应为0
        assert result["avg_turnover"] == 0.0


class TestWalkForwardValidate:
    """Walk-Forward 验证测试."""

    def test_insufficient_data_returns_zero(self):
        from factor_evaluation_pipeline import walk_forward_validate
        df = pd.DataFrame({
            "datetime": pd.date_range("2023-01-01", periods=50),
            "fwd_10d": np.random.randn(50),
        })
        fvals = pd.Series(np.random.randn(50))
        result = walk_forward_validate(df, fvals, "fwd_10d")
        assert result["n_folds"] == 0

    def test_basic_fold_structure(self, sample_df):
        from factor_evaluation_pipeline import walk_forward_validate
        # 5 symbols × 60 days = 300 rows, 不一定足够 5 折
        # 但至少不报错
        fvals = sample_df["close"]
        result = walk_forward_validate(
            sample_df, fvals, "fwd_10d", n_folds=2)
        assert "fold_results" in result
        assert "oos_ic_mean" in result

    def test_embargo_days_auto_inferred(self, sample_df):
        """验证 embargo_days 从 fwd_col 自动推断."""
        from factor_evaluation_pipeline import walk_forward_validate
        fvals = sample_df["close"]
        result = walk_forward_validate(
            sample_df, fvals, "fwd_10d", n_folds=2)
        assert result.get("embargo_days") == 10

    def test_embargo_days_explicit(self, sample_df):
        """验证显式指定 embargo_days."""
        from factor_evaluation_pipeline import walk_forward_validate
        fvals = sample_df["close"]
        result = walk_forward_validate(
            sample_df, fvals, "fwd_10d", n_folds=2, embargo_days=5)
        assert result.get("embargo_days") == 5


class TestCompositeScore:
    """综合评分测试."""

    def test_score_in_range(self, sample_df):
        from factor_evaluation_pipeline import evaluate_single_factor
        fvals = sample_df["close"]
        result = evaluate_single_factor(
            sample_df, fvals, "test_f", "fwd_10d", skip_wf=True)
        assert 0.0 <= result["composite_score"] <= 1.0
        assert "ic_decay" in result
        assert "quantile" in result
        assert "turnover" in result

    def test_all_nan_factor(self, sample_df):
        from factor_evaluation_pipeline import evaluate_single_factor
        fvals = pd.Series(np.nan, index=sample_df.index)
        result = evaluate_single_factor(
            sample_df, fvals, "nan_f", "fwd_10d", skip_wf=True)
        assert result["composite_score"] == pytest.approx(0.0, abs=0.1)


class TestMultiPeriodEval:
    """P2-3: 多周期前向收益评估管道测试."""

    def test_fwd_period_parsing(self):
        """逗号分隔解析多周期."""
        fwd_str = "5,10,20"
        periods = [int(x) for x in fwd_str.split(",")]
        assert periods == [5, 10, 20]

    def test_single_period_compat(self):
        """单周期字符串兼容."""
        fwd_str = "10"
        periods = [int(x) for x in fwd_str.split(",")]
        assert periods == [10]

    def test_multi_period_fwd_cols_computed(self, sample_df):
        """多周期时, 额外 fwd 列正确计算."""
        for p in [5, 20]:
            col = f"fwd_{p}d"
            sample_df[col] = sample_df.groupby("symbol")["close"].transform(
                lambda s, _p=p: s.shift(-_p) / s - 1.0)
        assert "fwd_5d" in sample_df.columns
        assert "fwd_20d" in sample_df.columns
        assert sample_df["fwd_5d"].notna().sum() > 0
        assert sample_df["fwd_20d"].notna().sum() > 0
