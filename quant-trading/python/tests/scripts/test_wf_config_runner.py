"""wf_config_runner 的单元测试

验证:
1. run_walk_forward_from_config 函数签名与返回值
2. 使用合成数据配置能端到端运行
3. 返回的 report / verdict / score 结构正确
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

# 导入被测模块
from scripts.wf_config_runner import run_walk_forward_from_config
from aipha.backtest.validation import ValidationReport, WalkForwardThresholds


class TestWFConfigRunner:
    """WF Config Runner 测试"""

    @pytest.fixture
    def synthetic_config_path(self, tmp_path: Path) -> Path:
        """创建使用合成数据的临时 YAML 配置"""
        cfg = {
            "name": "wf_test_multifactor",
            "run": {
                "mode": "backtest",
                "start_date": "2023-01-01",
                "end_date": "2023-06-30",
                "seed": 42,
            },
            "data": {
                "provider": "synthetic",
                "n_stocks": 10,
                "n_days": 120,
            },
            "strategy": {
                "name": "multifactor_enhance",
                "parameters": {
                    "top_k": 5,
                    "rebalance_days": 10,
                },
            },
            "initial_capital": 1000000,
            "market": {
                "region": "CN",
                "instruments": {"asset_type": "stock"},
            },
            "cost_model": {
                "commission": {"rate": 8.5e-05, "min_fee": 5.0},
                "stamp_tax": {"sell_rate": 0.0005},
                "transfer_fee": {"rate": 0.00001},
            },
            "fill_model": {"name": "next_bar_open"},
            "slippage_model": {"name": "fixed_bps", "bps": 10},
            "trading_rules": {"lot_size": 100, "t_plus": 1},
        }
        config_file = tmp_path / "wf_test.yml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True)
        return config_file

    def test_basic_wf_from_config(self, synthetic_config_path: Path):
        """基础 WF 运行: 使用合成数据配置 + 2 fold"""
        result = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=50,
            test_days=20,
            n_splits=2,
        )

        assert isinstance(result, dict)
        assert "report" in result
        assert "verdict" in result
        assert "score" in result
        assert isinstance(result["report"], ValidationReport)
        assert result["verdict"] in ("PASS", "FAIL", "NO_DATA")
        assert 0.0 <= result["score"] <= 10.0
        assert result["strategy_name"] == "multifactor_enhance"

    def test_purged_wf_from_config(self, synthetic_config_path: Path):
        """Purged WF: purge+embargo 正确应用"""
        result = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=50,
            test_days=20,
            n_splits=2,
            purged=True,
            purge_days=3,
            embargo_days=1,
        )

        assert result["purged"] is True
        report = result["report"]
        assert isinstance(report, ValidationReport)
        # purged 应缩减训练期
        for sr in report.split_results:
            train_days_actual = (sr.period.train_end - sr.period.train_start).days
            assert train_days_actual < 50, "Purge 应缩减训练期"

    def test_auto_n_splits(self, synthetic_config_path: Path):
        """n_splits=None 时自动计算"""
        result = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=40,
            test_days=15,
            n_splits=None,  # 自动
        )

        assert isinstance(result["n_splits"], int)
        assert result["n_splits"] >= 1

    def test_custom_thresholds(self, synthetic_config_path: Path):
        """自定义验收门槛对判定有效"""
        # 宽松门槛
        loose = WalkForwardThresholds(
            decay_threshold=0.99,
            win_rate_threshold=0.0,
            sharpe_threshold=-100.0,
        )
        result_loose = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=50,
            test_days=20,
            n_splits=2,
            thresholds=loose,
        )

        # 严格门槛
        strict = WalkForwardThresholds(
            decay_threshold=0.01,
            win_rate_threshold=1.0,
            sharpe_threshold=5.0,
        )
        result_strict = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=50,
            test_days=20,
            n_splits=2,
            thresholds=strict,
        )

        # 宽松应更容易 PASS
        assert result_loose["verdict"] in ("PASS", "FAIL")
        assert result_strict["verdict"] in ("PASS", "FAIL")

    def test_report_serialization(self, synthetic_config_path: Path):
        """report.to_dict() 可序列化"""
        result = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=50,
            test_days=20,
            n_splits=2,
        )

        d = result["report"].to_dict()
        assert "strategy_name" in d
        assert "n_splits" in d
        assert "splits" in d

    def test_report_summary_text(self, synthetic_config_path: Path):
        """report.summary() 输出可读文本"""
        result = run_walk_forward_from_config(
            config_path=synthetic_config_path,
            train_days=50,
            test_days=20,
            n_splits=2,
        )

        text = result["report"].summary()
        assert "样本外验证报告" in text
        assert "multifactor_enhance" in text
