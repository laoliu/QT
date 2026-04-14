"""GAT 因子排名模型单元测试。"""

from __future__ import annotations

import pytest
import numpy as np
import pandas as pd
import tempfile
from pathlib import Path

torch = pytest.importorskip("torch")
pyg = pytest.importorskip("torch_geometric")

from aipha.models.gat_ranker import (
    GATRankerConfig,
    GATFactorRanker,
    GATNetwork,
    GATBlock,
    IndustryGraphBuilder,
)


# ── 测试数据工厂 ──

def make_cross_section(
    n_stocks: int = 20, n_dates: int = 5, n_features: int = 10,
) -> pd.DataFrame:
    """生成合成截面数据"""
    rng = np.random.RandomState(42)
    rows = []
    industries = ["银行", "地产", "科技", "医药"]
    for d in range(n_dates):
        dt = f"2025010{d + 1}"
        for s in range(n_stocks):
            row = {"trade_date": dt, "ts_code": f"{s:06d}.SZ"}
            row["industry"] = industries[s % len(industries)]
            for f in range(n_features):
                row[f"f{f}"] = rng.randn()
            row["fwd_ret_rank"] = rng.rand()
            rows.append(row)
    return pd.DataFrame(rows)


# ── 配置测试 ──

class TestGATRankerConfig:
    def test_defaults(self):
        c = GATRankerConfig()
        assert c.input_dim == 30
        assert c.n_heads == 4
        assert c.n_layers == 2

    def test_custom(self):
        c = GATRankerConfig(input_dim=10, hidden_dim=32, n_heads=2)
        assert c.input_dim == 10
        assert c.hidden_dim == 32


# ── 图构建测试 ──

class TestIndustryGraphBuilder:
    def test_basic_graph(self):
        df = pd.DataFrame({
            "industry": ["银行", "银行", "科技", "科技"],
            "f1": [1.0, 2.0, 3.0, 4.0],
        })
        gb = IndustryGraphBuilder()
        ei, n = gb.build_graph(df, "20250101")
        assert n == 4
        assert ei.shape[0] == 2
        assert ei.shape[1] > 0  # 有边

    def test_no_industry_col(self):
        df = pd.DataFrame({"f1": [1.0, 2.0, 3.0]})
        gb = IndustryGraphBuilder()
        ei, n = gb.build_graph(df, "20250101")
        assert n == 3
        # 自连接
        assert ei.shape[1] == 3

    def test_single_stock(self):
        df = pd.DataFrame({"industry": ["银行"], "f1": [1.0]})
        gb = IndustryGraphBuilder()
        ei, n = gb.build_graph(df, "20250101")
        assert n == 1


# ── GAT 网络结构测试 ──

class TestGATNetwork:
    def test_forward_shape(self):
        config = GATRankerConfig(input_dim=10, hidden_dim=8, n_heads=2, n_layers=2)
        net = GATNetwork(config)
        x = torch.randn(5, 10)
        ei = torch.tensor([[0, 1, 2, 3], [1, 0, 3, 2]], dtype=torch.long)
        out = net(x, ei)
        assert out.shape == (5,)

    def test_output_is_float(self):
        config = GATRankerConfig(input_dim=5, hidden_dim=4, n_heads=1, n_layers=1)
        net = GATNetwork(config)
        x = torch.randn(3, 5)
        ei = torch.tensor([[0, 1], [1, 0]], dtype=torch.long)
        out = net(x, ei)
        assert out.dtype == torch.float32


# ── 完整训练/推理测试 ──

class TestGATFactorRanker:
    def test_fit_predict(self):
        df = make_cross_section(n_stocks=10, n_dates=3, n_features=5)
        config = GATRankerConfig(
            input_dim=5, hidden_dim=8, n_heads=2, n_layers=1,
            epochs=3, patience=5,
        )
        ranker = GATFactorRanker(config)
        result = ranker.fit(df)
        assert "best_loss" in result
        assert result["epochs"] >= 1

        scores = ranker.predict(df)
        assert scores.shape == (len(df),)
        assert not np.all(np.isnan(scores))

    def test_save_load_roundtrip(self):
        df = make_cross_section(n_stocks=8, n_dates=2, n_features=4)
        config = GATRankerConfig(
            input_dim=4, hidden_dim=4, n_heads=1, n_layers=1, epochs=2,
        )
        ranker = GATFactorRanker(config)
        ranker.fit(df)
        scores_before = ranker.predict(df)

        with tempfile.TemporaryDirectory() as tmp:
            ranker.save(tmp)
            loaded = GATFactorRanker.load(tmp)
            scores_after = loaded.predict(df)

        np.testing.assert_allclose(scores_before, scores_after, atol=1e-5)

    def test_missing_label_raises(self):
        df = pd.DataFrame({"trade_date": ["20250101"], "ts_code": ["000001.SZ"]})
        ranker = GATFactorRanker()
        with pytest.raises(ValueError, match="Label column"):
            ranker.fit(df)

    def test_predict_before_fit_raises(self):
        ranker = GATFactorRanker()
        df = pd.DataFrame({"trade_date": ["20250101"], "f1": [1.0]})
        with pytest.raises(RuntimeError, match="not trained"):
            ranker.predict(df)

    def test_early_stopping(self):
        df = make_cross_section(n_stocks=6, n_dates=2, n_features=3)
        config = GATRankerConfig(
            input_dim=3, hidden_dim=4, n_heads=1, n_layers=1,
            epochs=100, patience=2,
        )
        ranker = GATFactorRanker(config)
        result = ranker.fit(df)
        # 应该在 100 个 epoch 前停止
        assert result["epochs"] <= 100
