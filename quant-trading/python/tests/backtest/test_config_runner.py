"""Tests for configuration-driven backtest runner.

Coverage targets:
- Config parsing from YAML/dict
- Config validation
- Config serialization
- Config template creation
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
import yaml

from aipha.backtest.config_runner import (
    BacktestRunConfig,
    CostConfig,
    DataConfig,
    FillModelConfig,
    StrategyConfig,
    UniverseConfig,
    _normalize_config,
    _isolate_project_output_during_pytest,
    config_to_dict,
    create_config_template,
    load_config,
    parse_config,
    save_config,
    to_backtest_config,
    validate_config,
)


# ============================================================================
# Config Parsing Tests
# ============================================================================


class TestConfigParsing:
    """Tests for config parsing"""

    def test_parse_minimal_config(self):
        """Should parse minimal config with defaults"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        }

        config = parse_config(raw)

        assert config.name == "test"
        assert config.data.source == "synthetic"
        assert config.data.start_date == date(2024, 1, 1)
        assert config.data.end_date == date(2024, 12, 31)

    def test_parse_full_config(self):
        """Should parse full config"""
        raw = {
            "name": "full_test",
            "description": "A full test config",
            "data": {
                "source": "tushare",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "symbols": ["600000.SH", "000001.SZ"],
                "frequency": "1d",
                "adjust": "qfq",
            },
            "universe": {
                "policy": "hs300",
                "exclude_st": True,
                "exclude_suspended": True,
                "min_listing_days": 60,
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {"top_k": 10, "rebalance_days": 20},
            },
            "cost": {
                "commission_rate": 0.0001,
                "commission_min": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_bps": 5.0,
            },
            "initial_capital": 1000000.0,
            "lot_size": 100,
            "t_plus_n": 1,
            "random_seed": 42,
            "output_dir": "results",
        }

        config = parse_config(raw)

        assert config.name == "full_test"
        assert config.description == "A full test config"
        assert config.data.symbols == ["600000.SH", "000001.SZ"]
        assert config.universe.policy == "hs300"
        assert config.strategy.name == "demo_momentum"
        assert config.strategy.params["top_k"] == 10
        assert config.cost.slippage_bps == 5.0
        assert config.initial_capital == 1000000.0
        assert config.random_seed == 42


def test_project_backtest_output_is_redirected_only_during_pytest(monkeypatch, tmp_path):
    from aipha.utils.paths import PROJECT_ROOT

    project_output = PROJECT_ROOT / "output" / "must-not-be-written-by-test"
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/backtest/test_config_runner.py::case")
    redirected = _isolate_project_output_during_pytest(project_output)
    assert redirected != project_output
    assert redirected.is_relative_to(Path(tempfile.gettempdir()))
    assert not project_output.exists()

    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert _isolate_project_output_during_pytest(project_output) == project_output
    tmp_output = tmp_path / "explicit-output"
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/backtest/test_config_runner.py::case")
    assert _isolate_project_output_during_pytest(tmp_output) == tmp_output

    def test_parse_date_formats(self):
        """Should handle different date formats"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": date(2024, 1, 1),  # date object
                "end_date": "2024-12-31",  # string
            },
        }

        config = parse_config(raw)

        assert config.data.start_date == date(2024, 1, 1)
        assert config.data.end_date == date(2024, 12, 31)


# ============================================================================
# Load Config Tests
# ============================================================================


class TestLoadConfig:
    """Tests for loading config from files"""

    def test_load_yaml_config(self):
        """Should load config from YAML file"""
        config_content = """
name: yaml_test
data:
  source: synthetic
  start_date: '2024-01-01'
  end_date: '2024-12-31'
strategy:
  name: buy_and_hold
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            path = f.name

        try:
            config = load_config(path)
            assert config.name == "yaml_test"
            assert config.data.source == "synthetic"
            assert config.strategy.name == "buy_and_hold"
        finally:
            Path(path).unlink()

    def test_load_json_config(self):
        """Should load config from JSON file"""
        import json

        config_dict = {
            "name": "json_test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_dict, f)
            path = f.name

        try:
            config = load_config(path)
            assert config.name == "json_test"
        finally:
            Path(path).unlink()

    def test_load_nonexistent_file(self):
        """Should raise FileNotFoundError for missing file"""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")


# ============================================================================
# Config Validation Tests
# ============================================================================


class TestConfigValidation:
    """Tests for config validation"""

    def test_valid_config(self):
        """Valid config should have no errors"""
        config = create_config_template()
        errors = validate_config(config)
        assert errors == []

    def test_invalid_date_range(self):
        """Should catch invalid date range"""
        config = create_config_template()
        config.data.start_date = date(2024, 12, 31)
        config.data.end_date = date(2024, 1, 1)

        errors = validate_config(config)

        assert len(errors) == 1
        assert "start_date" in errors[0]

    def test_invalid_capital(self):
        """Should catch invalid capital"""
        config = create_config_template()
        config.initial_capital = -1000.0

        errors = validate_config(config)

        assert len(errors) == 1
        assert "initial_capital" in errors[0]

    def test_invalid_commission_rate(self):
        """Should catch negative commission rate"""
        config = create_config_template()
        config.cost.commission_rate = -0.001

        errors = validate_config(config)

        assert len(errors) == 1
        assert "commission_rate" in errors[0]

    def test_unknown_slippage_model(self):
        """R87: Should catch unknown slippage model name"""
        config = create_config_template()
        config.cost.slippage_model = "nonexistent_model"

        errors = validate_config(config)

        assert any("slippage_model" in e for e in errors)
        assert any("nonexistent_model" in e for e in errors)

    def test_known_slippage_models_pass(self):
        """R87: Known slippage models should pass validation"""
        for model in ("fixed", "linear_impact", "square_root_impact", "none"):
            config = create_config_template()
            config.cost.slippage_model = model
            errors = validate_config(config)
            assert not any("slippage_model" in e for e in errors), (
                f"Model '{model}' should be valid"
            )

    def test_excessive_commission_rate(self):
        """R87: Commission rate > 1% should trigger error"""
        config = create_config_template()
        config.cost.commission_rate = 0.05  # 5%, absurd

        errors = validate_config(config)

        assert any("commission_rate" in e and "too high" in e for e in errors)

    def test_excessive_stamp_tax_rate(self):
        """R87: Stamp tax > 0.3% should trigger error"""
        config = create_config_template()
        config.cost.stamp_tax_rate = 0.01  # 1%, too high

        errors = validate_config(config)

        assert any("stamp_tax_rate" in e and "too high" in e for e in errors)

    def test_reasonable_rates_pass(self):
        """R87: Standard market rates should pass"""
        config = create_config_template()
        config.cost.commission_rate = 0.0000854  # 万0.85
        config.cost.stamp_tax_rate = 0.0005  # 万5
        config.cost.slippage_model = "square_root_impact"

        errors = validate_config(config)

        assert errors == []


# ============================================================================
# Config Serialization Tests
# ============================================================================


class TestConfigSerialization:
    """Tests for config serialization"""

    def test_config_to_dict(self):
        """Should convert config to dict"""
        config = create_config_template(name="test_config")
        d = config_to_dict(config)

        assert d["name"] == "test_config"
        assert "data" in d
        assert "strategy" in d
        assert "cost" in d

    def test_save_and_load_config(self):
        """Should round-trip config correctly"""
        config = create_config_template(name="roundtrip_test")

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name

        try:
            save_config(config, path)
            loaded = load_config(path)

            assert loaded.name == config.name
            assert loaded.data.source == config.data.source
            assert loaded.initial_capital == config.initial_capital
        finally:
            Path(path).unlink()


# ============================================================================
# Config Template Tests
# ============================================================================


class TestConfigTemplate:
    """Tests for config template creation"""

    def test_create_template_defaults(self):
        """Should create template with sensible defaults"""
        template = create_config_template()

        assert template.name == "my_backtest"
        assert template.data.source == "synthetic"
        assert template.initial_capital == 1_000_000.0
        assert template.cost.commission_rate == 8.54e-05  # §13.1 MarketRulesCNEquity 万0.85

    def test_create_template_custom_dates(self):
        """Should use custom dates"""
        template = create_config_template(
            name="custom",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )

        assert template.name == "custom"
        assert template.data.start_date == date(2023, 1, 1)
        assert template.data.end_date == date(2023, 12, 31)


# ============================================================================
# Backtest Config Conversion Tests
# ============================================================================


class TestBacktestConfigConversion:
    """Tests for converting to BacktestConfig"""

    def test_to_backtest_config(self):
        """Should convert run config to backtest config"""
        run_config = create_config_template()
        bt_config = to_backtest_config(run_config)

        assert bt_config.initial_capital == run_config.initial_capital
        assert bt_config.lot_size == run_config.lot_size
        assert bt_config.commission_rate == run_config.cost.commission_rate
        assert bt_config.stamp_tax_rate == run_config.cost.stamp_tax_rate
        assert bt_config.slippage_bps == run_config.cost.slippage_bps


# ============================================================================
# DataConfig Tests
# ============================================================================


class TestDataConfig:
    """Tests for DataConfig"""

    def test_data_config_defaults(self):
        """DataConfig should have sensible defaults"""
        config = DataConfig(
            source="synthetic",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert config.frequency == "1d"
        assert config.adjust == "qfq"
        assert config.symbols is None


# ============================================================================
# UniverseConfig Tests
# ============================================================================


class TestUniverseConfig:
    """Tests for UniverseConfig"""

    def test_universe_config_defaults(self):
        """UniverseConfig should have sensible defaults"""
        config = UniverseConfig()

        assert config.policy == "default"
        assert config.exclude_st is True
        assert config.exclude_suspended is True
        assert config.min_listing_days == 60


# ============================================================================
# CostConfig Tests
# ============================================================================


class TestCostConfig:
    """Tests for CostConfig"""

    def test_cost_config_defaults(self):
        """CostConfig should have A-share defaults"""
        config = CostConfig()

        assert config.commission_rate == 8.54e-05  # §13.1 MarketRulesCNEquity 万0.85
        assert config.commission_min == 5.0
        assert config.stamp_tax_rate == 0.0005
        assert config.transfer_fee_rate == 0.00001
        assert config.slippage_bps == 10.0  # A 股默认 10bps


# ============================================================================
# FillModelConfig Tests
# ============================================================================


class TestFillModelConfig:
    """Tests for FillModelConfig"""

    def test_fill_model_config_default(self):
        """FillModelConfig should default to next_bar_open"""
        config = FillModelConfig()
        assert config.name == "next_bar_open"

    def test_fill_model_parsed_from_yaml(self):
        """fill_model section should be parsed from config"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
            "fill_model": {
                "name": "next_bar_close",
            },
        }
        config = parse_config(raw)
        assert config.fill_model.name == "next_bar_close"

    def test_fill_model_defaults_when_missing(self):
        """fill_model should default when not specified"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        }
        config = parse_config(raw)
        assert config.fill_model.name == "next_bar_open"

    def test_fill_model_in_config_to_dict(self):
        """config_to_dict should include fill_model"""
        config = create_config_template()
        d = config_to_dict(config)
        assert "fill_model" in d
        assert d["fill_model"]["name"] == "next_bar_open"

    def test_fill_model_round_trip(self):
        """fill_model should survive save/load round trip"""
        config = create_config_template()
        config.fill_model = FillModelConfig(name="vwap")

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name

        try:
            save_config(config, path)
            loaded = load_config(path)
            assert loaded.fill_model.name == "vwap"
        finally:
            Path(path).unlink()


# ============================================================================
# CB Config Parsing Tests
# ============================================================================


class TestCBConfigParsing:
    """Tests for parsing CB strategy configs (configs/cb_strategies/*.yml)"""

    def test_parse_cb_strategy_config(self):
        """Should parse CB-style config with tushare_cb source"""
        raw = {
            "name": "new_bond",
            "data": {
                "source": "tushare_cb",
                "start_date": "2022-01-01",
                "end_date": "2024-12-31",
                "cache_dir": "data/cache/cb_tushare",
            },
            "strategy": {
                "name": "new_bond",
                "params": {
                    "top_k": 10,
                    "rebalance_days": 7,
                    "max_age_days": 60,
                },
            },
            "fill_model": {"name": "next_bar_open"},
            "cost": {
                "commission_rate": 8.54e-05,
                "commission_min": 5.0,
                "stamp_tax_rate": 0.0,
                "transfer_fee_rate": 0.0,
                "slippage_bps": 5.0,
            },
            "initial_capital": 500000.0,
            "lot_size": 10,
            "t_plus_n": 0,
        }

        config = parse_config(raw)

        assert config.data.source == "tushare_cb"
        assert config.strategy.name == "new_bond"
        assert config.strategy.params["top_k"] == 10
        assert config.cost.stamp_tax_rate == 0.0
        assert config.lot_size == 10
        assert config.t_plus_n == 0
        assert config.fill_model.name == "next_bar_open"


# ============================================================================
# _build_data_summary Tests
# ============================================================================


class TestBuildDataSummary:
    """测试数据摘要构建"""

    def _make_config(self, source="synthetic"):
        from aipha.backtest.config_runner import parse_config
        return parse_config({
            "name": "test",
            "data": {"source": source, "start_date": "2024-01-01", "end_date": "2024-03-31"},
            "strategy": {"name": "demo_momentum"},
        })

    def test_empty_data(self):
        from aipha.backtest.config_runner import _build_data_summary

        config = self._make_config()
        summary = _build_data_summary({}, config)
        assert summary["n_dates"] == 0
        assert summary["source"] == "synthetic"

    def test_normal_data(self):
        from datetime import date, timedelta
        from aipha.backtest.config_runner import _build_data_summary
        from aipha.domain.models import Bar

        config = self._make_config()

        bars_by_date = {}
        for i in range(3):
            d = date(2024, 1, 1) + timedelta(days=i)
            bars_by_date[d] = {
                f"SYM{j}": Bar(
                    symbol=f"SYM{j}",
                    start_ts=None,
                    end_ts=None,
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1000,
                )
                for j in range(5)
            }

        summary = _build_data_summary(bars_by_date, config)
        assert summary["n_dates"] == 3
        assert summary["total_bars"] == 15
        assert summary["avg_symbols_per_day"] == 5.0
        assert summary["first_date"] == "2024-01-01"
        assert summary["last_date"] == "2024-01-03"


# ============================================================================
# run_from_config Integration Tests
# ============================================================================


class TestRunFromConfig:
    """Integration tests for run_from_config (synthetic data)"""

    def _make_synthetic_config_file(self, tmp_path: Path) -> Path:
        """创建合成数据回测配置文件"""
        config_content = {
            "name": "integration_test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
                "symbols": ["600000.SH"],
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {"lookback_days": 20, "top_k": 1},
            },
            "fill_model": {"name": "next_bar_open"},
            "cost": {
                "commission_rate": 0.0003,
                "commission_min": 5.0,
                "stamp_tax_rate": 0.001,
                "transfer_fee_rate": 0.00002,
                "slippage_bps": 2.0,
            },
            "initial_capital": 100000.0,
            "lot_size": 100,
            "t_plus_n": 1,
            "random_seed": 42,
            "output_dir": str(tmp_path / "output"),
        }

        config_path = tmp_path / "test_config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)
        return config_path

    def test_run_from_config_synthetic(self, tmp_path):
        """Should run end-to-end backtest with synthetic data"""
        from aipha.backtest.config_runner import run_from_config

        config_path = self._make_synthetic_config_file(tmp_path)
        result = run_from_config(config_path)

        # 验证返回结构
        assert result.config.name == "integration_test"
        assert result.result is not None
        assert result.result.initial_capital == 100000.0
        assert result.fingerprint is not None
        assert result.fingerprint.random_seed == 42

    def test_run_from_config_creates_backtest_result(self, tmp_path):
        """BacktestResult should have valid fields"""
        from aipha.backtest.config_runner import run_from_config

        config_path = self._make_synthetic_config_file(tmp_path)
        run_result = run_from_config(config_path)

        result = run_result.result
        assert result.final_nav > 0
        assert result.start_date == date(2024, 1, 1)
        assert result.end_date <= date(2024, 3, 31)

    def test_run_from_config_uses_fill_model_from_config(self, tmp_path):
        """Should use fill_model specified in config, not hardcoded"""
        from aipha.backtest.config_runner import run_from_config

        config_content = {
            "name": "fill_model_test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-02-28",
                "symbols": ["600000.SH"],
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {"lookback_days": 10, "top_k": 1},
            },
            "fill_model": {"name": "next_bar_close"},
            "cost": {
                "commission_rate": 0.0001,
                "stamp_tax_rate": 0.0005,
            },
            "initial_capital": 100000.0,
            "random_seed": 42,
        }

        config_path = tmp_path / "fill_model_test.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)

        result = run_from_config(config_path, output_dir=tmp_path / "results")
        # If it didn't error, the fill model was created from config
        assert result.config.fill_model.name == "next_bar_close"
        assert result.result is not None

    def test_run_from_config_validation_error(self, tmp_path):
        """Should raise ValueError for invalid config"""
        from aipha.backtest.config_runner import run_from_config

        config_content = {
            "name": "bad_config",
            "data": {
                "source": "synthetic",
                "start_date": "2024-12-31",
                "end_date": "2024-01-01",  # end before start
            },
        }

        config_path = tmp_path / "bad.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)

        with pytest.raises(ValueError, match="配置验证失败"):
            run_from_config(config_path)

    def test_run_from_config_file_not_found(self):
        """Should raise FileNotFoundError for missing config"""
        from aipha.backtest.config_runner import run_from_config

        with pytest.raises(FileNotFoundError):
            run_from_config("/nonexistent/config.yml")

    def test_run_from_config_output_dir_override(self, tmp_path):
        """Should allow overriding output_dir"""
        from aipha.backtest.config_runner import run_from_config

        config_path = self._make_synthetic_config_file(tmp_path)
        custom_output = tmp_path / "custom_output"

        result = run_from_config(config_path, output_dir=custom_output)
        assert result.output_dir == custom_output
        assert list((custom_output / "audit").glob("audit_*.jsonl"))
        assert not (tmp_path / "output" / "audit").exists()

    @pytest.mark.parametrize("explicit_output", [None, "relative-results"])
    def test_relative_paths_ignore_working_directory(self, tmp_path, monkeypatch, explicit_output):
        from aipha.backtest.config_runner import run_from_config
        from aipha.utils import paths

        config_path = self._make_synthetic_config_file(tmp_path)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["output_dir"] = "configured-results"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
        monkeypatch.chdir(elsewhere)
        result = run_from_config(config_path.name, output_dir=explicit_output)
        expected = tmp_path / (explicit_output or "configured-results")
        assert result.output_dir == expected
        assert (expected / "fingerprint.json").is_file()
        assert list((expected / "audit").glob("audit_*.jsonl"))
        assert not list(elsewhere.iterdir())

    def test_run_from_config_unsupported_source(self, tmp_path):
        """Should raise ValueError for unsupported data source"""
        from aipha.backtest.config_runner import run_from_config

        config_content = {
            "name": "bad_source",
            "data": {
                "source": "unknown_source",
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
            },
            "strategy": {"name": "demo_momentum"},
        }

        config_path = tmp_path / "bad_source.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)

        with pytest.raises(ValueError, match="不支持的数据源"):
            run_from_config(config_path, output_dir=tmp_path / "results")

    def test_run_from_config_data_fingerprint(self, tmp_path):
        """数据指纹应包含实际数据哈希，不是 'none'"""
        from aipha.backtest.config_runner import run_from_config

        config_path = self._make_synthetic_config_file(tmp_path)
        result = run_from_config(config_path)

        assert result.fingerprint.data_fingerprint != "none"
        assert len(result.fingerprint.data_fingerprint) > 0

    def test_run_from_config_data_health(self, tmp_path):
        """回测结果应包含数据健康检查摘要"""
        from aipha.backtest.config_runner import run_from_config

        config_path = self._make_synthetic_config_file(tmp_path)
        result = run_from_config(config_path)

        assert result.data_health is not None
        assert "status" in result.data_health
        assert "checks" in result.data_health
        assert result.data_health["n_dates"] > 0

    def test_run_from_config_quality_gates_default(self, tmp_path):
        """回测结果应包含默认质量门禁报告"""
        from aipha.backtest.config_runner import run_from_config

        config_path = self._make_synthetic_config_file(tmp_path)
        result = run_from_config(config_path)

        assert result.quality_report is not None
        # 应有检查结果
        assert len(result.quality_report.results) > 0
        # all_passed 应为 bool
        assert isinstance(result.quality_report.all_passed, bool)

    def test_run_from_config_quality_gates_custom_yaml(self, tmp_path):
        """应支持通过配置指定自定义质量门禁 YAML"""
        from aipha.backtest.config_runner import run_from_config

        # 创建宽松的质量门禁配置（几乎一定通过）
        qg_config = {
            "quality_gates": [
                {
                    "metric": "total_return",
                    "operator": ">",
                    "threshold": -1.0,
                    "severity": "error",
                    "description": "总收益大于-100%",
                },
            ]
        }
        qg_path = tmp_path / "custom_qg.yml"
        with open(qg_path, "w") as f:
            yaml.dump(qg_config, f)

        # 创建带 quality_gates_path 的配置
        config_content = {
            "name": "qg_test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
                "symbols": ["600000.SH"],
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {"lookback_days": 20, "top_k": 1},
            },
            "fill_model": {"name": "next_bar_open"},
            "cost": {
                "commission_rate": 0.0003,
                "slippage_bps": 2.0,
            },
            "initial_capital": 100000.0,
            "random_seed": 42,
            "output_dir": str(tmp_path / "output"),
            "quality_gates_path": str(qg_path),
        }
        config_path = tmp_path / "qg_config.yml"
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)

        result = run_from_config(config_path)

        assert result.quality_report is not None
        assert result.quality_report.all_passed is True
        assert len(result.quality_report.results) == 1


# ============================================================================
# Quality Gates Path Config Tests
# ============================================================================


class TestQualityGatesPathConfig:
    """Tests for quality_gates_path in config"""

    def test_parse_quality_gates_path(self):
        """应从配置解析 quality_gates_path"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
            "quality_gates_path": "configs/quality_gates.yml",
        }
        config = parse_config(raw)
        assert config.quality_gates_path == "configs/quality_gates.yml"

    def test_parse_quality_gates_path_default_none(self):
        """quality_gates_path 默认为 None"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        }
        config = parse_config(raw)
        assert config.quality_gates_path is None

    def test_config_to_dict_includes_quality_gates_path(self):
        """config_to_dict 应包含 quality_gates_path"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
            "quality_gates_path": "configs/custom_qg.yml",
        }
        config = parse_config(raw)
        d = config_to_dict(config)
        assert d["quality_gates_path"] == "configs/custom_qg.yml"

    def test_config_round_trip_quality_gates_path(self, tmp_path):
        """quality_gates_path 应支持保存和重新加载"""
        raw = {
            "name": "test",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
            "quality_gates_path": "configs/quality_gates.yml",
        }
        config = parse_config(raw)

        # 保存
        path = tmp_path / "test.yml"
        save_config(config, path)

        # 重新加载
        loaded = load_config(path)
        assert loaded.quality_gates_path == "configs/quality_gates.yml"


# ============================================================================
# Config Normalization / Compatibility Tests
# ============================================================================


class TestNormalizeConfig:
    """_normalize_config 兼容层测试"""

    def test_run_block_to_data_dates(self):
        """run.start_date/end_date → data.start_date/end_date"""
        raw = {
            "run": {"start_date": "2020-01-01", "end_date": "2024-12-31", "seed": 42},
            "data": {"provider": "tushare_pro"},
        }
        out = _normalize_config(raw)
        assert out["data"]["start_date"] == "2020-01-01"
        assert out["data"]["end_date"] == "2024-12-31"
        assert out["random_seed"] == 42
        assert "run" not in out

    def test_data_provider_to_source(self):
        """data.provider → data.source"""
        raw = {"data": {"provider": "tushare_pro", "start_date": "2024-01-01", "end_date": "2024-12-31"}}
        out = _normalize_config(raw)
        assert out["data"]["source"] == "tushare_pro"
        assert "provider" not in out["data"]

    def test_data_source_not_overwritten(self):
        """data.source 已存在时不覆盖"""
        raw = {"data": {"source": "synthetic", "provider": "tushare_pro", "start_date": "2024-01-01", "end_date": "2024-12-31"}}
        out = _normalize_config(raw)
        assert out["data"]["source"] == "synthetic"

    def test_universe_policy_to_universe(self):
        """universe_policy.* → universe.*"""
        raw = {
            "universe_policy": {
                "name": "hs300_momentum",
                "auto_exclude": ["ST", "DELISTING", "SUSPENDED_LONG"],
                "manual_exclude_list": "configs/universe/exclude_list.yml",
            },
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["universe"]["policy"] == "hs300_momentum"
        assert out["universe"]["exclude_st"] is True
        assert out["universe"]["exclude_suspended"] is True
        assert out["universe"]["exclude_list_path"] == "configs/universe/exclude_list.yml"
        assert "universe_policy" not in out

    def test_cost_model_to_cost(self):
        """cost_model.commission/stamp_tax/transfer_fee → cost.*"""
        raw = {
            "cost_model": {
                "commission": {"rate": 0.0001, "min_fee": 5.0},
                "stamp_tax": {"sell_rate": 0.0005},
                "transfer_fee": {"rate": 0.00001},
            },
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["cost"]["commission_rate"] == 0.0001
        assert out["cost"]["commission_min"] == 5.0
        assert out["cost"]["stamp_tax_rate"] == 0.0005
        assert out["cost"]["transfer_fee_rate"] == 0.00001
        assert "cost_model" not in out

    def test_slippage_model_to_cost(self):
        """slippage_model.bps/name → cost.slippage_bps/slippage_model"""
        raw = {
            "slippage_model": {"name": "fixed_bps", "bps": 5},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["cost"]["slippage_bps"] == 5
        assert out["cost"]["slippage_model"] == "fixed_bps"

    def test_slippage_variant_to_cost(self):
        """slippage.model/bps → cost.slippage_model/slippage_bps"""
        raw = {
            "slippage": {"model": "fixed_bps", "bps": 3.0},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["cost"]["slippage_bps"] == 3.0
        assert out["cost"]["slippage_model"] == "fixed_bps"

    def test_slippage_linear_impact_params_passthrough(self):
        """slippage_model 中 linear_impact 的额外参数传递到 slippage_params"""
        raw = {
            "slippage_model": {
                "name": "linear_impact",
                "base_bps": 3.0,
                "impact_coeff": 50.0,
                "fallback_bps": 10.0,
                "max_bps": 100.0,
            },
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["cost"]["slippage_model"] == "linear_impact"
        params = out["cost"]["slippage_params"]
        assert params["base_bps"] == 3.0
        assert params["impact_coeff"] == 50.0
        assert params["fallback_bps"] == 10.0
        assert params["max_bps"] == 100.0

    def test_slippage_sqrt_impact_params_passthrough(self):
        """slippage_model 中 square_root_impact 参数也能正确传递"""
        raw = {
            "slippage_model": {
                "name": "square_root_impact",
                "impact_coeff": 0.5,
                "sigma_daily": 0.02,
                "max_bps": 200.0,
            },
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["cost"]["slippage_model"] == "square_root_impact"
        params = out["cost"]["slippage_params"]
        assert params["impact_coeff"] == 0.5
        assert params["sigma_daily"] == 0.02

    def test_strategy_parameters_to_params(self):
        """strategy.parameters → strategy.params"""
        raw = {
            "strategy": {"name": "demo_momentum", "parameters": {"lookback": 20}},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["strategy"]["params"] == {"lookback": 20}
        assert "parameters" not in out["strategy"]

    def test_strategy_config_to_params(self):
        """strategy.config → strategy.params (CB策略常用格式)"""
        raw = {
            "strategy": {"name": "cb_adaptive_factor", "config": {"top_k": 12, "blend_ratio": 0.4}},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["strategy"]["params"] == {"top_k": 12, "blend_ratio": 0.4}
        assert "config" not in out["strategy"]

    def test_strategy_params_takes_precedence_over_config(self):
        """If both params and config exist, params wins."""
        raw = {
            "strategy": {"name": "test", "params": {"a": 1}, "config": {"b": 2}},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["strategy"]["params"] == {"a": 1}
        # config key not migrated since params exists
        assert "config" in out["strategy"]

    def test_strategy_type_as_name_fallback(self):
        """strategy.type → strategy.name (when name missing)"""
        raw = {
            "strategy": {"type": "factor", "params": {}},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["strategy"]["name"] == "factor"

    def test_capital_initial_to_initial_capital(self):
        """capital.initial → initial_capital"""
        raw = {
            "capital": {"initial": 2000000.0, "currency": "CNY"},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["initial_capital"] == 2000000.0
        assert "capital" not in out

    def test_trading_rules_to_top_level(self):
        """trading_rules.t_plus/lot_size → t_plus_n/lot_size"""
        raw = {
            "trading_rules": {"t_plus": 1, "lot_size": 100},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["t_plus_n"] == 1
        assert out["lot_size"] == 100

    def test_min_commission_alias(self):
        """cost.min_commission → cost.commission_min"""
        raw = {
            "cost": {"commission_rate": 8.54e-05, "min_commission": 5.0, "stamp_tax_rate": 0.0005},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["cost"]["commission_min"] == 5.0
        assert "min_commission" not in out["cost"]

    def test_report_output_dir(self):
        """report.output_dir → output_dir"""
        raw = {
            "report": {"output_dir": "reports/test"},
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
        }
        out = _normalize_config(raw)
        assert out["output_dir"] == "reports/test"

    def test_standard_format_passthrough(self):
        """标准格式不受归一化影响"""
        raw = {
            "name": "test",
            "data": {"source": "synthetic", "start_date": "2024-01-01", "end_date": "2024-12-31"},
            "universe": {"policy": "default"},
            "strategy": {"name": "demo_momentum", "params": {"top_k": 10}},
            "cost": {"commission_rate": 0.0001, "commission_min": 5.0},
            "initial_capital": 1000000.0,
        }
        out = _normalize_config(raw)
        assert out["data"]["source"] == "synthetic"
        assert out["universe"]["policy"] == "default"
        assert out["strategy"]["params"] == {"top_k": 10}
        assert out["cost"]["commission_rate"] == 0.0001

    def test_unknown_keys_warning(self, caplog):
        """未识别的顶层 key 应产生 warning"""
        import logging
        raw = {
            "data": {"source": "s", "start_date": "2024-01-01", "end_date": "2024-12-31"},
            "unknown_block": {"foo": "bar"},
        }
        with caplog.at_level(logging.WARNING, logger="aipha.backtest.config_runner"):
            _normalize_config(raw)
        assert "unknown_block" in caplog.text


class TestParseNewFormatConfig:
    """parse_config 解析新格式配置的端对端测试"""

    def test_parse_momentum_hs300_style(self):
        """解析 momentum_hs300.yml 风格的完整新格式"""
        raw = {
            "run": {"mode": "backtest", "start_date": "2020-01-01", "end_date": "2024-12-31", "seed": 42},
            "data": {"provider": "tushare_pro"},
            "universe_policy": {
                "name": "hs300_momentum",
                "auto_exclude": ["ST", "DELISTING", "SUSPENDED_LONG"],
                "manual_exclude_list": "configs/universe/exclude_list.yml",
            },
            "cost_model": {
                "commission": {"rate": 0.0001, "min_fee": 5.0},
                "stamp_tax": {"sell_rate": 0.0005},
                "transfer_fee": {"rate": 0.00001},
            },
            "slippage_model": {"name": "fixed_bps", "bps": 5},
            "fill_model": {"name": "next_bar_open"},
            "initial_capital": 1000000,
            "trading_rules": {"t_plus": 1, "lot_size": 100},
            "strategy": {"name": "demo_momentum", "parameters": {"lookback_period": 20, "top_n": 20}},
        }

        config = parse_config(raw)

        assert config.data.source == "tushare_pro"
        assert config.data.start_date.isoformat() == "2020-01-01"
        assert config.data.end_date.isoformat() == "2024-12-31"
        assert config.random_seed == 42
        assert config.universe.policy == "hs300_momentum"
        assert config.universe.exclude_st is True
        assert config.cost.commission_rate == 0.0001
        assert config.cost.commission_min == 5.0
        assert config.cost.stamp_tax_rate == 0.0005
        assert config.cost.slippage_bps == 5
        assert config.strategy.params["lookback_period"] == 20
        assert config.t_plus_n == 1
        assert config.fill_model.name == "next_bar_open"

    def test_parse_dual_ma_style_with_compat(self):
        """解析 dual_ma.yml 风格（旧格式 + min_commission alias + slippage 独立段）"""
        raw = {
            "name": "dual_ma",
            "data": {"source": "tushare_pro", "start_date": "2020-01-01", "end_date": "2024-12-31"},
            "strategy": {"name": "demo_dual_ma", "params": {"short_window": 5}},
            "cost": {
                "commission_rate": 8.54e-05,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
            },
            "slippage": {"model": "fixed_bps", "bps": 5.0},
            "capital": {"initial": 1000000.0},
            "fill_model": {"name": "next_bar_open"},
        }

        config = parse_config(raw)

        assert config.name == "dual_ma"
        assert config.cost.commission_min == 5.0
        assert config.cost.slippage_bps == 5.0
        assert config.initial_capital == 1000000.0


# ============================================================================
# Equity Metadata Enrichment Tests
# ============================================================================


class TestEnrichBarMetadata:
    """Tests for _enrich_bar_metadata and _safe_float helpers."""

    def test_enrich_with_stock_basic(self):
        """stock_basic 元数据正确注入到 meta"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        stock_basic_df = pd.DataFrame({
            "name": ["平安银行", "贵州茅台"],
            "list_date": ["19910403", "20010827"],
            "industry": ["银行", "白酒"],
        }, index=pd.Index(["000001.SZ", "600519.SH"], name="ts_code"))

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000001.SZ", dt_date(2024, 1, 2),
            stock_basic_df, {},
        )

        assert meta["name"] == "平安银行"
        assert meta["list_date"] == "19910403"
        assert meta["industry"] == "银行"

    def test_enrich_with_daily_basic(self):
        """daily_basic 元数据正确注入到 meta"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        db_df = pd.DataFrame({
            "circ_mv": [3_500_000.0],
            "total_mv": [4_000_000.0],
            "pe_ttm": [8.5],
            "pb": [0.9],
            "turnover_rate": [1.2],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))
        daily_basic_map = {dt_date(2024, 1, 2): db_df}

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000001.SZ", dt_date(2024, 1, 2),
            None, daily_basic_map,
        )

        assert meta["circ_mv"] == 3_500_000.0
        assert meta["pe_ttm"] == 8.5
        assert meta["pb"] == 0.9
        assert meta["turnover_rate"] == 1.2

    def test_enrich_combined_stock_and_daily(self):
        """stock_basic + daily_basic 同时注入"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        stock_basic_df = pd.DataFrame({
            "name": ["平安银行"],
            "list_date": ["19910403"],
            "industry": ["银行"],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))

        db_df = pd.DataFrame({
            "circ_mv": [3_500_000.0],
            "total_mv": [4_000_000.0],
            "pe_ttm": [8.5],
            "pb": [0.9],
            "turnover_rate": [1.2],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))
        daily_basic_map = {dt_date(2024, 1, 2): db_df}

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000001.SZ", dt_date(2024, 1, 2),
            stock_basic_df, daily_basic_map,
        )

        assert meta["name"] == "平安银行"
        assert meta["circ_mv"] == 3_500_000.0
        assert meta["pe_ttm"] == 8.5

    def test_enrich_unknown_symbol_no_crash(self):
        """未知 symbol 不崩溃，meta 保持为空"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        stock_basic_df = pd.DataFrame({
            "name": ["平安银行"],
            "list_date": ["19910403"],
            "industry": ["银行"],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "999999.SZ", dt_date(2024, 1, 2),
            stock_basic_df, {},
        )

        assert meta == {}

    def test_enrich_st_detection(self):
        """ST 股票正确标记 is_st"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        stock_basic_df = pd.DataFrame({
            "name": ["*ST某股"],
            "list_date": ["20100101"],
            "industry": ["其他"],
        }, index=pd.Index(["600xxx.SH"], name="ts_code"))

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "600xxx.SH", dt_date(2024, 1, 2),
            stock_basic_df, {},
        )

        assert meta.get("is_st") is True

    def test_enrich_nan_values_converted_to_default(self):
        """NaN 值正确转换: circ_mv/total_mv/pe_ttm/pb → None, turnover_rate → 0.0"""
        import math
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        db_df = pd.DataFrame({
            "circ_mv": [float("nan")],
            "total_mv": [float("nan")],
            "pe_ttm": [float("nan")],
            "pb": [float("nan")],
            "turnover_rate": [float("nan")],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))
        daily_basic_map = {dt_date(2024, 1, 2): db_df}

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000001.SZ", dt_date(2024, 1, 2),
            None, daily_basic_map,
        )

        # circ_mv/total_mv/pe_ttm/pb 使用 _safe_float_or_none: NaN → None
        assert meta["circ_mv"] is None
        assert meta["pe_ttm"] is None
        assert meta["pb"] is None
        # turnover_rate 仍使用 _safe_float: NaN → 0.0
        assert meta["turnover_rate"] == 0.0

    def test_enrich_does_not_overwrite_existing(self):
        """已有的 meta 字段不被覆盖（setdefault 行为）"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        stock_basic_df = pd.DataFrame({
            "name": ["平安银行"],
            "list_date": ["19910403"],
            "industry": ["银行"],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))

        meta = {"name": "自定义名称", "industry": "自定义行业"}
        _enrich_bar_metadata(
            meta, "000001.SZ", dt_date(2024, 1, 2),
            stock_basic_df, {},
        )

        assert meta["name"] == "自定义名称"
        assert meta["industry"] == "自定义行业"

    def test_enrich_date_not_in_daily_basic(self):
        """交易日不在 daily_basic_map 中不崩溃"""
        import pandas as pd
        from datetime import date as dt_date
        from aipha.backtest.config_runner import _enrich_bar_metadata

        db_df = pd.DataFrame({
            "circ_mv": [100.0],
        }, index=pd.Index(["000001.SZ"], name="ts_code"))
        daily_basic_map = {dt_date(2024, 1, 3): db_df}

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000001.SZ", dt_date(2024, 1, 2),
            None, daily_basic_map,
        )

        # 日期 1/2 不在 map 中，meta 应为空
        assert "circ_mv" not in meta


class TestSafeFloat:
    """Tests for _safe_float helper."""

    def test_normal_value(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"val": 3.14})
        assert _safe_float(row, "val") == 3.14

    def test_nan_value(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"val": float("nan")})
        assert _safe_float(row, "val") == 0.0

    def test_none_value(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"val": None})
        assert _safe_float(row, "val") == 0.0

    def test_missing_column(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"other": 1.0})
        assert _safe_float(row, "val") == 0.0

    def test_custom_default(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"val": float("nan")})
        assert _safe_float(row, "val", default=-1.0) == -1.0

    def test_string_value(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"val": "not_a_number"})
        assert _safe_float(row, "val") == 0.0

    def test_integer_value(self):
        from aipha.backtest.config_runner import _safe_float
        import pandas as pd

        row = pd.Series({"val": 42})
        assert _safe_float(row, "val") == 42.0


# ============================================================================
# 多周期配置文件验证
# ============================================================================


class TestMultiPeriodConfigFiles:
    """验证 1y / 3y 配置文件格式正确且参数一致"""

    CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "backtest"
    STRATEGIES = ["multifactor_enhance", "pead_event", "smallcap_rotation"]

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_3y_config_exists(self, strategy):
        """3y 配置文件存在"""
        p = self.CONFIG_DIR / f"{strategy}_3y.yml"
        assert p.exists(), f"配置 {p.name} 不存在"

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_3y_config_parseable(self, strategy):
        """3y 配置文件可正确解析为 YAML"""
        import yaml
        p = self.CONFIG_DIR / f"{strategy}_3y.yml"
        with open(p, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert isinstance(cfg, dict)
        assert cfg["strategy"]["name"] == strategy

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_3y_date_range(self, strategy):
        """3y 配置的日期跨度为 3 年"""
        import yaml
        p = self.CONFIG_DIR / f"{strategy}_3y.yml"
        with open(p, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        start = cfg["run"]["start_date"]
        end = cfg["run"]["end_date"]
        assert start == "2022-01-01"
        assert end == "2024-12-31"

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_1y_3y_strategy_params_consistent(self, strategy):
        """1y 和 3y 的策略参数相同（日期/输出目录除外）"""
        import yaml
        p1 = self.CONFIG_DIR / f"{strategy}_1y.yml"
        p3 = self.CONFIG_DIR / f"{strategy}_3y.yml"
        if not p1.exists():
            pytest.skip(f"1y 配置 {p1.name} 不存在")
        with open(p1, encoding="utf-8") as f:
            cfg1 = yaml.safe_load(f)
        with open(p3, encoding="utf-8") as f:
            cfg3 = yaml.safe_load(f)
        # 策略名相同
        assert cfg1["strategy"]["name"] == cfg3["strategy"]["name"]
        # 成本模型相同
        assert cfg1["cost_model"] == cfg3["cost_model"]
        # 交易规则相同
        assert cfg1["trading_rules"] == cfg3["trading_rules"]

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_3y_has_cache_dir(self, strategy):
        """3y 配置显式声明 cache_dir"""
        import yaml
        p = self.CONFIG_DIR / f"{strategy}_3y.yml"
        with open(p, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg.get("data", {}).get("cache_dir") is not None


class TestSyntheticFallbackBlocked:
    """验证 _load_tushare_data 不再静默回退到合成数据 (H1 修复)"""

    def test_no_token_raises_value_error(self, monkeypatch):
        """无 TUSHARE_TOKEN 时应抛 ValueError 而非静默生成合成数据"""
        from aipha.backtest.config_runner import (
            _load_tushare_data, DataConfig, BacktestRunConfig,
            UniverseConfig, StrategyConfig, CostConfig,
        )

        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        config = BacktestRunConfig(
            name="test_no_token",
            data=DataConfig(
                source="tushare",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbols=["600000.SH"],
            ),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="test", params={}),
            cost=CostConfig(),
            initial_capital=100000.0,
        )

        with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
            _load_tushare_data(config)

    def test_explicit_synthetic_source_still_works(self, tmp_path):
        """显式设置 source=synthetic 仍可正常运行（合法的测试用途）"""
        from aipha.backtest.config_runner import run_from_config
        import yaml

        config = {
            "name": "explicit_synthetic",
            "data": {
                "source": "synthetic",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "symbols": ["600000.SH"],
            },
            "strategy": {"name": "demo_momentum", "params": {"lookback_days": 5, "top_k": 1}},
            "cost": {"commission_rate": 0.0001, "stamp_tax_rate": 0.0005},
            "initial_capital": 100000.0,
            "random_seed": 42,
        }
        p = tmp_path / "test.yml"
        with open(p, "w") as f:
            yaml.dump(config, f)
        result = run_from_config(p, output_dir=tmp_path / "results")
        assert result.result is not None, "显式 synthetic 应可正常运行"

    def test_tushare_empty_data_raises_runtime_error(self, monkeypatch):
        """TuShare 加载到 0 条数据时应抛 RuntimeError 而非回退"""
        from aipha.backtest.config_runner import (
            _load_tushare_data, DataConfig, BacktestRunConfig,
            UniverseConfig, StrategyConfig, CostConfig,
        )

        monkeypatch.setenv("TUSHARE_TOKEN", "fake_token_for_test")
        config = BacktestRunConfig(
            name="test_empty",
            data=DataConfig(
                source="tushare",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbols=["NONEXIST.SH"],
            ),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="test", params={}),
            cost=CostConfig(),
            initial_capital=100000.0,
        )

        # Mock provider to return empty data
        import aipha.backtest.config_runner as cr
        original_load = cr._load_tushare_data

        # 我们 mock TuShareProBarProvider 使其返回空 DataFrame
        from unittest.mock import MagicMock, patch
        mock_provider = MagicMock()
        mock_provider.load_bars.return_value = []

        with patch("aipha.data.tushare_provider.TuShareProBarProvider", return_value=mock_provider):
            with pytest.raises(RuntimeError, match="未加载到任何数据"):
                _load_tushare_data(config)


class TestExceptionPropagation:
    """验证 DANGEROUS except Exception 块已正确修复 (H3)"""

    def _make_config(self):
        from aipha.backtest.config_runner import (
            DataConfig, BacktestRunConfig,
            UniverseConfig, StrategyConfig, CostConfig,
        )
        return BacktestRunConfig(
            name="test_exc",
            data=DataConfig(
                source="tushare",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 3, 31),
                symbols=["000001.SZ"],
            ),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="multifactor_enhance", params={}),
            cost=CostConfig(),
            initial_capital=100000.0,
        )

    def test_equity_provider_init_failure_raises(self, monkeypatch):
        """TuShareEquityProvider 初始化失败应 raise RuntimeError（非 ImportError）"""
        monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")
        from unittest.mock import patch
        from aipha.backtest.config_runner import _load_tushare_data

        config = self._make_config()

        # Mock provider constructor to raise a generic error (network/auth)
        with patch(
            "aipha.data.tushare_equity_provider.TuShareEquityProvider",
            side_effect=ConnectionError("模拟网络故障"),
        ):
            with patch(
                "aipha.data.tushare_provider.TuShareProBarProvider",
            ):
                with pytest.raises(RuntimeError, match="初始化或 stock_basic"):
                    _load_tushare_data(config)

    def test_index_members_failure_raises_not_fallback(self, monkeypatch):
        """配置了 index_code 但加载成分失败时应 raise 而非默默切全市场"""
        monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")
        from unittest.mock import patch, MagicMock
        import pandas as pd
        from aipha.backtest.config_runner import _load_tushare_data

        config = self._make_config()
        # 设置 base_index（模拟指数配置）
        config.strategy.params = {"benchmark": "000300.SH"}
        # 清空 symbols，否则不会进入指数成分股分支
        config.data.symbols = None

        mock_eq = MagicMock()
        stock_df = pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["平安银行", "万科A"],
            "list_status": ["L", "L"],
        })
        mock_eq.load_stock_basic.return_value = stock_df
        mock_eq.load_daily_basic_range.return_value = None
        mock_eq.load_index_members_snapshots.side_effect = IOError("API 限流")
        mock_eq.load_index_members.side_effect = IOError("API 限流")

        with patch(
            "aipha.data.tushare_equity_provider.TuShareEquityProvider",
            return_value=mock_eq,
        ):
            with patch("aipha.data.tushare_provider.TuShareProBarProvider"):
                with pytest.raises(RuntimeError, match="加载指数.*成分股失败"):
                    _load_tushare_data(config)

    def test_bar_loading_circuit_breaker(self, monkeypatch):
        """连续 10 只股票加载失败时应触发熔断"""
        monkeypatch.setenv("TUSHARE_TOKEN", "fake_token")
        from unittest.mock import patch, MagicMock
        from aipha.backtest.config_runner import _load_tushare_data

        # 使用非 equity 策略，避免触发 need_equity_meta 代码路径
        config = self._make_config()
        config.strategy.name = "demo_momentum"
        config.strategy.params = {"lookback_days": 5, "top_k": 1}
        config.data.symbols = [f"TEST{i:04d}.SZ" for i in range(15)]

        mock_provider = MagicMock()
        mock_provider.load_bars.side_effect = ConnectionError("超时")

        # Reset circuit breaker state
        if hasattr(_load_tushare_data, "_n_consec_fail"):
            _load_tushare_data._n_consec_fail = 0
        if hasattr(_load_tushare_data, "_n_fail"):
            _load_tushare_data._n_fail = 0

        with patch(
            "aipha.data.tushare_provider.TuShareProBarProvider",
            return_value=mock_provider,
        ):
            with pytest.raises(RuntimeError, match="连续.*只股票加载失败"):
                _load_tushare_data(config)

    def test_fina_indicator_circuit_breaker(self):
        """连续 10 只 fina_indicator 加载失败时应触发熔断"""
        from unittest.mock import MagicMock
        from aipha.backtest.config_runner import _load_fina_indicators_pit

        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.side_effect = ConnectionError("API 认证失败")

        symbols = [f"TEST{i:04d}.SZ" for i in range(15)]

        # Reset circuit breaker state
        if hasattr(_load_fina_indicators_pit, "_consec"):
            _load_fina_indicators_pit._consec = 0

        with pytest.raises(RuntimeError, match="连续.*fina_indicator.*失败"):
            _load_fina_indicators_pit(mock_eq, symbols, MagicMock())

    def test_fina_indicator_high_failure_ratio(self):
        """超过 50% 标的 fina_indicator 加载失败应 raise"""
        from unittest.mock import MagicMock
        from aipha.backtest.config_runner import _load_fina_indicators_pit

        call_count = 0

        def _alternating_fail(sym):
            nonlocal call_count
            call_count += 1
            if call_count % 3 != 0:
                raise ValueError("模拟数据缺失")
            # 每3只成功一只 → 失败率 66%
            import pandas as pd
            return pd.DataFrame({
                "ann_date": ["20240101"],
                "roe": [10.5],
            })

        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.side_effect = _alternating_fail

        symbols = [f"TEST{i:04d}.SZ" for i in range(30)]

        # Reset circuit breaker state
        if hasattr(_load_fina_indicators_pit, "_consec"):
            _load_fina_indicators_pit._consec = 0

        with pytest.raises(RuntimeError, match="失败比例过高"):
            _load_fina_indicators_pit(mock_eq, symbols, MagicMock())


# ============================================================================
# Config YAML integration: linear_impact + opening_volume_fraction
# ============================================================================


class TestLinearImpactConfigIntegration:
    """验证 YAML 中 linear_impact 滑点 + opening_volume_fraction 的端到端解析。"""

    def test_parse_linear_impact_slippage(self):
        """YAML slippage_model.name=linear_impact → CostConfig.slippage_model='linear_impact'"""
        raw = {
            "data": {"source": "synthetic", "start_date": "2024-01-01", "end_date": "2024-12-31"},
            "slippage_model": {
                "name": "linear_impact",
                "base_bps": 3.0,
                "impact_coeff": 30.0,
                "fallback_bps": 8.0,
                "max_bps": 50.0,
            },
        }
        config = parse_config(raw)
        assert config.cost.slippage_model == "linear_impact"
        assert config.cost.slippage_params["base_bps"] == 3.0
        assert config.cost.slippage_params["impact_coeff"] == 30.0
        assert config.cost.slippage_params["fallback_bps"] == 8.0
        assert config.cost.slippage_params["max_bps"] == 50.0

    def test_create_linear_impact_from_config(self):
        """linear_impact config → 正确实例化 LinearImpactSlippageModel"""
        from aipha.backtest.config_runner import CostConfig, _create_slippage_model
        from aipha.execution.slippage import LinearImpactSlippageModel

        cost = CostConfig(
            slippage_model="linear_impact",
            slippage_params={"base_bps": 3.0, "impact_coeff": 30.0, "fallback_bps": 8.0, "max_bps": 50.0},
        )
        s = _create_slippage_model(cost)
        assert isinstance(s, LinearImpactSlippageModel)
        assert s.base_bps == 3.0
        assert s.impact_coeff == 30.0

    def test_parse_opening_volume_fraction(self):
        """YAML fill_model.volume_constraint.opening_volume_fraction 正确解析"""
        raw = {
            "data": {"source": "synthetic", "start_date": "2024-01-01", "end_date": "2024-12-31"},
            "fill_model": {
                "name": "next_bar_open",
                "volume_constraint": {
                    "max_participation_rate": 0.10,
                    "lot_size": 100,
                    "opening_volume_fraction": 0.10,
                },
            },
        }
        config = parse_config(raw)
        vc = config.fill_model.volume_constraint
        assert vc is not None
        assert vc["opening_volume_fraction"] == 0.10
        assert vc["max_participation_rate"] == 0.10

    def test_real_config_balanced_mf_hs300_v5(self):
        """验证 balanced_mf_hs300_v5.yml 实际配置可正确解析"""
        import os
        path = os.path.join("configs", "backtest", "balanced_mf_hs300_v5.yml")
        if not os.path.exists(path):
            pytest.skip("config file not found")
        config = load_config(path)
        # 应使用 linear_impact 滑点
        assert config.cost.slippage_model == "linear_impact"
        assert "base_bps" in config.cost.slippage_params
        assert "impact_coeff" in config.cost.slippage_params
        # 应有 opening_volume_fraction
        vc = config.fill_model.volume_constraint
        assert vc is not None
        assert vc.get("opening_volume_fraction", 1.0) == 0.10

    def test_real_config_balanced_mf_csi800(self):
        """验证 balanced_mf_csi800_9y.yml 实际配置可正确解析"""
        import os
        path = os.path.join("configs", "backtest", "balanced_mf_csi800_9y.yml")
        if not os.path.exists(path):
            pytest.skip("config file not found")
        config = load_config(path)
        assert config.cost.slippage_model == "linear_impact"
        assert config.cost.slippage_params.get("impact_coeff", 0) == 40.0
        vc = config.fill_model.volume_constraint
        assert vc is not None
        assert vc.get("opening_volume_fraction", 1.0) == 0.10
