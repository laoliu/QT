"""Canonical TuShare training reuses the configured shared raw cache."""
import pytest

from aipha.engines.aipha_ml_engine import _tushare_cache_dir


def test_research_uses_data_panel_root_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_CACHE_DIR", raising=False)
    monkeypatch.setenv("QTDATA_DIR", str(tmp_path / "raw-data"))
    monkeypatch.chdir(tmp_path)
    assert _tushare_cache_dir(None) == tmp_path / "raw-data/cache/equity_tushare"
    assert not (tmp_path / "raw-data").exists()


def test_explicit_shared_cache_root_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "shared-cache"))
    monkeypatch.setenv("QTDATA_DIR", str(tmp_path / "other-data"))
    assert _tushare_cache_dir("") == tmp_path / "shared-cache/equity_tushare"
    assert _tushare_cache_dir(str(tmp_path / "run-cache")) == tmp_path / "run-cache"


def test_explicit_relative_cache_is_anchored_to_project_not_cwd(tmp_path, monkeypatch):
    from aipha.utils import paths

    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.chdir(tmp_path)
    assert _tushare_cache_dir("data/cache/research") == tmp_path / "project/data/cache/research"


def test_train_tushare_passes_shared_cache_to_its_provider(tmp_path, monkeypatch):
    from aipha.data import tushare_equity_provider
    from aipha.engines.aipha_ml_engine import run
    from aipha.research import ResearchPipeline

    monkeypatch.delenv("DATA_CACHE_DIR", raising=False)
    monkeypatch.setenv("QTDATA_DIR", str(tmp_path / "data-panel"))
    provider = object()
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return provider

    def train(self, source, output, **kwargs):
        assert source is provider
        return {"run_envelope": {}, "run_id": "cache-contract", "strategy_id": "ml",
                "strategy_version": "1", "data_as_of": "2026-10-16"}

    monkeypatch.setattr(tushare_equity_provider, "get_equity_provider", factory)
    monkeypatch.setattr(ResearchPipeline, "run_tushare", train)
    result = run({"action": "train_tushare", "startDate": "20200101", "endDate": "20261016"})
    assert result["ok"], result
    assert captured["cache_dir"] == str(tmp_path / "data-panel/cache/equity_tushare")


@pytest.mark.parametrize("value", [False, 1, {}])
def test_malformed_explicit_cache_does_not_silently_select_another_source(value):
    with pytest.raises(ValueError, match="cacheDir"):
        _tushare_cache_dir(value)
