"""tests/scripts/test_deep_fundamental_factors.py

测试 train_ml_factor.py 中深度基本面因子加载+工程的核心函数。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from train_ml_factor import (
    _DEEP_FINA_COLS,
    _add_deep_fundamental_factors,
    _load_fina_indicator_bulk,
    _pit_merge_fina,
)


# -- fixtures ---------------------------------------------------------------

@pytest.fixture
def sample_fina(tmp_path: Path) -> tuple[Path, list[str]]:
    """创建模拟 fina_indicator parquet 文件。"""
    symbols = ["000001.SZ", "000002.SZ"]
    for sym in symbols:
        fname = f"fina_indicator_{sym.replace('.', '_')}.parquet"
        rows = []
        for ann, end, roe, roa in [
            ("20230415", "20230331", 8.0, 2.5),
            ("20230825", "20230630", 5.5, 1.8),
            ("20231028", "20230930", 7.2, 2.3),
        ]:
            rows.append({
                "ts_code": sym, "ann_date": ann, "end_date": end,
                "roe": roe, "roa": roa, "grossprofit_margin": 30.0,
                "netprofit_margin": 15.0, "debt_to_assets": 50.0,
                "current_ratio": 1.5, "quick_ratio": 1.2,
                "dt_netprofit_yoy": 10.0, "op_yoy": 8.0,
                "cfps": 0.5, "eps": 0.8, "bps": 5.0,
            })
        df = pd.DataFrame(rows)
        df.to_parquet(tmp_path / fname)
    return tmp_path, symbols


@pytest.fixture
def sample_daily() -> pd.DataFrame:
    """创建模拟 daily DataFrame。"""
    dates = pd.date_range("2023-04-01", "2023-11-30", freq="B")
    rows = []
    for sym in ["000001.SZ", "000002.SZ"]:
        for d in dates:
            rows.append({"datetime": d, "symbol": sym, "close": 10.0 + np.random.randn() * 0.5})
    return pd.DataFrame(rows).sort_values(["symbol", "datetime"]).reset_index(drop=True)


# -- tests -------------------------------------------------------------------

class TestLoadFinaIndicatorBulk:
    """_load_fina_indicator_bulk 测试。"""

    def test_load_basic(self, sample_fina):
        """能正确加载 fina_indicator parquet。"""
        cache_dir, symbols = sample_fina
        result = _load_fina_indicator_bulk(cache_dir, symbols)
        assert not result.empty
        assert "roe" in result.columns
        assert "symbol" in result.columns
        assert result["symbol"].nunique() == 2

    def test_empty_cache(self, tmp_path: Path):
        """空目录返回空 DataFrame。"""
        result = _load_fina_indicator_bulk(tmp_path, ["000001.SZ"])
        assert result.empty

    def test_dedup_by_ann_date(self, sample_fina):
        """同 symbol+ann_date 去重取最新 end_date。"""
        cache_dir, symbols = sample_fina
        result = _load_fina_indicator_bulk(cache_dir, symbols)
        # 每个 symbol 有 3 个不同 ann_date
        per_sym = result.groupby("symbol").size()
        assert (per_sym == 3).all()


class TestPITMerge:
    """_pit_merge_fina PIT 对齐测试。"""

    def test_merge_basic(self, sample_fina, sample_daily):
        """基本 PIT 合并。"""
        cache_dir, symbols = sample_fina
        fina = _load_fina_indicator_bulk(cache_dir, symbols)
        merged = _pit_merge_fina(sample_daily, fina)
        assert len(merged) == len(sample_daily)
        assert "roe" in merged.columns

    def test_empty_fina(self, sample_daily):
        """空 fina 返回原始 daily。"""
        result = _pit_merge_fina(sample_daily, pd.DataFrame())
        assert len(result) == len(sample_daily)

    def test_pit_no_future_leak(self, sample_fina):
        """PIT: ann_date > trade_date 的财报不被使用。"""
        cache_dir, symbols = sample_fina
        fina = _load_fina_indicator_bulk(cache_dir, symbols)
        # 日期在 2023-04-10 (ann_date=20230415 之前)
        early = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-04-10")] * 2,
            "symbol": symbols, "close": [10.0, 11.0],
        })
        merged = _pit_merge_fina(early, fina)
        # 2023-04-10 < ann_date 2023-04-15 → roe 应该是 NaN
        assert merged["roe"].isna().all()


class TestDeepFundamentalFactors:
    """_add_deep_fundamental_factors 集成测试。"""

    def test_returns_factor_count(self, sample_fina, sample_daily):
        """返回新增因子数 > 0。"""
        cache_dir, _ = sample_fina
        count = _add_deep_fundamental_factors(sample_daily.copy(), cache_dir)
        assert count > 0

    def test_no_fina_data_returns_zero(self, tmp_path: Path, sample_daily):
        """无 fina 数据时返回 0。"""
        count = _add_deep_fundamental_factors(sample_daily.copy(), tmp_path)
        assert count == 0
