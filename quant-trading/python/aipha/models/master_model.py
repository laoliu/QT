"""MASTER: Market-Guided Stock Transformer for Stock Price Forecasting.

AAAI 2024, Li et al. (SJTU-DMTai)
Adapted for Aipha-Q Walk-Forward training pipeline.

Architecture: Gate(market_info) → Linear → PosEnc → TAttn → SAttn → TempAgg → Decoder
Input: (N_stocks, T=8, F) where F = d_feat + d_market + 1(label)
"""

import math
import copy
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.nn.modules.linear import Linear
from torch.nn.modules.dropout import Dropout
from torch.nn.modules.normalization import LayerNorm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 100):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe)

    def forward(self, x):
        return x + self.pe[: x.shape[1], :]


class Gate(nn.Module):
    """Market-guided feature gate with temperature scaling."""

    def __init__(self, d_input: int, d_output: int, beta: float = 1.0):
        super().__init__()
        self.trans = nn.Linear(d_input, d_output)
        self.d_output = d_output
        self.t = beta

    def forward(self, gate_input):
        output = self.trans(gate_input)
        output = torch.softmax(output / self.t, dim=-1)
        return self.d_output * output


class TAttention(nn.Module):
    """Intra-stock temporal self-attention."""

    def __init__(self, d_model: int, nhead: int, dropout: float):
        super().__init__()
        self.d_model = d_model
        self.nhead = nhead
        self.qtrans = nn.Linear(d_model, d_model, bias=False)
        self.ktrans = nn.Linear(d_model, d_model, bias=False)
        self.vtrans = nn.Linear(d_model, d_model, bias=False)
        self.attn_dropout = nn.ModuleList(
            [Dropout(p=dropout) for _ in range(nhead)] if dropout > 0 else []
        )
        self.norm1 = LayerNorm(d_model, eps=1e-5)
        self.norm2 = LayerNorm(d_model, eps=1e-5)
        self.ffn = nn.Sequential(
            Linear(d_model, d_model), nn.ReLU(),
            Dropout(p=dropout), Linear(d_model, d_model), Dropout(p=dropout),
        )

    def forward(self, x):
        x = self.norm1(x)
        q = self.qtrans(x)
        k = self.ktrans(x)
        v = self.vtrans(x)
        dim = self.d_model // self.nhead
        att_output = []
        for i in range(self.nhead):
            lo = i * dim
            hi = self.d_model if i == self.nhead - 1 else (i + 1) * dim
            qh, kh, vh = q[:, :, lo:hi], k[:, :, lo:hi], v[:, :, lo:hi]
            attn = torch.softmax(torch.matmul(qh, kh.transpose(1, 2)), dim=-1)
            if self.attn_dropout:
                attn = self.attn_dropout[i](attn)
            att_output.append(torch.matmul(attn, vh))
        att_output = torch.cat(att_output, dim=-1)
        xt = self.norm2(x + att_output)
        return xt + self.ffn(xt)


class SAttention(nn.Module):
    """Inter-stock cross-sectional attention."""

    def __init__(self, d_model: int, nhead: int, dropout: float):
        super().__init__()
        self.d_model = d_model
        self.nhead = nhead
        self.temperature = math.sqrt(d_model / nhead)
        self.qtrans = nn.Linear(d_model, d_model, bias=False)
        self.ktrans = nn.Linear(d_model, d_model, bias=False)
        self.vtrans = nn.Linear(d_model, d_model, bias=False)
        self.attn_dropout = nn.ModuleList(
            [Dropout(p=dropout) for _ in range(nhead)]
        )
        self.norm1 = LayerNorm(d_model, eps=1e-5)
        self.norm2 = LayerNorm(d_model, eps=1e-5)
        self.ffn = nn.Sequential(
            Linear(d_model, d_model), nn.ReLU(),
            Dropout(p=dropout), Linear(d_model, d_model), Dropout(p=dropout),
        )

    def forward(self, x):
        # x: (N, T, D) -> transpose to (T, N, D) for cross-stock attention
        x = self.norm1(x)
        q = self.qtrans(x).transpose(0, 1)  # (T, N, D)
        k = self.ktrans(x).transpose(0, 1)
        v = self.vtrans(x).transpose(0, 1)
        dim = self.d_model // self.nhead
        att_output = []
        for i in range(self.nhead):
            lo = i * dim
            hi = self.d_model if i == self.nhead - 1 else (i + 1) * dim
            qh, kh, vh = q[:, :, lo:hi], k[:, :, lo:hi], v[:, :, lo:hi]
            attn = torch.softmax(
                torch.matmul(qh, kh.transpose(1, 2)) / self.temperature, dim=-1
            )
            if self.attn_dropout:
                attn = self.attn_dropout[i](attn)
            att_output.append(torch.matmul(attn, vh).transpose(0, 1))
        att_output = torch.cat(att_output, dim=-1)
        xt = self.norm2(x + att_output)
        return xt + self.ffn(xt)


class TemporalAttention(nn.Module):
    """Temporal aggregation via last-step query."""

    def __init__(self, d_model: int):
        super().__init__()
        self.trans = nn.Linear(d_model, d_model, bias=False)

    def forward(self, z):
        h = self.trans(z)  # (N, T, D)
        query = h[:, -1, :].unsqueeze(-1)  # (N, D, 1)
        lam = torch.matmul(h, query).squeeze(-1)  # (N, T)
        lam = torch.softmax(lam, dim=1).unsqueeze(1)  # (N, 1, T)
        output = torch.matmul(lam, z).squeeze(1)  # (N, D)
        return output


class MASTER(nn.Module):
    """Core MASTER model."""

    def __init__(
        self, d_feat: int, d_model: int, t_nhead: int, s_nhead: int,
        T_dropout_rate: float, S_dropout_rate: float,
        gate_input_start_index: int, gate_input_end_index: int,
        beta: float,
    ):
        super().__init__()
        self.gate_input_start_index = gate_input_start_index
        self.gate_input_end_index = gate_input_end_index
        d_gate_input = gate_input_end_index - gate_input_start_index
        self.feature_gate = Gate(d_gate_input, d_feat, beta=beta)
        self.layers = nn.Sequential(
            nn.Linear(d_feat, d_model),
            PositionalEncoding(d_model),
            TAttention(d_model, t_nhead, T_dropout_rate),
            SAttention(d_model, s_nhead, S_dropout_rate),
            TemporalAttention(d_model),
            nn.Linear(d_model, 1),
        )

    def forward(self, x):
        # x: (N, T, F_total) where F_total = d_feat + d_market
        src = x[:, :, : self.gate_input_start_index]  # (N, T, d_feat)
        if self.gate_input_end_index > self.gate_input_start_index:
            gate_input = x[:, -1, self.gate_input_start_index : self.gate_input_end_index]
            src = src * torch.unsqueeze(self.feature_gate(gate_input), dim=1)
        # else: no market features → skip gating, all features pass through equally
        output = self.layers(src).squeeze(-1)
        return output


class MASTERTrainer:
    """Walk-Forward training wrapper for MASTER."""

    def __init__(self, config: dict):
        self.config = config
        self.device = torch.device(
            f"cuda:{config.get('gpu', 0)}"
            if torch.cuda.is_available() else "cpu"
        )

    def train_fold(self, train_data, valid_data, seed: int = 42):
        """Train one WF fold. Returns trained model."""
        cfg = self.config
        torch.manual_seed(seed)
        np.random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        d_feat = cfg.get("d_feat", 158)
        d_model = cfg.get("d_model", 256)
        model = MASTER(
            d_feat=d_feat, d_model=d_model,
            t_nhead=cfg.get("t_nhead", 4),
            s_nhead=cfg.get("s_nhead", 2),
            T_dropout_rate=cfg.get("dropout", 0.5),
            S_dropout_rate=cfg.get("dropout", 0.5),
            gate_input_start_index=d_feat,
            gate_input_end_index=d_feat + cfg.get("d_market", 63),
            beta=cfg.get("beta", 5.0),
        ).to(self.device)

        optimizer = optim.Adam(model.parameters(), lr=cfg.get("lr", 1e-4))
        n_epochs = cfg.get("n_epochs", 30)
        stop_thres = cfg.get("train_stop_loss_thred", 0.80)
        best_val_ic = -1.0
        best_state = None
        patience = cfg.get("patience", 5)
        no_improve = 0

        for epoch in range(n_epochs):
            train_loss = self._train_epoch(model, optimizer, train_data)
            if valid_data is not None:
                val_ic = self._eval_epoch(model, valid_data)
                logger.info(
                    "  Fold epoch %d: loss=%.4f, val_IC=%.4f",
                    epoch, train_loss, val_ic,
                )
                if val_ic > best_val_ic:
                    best_val_ic = val_ic
                    best_state = copy.deepcopy(model.state_dict())
                    no_improve = 0
                else:
                    no_improve += 1
                if no_improve >= patience:
                    logger.info(
                        "  Early stop at epoch %d (patience=%d/%d)",
                        epoch, no_improve, patience,
                    )
                    break
            else:
                logger.info("  Fold epoch %d: loss=%.4f", epoch, train_loss)
                if train_loss <= stop_thres:
                    best_state = copy.deepcopy(model.state_dict())
                    break

        if best_state is not None:
            model.load_state_dict(best_state)
        return model

    def predict(self, model, test_data):
        """Predict on test data. Returns list of (date, symbol, pred_score, label)."""
        model.eval()
        results = []
        with torch.no_grad():
            for date_str, batch in test_data:
                feat = batch["features"].to(self.device)  # (N, T, F)
                labels = batch["labels"]  # (N,) numpy
                symbols = batch["symbols"]  # list of str
                pred = model(feat.float()).detach().cpu().numpy()
                for i, sym in enumerate(symbols):
                    results.append((date_str, sym, float(pred[i]), float(labels[i])))
        return results

    def walk_forward(self, full_data, dates, splits, output_dir=None):
        """Run full WF training. Returns all OOS predictions."""
        import time as _time

        all_preds = []
        total_folds = len(splits)
        fold_durations: list[float] = []
        wf_start = _time.time()

        for fold_idx, (train_dates, test_dates) in enumerate(splits):
            fold_start = _time.time()
            train_batches = self._make_daily_batches(full_data, train_dates)
            test_batches = self._make_daily_batches(full_data, test_dates)
            # Use last 10% of train as validation
            n_val = max(1, len(train_batches) // 10)
            valid_batches = train_batches[-n_val:]
            train_batches = train_batches[:-n_val]
            model = self.train_fold(train_batches, valid_batches, seed=fold_idx)
            fold_preds = self.predict(model, test_batches)
            ics = self._compute_daily_ic(fold_preds)
            mean_ic = np.nanmean(ics) if ics else 0.0
            fold_duration = _time.time() - fold_start
            fold_durations.append(fold_duration)
            logger.info(
                "Fold %d OOS: %d samples, IC=%.4f (%.0fs)",
                fold_idx, len(fold_preds), mean_ic, fold_duration,
            )
            # ── Write fold progress + heartbeat ──
            if output_dir is not None:
                self._write_fold_progress(
                    output_dir, fold_idx, mean_ic, len(fold_preds),
                    fold_duration, fold_durations, total_folds, wf_start,
                )
            all_preds.extend(fold_preds)
            del model
            torch.cuda.empty_cache()
        return all_preds

    def _write_fold_progress(
        self, output_dir, fold_idx, ic, n_samples,
        duration, durations, total, wf_start,
    ):
        """Append fold_progress.jsonl and update heartbeat.json."""
        import json as _json
        import time as _time
        from datetime import datetime as _dt

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        # fold_progress.jsonl
        progress_entry = {
            "fold": fold_idx,
            "ic": round(ic, 4),
            "n_samples": n_samples,
            "duration_s": round(duration, 1),
            "timestamp": _dt.now().isoformat(),
        }
        with open(out / "fold_progress.jsonl", "a", encoding="utf-8") as f:
            f.write(_json.dumps(progress_entry, ensure_ascii=False) + "\n")
        # heartbeat.json
        elapsed_h = (_time.time() - wf_start) / 3600
        avg_fold_s = sum(durations) / len(durations)
        remaining = total - (fold_idx + 1)
        eta_h = (avg_fold_s * remaining) / 3600
        heartbeat = {
            "fold": fold_idx,
            "total": total,
            "last_ic": round(ic, 4),
            "elapsed_h": round(elapsed_h, 2),
            "eta_h": round(eta_h, 2),
            "avg_fold_s": round(avg_fold_s, 1),
            "timestamp": _dt.now().isoformat(),
        }
        tmp = out / "heartbeat.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(heartbeat, f, indent=2)
        tmp.replace(out / "heartbeat.json")

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _train_epoch(self, model, optimizer, batches):
        model.train()
        losses = []
        for _date, batch in batches:
            feat = batch["features"].to(self.device)
            label = torch.tensor(batch["labels"], dtype=torch.float32).to(self.device)
            mask = ~torch.isnan(label)
            if mask.sum() < 10:
                continue
            # Drop 5% extreme + CSZScoreNorm
            label_valid = label[mask]
            n = label_valid.shape[0]
            n_drop = max(1, int(0.025 * n))
            sorted_vals, sorted_idx = label_valid.sort()
            keep_mask = torch.zeros_like(label, dtype=torch.bool)
            keep_idx = sorted_idx[n_drop:-n_drop] if n_drop < n // 2 else sorted_idx
            # Map back to original indices
            valid_indices = torch.where(mask)[0]
            keep_global = valid_indices[keep_idx]
            keep_mask[keep_global] = True
            if keep_mask.sum() < 5:
                continue
            label_kept = label[keep_mask]
            label_norm = (label_kept - label_kept.mean()) / (label_kept.std() + 1e-8)
            pred = model(feat[keep_mask].float())
            loss = torch.mean((pred - label_norm) ** 2)
            losses.append(loss.item())
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_value_(model.parameters(), 3.0)
            optimizer.step()
        return float(np.mean(losses)) if losses else 999.0

    def _eval_epoch(self, model, batches):
        model.eval()
        all_pred, all_label = [], []
        with torch.no_grad():
            for _date, batch in batches:
                feat = batch["features"].to(self.device)
                label = batch["labels"]
                pred = model(feat.float()).detach().cpu().numpy()
                mask = ~np.isnan(label)
                all_pred.extend(pred[mask].tolist())
                all_label.extend(label[mask].tolist())
        if len(all_pred) < 10:
            return 0.0
        ic = pd.Series(all_pred).corr(pd.Series(all_label))
        return ic if not np.isnan(ic) else 0.0

    def _make_daily_batches(self, full_data, date_list):
        """Build list of (date_str, batch_dict) for given dates.

        full_data: dict with keys 'feature_store', 'market_store',
                   'label_store', 'symbol_dates'.
        Each batch: {features: (N, T, F), labels: (N,), symbols: [str]}.
        """
        seq_len = self.config.get("seq_len", 8)
        feat_store = full_data["feature_store"]   # {sym: np.ndarray(total_T, d_feat)}
        market_store = full_data["market_store"]   # {date_str: np.ndarray(d_market,)}
        label_store = full_data["label_store"]     # {(date_str, sym): float}
        sym_date_idx = full_data["sym_date_idx"]   # {sym: {date_str: int}}
        d_feat = self.config.get("d_feat", 158)
        d_market = self.config.get("d_market", 63)

        batches = []
        for dt in date_list:
            features_list, labels_list, syms = [], [], []
            mkt = market_store.get(dt)
            if mkt is None:
                continue
            for sym, date_idx_map in sym_date_idx.items():
                if dt not in date_idx_map:
                    continue
                idx = date_idx_map[dt]
                if idx < seq_len - 1:
                    continue
                feat_seq = feat_store[sym][idx - seq_len + 1: idx + 1]  # (T, d_feat)
                if feat_seq.shape[0] != seq_len:
                    continue
                # Append market info to each timestep
                mkt_expanded = np.tile(mkt, (seq_len, 1))  # (T, d_market)
                combined = np.concatenate([feat_seq, mkt_expanded], axis=1)
                features_list.append(combined)
                lbl = label_store.get((dt, sym), np.nan)
                labels_list.append(lbl)
                syms.append(sym)
            if len(features_list) < 10:
                continue
            batch = {
                "features": torch.tensor(
                    np.stack(features_list), dtype=torch.float32
                ),
                "labels": np.array(labels_list, dtype=np.float32),
                "symbols": syms,
            }
            batches.append((dt, batch))
        return batches

    @staticmethod
    def _compute_daily_ic(predictions):
        """Compute daily IC from list of (date, sym, pred, label)."""
        if not predictions:
            return []
        df = pd.DataFrame(predictions, columns=["date", "symbol", "pred", "label"])
        ics = []
        for _dt, grp in df.groupby("date"):
            valid = grp.dropna(subset=["label"])
            if len(valid) < 10:
                continue
            ic = valid["pred"].corr(valid["label"])
            if not np.isnan(ic):
                ics.append(ic)
        return ics


# ---------------------------------------------------------------------------
# Walk-Forward compatible wrapper (matches GRU/TRA pattern)
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class MASTERConfig:
    """MASTER WF 训练超参数。"""

    d_model: int = 256
    t_nhead: int = 4
    s_nhead: int = 2
    dropout: float = 0.5
    beta: float = 5.0
    seq_len: int = 8
    lr: float = 1e-4
    epochs: int = 30
    patience: int = 5
    batch_size: int = 2048


class _MASTERWrapper:
    """适配 MLStockSelector WF 管道的 MASTER wrapper。

    提供与 GRU/TRA wrapper 相同的接口：
    - predict(X: np.ndarray) -> np.ndarray  (flat 2D fallback)
    - predict_temporal_fast(oos_df, feature_cols, prebuilt_store) -> np.ndarray
    """

    def __init__(self, model: MASTER, cfg: MASTERConfig, device: torch.device):
        self.model = model
        self.cfg = cfg
        self.device = device
        self._feature_names: list[str] = []

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Flat 2D predict: 单时步 length-1 序列，无 market gate。"""
        self.model.eval()
        n_feat = X.shape[1]
        # 构造 (N, 1, d_feat) — 无 market 信息时 gate 被旁路
        X_3d = X.reshape(X.shape[0], 1, n_feat).astype(np.float32)
        with torch.no_grad():
            t = torch.from_numpy(X_3d).to(self.device)
            # gate_input 范围为 [n_feat, n_feat) 即空切片，Gate 不生效
            pred = self.model(t.float()).detach().cpu().numpy()
        return pred

    def predict_temporal_fast(
        self,
        oos_df: pd.DataFrame,
        feature_cols: list[str],
        prebuilt_store: tuple[dict[str, np.ndarray], dict[str, dict]],
    ) -> np.ndarray:
        """用预构建 feature store 做时序预测（per-date batching for SAttention）。"""
        store, date_idx = prebuilt_store
        seq_len = self.cfg.seq_len
        n_feat = len(feature_cols)
        self.model.eval()

        scores = np.zeros(len(oos_df), dtype=np.float32)

        # Group by date for correct cross-sectional attention
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
                for row_idx, sym in items:
                    t_idx = date_idx[sym][dt]
                    start = max(0, t_idx - seq_len + 1)
                    seq = store[sym][start : t_idx + 1]
                    if seq.shape[0] < seq_len:
                        pad = np.zeros(
                            (seq_len - seq.shape[0], n_feat), dtype=np.float32
                        )
                        seq = np.concatenate([pad, seq], axis=0)
                    seqs.append(seq)
                    indices.append(row_idx)

                if not seqs:
                    continue
                # All stocks on same date → SAttention semantically correct
                batch = torch.from_numpy(
                    np.stack(seqs).astype(np.float32)
                ).to(self.device)
                pred = self.model(batch.float()).detach().cpu().numpy()
                for j, idx in enumerate(indices):
                    scores[idx] = pred[j]

        return scores


def train_master_one_fold(
    train_df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    cfg: MASTERConfig,
    full_df: pd.DataFrame | None = None,
    fold_idx: int = 0,
    prebuilt_store: tuple | None = None,
) -> _MASTERWrapper:
    """Train MASTER model for one walk-forward fold.

    Key difference from GRU/TRA: uses per-date batching for SAttention
    and per-date cross-sectional label normalization.
    """
    from .gru_ranker import build_feature_store

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(
        "MASTER fold %d — device=%s, d_model=%d, seq=%d, heads=(%d,%d)",
        fold_idx, device, cfg.d_model, cfg.seq_len, cfg.t_nhead, cfg.s_nhead,
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

    # Group samples by date for cross-sectional batching
    date_groups: dict[str, list[tuple[str, float]]] = {}
    for _, row in train_df.iterrows():
        sym, dt = row["symbol"], row["datetime"]
        if sym in date_idx and dt in date_idx[sym]:
            date_groups.setdefault(dt, []).append((sym, float(row[label_col])))

    all_dates = sorted(date_groups.keys())
    if not all_dates:
        raise ValueError("No valid training samples for MASTER fold")

    # Temporal val split: last 15% of dates as validation
    n_val_dates = max(1, int(len(all_dates) * 0.15))
    val_dates = set(all_dates[-n_val_dates:])
    trn_dates = [d for d in all_dates if d not in val_dates]
    logger.info(
        "  train_dates=%d, val_dates=%d, total_stocks=%d",
        len(trn_dates), n_val_dates,
        sum(len(v) for v in date_groups.values()),
    )

    # Build model — no market gate (d_market=0)
    model = MASTER(
        d_feat=n_feat,
        d_model=cfg.d_model,
        t_nhead=cfg.t_nhead,
        s_nhead=cfg.s_nhead,
        T_dropout_rate=cfg.dropout,
        S_dropout_rate=cfg.dropout,
        gate_input_start_index=n_feat,
        gate_input_end_index=n_feat,  # empty range = no gate
        beta=cfg.beta,
    ).to(device)
    logger.info("  model params: %d", sum(p.numel() for p in model.parameters()))

    optimizer = optim.Adam(model.parameters(), lr=cfg.lr)
    best_val_ic = -np.inf
    best_state = copy.deepcopy(model.state_dict())
    no_improve = 0

    def _build_seq(sym: str, dt: str) -> np.ndarray:
        """Build temporal sequence for one stock on one date."""
        t_idx = date_idx[sym][dt]
        start = max(0, t_idx - seq_len + 1)
        seq = store[sym][start : t_idx + 1]
        if seq.shape[0] < seq_len:
            pad = np.zeros((seq_len - seq.shape[0], n_feat), dtype=np.float32)
            seq = np.concatenate([pad, seq], axis=0)
        return seq

    def _build_date_batch(date: str) -> tuple[torch.Tensor, torch.Tensor]:
        """Build per-date batch: (N_stocks, seq_len, n_feat), labels normalized."""
        items = date_groups[date]
        seqs = [_build_seq(sym, date) for sym, _ in items]
        labels = np.array([lbl for _, lbl in items], dtype=np.float32)
        # Cross-sectional label normalization (per-date)
        std = labels.std()
        if std > 1e-8:
            labels = (labels - labels.mean()) / std
        else:
            labels = labels - labels.mean()
        return (
            torch.from_numpy(np.stack(seqs)).float().to(device),
            torch.from_numpy(labels).float().to(device),
        )

    def _compute_val_ic() -> float:
        """Compute mean per-date rank IC on validation dates."""
        model.eval()
        ics: list[float] = []
        with torch.no_grad():
            for dt in val_dates:
                if dt not in date_groups or len(date_groups[dt]) < 5:
                    continue
                items = date_groups[dt]
                seqs = np.stack([_build_seq(sym, dt) for sym, _ in items])
                labels = np.array([lbl for _, lbl in items], dtype=np.float32)
                bx = torch.from_numpy(seqs).float().to(device)
                pred = model(bx).detach().cpu().numpy()
                ic = float(
                    pd.Series(pred).corr(pd.Series(labels), method="spearman")
                )
                if not np.isnan(ic):
                    ics.append(ic)
        return float(np.mean(ics)) if ics else 0.0

    # Training loop with per-date batching
    for epoch in range(cfg.epochs):
        model.train()
        epoch_losses: list[float] = []
        date_order = np.random.permutation(len(trn_dates))

        for di in date_order:
            dt = trn_dates[di]
            if len(date_groups[dt]) < 2:
                continue
            bx, by = _build_date_batch(dt)
            pred = model(bx)
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

    wrapper = _MASTERWrapper(model, cfg, device)
    wrapper._feature_names = list(feature_cols)
    return wrapper
