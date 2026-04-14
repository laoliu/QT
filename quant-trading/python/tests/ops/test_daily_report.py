"""tests/ops/test_daily_report.py — B4 每日报告生成器测试 (R88)"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ops.daily_report import (
    load_state_file,
    load_metrics_file,
    load_nav_snapshot,
    _normalize_state,
    compute_cost_basis_value,
    build_daily_report,
    append_nav_history,
    format_markdown_report,
    generate_daily_reports,
    main as cli_main,
)


def _make_state(cash: float = 100000, positions: list | None = None):
    """创建最小 state 字典。"""
    return {
        "account": {
            "cash": cash,
            "frozen_cash": 0,
            "positions": positions or [],
        },
        "meta": {"saved_at": "2026-03-15T18:00:00", "strategy_name": "test"},
    }


def _make_metrics(nav: float = 510000, ret_pct: float = 2.0):
    """创建最小 metrics 字典。"""
    return {
        "strategy_name": "test_strategy",
        "timestamp": "2026-03-15T18:00:00",
        "nav": {
            "current": nav,
            "initial": 500000,
            "peak": nav,
            "total_return_pct": ret_pct,
            "daily_return_pct": 0.1,
        },
        "risk": {
            "current_drawdown_pct": 0.5,
            "max_drawdown_pct": 3.0,
            "rolling_sharpe": 1.2,
            "rolling_volatility_pct": 12.0,
        },
        "trading": {"total_fills": 10, "total_orders": 15, "total_bars": 100},
    }


def _make_r106_state(
    cash: float = 97208.71,
    positions: list | None = None,
    profile: str = "r106_attack",
):
    """创建 R106/R107 扁平格式 state 字典。"""
    if positions is None:
        positions = [
            {"code": "920608.BJ", "shares": 2000, "avg_cost": 16.56},
            {"code": "920685.BJ", "shares": 2600, "avg_cost": 12.48},
        ]
    return {
        "profile_name": profile,
        "initial_capital": 1000000.0,
        "cash": cash,
        "positions": positions,
    }


class TestLoadStateFile:
    """load_state_file 测试。"""

    def test_paper_state_json(self, tmp_path: Path):
        state = _make_state(cash=200000)
        (tmp_path / "paper_state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        result = load_state_file(tmp_path)
        assert result is not None
        assert result["account"]["cash"] == 200000

    def test_state_json_fallback(self, tmp_path: Path):
        state = _make_state(cash=300000)
        (tmp_path / "state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        result = load_state_file(tmp_path)
        assert result is not None
        assert result["account"]["cash"] == 300000

    def test_no_state_file(self, tmp_path: Path):
        result = load_state_file(tmp_path)
        assert result is None


class TestLoadMetricsFile:
    """load_metrics_file 测试。"""

    def test_portfolio_subdir(self, tmp_path: Path):
        sub = tmp_path / "port1"
        sub.mkdir()
        (sub / "live_metrics.json").write_text(
            json.dumps(_make_metrics()), encoding="utf-8"
        )
        result = load_metrics_file("port1", tmp_path)
        assert result is not None
        assert result["nav"]["current"] == 510000

    def test_global_fallback(self, tmp_path: Path):
        (tmp_path / "live_metrics.json").write_text(
            json.dumps(_make_metrics(nav=600000)), encoding="utf-8"
        )
        result = load_metrics_file("any_port", tmp_path)
        assert result is not None
        assert result["nav"]["current"] == 600000

    def test_no_metrics(self, tmp_path: Path):
        result = load_metrics_file("nonexistent", tmp_path)
        assert result is None


class TestComputeCostBasisValue:
    """compute_cost_basis_value 测试。"""

    def test_cash_plus_positions(self):
        state = _make_state(
            cash=100000,
            positions=[
                {"symbol": "123456.SZ", "qty": 100, "avg_cost": 110.5},
                {"symbol": "789012.SZ", "qty": 200, "avg_cost": 95.0},
            ],
        )
        val = compute_cost_basis_value(state)
        expected = 100000 + 100 * 110.5 + 200 * 95.0
        assert abs(val - expected) < 0.01

    def test_empty_positions(self):
        state = _make_state(cash=500000, positions=[])
        assert compute_cost_basis_value(state) == 500000


class TestBuildDailyReport:
    """build_daily_report 测试。"""

    def test_from_metrics(self):
        metrics = _make_metrics(nav=510000, ret_pct=2.0)
        state = _make_state(cash=50000, positions=[
            {"symbol": "AAA.SZ", "qty": 10, "avg_cost": 100},
        ])
        r = build_daily_report("port1", state, metrics, "2026-03-15")
        assert r["portfolio_id"] == "port1"
        assert r["nav"]["current"] == 510000
        assert r["n_positions"] == 1
        assert "live_metrics" in r["data_sources"]

    def test_from_state_only(self):
        state = _make_state(cash=100000, positions=[
            {"symbol": "BBB.SZ", "qty": 50, "avg_cost": 200},
        ])
        r = build_daily_report("port2", state, None, "2026-03-15")
        assert r["nav"]["current"] == 100000 + 50 * 200
        assert "state_cost_basis" in r["data_sources"]
        assert r["risk"] is None

    def test_no_data(self):
        r = build_daily_report("port3", None, None, "2026-03-15")
        assert r["nav"]["current"] == 0
        assert "none" in r["data_sources"]


class TestAppendNavHistory:
    """append_nav_history 测试。"""

    def test_append(self, tmp_path: Path):
        report = {
            "report_date": "2026-03-15",
            "generated_at": "2026-03-15T18:00:00",
            "nav": {"current": 510000, "total_return_pct": 2.0},
        }
        append_nav_history(tmp_path, report)
        nav_file = tmp_path / "nav_history.jsonl"
        assert nav_file.exists()
        line = json.loads(nav_file.read_text(encoding="utf-8").strip())
        assert line["nav"] == 510000
        assert line["date"] == "2026-03-15"

    def test_skip_zero_nav(self, tmp_path: Path):
        report = {
            "report_date": "2026-03-15",
            "generated_at": "2026-03-15T18:00:00",
            "nav": {"current": 0, "total_return_pct": 0},
        }
        append_nav_history(tmp_path, report)
        assert not (tmp_path / "nav_history.jsonl").exists()


class TestFormatMarkdownReport:
    """format_markdown_report 测试。"""

    def test_single_report(self):
        r = build_daily_report(
            "p1",
            _make_state(cash=100000),
            _make_metrics(nav=510000),
            "2026-03-15",
        )
        md = format_markdown_report([r])
        assert "Aipha-Q" in md
        assert "510,000.00" in md
        assert "p1" in md

    def test_empty(self):
        md = format_markdown_report([])
        assert "无可用组合数据" in md


class TestGenerateDailyReports:
    """generate_daily_reports 集成测试。"""

    def test_scan_portfolios(self, tmp_path: Path):
        # 创建两个组合目录
        p1 = tmp_path / "state" / "port_a"
        p1.mkdir(parents=True)
        (p1 / "paper_state.json").write_text(
            json.dumps(_make_state(cash=100000)), encoding="utf-8"
        )
        p2 = tmp_path / "state" / "port_b"
        p2.mkdir(parents=True)
        (p2 / "paper_state.json").write_text(
            json.dumps(_make_state(cash=200000)), encoding="utf-8"
        )

        m_dir = tmp_path / "metrics"
        m_dir.mkdir()
        # port_a 有 metrics
        ma = m_dir / "port_a"
        ma.mkdir()
        (ma / "live_metrics.json").write_text(
            json.dumps(_make_metrics(nav=510000)), encoding="utf-8"
        )

        reports = generate_daily_reports(
            state_dir=tmp_path / "state",
            metrics_dir=m_dir,
        )
        assert len(reports) == 2
        ids = {r["portfolio_id"] for r in reports}
        assert ids == {"port_a", "port_b"}

        # port_a 应有 live_metrics 数据源
        pa = [r for r in reports if r["portfolio_id"] == "port_a"][0]
        assert "live_metrics" in pa["data_sources"]

        # port_b 只有 state 数据
        pb = [r for r in reports if r["portfolio_id"] == "port_b"][0]
        assert "state_cost_basis" in pb["data_sources"]

        # nav_history.jsonl 应已写入
        assert (tmp_path / "state" / "port_a" / "nav_history.jsonl").exists()

        # 每组合 JSON 报告应已生成
        assert len(list(m_dir.glob("port_*/daily_report_*.json"))) >= 1


class TestDailyReportCli:
    """CLI 入口测试。"""

    def test_no_portfolios(self, tmp_path: Path):
        empty = tmp_path / "empty_state"
        empty.mkdir()
        rc = cli_main([
            "--state-dir", str(empty),
            "--metrics-dir", str(tmp_path / "no_metrics"),
        ])
        assert rc == 0

    def test_with_portfolio(self, tmp_path: Path):
        p1 = tmp_path / "state" / "my_port"
        p1.mkdir(parents=True)
        (p1 / "paper_state.json").write_text(
            json.dumps(_make_state(cash=500000)), encoding="utf-8"
        )
        m_dir = tmp_path / "metrics"
        m_dir.mkdir()

        rc = cli_main([
            "--state-dir", str(tmp_path / "state"),
            "--metrics-dir", str(m_dir),
            "--portfolio", "my_port",
        ])
        assert rc == 0
        # JSON 报告应已生成
        rpts = list(m_dir.glob("my_port/daily_report_*.json"))
        assert len(rpts) == 1


class TestNormalizeState:
    """_normalize_state 支持两种格式的测试。"""

    def test_state_manager_format(self):
        state = _make_state(cash=100000, positions=[
            {"symbol": "000001.SZ", "qty": 1000, "avg_cost": 15.5},
        ])
        state["meta"]["initial_capital"] = 500000
        cash, init, pos, name = _normalize_state(state)
        assert cash == 100000
        assert init == 500000
        assert len(pos) == 1
        assert pos[0]["symbol"] == "000001.SZ"
        assert pos[0]["qty"] == 1000
        assert name == "test"

    def test_r106_flat_format(self):
        state = _make_r106_state()
        cash, init, pos, name = _normalize_state(state)
        assert cash == 97208.71
        assert init == 1000000.0
        assert len(pos) == 2
        # code -> symbol 映射
        assert pos[0]["symbol"] == "920608.BJ"
        # shares -> qty 映射
        assert pos[0]["qty"] == 2000
        assert name == "r106_attack"

    def test_r106_empty_positions(self):
        state = _make_r106_state(cash=1000000, positions=[])
        cash, init, pos, name = _normalize_state(state)
        assert cash == 1000000
        assert len(pos) == 0


class TestComputeCostBasisR106:
    """compute_cost_basis_value 对 R106 扁平格式的测试。"""

    def test_r106_format(self):
        state = _make_r106_state()
        val = compute_cost_basis_value(state)
        expected = 97208.71 + 2000 * 16.56 + 2600 * 12.48
        assert abs(val - expected) < 0.01

    def test_r106_empty(self):
        state = _make_r106_state(cash=500000, positions=[])
        assert compute_cost_basis_value(state) == 500000


class TestBuildDailyReportR106:
    """build_daily_report 对 R106 扁平格式的测试。"""

    def test_r106_state_only(self):
        state = _make_r106_state()
        r = build_daily_report("r106_attack", state, None, "2026-03-15")
        assert r["portfolio_id"] == "r106_attack"
        assert r["nav"]["current"] > 0  # 不再是 0
        assert r["nav"]["initial"] == 1000000.0
        assert "state_cost_basis" in r["data_sources"]
        assert r["n_positions"] == 2
        assert r["cash"] == 97208.71
        # 验证 symbol 映射正确
        symbols = [p["symbol"] for p in r["positions"]]
        assert "920608.BJ" in symbols

    def test_r106_with_metrics_takes_priority(self):
        state = _make_r106_state()
        metrics = _make_metrics(nav=1050000, ret_pct=5.0)
        r = build_daily_report("r106_attack", state, metrics, "2026-03-15")
        assert r["nav"]["current"] == 1050000
        assert "live_metrics" in r["data_sources"]


class TestNavSnapshot:
    """nav_snapshot 盯市 NAV 测试。"""

    def test_load_nav_snapshot(self, tmp_path: Path):
        snap = {"date": "2026-03-15", "nav": 1023456.78,
                "initial_capital": 1000000, "total_return_pct": 2.35}
        (tmp_path / "nav_snapshot.json").write_text(
            json.dumps(snap), encoding="utf-8"
        )
        result = load_nav_snapshot(tmp_path)
        assert result is not None
        assert result["nav"] == 1023456.78

    def test_load_nav_snapshot_missing(self, tmp_path: Path):
        assert load_nav_snapshot(tmp_path) is None

    def test_build_report_with_nav_snapshot(self):
        state = _make_r106_state()
        snap = {"nav": 1050000, "initial_capital": 1000000,
                "total_return_pct": 5.0}
        r = build_daily_report(
            "r106_attack", state, None, "2026-03-15",
            nav_snapshot=snap,
        )
        assert r["nav"]["current"] == 1050000
        assert r["nav"]["total_return_pct"] == 5.0
        assert "nav_snapshot" in r["data_sources"]
        # state 持仓仍应正确解析
        assert r["n_positions"] == 2

    def test_metrics_overrides_nav_snapshot(self):
        """metrics 优先级高于 nav_snapshot。"""
        state = _make_r106_state()
        metrics = _make_metrics(nav=1100000, ret_pct=10.0)
        snap = {"nav": 1050000, "initial_capital": 1000000}
        r = build_daily_report(
            "r106_attack", state, metrics, "2026-03-15",
            nav_snapshot=snap,
        )
        assert r["nav"]["current"] == 1100000
        assert "live_metrics" in r["data_sources"]