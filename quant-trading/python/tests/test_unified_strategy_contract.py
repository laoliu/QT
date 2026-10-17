import json
from pathlib import Path

from strategies.base import get_all_strategies


ROOT = Path(__file__).resolve().parents[2]


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


def test_default_low_frequency_research_strategy_is_in_shared_catalog():
    catalog = json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8"))
    default = next(entry for entry in catalog["entries"] if entry["id"] == catalog["defaultResearchStrategy"])
    assert default["id"] == "aipha-ml-ranking"
    assert default["engine"] == "aipha"
    assert default["signalFormat"] == "target_position"
    assert {"fundamentals_pit", "index_membership_pit", "features"}.issubset(default["dataRequirements"])
