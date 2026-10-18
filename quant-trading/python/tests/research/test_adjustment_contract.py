"""Missing source factors must never become a unit-factor research snapshot."""
from copy import deepcopy

import pandas as pd
import pytest

from aipha.research import snapshots


def _inputs():
    market = pd.DataFrame({
        "ts_code": ["600000.SH"] * 2, "trade_date": ["20240102", "20240103"],
        "open": [10., 5.], "high": [10., 5.], "low": [10., 5.], "close": [10., 5.],
        "vol": [1000., 2000.], "amount": [10000., 10000.], "adj_factor": [1., 2.],
    })
    kwargs = dict(
        daily_basic=None,
        stock_basic=pd.DataFrame({"ts_code": ["600000.SH"], "list_date": ["20200101"]}),
        index_snapshots={"20240101": ["600000.SH"]},
        st_status=market[["trade_date", "ts_code"]].assign(is_st=False),
        limit_prices=market[["trade_date", "ts_code"]].assign(up_limit=[11., 5.5], down_limit=[9., 4.5]),
        trade_calendar=pd.DataFrame({
            "cal_date": pd.date_range("20200101", "20240103"),
            "is_open": pd.date_range("20200101", "20240103").dayofweek < 5,
        }),
        spec=snapshots.SnapshotSpec("20240102", "20240103"),
    )
    return market, kwargs


@pytest.mark.parametrize("bad", ["missing_column", None, "unknown", 0., -1., float("nan"), float("inf"), -float("inf")])
def test_snapshot_rejects_missing_or_invalid_factor_before_features(monkeypatch, bad):
    market, kwargs = _inputs()
    if bad == "missing_column":
        market = market.drop(columns="adj_factor")
    else:
        market["adj_factor"] = market["adj_factor"].astype(object)
        market.loc[1, "adj_factor"] = bad
    before = deepcopy(market)
    monkeypatch.setattr(snapshots, "build_cn_equity_features", lambda _: pytest.fail("computed features with invalid factors"))
    with pytest.raises(ValueError, match="adj_factor"):
        snapshots.assemble_tushare_snapshot(market, **kwargs)
    pd.testing.assert_frame_equal(market, before)


def test_same_day_factors_remove_split_jump_and_preserve_raw_execution_prices():
    market, kwargs = _inputs()
    before = deepcopy(market)
    # Numeric strings are normalized, without changing source rows.
    market["adj_factor"] = ["1", "2"]
    result = snapshots.assemble_tushare_snapshot(market, **kwargs).panel
    assert result.close.tolist() == [10., 10.]
    assert result.close_raw.tolist() == [10., 5.]
    assert result.adj_factor.tolist() == [1., 2.]
    assert pd.api.types.is_numeric_dtype(result.adj_factor)
    pd.testing.assert_frame_equal(market.drop(columns="adj_factor"), before.drop(columns="adj_factor"))
    assert market.adj_factor.tolist() == ["1", "2"]


def test_finite_but_overflowing_factor_is_rejected_before_features(monkeypatch):
    market, kwargs = _inputs()
    market["adj_factor"] = 1e308
    monkeypatch.setattr(snapshots, "build_cn_equity_features", lambda _: pytest.fail("computed overflowing features"))
    with pytest.raises(ValueError, match="adjusted.*finite"):
        snapshots.assemble_tushare_snapshot(market, **kwargs)


def test_listing_source_survives_feature_materialization():
    market, kwargs = _inputs()
    kwargs["stock_basic"] = kwargs["stock_basic"].assign(
        list_date_source="tushare.bak_basic", list_date_observation_date="20240103",
    )
    result = snapshots.assemble_tushare_snapshot(market, **kwargs).panel
    assert result.list_date_source.eq("tushare.bak_basic").all()
    assert result.list_date_observation_date.eq("20240103").all()


def test_provider_financial_aliases_preserve_quarantine_nulls():
    source = pd.DataFrame({"trade_date": ["20240102", "20240103"], "symbol": "600000.SH",
                           "close": 10., "volume": 100., "amount": 1000.,
                           "gross_margin": [None, 20.], "or_yoy": [3., None]})
    before = source.copy(deep=True)
    result = snapshots.build_cn_equity_features(source)
    assert pd.isna(result.factor_grossprofit_margin.iloc[0])
    assert result.factor_grossprofit_margin.iloc[1] == 20.
    assert result.factor_revenue_yoy.iloc[0] == 3.
    assert pd.isna(result.factor_revenue_yoy.iloc[1])
    pd.testing.assert_frame_equal(source, before)
