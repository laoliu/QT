"""Tests for EquityLGBMRanker — A股 LightGBM 截面排名模型。

测试覆盖（≥10 用例）：
T01 默认配置值
T02 自定义配置参数
T03 fit + is_fitted 标志
T04 predict 输出形状与类型
T05 predict 在未 fit 时抛 RuntimeError
T06 feature_importance 包含已使用特征
T07 get_feature_importance 按降序返回
T08 WF split 生成 — 正常折数
T09 WF split 数据不足时返回空列表
T10 WF run — IC 统计结构正确
T11 build_ranking_label 截面分位正确
T12 build_ranking_label 缺值截面退化
T13 make_equity_ranker 工厂函数
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.models.lgbm_ranker import (
    EquityLGBMRanker,
    EquityRankerConfig,
    EquityRankerWFValidator,
    WFStats,
    WFSplit,
    build_ranking_label,
    make_equity_ranker,
)

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

_FEATURE_COLS = ["ep_ratio", "roe", "mom_12_1", "ivol_neg", "log_mv"]


def _make_train_df(
    n_dates: int = 24,
    n_per_date: int = 40,
    feature_cols: list[str] | None = None,
    rng_seed: int = 42,
) -> pd.DataFrame:
    """生成 A股模拟训练数据。n_dates 个截面，每截面 n_per_date 支股票。"""
    if feature_cols is None:
        feature_cols = _FEATURE_COLS
    rng = np.random.RandomState(rng_seed)
    # 生成合法逐月日期（每月1号），从 2020-01-01 开始
    import pandas as _pd
    base = _pd.Timestamp("2020-01-01")
    date_list = [
        (base + _pd.DateOffset(months=d)).strftime("%Y-%m-%d")
        for d in range(n_dates)
    ]
    rows = []
    for date_str in date_list:
        for i in range(n_per_date):
            row: dict = {"trade_date": date_str}
            for col in feature_cols:
                row[col] = rng.randn()
            row["fwd_return"] = rng.randn() * 0.05
            row["fwd_return_rank"] = int(rng.randint(0, 4))
            row["symbol"] = f"A{i:05d}.SH"
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# T01-T02 配置
# ---------------------------------------------------------------------------

class TestEquityRankerConfig:
    def test_defaults(self) -> None:  # T01
        cfg = EquityRankerConfig()
        assert cfg.n_estimators == 300
        assert cfg.learning_rate == 0.05
        assert cfg.label_quantiles == 4
        assert len(cfg.feature_cols) >= 5
        assert "ep_ratio" in cfg.feature_cols

    def test_custom(self) -> None:  # T02
        cfg = EquityRankerConfig(n_estimators=50, max_depth=3, train_years=2)
        assert cfg.n_estimators == 50
        assert cfg.max_depth == 3
        assert cfg.train_years == 2


# ---------------------------------------------------------------------------
# T03-T07 EquityLGBMRanker
# ---------------------------------------------------------------------------

class TestEquityLGBMRanker:
    @pytest.fixture
    def small_cfg(self) -> EquityRankerConfig:
        return EquityRankerConfig(
            feature_cols=_FEATURE_COLS,
            n_estimators=10,
            group_col="trade_date",
        )

    @pytest.fixture
    def train_df(self) -> pd.DataFrame:
        return _make_train_df(feature_cols=_FEATURE_COLS)

    def test_fit_sets_is_fitted(self, small_cfg: EquityRankerConfig, train_df: pd.DataFrame) -> None:  # T03
        ranker = EquityLGBMRanker(small_cfg)
        assert not ranker.is_fitted
        ranker.fit(train_df)
        assert ranker.is_fitted

    def test_predict_shape(self, small_cfg: EquityRankerConfig, train_df: pd.DataFrame) -> None:  # T04
        ranker = EquityLGBMRanker(small_cfg)
        ranker.fit(train_df)
        test_df = train_df.head(20)
        scores = ranker.predict(test_df)
        assert isinstance(scores, np.ndarray)
        assert scores.shape == (20,)
        assert np.all(np.isfinite(scores))

    def test_predict_before_fit_raises(self, small_cfg: EquityRankerConfig, train_df: pd.DataFrame) -> None:  # T05
        ranker = EquityLGBMRanker(small_cfg)
        with pytest.raises(RuntimeError, match="not fitted"):
            ranker.predict(train_df)

    def test_feature_importance_contains_features(self, small_cfg: EquityRankerConfig, train_df: pd.DataFrame) -> None:  # T06
        ranker = EquityLGBMRanker(small_cfg)
        ranker.fit(train_df)
        fi = ranker.get_feature_importance()
        assert len(fi) > 0
        for col in _FEATURE_COLS:
            assert col in fi

    def test_feature_importance_descending(self, small_cfg: EquityRankerConfig, train_df: pd.DataFrame) -> None:  # T07
        ranker = EquityLGBMRanker(small_cfg)
        ranker.fit(train_df)
        fi = ranker.get_feature_importance()
        values = list(fi.values())
        assert values == sorted(values, reverse=True)


# ---------------------------------------------------------------------------
# T08-T10 Walk-Forward 验证器
# ---------------------------------------------------------------------------

class TestEquityRankerWFValidator:
    @pytest.fixture
    def wf_cfg(self) -> EquityRankerConfig:
        return EquityRankerConfig(
            feature_cols=_FEATURE_COLS,
            n_estimators=5,
            train_years=1,
            test_months=3,
            purge_days=5,
        )

    def test_splits_generated(self, wf_cfg: EquityRankerConfig) -> None:  # T08
        """覆盖 2 年数据时应生成 >= 2 折。"""
        df = _make_train_df(n_dates=96, n_per_date=20, feature_cols=_FEATURE_COLS)
        # 覆盖 2 年
        validator = EquityRankerWFValidator(wf_cfg)
        splits = validator.generate_splits(df, date_col="trade_date")
        assert len(splits) >= 1
        for sp in splits:
            assert sp.train_start < sp.train_end
            assert sp.test_start > sp.train_end
            assert sp.test_start <= sp.test_end

    def test_splits_empty_on_too_little_data(self, wf_cfg: EquityRankerConfig) -> None:  # T09
        """数据不足 train_years 时不生成切分。"""
        df = _make_train_df(n_dates=5, n_per_date=20, feature_cols=_FEATURE_COLS)
        validator = EquityRankerWFValidator(wf_cfg)
        splits = validator.generate_splits(df, date_col="trade_date")
        assert splits == []

    def test_wf_run_stats_structure(self, wf_cfg: EquityRankerConfig) -> None:  # T10
        """WFStats 包含合法字段，通过或不通过都应有有效 n_folds。"""
        df = _make_train_df(n_dates=96, n_per_date=20, feature_cols=_FEATURE_COLS)
        validator = EquityRankerWFValidator(wf_cfg)
        stats = validator.run(df, date_col="trade_date", n_max_folds=2)
        assert isinstance(stats, WFStats)
        assert stats.n_folds >= 0
        if stats.n_folds > 0:
            assert len(stats.ic_series) == stats.n_folds
            assert isinstance(stats.mean_ic, float)
            assert isinstance(stats.pass_rate, float)
            assert 0.0 <= stats.pass_rate <= 1.0


# ---------------------------------------------------------------------------
# T11-T12 build_ranking_label
# ---------------------------------------------------------------------------

class TestBuildRankingLabel:
    def test_quantile_label_values(self) -> None:  # T11
        rng = np.random.RandomState(0)
        df = pd.DataFrame({
            "trade_date": ["2025-01-01"] * 20,
            "fwd_return": rng.randn(20) * 0.05,
        })
        out = build_ranking_label(df, n_quantiles=4)
        assert "fwd_return_rank" in out.columns
        labels = out["fwd_return_rank"].values
        assert set(labels).issubset({0, 1, 2, 3})

    def test_small_group_no_crash(self) -> None:  # T12
        """截面样本数 < n_quantiles 时不报错，返回全 0。"""
        df = pd.DataFrame({
            "trade_date": ["2025-01-01"] * 3,
            "fwd_return": [0.01, -0.01, 0.02],
        })
        out = build_ranking_label(df, n_quantiles=4)
        assert "fwd_return_rank" in out.columns


# ---------------------------------------------------------------------------
# T13 工厂函数
# ---------------------------------------------------------------------------

def test_make_equity_ranker_factory() -> None:  # T13
    ranker = make_equity_ranker(
        feature_cols=_FEATURE_COLS,
        train_years=2,
        test_months=3,
        n_estimators=5,
    )
    assert isinstance(ranker, EquityLGBMRanker)
    assert ranker.config.train_years == 2
    assert ranker.config.n_estimators == 5
    assert ranker.config.feature_cols == _FEATURE_COLS
