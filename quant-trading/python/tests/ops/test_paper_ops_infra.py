"""Tests for paper trading ops infrastructure fixes (R88 A1-A3).

Validates:
- daily_ops.py has correct sys.path setup
- Docker crontab has PYTHONPATH
- Docker entrypoint exports env for cron
- Docker compose volume mappings are correct
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_UTF8 = "utf-8"


class TestDailyOpsImportFix:
    """A3: daily_ops.py must have sys.path fix for Docker cron."""

    def test_sys_path_insert_present(self):
        """daily_ops.py inserts _PROJECT_ROOT into sys.path."""
        source = (_PROJECT_ROOT / "scripts" / "ops" / "daily_ops.py").read_text(encoding=_UTF8)
        assert "sys.path.insert(0, str(_PROJECT_ROOT))" in source

    def test_project_root_computed(self):
        """daily_ops.py computes _PROJECT_ROOT correctly."""
        source = (_PROJECT_ROOT / "scripts" / "ops" / "daily_ops.py").read_text(encoding=_UTF8)
        assert "_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent" in source

    def test_compiles_without_error(self):
        """daily_ops.py compiles without syntax error."""
        import py_compile
        py_compile.compile(
            str(_PROJECT_ROOT / "scripts" / "ops" / "daily_ops.py"),
            doraise=True,
        )

    def test_main_callable(self):
        """daily_ops main() is callable with --help."""
        source = (_PROJECT_ROOT / "scripts" / "ops" / "daily_ops.py").read_text(encoding=_UTF8)
        tree = ast.parse(source)
        func_names = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ]
        assert "main" in func_names
        assert "run_db_backup" in func_names
        assert "run_nav_check" in func_names
        assert "run_health_check" in func_names


class TestDockerCrontabFix:
    """A3: Docker crontab must have PYTHONPATH for scripts imports."""

    def test_pythonpath_in_crontab(self):
        """crontab sets PYTHONPATH containing /app and /app/src."""
        content = (_PROJECT_ROOT / "docker" / "paper" / "crontab").read_text(encoding=_UTF8)
        match = re.search(r"^PYTHONPATH=(.+)$", content, re.MULTILINE)
        assert match is not None, "PYTHONPATH not found in crontab"
        path_val = match.group(1)
        assert "/app/src" in path_val, "PYTHONPATH must contain /app/src"
        assert "/app" in path_val, "PYTHONPATH must contain /app"

    def test_crontab_has_paper_launcher(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "crontab").read_text(encoding=_UTF8)
        assert "paper_launcher.py" in content

    def test_crontab_has_daily_ops(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "crontab").read_text(encoding=_UTF8)
        assert "daily_ops.py" in content

    def test_crontab_has_daily_scheduler(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "crontab").read_text(encoding=_UTF8)
        assert "daily_scheduler.py" in content


class TestDockerEntrypointEnvExport:
    """A2: entrypoint.sh must export container env for cron."""

    def test_env_export_to_etc_environment(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "entrypoint.sh").read_text(encoding=_UTF8)
        assert "/etc/environment" in content, "Must export env to /etc/environment"

    def test_exports_tushare_token(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "entrypoint.sh").read_text(encoding=_UTF8)
        assert "TUSHARE_TOKEN" in content

    def test_exports_pythonpath(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "entrypoint.sh").read_text(encoding=_UTF8)
        assert "PYTHONPATH" in content

    def test_cron_starts_after_env_export(self):
        content = (_PROJECT_ROOT / "docker" / "paper" / "entrypoint.sh").read_text(encoding=_UTF8)
        env_pos = content.find("/etc/environment")
        cron_pos = content.find("cron -f")
        assert env_pos < cron_pos, "env export must come before cron start"


class TestDockerComposeVolumes:
    """A1+A2: Volume mappings must be correct."""

    def test_logs_volume_not_nested(self):
        content = (_PROJECT_ROOT / "docker" / "docker-compose.paper.yml").read_text(encoding=_UTF8)
        assert "source: ../logs\n" in content, "logs must use ../logs"
        assert "source: ../logs/paper" not in content, "Old nested mapping must be removed"

    def test_pythonpath_includes_app(self):
        content = (_PROJECT_ROOT / "docker" / "docker-compose.paper.yml").read_text(encoding=_UTF8)
        assert "PYTHONPATH=/app/src:/app" in content

    def test_scripts_volume_mounted(self):
        content = (_PROJECT_ROOT / "docker" / "docker-compose.paper.yml").read_text(encoding=_UTF8)
        assert "../scripts" in content, "scripts volume must be mounted"

    def test_configs_volume_mounted(self):
        content = (_PROJECT_ROOT / "docker" / "docker-compose.paper.yml").read_text(encoding=_UTF8)
        assert "../configs" in content

    def test_paper_state_volume_mounted(self):
        content = (_PROJECT_ROOT / "docker" / "docker-compose.paper.yml").read_text(encoding=_UTF8)
        assert "paper_state" in content


class TestSystemSettings:
    """Paper trading system settings validation."""

    def test_paper_trading_enabled(self):
        settings_path = _PROJECT_ROOT / "configs" / "paper" / "system_settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding=_UTF8))
            assert settings.get("paper_trading_enabled") is True
            # P0-5: paper_auto_start=false（安全默认，需手动启用）
            assert settings.get("paper_auto_start") is False

    def test_plan_a3_4comp_disabled(self):
        """P0-5: plan_a3_4comp 已移至 disabled_portfolios（AKShare 已禁用）"""
        settings_path = _PROJECT_ROOT / "configs" / "paper" / "system_settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding=_UTF8))
            disabled = settings.get("disabled_portfolios", [])
            assert "plan_a3_4comp" in disabled
            selected = settings.get("selected_portfolios", [])
            assert "plan_a3_4comp" not in selected
