"""Historical equity eligibility measured against an explicit exchange calendar."""
from __future__ import annotations

import numpy as np
import pandas as pd


LISTING_DAYS_BASIS = "exchange_open_sessions"


def validate_listing_threshold(value: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 252:
        raise ValueError("min_listing_days must be an integer >= 252 trading sessions")
    return int(value)


def normalize_trade_calendar(calendar: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    """Require every calendar date, including closures, to prove coverage.

    A list of observed stock bars or weekdays cannot prove that a missing
    session was a holiday. No weekday or current-calendar fallback is used.
    """
    if calendar is None or not {"cal_date", "is_open"} <= set(calendar.columns):
        raise ValueError("trade calendar requires cal_date and is_open")
    out = calendar.copy()
    out["cal_date"] = pd.to_datetime(out["cal_date"].astype(str), errors="raise").dt.normalize()
    start, end = pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize()
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError("invalid trade calendar range")
    out = out.loc[out.cal_date.between(start, end)].sort_values("cal_date")
    if out.cal_date.duplicated().any():
        raise ValueError("trade calendar contains duplicate dates")
    expected = pd.date_range(start, end, freq="D")
    if not pd.DatetimeIndex(out.cal_date).equals(expected):
        raise ValueError("trade calendar has incomplete date coverage")
    flags = pd.to_numeric(out["is_open"], errors="coerce")
    if not flags.isin([0, 1]).all():
        raise ValueError("trade calendar is_open must contain only 0 or 1")
    if "exchange" in out and not out["exchange"].eq("SSE").all():
        raise ValueError("trade calendar exchange must be SSE")
    out["is_open"] = flags.astype(int)
    return out.reset_index(drop=True)


def listing_session_counts(trade_dates: pd.Series, list_dates: pd.Series, calendar: pd.DataFrame) -> pd.Series:
    """Count exchange open sessions from listing through signal close, inclusive.

    Suspensions do not reset an issuer's age. History preceding the research
    tape must be present in the calendar, so a truncated tape cannot make an
    established issuer look newly listed.
    """
    days = pd.to_datetime(trade_dates.astype(str), errors="raise").dt.normalize()
    listed = pd.to_datetime(list_dates.astype(str), errors="raise").dt.normalize()
    if len(days) == 0 or days.isna().any() or listed.isna().any() or (days < listed).any():
        raise ValueError("listing dates must be present and no later than market dates")
    complete = normalize_trade_calendar(calendar, listed.min(), days.max())
    sessions = pd.DatetimeIndex(complete.loc[complete.is_open.eq(1), "cal_date"])
    if not days.isin(sessions).all():
        raise ValueError("market rows must fall on open trade calendar sessions")
    counts = sessions.searchsorted(days, side="right") - sessions.searchsorted(listed, side="left")
    return pd.Series(counts, index=trade_dates.index, dtype="int64")


def filter_historical_universe(frame: pd.DataFrame, *, min_listing_days: int = 252) -> pd.DataFrame:
    threshold = validate_listing_threshold(min_listing_days)
    required = {"is_index_member", "is_st", "listing_days", "listing_days_basis", "is_suspended"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("historical point-in-time universe fields required: " + ", ".join(sorted(missing)))
    if frame[list(required)].isna().any().any():
        raise ValueError("historical universe fields cannot be missing")
    if not frame["listing_days_basis"].eq(LISTING_DAYS_BASIS).all():
        raise ValueError("listing_days must be measured in exchange_open_sessions; rebuild legacy snapshots")

    def as_bool(series: pd.Series) -> pd.Series:
        if pd.api.types.is_bool_dtype(series):
            return series
        values = series.astype(str).str.strip().str.lower()
        if not values.isin({"0", "1", "false", "true", "no", "yes", "n", "y"}).all():
            raise ValueError(f"historical universe field {series.name} must contain boolean values")
        return values.isin({"1", "true", "yes", "y"})

    ages = pd.to_numeric(frame["listing_days"], errors="coerce")
    if not np.isfinite(ages).all() or (ages < 0).any() or (ages % 1 != 0).any():
        raise ValueError("listing_days must contain non-negative integer session counts")
    eligible = (
        as_bool(frame["is_index_member"]) & ~as_bool(frame["is_st"])
        & (ages >= threshold) & ~as_bool(frame["is_suspended"])
    )
    return frame.loc[eligible].reset_index(drop=True)
