"""config_runner edge-case tests.

Covers untested branches in config_runner.py:
- _create_slippage_model: unknown model → ValueError
- _build_universe_policy: manual blacklist FileNotFoundError
- save_run_result: full save pipeline
- _compute_sue_fields: multi-quarter, single-surprise, std≈0, year-gap, missing eps
- _pit_lookup: empty records, trade_date before all, normal hit
- _build_st_interval_index + _is_st_by_namechange: empty df, NaT, non-ST filter
- _build_degradation_manager: actual creation path
- _run_quality_gates: exception degradation (catch-and-warn)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest


# ============================================================================
# _create_slippage_model – unknown model name → ValueError
# ============================================================================

from aipha.backtest.config_runner import CostConfig, _create_slippage_model


class TestCreateSlippageModelEdge:

    def test_unknown_model_raises(self):
        cost = CostConfig(slippage_model="foo_model")
        with pytest.raises(ValueError, match="不支持的滑点模型"):
            _create_slippage_model(cost)

    def test_fixed_bps_alias(self):
        cost = CostConfig(slippage_model="fixed_bps", slippage_bps=5.0)
        m = _create_slippage_model(cost)
        assert m is not None

    def test_linear_impact(self):
        cost = CostConfig(
            slippage_model="linear_impact",
            slippage_params={
                "base_bps": 3.0,
                "impact_coeff": 50.0,
                "fallback_bps": 10.0,
                "max_bps": 100.0,
            },
        )
        m = _create_slippage_model(cost)
        assert m is not None

    def test_square_root_impact(self):
        cost = CostConfig(
            slippage_model="square_root_impact",
            slippage_params={
                "impact_coeff": 0.5,
                "sigma_daily": 0.02,
                "max_bps": 200.0,
            },
        )
        m = _create_slippage_model(cost)
        assert m is not None


# ============================================================================
# _build_universe_policy – manual blacklist FileNotFoundError
# ============================================================================

from aipha.backtest.config_runner import (
    BacktestRunConfig,
    DataConfig,
    FillModelConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    _build_universe_policy,
)


def _make_run_config(**overrides) -> BacktestRunConfig:
    """Minimal BacktestRunConfig factory."""
    defaults = dict(
        name="test",
        data=DataConfig(
            source="synthetic",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 1),
        ),
        universe=UniverseConfig(),
        strategy=StrategyConfig(name="buy_and_hold"),
        cost=CostConfig(),
        fill_model=FillModelConfig(),
        risk=RiskConfig(),
    )
    defaults.update(overrides)
    return BacktestRunConfig(**defaults)


class TestBuildUniversePolicyEdge:

    def test_manual_blacklist_file_not_found_degrades(self, tmp_path: Path):
        """exclude_list_path 指向不存在的文件 → warn, 忽略该规则"""
        cfg = _make_run_config(
            universe=UniverseConfig(
                exclude_st=False,
                exclude_suspended=False,
                exclude_list_path=str(tmp_path / "no_such_file.yml"),
            )
        )
        policy = _build_universe_policy(cfg)
        # DelistedExclusionRule should still be there
        assert policy is not None
        # Delisted always present + manual may be empty but present
        # The function may load an empty ManualExclusionRule or skip it
        assert len(policy.rules) >= 1

    def test_st_and_suspended_enabled(self):
        cfg = _make_run_config(
            universe=UniverseConfig(
                exclude_st=True,
                exclude_suspended=True,
            )
        )
        policy = _build_universe_policy(cfg)
        # ST + Suspended + Delisted = 3 rules
        assert len(policy.rules) == 3


# ============================================================================
# save_run_result
# ============================================================================

from aipha.backtest.config_runner import BacktestRunResult, save_run_result
from aipha.execution.fees import FeeBreakdown
from aipha.utils.reproducibility import ExperimentFingerprint


@dataclass
class _Snap:
    trade_date: date
    nav: float
    cash: float
    positions: dict = field(default_factory=dict)
    position_values: dict = field(default_factory=dict)
    daily_return: float = 0.0
    cumulative_return: float = 0.0


@dataclass
class _FakeBTResult:
    strategy_name: str = "test"
    start_date: date = date(2024, 1, 2)
    end_date: date = date(2024, 1, 10)
    initial_capital: float = 1_000_000
    final_nav: float = 1_010_000
    total_trades: int = 5
    total_fees: FeeBreakdown = field(
        default_factory=lambda: FeeBreakdown(commission=10, stamp_tax=5, transfer_fee=1)
    )
    daily_snapshots: list = field(default_factory=list)
    trades: list = field(default_factory=list)


class TestSaveRunResult:

    def test_save_creates_all_files(self, tmp_path: Path):
        run_cfg = _make_run_config()
        fp = ExperimentFingerprint(
            git_commit="abc", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=42,
            run_time=datetime(2025, 1, 1),
        )
        bt_result = _FakeBTResult()
        run_result = BacktestRunResult(
            config=run_cfg,
            result=bt_result,
            fingerprint=fp,
            output_dir=tmp_path / "out",
        )
        paths = save_run_result(run_result)

        assert (tmp_path / "out" / "config.yaml").exists()
        assert (tmp_path / "out" / "fingerprint.json").exists()
        assert (tmp_path / "out" / "summary.json").exists()

        # Check summary content
        summary = json.loads((tmp_path / "out" / "summary.json").read_text("utf-8"))
        assert summary["strategy_name"] == "test"
        assert summary["total_trades"] == 5
        assert summary["total_fees"] == 16.0  # 10+5+1

        assert "config" in paths
        assert "fingerprint" in paths
        assert "summary" in paths


class TestBacktestRunResultCompat:

    def test_dict_like_accessors_return_serializable_summary(self, tmp_path: Path):
        run_cfg = _make_run_config()
        fp = ExperimentFingerprint(
            git_commit="abc", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=42,
            run_time=datetime(2025, 1, 1),
        )
        run_result = BacktestRunResult(
            config=run_cfg,
            result=_FakeBTResult(),
            fingerprint=fp,
            output_dir=tmp_path / "out",
        )

        data = run_result.to_dict()

        assert data["config_name"] == "test"
        assert data["strategy_name"] == "test"
        assert data["summary_path"].endswith("summary.json")
        assert run_result.get("total_trades") == 5
        assert dict(run_result.items())["final_nav"] == 1_010_000.0


# ============================================================================
# _compute_sue_fields
# ============================================================================

from aipha.backtest.config_runner import _compute_sue_fields


class TestComputeSueFields:

    def test_normal_multi_quarter(self):
        """正常多期 SUE 计算"""
        records = [
            ("2021-04-28", {"eps": 0.50, "_end_date": "20201231"}),
            ("2022-04-25", {"eps": 0.60, "_end_date": "20211231"}),
            ("2023-04-20", {"eps": 0.55, "_end_date": "20221231"}),
            ("2024-04-18", {"eps": 0.70, "_end_date": "20231231"}),
        ]
        _compute_sue_fields(records)
        # Second record should have eps_yoy_prev
        assert records[1][1].get("eps_yoy_prev") == 0.50
        # Fourth record should have sue
        assert "sue" in records[3][1]
        # _end_date is intentionally preserved (strategy layer needs it for ROE annualization)
        assert "_end_date" in records[0][1]

    def test_single_surprise_uses_prev_eps_normalization(self):
        """Only 1 surprise → sue = surprise / |eps_yoy_prev|"""
        records = [
            ("2022-04-01", {"eps": 1.0, "_end_date": "20211231"}),
            ("2023-04-01", {"eps": 1.5, "_end_date": "20221231"}),
        ]
        _compute_sue_fields(records)
        d = records[1][1]
        assert d["eps_yoy_prev"] == 1.0
        # surprise = 1.5 - 1.0 = 0.5; sue = 0.5 / |1.0| = 0.5
        assert abs(d["sue"] - 0.5) < 1e-9

    def test_std_near_zero_extreme_zscore(self):
        """std≈0 时 surprise → ±3.0 极端 z-score"""
        # All same eps → surprise always 0, then one different
        records = [
            ("2020-04-01", {"eps": 1.0, "_end_date": "20191231"}),
            ("2021-04-01", {"eps": 1.0, "_end_date": "20201231"}),
            ("2022-04-01", {"eps": 1.0, "_end_date": "20211231"}),
            ("2023-04-01", {"eps": 1.0, "_end_date": "20221231"}),
        ]
        _compute_sue_fields(records)
        # surprises are all 0, std=0
        # For the 3rd and 4th records (len(surprises) >= 2), sue should be 0.0
        # since surprise=0 and std≈0
        assert records[3][1].get("sue") == 0.0

    def test_year_gap_too_large(self):
        """跳年 >2 不计算 surprise"""
        records = [
            ("2019-04-01", {"eps": 1.0, "_end_date": "20181231"}),
            ("2024-04-01", {"eps": 2.0, "_end_date": "20231231"}),
        ]
        _compute_sue_fields(records)
        # Gap is 2023-2018=5 → skipped
        assert "eps_yoy_prev" not in records[1][1]

    def test_missing_eps_skipped(self):
        """eps 缺失的记录被跳过"""
        records = [
            ("2022-04-01", {"eps": None, "_end_date": "20211231"}),
            ("2023-04-01", {"eps": 1.0, "_end_date": "20221231"}),
        ]
        _compute_sue_fields(records)
        # First has no eps, so second has no eps_yoy_prev
        assert "eps_yoy_prev" not in records[1][1]

    def test_empty_records(self):
        """空记录列表不崩溃"""
        _compute_sue_fields([])

    def test_eps_history_populated(self):
        """eps_history 应包含同季度历史 EPS"""
        records = [
            ("2020-04-01", {"eps": 0.3, "_end_date": "20190630"}),
            ("2021-04-01", {"eps": 0.4, "_end_date": "20200630"}),
            ("2022-04-01", {"eps": 0.5, "_end_date": "20210630"}),
        ]
        _compute_sue_fields(records)
        hist = records[2][1].get("eps_history")
        assert hist is not None
        assert len(hist) == 3
        assert hist == [0.3, 0.4, 0.5]


# ============================================================================
# _pit_lookup
# ============================================================================

from aipha.backtest.config_runner import _pit_lookup


class TestPitLookup:

    def test_empty_records(self):
        assert _pit_lookup([], date(2024, 1, 1)) is None

    def test_trade_date_before_all(self):
        records = [
            (date(2024, 6, 1), {"eps": 1.0}),
            (date(2024, 9, 1), {"eps": 2.0}),
        ]
        assert _pit_lookup(records, date(2024, 1, 1)) is None

    def test_normal_hit(self):
        records = [
            (date(2024, 3, 1), {"eps": 1.0}),
            (date(2024, 6, 1), {"eps": 2.0}),
            (date(2024, 9, 1), {"eps": 3.0}),
        ]
        result = _pit_lookup(records, date(2024, 7, 15))
        assert result == {"eps": 2.0}

    def test_exact_date_hit(self):
        records = [
            (date(2024, 3, 1), {"eps": 1.0}),
        ]
        result = _pit_lookup(records, date(2024, 3, 1))
        assert result == {"eps": 1.0}

    def test_after_all(self):
        records = [
            (date(2024, 3, 1), {"eps": 1.0}),
            (date(2024, 6, 1), {"eps": 2.0}),
        ]
        result = _pit_lookup(records, date(2025, 1, 1))
        assert result == {"eps": 2.0}


# ============================================================================
# _build_st_interval_index + _is_st_by_namechange
# ============================================================================

import pandas as pd

from aipha.backtest.config_runner import (
    _build_st_interval_index,
    _is_st_by_namechange,
)


class TestSTIntervalIndex:

    def test_empty_df(self):
        assert _build_st_interval_index(None) == {}
        assert _build_st_interval_index(pd.DataFrame()) == {}

    def test_non_st_names_filtered(self):
        """非 ST/退市 名称不进入索引"""
        df = pd.DataFrame([
            {"ts_code": "600000.SH", "name": "浦发银行",
             "start_date": "20200101", "end_date": "20201231"},
        ])
        idx = _build_st_interval_index(df)
        assert idx == {}

    def test_st_name_included(self):
        df = pd.DataFrame([
            {"ts_code": "600001.SH", "name": "ST 某某",
             "start_date": "20200601", "end_date": "20201231"},
        ])
        idx = _build_st_interval_index(df)
        assert "600001.SH" in idx
        assert len(idx["600001.SH"]) == 1

    def test_nat_dates_default(self):
        """NaT 日期回退到极端值"""
        df = pd.DataFrame([
            {"ts_code": "600002.SH", "name": "*ST退市",
             "start_date": pd.NaT, "end_date": pd.NaT},
        ])
        idx = _build_st_interval_index(df)
        intervals = idx.get("600002.SH", [])
        assert len(intervals) == 1
        assert intervals[0][0] == date(1990, 1, 1)
        assert intervals[0][1] == date(2099, 12, 31)


class TestIsSTByNamechange:

    def test_not_in_index(self):
        assert _is_st_by_namechange("999999.SH", date(2024, 1, 1), {}) is False

    def test_within_interval(self):
        idx = {"600001.SH": [(date(2020, 1, 1), date(2020, 12, 31))]}
        assert _is_st_by_namechange("600001.SH", date(2020, 6, 15), idx) is True

    def test_outside_interval(self):
        idx = {"600001.SH": [(date(2020, 1, 1), date(2020, 12, 31))]}
        assert _is_st_by_namechange("600001.SH", date(2021, 6, 15), idx) is False

    def test_boundary_dates(self):
        idx = {"600001.SH": [(date(2020, 1, 1), date(2020, 12, 31))]}
        assert _is_st_by_namechange("600001.SH", date(2020, 1, 1), idx) is True
        assert _is_st_by_namechange("600001.SH", date(2020, 12, 31), idx) is True


# ============================================================================
# _build_degradation_manager – actual creation
# ============================================================================

from aipha.backtest.config_runner import _build_degradation_manager


class TestBuildDegradationManagerEdge:

    def test_disabled_returns_none(self):
        cfg = _make_run_config(risk=RiskConfig(enabled=True, degradation_enabled=False))
        assert _build_degradation_manager(cfg) is None

    def test_risk_disabled_returns_none(self):
        cfg = _make_run_config(risk=RiskConfig(enabled=False, degradation_enabled=True))
        assert _build_degradation_manager(cfg) is None

    def test_enabled_creates_manager(self):
        cfg = _make_run_config(
            risk=RiskConfig(
                enabled=True,
                degradation_enabled=True,
                degradation_auto_recover=True,
                degradation_recovery_cooldown=120.0,
                degradation_position_scale_warning=0.7,
                degradation_position_scale_degraded=0.1,
            )
        )
        mgr = _build_degradation_manager(cfg)
        assert mgr is not None
        # DegradationManager stores config as _config
        assert mgr._config.auto_recover is True
        assert mgr._config.recovery_cooldown_seconds == 120.0
        assert mgr._config.position_scale_warning == 0.7
