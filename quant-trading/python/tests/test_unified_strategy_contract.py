import json
import ast
from pathlib import Path

import pytest

from strategies.base import get_all_strategies


ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8"))


def test_python_strategies_expose_canonical_metadata():
    catalog = json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8"))
    catalog_ids = {entry["id"] for entry in catalog["entries"]}
    strategies = get_all_strategies()
    assert strategies
    for strategy in strategies:
        assert strategy["id"] in catalog_ids
        assert strategy["engine"] in {"builtin", "backtrader", "vnpy", "qmt", "aipha"}
        assert strategy["version"]
        assert strategy["markets"]
        assert strategy["timeframes"]
        assert strategy["dataRequirements"]
        assert strategy["signalFormat"] in {"trading_signal", "target_position", "order_intent"}
        assert strategy["lifecycle"] in {"primary", "compatibility", "experimental", "live_only"}


def test_python_catalog_exposes_only_canonical_routes_for_migrated_strategies():
    by_id = {entry["id"]: entry for entry in CATALOG["entries"]}
    for strategy in get_all_strategies():
        migration = by_id[strategy["id"]].get("migration", {})
        assert migration.get("status") != "retired"
        if migration.get("status") == "migrated_to_builtin":
            assert strategy["engine"] == "builtin"
            assert strategy["availableEngines"] == ["builtin"]
            assert strategy["canonicalStrategyId"] == migration["canonicalId"]
            assert strategy["version"] == "2.0.0"


def test_python_engine_listing_uses_shared_catalog_without_inline_legacy_entries():
    from engines.backtrader_engine import list_strategies
    strategies = list_strategies()
    assert strategies == get_all_strategies()
    assert all(strategy.get("signalFormat") for strategy in strategies)


@pytest.mark.parametrize("entry", [entry for entry in CATALOG["entries"] if entry.get("migration") or entry.get("retiredImplementations")], ids=lambda entry: entry["id"])
def test_direct_python_engines_cannot_bypass_catalog_lifecycle(entry, monkeypatch):
    from engines import backtrader_engine, vnpy_engine

    def unexpected(*args, **kwargs):
        pytest.fail("A closed legacy route attempted to load data or strategies")

    monkeypatch.setattr(backtrader_engine, "_fetch_data", unexpected)
    monkeypatch.setattr(vnpy_engine, "_build_strategies", unexpected)
    for engine in (backtrader_engine, vnpy_engine):
        result = engine.run_backtest({"strategyId": entry["id"]})
        assert result["ok"] is False
        expected = "retired" if entry.get("retiredImplementations") or entry["migration"]["status"] == "retired" else "canonical builtin"
        assert expected in result["error"]


def test_every_retained_python_source_has_a_migration_or_explicit_retirement():
    # Inspect source declarations, including modules unavailable to auto-discovery.
    by_id = {entry["id"]: entry for entry in CATALOG["entries"]}
    for path in (ROOT / "python" / "strategies").glob("*_strategy.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "StrategyMeta":
                continue
            strategy_id = next(keyword.value.value for keyword in node.keywords if keyword.arg == "id")
            entry = by_id[strategy_id]
            if entry.get("migration"):
                assert entry["migration"]["status"] in {"retired", "migrated_to_builtin"}
                continue
            retirement = next(item for item in entry.get("retiredImplementations", []) if item["engine"] == "backtrader")
            assert retirement["source"] == path.relative_to(ROOT).as_posix()
            assert retirement["note"]


def test_default_low_frequency_research_strategy_is_in_shared_catalog():
    catalog = json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8"))
    default = next(entry for entry in catalog["entries"] if entry["id"] == catalog["defaultResearchStrategy"])
    assert default["id"] == "aipha-ml-ranking"
    assert default["engine"] == "aipha"
    assert default["signalFormat"] == "target_position"
    assert {"fundamentals_pit", "index_membership_pit", "features"}.issubset(default["dataRequirements"])
