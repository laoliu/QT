"""Capacity estimator for strategy lifecycle governance (TS-3).

Estimates conservative strategy capacity by scanning historical
position weights against daily trading volumes.

Core formula:
    cap_{t,i} = V_{t,i} * max_participation_rate / w_{t,i}
    capacity  = percentile(min_i(cap_{t,i}), q=conservative_percentile)

Tier judgment:
    S  — capacity >= 1000万
    L  — capacity >= 2亿
    none — capacity < 1000万
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TIER_S_THRESHOLD_CNY = 10_000_000.0   # 1000万
TIER_L_THRESHOLD_CNY = 200_000_000.0  # 2亿


@dataclass
class CapacityReport:
    """Result of capacity estimation."""
    estimated_capacity_cny: float = 0.0
    estimated_capacity_wan: float = 0.0
    method: str = "liquidity_constraint_scan"
    max_participation_rate: float = 0.10
    conservative_percentile: float = 5.0
    bottleneck_symbols: list[str] = field(default_factory=list)
    tier_suitable: str = "unknown"
    n_trading_days: int = 0

    def to_validation_capacity(self) -> dict[str, Any]:
        """Convert to validation.capacity block for strategy_catalog."""
        return {
            "conservative_wan": round(self.estimated_capacity_wan, 1),
            "method": self.method,
            "max_participation_rate": self.max_participation_rate,
            "conservative_percentile": self.conservative_percentile,
            "bottleneck_symbols": self.bottleneck_symbols[:3],
            "tier_suitable": self.tier_suitable,
            "n_trading_days": self.n_trading_days,
            "date": pd.Timestamp.now().strftime("%Y-%m-%d"),
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict."""
        return {
            "estimated_capacity_cny": self.estimated_capacity_cny,
            "estimated_capacity_wan": self.estimated_capacity_wan,
            "method": self.method,
            "max_participation_rate": self.max_participation_rate,
            "conservative_percentile": self.conservative_percentile,
            "bottleneck_symbols": self.bottleneck_symbols,
            "tier_suitable": self.tier_suitable,
            "n_trading_days": self.n_trading_days,
        }


class CapacityEstimator:
    """Estimate strategy capacity from historical positions + volumes."""

    def __init__(
        self,
        max_participation_rate: float = 0.10,
        conservative_percentile: float = 5.0,
    ) -> None:
        self.max_participation_rate = max_participation_rate
        self.conservative_percentile = conservative_percentile

    def estimate_from_positions(
        self,
        positions: pd.DataFrame,
        volumes: pd.DataFrame,
    ) -> CapacityReport:
        """Estimate capacity from position weights and volumes.

        Args:
            positions: DataFrame with columns [trade_date, ts_code, weight].
                       weight is fraction of NAV (0-1).
            volumes: DataFrame with columns [trade_date, ts_code, amount].
                     amount is daily trading amount in CNY.
        """
        merged = positions.merge(
            volumes, on=["trade_date", "ts_code"], how="inner",
        )
        # Filter rows with valid weight and amount
        merged = merged[
            (merged["weight"] > 1e-8) & (merged["amount"] > 0)
        ].copy()
        if merged.empty:
            return CapacityReport(tier_suitable="none")

        # Per-position-per-day capacity
        merged["cap_cny"] = (
            merged["amount"] * self.max_participation_rate / merged["weight"]
        )
        # Daily capacity = min across all positions that day
        daily_cap = merged.groupby("trade_date")["cap_cny"].min()

        capacity_cny = float(np.percentile(
            daily_cap.values, self.conservative_percentile,
        ))
        # Find bottleneck symbols (most frequently the daily min)
        merged["is_bottleneck"] = (
            merged.groupby("trade_date")["cap_cny"]
            .transform("min") == merged["cap_cny"]
        )
        bottleneck_counts = (
            merged[merged["is_bottleneck"]]
            .groupby("ts_code")
            .size()
            .sort_values(ascending=False)
        )
        bottleneck_symbols = bottleneck_counts.index.tolist()[:3]

        tier = self._determine_tier(capacity_cny)
        return CapacityReport(
            estimated_capacity_cny=capacity_cny,
            estimated_capacity_wan=round(capacity_cny / 10_000, 1),
            method=self.method if hasattr(self, "method") else "liquidity_constraint_scan",
            max_participation_rate=self.max_participation_rate,
            conservative_percentile=self.conservative_percentile,
            bottleneck_symbols=bottleneck_symbols,
            tier_suitable=tier,
            n_trading_days=len(daily_cap),
        )

    def _determine_tier(self, capacity_cny: float) -> str:
        """Determine suitable tier based on capacity."""
        if capacity_cny >= TIER_L_THRESHOLD_CNY:
            return "L"
        if capacity_cny >= TIER_S_THRESHOLD_CNY:
            return "S"
        return "none"
