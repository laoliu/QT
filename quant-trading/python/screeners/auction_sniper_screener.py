"""
竞价异动量大单抢筹选股器 (Auction Sniper Screener).

12060988180213009:15-9:25）检测主力隐蔽抢筹行为。

策略逻辑：
  1. 9:2408020902480100
  2. 9:24-9:250906030312180098
  3. 048109001218020200

010917
  竞价有主力异动量大单抢筹（隐蔽抢筹）+ 开盘后点火拉升 = 大概率大涨

000104
  - 06020004 9:15-9:25 多次调用 screen() 08039:25后分析
  - 041000040812 stock_data 进行分析

0069
  - 08084609020302
  - 061702070809
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

from engines.auction_sniper import (
    AuctionSniperEngine,
    AuctionSnapshot,
    SniperSignal,
    scan_auction_sniper,
    _fetch_auction_snapshot_batch,
    MIN_LATE_AMOUNT_WEAK,
)

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class AuctionSniperScreener(BaseScreener):
    """竞价异动量大单抢筹选股器 — 检测主力在集合竞价末期的隐蔽抢筹。"""

    screener_id = "auction_sniper"
    screener_name = "竞价抢筹狙击 (Auction Sniper)"
    screener_description = (
        "990213009:24-9:25）主力异动量大单抢筹行为。"
        "核心特征：早期竞价稳定（主力隐蔽）+ 0609060998"
        "异动抢筹 + 开盘后点火拉升 = 050208"
    )
    supported_markets = ["cn_stock"]

    def __init__(self, params: Optional[dict[str, Any]] = None):
        super().__init__(params)
        self._engine = AuctionSniperEngine(params)
        # Accumulated snapshot batches for multi-pass scanning
        self._snapshot_batches: list[list[dict]] = []

    def add_snapshot_batch(self, snapshots: list[AuctionSnapshot]) -> None:
        """Add a batch of auction snapshots (call multiple times during 9:15-9:25).

        Each call typically represents one polling cycle (e.g. every 15-30 seconds).
        The engine will use early vs late snapshots to detect abnormal patterns.
        """
        batch = [s.to_dict() for s in snapshots]
        self._snapshot_batches.append(batch)
        for snap in snapshots:
            self._engine.add_snapshot(snap)

    def reset_snapshots(self) -> None:
        """Clear accumulated snapshots (daily reset)."""
        self._snapshot_batches.clear()
        self._engine.reset()

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Run auction sniper screening.

        stock_data format options:

        1. Standard format (0410):
           {"600519": {"name": "06", "prev_close": 1800, "auction_snapshots": [...]}}
           Each auction_snapshot: {"timestamp": "09:20:00", "auction_price": 1810, ...}

        2. Pre-collected snapshot data:
           Use add_snapshot_batch() to accumulate data, then call screen() with empty stock_data.

        3. Live scan (竞价时段):
           Pass stock_data={"symbols": ["600519", "000001"]} to trigger live fetch.
        """
        min_score = float(self.params.get("min_score", 35.0))
        results: list[ScreenerResult] = []

        # Mode 1: Use pre-accumulated snapshots from add_snapshot_batch()
        if self._engine._snapshots:
            all_symbols = list(self._engine._snapshots.keys())
            for sym in all_symbols:
                sig = self._engine.analyze(sym)
                if sig.score >= min_score:
                    results.append(self._signal_to_result(sig))

        # Mode 2: Build from stock_data with embedded auction_snapshots
        elif stock_data:
            symbols_key = stock_data.get("symbols")
            if isinstance(symbols_key, list):
                # Live scan mode
                snaps = _fetch_auction_snapshot_batch(symbols_key)
                for snap in snaps:
                    self._engine.add_snapshot(snap)
                for sym in [s.symbol for s in snaps]:
                    sig = self._engine.analyze(sym)
                    if sig.score >= min_score:
                        results.append(self._signal_to_result(sig))
            else:
                # Historical replay mode
                engine = AuctionSniperEngine(self.params)
                for code, data in stock_data.items():
                    if code in ("symbols",):
                        continue
                    snapshots_raw = data.get("auction_snapshots", [])
                    name = data.get("name", "")
                    prev_close = float(data.get("prev_close", 0))

                    for snap_raw in snapshots_raw:
                        snap = AuctionSnapshot(
                            symbol=code,
                            name=name,
                            timestamp=snap_raw.get("timestamp", ""),
                            auction_price=float(snap_raw.get("auction_price", 0)),
                            prev_close=prev_close,
                            change_pct=float(snap_raw.get("change_pct", 0)),
                            volume=float(snap_raw.get("volume", 0)),
                            amount=float(snap_raw.get("amount", 0)),
                            buy1_volume=float(snap_raw.get("buy1_volume", 0)),
                            sell1_volume=float(snap_raw.get("sell1_volume", 0)),
                        )
                        engine.add_snapshot(snap)

                    sig = engine.analyze(code)
                    if sig.score >= min_score:
                        results.append(self._signal_to_result(sig))

        # Sort by score (best first)
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def confirm_post_open(
        self,
        symbol: str,
        open_price: float,
        current_price: float,
        high_price: float,
    ) -> Optional[ScreenerResult]:
        """开盘后确认：检查是否出现点火拉升。

        在 9:31+ 00080209810203
        """
        sig = self._engine.confirm(symbol, open_price, current_price, high_price)
        if sig.signal_type != "none":
            return self._signal_to_result(sig)
        return None

    @staticmethod
    def _signal_to_result(sig: SniperSignal) -> ScreenerResult:
        """Convert SniperSignal to ScreenerResult."""
        return ScreenerResult(
            stock_id=sig.symbol,
            name=sig.name,
            score=sig.score / 100.0,  # Normalize to 0-1
            factors={
                "signal_type": sig.signal_type,
                "gap_up_pct": sig.gap_up_pct,
                "early_stability": sig.early_stability_score,
                "late_surge": sig.late_surge_score,
                "volume_spike": sig.volume_spike_score,
                "order_imbalance": sig.order_imbalance_score,
                "late_price_jump": sig.late_price_jump,
                "late_amount_wan": sig.late_amount,
                "late_volume_ratio": sig.late_volume_ratio,
                "post_open_confirmed": sig.post_open_confirmed,
            },
            signals=sig.signals + [f"72 {n}" for n in sig.risk_notes],
        )
