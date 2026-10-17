from aipha.backtest import result_store
from aipha.backtest.config_parser import _is_test_or_temp_result
from aipha.utils.paths import PROJECT_ROOT


def test_database_override_precedes_output_override(monkeypatch):
    monkeypatch.setenv("AIPHA_DB_PATH", "custom/history.db")
    monkeypatch.setenv("AIPHA_OUTPUT_DIR", "other-output")
    assert result_store._resolve_db_path() == PROJECT_ROOT / "custom/history.db"


def test_output_override_is_shared_by_backtests(monkeypatch):
    monkeypatch.delenv("AIPHA_DB_PATH", raising=False)
    monkeypatch.setenv("AIPHA_OUTPUT_DIR", "other-output")
    assert result_store._resolve_db_path() == (
        PROJECT_ROOT / "other-output/backtest_index.db"
    )


def test_default_preserves_legacy_history_until_migration(monkeypatch, tmp_path):
    monkeypatch.delenv("AIPHA_DB_PATH", raising=False)
    monkeypatch.delenv("AIPHA_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("AIPHA_ALLOW_LEGACY_DB", raising=False)
    legacy = tmp_path / "legacy.db"
    legacy.touch()
    monkeypatch.setattr(result_store, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(result_store, "_LEGACY_DB_PATH", legacy)
    assert result_store._resolve_db_path() == tmp_path / "output/backtest_index.db"
    monkeypatch.setenv("AIPHA_ALLOW_LEGACY_DB", "1")
    assert result_store._resolve_db_path() == legacy
    current = tmp_path / "output/backtest_index.db"
    current.parent.mkdir()
    current.touch()
    assert result_store._resolve_db_path() == current


def test_pytest_basetemp_outside_system_temp_is_never_indexed(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/backtest/test_result_store_paths.py::case")
    result = tmp_path / "external-basetemp" / "summary.json"
    assert _is_test_or_temp_result(result)


def test_non_test_project_result_is_indexable(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("AIPHA_TEST_RUNTIME", raising=False)
    result = PROJECT_ROOT / "output" / "real-run" / "summary.json"
    assert not _is_test_or_temp_result(result)


def test_inherited_test_session_marker_blocks_external_basetemp(monkeypatch, tmp_path):
    """子进程/后台保存时没有 PYTEST_CURRENT_TEST 也不能污染共享索引。"""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AIPHA_TEST_RUNTIME", "1")
    result = tmp_path / "external-basetemp" / "summary.json"
    assert _is_test_or_temp_result(result)
