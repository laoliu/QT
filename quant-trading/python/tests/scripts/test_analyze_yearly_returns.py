"""tests/scripts/test_analyze_yearly_returns.py

analyze_yearly_returns 核心函数单元测试（≥10 条）。

通过构造真实数据结构实例测试，不 Mock 业务对象。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from scripts.analyze_yearly_returns import (
    ANOMALY_YEAR,
    MIN_POSITIVE_YEARS_RATIO,
    YearlyReturnRow,
    YearlyReport,
    _compute_anomaly_contribution,
    _ex_anomaly_ar_proxy,
    _load_from_files,
    build_yearly_row,
    build_report,
    save_report,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _raw(
    name: str = "test_strategy",
    run_id: str = "run_001",
    overall_ar: float = 0.30,
    overall_sharpe: float = 1.2,
    overall_mdd: float = 0.18,
    yearly: dict | None = None,
) -> dict:
    """构造一条原始摘要字典（真实数据结构）。"""
    if yearly is None:
        yearly = {2018: 0.20, 2019: 0.15, 2020: 1.35, 2021: 0.10, 2022: 0.05}
    return {
        "run_id": run_id,
        "strategy_name": name,
        "start_date": "2018-01-02",
        "end_date": "2022-12-30",
        "overall_ar": overall_ar,
        "overall_sharpe": overall_sharpe,
        "overall_mdd": overall_mdd,
        "yearly_returns": yearly,
    }


# ── 单元测试 ──────────────────────────────────────────────────────────────────


class TestComputeAnomalyContribution:
    """_compute_anomaly_contribution 边界验证。"""

    def test_normal_case(self):
        yearly = {2018: 0.10, 2019: 0.12, 2020: 1.35, 2021: 0.08}
        # other mean = (0.10 + 0.12 + 0.08) / 3 ≈ 0.1
        result = _compute_anomaly_contribution(yearly, 2020)
        assert result == pytest.approx(1.35 / 0.1, rel=0.01)

    def test_no_anomaly_year(self):
        """不含异常年份时返回 0.0。"""
        yearly = {2018: 0.10, 2019: 0.12}
        result = _compute_anomaly_contribution(yearly, 2020)
        assert result == 0.0

    def test_only_anomaly_year(self):
        """只有异常年份时（无其他参照），返回 0.0。"""
        yearly = {2020: 1.35}
        result = _compute_anomaly_contribution(yearly, 2020)
        assert result == 0.0

    def test_other_mean_zero_returns_inf(self):
        """其余年份均值为 0 时，正异常年份返回 inf。"""
        yearly = {2019: 0.0, 2020: 1.35}
        result = _compute_anomaly_contribution(yearly, 2020)
        assert result == float("inf")

    def test_negative_other_mean_returns_inf(self):
        """其余年份均值为负而异常年为正 → 极度异常，返回 +inf。

        含义：典型年份亏损而异常年份盈利，2020 贡献无可比较基准 = 无穷偏差。
        """
        yearly = {2019: -0.10, 2020: 0.50, 2021: -0.10}
        result = _compute_anomaly_contribution(yearly, 2020)
        assert result == float("inf")


class TestExAnomalyArProxy:
    """_ex_anomaly_ar_proxy 验证。"""

    def test_removes_anomaly_year(self):
        yearly = {2018: 0.10, 2019: 0.12, 2020: 1.35, 2021: 0.08}
        ex_ar, ex_pos = _ex_anomaly_ar_proxy(yearly, 2020)
        expected_ar = (0.10 + 0.12 + 0.08) / 3
        assert ex_ar == pytest.approx(expected_ar, abs=1e-6)
        assert ex_pos == pytest.approx(1.0, abs=1e-6)  # 全正

    def test_all_anomaly_year(self):
        yearly = {2020: 1.35}
        ex_ar, ex_pos = _ex_anomaly_ar_proxy(yearly, 2020)
        assert ex_ar == 0.0
        assert ex_pos == 0.0

    def test_mixed_positive_negative(self):
        yearly = {2019: 0.10, 2020: 1.35, 2021: -0.05, 2022: 0.08}
        ex_ar, ex_pos = _ex_anomaly_ar_proxy(yearly, 2020)
        expected_ar = (0.10 - 0.05 + 0.08) / 3
        assert ex_ar == pytest.approx(expected_ar, abs=1e-6)
        assert ex_pos == pytest.approx(2 / 3, abs=1e-4)


class TestBuildYearlyRow:
    """build_yearly_row 构建验证。"""

    def test_basic_build(self):
        raw = _raw()
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        assert row is not None
        assert row.strategy_name == "test_strategy"
        assert row.n_years == 5
        assert ANOMALY_YEAR in row.yearly

    def test_n_positive_count(self):
        """计算正收益年份数正确。"""
        raw = _raw(yearly={2018: 0.10, 2019: -0.05, 2020: 1.35, 2021: -0.08, 2022: 0.05})
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        assert row.n_positive == 3
        assert row.positive_ratio == pytest.approx(3 / 5, abs=1e-6)

    def test_passes_yearly_stability_true(self):
        """全正收益策略达标。"""
        raw = _raw(yearly={2018: 0.30, 2019: 0.25, 2020: 1.35, 2021: 0.10, 2022: 0.08})
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        assert row.passes_yearly_stability is True

    def test_passes_yearly_stability_false(self):
        """正收益比例不足时不达标。"""
        raw = _raw(yearly={2018: -0.10, 2019: -0.15, 2020: 1.35, 2021: -0.08, 2022: 0.05})
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        assert row.passes_yearly_stability is False

    def test_empty_yearly_returns_none(self):
        raw = _raw(yearly={})
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        assert row is None

    def test_year_keys_are_ints(self):
        """年份键应被转为 int。"""
        raw = _raw(yearly={"2018": 0.10, "2019": 0.12, "2020": 1.35})
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        assert row is not None
        assert 2018 in row.yearly
        assert isinstance(next(iter(row.yearly.keys())), int)

    def test_ex_anomaly_ar_proxy_computed(self):
        yearly = {2018: 0.10, 2019: 0.12, 2020: 1.35, 2021: 0.08}
        raw = _raw(yearly=yearly)
        row = build_yearly_row(raw, anomaly_year=2020)
        expected_ar = (0.10 + 0.12 + 0.08) / 3
        assert row.ex_anomaly_ar_proxy == pytest.approx(expected_ar, abs=1e-6)


class TestBuildReport:
    """build_report 整合验证。"""

    def test_all_years_aggregated(self):
        raws = [
            _raw("s1", yearly={2018: 0.10, 2019: 0.12, 2020: 1.35}),
            _raw("s2", yearly={2019: 0.08, 2020: 0.90, 2021: 0.05}),
        ]
        rows = [build_yearly_row(r, 2020) for r in raws]
        rows = [r for r in rows if r is not None]
        report = build_report(rows, anomaly_year=2020)
        assert 2018 in report.all_years
        assert 2019 in report.all_years
        assert 2020 in report.all_years
        assert 2021 in report.all_years

    def test_n_stable_count(self):
        raws = [
            _raw("stable1", yearly={2018: 0.10, 2019: 0.12, 2020: 1.35, 2021: 0.08}),
            _raw("unstable", yearly={2018: -0.15, 2019: -0.20, 2020: 1.35, 2021: -0.10}),
        ]
        rows = [build_yearly_row(r, 2020) for r in raws if r]
        rows = [r for r in rows if r is not None]
        report = build_report(rows, anomaly_year=2020)
        assert report.n_stable == 1

    def test_median_anomaly_contribution_computed(self):
        raws = [
            _raw("s1", yearly={2018: 0.10, 2020: 0.50}),
            _raw("s2", yearly={2018: 0.20, 2020: 0.80}),
        ]
        rows = [build_yearly_row(r, 2020) for r in raws if r]
        rows = [r for r in rows if r is not None]
        report = build_report(rows, anomaly_year=2020)
        assert report.median_anomaly_contribution > 0


class TestSaveReport:
    """save_report I/O 验证。"""

    def test_save_and_reload(self, tmp_path):
        raw = _raw()
        row = build_yearly_row(raw, anomaly_year=ANOMALY_YEAR)
        report = build_report([row], anomaly_year=ANOMALY_YEAR)
        out_path = tmp_path / "report.json"
        save_report(report, out_path)
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["n_strategies"] == 1
        assert "strategies" in data
        assert len(data["strategies"]) == 1
        assert str(ANOMALY_YEAR) in data["strategies"][0]["yearly_returns"]


class TestLoadFromFiles:
    """_load_from_files 文件扫描验证（无需 DB）。"""

    def test_scan_summary_json(self, tmp_path):
        """创建虚拟 summary.json，验证能正确加载 yearly_returns。"""
        strategy_dir = tmp_path / "test_strat" / "20250101_120000"
        strategy_dir.mkdir(parents=True)
        summary = {
            "strategy_name": "test_strat",
            "run_id": "20250101_120000",
            "start_date": "2018-01-02",
            "end_date": "2022-12-30",
            "metrics": {
                "annual_return": 0.30,
                "sharpe_ratio": 1.2,
                "max_drawdown": 0.18,
                "yearly_returns": {"2018": 0.20, "2019": 0.15, "2020": 1.35},
            },
        }
        (strategy_dir / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        # 临时替换 OUTPUT_DIR
        import scripts.analyze_yearly_returns as _mod
        orig = _mod.OUTPUT_DIR
        _mod.OUTPUT_DIR = tmp_path
        try:
            records = _load_from_files(strategy_filter=None)
        finally:
            _mod.OUTPUT_DIR = orig

        assert len(records) == 1
        assert records[0]["strategy_name"] == "test_strat"
        assert 2020 in records[0]["yearly_returns"]

    def test_missing_yearly_returns_skipped(self, tmp_path):
        """无 yearly_returns 的 summary.json 应被跳过。"""
        strategy_dir = tmp_path / "no_yearly"
        strategy_dir.mkdir()
        summary = {"strategy_name": "no_yearly", "metrics": {"annual_return": 0.10}}
        (strategy_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

        import scripts.analyze_yearly_returns as _mod
        orig = _mod.OUTPUT_DIR
        _mod.OUTPUT_DIR = tmp_path
        try:
            records = _load_from_files(strategy_filter=None)
        finally:
            _mod.OUTPUT_DIR = orig

        assert len(records) == 0
