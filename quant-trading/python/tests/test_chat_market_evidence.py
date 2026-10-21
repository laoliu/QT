"""Regression coverage for the market evidence exposed to AI chat."""
import importlib
import math
import sys
from types import SimpleNamespace

import pandas as pd
import pytest


@pytest.fixture(params=["engines.fund_flow", "aipha.engines.fund_flow"])
def flow(request):
    return importlib.import_module(request.param)


@pytest.mark.parametrize("prefix", ["今日", ""])
@pytest.mark.parametrize("action", ["sector", "rank"])
def test_fund_flow_reads_today_prefixed_and_legacy_columns(flow, monkeypatch, action, prefix):
    row = {"名称": "电子", "代码": "002428", "今日涨跌幅": 1.2,
           prefix + "主力净流入-净额": 123456, prefix + "主力净流入-净占比": 3.4,
           prefix + "超大单净流入-净额": 789, prefix + "大单净流入-净额": 123}
    fetch = lambda **kwargs: pd.DataFrame([row])
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_sector_fund_flow_rank=fetch, stock_individual_fund_flow_rank=fetch))
    result = flow.run_fund_flow({"action": action, "limit": 1})
    item = result["sectors" if action == "sector" else "stocks"][0]
    assert item["mainNetIn"] == 123456
    assert item["mainNetInPct"] == 3.4
    assert item["superLargeNetIn"] == 789
    assert result["availability"] == "available"
    assert result["dataTime"] is None  # API has no source timestamp
    assert result["retrievedAt"]


@pytest.mark.parametrize("missing", [math.nan, math.inf, -math.inf])
def test_missing_fund_values_remain_unknown(flow, monkeypatch, missing):
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_sector_fund_flow_rank=lambda **kw: pd.DataFrame([{"名称": "电子", "今日主力净流入-净额": missing}])))
    result = flow.run_fund_flow({"action": "sector"})
    assert result["sectors"][0]["mainNetIn"] is None
    assert result["availability"] == "unavailable"


def north_rows(date="2026-10-21", sh=0, sz=0):
    return pd.DataFrame([
        {"资金方向": "北向", "板块": board, "资金净流入": value, "当日资金余额": 0, "交易日": date}
        for board, value in [("沪股通", sh), ("深股通", sz)]
    ])


def test_northbound_placeholder_is_not_zero_flow_and_market_history_is_labeled(flow, monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_hsgt_fund_flow_summary_em=lambda: north_rows(),
        stock_market_fund_flow=lambda: pd.DataFrame([{"日期": "2026-10-20", "主力净流入-净额": 123}]),
    ))
    result = flow.run_fund_flow({"action": "northbound"})
    assert result["availability"] == "not_disclosed"
    assert result["realtime"]["available"] is False
    assert result["realtime"]["northTotal"] is None
    assert result["realtime"]["hk2sh"] is None
    assert result["rawRealtime"]["hk2sh"] == 0
    assert result["dataTime"] is None
    assert result["historyType"] == "market_fund_flow"
    assert "不是北向" in result["historyNote"]


def test_northbound_does_not_fill_a_missing_channel_with_zero(flow, monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_hsgt_fund_flow_summary_em=lambda: north_rows("2024-08-16", 10, math.nan),
        stock_market_fund_flow=lambda: pd.DataFrame(),
    ))
    result = flow.run_fund_flow({"action": "northbound"})
    assert result["realtime"]["northTotal"] is None
    assert result["realtime"]["available"] is False


def test_northbound_old_measured_data_retains_its_date(flow, monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_hsgt_fund_flow_summary_em=lambda: north_rows("2024-08-16", 10, -3),
        stock_market_fund_flow=lambda: pd.DataFrame(),
    ))
    result = flow.run_fund_flow({"action": "northbound"})
    assert result["availability"] == "historical"
    assert result["realtime"]["northTotal"] == 7
    assert result["dataTime"] == "2024-08-16"


@pytest.mark.parametrize("symbol,code,market", [("002428.SZ", "002428", "sz"), ("sh600519", "600519", "sh")])
def test_stock_flow_preserves_stock_identity_and_latest_source_date(flow, monkeypatch, symbol, code, market):
    def fetch(**kwargs):
        assert kwargs == {"stock": code, "market": market}
        return pd.DataFrame([{"日期": "2026-10-20", "主力净流入-净额": 20}, {"日期": "2026-10-19", "主力净流入-净额": 10}])
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_individual_fund_flow=fetch))
    result = flow.run_fund_flow({"action": "stock", "symbol": symbol})
    assert result["symbol"] == symbol
    assert result["dataTime"] == "2026-10-20"
    assert [f["mainNetIn"] for f in result["flows"]] == [10, 20]


def test_stock_flow_does_not_treat_shanghai_index_as_shenzhen_stock(flow, monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("Index identity was silently sent to a stock endpoint")
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_individual_fund_flow=unexpected))
    assert "error" in flow.run_fund_flow({"action": "stock", "symbol": "000001.SH"})


@pytest.fixture
def rotation(monkeypatch):
    module = importlib.import_module("aipha.engines.sector_rotation")
    sectors = [
        {"name": "上涨", "code": "a", "changePct": 3, "type": "industry"},
        {"name": "下跌", "code": "b", "changePct": -8, "type": "industry"},
        {"name": "未知", "code": "c", "changePct": None, "type": "concept"},
    ]
    monkeypatch.setattr(module, "_sector_ranking", lambda *args: {"sectors": sectors})
    def history(name, days, errors):
        return {"history": [
            {"date": f"2026-10-{19+i}", "close": close, "volume": 100 + i * 10}
            for i, close in enumerate([100, 110, 121] if name == "上涨" else [100, 90, 81])
        ]}
    monkeypatch.setattr(module, "_sector_history", history)
    return module


@pytest.mark.parametrize("entry", ["engines.sector_analysis", "aipha.engines.sector_analysis"])
def test_hot_dispatch_works_in_both_packages_and_does_not_invent_breadth(rotation, entry):
    result = importlib.import_module(entry).run_sector_analysis({"action": "hot", "limit": 2})
    assert [s["name"] for s in result["hotSectors"]] == ["上涨"]
    assert result["unavailableMetrics"] == ["turnoverRate", "upCount", "mainNetIn"]
    assert "strength" not in result["hotSectors"][0]
    assert result["dataTime"] is None


@pytest.mark.parametrize("action", ["rotation", "momentum"])
def test_history_metrics_use_observed_returns_and_disclose_sample(rotation, action):
    result = rotation.run_sector_rotation({"action": action, "days": 2, "limit": 2})
    assert result["sectors"][0]["nDayChange"] == 21
    assert result["dataTime"] == "2026-10-21"
    assert "非全市场" in result["scope"]
    if action == "momentum":
        assert 0 <= result["sectors"][0]["momentumScore"] <= 100
    else:
        assert result["spread"] == 40


def test_insufficient_history_is_reported_not_a_fake_20_day_return(rotation):
    result = rotation.run_sector_rotation({"action": "rotation", "days": 20})
    assert "error" in result
    assert result["errors"]


def test_missing_volume_does_not_get_neutral_momentum_points(rotation, monkeypatch):
    monkeypatch.setattr(rotation, "_sector_history", lambda *args: {"history": [
        {"date": f"2026-10-{19+i}", "close": c} for i, c in enumerate([100, 110, 121])
    ]})
    result = rotation.run_sector_rotation({"action": "momentum", "days": 2})
    assert all(s["momentumScore"] is None for s in result["sectors"])


def test_partial_history_failures_leave_valid_boards_available(rotation, monkeypatch):
    original = rotation._sector_history
    def history(name, *args):
        if name == "下跌":
            raise TimeoutError("source unavailable")
        return original(name, *args)
    monkeypatch.setattr(rotation, "_sector_history", history)
    result = rotation.run_sector_rotation({"action": "rotation", "days": 2})
    assert result["status"] == "partial"
    assert result["count"] == 1
    assert result["rotationSignal"] == "样本不足"
