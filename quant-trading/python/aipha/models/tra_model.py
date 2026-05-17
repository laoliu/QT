"""TRA (Temporal Routing Adaptor) 模型实现

基于 Hengxu Lin et al., AAAI 2021:
"Learning Multiple Stock Trading Patterns with Temporal Routing Adaptor and Optimal Transport"

核心思想: 不同市场制度下使用不同预测头，Router 基于历史预测误差自动路由。
- 多预测头: N 个独立 predictor 对应 N 种交易模式
- 路由器: GRU 编码历史 loss → Gumbel Softmax 选择预测头
- 最优传输: Sinkhorn 算法做 soft assignment

参考: https://github.com/microsoft/qlib/blob/main/qlib/contrib/model/pytorch_tra.py
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def sinkhorn(M: torch.Tensor, reg: float = 0.1, n_iter: int = 10) -> torch.Tensor:
    """Sinkhorn-Knopp 最优传输求解。

    Args:
        M: cost matrix [N, K] (negative → assignment 概率高)
        reg: 正则化参数
        n_iter: 迭代次数

    Returns:
        P: transport plan [N, K]
    """
    N, K = M.shape
    # 初始化为均匀分布
    log_P = M / reg
    for _ in range(n_iter):
        # 行归一化
        log_P = log_P - torch.logsumexp(log_P, dim=1, keepdim=True)
        # 列归一化 (确保每列之和约 = N/K)
        log_P = log_P - torch.logsumexp(log_P, dim=0, keepdim=True) + math.log(N / K)
    return torch.exp(log_P)


def minmax_norm(x: torch.Tensor) -> torch.Tensor:
    """Min-max 归一化到 [0, 1]。"""
    x_min = x.min(dim=0, keepdim=True).values
    x_max = x.max(dim=0, keepdim=True).values
    denom = x_max - x_min
    denom = denom.clamp(min=1e-8)
    return (x - x_min) / denom


def loss_fn(pred: torch.Tensor, label: torch.Tensor) -> torch.Tensor:
    """MSE loss, 忽略 NaN label。"""
    mask = ~torch.isnan(label)
    if mask.sum() == 0:
        return torch.tensor(0.0, device=pred.device, requires_grad=True)
    return F.mse_loss(pred[mask], label[mask])


# ---------------------------------------------------------------------------
# Backbone: GRU with Attention
# ---------------------------------------------------------------------------


class GRUBackbone(nn.Module):
    """GRU backbone with optional attention (ALSTM style)。

    Args:
        input_size: 输入特征维度
        hidden_size: GRU 隐藏层大小
        num_layers: GRU 层数
        dropout: dropout 率
        use_attn: 是否使用注意力
    """

    def __init__(
        self,
        input_size: int = 158,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.0,
        use_attn: bool = True,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.use_attn = use_attn
        self.output_size = hidden_size

        self.rnn = nn.GRU(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )

        if use_attn:
            self.W_attn = nn.Linear(hidden_size, hidden_size)
            self.u_attn = nn.Linear(hidden_size, 1, bias=False)
            self.output_size = hidden_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass.

        Args:
            x: [batch, seq_len, input_size]

        Returns:
            hidden: [batch, hidden_size]
        """
        out, _ = self.rnn(x)  # [batch, seq_len, hidden_size]

        if self.use_attn:
            # Concat attention (Adv-ALSTM style)
            attn_scores = self.u_attn(torch.tanh(self.W_attn(out)))  # [B, T, 1]
            attn_weights = F.softmax(attn_scores, dim=1)  # [B, T, 1]
            hidden = (out * attn_weights).sum(dim=1)  # [B, H]
        else:
            hidden = out[:, -1, :]  # 取最后时间步

        return hidden


# ---------------------------------------------------------------------------
# TRA Router + Predictors
# ---------------------------------------------------------------------------


class TRA(nn.Module):
    """Temporal Routing Adaptor.

    Args:
        input_size: backbone 输出维度
        num_states: 交易模式/预测头数量 (推荐 2-5)
        hidden_size: router 隐藏层大小
        tau: Gumbel softmax 温度
        src_info: 路由信息源 ("LR" | "TPE" | "LR_TPE")
    """

    def __init__(
        self,
        input_size: int,
        num_states: int = 3,
        hidden_size: int = 8,
        tau: float = 1.0,
        src_info: str = "LR_TPE",
    ):
        super().__init__()
        self.num_states = num_states
        self.tau = tau
        self.src_info = src_info

        # 多预测头
        self.predictors = nn.Linear(input_size, num_states)

        # Router
        if num_states > 1:
            if "TPE" in src_info:
                self.router = nn.GRU(
                    input_size=num_states,
                    hidden_size=hidden_size,
                    num_layers=1,
                    batch_first=True,
                )
            if "LR" in src_info and "TPE" in src_info:
                self.fc = nn.Linear(input_size + hidden_size, num_states)
            elif "LR" in src_info:
                self.fc = nn.Linear(input_size, num_states)
            else:
                self.fc = nn.Linear(hidden_size, num_states)

    def forward(
        self, hidden: torch.Tensor, hist_loss: torch.Tensor,
    ) -> tuple[torch.Tensor, Optional[torch.Tensor], Optional[torch.Tensor]]:
        """Forward pass.

        Args:
            hidden: backbone 输出 [batch, input_size]
            hist_loss: 历史 loss [batch, seq_len, num_states]

        Returns:
            preds: 所有预测头输出 [batch, num_states]
            choice: Gumbel softmax hard choice [batch, num_states] (或 None)
            prob: softmax 概率 [batch, num_states] (或 None)
        """
        preds = self.predictors(hidden)  # [B, num_states]

        if self.num_states == 1:
            return preds, None, None

        # Router: 基于历史 loss 和/或隐藏表示做路由
        if "TPE" in self.src_info:
            _, router_out = self.router(hist_loss)  # [1, B, router_hidden]
            router_out = router_out.squeeze(0)  # [B, router_hidden]
            if "LR" in self.src_info:
                router_out = torch.cat([hidden, router_out], dim=-1)  # LR_TPE
        else:
            router_out = hidden  # LR only

        logits = self.fc(router_out)  # [B, num_states]

        # Gumbel softmax: 训练时 hard=True, 推理时用 argmax
        if self.training:
            choice = F.gumbel_softmax(logits, dim=-1, tau=self.tau, hard=True)
        else:
            choice = F.one_hot(logits.argmax(dim=-1), self.num_states).float()

        prob = torch.softmax(logits / self.tau, dim=-1)

        return preds, choice, prob


# ---------------------------------------------------------------------------
# 完整 TRA Ranker (集成到我们的训练管道)
# ---------------------------------------------------------------------------


class TRARanker(nn.Module):
    """完整 TRA 排名模型，适配 Aipha-Q Walk-Forward 训练管道。

    Args:
        input_size: 特征数
        hidden_size: GRU 隐藏层大小
        num_layers: GRU 层数
        num_states: 交易模式数量
        router_hidden: Router GRU 隐藏大小
        tau: Gumbel softmax 温度
        seq_len: 时序窗口长度
        dropout: dropout 率
        use_attn: backbone 是否用注意力
        src_info: 路由信息源
    """

    def __init__(
        self,
        input_size: int = 158,
        hidden_size: int = 64,
        num_layers: int = 2,
        num_states: int = 3,
        router_hidden: int = 8,
        tau: float = 1.0,
        seq_len: int = 20,
        dropout: float = 0.1,
        use_attn: bool = True,
        src_info: str = "LR_TPE",
    ):
        super().__init__()
        self.seq_len = seq_len
        self.num_states = num_states

        self.backbone = GRUBackbone(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            use_attn=use_attn,
        )

        self.tra = TRA(
            input_size=self.backbone.output_size,
            num_states=num_states,
            hidden_size=router_hidden,
            tau=tau,
            src_info=src_info,
        )

    def forward(
        self,
        x: torch.Tensor,
        hist_loss: Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, Optional[torch.Tensor], Optional[torch.Tensor]]:
        """Forward pass.

        Args:
            x: [batch, seq_len, input_size]
            hist_loss: [batch, memory_len, num_states] 历史预测误差

        Returns:
            preds: [batch, num_states] 所有预测头输出
            choice: [batch, num_states] Gumbel 路由选择
            prob: [batch, num_states] 路由概率
        """
        hidden = self.backbone(x)  # [B, hidden_size]

        # 如果没有历史 loss, 创建零向量 (首次训练时)
        if hist_loss is None:
            hist_loss = torch.zeros(
                x.size(0), 1, self.num_states, device=x.device
            )

        return self.tra(hidden, hist_loss)

    def predict(self, x: torch.Tensor, hist_loss: Optional[torch.Tensor] = None) -> torch.Tensor:
        """推理时输出单个分数。

        Args:
            x: [batch, seq_len, input_size]
            hist_loss: [batch, memory_len, num_states]

        Returns:
            score: [batch] 最终预测分数
        """
        self.eval()
        with torch.no_grad():
            preds, choice, prob = self.forward(x, hist_loss)
            if choice is not None:
                # 使用 router 选择的预测头
                score = (preds * choice).sum(dim=1)
            else:
                score = preds.squeeze(-1)
        return score


# ---------------------------------------------------------------------------
# Optimal Transport 训练函数
# ---------------------------------------------------------------------------


def transport_sample(
    all_preds: torch.Tensor,
    label: torch.Tensor,
    choice: torch.Tensor,
    prob: torch.Tensor,
    hist_loss_mean: torch.Tensor,
    transport_method: str,
    alpha: float = 1.0,
    training: bool = False,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Sample-wise 最优传输。

    Args:
        all_preds: [N, K] 所有预测头输出
        label: [N] 真实标签
        choice: [N, K] Gumbel softmax 选择
        prob: [N, K] Router 概率
        hist_loss_mean: [N, K] 平均历史 loss
        transport_method: "oracle" | "router"
        alpha: 融合参数 (当前 loss vs 历史 loss)
        training: 是否训练模式

    Returns:
        loss, pred, L (当前 loss 矩阵), P (传输计划)
    """
    mask = ~torch.isnan(label)
    all_loss = torch.zeros_like(all_preds)
    if mask.sum() > 0:
        all_loss[mask] = (all_preds[mask] - label[mask, None]).pow(2)

    L = minmax_norm(all_loss.detach())
    # 融合当前 loss 与历史 loss
    Lh = L * alpha + minmax_norm(hist_loss_mean) * (1 - alpha)
    Lh = minmax_norm(Lh)
    P = sinkhorn(-Lh)

    if transport_method == "router":
        if training:
            pred = (all_preds * choice).sum(dim=1)  # Gumbel softmax
        else:
            pred = all_preds[range(len(all_preds)), prob.argmax(dim=-1)]
    else:  # oracle
        pred = (all_preds * P).sum(dim=1)

    if transport_method == "router":
        loss = loss_fn(pred, label)
    else:
        loss = (all_loss * P).sum(dim=1).mean() if mask.sum() > 0 else torch.tensor(0.0, device=all_preds.device)

    return loss, pred, L, P


# ---------------------------------------------------------------------------
# TRA 配置与训练接口
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from torch.utils.data import DataLoader, Dataset


@dataclass
class TRAConfig:
    """TRA 训练超参数。"""

    hidden_size: int = 64
    num_layers: int = 2
    num_states: int = 3
    router_hidden: int = 8
    tau: float = 1.0
    src_info: str = "LR_TPE"
    seq_len: int = 20
    dropout: float = 0.1
    use_attn: bool = True
    lr: float = 1e-3
    epochs: int = 100
    patience: int = 15
    batch_size: int = 2048
    weight_decay: float = 1e-4
    alpha: float = 1.0  # transport loss fusion
    lamb: float = 1.0  # router regularization weight
    rho: float = 0.99  # lambda decay
    pretrain_epochs: int = 20  # pretrain backbone + predictors (oracle transport)
    memory_len: int = 5  # 历史 loss 记忆长度
    transport_method: str = "router"  # "router" | "oracle" | "none"


class TRATemporalDataset(Dataset):
    """TRA 时序数据集 (支持 loss memory)。"""

    def __init__(
        self,
        feature_arrays: dict[str, np.ndarray],
        date_indices: dict[str, dict],
        samples: list[tuple[str, object, float]],
        seq_len: int,
        num_states: int = 3,
        memory_len: int = 5,
    ):
        self.features = feature_arrays
        self.date_idx = date_indices
        self.samples = samples
        self.seq_len = seq_len
        self.num_states = num_states
        self.memory_len = memory_len
        # 历史 loss 记忆: per sample
        self.loss_memory = np.zeros(
            (len(samples), memory_len, num_states), dtype=np.float32
        )

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        sym, date_key, label = self.samples[idx]
        arr = self.features[sym]
        i = self.date_idx[sym][date_key]
        start = max(0, i - self.seq_len + 1)
        seq = arr[start: i + 1]
        n_feat = arr.shape[1]
        if len(seq) < self.seq_len:
            pad = np.zeros((self.seq_len - len(seq), n_feat), dtype=np.float32)
            seq = np.concatenate([pad, seq], axis=0)
        return (
            torch.from_numpy(seq.astype(np.float32)),
            torch.tensor([label], dtype=torch.float32),
            torch.from_numpy(self.loss_memory[idx].copy()),
            idx,
        )

    def update_memory(self, indices: np.ndarray, new_loss: np.ndarray):
        """更新 loss memory (滚动窗口)。

        Args:
            indices: sample indices [N]
            new_loss: 新的 per-predictor loss [N, num_states]
        """
        # 向左滚动并在末尾追加
        self.loss_memory[indices, :-1] = self.loss_memory[indices, 1:]
        self.loss_memory[indices, -1] = new_loss


# ---------------------------------------------------------------------------
# TRA Wrapper (WF pipeline 接口)
# ---------------------------------------------------------------------------


class _TRAWrapper:
    """适配 Walk-Forward 训练管道的 TRA wrapper。提供 predict() 接口。"""

    def __init__(self, model: TRARanker, cfg: TRAConfig, device: torch.device):
        self.model = model
        self.cfg = cfg
        self.device = device
        self._feature_names: list[str] = []

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Flat 2D predict: 单时步作为 length-1 序列，无历史 loss。"""
        X_3d = X.reshape(X.shape[0], 1, X.shape[1]).astype(np.float32)
        self.model.eval()
        with torch.no_grad():
            t = torch.from_numpy(X_3d).to(self.device)
            scores = self.model.predict(t, hist_loss=None)
            return scores.cpu().numpy()

    def predict_temporal(
        self, oos_df: pd.DataFrame, feature_cols: list[str], full_df: pd.DataFrame,
    ) -> np.ndarray:
        """Build temporal sequences from full_df, predict for oos_df rows."""
        from .gru_ranker import build_feature_store
        store, date_idx = build_feature_store(full_df, feature_cols)
        return self.predict_temporal_fast(oos_df, feature_cols, (store, date_idx))

    def predict_temporal_fast(
        self,
        oos_df: pd.DataFrame,
        feature_cols: list[str],
        prebuilt_store: tuple[dict[str, np.ndarray], dict[str, dict]],
    ) -> np.ndarray:
        """Predict using a pre-built feature store."""
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

        ds = TRATemporalDataset(
            store, date_idx, valid_samples,
            self.cfg.seq_len, self.cfg.num_states, self.cfg.memory_len,
        )
        loader = DataLoader(ds, batch_size=self.cfg.batch_size * 2, shuffle=False)
        scores = self._predict_loader(loader)

        result = np.zeros(len(samples), dtype=np.float32)
        j = 0
        for i, s in enumerate(samples):
            if s is not None:
                result[i] = scores[j]
                j += 1
        return result

    def _predict_loader(self, loader: DataLoader) -> np.ndarray:
        self.model.eval()
        parts: list[np.ndarray] = []
        with torch.no_grad():
            for X_batch, _, hist_batch, _ in loader:
                scores = self.model.predict(
                    X_batch.to(self.device), hist_batch.to(self.device)
                )
                parts.append(scores.cpu().numpy())
        return np.concatenate(parts) if parts else np.array([], dtype=np.float32)


# ---------------------------------------------------------------------------
# 训练入口
# ---------------------------------------------------------------------------


def train_tra_one_fold(
    train_df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    cfg: TRAConfig,
    full_df: pd.DataFrame | None = None,
    fold_idx: int = 0,
    prebuilt_store: tuple | None = None,
) -> _TRAWrapper:
    """Train TRA model for one walk-forward fold.

    训练分两阶段:
    1. Pretrain: backbone + predictors 用 oracle transport 训练
    2. Main: 加入 router, 用 router transport 训练
    """
    from .gru_ranker import build_feature_store

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(
        "TRA fold %d — device=%s, states=%d, seq=%d, transport=%s",
        fold_idx, device, cfg.num_states, cfg.seq_len, cfg.transport_method,
    )

    # Build feature store
    if prebuilt_store is not None:
        store, date_idx = prebuilt_store
    else:
        src_df = full_df if full_df is not None else train_df
        store, date_idx = build_feature_store(src_df, feature_cols)

    # Build samples
    train_samples = []
    for _, row in train_df.iterrows():
        sym, dt = row["symbol"], row["datetime"]
        if sym in date_idx and dt in date_idx[sym]:
            train_samples.append((sym, dt, float(row[label_col])))
    if not train_samples:
        raise ValueError("No valid training samples for TRA fold")

    # Train-val split
    n_val = max(1, int(len(train_samples) * 0.15))
    val_samples = train_samples[-n_val:]
    trn_samples = train_samples[:-n_val]
    logger.info("  train=%d, val=%d samples", len(trn_samples), len(val_samples))

    trn_ds = TRATemporalDataset(
        store, date_idx, trn_samples, cfg.seq_len, cfg.num_states, cfg.memory_len
    )
    val_ds = TRATemporalDataset(
        store, date_idx, val_samples, cfg.seq_len, cfg.num_states, cfg.memory_len
    )

    n_features = len(feature_cols)
    model = TRARanker(
        input_size=n_features,
        hidden_size=cfg.hidden_size,
        num_layers=cfg.num_layers,
        num_states=cfg.num_states,
        router_hidden=cfg.router_hidden,
        tau=cfg.tau,
        seq_len=cfg.seq_len,
        dropout=cfg.dropout,
        use_attn=cfg.use_attn,
        src_info=cfg.src_info,
    ).to(device)
    logger.info("  model params: %d", sum(p.numel() for p in model.parameters()))

    # --- Phase 1: Pretrain with oracle transport ---
    if cfg.pretrain_epochs > 0 and cfg.transport_method != "none":
        logger.info("  Phase 1: pretrain %d epochs (oracle transport)", cfg.pretrain_epochs)
        _train_phase(
            model, trn_ds, val_ds, cfg, device,
            n_epochs=cfg.pretrain_epochs,
            transport_method="oracle",
            train_router=False,
        )

    # --- Phase 2: Main training with router ---
    logger.info("  Phase 2: main training %d epochs (method=%s)", cfg.epochs, cfg.transport_method)
    best_state = _train_phase(
        model, trn_ds, val_ds, cfg, device,
        n_epochs=cfg.epochs,
        transport_method=cfg.transport_method,
        train_router=True,
    )

    if best_state is not None:
        model.load_state_dict(best_state)
    model.to(device)

    wrapper = _TRAWrapper(model, cfg, device)
    wrapper._feature_names = list(feature_cols)
    return wrapper


def _train_phase(
    model: TRARanker,
    trn_ds: TRATemporalDataset,
    val_ds: TRATemporalDataset,
    cfg: TRAConfig,
    device: torch.device,
    n_epochs: int,
    transport_method: str,
    train_router: bool,
) -> dict | None:
    """TRA 训练单阶段 (pretrain 或 main training)。

    Returns:
        best_state: 最佳模型状态字典 (或 None)
    """
    # 决定哪些参数需要梯度
    params = list(model.parameters())
    if not train_router and hasattr(model.tra, 'router'):
        # Pretrain 阶段: 冻结 router
        for p in model.tra.router.parameters():
            p.requires_grad_(False)
        for p in model.tra.fc.parameters():
            p.requires_grad_(False)
        params = [p for p in model.parameters() if p.requires_grad]

    optimizer = torch.optim.AdamW(params, lr=cfg.lr, weight_decay=cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=n_epochs, eta_min=cfg.lr * 0.01,
    )

    best_val_ic = -float("inf")
    best_state = None
    patience_cnt = 0
    global_step = 0

    trn_loader = DataLoader(trn_ds, batch_size=cfg.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=cfg.batch_size * 2, shuffle=False)

    for epoch in range(n_epochs):
        model.train()
        losses = []
        for X_b, y_b, hist_b, idx_b in trn_loader:
            X_b = X_b.to(device)
            y_b = y_b.to(device).squeeze(-1)
            hist_b = hist_b.to(device)

            preds, choice, prob = model(X_b, hist_b)

            if transport_method != "none" and cfg.num_states > 1:
                hist_mean = hist_b.mean(dim=1)  # [B, K]
                loss, _, L, P = transport_sample(
                    preds, y_b, choice, prob, hist_mean,
                    transport_method=transport_method,
                    alpha=cfg.alpha,
                    training=True,
                )
                # Update memory
                trn_ds.update_memory(
                    idx_b.numpy(), L.detach().cpu().numpy()
                )
                # Router regularization
                if train_router and prob is not None:
                    decay = cfg.rho ** (global_step // 100)
                    reg = prob.log().mul(P.detach()).sum(dim=1).mean()
                    loss = loss - cfg.lamb * decay * reg
            else:
                # Simple mean prediction
                pred = preds.mean(dim=1) if preds.dim() > 1 else preds.squeeze(-1)
                loss = loss_fn(pred, y_b)

            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(loss.item())
            global_step += 1

        scheduler.step()

        # Validation: compute IC
        val_ic = _evaluate_ic(model, val_loader, device, cfg)

        if val_ic > best_val_ic:
            best_val_ic = val_ic
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1

        if epoch % 10 == 0 or patience_cnt == 0:
            logger.info(
                "    epoch %d: loss=%.5f val_IC=%.4f best=%.4f pat=%d",
                epoch, np.mean(losses), val_ic, best_val_ic, patience_cnt,
            )
        if patience_cnt >= cfg.patience:
            logger.info("    early stop at epoch %d", epoch)
            break

    # 恢复 router 梯度
    if not train_router and hasattr(model.tra, 'router'):
        for p in model.tra.router.parameters():
            p.requires_grad_(True)
        for p in model.tra.fc.parameters():
            p.requires_grad_(True)

    return best_state


def _evaluate_ic(
    model: TRARanker,
    loader: DataLoader,
    device: torch.device,
    cfg: TRAConfig,
) -> float:
    """计算验证集 IC (Spearman rank correlation)。"""
    model.eval()
    all_preds = []
    all_labels = []
    with torch.no_grad():
        for X_b, y_b, hist_b, _ in loader:
            X_b = X_b.to(device)
            hist_b = hist_b.to(device)
            scores = model.predict(X_b, hist_b)
            all_preds.append(scores.cpu().numpy())
            all_labels.append(y_b.numpy().ravel())

    preds = np.concatenate(all_preds)
    labels = np.concatenate(all_labels)
    # Pearson IC on ranks
    mask = ~np.isnan(labels)
    if mask.sum() < 10:
        return 0.0
    p_rank = preds[mask].argsort().argsort().astype(float)
    l_rank = labels[mask].argsort().argsort().astype(float)
    ic = np.corrcoef(p_rank, l_rank)[0, 1]
    return float(ic) if np.isfinite(ic) else 0.0
