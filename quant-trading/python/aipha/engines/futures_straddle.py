"""
Futures straddle strategies: butterfly spread and premium/discount arbitrage.

Migrated from jtstock/futures_straddle/:
  - IC_butterfly.py → butterfly spread profit calculation
  - sys_premium.py  → premium/discount arbitrage strategy

These strategies work with Chinese index futures (IC, IF, IH)
and their monthly contracts.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

# Futures contract multiplier
CONTRACT_MULTIPLIER = {
    "IC": 200,  # CSI 500 Index × 200
    "IF": 300,  # CSI 300 Index × 300
    "IH": 300,  # SSE 50 Index × 300
}

# Margin ratios
MARGIN_RATIO = {
    "IC": 0.14,
    "IF": 0.12,
    "IH": 0.12,
}


@dataclass
class FuturesContract:
    """A single futures contract."""

    code: str          # e.g. "IC2106"
    product: str       # e.g. "IC"
    delivery: str      # e.g. "2106" (YYMM)
    price: float       # current price
    settlement: float  # settlement price
    volume: int = 0
    open_interest: int = 0

    @property
    def multiplier(self) -> int:
        return CONTRACT_MULTIPLIER.get(self.product, 200)

    @property
    def margin_ratio(self) -> float:
        return MARGIN_RATIO.get(self.product, 0.14)

    @property
    def contract_value(self) -> float:
        return self.price * self.multiplier

    @property
    def margin_required(self) -> float:
        return self.contract_value * self.margin_ratio


# ── Butterfly Spread ─────────────────────────────────────────────


@dataclass
class ButterflySpread:
    """Butterfly spread: buy near + buy far, sell 2× mid.

    Profit from time-spread convergence. Low-risk strategy that
    profits when the mid-month contract price is relatively expensive.

    Entry: buy 1 near + buy 1 far, sell 2 mid
    Exit: reverse all positions at expiry or when spread converges
    """

    near: FuturesContract
    mid: FuturesContract
    far: FuturesContract
    position_lots: int = 1

    def entry_cost(self) -> dict:
        """Calculate entry cost and margin for the butterfly position."""
        multi = self.near.multiplier
        lots = self.position_lots

        near_value = self.near.price * multi * lots
        mid_value = self.mid.price * multi * lots * 2
        far_value = self.far.price * multi * lots

        # Net cost = buy near + buy far - sell 2×mid
        net_premium = (self.near.price + self.far.price - 2 * self.mid.price) * multi * lots

        # Margin = max(buy side, sell side margin)
        margin = max(near_value + far_value, mid_value) * self.near.margin_ratio

        return {
            "netPremium": round(net_premium, 2),
            "marginRequired": round(margin, 2),
            "nearValue": round(near_value, 2),
            "midValue": round(mid_value, 2),
            "farValue": round(far_value, 2),
        }

    def calculate_pnl(
        self,
        near_exit: float,
        mid_exit: float,
        far_exit: float,
    ) -> dict:
        """Calculate P&L at exit prices.

        Butterfly P&L = (near_exit - near_entry) + (far_exit - far_entry) - 2*(mid_exit - mid_entry)
        """
        multi = self.near.multiplier
        lots = self.position_lots

        near_pnl = (near_exit - self.near.price) * multi * lots
        mid_pnl = (mid_exit - self.mid.price) * multi * lots * 2
        far_pnl = (far_exit - self.far.price) * multi * lots

        # We're long near+far, short 2×mid
        total_pnl = near_pnl + far_pnl - mid_pnl

        entry = self.entry_cost()
        return_pct = total_pnl / entry["marginRequired"] if entry["marginRequired"] > 0 else 0

        return {
            "nearPnL": round(near_pnl, 2),
            "midPnL": round(-mid_pnl, 2),  # short side
            "farPnL": round(far_pnl, 2),
            "totalPnL": round(total_pnl, 2),
            "returnPct": round(return_pct, 4),
            "entrySpread": round(self.near.price + self.far.price - 2 * self.mid.price, 2),
            "exitSpread": round(near_exit + far_exit - 2 * mid_exit, 2),
        }

    @staticmethod
    def analyze_spread_history(
        near_prices: list[float],
        mid_prices: list[float],
        far_prices: list[float],
        dates: list[str],
    ) -> dict:
        """Analyze historical spread for entry/exit signals."""
        spreads = []
        for i in range(len(near_prices)):
            if i < len(mid_prices) and i < len(far_prices):
                sp = near_prices[i] + far_prices[i] - 2 * mid_prices[i]
                spreads.append({"date": dates[i] if i < len(dates) else str(i), "spread": round(sp, 2)})

        values = [s["spread"] for s in spreads]
        if not values:
            return {"spreads": [], "mean": 0, "std": 0, "zScore": 0}

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values) if len(values) > 1 else 0
        std = math.sqrt(variance)
        z_score = (values[-1] - mean) / std if std > 0 else 0

        return {
            "spreads": spreads,
            "mean": round(mean, 2),
            "std": round(std, 2),
            "current": round(values[-1], 2),
            "zScore": round(z_score, 4),
            "signal": "entry" if abs(z_score) > 2 else ("monitor" if abs(z_score) > 1 else "neutral"),
        }


# ── Premium/Discount Arbitrage ───────────────────────────────────


class PremiumArbitrage:
    """Futures premium/discount arbitrage strategy.

    When futures trade at a significant premium/discount to spot index,
    open basis convergence trades near delivery.

    From jtstock/futures_straddle/sys_premium.py:
    - Opens positions ~15 trading days before near-month delivery
    - Profits from basis convergence (futures → spot at expiry)
    """

    def __init__(
        self,
        product: str = "IC",
        entry_days_before: int = 15,
        premium_threshold: float = 0.01,
        discount_threshold: float = -0.01,
    ):
        self.product = product
        self.entry_days_before = entry_days_before
        self.premium_threshold = premium_threshold
        self.discount_threshold = discount_threshold
        self.multi = CONTRACT_MULTIPLIER.get(product, 200)

    def calculate_basis(self, futures_price: float, spot_price: float) -> dict:
        """Calculate premium/discount basis."""
        if spot_price <= 0:
            return {"basis": 0, "basisPct": 0, "signal": "none"}

        basis = futures_price - spot_price
        basis_pct = basis / spot_price

        if basis_pct > self.premium_threshold:
            signal = "short_futures"  # futures expensive, short futures + long spot
        elif basis_pct < self.discount_threshold:
            signal = "long_futures"  # futures cheap, long futures + short spot
        else:
            signal = "neutral"

        return {
            "basis": round(basis, 2),
            "basisPct": round(basis_pct, 4),
            "signal": signal,
            "futuresPrice": futures_price,
            "spotPrice": spot_price,
        }

    def simulate_convergence(
        self,
        entry_futures: float,
        entry_spot: float,
        exit_futures: float,
        exit_spot: float,
        lots: int = 1,
        direction: str = "short_futures",
    ) -> dict:
        """Simulate a convergence trade.

        direction: "short_futures" (premium) or "long_futures" (discount)
        """
        if direction == "short_futures":
            futures_pnl = (entry_futures - exit_futures) * self.multi * lots
            spot_pnl = (exit_spot - entry_spot) / entry_spot * entry_futures * self.multi * lots
        else:
            futures_pnl = (exit_futures - entry_futures) * self.multi * lots
            spot_pnl = (entry_spot - exit_spot) / entry_spot * entry_futures * self.multi * lots

        total_pnl = futures_pnl + spot_pnl
        margin = entry_futures * self.multi * lots * MARGIN_RATIO.get(self.product, 0.14)
        capital = margin + entry_spot * self.multi * lots  # margin + spot collateral
        return_pct = total_pnl / capital if capital > 0 else 0

        return {
            "direction": direction,
            "futuresPnL": round(futures_pnl, 2),
            "spotPnL": round(spot_pnl, 2),
            "totalPnL": round(total_pnl, 2),
            "capitalUsed": round(capital, 2),
            "returnPct": round(return_pct, 4),
            "entryBasis": round(entry_futures - entry_spot, 2),
            "exitBasis": round(exit_futures - exit_spot, 2),
            "basisConvergence": round(
                abs(entry_futures - entry_spot) - abs(exit_futures - exit_spot), 2
            ),
        }

    def analyze_basis_history(
        self,
        futures_prices: list[float],
        spot_prices: list[float],
        dates: list[str],
    ) -> dict:
        """Analyze historical basis for arbitrage opportunities."""
        basis_series = []
        for i in range(min(len(futures_prices), len(spot_prices))):
            fp = futures_prices[i]
            sp = spot_prices[i]
            if sp > 0:
                basis_series.append({
                    "date": dates[i] if i < len(dates) else str(i),
                    "basis": round(fp - sp, 2),
                    "basisPct": round((fp - sp) / sp, 4),
                })

        if not basis_series:
            return {"series": [], "stats": {}}

        pcts = [b["basisPct"] for b in basis_series]
        mean_pct = sum(pcts) / len(pcts)
        variance = sum((p - mean_pct) ** 2 for p in pcts) / len(pcts) if len(pcts) > 1 else 0
        std_pct = math.sqrt(variance)

        # Count premium/discount days
        premium_days = sum(1 for p in pcts if p > 0)
        discount_days = sum(1 for p in pcts if p < 0)
        current = pcts[-1]
        z = (current - mean_pct) / std_pct if std_pct > 0 else 0

        return {
            "series": basis_series,
            "stats": {
                "meanBasisPct": round(mean_pct, 4),
                "stdBasisPct": round(std_pct, 4),
                "currentBasisPct": round(current, 4),
                "zScore": round(z, 4),
                "premiumDays": premium_days,
                "discountDays": discount_days,
                "premiumRatio": round(premium_days / len(pcts), 4) if pcts else 0,
            },
        }


# ── CLI Entry Point ──────────────────────────────────────────────


def run_futures_analysis(config: dict) -> dict:
    """CLI entry point for futures straddle analysis.

    Config:
    {
      "action": "butterfly" | "premium" | "basis_check",
      "product": "IC",
      ...action-specific params...
    }
    """
    action = config.get("action", "basis_check")

    if action == "butterfly":
        near = FuturesContract(
            code=config.get("nearCode", ""),
            product=config.get("product", "IC"),
            delivery=config.get("nearDelivery", ""),
            price=config.get("nearPrice", 0),
            settlement=config.get("nearPrice", 0),
        )
        mid = FuturesContract(
            code=config.get("midCode", ""),
            product=config.get("product", "IC"),
            delivery=config.get("midDelivery", ""),
            price=config.get("midPrice", 0),
            settlement=config.get("midPrice", 0),
        )
        far = FuturesContract(
            code=config.get("farCode", ""),
            product=config.get("product", "IC"),
            delivery=config.get("farDelivery", ""),
            price=config.get("farPrice", 0),
            settlement=config.get("farPrice", 0),
        )
        lots = config.get("lots", 1)
        spread = ButterflySpread(near, mid, far, lots)
        result = spread.entry_cost()

        # Exit prices for P&L calc
        if "nearExitPrice" in config:
            pnl = spread.calculate_pnl(
                config["nearExitPrice"],
                config["midExitPrice"],
                config["farExitPrice"],
            )
            result["pnl"] = pnl

        # Historical spread analysis
        if "nearPrices" in config:
            history = ButterflySpread.analyze_spread_history(
                config["nearPrices"],
                config["midPrices"],
                config["farPrices"],
                config.get("dates", []),
            )
            result["spreadAnalysis"] = history

        return result

    elif action == "premium":
        arb = PremiumArbitrage(
            product=config.get("product", "IC"),
            entry_days_before=config.get("entryDaysBefore", 15),
            premium_threshold=config.get("premiumThreshold", 0.01),
            discount_threshold=config.get("discountThreshold", -0.01),
        )

        if "futuresPrice" in config and "spotPrice" in config:
            basis = arb.calculate_basis(config["futuresPrice"], config["spotPrice"])

            result: dict[str, Any] = {"basis": basis}

            if "exitFuturesPrice" in config:
                sim = arb.simulate_convergence(
                    config["futuresPrice"],
                    config["spotPrice"],
                    config["exitFuturesPrice"],
                    config["exitSpotPrice"],
                    config.get("lots", 1),
                    basis.get("signal", "short_futures"),
                )
                result["simulation"] = sim

            return result

        if "futuresPrices" in config and "spotPrices" in config:
            return arb.analyze_basis_history(
                config["futuresPrices"],
                config["spotPrices"],
                config.get("dates", []),
            )

        return {"error": "Missing futuresPrice/spotPrice or futuresPrices/spotPrices"}

    elif action == "basis_check":
        arb = PremiumArbitrage(
            product=config.get("product", "IC"),
            premium_threshold=config.get("premiumThreshold", 0.01),
            discount_threshold=config.get("discountThreshold", -0.01),
        )
        return arb.calculate_basis(
            config.get("futuresPrice", 0),
            config.get("spotPrice", 0),
        )

    return {"error": f"Unknown action: {action}"}


if __name__ == "__main__":
    config = json.loads(sys.stdin.read())
    result = run_futures_analysis(config)
    print(json.dumps(result, ensure_ascii=False))
