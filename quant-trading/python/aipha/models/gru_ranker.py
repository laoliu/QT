"""GRU-based stock ranking model for temporal pattern capture.

Implements a GRU network that processes time-series feature sequences
to predict stock scores. Designed as a diversifier to tree-based models
(LightGBM / DoubleEnsemble) by exploiting temporal dynamics.

Architecture:
    Input: (batch, seq_len, n_features)
    → GRU layers (2-layer, bidirectional optional)
    → LayerNorm → Dropout → FC → score
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────
@dataclass
class GRUConfig:
    hidden_dim: int = 128
    n_layers: int = 2
    dropout: float = 0.2
    seq_len: int = 20
    lr: float = 1e-3
    epochs: int = 80
    patience: int = 10
    batch_size: int = 2048
    weight_decay: float = 1e-4
    bidirectional: bool = False


# ── Dataset ─────────────────────────────────────────
class TemporalStockDataset(Dataset):
    """Lazy-loading dataset that builds (seq_len, F) sequences on the fly."""

    def __init__(
        self,
        feature_arrays: dict[str, np.ndarray],
        date_indices: dict[str, dict[Any, int]],
        samples: list[tuple[str, Any, float]],
        seq_len: int,
    ):
        self.features = feature_arrays
        self.date_idx = date_indices
        self.samples = samples
        self.seq_len = seq_len

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        sym, date_key, label = self.samples[idx]
        arr = self.features[sym]  # (T_sym, F)
        i = self.date_idx[sym][date_key]
        start = max(0, i - self.seq_len + 1)
        seq = arr[start : i + 1]  # up to (seq_len, F)
        n_feat = arr.shape[1]
        if len(seq) < self.seq_len:
            pad = np.zeros((self.seq_len - len(seq), n_feat), dtype=np.float32)
            seq = np.concatenate([pad, seq], axis=0)
        return (
            torch.from_numpy(seq.astype(np.float32)),
            torch.tensor([label], dtype=torch.float32),
        )


# ── Network ─────────────────────────────────────────
class GRUNetwork(nn.Module):
    """GRU encoder + FC head for stock score prediction."""

    def __init__(self, n_features: int, cfg: GRUConfig):
        super().__init__()
        self.cfg = cfg
        self.input_norm = nn.LayerNorm(n_features)
        self.gru = nn.GRU(
            input_size=n_features,
            hidden_size=cfg.hidden_dim,
            num_layers=cfg.n_layers,
            batch_first=True,
            dropout=cfg.dropout if cfg.n_layers > 1 else 0.0,
            bidirectional=cfg.bidirectional,
        )
        fc_in = cfg.hidden_dim * (2 if cfg.bidirectional else 1)
        self.head = nn.Sequential(
            nn.LayerNorm(fc_in),
            nn.Dropout(cfg.dropout),
            nn.Linear(fc_in, fc_in // 2),
            nn.GELU(),
            nn.Dropout(cfg.dropout * 0.5),
            nn.Linear(fc_in // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, seq_len, n_features) → (batch, 1)."""
        x = self.input_norm(x)
        out, _ = self.gru(x)  # (batch, seq_len, hidden)
        last = out[:, -1, :]  # take last time step
        return self.head(last)


# ── Wrapper ─────────────────────────────────────────
class _GRUWrapper:
    """Wraps GRUNetwork to provide flat predict(X) interface."""

    def __init__(self, model: GRUNetwork, cfg: GRUConfig, device: torch.device):
        self.model = model
        self.cfg = cfg
        self.device = device
        self._feature_names: list[str] = []
        self._temporal_store: dict[str, np.ndarray] | None = None
        self._date_indices: dict[str, dict] | None = None

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Flat 2D predict: treat each row as length-1 sequence."""
        X_3d = X.reshape(X.shape[0], 1, X.shape[1]).astype(np.float32)
        return self._predict_3d(X_3d)

    def predict_temporal(
        self, oos_df: pd.DataFrame, feature_cols: list[str], full_df: pd.DataFrame,
    ) -> np.ndarray:
        """Build temporal sequences from full_df, predict for oos_df rows."""
        store, date_idx = build_feature_store(full_df, feature_cols)
        return self.predict_temporal_fast(oos_df, feature_cols, (store, date_idx))

    def predict_temporal_fast(
        self,
        oos_df: pd.DataFrame,
        feature_cols: list[str],
        prebuilt_store: tuple[dict[str, np.ndarray], dict[str, dict]],
    ) -> np.ndarray:
        """Predict using a pre-built feature store (avoids rebuild)."""
        store, date_idx = prebuilt_store
        samples = []
        for _, row in oos_df.iterrows():
            sym = row["symbol"]
            dt = row["datetime"]
            if sym in date_idx and dt in date_idx[sym]:
                samples.append((sym, dt, 0.0))
            else:
                samples.append(None)
        valid_samples = [s for s in samples if s is not None]
        if not valid_samples:
            return np.zeros(len(oos_df), dtype=np.float32)
        ds = TemporalStockDataset(store, date_idx, valid_samples, self.cfg.seq_len)
        loader = DataLoader(ds, batch_size=self.cfg.batch_size * 2, shuffle=False)
        scores = self._predict_loader(loader)
        result = np.zeros(len(samples), dtype=np.float32)
        j = 0
        for i, s in enumerate(samples):
            if s is not None:
                result[i] = scores[j]
                j += 1
        return result

    def _predict_3d(self, X_3d: np.ndarray) -> np.ndarray:
        self.model.eval()
        with torch.no_grad():
            t = torch.from_numpy(X_3d).to(self.device)
            return self.model(t).cpu().numpy().ravel()

    def _predict_loader(self, loader: DataLoader) -> np.ndarray:
        self.model.eval()
        parts: list[np.ndarray] = []
        with torch.no_grad():
            for X_batch, _ in loader:
                out = self.model(X_batch.to(self.device)).cpu().numpy().ravel()
                parts.append(out)
        return np.concatenate(parts) if parts else np.array([], dtype=np.float32)


# ── Training ────────────────────────────────────────
def build_feature_store(
    df: pd.DataFrame, feature_cols: list[str],
) -> tuple[dict[str, np.ndarray], dict[str, dict]]:
    """Build per-symbol feature arrays and date→index maps.

    Returns:
        feature_arrays: {symbol: np.ndarray(T, F)}
        date_indices:   {symbol: {datetime_value: int}}
    """
    feature_arrays: dict[str, np.ndarray] = {}
    date_indices: dict[str, dict] = {}
    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("datetime")
        arr = grp[feature_cols].fillna(0.0).values.astype(np.float32)
        feature_arrays[sym] = arr
        date_indices[sym] = {dt: i for i, dt in enumerate(grp["datetime"].values)}
    return feature_arrays, date_indices


def train_gru_one_fold(
    train_df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    cfg: GRUConfig,
    full_df: pd.DataFrame | None = None,
    fold_idx: int = 0,
    prebuilt_store: tuple | None = None,
) -> _GRUWrapper:
    """Train GRU model for one walk-forward fold."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("GRU fold %d — device=%s, seq_len=%d", fold_idx, device, cfg.seq_len)

    # Use prebuilt store if provided, else build from full_df
    if prebuilt_store is not None:
        store, date_idx = prebuilt_store
    else:
        src_df = full_df if full_df is not None else train_df
        store, date_idx = build_feature_store(src_df, feature_cols)

    # Samples for training period
    train_samples = []
    for _, row in train_df.iterrows():
        sym, dt = row["symbol"], row["datetime"]
        if sym in date_idx and dt in date_idx[sym]:
            train_samples.append((sym, dt, float(row[label_col])))
    if not train_samples:
        raise ValueError("No valid training samples for GRU fold")

    # Train-val split: last 15% chronological as val
    n_val = max(1, int(len(train_samples) * 0.15))
    val_samples = train_samples[-n_val:]
    trn_samples = train_samples[:-n_val]
    logger.info("  train=%d, val=%d samples", len(trn_samples), len(val_samples))

    trn_ds = TemporalStockDataset(store, date_idx, trn_samples, cfg.seq_len)
    val_ds = TemporalStockDataset(store, date_idx, val_samples, cfg.seq_len)
    trn_loader = DataLoader(trn_ds, batch_size=cfg.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=cfg.batch_size * 2, shuffle=False)

    n_features = len(feature_cols)
    model = GRUNetwork(n_features, cfg).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.epochs, eta_min=cfg.lr * 0.01,
    )

    best_val, best_state, patience_cnt = float("inf"), None, 0
    for epoch in range(cfg.epochs):
        model.train()
        losses = []
        for X_b, y_b in trn_loader:
            X_b, y_b = X_b.to(device), y_b.to(device)
            pred = model(X_b)
            loss = F.mse_loss(pred, y_b)
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(loss.item())
        scheduler.step()

        # Validation
        model.eval()
        val_losses = []
        with torch.no_grad():
            for X_b, y_b in val_loader:
                pred = model(X_b.to(device))
                val_losses.append(F.mse_loss(pred, y_b.to(device)).item())
        val_loss = float(np.mean(val_losses)) if val_losses else float("inf")

        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
        if epoch % 10 == 0 or patience_cnt == 0:
            logger.info(
                "  epoch %d: trn=%.5f val=%.5f best=%.5f pat=%d",
                epoch, np.mean(losses), val_loss, best_val, patience_cnt,
            )
        if patience_cnt >= cfg.patience:
            logger.info("  early stop at epoch %d", epoch)
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.to(device)

    wrapper = _GRUWrapper(model, cfg, device)
    wrapper._feature_names = list(feature_cols)
    return wrapper
