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


@pytest.fixture
def provider_factory(monkeypatch):
    from types import SimpleNamespace
    from aipha.data import tushare_equity_provider as module
    from aipha.data import tushare_gateway

    def offline_call(*args, **kwargs):
        pytest.fail("cache routing must not query TuShare")

    gateway = SimpleNamespace(_token=None, api=object(), call=offline_call)
    monkeypatch.setattr(tushare_gateway, "get_tushare_gateway", lambda **kw: gateway)
    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    monkeypatch.setattr(module, "_equity_providers", {})
    return module.get_equity_provider


def test_factory_keeps_real_parquet_caches_separate(tmp_path, provider_factory):
    import pandas as pd

    roots = [tmp_path / "first", tmp_path / "second"]
    for root, close in zip(roots, [10.0, 20.0]):
        root.mkdir()
        pd.DataFrame({"ts_code": ["000001.SZ"], "close": [close]}).to_parquet(
            root / "daily_20261016.parquet", index=False
        )
    before = [(root / "daily_20261016.parquet").read_bytes() for root in roots]
    first = provider_factory(cache_dir=str(roots[0]))
    second = provider_factory(cache_dir=str(roots[1]))
    assert second is not first
    assert first.get_daily_snapshot("20261016", max_wait=0).close.iloc[0] == 10.0
    assert second.get_daily_snapshot("20261016", max_wait=0).close.iloc[0] == 20.0
    assert provider_factory(cache_dir=str(roots[0] / ".." / "first")) is first
    assert before == [(root / "daily_20261016.parquet").read_bytes() for root in roots]


def test_factory_relative_root_does_not_follow_cwd(tmp_path, monkeypatch, provider_factory):
    from aipha.utils import paths

    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "project")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    first = provider_factory(cache_dir="data/cache/equity_tushare")
    monkeypatch.chdir(elsewhere)
    assert provider_factory(cache_dir="data/cache/equity_tushare") is first
    assert first.cache_dir == tmp_path / "project/data/cache/equity_tushare"
    assert not (elsewhere / "data").exists()


def test_concurrent_factory_calls_share_one_provider(tmp_path, monkeypatch, provider_factory):
    from concurrent.futures import ThreadPoolExecutor
    from aipha.data import tushare_equity_provider as module

    constructor = module.TuShareEquityProvider
    calls = []

    def counted(**kwargs):
        calls.append(kwargs["cache_dir"])
        return constructor(**kwargs)

    monkeypatch.setattr(module, "TuShareEquityProvider", counted)
    with ThreadPoolExecutor(max_workers=8) as pool:
        providers = list(pool.map(lambda _: provider_factory(cache_dir=str(tmp_path)), range(24)))
    assert len(calls) == 1
    assert all(provider is providers[0] for provider in providers)
