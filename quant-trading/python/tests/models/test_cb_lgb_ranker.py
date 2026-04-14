"""Tests for CBLGBRanker — CB LightGBM 排名模型。

测试覆盖（≥8 用例）：
- Config: 默认值、自定义
- Ranker: fit/predict、特征重要性、save/load
- Edge cases: 空数据、未 fit 预测
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.models.cb_lgb_ranker import CBLGBRanker, CBRankerConfig


def _make_train_df(n_dates: int = 20, n_per_date: int = 30) -> pd.DataFrame:
    """生成模拟训练数据。"""
    rng = np.random.RandomState(42)
    rows = []
    for d in range(n_dates):
        date_str = f"2024-{(d // 28 + 1):02d}-{(d % 28 + 1):02d}"
        for _ in range(n_per_date):
            row = {
                "trade_date": date_str,
                "close": 80 + rng.rand() * 60,
                "cb_over_rate": rng.rand() * 30,
                "cb_value": 70 + rng.rand() * 60,
                "issue_size": 3000 + rng.rand() * 7000,
                "rating_score": rng.choice([3.0, 4.0, 5.0]),
                "iv_bias": rng.rand() * 0.5,
                "gamma_convexity": rng.rand(),
                "credit_spread": -0.02 + rng.rand() * 0.08,
                "liquidity_activity": rng.rand() * 0.5,
                "maturity_convexity": rng.rand(),
                "amount": 100 + rng.rand() * 500,
            }
            row["fwd_return_rank"] = int(rng.randint(0, 5))  # 整数排名等级
            rows.append(row)
    return pd.DataFrame(rows)


class TestCBRankerConfig:
    def test_defaults(self) -> None:
        cfg = CBRankerConfig()
        assert cfg.n_estimators == 200
        assert cfg.learning_rate == 0.05
        assert len(cfg.feature_cols) >= 10

    def test_custom(self) -> None:
        cfg = CBRankerConfig(n_estimators=50, max_depth=4)
        assert cfg.n_estimators == 50
        assert cfg.max_depth == 4


class TestCBLGBRanker:
    @pytest.fixture
    def train_df(self) -> pd.DataFrame:
        return _make_train_df()

    def test_fit(self, train_df) -> None:
        ranker = CBLGBRanker(CBRankerConfig(n_estimators=10))
        ranker.fit(train_df)
        assert ranker.is_fitted

    def test_predict(self, train_df) -> None:
        ranker = CBLGBRanker(CBRankerConfig(n_estimators=10))
        ranker.fit(train_df)
        scores = ranker.predict(train_df.head(30))
        assert len(scores) == 30
        assert all(np.isfinite(scores))

    def test_feature_importance(self, train_df) -> None:
        ranker = CBLGBRanker(CBRankerConfig(n_estimators=10))
        ranker.fit(train_df)
        imp = ranker.get_feature_importance()
        assert len(imp) > 0
        assert all(v >= 0 for v in imp.values())

    def test_predict_without_fit(self) -> None:
        ranker = CBLGBRanker()
        with pytest.raises(RuntimeError, match="not fitted"):
            ranker.predict(pd.DataFrame({"close": [100.0]}))

    def test_save_load(self, train_df, tmp_path) -> None:
        ranker = CBLGBRanker(CBRankerConfig(n_estimators=10))
        ranker.fit(train_df)
        model_path = tmp_path / "test_model.txt"
        ranker.save(model_path)
        assert model_path.exists()
        ranker2 = CBLGBRanker()
        ranker2.load(model_path)
        scores1 = ranker.predict(train_df.head(10))
        scores2 = ranker2.predict(train_df.head(10))
        np.testing.assert_allclose(scores1, scores2, rtol=1e-5)

    def test_missing_features(self, train_df) -> None:
        """部分特征缺失时仍能训练/预测。"""
        df = train_df.drop(columns=["gamma_convexity", "credit_spread"])
        ranker = CBLGBRanker(CBRankerConfig(n_estimators=10))
        ranker.fit(df)
        scores = ranker.predict(df.head(10))
        assert len(scores) == 10
