"""Experimental GAT（图注意力网络）截面因子排名模型。

使用 Graph Attention Networks 对 A股截面进行因子打分排名，
通过行业关联图捕获股票间的相互依赖关系。

理论基础：
- Veličković et al. (2018) — Graph Attention Networks (GAT)
- Qlib: GATs 截面模型 (ICIR=0.3535, 知识库 D7)
- Tsiveriotis-Fernandes (1998) — 图结构因子关联

知识库对齐：知识库 G2 "GAT图神经网络因子模型"，D7 基准 ICIR=0.3535

与现有架构关系：
- 特征来自 equity_factors.py / alpha158.py
- 行业关联图：申万一级行业同质股票连接
- 推理输出对接 MLFactorStrategy 的 model_type='gat'
- Walk-Forward 验证兼容 wf_config_runner.py

使用示例::

    config = GATRankerConfig(input_dim=30, hidden_dim=64, n_heads=4)
    model = GATFactorRanker(config)
    model.fit(train_df, label_col="fwd_ret_rank")
    scores = model.predict(test_df)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 可选深度学习依赖
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from torch_geometric.nn import GATConv
    from torch_geometric.data import Data

    HAS_PYG = True
except ImportError:
    HAS_PYG = False


# ── 配置 ──


@dataclass
class GATRankerConfig:
    """GAT 排名模型配置"""

    input_dim: int = 30
    hidden_dim: int = 64
    output_dim: int = 1
    n_heads: int = 4
    n_layers: int = 2
    dropout: float = 0.3
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    epochs: int = 100
    patience: int = 10
    batch_size: int = 512
    label_col: str = "fwd_ret_rank"
    date_col: str = "trade_date"
    code_col: str = "ts_code"
    industry_col: str = "industry"


# ── GAT 网络模块 ──

# 当 torch 不可用时使用 object 作为基类（模块仍可导入，实例化时才报错）
_nn_Module = nn.Module if HAS_TORCH else object


class GATBlock(_nn_Module):
    """单层 GAT 模块"""

    def __init__(
        self, in_dim: int, out_dim: int, heads: int, dropout: float,
    ) -> None:
        super().__init__()
        if not HAS_PYG:
            raise ImportError(
                "torch_geometric is required for GATBlock. "
                "pip install torch_geometric"
            )
        self.conv = GATConv(
            in_dim, out_dim, heads=heads, dropout=dropout,
        )
        self.norm = nn.LayerNorm(out_dim * heads)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, edge_index) -> Any:
        h = self.conv(x, edge_index)
        h = self.norm(h)
        h = F.elu(h)
        return self.dropout(h)


class GATNetwork(_nn_Module):
    """多层 GAT 网络"""

    def __init__(self, config: GATRankerConfig) -> None:
        super().__init__()
        if not HAS_TORCH:
            raise ImportError("PyTorch required. pip install torch")
        layers = []
        in_d = config.input_dim
        for i in range(config.n_layers - 1):
            layers.append(
                GATBlock(in_d, config.hidden_dim, config.n_heads, config.dropout)
            )
            in_d = config.hidden_dim * config.n_heads
        self.layers = nn.ModuleList(layers)
        # 最后一层：单头输出
        self.final_conv = GATConv(
            in_d, config.output_dim, heads=1, concat=False,
        )
        self.fc = nn.Linear(config.output_dim, 1)

    def forward(self, x, edge_index) -> Any:
        for layer in self.layers:
            x = layer(x, edge_index)
        x = self.final_conv(x, edge_index)
        x = F.elu(x)
        return self.fc(x).squeeze(-1)


# ── 图构建 ──


class IndustryGraphBuilder:
    """基于行业关联构建股票图。

    同行业股票双向连接，形成行业内完全图。
    """

    def __init__(self, industry_col: str = "industry") -> None:
        self.industry_col = industry_col

    def build_graph(
        self, df: pd.DataFrame, date: str,
    ) -> tuple:
        """构建单截面行业关联图。

        Returns:
            (edge_index, n_nodes): edge_index shape [2, E], n_nodes int
        """
        codes = df.index.tolist() if df.index.name else list(range(len(df)))
        n = len(df)
        if self.industry_col not in df.columns or n < 2:
            # 无行业信息：自连接
            idx = list(range(n))
            return np.array([idx, idx], dtype=np.int64), n

        src, dst = [], []
        groups = df.groupby(self.industry_col)
        for _, group_df in groups:
            idxs = group_df.index.tolist()
            local_idxs = [df.index.get_loc(i) for i in idxs]
            for i in local_idxs:
                for j in local_idxs:
                    if i != j:
                        src.append(i)
                        dst.append(j)
        if not src:
            idx = list(range(n))
            return np.array([idx, idx], dtype=np.int64), n

        edge_index = np.array([src, dst], dtype=np.int64)
        return edge_index, n


# ── 主模型类 ──


class GATFactorRanker:
    """GAT 因子排名模型"""

    def __init__(self, config: Optional[GATRankerConfig] = None) -> None:
        if not HAS_TORCH:
            raise ImportError("PyTorch required. pip install torch")
        self.config = config or GATRankerConfig()
        self.model: Optional[GATNetwork] = None
        self.graph_builder = IndustryGraphBuilder(self.config.industry_col)
        self.feature_cols: list[str] = []
        self._device = torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )

    def fit(
        self,
        df: pd.DataFrame,
        label_col: Optional[str] = None,
        val_df: Optional[pd.DataFrame] = None,
    ) -> dict:
        """训练 GAT 模型（按日期截面训练）"""
        label_col = label_col or self.config.label_col
        if label_col not in df.columns:
            raise ValueError(f"Label column '{label_col}' not in df")

        features, self.feature_cols = self._prepare_features(df)
        self.config.input_dim = len(self.feature_cols)

        self.model = GATNetwork(self.config).to(self._device)
        optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )

        dates = sorted(df[self.config.date_col].unique())
        best_loss = float("inf")
        patience_counter = 0
        history: list[float] = []

        for epoch in range(self.config.epochs):
            epoch_loss = self._train_epoch_dates(
                df, features, dates, label_col, optimizer,
            )
            history.append(epoch_loss)
            if epoch_loss < best_loss:
                best_loss = epoch_loss
                patience_counter = 0
            else:
                patience_counter += 1
            if patience_counter >= self.config.patience:
                logger.info("Early stop at epoch %d", epoch + 1)
                break

        return {"best_loss": best_loss, "epochs": len(history)}

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """预测截面分数"""
        if self.model is None:
            raise RuntimeError("Model not trained. Call fit() first.")
        self.model.eval()
        features, _ = self._prepare_features(df)
        dates = sorted(df[self.config.date_col].unique())
        all_scores = np.full(len(df), np.nan)

        with torch.no_grad():
            for dt in dates:
                mask = df[self.config.date_col] == dt
                sub = df.loc[mask]
                idx_in_df = np.where(mask.values)[0]
                x_np = features[idx_in_df]
                edge_index, n = self.graph_builder.build_graph(sub, dt)
                x_t = torch.tensor(x_np, dtype=torch.float32).to(self._device)
                ei_t = torch.tensor(edge_index, dtype=torch.long).to(self._device)
                scores = self.model(x_t, ei_t).cpu().numpy()
                all_scores[idx_in_df] = scores
        return all_scores

    def save(self, path: str | Path) -> None:
        """保存模型到目录"""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        if self.model is not None:
            torch.save(self.model.state_dict(), path / "gat_model.pt")
        import json
        meta = {
            "feature_cols": self.feature_cols,
            "config": {
                k: v for k, v in self.config.__dict__.items()
            },
        }
        (path / "gat_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2)
        )

    @classmethod
    def load(cls, path: str | Path) -> GATFactorRanker:
        """加载模型"""
        import json
        path = Path(path)
        meta = json.loads((path / "gat_meta.json").read_text())
        config = GATRankerConfig(**meta["config"])
        ranker = cls(config)
        ranker.feature_cols = meta["feature_cols"]
        ranker.model = GATNetwork(config).to(ranker._device)
        state = torch.load(
            path / "gat_model.pt",
            map_location=ranker._device,
            weights_only=True,
        )
        ranker.model.load_state_dict(state)
        return ranker

    def _prepare_features(
        self, df: pd.DataFrame,
    ) -> tuple[np.ndarray, list[str]]:
        """提取特征矩阵（排除日期/代码/行业/标签列）"""
        exclude = {
            self.config.date_col,
            self.config.code_col,
            self.config.industry_col,
            self.config.label_col,
        }
        if self.feature_cols:
            cols = [c for c in self.feature_cols if c in df.columns]
        else:
            cols = [c for c in df.columns if c not in exclude]
            cols = [
                c for c in cols
                if df[c].dtype in (np.float64, np.float32, np.int64, np.int32)
            ]
        arr = df[cols].fillna(0).values.astype(np.float32)
        return arr, cols

    def _train_epoch(
        self, loader_data: list, optimizer: Any,
    ) -> float:
        """单 epoch 训练（通用接口）"""
        return self._train_epoch_dates(
            loader_data[0], loader_data[1], loader_data[2],
            self.config.label_col, optimizer,
        )

    def _train_epoch_dates(
        self,
        df: pd.DataFrame,
        features: np.ndarray,
        dates: list,
        label_col: str,
        optimizer: Any,
    ) -> float:
        """按日期截面训练一个 epoch"""
        self.model.train()
        total_loss = 0.0
        n_dates = 0
        for dt in dates:
            mask = df[self.config.date_col] == dt
            sub = df.loc[mask]
            if len(sub) < 3:
                continue
            idx_in_df = np.where(mask.values)[0]
            x_np = features[idx_in_df]
            y_np = sub[label_col].values.astype(np.float32)
            edge_index, n = self.graph_builder.build_graph(sub, dt)
            x_t = torch.tensor(x_np, dtype=torch.float32).to(self._device)
            y_t = torch.tensor(y_np, dtype=torch.float32).to(self._device)
            ei_t = torch.tensor(edge_index, dtype=torch.long).to(self._device)
            optimizer.zero_grad()
            pred = self.model(x_t, ei_t)
            loss = F.mse_loss(pred, y_t)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()
            n_dates += 1
        return total_loss / max(n_dates, 1)
