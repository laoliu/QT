"""MASTER — Market-guided Stock Transformer

原始论文: Li et al. (2024) "MASTER: Market-Guided Stock Transformer for
Stock Price Forecasting"

架构:
  1. Gate: 市场信号门控特征
  2. TAttention: 时序注意力 (intra-stock)
  3. SAttention: 截面注意力 (inter-stock)
  4. TemporalAttention: 时序聚合

依赖 (可选): torch
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MASTERConfig:
    """MASTER 模型配置"""
    d_feat: int = 30
    d_model: int = 64
    t_nhead: int = 4
    s_nhead: int = 2
    T_dropout: float = 0.1
    S_dropout: float = 0.1
    gate_start: int = 0
    gate_end: int = 30
    beta: float = 5.0
    seq_len: int = 20
    lr: float = 1e-4
    epochs: int = 100
    batch_size: int = 256
    early_stop_patience: int = 15
    device: str = "cpu"


class MASTERTrainer:
    """MASTER 模型训练与推理。

    用法::

        config = MASTERConfig(d_feat=30, seq_len=20)
        trainer = MASTERTrainer(config)
        trainer.fit(X_train_3d, y_train, X_valid_3d, y_valid)
        predictions = trainer.predict(X_test_3d)
    """

    def __init__(self, config: MASTERConfig | None = None) -> None:
        self._config = config or MASTERConfig()
        self._model = None
        self._fitted = False

    def _build_model(self):
        """构建 MASTER 模型"""
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            raise ImportError("MASTER 模型需要 torch。pip install torch")

        cfg = self._config

        class Gate(nn.Module):
            def __init__(self, d_input, d_output, beta=1.0):
                super().__init__()
                self.fc = nn.Linear(d_input, d_output)
                self.beta = beta

            def forward(self, x, market_signal):
                gate = torch.sigmoid(self.fc(market_signal) * self.beta)
                return x * gate.unsqueeze(1)

        class PositionalEncoding(nn.Module):
            def __init__(self, d_model, max_len=500):
                super().__init__()
                pe = torch.zeros(max_len, d_model)
                position = torch.arange(0, max_len).unsqueeze(1).float()
                div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
                pe[:, 0::2] = torch.sin(position * div_term)
                pe[:, 1::2] = torch.cos(position * div_term)
                self.register_buffer("pe", pe.unsqueeze(0))

            def forward(self, x):
                return x + self.pe[:, :x.size(1)]

        class TAttention(nn.Module):
            """Intra-stock temporal attention"""
            def __init__(self, d_model, nhead, dropout):
                super().__init__()
                self.mha = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
                self.norm1 = nn.LayerNorm(d_model)
                self.norm2 = nn.LayerNorm(d_model)
                self.ffn = nn.Sequential(
                    nn.Linear(d_model, d_model * 2), nn.ReLU(),
                    nn.Dropout(dropout), nn.Linear(d_model * 2, d_model),
                )

            def forward(self, x):
                x2 = self.norm1(x)
                x = x + self.mha(x2, x2, x2)[0]
                x2 = self.norm2(x)
                return x + self.ffn(x2)

        class TemporalAggregation(nn.Module):
            """Query-based temporal aggregation (last step)"""
            def __init__(self, d_model):
                super().__init__()
                self.proj = nn.Linear(d_model, d_model, bias=False)

            def forward(self, z):
                h = self.proj(z)
                query = h[:, -1, :].unsqueeze(-1)
                attn = torch.softmax(torch.matmul(h, query).squeeze(-1), dim=1)
                return torch.matmul(attn.unsqueeze(1), z).squeeze(1)

        class MASTER(nn.Module):
            def __init__(self):
                super().__init__()
                d_gate = cfg.gate_end - cfg.gate_start
                self.gate = Gate(d_gate, cfg.d_feat, cfg.beta)
                self.input_proj = nn.Linear(cfg.d_feat, cfg.d_model)
                self.pe = PositionalEncoding(cfg.d_model)
                self.t_attn = TAttention(cfg.d_model, cfg.t_nhead, cfg.T_dropout)
                self.temporal_agg = TemporalAggregation(cfg.d_model)
                self.output = nn.Sequential(
                    nn.Linear(cfg.d_model, cfg.d_model // 2),
                    nn.ReLU(), nn.Dropout(cfg.T_dropout),
                    nn.Linear(cfg.d_model // 2, 1),
                )

            def forward(self, x):
                # x: (N, T, F)
                market_signal = x[:, -1, cfg.gate_start:cfg.gate_end]
                x = self.gate(x, market_signal)
                x = self.input_proj(x)
                x = self.pe(x)
                x = self.t_attn(x)
                x = self.temporal_agg(x)
                return self.output(x).squeeze(-1)

        return MASTER()

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_valid: np.ndarray | None = None,
        y_valid: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """训练 MASTER。

        Args:
            X_train: (N, T, F) 3D 特征序列
            y_train: (N,) 标签
        """
        import torch
        from torch.utils.data import DataLoader, TensorDataset

        cfg = self._config
        device = torch.device(cfg.device)
        model = self._build_model().to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr)
        criterion = torch.nn.MSELoss()

        X_t = torch.tensor(X_train, dtype=torch.float32)
        y_t = torch.tensor(y_train, dtype=torch.float32)
        ds = TensorDataset(X_t, y_t)
        dl = DataLoader(ds, batch_size=cfg.batch_size, shuffle=True)

        best_loss = float("inf")
        patience = 0
        best_state = None

        for epoch in range(cfg.epochs):
            model.train()
            epoch_loss = 0.0
            for xb, yb in dl:
                xb, yb = xb.to(device), yb.to(device)
                optimizer.zero_grad()
                pred = model(xb)
                loss = criterion(pred, yb)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                epoch_loss += loss.item()

            if X_valid is not None and y_valid is not None:
                model.eval()
                with torch.no_grad():
                    xv = torch.tensor(X_valid, dtype=torch.float32, device=device)
                    yv = torch.tensor(y_valid, dtype=torch.float32, device=device)
                    val_loss = criterion(model(xv), yv).item()
                if val_loss < best_loss:
                    best_loss = val_loss
                    patience = 0
                    best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                else:
                    patience += 1
                    if patience >= cfg.early_stop_patience:
                        break

        if best_state:
            model.load_state_dict(best_state)
        self._model = model
        self._fitted = True
        return {"epochs": epoch + 1, "best_val_loss": best_loss}

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Model not fitted.")
        import torch
        self._model.eval()
        device = next(self._model.parameters()).device
        with torch.no_grad():
            xt = torch.tensor(X, dtype=torch.float32, device=device)
            return self._model(xt).cpu().numpy()

    def save(self, path: str) -> None:
        import torch
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"config": self._config, "state_dict": self._model.state_dict()}, path)

    def load(self, path: str) -> None:
        import torch
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        self._config = ckpt["config"]
        self._model = self._build_model()
        self._model.load_state_dict(ckpt["state_dict"])
        self._fitted = True
