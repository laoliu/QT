"""CB Event Study — 事件窗口异常收益(CAR)分析。

计算 CB 生命周期事件（强赎、下修等）前后的累积异常收益(CAR)，
用于验证事件驱动 alpha 的统计显著性。

学术依据：
- MacKinlay (1997) "Event Studies in Economics and Finance"
- 异常收益 AR_t = R_t - E[R_t]，E[R_t] 用市场模型/等权基准估计
- CAR(t1, t2) = Σ AR_t，检验 H0: CAR=0 用截面 t-test

使用方式：
    store = CBEventStore("data/cache/cb_events.db")
    result = compute_event_car(store, "down_revision", window=(-5, 20))
    print(result.summary)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from aipha.data.cb_event_tracker import CBEventRecord, CBEventStore, CBEventType

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data Structures
# ------------------------------------------------------------------

@dataclass
class EventCARResult:
    """事件研究结果容器。"""

    event_type: str
    window: tuple[int, int]
    n_events: int
    n_valid: int  # 有完整价格数据的事件数

    # Per-event CAR DataFrame: columns=[ts_code, event_date, announce_date, car]
    event_cars: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Cross-event average CAR at each relative day
    avg_car_by_day: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    # Statistical test
    car_mean: float = 0.0
    car_std: float = 0.0
    t_stat: float = 0.0
    p_value: float = 1.0

    @property
    def summary(self) -> dict:
        """Summary dict for reporting."""
        return {
            "event_type": self.event_type,
            "window": list(self.window),
            "n_events": self.n_events,
            "n_valid": self.n_valid,
            "car_mean": round(self.car_mean, 6),
            "car_std": round(self.car_std, 6),
            "t_stat": round(self.t_stat, 4),
            "p_value": round(self.p_value, 6),
            "significant_5pct": self.p_value < 0.05,
        }


# Price fetcher type: (ts_code, start_date, end_date) -> DataFrame with columns [date, close]
PriceFetcher = Callable[[str, str, str], pd.DataFrame]


def _compute_single_event_car(
    event: CBEventRecord,
    price_fetcher: PriceFetcher,
    window: tuple[int, int],
    benchmark_returns: pd.Series | None = None,
) -> dict | None:
    """Compute CAR for a single event. Returns None if data insufficient.

    Uses announce_date as anchor if available, else event_date.
    Fetches prices around window, computes daily returns, subtracts
    benchmark, and cumulates to get CAR.
    """
    anchor = event.announce_date if event.announce_date else event.event_date
    if not anchor:
        return None

    # Fetch price data with margin for window alignment
    pre_days, post_days = abs(window[0]), abs(window[1])
    # Request extra calendar days to cover trading-day gaps
    margin = max(pre_days, post_days) + 15

    try:
        anchor_dt = pd.Timestamp(anchor)
    except (ValueError, TypeError):
        return None

    start_dt = anchor_dt - pd.Timedelta(days=margin + pre_days * 2)
    end_dt = anchor_dt + pd.Timedelta(days=margin + post_days * 2)

    start_str = start_dt.strftime("%Y%m%d")
    end_str = end_dt.strftime("%Y%m%d")

    try:
        prices = price_fetcher(event.ts_code, start_str, end_str)
    except Exception:
        logger.warning("Price fetch failed for %s", event.ts_code)
        return None

    if prices is None or prices.empty or len(prices) < 5:
        return None

    # Normalize columns
    prices = prices.copy()
    if "date" in prices.columns:
        prices["date"] = pd.to_datetime(prices["date"])
    elif prices.index.name == "date":
        prices = prices.reset_index()
        prices["date"] = pd.to_datetime(prices["date"])
    else:
        return None

    prices = prices.sort_values("date").reset_index(drop=True)
    prices["ret"] = prices["close"].pct_change()

    # Find anchor index in trading calendar
    anchor_idx = prices["date"].searchsorted(anchor_dt)
    if anchor_idx >= len(prices):
        anchor_idx = len(prices) - 1
    # Snap to nearest trading day
    if prices["date"].iloc[anchor_idx] != anchor_dt:
        diffs = (prices["date"] - anchor_dt).abs()
        anchor_idx = diffs.idxmin()

    # Extract window
    win_start = anchor_idx + window[0]
    win_end = anchor_idx + window[1]
    if win_start < 0 or win_end >= len(prices):
        return None

    window_prices = prices.iloc[win_start:win_end + 1].copy()
    window_rets = window_prices["ret"].values.copy()

    # Subtract benchmark returns if provided
    if benchmark_returns is not None:
        for i, (_, row) in enumerate(window_prices.iterrows()):
            dt = row["date"]
            if dt in benchmark_returns.index:
                window_rets[i] -= benchmark_returns.loc[dt]

    # Replace NaN with 0 for first observation
    window_rets = np.where(np.isnan(window_rets), 0.0, window_rets)

    # Cumulative abnormal return
    car_series = np.cumsum(window_rets)
    car_final = float(car_series[-1])

    return {
        "ts_code": event.ts_code,
        "event_date": event.event_date,
        "announce_date": event.announce_date,
        "anchor_date": anchor,
        "car": car_final,
        "car_series": car_series.tolist(),
        "n_days": len(window_rets),
    }


def _cross_event_ttest(cars: list[float]) -> tuple[float, float, float, float]:
    """Cross-sectional t-test on event CARs.

    Returns (mean, std, t_stat, p_value).
    H0: mean(CAR) = 0 against H1: mean(CAR) != 0.
    """
    from scipy import stats

    arr = np.array(cars, dtype=float)
    n = len(arr)
    if n < 2:
        return (float(np.mean(arr)), 0.0, 0.0, 1.0)

    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std < 1e-12:
        return (mean, std, 0.0, 1.0 if abs(mean) < 1e-12 else 0.0)

    t_stat = mean / (std / np.sqrt(n))
    p_value = float(2 * stats.t.sf(abs(t_stat), df=n - 1))
    return (mean, std, float(t_stat), p_value)


def compute_event_car(
    store: CBEventStore,
    event_type: str,
    window: tuple[int, int] = (-5, 20),
    price_fetcher: PriceFetcher | None = None,
    benchmark_returns: pd.Series | None = None,
    pit_strict: bool = True,
    min_events: int = 5,
) -> EventCARResult:
    """计算指定事件类型的累积异常收益(CAR)。

    Args:
        store: CBEventStore instance
        event_type: Event type string (e.g. "down_revision")
        window: (pre_days, post_days) trading days around event
        price_fetcher: function(ts_code, start, end) -> DataFrame[date, close]
        benchmark_returns: market benchmark daily returns (index=date)
        pit_strict: use announce_date as event anchor (True) or event_date
        min_events: minimum events required for valid analysis

    Returns:
        EventCARResult with per-event CARs and statistical summary
    """
    # Query events from store
    events = store.query_events(event_type=event_type)
    n_events = len(events)

    if n_events < min_events:
        logger.warning(
            "Only %d events of type %s (need %d), returning empty result",
            n_events, event_type, min_events,
        )
        return EventCARResult(
            event_type=event_type,
            window=window,
            n_events=n_events,
            n_valid=0,
        )

    # Use a default price fetcher that raises if none provided
    if price_fetcher is None:
        raise ValueError(
            "price_fetcher is required. Provide a callable "
            "(ts_code, start_date, end_date) -> DataFrame[date, close]"
        )

    # Compute per-event CARs
    valid_results: list[dict] = []
    for event in events:
        result = _compute_single_event_car(
            event, price_fetcher, window, benchmark_returns
        )
        if result is not None:
            valid_results.append(result)

    n_valid = len(valid_results)
    logger.info(
        "%s: %d/%d events have valid CAR data",
        event_type, n_valid, n_events,
    )

    if n_valid < min_events:
        return EventCARResult(
            event_type=event_type,
            window=window,
            n_events=n_events,
            n_valid=n_valid,
        )

    # Build per-event DataFrame
    event_cars_df = pd.DataFrame([
        {
            "ts_code": r["ts_code"],
            "event_date": r["event_date"],
            "announce_date": r["announce_date"],
            "car": r["car"],
        }
        for r in valid_results
    ])

    # Average CAR by relative day
    max_len = max(len(r["car_series"]) for r in valid_results)
    car_matrix = np.full((n_valid, max_len), np.nan)
    for i, r in enumerate(valid_results):
        series = r["car_series"]
        car_matrix[i, :len(series)] = series
    avg_car = pd.Series(
        np.nanmean(car_matrix, axis=0),
        index=range(window[0], window[0] + max_len),
        name="avg_car",
    )

    # Statistical test on final CARs
    final_cars = [r["car"] for r in valid_results]
    mean, std, t_stat, p_value = _cross_event_ttest(final_cars)

    return EventCARResult(
        event_type=event_type,
        window=window,
        n_events=n_events,
        n_valid=n_valid,
        event_cars=event_cars_df,
        avg_car_by_day=avg_car,
        car_mean=mean,
        car_std=std,
        t_stat=t_stat,
        p_value=p_value,
    )
