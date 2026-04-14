"""Tests for Barra style exposure monitor (O6)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aipha.risk.barra_style_monitor import (
    STYLE_FACTORS,
    BarraStyleMonitor,
    ExposureAlert,
    StyleExposure,
    StyleMonitorConfig,
    _zscore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_universe(n: int = 50, seed: int = 42) -> pd.DataFrame:
    """生成模拟股票 universe 数据."""
    rng = np.random.RandomState(seed)
    codes = [f"{600000 + i}.SH" for i in range(n)]
    return pd.DataFrame({
        "ts_code": codes,
        "total_mv": rng.lognormal(mean=10, sigma=1.5, size=n),
        "pe_ttm": rng.uniform(5, 80, size=n),
        "pct_chg": rng.normal(0.1, 0.3, size=n),  # proxy for mom
        "vol_20d": rng.uniform(0.1, 0.6, size=n),
        "turnover_rate": rng.uniform(0.5, 15, size=n),
    })


def _equal_weights(codes: list[str]) -> dict[str, float]:
    w = 1.0 / len(codes)
    return {c: w for c in codes}


# ---------------------------------------------------------------------------
# _zscore tests
# ---------------------------------------------------------------------------

class TestZscore:
    def test_basic(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        z = _zscore(s)
        assert abs(z.mean()) < 0.01
        assert z.std() > 0

    def test_clip(self):
        s = pd.Series([1] * 100 + [1000])
        z = _zscore(s)
        assert z.max() <= 3.01
        assert z.min() >= -3.01

    def test_constant(self):
        s = pd.Series([5.0] * 10)
        z = _zscore(s)
        assert (z == 0).all()

    def test_nan_handling(self):
        s = pd.Series([1.0, np.nan, 3.0, 4.0])
        z = _zscore(s)
        assert pd.isna(z.iloc[1])
        assert pd.notna(z.iloc[0])


# ---------------------------------------------------------------------------
# compute_style_scores tests
# ---------------------------------------------------------------------------

class TestComputeStyleScores:
    def test_output_columns(self):
        df = _sample_universe(20)
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        for f in STYLE_FACTORS:
            assert f in scores.columns, f"Missing factor {f}"
        assert "ts_code" in scores.columns

    def test_zscore_range(self):
        df = _sample_universe(100)
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        for f in STYLE_FACTORS:
            col = scores[f].dropna()
            assert col.max() <= 3.01
            assert col.min() >= -3.01

    def test_row_count(self):
        df = _sample_universe(30)
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        assert len(scores) == 30

    def test_missing_column_fills_zero(self):
        df = _sample_universe(10)
        df = df.drop(columns=["vol_20d"])
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        assert (scores["VOL"] == 0.0).all()


# ---------------------------------------------------------------------------
# compute_portfolio_exposure tests
# ---------------------------------------------------------------------------

class TestPortfolioExposure:
    def test_equal_weight_exposure(self):
        df = _sample_universe(20)
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        weights = _equal_weights(df["ts_code"].tolist())
        exp = mon.compute_portfolio_exposure(weights, scores, date(2025, 1, 1))
        assert isinstance(exp, StyleExposure)
        assert exp.n_holdings == 20
        for f in STYLE_FACTORS:
            assert abs(exp.exposures[f]) < 2.0

    def test_concentrated_size_exposure(self):
        """所有权重集中在最大市值股票 → SIZE 暴露应为正."""
        df = _sample_universe(50)
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        largest = scores.nlargest(3, "SIZE")["ts_code"].tolist()
        weights = _equal_weights(largest)
        exp = mon.compute_portfolio_exposure(weights, scores, date(2025, 1, 1))
        assert exp.exposures["SIZE"] > 0.5

    def test_history_appended(self):
        df = _sample_universe(10)
        mon = BarraStyleMonitor()
        scores = mon.compute_style_scores(df)
        weights = _equal_weights(df["ts_code"].tolist())
        mon.compute_portfolio_exposure(weights, scores, date(2025, 1, 1))
        mon.compute_portfolio_exposure(weights, scores, date(2025, 2, 1))
        assert len(mon._history) == 2


# ---------------------------------------------------------------------------
# check_alerts tests
# ---------------------------------------------------------------------------

class TestCheckAlerts:
    def test_no_alert_when_exposure_small(self):
        exp = StyleExposure(
            trade_date=date(2025, 1, 1),
            exposures={f: 0.0 for f in STYLE_FACTORS},
            n_holdings=10,
            total_weight=1.0,
        )
        mon = BarraStyleMonitor()
        alerts = mon.check_alerts(exp)
        assert len(alerts) == 0

    def test_alert_triggered(self):
        exp = StyleExposure(
            trade_date=date(2025, 1, 1),
            exposures={"SIZE": 2.0, "BM": 0.5, "MOM": 0.0,
                        "VOL": 0.0, "LIQUIDITY": 0.0},
            n_holdings=10,
            total_weight=1.0,
        )
        mon = BarraStyleMonitor()  # SIZE threshold=1.5
        alerts = mon.check_alerts(exp)
        assert len(alerts) == 1
        assert alerts[0].factor == "SIZE"
        assert alerts[0].direction == "long"

    def test_short_exposure_alert(self):
        exp = StyleExposure(
            trade_date=date(2025, 1, 1),
            exposures={"SIZE": -2.0, "BM": 0.0, "MOM": 0.0,
                        "VOL": 0.0, "LIQUIDITY": 0.0},
            n_holdings=10,
            total_weight=1.0,
        )
        mon = BarraStyleMonitor()
        alerts = mon.check_alerts(exp)
        assert alerts[0].direction == "short"

    def test_custom_thresholds(self):
        cfg = StyleMonitorConfig(alert_thresholds={"SIZE": 0.1})
        mon = BarraStyleMonitor(config=cfg)
        exp = StyleExposure(
            trade_date=date(2025, 1, 1),
            exposures={"SIZE": 0.5},
            n_holdings=5,
            total_weight=1.0,
        )
        alerts = mon.check_alerts(exp)
        assert len(alerts) == 1


# ---------------------------------------------------------------------------
# get_history_df / generate_report tests
# ---------------------------------------------------------------------------

class TestHistoryAndReport:
    def _build_monitor_with_history(self) -> BarraStyleMonitor:
        mon = BarraStyleMonitor()
        df = _sample_universe(20)
        scores = mon.compute_style_scores(df)
        weights = _equal_weights(df["ts_code"].tolist())
        for i in range(5):
            mon.compute_portfolio_exposure(
                weights, scores, date(2025, 1 + i, 1)
            )
        return mon

    def test_history_df_shape(self):
        mon = self._build_monitor_with_history()
        hdf = mon.get_history_df()
        assert len(hdf) == 5
        assert "trade_date" in hdf.columns
        for f in STYLE_FACTORS:
            assert f in hdf.columns

    def test_empty_history(self):
        mon = BarraStyleMonitor()
        assert mon.get_history_df().empty
        report = mon.generate_report()
        assert report["status"] == "no_data"

    def test_report_structure(self):
        mon = self._build_monitor_with_history()
        report = mon.generate_report()
        assert report["status"] in ("normal", "alert")
        assert report["n_snapshots"] == 5
        assert "latest_exposures" in report
        assert "history_stats" in report
        for f in STYLE_FACTORS:
            if f in report["history_stats"]:
                st = report["history_stats"][f]
                assert "mean" in st
                assert "std" in st
                assert "max_abs" in st
