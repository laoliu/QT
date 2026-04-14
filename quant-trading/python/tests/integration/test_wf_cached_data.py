"""Walk-Forward + 真实缓存数据 集成测试

使用 data/cache/tushare 中的缓存日线数据验证 WF 链路。
如果缓存数据不存在则自动跳过。
"""
from __future__ import annotations

from pathlib import Path

import pytest

# 检查是否有缓存数据
_CACHE_DIR = Path("data/cache/tushare")
_HAS_CACHE = _CACHE_DIR.exists() and any(_CACHE_DIR.glob("*.parquet"))
_skip_reason = "需要 data/cache/tushare 中的本地缓存数据"

# 检查 dual_ma.yml 配置
_DUAL_MA_CFG = Path("configs/backtest/dual_ma.yml")
_HAS_CONFIG = _DUAL_MA_CFG.exists()


@pytest.mark.skipif(not (_HAS_CACHE and _HAS_CONFIG), reason=_skip_reason)
class TestWFWithCachedData:
    """Walk-Forward 缓存数据集成测试"""

    def test_dual_ma_wf_completes(self):
        """dual_ma 策略 WF 能加载缓存数据并运行完整链路"""
        from scripts.wf_config_runner import run_walk_forward_from_config

        result = run_walk_forward_from_config(
            config_path=str(_DUAL_MA_CFG),
            train_days=252,
            test_days=63,
            n_splits=2,
        )

        # 基本结构验证
        assert "report" in result
        assert "verdict" in result
        assert "score" in result
        assert result["strategy_name"] == "demo_dual_ma"
        assert result["n_splits"] == 2
        assert result["n_trading_days"] > 200  # 至少有 200 交易日

    def test_dual_ma_wf_report_has_splits(self):
        """WF 报告包含正确的折数明细"""
        from scripts.wf_config_runner import run_walk_forward_from_config

        result = run_walk_forward_from_config(
            config_path=str(_DUAL_MA_CFG),
            train_days=252,
            test_days=63,
            n_splits=2,
        )

        report = result["report"]
        # report 应有 to_dict 方法
        rd = report.to_dict()
        assert "splits" in rd
        assert len(rd["splits"]) == 2

        # 每折都有 train/test 指标
        for split in rd["splits"]:
            assert "train_return" in split
            assert "test_return" in split
            assert "train_sharpe" in split
            assert "test_sharpe" in split

    def test_dual_ma_wf_score_range(self):
        """WF 评分在合理范围 [0, 10]"""
        from scripts.wf_config_runner import run_walk_forward_from_config

        result = run_walk_forward_from_config(
            config_path=str(_DUAL_MA_CFG),
            train_days=252,
            test_days=63,
            n_splits=2,
        )

        assert 0 <= result["score"] <= 10
        assert result["verdict"] in ("PASS", "FAIL", "WARN")

    def test_dual_ma_purged_wf(self):
        """Purged WF 模式正常运行"""
        from scripts.wf_config_runner import run_walk_forward_from_config

        result = run_walk_forward_from_config(
            config_path=str(_DUAL_MA_CFG),
            train_days=252,
            test_days=63,
            n_splits=2,
            purged=True,
            purge_days=5,
            embargo_days=2,
        )

        assert result["purged"] is True
        assert result["score"] >= 0
        assert result["verdict"] in ("PASS", "FAIL", "WARN")

    def test_wf_data_range_correct(self):
        """WF 数据范围覆盖配置的日期区间"""
        from scripts.wf_config_runner import run_walk_forward_from_config

        result = run_walk_forward_from_config(
            config_path=str(_DUAL_MA_CFG),
            train_days=252,
            test_days=63,
            n_splits=2,
        )

        # dual_ma.yml: 2020-01-01 ~ 2024-12-31
        assert "2020" in result["data_range"]
        assert "2024" in result["data_range"]
