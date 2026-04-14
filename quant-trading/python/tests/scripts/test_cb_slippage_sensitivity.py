"""Tests for scripts/cb_slippage_sensitivity.py (A3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from cb_slippage_sensitivity import (  # noqa: E402
    DEFAULT_SLIPPAGE_BPS,
    SLIPPAGE_YAML_KEY,
    SensitivityReport,
    SlippageRunResult,
    StrategySensitivityReport,
    _patch_slippage_bps,
    print_report,
    run_sensitivity,
    save_report,
)


# ---------------------------------------------------------------------------
# _patch_slippage_bps
# ---------------------------------------------------------------------------

class TestPatchSlippageBps:
    """Unit tests for _patch_slippage_bps."""

    def test_patches_cost_node(self):
        cfg = {"cost": {"commission_rate": 0.0000854, "slippage_bps": 5.0}}
        result = _patch_slippage_bps(cfg, 20)
        assert result["cost"]["slippage_bps"] == 20.0

    def test_patches_cost_model_slippage_node(self):
        cfg = {"cost_model": {"slippage": {"bps": 5, "model": "fixed"}}}
        result = _patch_slippage_bps(cfg, 15)
        assert result["cost_model"]["slippage"]["bps"] == 15

    def test_patches_slippage_model_node(self):
        cfg = {"slippage_model": {"bps": 5, "name": "fixed_bps"}}
        result = _patch_slippage_bps(cfg, 10)
        assert result["slippage_model"]["bps"] == 10

    def test_patches_slippage_dict_node(self):
        cfg = {"slippage": {"bps": 5, "model": "fixed"}}
        result = _patch_slippage_bps(cfg, 25)
        assert result["slippage"]["bps"] == 25

    def test_fallback_to_root_key(self):
        cfg = {"strategy": "test"}
        result = _patch_slippage_bps(cfg, 20)
        assert result[SLIPPAGE_YAML_KEY] == 20

    def test_does_not_mutate_original(self):
        cfg = {"cost": {"slippage_bps": 5.0}}
        _patch_slippage_bps(cfg, 20)
        assert cfg["cost"]["slippage_bps"] == 5.0

    def test_prefers_cost_over_root(self):
        """cost 节点优先级高于根节点 fallback."""
        cfg = {"cost": {"slippage_bps": 5.0}, SLIPPAGE_YAML_KEY: 3}
        result = _patch_slippage_bps(cfg, 15)
        assert result["cost"]["slippage_bps"] == 15.0
        # 根节点 slippage_bps 不变（因为 cost 节点已 patched）
        assert result[SLIPPAGE_YAML_KEY] == 3


# ---------------------------------------------------------------------------
# SlippageRunResult
# ---------------------------------------------------------------------------

class TestSlippageRunResult:
    def test_is_ok_true(self):
        r = SlippageRunResult(
            strategy_name="s", config_path="c", slippage_bps=5,
            annual_return=0.20, max_drawdown=0.15, sharpe=1.2, calmar=1.5,
            run_id="rid",
        )
        assert r.is_ok is True

    def test_is_ok_false_when_error(self):
        r = SlippageRunResult(
            strategy_name="s", config_path="c", slippage_bps=5,
            annual_return=0.20, max_drawdown=None, sharpe=None, calmar=None,
            run_id=None, error="some error",
        )
        assert r.is_ok is False

    def test_is_ok_false_when_no_return(self):
        r = SlippageRunResult(
            strategy_name="s", config_path="c", slippage_bps=5,
            annual_return=None, max_drawdown=0.10, sharpe=1.0, calmar=1.0,
            run_id="rid",
        )
        assert r.is_ok is False


# ---------------------------------------------------------------------------
# StrategySensitivityReport.ar_decay_pct
# ---------------------------------------------------------------------------

class TestArDecayPct:
    def _make_strat(self, baseline_ar: float, bps_ars: dict) -> StrategySensitivityReport:
        runs = [
            SlippageRunResult(
                strategy_name="s", config_path="c", slippage_bps=bps,
                annual_return=ar, max_drawdown=0.10, sharpe=1.0, calmar=1.0,
                run_id=None,
            )
            for bps, ar in bps_ars.items()
        ]
        return StrategySensitivityReport(
            strategy_name="s", config_path="c",
            baseline_bps=min(bps_ars.keys()),
            baseline_ar=baseline_ar,
            runs=runs,
        )

    def test_zero_at_baseline(self):
        strat = self._make_strat(0.25, {5: 0.25, 15: 0.20, 25: 0.15})
        assert strat.ar_decay_pct(5) == pytest.approx(0.0, abs=1e-9)

    def test_negative_decay_at_higher_bps(self):
        strat = self._make_strat(0.25, {5: 0.25, 15: 0.20, 25: 0.15})
        assert strat.ar_decay_pct(15) == pytest.approx(-0.05, abs=1e-9)
        assert strat.ar_decay_pct(25) == pytest.approx(-0.10, abs=1e-9)

    def test_returns_none_when_baseline_ar_none(self):
        strat = self._make_strat(None, {5: 0.25, 15: 0.20})
        # noinspection PyTypeChecker
        strat.baseline_ar = None
        assert strat.ar_decay_pct(15) is None

    def test_returns_none_for_missing_bps(self):
        strat = self._make_strat(0.25, {5: 0.25})
        assert strat.ar_decay_pct(99) is None


# ---------------------------------------------------------------------------
# run_sensitivity (dry_run mode - no data required)
# ---------------------------------------------------------------------------

class TestRunSensitivityDryRun:
    def test_dry_run_returns_report(self, tmp_path):
        """Dry-run 不实际运行回测，返回含占位结果的 SensitivityReport."""
        # Write a minimal valid config YAML
        cfg = {"cost": {"slippage_bps": 5.0}, "run": {"mode": "backtest"}}
        cfg_path = tmp_path / "test_strat.yml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

        report = run_sensitivity(
            config_paths=[str(cfg_path)],
            slippage_levels=[5, 10, 15],
            dry_run=True,
        )
        assert len(report.strategies) == 1
        strat = report.strategies[0]
        assert strat.strategy_name == "test_strat"
        assert len(strat.runs) == 3
        for run in strat.runs:
            assert run.error == "dry_run"

    def test_dry_run_baseline_bps_is_min(self, tmp_path):
        cfg = {}
        cfg_path = tmp_path / "s.yml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        report = run_sensitivity(
            config_paths=[str(cfg_path)],
            slippage_levels=[25, 5, 15],
            dry_run=True,
        )
        assert report.baseline_bps == 5

    def test_dry_run_deduplicates_slippage_levels(self, tmp_path):
        cfg = {}
        cfg_path = tmp_path / "s.yml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")
        report = run_sensitivity(
            config_paths=[str(cfg_path)],
            slippage_levels=[5, 5, 10, 10],
            dry_run=True,
        )
        assert len(report.strategies[0].runs) == 2


# ---------------------------------------------------------------------------
# save_report / to_dict
# ---------------------------------------------------------------------------

class TestSaveReport:
    def _make_report(self) -> SensitivityReport:
        runs = [
            SlippageRunResult(
                strategy_name="s", config_path="c", slippage_bps=5,
                annual_return=0.25, max_drawdown=0.12, sharpe=1.1, calmar=1.5,
                run_id="r1",
            ),
            SlippageRunResult(
                strategy_name="s", config_path="c", slippage_bps=15,
                annual_return=0.20, max_drawdown=0.12, sharpe=0.9, calmar=1.2,
                run_id="r2",
            ),
        ]
        strat = StrategySensitivityReport(
            strategy_name="s", config_path="c",
            baseline_bps=5, baseline_ar=0.25, runs=runs,
        )
        return SensitivityReport(
            strategies=[strat],
            baseline_bps=5,
            slippage_levels=[5, 15],
        )

    def test_save_report_creates_json(self, tmp_path):
        report = self._make_report()
        out = tmp_path / "out.json"
        save_report(report, str(out))
        assert out.is_file()
        data = json.loads(out.read_text())
        assert data["baseline_bps"] == 5
        assert len(data["strategies"]) == 1

    def test_to_dict_structure(self):
        report = self._make_report()
        d = report.to_dict()
        assert "strategies" in d
        assert "baseline_bps" in d
        assert "slippage_levels" in d
        assert d["slippage_levels"] == [5, 15]

    def test_save_creates_parent_dirs(self, tmp_path):
        report = self._make_report()
        out = tmp_path / "nested" / "dir" / "report.json"
        save_report(report, str(out))
        assert out.is_file()
