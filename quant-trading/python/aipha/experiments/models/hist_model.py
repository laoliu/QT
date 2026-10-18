"""Experimental HIST: Graph-based Framework for Stock Trend Forecasting via Mining Concept-Oriented Shared Information.

Xu W, Liu W, Xu C, Bian J, Yin J, Liu T-Y (2021).
Qlib Alpha360 SOTA: IC=0.052, IR=1.37 on CSI300.

Architecture:
  Individual Stock Encoder (GRU) →
  Predefined Concept Attention (industry graph) →
  Residual (stock - concept_shared) →
  Hidden Concept Discovery (learned attention) →
  Prediction Head

Adapted for Aipha-Q Walk-Forward pipeline, matching MASTERWrapper interface.
"""

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class HISTConfig:
    """HIST model hyperparameters."""

    d_feat: int = 158
    hidden_size: int = 128
    num_gru_layers: int = 2
    num_concepts: int = 64  # predefined concept count (industry groups)
    num_hidden_concepts: int = 32  # hidden concept count (learned)
    dropout: float = 0.3
    seq_len: int = 20  # lookback window (days)
    lr: float = 1e-3
    epochs: int = 50
    patience: int = 8
    batch_size: int = 2048  # cross-sectional batch (per-date)
    K: int = 3  # top-K concepts per stock for attention sparsification


# ---------------------------------------------------------------------------
# Model Components
# ---------------------------------------------------------------------------


class GRUEncoder(nn.Module):
    """Per-stock temporal encoder using GRU."""

    def __init__(self, d_feat: int, hidden_size: int, num_layers: int, dropout: float):
        super().__init__()
        self.gru = nn.GRU(
            input_size=d_feat,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden_size, hidden_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (N, T, d_feat) -> (N, hidden_size)"""
        out, _ = self.gru(x)  # (N, T, hidden_size)
        # Use last hidden state
        h = out[:, -1, :]  # (N, hidden_size)
        return self.fc(h)


class PredefinedConceptModule(nn.Module):
    """Extract concept-shared information using predefined industry graph."""

    def __init__(self, hidden_size: int, num_concepts: int):
        super().__init__()
        self.fc_stock2concept = nn.Linear(hidden_size, hidden_size)
        self.fc_concept2stock = nn.Linear(hidden_size, hidden_size)

    def forward(
        self, stock_emb: torch.Tensor, concept_matrix: torch.Tensor
    ) -> torch.Tensor:
        """
        stock_emb: (N, D) individual stock embeddings
        concept_matrix: (N, C) binary/soft assignment matrix
        Returns: concept_shared (N, D) — the part shared with concept peers
        """
        # Stock → Concept aggregation: weighted average per concept
        # concept_matrix: (N, C), stock_emb: (N, D)
        # Normalize concept_matrix column-wise (each concept averages its members)
        cm_norm = concept_matrix / (concept_matrix.sum(dim=0, keepdim=True) + 1e-8)
        # Concept embeddings: (C, D) = (C, N) @ (N, D)
        concept_emb = cm_norm.T @ self.fc_stock2concept(stock_emb)
        # Concept → Stock: each stock gets its concept-weighted representation
        # (N, D) = (N, C) @ (C, D)
        concept_shared = concept_matrix @ self.fc_concept2stock(concept_emb)
        # Normalize by number of concepts per stock
        n_concepts_per_stock = concept_matrix.sum(dim=1, keepdim=True) + 1e-8
        concept_shared = concept_shared / n_concepts_per_stock
        return concept_shared


class HiddenConceptModule(nn.Module):
    """Discover hidden concepts via learned attention clustering."""

    def __init__(self, hidden_size: int, num_hidden_concepts: int):
        super().__init__()
        self.concept_embeddings = nn.Parameter(
            torch.randn(num_hidden_concepts, hidden_size)
        )
        self.fc_query = nn.Linear(hidden_size, hidden_size)
        self.fc_key = nn.Linear(hidden_size, hidden_size)

    def forward(self, residual: torch.Tensor) -> torch.Tensor:
        """
        residual: (N, D) stock residuals after removing predefined concept info
        Returns: hidden_shared (N, D) — hidden concept shared information
        """
        # Query from stock residuals, Key from learnable concept embeddings
        query = self.fc_query(residual)  # (N, D)
        key = self.fc_key(self.concept_embeddings)  # (H, D)
        # Attention: (N, H) = (N, D) @ (D, H)
        d_k = query.shape[-1] ** 0.5
        attn_scores = torch.matmul(query, key.T) / d_k  # (N, H)
        attn_weights = torch.softmax(attn_scores, dim=-1)  # (N, H)
        # Hidden concept aggregation: (N, D) = (N, H) @ (H, D)
        hidden_shared = torch.matmul(attn_weights, self.concept_embeddings)
        return hidden_shared


class HIST(nn.Module):
    """Full HIST model: GRU encoder + dual concept modules + prediction."""

    def __init__(self, config: HISTConfig):
        super().__init__()
        self.config = config
        self.encoder = GRUEncoder(
            config.d_feat, config.hidden_size, config.num_gru_layers, config.dropout,
        )
        self.predefined_module = PredefinedConceptModule(
            config.hidden_size, config.num_concepts,
        )
        self.hidden_module = HiddenConceptModule(
            config.hidden_size, config.num_hidden_concepts,
        )
        self.predictor = nn.Sequential(
            nn.Linear(config.hidden_size * 3, config.hidden_size),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.hidden_size, 1),
        )

    def forward(
        self, x: torch.Tensor, concept_matrix: torch.Tensor
    ) -> torch.Tensor:
        """
        x: (N, T, d_feat) raw price-volume features
        concept_matrix: (N, C) industry/concept assignment
        Returns: (N,) prediction scores
        """
        # Step 1: Encode individual stocks
        stock_emb = self.encoder(x)  # (N, D)

        # Step 2: Extract predefined concept-shared information
        concept_shared = self.predefined_module(stock_emb, concept_matrix)  # (N, D)

        # Step 3: Compute residual (stock-specific info)
        residual = stock_emb - concept_shared  # (N, D)

        # Step 4: Discover hidden concept information from residuals
        hidden_shared = self.hidden_module(residual)  # (N, D)

        # Step 5: Concatenate all three streams and predict
        combined = torch.cat([stock_emb, concept_shared, hidden_shared], dim=-1)
        output = self.predictor(combined).squeeze(-1)  # (N,)
        return output


# ---------------------------------------------------------------------------
# Wrapper for MLStockSelector integration
# ---------------------------------------------------------------------------


class _HISTWrapper:
    """适配 MLStockSelector WF 管道的 HIST wrapper。

    Interface matches _MASTERWrapper:
    - predict(X: np.ndarray) -> np.ndarray  (flat 2D fallback)
    - predict_temporal_fast(oos_df, feature_cols, prebuilt_store) -> np.ndarray
    """

    def __init__(self, model: HIST, cfg: HISTConfig, device: torch.device,
                 concept_builder: Any = None):
        self.model = model
        self.cfg = cfg
        self.device = device
        self.concept_builder = concept_builder
        self._feature_names: list[str] = []

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Flat 2D predict: single-timestep fallback (no concept graph)."""
        self.model.eval()
        n_feat = X.shape[1]
        # (N, 1, d_feat) - single timestep
        X_3d = X.reshape(X.shape[0], 1, n_feat).astype(np.float32)
        # Use identity concept matrix (each stock = own concept)
        N = X.shape[0]
        concept_mat = torch.eye(N, device=self.device)
        with torch.no_grad():
            t = torch.from_numpy(X_3d).to(self.device)
            pred = self.model(t, concept_mat).detach().cpu().numpy()
        return pred

    def predict_temporal_fast(
        self,
        oos_df: pd.DataFrame,
        feature_cols: list[str],
        prebuilt_store: tuple[dict[str, np.ndarray], dict[str, dict]],
    ) -> np.ndarray:
        """Per-date temporal prediction with concept graph."""
        store, date_idx = prebuilt_store
        seq_len = self.cfg.seq_len
        n_feat = len(feature_cols)
        self.model.eval()

        scores = np.zeros(len(oos_df), dtype=np.float32)

        # Group by date for correct cross-sectional concept attention
        date_to_rows: dict[str, list[tuple[int, str]]] = {}
        for i, (_, row) in enumerate(oos_df.iterrows()):
            sym = row["symbol"]
            dt = row["datetime"]
            if sym in date_idx and dt in date_idx[sym]:
                date_to_rows.setdefault(dt, []).append((i, sym))

        with torch.no_grad():
            for dt, items in date_to_rows.items():
                seqs: list[np.ndarray] = []
                indices: list[int] = []
                syms_in_batch: list[str] = []

                for row_idx, sym in items:
                    t_idx = date_idx[sym][dt]
                    start = max(0, t_idx - seq_len + 1)
                    seq = store[sym][start: t_idx + 1]
                    if seq.shape[0] < seq_len:
                        pad = np.zeros(
                            (seq_len - seq.shape[0], n_feat), dtype=np.float32
                        )
                        seq = np.concatenate([pad, seq], axis=0)
                    seqs.append(seq)
                    indices.append(row_idx)
                    syms_in_batch.append(sym)

                if not seqs:
                    continue

                batch = torch.from_numpy(
                    np.stack(seqs).astype(np.float32)
                ).to(self.device)
                # Build concept matrix for this date's stocks
                concept_mat = self._build_concept_matrix(syms_in_batch)
                pred = self.model(batch, concept_mat).detach().cpu().numpy()
                for j, idx in enumerate(indices):
                    scores[idx] = pred[j]

        return scores

    def _build_concept_matrix(self, symbols: list[str]) -> torch.Tensor:
        """Build concept assignment matrix for a batch of stocks."""
        if self.concept_builder is not None:
            mat = self.concept_builder(symbols)  # (N, C) numpy
            return torch.from_numpy(mat.astype(np.float32)).to(self.device)
        # Fallback: identity matrix (each stock = own concept)
        N = len(symbols)
        return torch.eye(N, device=self.device)


# ---------------------------------------------------------------------------
# Training entry point
# ---------------------------------------------------------------------------


def train_hist_one_fold(
    train_df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    cfg: HISTConfig,
    full_df: pd.DataFrame | None = None,
    fold_idx: int = 0,
    prebuilt_store: tuple | None = None,
    concept_matrix_path: str | None = None,
) -> _HISTWrapper:
    """Train HIST model for one walk-forward fold.

    Follows the same pattern as train_master_one_fold:
    - Per-date batching for cross-sectional concept attention
    - Temporal validation split (last 15%)
    - Early stopping on validation IC
    """
    from .gru_ranker import build_feature_store

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(
        "HIST fold %d — device=%s, hidden=%d, seq=%d, concepts=%d/%d",
        fold_idx, device, cfg.hidden_size, cfg.seq_len,
        cfg.num_concepts, cfg.num_hidden_concepts,
    )

    # Reproducibility
    np.random.seed(fold_idx + 42)
    torch.manual_seed(fold_idx + 42)

    # Build feature store
    if prebuilt_store is not None:
        store, date_idx = prebuilt_store
    else:
        src_df = full_df if full_df is not None else train_df
        store, date_idx = build_feature_store(src_df, feature_cols)

    n_feat = len(feature_cols)
    seq_len = cfg.seq_len

    # Load concept matrix (industry classification)
    concept_builder = _load_concept_builder(concept_matrix_path, cfg.num_concepts)

    # Group samples by date for cross-sectional batching
    date_groups: dict[str, list[tuple[str, float]]] = {}
    for _, row in train_df.iterrows():
        sym, dt = row["symbol"], row["datetime"]
        if sym in date_idx and dt in date_idx[sym]:
            date_groups.setdefault(dt, []).append((sym, float(row[label_col])))

    all_dates = sorted(date_groups.keys())
    if not all_dates:
        raise ValueError("No valid training samples for HIST fold")

    # Temporal val split: last 15% of dates as validation
    n_val_dates = max(1, int(len(all_dates) * 0.15))
    val_dates = set(all_dates[-n_val_dates:])
    trn_dates = [d for d in all_dates if d not in val_dates]
    logger.info(
        "  train_dates=%d, val_dates=%d, total_stocks=%d",
        len(trn_dates), n_val_dates,
        sum(len(v) for v in date_groups.values()),
    )

    # Build model
    model = HIST(cfg).to(device)
    logger.info("  HIST params: %d", sum(p.numel() for p in model.parameters()))

    optimizer = optim.Adam(model.parameters(), lr=cfg.lr)
    best_val_ic = -np.inf
    best_state = copy.deepcopy(model.state_dict())
    no_improve = 0

    def _build_seq(sym: str, dt: str) -> np.ndarray:
        """Build temporal sequence for one stock on one date."""
        t_idx = date_idx[sym][dt]
        start = max(0, t_idx - seq_len + 1)
        seq = store[sym][start: t_idx + 1]
        if seq.shape[0] < seq_len:
            pad = np.zeros((seq_len - seq.shape[0], n_feat), dtype=np.float32)
            seq = np.concatenate([pad, seq], axis=0)
        return seq

    def _build_date_batch(date: str):
        """Build per-date batch with concept matrix."""
        items = date_groups[date]
        syms = [sym for sym, _ in items]
        seqs = [_build_seq(sym, date) for sym in syms]
        labels = np.array([lbl for _, lbl in items], dtype=np.float32)
        # Cross-sectional label normalization
        std = labels.std()
        if std > 1e-8:
            labels = (labels - labels.mean()) / std
        else:
            labels = labels - labels.mean()
        batch_x = torch.from_numpy(np.stack(seqs)).float().to(device)
        batch_y = torch.from_numpy(labels).float().to(device)
        # Concept matrix for this batch
        if concept_builder is not None:
            cm = concept_builder(syms)
            batch_cm = torch.from_numpy(cm.astype(np.float32)).to(device)
        else:
            batch_cm = torch.eye(len(syms), device=device)
        return batch_x, batch_y, batch_cm

    def _compute_val_ic() -> float:
        """Compute mean per-date rank IC on validation dates."""
        model.eval()
        ics: list[float] = []
        with torch.no_grad():
            for dt in val_dates:
                if dt not in date_groups or len(date_groups[dt]) < 5:
                    continue
                items = date_groups[dt]
                syms = [sym for sym, _ in items]
                seqs = np.stack([_build_seq(sym, dt) for sym in syms])
                labels = np.array([lbl for _, lbl in items], dtype=np.float32)
                bx = torch.from_numpy(seqs).float().to(device)
                if concept_builder is not None:
                    cm = concept_builder(syms)
                    bcm = torch.from_numpy(cm.astype(np.float32)).to(device)
                else:
                    bcm = torch.eye(len(syms), device=device)
                pred = model(bx, bcm).detach().cpu().numpy()
                ic = float(
                    pd.Series(pred).corr(pd.Series(labels), method="spearman")
                )
                if not np.isnan(ic):
                    ics.append(ic)
        return float(np.mean(ics)) if ics else 0.0

    # Training loop
    for epoch in range(cfg.epochs):
        model.train()
        epoch_losses: list[float] = []
        date_order = np.random.permutation(len(trn_dates))

        for di in date_order:
            dt = trn_dates[di]
            if len(date_groups[dt]) < 2:
                continue
            bx, by, bcm = _build_date_batch(dt)
            pred = model(bx, bcm)
            loss = torch.mean((pred - by) ** 2)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_value_(model.parameters(), 3.0)
            optimizer.step()
            epoch_losses.append(loss.item())

        # Validation
        val_ic = _compute_val_ic()
        train_loss = float(np.mean(epoch_losses)) if epoch_losses else 999.0

        if val_ic > best_val_ic:
            best_val_ic = val_ic
            best_state = copy.deepcopy(model.state_dict())
            no_improve = 0
        else:
            no_improve += 1

        if epoch % 5 == 0 or no_improve >= cfg.patience:
            logger.info(
                "  epoch %d: loss=%.4f, val_IC=%.4f (best=%.4f)",
                epoch, train_loss, val_ic, best_val_ic,
            )
        if no_improve >= cfg.patience:
            logger.info("  Early stop at epoch %d", epoch)
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.to(device)

    wrapper = _HISTWrapper(model, cfg, device, concept_builder)
    wrapper._feature_names = list(feature_cols)
    return wrapper


# ---------------------------------------------------------------------------
# Concept graph utilities
# ---------------------------------------------------------------------------


def _load_concept_builder(path: str | None, num_concepts: int):
    """Load or create concept matrix builder.

    Returns a callable: (symbols: list[str]) -> np.ndarray of shape (N, C)
    """
    if path is None:
        return None

    from pathlib import Path
    concept_path = Path(path)
    if not concept_path.exists():
        logger.warning("Concept matrix file not found: %s, using identity", path)
        return None

    # Load precomputed mapping: {symbol: concept_id}
    import json
    with open(concept_path, encoding="utf-8") as f:
        symbol_to_concepts = json.load(f)

    def builder(symbols: list[str]) -> np.ndarray:
        N = len(symbols)
        mat = np.zeros((N, num_concepts), dtype=np.float32)
        for i, sym in enumerate(symbols):
            concept_ids = symbol_to_concepts.get(sym, [])
            if isinstance(concept_ids, int):
                concept_ids = [concept_ids]
            for cid in concept_ids:
                if 0 <= cid < num_concepts:
                    mat[i, cid] = 1.0
        # Stocks with no concept assigned: uniform across all concepts
        no_concept_mask = mat.sum(axis=1) == 0
        if no_concept_mask.any():
            mat[no_concept_mask] = 1.0 / num_concepts
        return mat

    return builder
