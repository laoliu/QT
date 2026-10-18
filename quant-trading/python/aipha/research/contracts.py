"""Versioned contracts for the A-share research-to-execution boundary.

These small immutable records deliberately contain no provider or broker
objects.  They make it possible to replay a research run from local fixtures
and prevent the execution layer from reaching into a training object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd


MARKET_COLUMNS = (
    "trade_date", "symbol", "open", "high", "low", "close", "volume",
    "amount", "adj_factor", "is_suspended", "is_limit_up", "is_limit_down",
)
FEATURE_COLUMNS = ("trade_date", "symbol", "feature_version")


@dataclass(frozen=True, slots=True)
class MarketFrame:
    """One normalized daily market observation."""

    trade_date: date
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    adj_factor: float = 1.0
    is_suspended: bool = False
    is_limit_up: bool | None = None
    is_limit_down: bool | None = None


@dataclass(frozen=True, slots=True)
class FinancialSnapshot:
    """A point-in-time financial value visible on ``announce_date``."""

    symbol: str
    report_date: date
    announce_date: date
    metric_name: str
    value: float | None


@dataclass(frozen=True, slots=True)
class FeatureFrame:
    """Metadata describing a materialized feature table."""

    trade_date: date
    symbol: str
    feature_set: str
    feature_version: str
    feature_values: dict[str, float | None] = field(default_factory=dict)
    missing_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class Prediction:
    trade_date: date
    symbol: str
    model_id: str
    score: float
    rank: int
    feature_version: str


@dataclass(frozen=True, slots=True)
class TargetPosition:
    rebalance_date: date
    symbol: str
    target_weight: float
    reason: str
    risk_flags: tuple[str, ...] = ()
    run_id: str = ""
    model_id: str = ""
    signal_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        """Serialize using the cross-language camelCase contract."""
        return {
            "symbol": self.symbol,
            "rebalanceDate": self.rebalance_date.isoformat(),
            "targetWeight": self.target_weight,
            "reason": self.reason,
            "riskFlags": list(self.risk_flags),
            "runId": self.run_id,
            "modelId": self.model_id,
            "signalId": self.signal_id,
        }


@dataclass(frozen=True, slots=True)
class ResearchRun:
    run_id: str
    data_snapshot_id: str
    feature_version: str
    model_config_hash: str
    train_start: date
    train_end: date
    oos_start: date
    oos_end: date
    metrics: dict[str, float] = field(default_factory=dict)
    artifact_path: str = ""


def _require_columns(frame: pd.DataFrame, required: Iterable[str], name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} missing required columns: {', '.join(missing)}")


def validate_market_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize a daily market table without mutating it."""
    _require_columns(frame, MARKET_COLUMNS[:8], "market frame")
    out = frame.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    out["symbol"] = out["symbol"].astype(str)
    for column in ("open", "high", "low", "close", "volume", "amount", "adj_factor"):
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    if out["symbol"].eq("").any():
        raise ValueError("market frame contains an empty symbol")
    if out[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("market frame contains missing OHLC values")
    if (out["close"] <= 0).any():
        raise ValueError("market frame contains non-positive close prices")
    if out.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("market frame contains duplicate trade_date/symbol rows")
    return out.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def validate_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate a feature table and reject duplicate point-in-time rows."""
    _require_columns(frame, FEATURE_COLUMNS, "feature frame")
    out = frame.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    out["symbol"] = out["symbol"].astype(str)
    out["feature_version"] = out["feature_version"].astype(str)
    if out["feature_version"].eq("").any():
        raise ValueError("feature frame contains an empty feature_version")
    if out.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("feature frame contains duplicate trade_date/symbol rows")
    return out.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def price_limit_flags(
    frame: pd.DataFrame, *, price_column: str = "open",
) -> tuple[pd.Series, pd.Series]:
    """Evaluate directional execution limits at the requested price time.

    TuShare limit prices are unadjusted. A PIT panel keeps raw OHLC alongside
    its adjusted feature prices, so compare in raw units rather than using a
    close-derived flag to decide an earlier open fill. Legacy panels without
    price bands retain their explicitly supplied eligibility flags.
    """
    has_bands = {"up_limit", "down_limit"} & set(frame.columns)
    if not has_bands:
        flags = []
        for column in ("is_limit_up", "is_limit_down"):
            if column not in frame:
                flags.append(pd.Series(False, index=frame.index))
                continue
            values = frame[column]
            if not values.dropna().isin([True, False]).all():
                raise ValueError(f"{column} must contain boolean execution flags")
            flags.append(values.fillna(True).astype(bool))
        return flags[0], flags[1]
    if len(has_bands) != 2:
        raise ValueError("execution requires both up_limit and down_limit")
    bands = frame[["up_limit", "down_limit"]].apply(pd.to_numeric, errors="coerce")
    if (not np.isfinite(bands.to_numpy(dtype=float)).all()
            or (bands <= 0).any().any()
            or (bands["down_limit"] > bands["up_limit"]).any()):
        raise ValueError("execution limit prices must be positive, finite and ordered")
    raw_column = f"{price_column}_raw"
    price = pd.to_numeric(frame[raw_column if raw_column in frame else price_column], errors="coerce")
    if raw_column not in frame and "price_adjustment" in frame:
        adjusted = frame["price_adjustment"].eq("point_in_time_factor")
        if adjusted.any():
            if "adj_factor" not in frame:
                raise ValueError("adjusted execution prices require adj_factor or raw prices")
            factors = pd.to_numeric(frame["adj_factor"], errors="coerce")
            if not (np.isfinite(factors[adjusted]) & factors[adjusted].gt(0)).all():
                raise ValueError("adjusted execution prices require positive finite adj_factor")
            price = price.where(~adjusted, price / factors)
    # Half a tick tolerates floating-point noise without treating the price
    # one full tick inside the band as locked.
    invalid = ~np.isfinite(price) | price.le(0)
    return (invalid | (price >= bands["up_limit"] - 0.005),
            invalid | (price <= bands["down_limit"] + 0.005))


def visible_financial_snapshots(
    snapshots: Iterable[FinancialSnapshot],
    trade_date: date,
) -> list[FinancialSnapshot]:
    """Return only financial facts announced by the signal date (PIT-safe)."""
    as_of = pd.Timestamp(trade_date).date()
    visible: list[FinancialSnapshot] = []
    for item in snapshots:
        announced = pd.Timestamp(item.announce_date).date()
        report = pd.Timestamp(item.report_date).date()
        if announced < report:
            raise ValueError("financial announce_date cannot precede report_date")
        if announced <= as_of:
            visible.append(item)
    return visible
