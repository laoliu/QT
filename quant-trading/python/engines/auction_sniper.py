"""
auction_sniper — 竞价异动量大单抢筹检测引擎

14060988990213009:15-9:25）主力隐蔽抢筹行为：

核心逻辑：
  1. 9:24080209/010309480100
  2. 9:24-9:250209060303180000
  3. 0409070109020100

0871
  - early_stability:  9:240212080505
  - late_surge:       9:24-9:2518020505090698
  - volume_spike:     000900710202040300
  - gap_up_pct:       开盘跳空高开幅度
  - post_open_rising: 开盘后是否继续上涨（点火拉升确认）

000104
  1. 060200049:15-9:2518ρ039:25后分析
  2. 0410740001001915

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "snapshot" | "analyze" | "scan" | "confirm",
    "symbol": "600519",
    "symbols": ["600519", "000001"],  # for scan
    "snapshots": [...],  # for analyze, list of AuctionSnapshot
    "confirm_data": {...}  # for confirm, post-open data
  }

Output: JSON with detection results.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class AuctionSnapshot:
    """Single point-in-time snapshot during call auction."""
    symbol: str
    name: str = ""
    timestamp: str = ""           # HH:MM:SS
    auction_price: float = 0.0    # 1218
    prev_close: float = 0.0       # 昨收
    change_pct: float = 0.0       # 07 (%)
    volume: float = 0.0           # 00 (手)
    amount: float = 0.0           # 00 (06)
    buy1_price: float = 0.0       # 05
    buy1_volume: float = 0.0      # 05
    sell1_price: float = 0.0      # 05
    sell1_volume: float = 0.0     # 05

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SniperSignal:
    """Detection result for a single stock."""
    symbol: str
    name: str = ""
    signal_type: str = ""          # "strong" | "moderate" | "weak" | "none"
    score: float = 0.0             # 0-100 overall score
    prev_close: float = 0.0
    final_auction_price: float = 0.0
    gap_up_pct: float = 0.0        # 1811 (%)

    # Sub-scores (each 0-100, weighted into overall)
    early_stability_score: float = 0.0   # 0109050500
    late_surge_score: float = 0.0        # 060906
    volume_spike_score: float = 0.0      # 成交额骤增
    order_imbalance_score: float = 0.0   # 070109

    # Raw metrics
    early_change_range: float = 0.0    # 9:15-9:24 1802 (%)
    late_price_jump: float = 0.0       # 9:24-9:25 价格跳变 (%)
    late_volume_ratio: float = 0.0     # 000900/1900
    late_amount: float = 0.0           # 000900 (06)

    signals: list[str] = field(default_factory=list)   # 0302
    risk_notes: list[str] = field(default_factory=list) # 风险提示

    # Post-open confirmation (if available)
    post_open_confirmed: Optional[bool] = None
    open_price: float = 0.0
    post_open_high: float = 0.0
    post_open_change_pct: float = 0.0  # 0409

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # round floats
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


# ── Thresholds & Config ───────────────────────────────────────────────────────

# 06139:24-9:25）价格跳变阈值
LATE_JUMP_STRONG = 3.0      # >=3% 0602
LATE_JUMP_MODERATE = 1.5    # >=1.5% 0302
LATE_JUMP_WEAK = 0.5        # >=0.5% 02

# 01099:15-9:24180205
EARLY_STABLE_EXCELLENT = 1.0   # <1% 非常稳定，主力在隐蔽
EARLY_STABLE_GOOD = 2.0       # <2% 01

# 成交额骤增比
VOLUME_SPIKE_STRONG = 5.0     # 000900>=早期5倍
VOLUME_SPIKE_MODERATE = 3.0

# 000100 (06) — 0402
MIN_LATE_AMOUNT_STRONG = 500    # >=500万 04
MIN_LATE_AMOUNT_MODERATE = 200  # >=200万 е
MIN_LATE_AMOUNT_WEAK = 50       # >=50万 最低门槛

# 0105
SCORE_STRONG = 75
SCORE_MODERATE = 55
SCORE_WEAK = 35

# 02110205
GAP_UP_MIN = 1.0  # 10111%才有意义

# 07
W_EARLY_STABILITY = 0.20   # 早期稳定性
W_LATE_SURGE = 0.35         # 06094408
W_VOLUME_SPIKE = 0.30       # 成交额骤增
W_ORDER_IMBALANCE = 0.15    # 070109


# ── Core Analysis Engine ──────────────────────────────────────────────────────

class AuctionSniperEngine:
    """竞价异动量大单抢筹检测引擎。

    Usage:
        engine = AuctionSniperEngine()

        # 1. Collect snapshots during 9:15-9:25
        engine.add_snapshot(symbol, snapshot_data)

        # 2. After 9:25, analyze
        signal = engine.analyze(symbol)

        # 3. After 9:31+, confirm with post-open data
        signal = engine.confirm(symbol, open_price, current_price, high_price)
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}
        # Per-symbol snapshot buffers: {symbol: [AuctionSnapshot, ...]}
        self._snapshots: dict[str, list[AuctionSnapshot]] = {}
        # Analysis cache
        self._results: dict[str, SniperSignal] = {}

    def reset(self, symbol: Optional[str] = None) -> None:
        """Reset tracking state (daily reset)."""
        if symbol:
            self._snapshots.pop(symbol, None)
            self._results.pop(symbol, None)
        else:
            self._snapshots.clear()
            self._results.clear()

    def add_snapshot(self, snapshot: AuctionSnapshot) -> None:
        """Record an auction snapshot for a symbol."""
        symbol = snapshot.symbol
        if symbol not in self._snapshots:
            self._snapshots[symbol] = []
        self._snapshots[symbol].append(snapshot)

    def analyze(self, symbol: str) -> SniperSignal:
        """Analyze collected snapshots to detect abnormal accumulation.

        Should be called after 9:25 when auction period ends.
        Requires at least 2 snapshots (one early, one late).
        """
        snaps = self._snapshots.get(symbol, [])
        if len(snaps) < 2:
            sig = SniperSignal(
                symbol=symbol,
                signal_type="none",
                risk_notes=["快照不足，无法分析（至少需要2个快照）"],
            )
            self._results[symbol] = sig
            return sig

        # Sort by timestamp
        snaps.sort(key=lambda s: s.timestamp)

        name = snaps[-1].name or symbol
        prev_close = snaps[-1].prev_close or snaps[0].prev_close

        if prev_close <= 0:
            sig = SniperSignal(
                symbol=symbol, name=name,
                signal_type="none",
                risk_notes=["08"],
            )
            self._results[symbol] = sig
            return sig

        # ── Split snapshots into early (before 9:24) and late (9:24+) ──
        early_snaps = [s for s in snaps if s.timestamp < "09:24:00"]
        late_snaps = [s for s in snaps if s.timestamp >= "09:24:00"]

        # If we can't distinguish early/late, use first half vs second half
        if not early_snaps or not late_snaps:
            mid = len(snaps) // 2
            early_snaps = snaps[:mid] if mid > 0 else snaps[:1]
            late_snaps = snaps[mid:] if mid > 0 else snaps[1:]

        final_snap = late_snaps[-1] if late_snaps else snaps[-1]
        first_snap = early_snaps[0] if early_snaps else snaps[0]

        # ── Metric 1: Early Stability (9:15-9:24) ──
        early_prices = [s.auction_price for s in early_snaps if s.auction_price > 0]
        if early_prices:
            early_min = min(early_prices)
            early_max = max(early_prices)
            early_change_range = (early_max - early_min) / prev_close * 100
        else:
            early_change_range = 0.0

        if early_change_range <= EARLY_STABLE_EXCELLENT:
            early_stability_score = 100.0
        elif early_change_range <= EARLY_STABLE_GOOD:
            early_stability_score = 60.0 + 40.0 * (EARLY_STABLE_GOOD - early_change_range) / (EARLY_STABLE_GOOD - EARLY_STABLE_EXCELLENT)
        else:
            early_stability_score = max(0, 60.0 - (early_change_range - EARLY_STABLE_GOOD) * 20)

        # ── Metric 2: Late Surge (9:24-9:25 price jump) ──
        # Compare last early price vs final auction price
        last_early_price = early_prices[-1] if early_prices else prev_close
        final_price = final_snap.auction_price if final_snap.auction_price > 0 else prev_close
        late_price_jump = (final_price - last_early_price) / prev_close * 100

        if late_price_jump >= LATE_JUMP_STRONG:
            late_surge_score = 80.0 + min(20, (late_price_jump - LATE_JUMP_STRONG) * 5)
        elif late_price_jump >= LATE_JUMP_MODERATE:
            late_surge_score = 50.0 + 30.0 * (late_price_jump - LATE_JUMP_MODERATE) / (LATE_JUMP_STRONG - LATE_JUMP_MODERATE)
        elif late_price_jump >= LATE_JUMP_WEAK:
            late_surge_score = 20.0 + 30.0 * (late_price_jump - LATE_JUMP_WEAK) / (LATE_JUMP_MODERATE - LATE_JUMP_WEAK)
        else:
            late_surge_score = max(0, late_price_jump / LATE_JUMP_WEAK * 20)

        # Penalize if price jumped DOWN (this is not accumulation)
        if late_price_jump < 0:
            late_surge_score = 0.0

        # ── Metric 3: Volume Spike ──
        early_amount = sum(s.amount for s in early_snaps)
        late_amount = sum(s.amount for s in late_snaps)

        # Use incremental amount if available; otherwise use absolute from last snap
        if late_amount <= 0 and final_snap.amount > 0:
            late_amount = final_snap.amount
        if early_amount <= 0 and first_snap.amount > 0:
            early_amount = first_snap.amount

        if early_amount > 0:
            late_volume_ratio = late_amount / early_amount
        else:
            # No early volume; if late has volume, that's still meaningful
            late_volume_ratio = late_amount / 10 if late_amount > 0 else 0

        # Score by volume ratio
        if late_volume_ratio >= VOLUME_SPIKE_STRONG:
            volume_spike_score = 80.0 + min(20, (late_volume_ratio - VOLUME_SPIKE_STRONG) * 3)
        elif late_volume_ratio >= VOLUME_SPIKE_MODERATE:
            volume_spike_score = 50.0 + 30.0 * (late_volume_ratio - VOLUME_SPIKE_MODERATE) / (VOLUME_SPIKE_STRONG - VOLUME_SPIKE_MODERATE)
        else:
            volume_spike_score = max(0, late_volume_ratio / VOLUME_SPIKE_MODERATE * 50)

        # Minimum absolute amount filter
        if late_amount < MIN_LATE_AMOUNT_WEAK:
            volume_spike_score *= 0.3
        elif late_amount < MIN_LATE_AMOUNT_MODERATE:
            volume_spike_score *= 0.7

        # ── Metric 4: Order Imbalance (buy vs sell pressure) ──
        # Use bid/ask data from the final snapshot
        order_imbalance_score = 50.0  # Default neutral
        if final_snap.buy1_volume > 0 and final_snap.sell1_volume > 0:
            buy_sell_ratio = final_snap.buy1_volume / final_snap.sell1_volume
            if buy_sell_ratio >= 3.0:
                order_imbalance_score = 90.0
            elif buy_sell_ratio >= 2.0:
                order_imbalance_score = 70.0
            elif buy_sell_ratio >= 1.5:
                order_imbalance_score = 55.0
            elif buy_sell_ratio >= 1.0:
                order_imbalance_score = 40.0
            else:
                order_imbalance_score = max(0, buy_sell_ratio * 30)

        # ── Overall Score ──
        overall = (
            early_stability_score * W_EARLY_STABILITY
            + late_surge_score * W_LATE_SURGE
            + volume_spike_score * W_VOLUME_SPIKE
            + order_imbalance_score * W_ORDER_IMBALANCE
        )

        # Gap up %
        gap_up_pct = (final_price - prev_close) / prev_close * 100

        # ── Signal Type ──
        if overall >= SCORE_STRONG:
            signal_type = "strong"
        elif overall >= SCORE_MODERATE:
            signal_type = "moderate"
        elif overall >= SCORE_WEAK:
            signal_type = "weak"
        else:
            signal_type = "none"

        # ── Build signals list ──
        signals: list[str] = []
        risk_notes: list[str] = []

        if late_price_jump >= LATE_JUMP_STRONG:
            signals.append(f"06181809 +{late_price_jump:.2f}%060200")
        elif late_price_jump >= LATE_JUMP_MODERATE:
            signals.append(f"061818 +{late_price_jump:.2f}%030200")

        if early_change_range <= EARLY_STABLE_EXCELLENT:
            signals.append(f"早期竞价极其稳定（波动{early_change_range:.2f}%），主力隐蔽良好")

        if late_volume_ratio >= VOLUME_SPIKE_STRONG:
            signals.append(f"061900{late_volume_ratio:.1f}0202040300")

        if late_amount >= MIN_LATE_AMOUNT_STRONG:
            signals.append(f"061900 {late_amount:.0f}060459")
        elif late_amount >= MIN_LATE_AMOUNT_MODERATE:
            signals.append(f"061900 {late_amount:.0f}060304")

        if gap_up_pct >= 5.0:
            signals.append(f"1811 {gap_up_pct:.2f}%（大幅跳空）")
        elif gap_up_pct >= GAP_UP_MIN:
            signals.append(f"1811 {gap_up_pct:.2f}%")

        # Risk notes
        if gap_up_pct >= 7.0:
            risk_notes.append("110401130906")
        if late_amount < MIN_LATE_AMOUNT_MODERATE:
            risk_notes.append("00010407")
        if early_change_range > EARLY_STABLE_GOOD:
            risk_notes.append("早期竞价波动较大，可能不是典型主力隐蔽抢筹")
        if late_price_jump < LATE_JUMP_WEAK:
            risk_notes.append("06181846")

        sig = SniperSignal(
            symbol=symbol,
            name=name,
            signal_type=signal_type,
            score=round(overall, 2),
            prev_close=prev_close,
            final_auction_price=final_price,
            gap_up_pct=round(gap_up_pct, 2),
            early_stability_score=round(early_stability_score, 2),
            late_surge_score=round(late_surge_score, 2),
            volume_spike_score=round(volume_spike_score, 2),
            order_imbalance_score=round(order_imbalance_score, 2),
            early_change_range=round(early_change_range, 4),
            late_price_jump=round(late_price_jump, 4),
            late_volume_ratio=round(late_volume_ratio, 2),
            late_amount=round(late_amount, 2),
            signals=signals,
            risk_notes=risk_notes,
        )
        self._results[symbol] = sig
        return sig

    def confirm(
        self, symbol: str,
        open_price: float,
        current_price: float,
        high_price: float,
    ) -> SniperSignal:
        """Post-open confirmation: check whether 点火拉升 is happening.

        Call this after 9:31 to see if price is rising from auction price.

        Logic:
          - open >= auction_price → 主力意图延续
          - current_price > open_price → 点火拉升确认
          - high_price > auction_price * 1.02 → 拉升力度确认
        """
        sig = self._results.get(symbol)
        if sig is None:
            return SniperSignal(
                symbol=symbol, signal_type="none",
                risk_notes=["未找到竞价分析结果，请先执行 analyze"],
            )

        sig.open_price = open_price
        sig.post_open_high = high_price

        if sig.final_auction_price > 0:
            sig.post_open_change_pct = round(
                (current_price - sig.final_auction_price) / sig.final_auction_price * 100, 2
            )

        # Confirmation logic
        auction_px = sig.final_auction_price
        if auction_px > 0 and open_price > 0:
            opened_above_auction = open_price >= auction_px * 0.998  # small tolerance
            rising_after_open = current_price > open_price
            strong_rise = high_price > auction_px * 1.02

            if opened_above_auction and rising_after_open:
                sig.post_open_confirmed = True
                sig.signals.append("73 开盘后点火拉升确认")
                if strong_rise:
                    sig.signals.append(f"73 盘中最高价 {high_price:.2f}，拉升力度强劲")
                # Boost overall score
                sig.score = min(100, sig.score + 10)
            elif not opened_above_auction:
                sig.post_open_confirmed = False
                sig.risk_notes.append("7215 0615101703")
                sig.score = max(0, sig.score - 20)
            elif not rising_after_open:
                sig.post_open_confirmed = False
                sig.risk_notes.append("7215 开盘后未上涨，主力可能力度不足或遇到抛压")
                sig.score = max(0, sig.score - 15)
        else:
            sig.post_open_confirmed = None

        # Recalculate signal type after confirmation
        if sig.score >= SCORE_STRONG:
            sig.signal_type = "strong"
        elif sig.score >= SCORE_MODERATE:
            sig.signal_type = "moderate"
        elif sig.score >= SCORE_WEAK:
            sig.signal_type = "weak"
        else:
            sig.signal_type = "none"

        return sig

    def get_result(self, symbol: str) -> Optional[SniperSignal]:
        """Get cached analysis result."""
        return self._results.get(symbol)

    def get_all_results(self) -> dict[str, SniperSignal]:
        """Get all cached results."""
        return dict(self._results)


# ── Scan via akshare (real-time quote as auction proxy) ───────────────────────

def _fetch_auction_snapshot_batch(symbols: list[str]) -> list[AuctionSnapshot]:
    """Fetch current auction data for a list of symbols via akshare.

    During 9:15-9:25, akshare's stock_zh_a_spot_em() returns auction quotes.
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or len(df) == 0:
            return []
    except Exception:
        return []

    now_str = datetime.now().strftime("%H:%M:%S")
    symbol_set = set(symbols) if symbols else None
    results: list[AuctionSnapshot] = []

    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        if symbol_set and code not in symbol_set:
            continue

        prev_close = float(row.get("昨收", 0) or 0)
        price = float(row.get("04", 0) or 0)
        change_pct = float(row.get("07", 0) or 0)
        vol = float(row.get("00", 0) or 0) / 100                # 手
        amt = float(row.get("00", 0) or 0) / 10_000             # 06

        snap = AuctionSnapshot(
            symbol=code,
            name=str(row.get("名称", "")),
            timestamp=now_str,
            auction_price=price,
            prev_close=prev_close,
            change_pct=round(change_pct, 2),
            volume=round(vol, 2),
            amount=round(amt, 2),
        )
        results.append(snap)

    return results


def scan_auction_sniper(
    symbols: Optional[list[str]] = None,
    min_score: float = 35.0,
    snapshots_data: Optional[list[list[dict]]] = None,
) -> list[dict[str, Any]]:
    """Batch scan for auction sniper signals.

    Args:
        symbols: list of stock codes to check. If None, checks all active.
        min_score: minimum score to include in results.
        snapshots_data: pre-collected snapshots (list of snapshot batches).
            If None, fetches live data (single snapshot only — for multi-snapshot,
            caller should collect over time and pass in).

    Returns:
        list of SniperSignal dicts, sorted by score descending.
    """
    engine = AuctionSniperEngine()

    if snapshots_data:
        # Use pre-collected multi-snapshot data
        for batch in snapshots_data:
            for snap_dict in batch:
                snap = AuctionSnapshot(**snap_dict)
                engine.add_snapshot(snap)
    else:
        # Single live snapshot — limited analysis capability
        snaps = _fetch_auction_snapshot_batch(symbols or [])
        for snap in snaps:
            engine.add_snapshot(snap)

    # Analyze all tracked symbols
    all_symbols = list(engine._snapshots.keys())
    results: list[dict[str, Any]] = []

    for sym in all_symbols:
        sig = engine.analyze(sym)
        if sig.score >= min_score:
            results.append(sig.to_dict())

    results.sort(key=lambda r: r.get("score", 0), reverse=True)
    return results


# ── CLI entry point ─────────────────────────────────────────────────────────

def run_auction_sniper(config: dict) -> dict:
    """CLI dispatcher for auction sniper engine."""
    action = config.get("action", "scan")

    if action == "snapshot":
        # Take a single snapshot
        symbols = config.get("symbols", [])
        snaps = _fetch_auction_snapshot_batch(symbols)
        return {
            "ok": True,
            "action": "snapshot",
            "count": len(snaps),
            "snapshots": [s.to_dict() for s in snaps],
        }

    elif action == "analyze":
        # Analyze pre-collected snapshots
        snapshots_data = config.get("snapshots", [])
        min_score = config.get("min_score", 35.0)
        symbols = config.get("symbols", [])

        if not snapshots_data:
            return {"ok": False, "error": "0859 snapshots 15ρ03"}

        results = scan_auction_sniper(
            symbols=symbols or None,
            min_score=min_score,
            snapshots_data=snapshots_data,
        )
        return {
            "ok": True,
            "action": "analyze",
            "count": len(results),
            "signals": results,
        }

    elif action == "scan":
        # Live single-shot scan (limited — should collect snapshots over time)
        symbols = config.get("symbols", [])
        min_score = config.get("min_score", 35.0)
        results = scan_auction_sniper(symbols=symbols or None, min_score=min_score)
        return {
            "ok": True,
            "action": "scan",
            "count": len(results),
            "signals": results,
        }

    elif action == "confirm":
        # Post-open confirmation
        engine = AuctionSniperEngine()
        confirm_data = config.get("confirm_data", {})
        snapshots_data = config.get("snapshots", [])

        # Rebuild state from snapshots
        if snapshots_data:
            for batch in snapshots_data:
                for snap_dict in batch:
                    snap = AuctionSnapshot(**snap_dict)
                    engine.add_snapshot(snap)

        symbol = config.get("symbol", "")
        if not symbol:
            return {"ok": False, "error": "0859 symbol"}

        # First analyze
        engine.analyze(symbol)
        # Then confirm
        sig = engine.confirm(
            symbol,
            open_price=float(confirm_data.get("open_price", 0)),
            current_price=float(confirm_data.get("current_price", 0)),
            high_price=float(confirm_data.get("high_price", 0)),
        )
        return {
            "ok": True,
            "action": "confirm",
            "signal": sig.to_dict(),
        }

    else:
        return {"ok": False, "error": f"Unknown action: {action}"}


# ── __main__ ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG", "")
        if not raw:
            raw = sys.stdin.read()
        cfg = json.loads(raw) if raw else {}
        result = run_auction_sniper(cfg)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()},
                          ensure_ascii=False))
        sys.exit(1)
