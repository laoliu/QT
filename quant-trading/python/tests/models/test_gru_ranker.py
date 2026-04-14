"""Tests for GRU ranker model components."""

import numpy as np
import pandas as pd
import pytest
import torch

from aipha.models.gru_ranker import (
    GRUConfig,
    GRUNetwork,
    TemporalStockDataset,
    _GRUWrapper,
    build_feature_store,
    train_gru_one_fold,
)


@pytest.fixture
def gru_cfg():
    return GRUConfig(
        hidden_dim=32, n_layers=1, dropout=0.0,
        seq_len=5, lr=1e-3, epochs=3, patience=2,
        batch_size=64, weight_decay=0.0,
    )


@pytest.fixture
def panel_df():
    """Small panel: 3 symbols × 30 trading days."""
    np.random.seed(42)
    dates = pd.bdate_range("2026-01-01", periods=30)
    symbols = ["A.SZ", "B.SZ", "C.SZ"]
    rows = []
    for s in symbols:
        for d in dates:
            rows.append({
                "datetime": d, "symbol": s,
                "f1": np.random.randn(),
                "f2": np.random.randn(),
                "f3": np.random.randn(),
                "fwd_return": np.random.randn() * 0.02,
                "fwd_return_rank": np.random.rand(),
            })
    return pd.DataFrame(rows)


# ── GRUNetwork ──────────────────────────────────────
class TestGRUNetwork:
    def test_forward_shape(self, gru_cfg):
        net = GRUNetwork(n_features=10, cfg=gru_cfg)
        x = torch.randn(8, 5, 10)  # (batch=8, seq=5, feat=10)
        out = net(x)
        assert out.shape == (8, 1)

    def test_output_finite(self, gru_cfg):
        net = GRUNetwork(n_features=5, cfg=gru_cfg)
        x = torch.randn(4, 5, 5)
        out = net(x)
        assert torch.isfinite(out).all()


# ── TemporalDataset ─────────────────────────────────
class TestTemporalDataset:
    def test_getitem(self, panel_df):
        fcols = ["f1", "f2", "f3"]
        store, didx = build_feature_store(panel_df, fcols)
        samples = [("A.SZ", panel_df["datetime"].unique()[10], 0.5)]
        ds = TemporalStockDataset(store, didx, samples, seq_len=5)
        x, y = ds[0]
        assert x.shape == (5, 3)
        assert y.shape == (1,)

    def test_padding_short_history(self, panel_df):
        fcols = ["f1", "f2", "f3"]
        store, didx = build_feature_store(panel_df, fcols)
        first_date = panel_df["datetime"].unique()[0]
        samples = [("A.SZ", first_date, 0.1)]
        ds = TemporalStockDataset(store, didx, samples, seq_len=5)
        x, _ = ds[0]
        assert x.shape == (5, 3)
        # First 4 rows should be zero-padded
        assert (x[:4] == 0).all()


# ── build_feature_store ─────────────────────────────
class TestBuildFeatureStore:
    def test_store_shapes(self, panel_df):
        fcols = ["f1", "f2", "f3"]
        store, didx = build_feature_store(panel_df, fcols)
        assert len(store) == 3  # 3 symbols
        assert store["A.SZ"].shape == (30, 3)
        assert len(didx["A.SZ"]) == 30


# ── _GRUWrapper ─────────────────────────────────────
class TestGRUWrapper:
    def test_flat_predict(self, gru_cfg):
        net = GRUNetwork(n_features=3, cfg=gru_cfg)
        device = torch.device("cpu")
        w = _GRUWrapper(net, gru_cfg, device)
        X = np.random.randn(10, 3).astype(np.float32)
        scores = w.predict(X)
        assert scores.shape == (10,)
        assert np.isfinite(scores).all()

    def test_temporal_predict(self, gru_cfg, panel_df):
        net = GRUNetwork(n_features=3, cfg=gru_cfg)
        device = torch.device("cpu")
        w = _GRUWrapper(net, gru_cfg, device)
        # Use last 5 days as OOS
        dates = sorted(panel_df["datetime"].unique())
        oos = panel_df[panel_df["datetime"].isin(dates[-5:])]
        scores = w.predict_temporal(oos, ["f1", "f2", "f3"], panel_df)
        assert len(scores) == len(oos)
        assert np.isfinite(scores).all()


# ── train_gru_one_fold integration ──────────────────
class TestTrainGRUOneFold:
    def test_train_and_predict(self, gru_cfg, panel_df):
        """End-to-end: train on small panel, then predict."""
        fcols = ["f1", "f2", "f3"]
        wrapper = train_gru_one_fold(
            train_df=panel_df,
            feature_cols=fcols,
            label_col="fwd_return_rank",
            cfg=gru_cfg,
            full_df=panel_df,
            fold_idx=0,
        )
        assert isinstance(wrapper, _GRUWrapper)
        # Flat predict
        X = panel_df[fcols].fillna(0.0).values[:5].astype(np.float32)
        scores = wrapper.predict(X)
        assert scores.shape == (5,)
        # Temporal predict
        dates = sorted(panel_df["datetime"].unique())
        oos = panel_df[panel_df["datetime"].isin(dates[-3:])]
        t_scores = wrapper.predict_temporal(oos, fcols, panel_df)
        assert len(t_scores) == len(oos)
