"""tests/backtest/test_benchmark_registry.py — BenchmarkRegistry 单元测试"""
from __future__ import annotations

import pandas as pd
import pytest

from aipha.backtest.benchmark_registry import (
    BenchmarkInfo,
    BenchmarkRegistry,
    get_default_registry,
)


# ---------------------------------------------------------------------------
# 基础注册 & 查询
# ---------------------------------------------------------------------------

def test_register_and_get():
    r = BenchmarkRegistry()
    r.register("hs300", "399300.SZ", description="沪深300", asset_type="index")
    info = r.get("hs300")
    assert info.name == "hs300"
    assert info.ts_code == "399300.SZ"
    assert info.asset_type == "index"


def test_register_duplicate_overwrites():
    r = BenchmarkRegistry()
    r.register("hs300", "399300.SZ", description="v1")
    r.register("hs300", "399300.SZ", description="v2")
    assert r.get("hs300").description == "v2"


def test_register_empty_name_raises():
    r = BenchmarkRegistry()
    with pytest.raises(ValueError, match="name"):
        r.register("", "000001.SH")


def test_get_missing_raises_key_error():
    r = BenchmarkRegistry()
    with pytest.raises(KeyError, match="not found"):
        r.get("nonexistent")


def test_contains_operator():
    r = BenchmarkRegistry()
    r.register("csi500", "000905.SH")
    assert "csi500" in r
    assert "hs300" not in r


def test_len():
    r = BenchmarkRegistry()
    assert len(r) == 0
    r.register("a", "000001.SH")
    r.register("b", "000002.SH")
    assert len(r) == 2


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------

def test_list_all_returns_all():
    r = BenchmarkRegistry()
    r.register("hs300", "399300.SZ")
    r.register("csi500", "000905.SH")
    names = {b.name for b in r.list_all()}
    assert names == {"hs300", "csi500"}


def test_list_all_empty():
    r = BenchmarkRegistry()
    assert r.list_all() == []


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

def test_benchmark_info_to_dict():
    info = BenchmarkInfo(
        name="hs300",
        ts_code="399300.SZ",
        description="沪深300",
        asset_type="index",
    )
    d = info.to_dict()
    assert d["name"] == "hs300"
    assert d["ts_code"] == "399300.SZ"
    assert "description" in d


# ---------------------------------------------------------------------------
# get_default_registry
# ---------------------------------------------------------------------------

def test_default_registry_has_four_benchmarks():
    r = get_default_registry()
    assert len(r) == 4


def test_default_registry_names():
    r = get_default_registry()
    for name in ("hs300", "csi500", "csi1000", "equal_weight_cb"):
        assert name in r, f"Missing benchmark: {name}"


def test_default_registry_ts_codes():
    r = get_default_registry()
    assert r.get("hs300").ts_code == "399300.SZ"
    assert r.get("csi500").ts_code == "000905.SH"
    assert r.get("csi1000").ts_code == "000852.SH"
    assert r.get("equal_weight_cb").ts_code is None  # 内部计算


# ---------------------------------------------------------------------------
# get_returns — cb_custom 类型（不访问网络）
# ---------------------------------------------------------------------------

def test_get_returns_cb_custom_returns_empty_series():
    r = get_default_registry()
    result = r.get_returns("equal_weight_cb", "2024-01-01", "2024-12-31")
    assert isinstance(result, pd.Series)
    assert result.empty


def test_get_returns_missing_ts_code_raises():
    r = BenchmarkRegistry()
    r.register("custom_no_code", None, asset_type="index")
    with pytest.raises(ValueError, match="ts_code"):
        r.get_returns("custom_no_code", "2024-01-01", "2024-12-31")
