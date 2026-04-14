"""WF batch runner 单元测试。

使用 mock 替换重量级依赖（_load_data、_create_*），
验证 batch 编排逻辑（多配置串行、错误处理、结果聚合）。
"""

from __future__ import annotations

import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))


# ============================================================================
# Fixtures
# ============================================================================

def _make_fake_bars(start: date, end: date):
    """生成假的 bars dict，key 为日期，value 为空列表。"""
    from datetime import timedelta
    bars = {}
    d = start
    while d <= end:
        if d.weekday() < 5:  # 跳过周末
            bars[d] = []
        d += timedelta(days=1)
    return bars


def _make_fake_config():
    """构造兼容 wf_config_runner 的最小 config mock。"""
    cfg = MagicMock()
    cfg.strategy.name = "test_strategy"
    cfg.risk.benchmark_symbol = None
    return cfg


# ============================================================================
# Tests
# ============================================================================


class TestRunBatchWalkForward:
    """run_batch_walk_forward 批量运行测试。"""

    @patch("wf_config_runner.compute_drawdown_analysis")
    @patch("wf_config_runner.compute_performance_metrics")
    @patch("wf_config_runner._load_data")
    @patch("wf_config_runner._create_strategy")
    @patch("wf_config_runner._create_engine_components")
    @patch("wf_config_runner._build_universe_policy")
    @patch("wf_config_runner._build_risk_manager")
    @patch("wf_config_runner._build_stop_loss_manager")
    @patch("wf_config_runner._build_market_regime_detector")
    @patch("wf_config_runner._build_weight_optimizer")
    @patch("wf_config_runner.load_config")
    @patch("wf_config_runner.validate_config")
    @patch("wf_config_runner.to_backtest_config")
    @patch("wf_config_runner.BacktestEngine")
    def test_batch_single_config_success(
        self,
        mock_engine_cls,
        mock_to_bt_config,
        mock_validate,
        mock_load_config,
        mock_build_weight_opt,
        mock_build_regime,
        mock_build_sl,
        mock_build_risk,
        mock_build_universe,
        mock_create_components,
        mock_create_strategy,
        mock_load_data,
        mock_perf,
        mock_dd,
    ):
        """单个配置的批量运行。"""
        from wf_config_runner import run_batch_walk_forward

        # Setup mocks
        mock_validate.return_value = []
        mock_load_config.return_value = _make_fake_config()
        mock_load_data.return_value = _make_fake_bars(
            date(2020, 1, 1), date(2024, 12, 31)
        )
        mock_create_components.return_value = (
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )

        # Performance / drawdown mocks
        perf = MagicMock()
        perf.total_return = 0.10
        perf.sharpe_ratio = 1.2
        mock_perf.return_value = perf

        dd = MagicMock()
        dd.max_drawdown_pct = 0.05
        mock_dd.return_value = dd

        mock_engine_cls.return_value.run.return_value = MagicMock()

        results = run_batch_walk_forward(
            ["fake_config.yml"],
            train_days=252,
            test_days=63,
        )

        assert len(results) == 1
        assert "report" in results[0]
        assert results[0]["strategy_name"] == "test_strategy"

    def test_batch_empty_list(self):
        """空配置列表应返回空结果。"""
        from wf_config_runner import run_batch_walk_forward

        results = run_batch_walk_forward([])
        assert results == []

    @patch("wf_config_runner.load_config")
    @patch("wf_config_runner.validate_config")
    def test_batch_error_handling(self, mock_validate, mock_load_config):
        """配置加载失败时，默认跳过继续。"""
        from wf_config_runner import run_batch_walk_forward

        mock_validate.return_value = ["invalid config"]
        mock_load_config.return_value = _make_fake_config()

        results = run_batch_walk_forward(
            ["bad1.yml", "bad2.yml"],
            stop_on_error=False,
        )

        assert len(results) == 2
        assert results[0]["verdict"] == "ERROR"
        assert results[1]["verdict"] == "ERROR"
        assert "error" in results[0]

    @patch("wf_config_runner.load_config")
    @patch("wf_config_runner.validate_config")
    def test_batch_stop_on_error(self, mock_validate, mock_load_config):
        """stop_on_error=True 时，第一个失败就抛异常。"""
        from wf_config_runner import run_batch_walk_forward

        mock_validate.return_value = ["invalid"]
        mock_load_config.return_value = _make_fake_config()

        with pytest.raises(ValueError):
            run_batch_walk_forward(
                ["bad.yml"],
                stop_on_error=True,
            )

    @patch("wf_config_runner.load_config")
    @patch("wf_config_runner.validate_config")
    def test_batch_mixed_success_and_error(self, mock_validate, mock_load_config):
        """混合成功和失败：第一个失败，结果里有 error 标记。"""
        from wf_config_runner import run_batch_walk_forward

        mock_validate.return_value = ["fail reason"]
        mock_load_config.return_value = _make_fake_config()

        results = run_batch_walk_forward(
            ["fail1.yml", "fail2.yml", "fail3.yml"],
            stop_on_error=False,
        )

        assert len(results) == 3
        for r in results:
            assert r["verdict"] == "ERROR"
            assert r["score"] == 0.0
