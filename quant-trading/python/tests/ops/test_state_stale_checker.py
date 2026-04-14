"""Tests for state_stale_checker.py (B2: R88)

Validates:
- check_state_stale detects stale/missing/fresh state files
- CLI returns correct exit codes
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest


class TestCheckStateStale:
    """state_stale_checker.check_state_stale 单元测试。"""

    def test_fresh_state_is_ok(self, tmp_path):
        """刚写入的 state.json 不报陈旧。"""
        from scripts.ops.state_stale_checker import check_state_stale

        (tmp_path / "combo1").mkdir()
        sf = tmp_path / "combo1" / "state.json"
        sf.write_text("{}", encoding="utf-8")
        results = check_state_stale(state_dir=tmp_path, threshold_hours=24)
        assert len(results) == 1
        assert results[0]["stale"] is False
        assert results[0]["status"] == "ok"

    def test_stale_state_detected(self, tmp_path):
        """超过阈值的 state.json 被检测为 stale。"""
        from scripts.ops.state_stale_checker import check_state_stale
        import os

        (tmp_path / "combo1").mkdir()
        sf = tmp_path / "combo1" / "state.json"
        sf.write_text("{}", encoding="utf-8")
        # 回拨 mtime 48 小时
        old_ts = time.time() - 48 * 3600
        os.utime(sf, (old_ts, old_ts))
        results = check_state_stale(state_dir=tmp_path, threshold_hours=24)
        assert len(results) == 1
        assert results[0]["stale"] is True
        assert results[0]["status"] == "stale"

    def test_missing_state_file(self, tmp_path):
        """目录存在但无 state.json。"""
        from scripts.ops.state_stale_checker import check_state_stale

        (tmp_path / "combo1").mkdir()
        results = check_state_stale(state_dir=tmp_path, threshold_hours=24)
        assert len(results) == 1
        assert results[0]["stale"] is True
        assert results[0]["status"] == "missing"

    def test_paper_state_json_also_found(self, tmp_path):
        """paper_state.json 也能被检测到。"""
        from scripts.ops.state_stale_checker import check_state_stale

        (tmp_path / "combo1").mkdir()
        sf = tmp_path / "combo1" / "paper_state.json"
        sf.write_text("{}", encoding="utf-8")
        results = check_state_stale(state_dir=tmp_path, threshold_hours=24)
        assert results[0]["stale"] is False

    def test_empty_dir_returns_empty(self, tmp_path):
        """空目录返回空列表。"""
        from scripts.ops.state_stale_checker import check_state_stale

        results = check_state_stale(state_dir=tmp_path, threshold_hours=24)
        assert results == []

    def test_nonexistent_dir(self, tmp_path):
        """不存在的目录返回空列表。"""
        from scripts.ops.state_stale_checker import check_state_stale

        results = check_state_stale(
            state_dir=tmp_path / "nonexistent",
            threshold_hours=24,
        )
        assert results == []

    def test_custom_threshold(self, tmp_path):
        """自定义阈值 (1h) — 刚写入不陈旧。"""
        from scripts.ops.state_stale_checker import check_state_stale

        (tmp_path / "combo1").mkdir()
        sf = tmp_path / "combo1" / "state.json"
        sf.write_text("{}", encoding="utf-8")
        results = check_state_stale(state_dir=tmp_path, threshold_hours=1)
        assert results[0]["stale"] is False

    def test_multiple_portfolios(self, tmp_path):
        """多个组合分别检查。"""
        import os
        from scripts.ops.state_stale_checker import check_state_stale

        for name in ("plan_a", "cb_aggressive"):
            d = tmp_path / name
            d.mkdir()
            sf = d / "state.json"
            sf.write_text("{}", encoding="utf-8")
        # 将 cb_aggressive 回拨 30h
        old_ts = time.time() - 30 * 3600
        os.utime(tmp_path / "cb_aggressive" / "state.json", (old_ts, old_ts))

        results = check_state_stale(state_dir=tmp_path, threshold_hours=24)
        assert len(results) == 2
        by_id = {r["portfolio_id"]: r for r in results}
        assert by_id["plan_a"]["stale"] is False
        assert by_id["cb_aggressive"]["stale"] is True


class TestStateStaleCheckerCli:
    """state_stale_checker CLI 测试。"""

    def test_cli_returns_0_when_fresh(self, tmp_path):
        """全部正常时返回 0。"""
        from scripts.ops.state_stale_checker import main

        (tmp_path / "combo1").mkdir()
        (tmp_path / "combo1" / "state.json").write_text("{}")
        code = main(["--state-dir", str(tmp_path)])
        assert code == 0

    def test_cli_returns_1_when_stale(self, tmp_path):
        """存在陈旧时返回 1。"""
        import os
        from scripts.ops.state_stale_checker import main

        (tmp_path / "combo1").mkdir()
        sf = tmp_path / "combo1" / "state.json"
        sf.write_text("{}")
        old_ts = time.time() - 48 * 3600
        os.utime(sf, (old_ts, old_ts))
        code = main(["--state-dir", str(tmp_path)])
        assert code == 1
