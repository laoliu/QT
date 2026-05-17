"""Call auction sniper engine.

Analyzes 9:15-9:25 auction snapshots to detect strong opening
signals — early stability, late surge, volume spike, and order
imbalance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["AuctionSniperEngine", "AuctionSnapshot", "SniperSignal"]

# -- scoring thresholds --
LATE_JUMP_STRONG = 3.0
LATE_JUMP_MODERATE = 1.5
VOLUME_SPIKE_STRONG = 5.0
VOLUME_SPIKE_MODERATE = 2.5
SCORE_STRONG = 75
SCORE_MODERATE = 55

_WEIGHTS = {
    "early_stability": 0.20,
    "late_surge": 0.35,
    "volume_spike": 0.30,
    "order_imbalance": 0.15,
}


@dataclass
class AuctionSnapshot:
    """A single snapshot from the call auction period."""

    symbol: str = ""
    name: str = ""
    timestamp: float = 0.0
    auction_price: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    amount: float = 0.0
    buy1_price: float = 0.0
    buy1_volume: int = 0
    sell1_price: float = 0.0
    sell1_volume: int = 0


@dataclass
class SniperSignal:
    """Detection result from auction analysis."""

    symbol: str = ""
    name: str = ""
    score: float = 0.0
    level: str = "none"
    early_stability: float = 0.0
    late_surge: float = 0.0
    volume_spike: float = 0.0
    order_imbalance: float = 0.0
    auction_change_pct: float = 0.0
    snapshot_count: int = 0
    confirmed: bool = False
    confirm_reason: str = ""


class AuctionSniperEngine:
    """Analyze call auction snapshots and generate sniper signals."""

    def __init__(self, config: Optional[Dict] = None) -> None:
        cfg = config or {}
        self._weights = {k: cfg.get(k, v) for k, v in _WEIGHTS.items()}
        self._data: Dict[str, List[AuctionSnapshot]] = {}

    def reset(self, symbol: str) -> None:
        self._data.pop(symbol, None)

    def add_snapshot(self, snap: AuctionSnapshot) -> None:
        self._data.setdefault(snap.symbol, []).append(snap)

    def analyze(self, symbol: str) -> SniperSignal:
        """Compute sniper signal from collected auction snapshots."""
        snaps = self._data.get(symbol)
        if not snaps:
            return SniperSignal(symbol=symbol)
        name = snaps[-1].name
        prev_close = snaps[-1].prev_close
        # split early (9:15-9:24) vs late (last 1-2 snapshots)
        if len(snaps) >= 3:
            early = snaps[:-2]
            late = snaps[-2:]
        else:
            early = snaps[:1]
            late = snaps[1:] if len(snaps) > 1 else snaps
        # 1. early stability — range of change_pcts in early window
        early_changes = [s.change_pct for s in early if s.change_pct != 0]
        if early_changes:
            rng = max(early_changes) - min(early_changes)
            es = max(0.0, 100.0 - rng * 20)
        else:
            es = 50.0
        # 2. late surge — jump in the last snapshot(s)
        if late and early:
            late_pct = late[-1].change_pct
            early_avg = sum(s.change_pct for s in early) / len(early) if early else 0
            jump = late_pct - early_avg
            if jump >= LATE_JUMP_STRONG:
                ls = 100.0
            elif jump >= LATE_JUMP_MODERATE:
                ls = 60.0 + 40.0 * (jump - LATE_JUMP_MODERATE) / (LATE_JUMP_STRONG - LATE_JUMP_MODERATE)
            elif jump > 0:
                ls = jump / LATE_JUMP_MODERATE * 60.0
            else:
                ls = 0.0
        else:
            ls = 0.0
        # 3. volume spike — late/early amount ratio
        early_amt = sum(s.amount for s in early) or 1.0
        late_amt = sum(s.amount for s in late) or 0.0
        ratio = late_amt / early_amt if early_amt > 0 else 1.0
        if ratio >= VOLUME_SPIKE_STRONG:
            vs = 100.0
        elif ratio >= VOLUME_SPIKE_MODERATE:
            vs = 50.0 + 50.0 * (ratio - VOLUME_SPIKE_MODERATE) / (VOLUME_SPIKE_STRONG - VOLUME_SPIKE_MODERATE)
        else:
            vs = ratio / VOLUME_SPIKE_MODERATE * 50.0
        # 4. order imbalance — bid/ask strength at final snapshot
        final = snaps[-1]
        bid_v = final.buy1_volume or 0
        ask_v = final.sell1_volume or 1
        imb_ratio = bid_v / ask_v if ask_v > 0 else 1.0
        oi = min(100.0, imb_ratio * 33.3)
        # combine
        w = self._weights
        score = (
            es * w["early_stability"]
            + ls * w["late_surge"]
            + vs * w["volume_spike"]
            + oi * w["order_imbalance"]
        )
        if score >= SCORE_STRONG:
            level = "strong"
        elif score >= SCORE_MODERATE:
            level = "moderate"
        else:
            level = "weak"
        return SniperSignal(
            symbol=symbol, name=name, score=score, level=level,
            early_stability=es, late_surge=ls,
            volume_spike=vs, order_imbalance=oi,
            auction_change_pct=snaps[-1].change_pct,
            snapshot_count=len(snaps),
        )

    def confirm(
        self, symbol: str, open_price: float, current_price: float, high_price: float,
    ) -> SniperSignal:
        """Post-open confirmation of the auction signal."""
        sig = self.analyze(symbol)
        if sig.score < SCORE_MODERATE:
            sig.confirmed = False
            sig.confirm_reason = "score_too_low"
            return sig
        snaps = self._data.get(symbol, [])
        auction_price = snaps[-1].auction_price if snaps else 0
        if auction_price > 0 and open_price > 0:
            gap = (open_price - auction_price) / auction_price
            if gap > 0.02:
                sig.confirmed = True
                sig.confirm_reason = "open_gap_up"
            elif current_price >= open_price:
                sig.confirmed = True
                sig.confirm_reason = "price_holding"
            else:
                sig.confirmed = False
                sig.confirm_reason = "price_retreated"
        else:
            sig.confirmed = False
            sig.confirm_reason = "missing_price"
        return sig
