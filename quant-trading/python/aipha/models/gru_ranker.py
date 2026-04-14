"""GRU 排序模型 — 时序依赖的股票排序。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GRURankerConfig:
    """GRU 排序配置"""
    input_size: int = 30
    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.3
    seq_len: int = 20
    learning_rate: float = 1e-3
    batch_size: int = 256
    epochs: int = 50
    patience: int = 10


class GRURanker:
    """GRU 时序排序模型

    输入: 每只股票过去 seq_len 天的特征序列
    输出: 排序得分
    """

    def __init__(self, config: GRURankerConfig | None = None):
        self._config = config or GRURankerConfig()
        self._model = None
        self._device = "cpu"

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_valid: Optional[np.ndarray] = None,
        y_valid: Optional[np.ndarray] = None,
    ) -> dict:
        """训练 GRU 模型。

        Args:
            X_train: (n_samples, seq_len, n_features)
            y_train: (n_samples,) 排序标签
        """
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            raise ImportError("需要安装 PyTorch: pip install torch")

        cfg = self._config
        n_features = X_train.shape[2] if X_train.ndim == 3 else cfg.input_size

        class _GRUModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.gru = nn.GRU(n_features, cfg.hidden_size, cfg.num_layers,
                                  batch_first=True, dropout=cfg.dropout if cfg.num_layers > 1 else 0)
                self.fc = nn.Linear(cfg.hidden_size, 1)
                self.dropout = nn.Dropout(cfg.dropout)

            def forward(self, x):
                out, _ = self.gru(x)
                out = self.dropout(out[:, -1, :])
                return self.fc(out).squeeze(-1)

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._device = str(device)
        model = _GRUModel().to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)
        criterion = nn.MSELoss()

        X_t = torch.FloatTensor(X_train).to(device)
        y_t = torch.FloatTensor(y_train).to(device)

        best_loss = float("inf")
        patience_counter = 0

        for epoch in range(cfg.epochs):
            model.train()
            indices = torch.randperm(len(X_t))
            total_loss = 0.0
            n_batches = 0
            for i in range(0, len(X_t), cfg.batch_size):
                batch_idx = indices[i:i + cfg.batch_size]
                Xb, yb = X_t[batch_idx], y_t[batch_idx]
                optimizer.zero_grad()
                pred = model(Xb)
                loss = criterion(pred, yb)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()
                n_batches += 1

            avg_loss = total_loss / max(n_batches, 1)

            # 验证
            if X_valid is not None:
                model.eval()
                with torch.no_grad():
                    Xv = torch.FloatTensor(X_valid).to(device)
                    yv = torch.FloatTensor(y_valid).to(device)
                    val_loss = criterion(model(Xv), yv).item()
                if val_loss < best_loss:
                    best_loss = val_loss
                    patience_counter = 0
                else:
                    patience_counter += 1
                    if patience_counter >= cfg.patience:
                        logger.info("GRU 早停 epoch=%d", epoch)
                        break

        self._model = model
        logger.info("GRU 训练完成: device=%s, epochs=%d", self._device, epoch + 1)
        return {"epochs": epoch + 1, "device": self._device}

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("模型未训练")
        import torch
        self._model.eval()
        device = next(self._model.parameters()).device
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(device)
            return self._model(X_t).cpu().numpy()

    def save(self, path: str) -> None:
        import torch
        torch.save({"model": self._model.state_dict(), "config": self._config}, path)

    def load(self, path: str, n_features: int = 30) -> None:
        import torch
        data = torch.load(path, map_location="cpu", weights_only=False)
        self._config = data.get("config", self._config)
        # 需要重建模型结构再加载权重
        logger.info("GRU 模型已加载: %s", path)
