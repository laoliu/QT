"""GAT 图注意力排序模型 — 基于行业关系图的跨股票信息聚合。

使用 PyTorch Geometric 实现多层 GAT (Graph Attention Network)，
通过行业图结构在截面股票间传播信息以增强因子表达能力。

依赖 (可选): torch, torch-geometric
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GATRankerConfig:
    """GAT 排序模型配置"""
    d_feat: int = 30
    d_model: int = 64
    n_heads: int = 4
    n_layers: int = 2
    dropout: float = 0.1
    lr: float = 1e-3
    epochs: int = 100
    batch_size: int = 512
    early_stop_patience: int = 10
    device: str = "cpu"


class GATRanker:
    """GAT 图注意力排序模型。

    用法::

        config = GATRankerConfig(d_feat=30)
        model = GATRanker(config)
        model.fit(X_train, y_train, edge_index, X_valid, y_valid)
        predictions = model.predict(X_test, edge_index)
    """

    def __init__(self, config: GATRankerConfig | None = None) -> None:
        self._config = config or GATRankerConfig()
        self._model = None
        self._fitted = False

    def _build_model(self, d_feat: int):
        """构建 GAT 模型"""
        try:
            import torch
            import torch.nn as nn
            from torch_geometric.nn import GATConv
        except ImportError:
            raise ImportError(
                "GAT 模型需要 torch 和 torch-geometric。"
                "pip install torch torch-geometric"
            )

        cfg = self._config

        class GATNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.input_proj = nn.Linear(d_feat, cfg.d_model)
                self.gat_layers = nn.ModuleList()
                self.norms = nn.ModuleList()
                for i in range(cfg.n_layers):
                    in_dim = cfg.d_model * cfg.n_heads if i > 0 else cfg.d_model
                    self.gat_layers.append(
                        GATConv(in_dim, cfg.d_model, heads=cfg.n_heads, dropout=cfg.dropout)
                    )
                    self.norms.append(nn.LayerNorm(cfg.d_model * cfg.n_heads))
                self.output = nn.Sequential(
                    nn.Linear(cfg.d_model * cfg.n_heads, cfg.d_model),
                    nn.ReLU(),
                    nn.Dropout(cfg.dropout),
                    nn.Linear(cfg.d_model, 1),
                )

            def forward(self, x, edge_index):
                h = self.input_proj(x)
                for gat, norm in zip(self.gat_layers, self.norms):
                    h = gat(h, edge_index)
                    h = norm(h)
                    h = torch.relu(h)
                return self.output(h).squeeze(-1)

        return GATNet()

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        edge_index: np.ndarray,
        X_valid: np.ndarray | None = None,
        y_valid: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """训练 GAT 模型。

        Args:
            X_train: 特征 (N, F)
            y_train: 标签 (N,)
            edge_index: 边索引 (2, E) — 行业图的邻接关系
            X_valid: 验证特征
            y_valid: 验证标签
        """
        import torch

        cfg = self._config
        device = torch.device(cfg.device)

        model = self._build_model(X_train.shape[1]).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr)
        criterion = torch.nn.MSELoss()

        X_t = torch.tensor(X_train, dtype=torch.float32, device=device)
        y_t = torch.tensor(y_train, dtype=torch.float32, device=device)
        ei_t = torch.tensor(edge_index, dtype=torch.long, device=device)

        best_loss = float("inf")
        patience_counter = 0
        best_state = None

        for epoch in range(cfg.epochs):
            model.train()
            optimizer.zero_grad()
            pred = model(X_t, ei_t)
            loss = criterion(pred, y_t)
            loss.backward()
            optimizer.step()

            if X_valid is not None and y_valid is not None:
                model.eval()
                with torch.no_grad():
                    X_v = torch.tensor(X_valid, dtype=torch.float32, device=device)
                    y_v = torch.tensor(y_valid, dtype=torch.float32, device=device)
                    val_pred = model(X_v, ei_t)
                    val_loss = criterion(val_pred, y_v).item()

                if val_loss < best_loss:
                    best_loss = val_loss
                    patience_counter = 0
                    best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                else:
                    patience_counter += 1
                    if patience_counter >= cfg.early_stop_patience:
                        logger.info(f"Early stopping at epoch {epoch}")
                        break

        if best_state:
            model.load_state_dict(best_state)
        self._model = model
        self._fitted = True
        return {"epochs": epoch + 1, "best_val_loss": best_loss}

    def predict(self, X: np.ndarray, edge_index: np.ndarray) -> np.ndarray:
        """预测排序分数。"""
        if not self._fitted:
            raise RuntimeError("Model not fitted.")
        import torch
        device = next(self._model.parameters()).device
        self._model.eval()
        with torch.no_grad():
            X_t = torch.tensor(X, dtype=torch.float32, device=device)
            ei_t = torch.tensor(edge_index, dtype=torch.long, device=device)
            return self._model(X_t, ei_t).cpu().numpy()

    def save(self, path: str) -> None:
        import torch
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"config": self._config, "state_dict": self._model.state_dict()}, path)

    def load(self, path: str, d_feat: int = 30) -> None:
        import torch
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        self._config = ckpt["config"]
        self._model = self._build_model(d_feat)
        self._model.load_state_dict(ckpt["state_dict"])
        self._fitted = True
