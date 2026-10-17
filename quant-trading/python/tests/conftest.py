"""
Shared test fixtures for quant-trading Python tests.
"""

import datetime
import os

import numpy as np
import pandas as pd
import pytest

# Persist for the whole pytest process and inherit into subprocesses/background
# workers; PYTEST_CURRENT_TEST is intentionally only set around one test call.
os.environ["AIPHA_TEST_RUNTIME"] = "1"

# 下面三个文件不是 pytest 测试，而是手工联调脚本：它们在模块顶层直接
# 对 QMT bridge（硬编码的局域网地址）发 HTTP 请求，并在 import 期调用
# sys.exit()。pytest 收集时会把 SystemExit 当成 INTERNALERROR，直接
# 中断整个 run —— 全量套件曾因此必须挂 --ignore 才能跑完。
# 保持文件原样（仍可 `python tests/test_direct_mode.py` 手工执行），
# 只把它们从自动收集里排除。
collect_ignore = [
    "test_direct_mode.py",
    "test_extended_bridge.py",
    "test_real_bridge.py",
]


@pytest.fixture(autouse=True)
def isolated_backtest_index(tmp_path, monkeypatch):
    """Backtest exports must not register synthetic runs in the operator's index."""
    monkeypatch.setenv("AIPHA_DB_PATH", str(tmp_path / "backtest_index.db"))


@pytest.fixture
def synthetic_klines():
    """Generate synthetic daily K-line data with a clear uptrend + downtrend pattern.

    Returns a DataFrame with columns: open, high, low, close, volume
    spanning ~200 trading days.
    """

    def _make(
        days: int = 200,
        start_price: float = 100.0,
        trend_up_pct: float = 0.3,
        seed: int = 42,
    ) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        prices = [start_price]
        mid = days // 2

        for i in range(1, days):
            # First half: uptrend; second half: downtrend
            if i < mid:
                drift = 0.002  # slight positive drift
            else:
                drift = -0.002
            change = drift + rng.normal(0, 0.015)
            prices.append(prices[-1] * (1 + change))

        dates = pd.bdate_range(start="2025-01-02", periods=days, freq="B")
        closes = np.array(prices)
        highs = closes * (1 + rng.uniform(0, 0.02, days))
        lows = closes * (1 - rng.uniform(0, 0.02, days))
        opens = closes * (1 + rng.uniform(-0.01, 0.01, days))
        volumes = rng.integers(100_000, 1_000_000, size=days)

        return pd.DataFrame(
            {
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
            },
            index=dates,
        )

    return _make


@pytest.fixture
def trending_up_klines(synthetic_klines):
    """200-day K-lines with a persistent uptrend."""

    def _make(days=200, seed=42):
        rng = np.random.default_rng(seed)
        prices = [100.0]
        for _ in range(1, days):
            prices.append(prices[-1] * (1 + 0.003 + rng.normal(0, 0.01)))

        dates = pd.bdate_range(start="2025-01-02", periods=days, freq="B")
        closes = np.array(prices)
        highs = closes * (1 + rng.uniform(0, 0.015, days))
        lows = closes * (1 - rng.uniform(0, 0.015, days))
        opens = closes * (1 + rng.uniform(-0.008, 0.008, days))
        volumes = rng.integers(100_000, 500_000, size=days)

        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )

    return _make
