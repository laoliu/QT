"""MLPredictStrategy 单元测试

覆盖：初始化、配置、特征构建、线性回退打分、ML模型打分、
缺失数据、空候选、缩尾处理、动量/波动率衍生特征、阈值过滤、
策略注册表等。
"""

from __future__ import annotations

import math
import pickle
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.ml_predict import (
    MLPredictStrategy,
    MLStrategyConfig,
    _DEFAULT_FALLBACK_WEIGHTS,
    _DEFAULT_META_FEATURES,
    _DERIVED_FEATURES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    symbol: str = "000001.SZ",
    close: float = 10.0,
    **meta_overrides: Any,
) -> EquityCandidateInfo:
    """构造候选标的。"""
    default_meta: dict[str, Any] = {
        "pe_ttm": 15.0,
        "pb": 1.5,
        "roe": 12.0,
        "circ_mv": 500_000.0,
        "turnover_rate": 3.0,
        "dt_netprofit_yoy": 20.0,
        "amount": 50000.0,
        "name": "平安银行",
        "list_date": "19910403",
    }
    default_meta.update(meta_overrides)
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        metadata=default_meta,
        circ_mv=default_meta.get("circ_mv", 0.0),
        turnover_rate=default_meta.get("turnover_rate", 0.0),
        amount=default_meta.get("amount", 0.0),
        list_date=str(default_meta.get("list_date", "")),
        name=default_meta.get("name", ""),
    )


def _make_context(nav: float = 1_000_000.0) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2025, 6, 1),
        positions={},
        cash=nav,
        nav=nav,
    )


def _make_candidates_batch(n: int = 5) -> list[EquityCandidateInfo]:
    """创建 n 个差异化候选标的。"""
    candidates = []
    for i in range(n):
        sym = f"{600000 + i}.SH"
        candidates.append(
            _make_candidate(
                symbol=sym,
                close=10.0 + i,
                pe_ttm=10.0 + i * 2,
                pb=1.0 + i * 0.3,
                roe=15.0 - i,
                circ_mv=100_000 + i * 50_000,
                turnover_rate=2.0 + i * 0.5,
                dt_netprofit_yoy=25.0 - i * 5,
            )
        )
    return candidates


class _SimpleModel:
    """模块级简单模型（可 pickle 序列化）。"""
    def predict(self, X):
        if isinstance(X, pd.DataFrame):
            return X.mean(axis=1).values
        return np.mean(X, axis=1)


class _BadModel:
    """模块级异常模型（可 pickle 序列化）。"""
    def predict(self, X):
        raise RuntimeError("Prediction failed")


def _save_mock_model(path: str, feature_names: list[str]) -> None:
    """保存一个简单线性模型用于测试。"""
    model_data = {
        "model": _SimpleModel(),
        "feature_names": feature_names,
    }
    with open(path, "wb") as f:
        pickle.dump(model_data, f)


# ---------------------------------------------------------------------------
# 初始化 & 配置测试
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_default_config(self):
        s = MLPredictStrategy()
        assert s.name == "ml_predict"
        assert s.config.model_path == ""
        assert not s._model_loaded

    def test_custom_config(self):
        cfg = MLStrategyConfig(
            top_k=20,
            rebalance_days=5,
            model_path="nonexistent.pkl",
            legacy_ml_compat=True,
            feature_columns=["pe_ttm", "pb"],
        )
        s = MLPredictStrategy(config=cfg)
        assert s.config.top_k == 20
        assert s.config.feature_columns == ["pe_ttm", "pb"]
        assert not s._model_loaded  # 文件不存在

    def test_kwargs_config(self):
        s = MLPredictStrategy(top_k=15, rebalance_days=3)
        assert s.config.top_k == 15
        assert s.config.rebalance_days == 3

    def test_default_feature_columns(self):
        cfg = MLStrategyConfig()
        assert cfg.feature_columns == list(_DEFAULT_META_FEATURES)

    def test_default_fallback_weights(self):
        cfg = MLStrategyConfig()
        assert "pe_ttm" in cfg.fallback_weights
        assert cfg.fallback_weights["pe_ttm"] == -1.0

    def test_winsorize_defaults(self):
        cfg = MLStrategyConfig()
        assert cfg.winsorize is True
        assert cfg.winsorize_lower == 0.02
        assert cfg.winsorize_upper == 0.98


# ---------------------------------------------------------------------------
# 特征构建测试
# ---------------------------------------------------------------------------

class TestFeatureBuilding:
    def test_basic_features(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm", "pb", "roe"])
        candidates = _make_candidates_batch(3)
        df = s._build_features(candidates)
        assert list(df.columns) == ["pe_ttm", "pb", "roe"]
        assert len(df) == 3
        assert df.index[0] == "600000.SH"

    def test_missing_metadata_filled_with_median(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"], winsorize=False)
        c1 = _make_candidate("A", pe_ttm=10.0)
        c2 = _make_candidate("B", pe_ttm=20.0)
        c3 = _make_candidate("C", pe_ttm=None)  # type: ignore
        # metadata 中 pe_ttm=None → NaN → filled with median(10, 20)=15
        df = s._build_features([c1, c2, c3])
        assert pytest.approx(df.loc["C", "pe_ttm"]) == 15.0

    def test_nan_values_filled(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"], winsorize=False)
        c1 = _make_candidate("A", pe_ttm=float("nan"))
        c2 = _make_candidate("B", pe_ttm=10.0)
        c3 = _make_candidate("C", pe_ttm=20.0)
        df = s._build_features([c1, c2, c3])
        assert pytest.approx(df.loc["A", "pe_ttm"]) == 15.0

    def test_all_nan_filled_with_zero(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"], winsorize=False)
        c1 = _make_candidate("A", pe_ttm=None)  # type: ignore
        c2 = _make_candidate("B", pe_ttm=None)  # type: ignore
        df = s._build_features([c1, c2])
        # all NaN → median is NaN → fallback to 0
        assert df.loc["A", "pe_ttm"] == 0.0

    def test_winsorize(self):
        s = MLPredictStrategy(
            feature_columns=["pe_ttm"],
            winsorize=True,
            winsorize_lower=0.1,
            winsorize_upper=0.9,
        )
        # 10 个候选，PE从 1 到 100
        raw_values = [float(1 + i * 11) for i in range(10)]
        candidates = [
            _make_candidate(f"S{i}", pe_ttm=v)
            for i, v in enumerate(raw_values)
        ]
        df = s._build_features(candidates)
        # winsorize 后：极端值被 clip 到原始分布的 [10%, 90%] 分位
        raw_series = pd.Series(raw_values)
        lo = raw_series.quantile(0.1)
        hi = raw_series.quantile(0.9)
        assert df["pe_ttm"].min() >= lo - 1e-9
        assert df["pe_ttm"].max() <= hi + 1e-9

    def test_no_winsorize(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"], winsorize=False)
        c1 = _make_candidate("A", pe_ttm=1.0)
        c2 = _make_candidate("B", pe_ttm=100.0)
        df = s._build_features([c1, c2])
        assert df.loc["A", "pe_ttm"] == 1.0
        assert df.loc["B", "pe_ttm"] == 100.0


# ---------------------------------------------------------------------------
# 衍生特征测试
# ---------------------------------------------------------------------------

class TestDerivedFeatures:
    def test_momentum_calculation(self):
        s = MLPredictStrategy(
            feature_columns=["momentum_20d"],
            momentum_lookback=5,
            winsorize=False,
        )
        # 注入价格历史
        s._price_history["A"] = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
        c = _make_candidate("A")
        df = s._build_features([c])
        # lookback=5: old=hist[-6]=11.0, new=hist[-1]=16.0, mom=(16-11)/11
        expected = (16.0 - 11.0) / 11.0
        assert pytest.approx(df.loc["A", "momentum_20d"], abs=1e-6) == expected

    def test_momentum_insufficient_history(self):
        s = MLPredictStrategy(
            feature_columns=["momentum_20d"],
            momentum_lookback=20,
            winsorize=False,
        )
        s._price_history["A"] = [10.0, 11.0, 12.0]  # 只有3个点，需要21个
        c1 = _make_candidate("A")
        c2 = _make_candidate("B", pe_ttm=10.0)
        s._price_history["B"] = list(range(1, 25))  # 24个点，够
        df = s._build_features([c1, c2])
        # A 的 momentum 是 NaN → 用 B 的 momentum 填充（中位数）
        assert not math.isnan(df.loc["A", "momentum_20d"])

    def test_volatility_calculation(self):
        s = MLPredictStrategy(
            feature_columns=["volatility_20d"],
            volatility_lookback=3,
            winsorize=False,
        )
        # 4 个价格 → 3 个收益率
        s._price_history["A"] = [10.0, 11.0, 10.5, 11.5]
        c = _make_candidate("A")
        df = s._build_features([c])
        # rets: (11-10)/10=0.1, (10.5-11)/11≈-0.0455, (11.5-10.5)/10.5≈0.0952
        # std with ddof=1
        rets = [0.1, (10.5 - 11) / 11, (11.5 - 10.5) / 10.5]
        expected = float(np.std(rets, ddof=1))
        assert pytest.approx(df.loc["A", "volatility_20d"], abs=1e-6) == expected

    def test_volatility_no_history(self):
        s = MLPredictStrategy(
            feature_columns=["volatility_20d"],
            volatility_lookback=20,
            winsorize=False,
        )
        # 无价格历史
        c1 = _make_candidate("A")
        c2 = _make_candidate("B")
        df = s._build_features([c1, c2])
        # 两个都 NaN → median=NaN → fallback 0
        assert df.loc["A", "volatility_20d"] == 0.0


# ---------------------------------------------------------------------------
# 线性回退打分测试
# ---------------------------------------------------------------------------

class TestLinearFallback:
    def test_basic_scoring(self):
        s = MLPredictStrategy(
            feature_columns=["pe_ttm", "roe"],
            fallback_weights={"pe_ttm": -1.0, "roe": 1.0},
        )
        candidates = _make_candidates_batch(5)
        ctx = _make_context()
        # 模拟至少一个调仓周期
        result = s._score_candidates(candidates, ctx)
        assert isinstance(result, list)
        assert len(result) == 5  # 所有候选都被排名

    def test_ranking_order(self):
        """验证低 PE + 高 ROE 的标的排名靠前。"""
        s = MLPredictStrategy(
            feature_columns=["pe_ttm", "roe"],
            fallback_weights={"pe_ttm": -1.0, "roe": 1.0},
            winsorize=False,
        )
        c_good = _make_candidate("GOOD", pe_ttm=5.0, roe=30.0)
        c_mid = _make_candidate("MID", pe_ttm=15.0, roe=15.0)
        c_bad = _make_candidate("BAD", pe_ttm=30.0, roe=5.0)
        ctx = _make_context()
        result = s._score_candidates([c_bad, c_good, c_mid], ctx)
        assert result[0] == "GOOD"
        assert result[-1] == "BAD"

    def test_single_candidate(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"])
        c = _make_candidate("ONLY")
        ctx = _make_context()
        result = s._score_candidates([c], ctx)
        assert result == ["ONLY"]

    def test_zero_weight_feature_ignored(self):
        s = MLPredictStrategy(
            feature_columns=["pe_ttm", "roe"],
            fallback_weights={"pe_ttm": 0.0, "roe": 1.0},
            winsorize=False,
        )
        # pe_ttm 权重 0 → 只有 roe 影响排名
        c_low_roe = _make_candidate("A", pe_ttm=1.0, roe=5.0)
        c_high_roe = _make_candidate("B", pe_ttm=100.0, roe=30.0)
        ctx = _make_context()
        result = s._score_candidates([c_low_roe, c_high_roe], ctx)
        assert result[0] == "B"  # 高 ROE 排第一


# ---------------------------------------------------------------------------
# 阈值过滤测试
# ---------------------------------------------------------------------------

class TestThresholdFilter:
    def test_no_threshold(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"])
        candidates = _make_candidates_batch(3)
        ctx = _make_context()
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_with_threshold_filters(self):
        s = MLPredictStrategy(
            feature_columns=["roe"],
            fallback_weights={"roe": 1.0},
            prediction_threshold=0.9,  # 很高阈值
            winsorize=False,
        )
        # 3 个候选，排名标准化后只有最高的接近1.0
        c1 = _make_candidate("A", roe=5.0)
        c2 = _make_candidate("B", roe=10.0)
        c3 = _make_candidate("C", roe=30.0)
        ctx = _make_context()
        result = s._score_candidates([c1, c2, c3], ctx)
        # 线性打分: rank_norm → 0, 0.5, 1.0; 乘以 w=1.0
        # 只有 score ≥ 0.9 的通过 → C(1.0)
        assert "C" in result
        assert len(result) <= 2  # A (0.0) 被过滤


# ---------------------------------------------------------------------------
# 空数据 & 边界测试
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_candidates(self):
        s = MLPredictStrategy()
        ctx = _make_context()
        assert s._score_candidates([], ctx) == []

    def test_all_same_values(self):
        """所有 PE 相同 → 排名相同，不崩溃。"""
        s = MLPredictStrategy(
            feature_columns=["pe_ttm"],
            fallback_weights={"pe_ttm": -1.0},
            winsorize=False,
        )
        candidates = [
            _make_candidate(f"S{i}", pe_ttm=15.0)
            for i in range(5)
        ]
        ctx = _make_context()
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 5

    def test_inf_values_treated_as_nan(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"], winsorize=False)
        c1 = _make_candidate("A", pe_ttm=float("inf"))
        c2 = _make_candidate("B", pe_ttm=10.0)
        df = s._build_features([c1, c2])
        # inf → NaN → filled with median(10) = 10
        assert pytest.approx(df.loc["A", "pe_ttm"]) == 10.0

    def test_string_value_treated_as_nan(self):
        s = MLPredictStrategy(feature_columns=["pe_ttm"], winsorize=False)
        c1 = _make_candidate("A", pe_ttm="invalid")
        c2 = _make_candidate("B", pe_ttm=10.0)
        df = s._build_features([c1, c2])
        assert pytest.approx(df.loc["A", "pe_ttm"]) == 10.0


# ---------------------------------------------------------------------------
# 策略注册表
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_registered(self):
        from aipha.strategy.registry import StrategyRegistry

        registry = StrategyRegistry()
        assert "ml_predict" in registry.list_strategies()

    def test_create_from_registry(self):
        from aipha.strategy.registry import StrategyRegistry

        registry = StrategyRegistry()
        s = registry.create("ml_predict")
        assert isinstance(s, MLPredictStrategy)


# ---------------------------------------------------------------------------
# 模型加载测试
# ---------------------------------------------------------------------------

class TestModelLoading:
    def test_load_valid_model(self, tmp_path):
        model_path = str(tmp_path / "test_model.pkl")
        _save_mock_model(model_path, ["pe_ttm", "pb"])

        s = MLPredictStrategy(
            model_path=model_path,
            legacy_ml_compat=True,
            feature_columns=["pe_ttm", "pb"],
        )
        assert s._model_loaded
        assert s._model_feature_names == ["pe_ttm", "pb"]

    def test_load_nonexistent_path(self):
        s = MLPredictStrategy(
            model_path="/nonexistent/model.pkl", legacy_ml_compat=True,
        )
        assert not s._model_loaded

    def test_predict_with_model(self, tmp_path):
        model_path = str(tmp_path / "model.pkl")
        _save_mock_model(model_path, ["pe_ttm", "pb"])

        s = MLPredictStrategy(
            model_path=model_path,
            legacy_ml_compat=True,
            feature_columns=["pe_ttm", "pb"],
            winsorize=False,
        )

        candidates = _make_candidates_batch(3)
        ctx = _make_context()
        result = s._score_candidates(candidates, ctx)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_model_fallback_on_predict_error(self, tmp_path):
        """模型 predict 异常时回退线性打分。"""
        model_path = str(tmp_path / "bad_model.pkl")
        model_data = {"model": _BadModel(), "feature_names": ["pe_ttm"]}
        with open(model_path, "wb") as f:
            pickle.dump(model_data, f)

        s = MLPredictStrategy(
            model_path=model_path,
            legacy_ml_compat=True,
            feature_columns=["pe_ttm"],
        )
        # 模型加载成功但 predict 会失败
        assert s._model_loaded

        candidates = _make_candidates_batch(3)
        ctx = _make_context()
        result = s._score_candidates(candidates, ctx)
        # 应该回退线性打分而不是崩溃
        assert len(result) == 3
