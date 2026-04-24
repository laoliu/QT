"""
live_engine.py — Python-side strategy runner for real-time live trading.

Persistent NDJSON bridge protocol (same as qmt_broker.py):
  stdin  → { "requestId": "...", "method": "...", "params": {...} }
  stdout ← { "requestId": "...", "ok": true, "data": ... }
  stdout ← { "event": "signal"|"bar"|"error", "data": {...} }

The engine receives bar data (either pushed from a broker quote feed or
polled by the TS LiveEngine) and runs registered strategies. When a strategy
produces a signal, it is pushed back to TS as an event.

Supported strategies: any class in strategies/ that inherits LiveStrategy.
"""

import sys
import json
import os
import logging
import importlib
import threading
import time
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, asdict

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("live_engine")

# ── NDJSON I/O ────────────────────────────────────────────────────

def send(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def reply_ok(request_id: str, data: Any) -> None:
    send({"requestId": request_id, "ok": True, "data": data})

def reply_err(request_id: str, error: str) -> None:
    send({"requestId": request_id, "ok": False, "error": error})

def push_event(event: str, data: Any) -> None:
    send({"event": event, "data": data})

# ── Bar dataclass ─────────────────────────────────────────────────

@dataclass
class Bar:
    symbol: str
    timestamp: int       # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    interval: str = "1d"

# ── Signal dataclass ──────────────────────────────────────────────

@dataclass
class Signal:
    timestamp: int
    symbol: str
    direction: str       # buy / sell / short / cover
    price: float
    quantity: Optional[int] = None
    reason: str = ""
    confidence: float = 1.0
    strategy_id: str = ""

# ── Base live strategy ────────────────────────────────────────────

class LiveStrategy:
    """
    Base class for all live strategies.
    Subclasses override on_bar() and emit signals by appending to self._signals.
    """
    strategy_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, symbols: List[str], parameters: Dict[str, Any]):
        self.symbols = symbols
        self.parameters = parameters
        self._signals: List[Signal] = []
        # Per-symbol bar history for indicator calculation
        self._bars: Dict[str, List[Bar]] = {s: [] for s in symbols}

    def on_bar(self, bar: Bar) -> None:
        """Called once per new bar. Override in subclasses."""
        raise NotImplementedError

    def emit(self, symbol: str, direction: str, price: float, reason: str = "",
             confidence: float = 1.0) -> None:
        """Helper: emit a trading signal."""
        self._signals.append(Signal(
            timestamp=int(time.time() * 1000),
            symbol=symbol,
            direction=direction,
            price=price,
            reason=reason,
            confidence=confidence,
            strategy_id=self.strategy_id,
        ))

    def feed(self, bar: Bar) -> List[Signal]:
        """Feed a bar and return any new signals."""
        self._bars.setdefault(bar.symbol, []).append(bar)
        self._signals.clear()
        self.on_bar(bar)
        return list(self._signals)

    # ── Indicator helpers ───────────────────────────────────────

    def closes(self, symbol: str, n: int) -> List[float]:
        """Return last N close prices for a symbol."""
        bars = self._bars.get(symbol, [])
        return [b.close for b in bars[-n:]] if bars else []

    def sma(self, symbol: str, period: int) -> Optional[float]:
        prices = self.closes(symbol, period)
        return sum(prices) / len(prices) if len(prices) >= period else None

    def ema(self, symbol: str, period: int) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if not bars:
            return None
        closes = [b.close for b in bars]
        if len(closes) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for c in closes[period:]:
            ema = c * k + ema * (1 - k)
        return ema

    def rsi(self, symbol: str, period: int = 14) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if len(bars) < period + 1:
            return None
        closes = [b.close for b in bars[-(period + 1):]]
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)


# ── Built-in live strategies ──────────────────────────────────────

class DualMaLive(LiveStrategy):
    """Dual moving-average crossover — live version."""
    strategy_id = "dual-ma-crossover"
    name = "双均线交叉（实盘）"

    def on_bar(self, bar: Bar) -> None:
        fast = self.parameters.get("fast_period", 5)
        slow = self.parameters.get("slow_period", 20)
        fast_ma = self.sma(bar.symbol, fast)
        slow_ma = self.sma(bar.symbol, slow)
        if fast_ma is None or slow_ma is None:
            return

        bars = self._bars.get(bar.symbol, [])
        if len(bars) < 2:
            return
        prev_bars = bars[-2]
        prev_fast = self.sma(bar.symbol, fast)  # approximate; full recompute
        # For crossover, compare current vs previous bar
        fast_now = fast_ma
        slow_now = slow_ma
        # Use raw closes for cross detection
        closes = self.closes(bar.symbol, slow + 1)
        if len(closes) < slow + 1:
            return
        fast_prev = sum(closes[-fast - 1: -1]) / fast
        slow_prev = sum(closes[-slow - 1: -1]) / slow
        fast_curr = sum(closes[-fast:]) / fast
        slow_curr = sum(closes[-slow:]) / slow

        crossed_up = fast_prev < slow_prev and fast_curr > slow_curr
        crossed_down = fast_prev > slow_prev and fast_curr < slow_curr

        if crossed_up:
            self.emit(bar.symbol, "buy", bar.close, f"快线({fast})上穿慢线({slow})")
        elif crossed_down:
            self.emit(bar.symbol, "sell", bar.close, f"快线({fast})下穿慢线({slow})")


class MomentumLive(LiveStrategy):
    """N-day momentum rebalancing — live version.

    Every ``rebalanceDays`` trading days, ranks all watched symbols by a
    composite momentum score (70 % 20-day return + 30 % 5-day return) and
    emits sell signals for demoted stocks / buy signals for promoted ones.

    Between rebalance days, N-day high/low breakout signals are still
    generated so sudden breakdowns can be captured intraday.
    """
    strategy_id = "momentum-breakout"
    name = "动量突破（实盘）"

    def __init__(self, symbols, parameters):
        super().__init__(symbols, parameters)
        self._bar_day_count: int = 0          # trading days since last rebalance
        self._last_rebalance_date: str = ""   # YYYY-MM-DD of last rebalance
        self._holdings: List[str] = []        # symbols currently "selected"
        self._buy_date: Dict[str, str] = {}   # symbol → YYYY-MM-DD of last buy (T+1)

    # ── helpers ─────────────────────────────────────────────────

    def _is_t1_locked(self, symbol: str, today: str) -> bool:
        """Return True if *symbol* was bought today (T+1: cannot sell same day)."""
        return self._buy_date.get(symbol) == today

    def _returns(self, symbol: str, n: int) -> Optional[float]:
        """N-day return for *symbol*. None if insufficient data."""
        closes = self.closes(symbol, n + 1)
        if len(closes) < n + 1 or closes[0] == 0:
            return None
        return (closes[-1] - closes[0]) / closes[0]

    def _rank_symbols(self) -> List[str]:
        """Rank all watched symbols by composite momentum; return top-N."""
        scores: Dict[str, float] = {}
        for sym in self.symbols:
            r20 = self._returns(sym, 20)
            r5 = self._returns(sym, 5)
            if r20 is None or r5 is None:
                continue
            # Same filter as backtest: 20d > 2% and 5d > 0
            if r20 > 0.02 and r5 > 0:
                scores[sym] = r20 * 0.7 + r5 * 0.3
        top_n = max(3, min(len(scores), len(self.symbols) // 2))
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [code for code, _ in ranked]

    # ── main logic ──────────────────────────────────────────────

    def on_bar(self, bar: Bar) -> None:
        period = self.parameters.get("period", 20)
        rebalance_days = self.parameters.get("rebalanceDays", 5)
        if isinstance(rebalance_days, str):
            rebalance_days = int(rebalance_days)

        # Detect new trading day (avoid counting intraday bars twice)
        bar_date = time.strftime("%Y-%m-%d", time.localtime(bar.timestamp / 1000))
        new_day = bar_date != self._last_rebalance_date

        # ── Rebalance check ─────────────────────────────────────
        if new_day:
            self._bar_day_count += 1
            self._last_rebalance_date = bar_date

            if self._bar_day_count >= rebalance_days or not self._holdings:
                self._bar_day_count = 0
                new_holdings = self._rank_symbols()
                if new_holdings:
                    old_set = set(self._holdings)
                    new_set = set(new_holdings)

                    # Sell demoted stocks (skip T+1 locked)
                    for sym in old_set - new_set:
                        if self._is_t1_locked(sym, bar_date):
                            log.info("T+1: skip sell %s (bought today)", sym)
                            continue
                        last_closes = self.closes(sym, 1)
                        px = last_closes[-1] if last_closes else bar.close
                        self.emit(sym, "sell", px,
                                  f"再平衡卖出: 动量排名下降 (每{rebalance_days}日)")

                    # Buy promoted stocks
                    for sym in new_set - old_set:
                        last_closes = self.closes(sym, 1)
                        px = last_closes[-1] if last_closes else bar.close
                        self.emit(sym, "buy", px,
                                  f"再平衡买入: 动量排名前列 (每{rebalance_days}日)")
                        self._buy_date[sym] = bar_date  # T+1 tracking

                    self._holdings = new_holdings
                    log.info("Rebalanced: holdings=%s", self._holdings)

        # ── Breakout signal (between rebalances) ────────────────
        closes = self.closes(bar.symbol, period)
        if len(closes) < period:
            return
        highest = max(closes[:-1])
        lowest = min(closes[:-1])

        if bar.close > highest and bar.symbol in self._holdings:
            self.emit(bar.symbol, "buy", bar.close, f"{period}日高点突破（加仓）",
                      confidence=0.8)
            self._buy_date[bar.symbol] = bar_date  # T+1 tracking

        if bar.close < lowest and bar.symbol in self._holdings:
            if self._is_t1_locked(bar.symbol, bar_date):
                log.info("T+1: skip breakout sell %s (bought today)", bar.symbol)
                return
            self.emit(bar.symbol, "sell", bar.close, f"{period}日低点跌破（减仓）",
                      confidence=0.8)


class RSILive(LiveStrategy):
    """RSI overbought/oversold — live version."""
    strategy_id = "rsi"
    name = "RSI 超买超卖（实盘）"

    def on_bar(self, bar: Bar) -> None:
        period = self.parameters.get("period", 14)
        overbought = self.parameters.get("overbought", 70)
        oversold = self.parameters.get("oversold", 30)
        rsi = self.rsi(bar.symbol, period)
        if rsi is None:
            return
        if rsi < oversold:
            self.emit(bar.symbol, "buy", bar.close, f"RSI({period})={rsi:.1f} 超卖", confidence=0.7)
        elif rsi > overbought:
            self.emit(bar.symbol, "sell", bar.close, f"RSI({period})={rsi:.1f} 超买", confidence=0.7)


# ── Strategy registry ─────────────────────────────────────────────

BUILTIN_STRATEGIES: Dict[str, type] = {
    "dual-ma-crossover": DualMaLive,
    "momentum-breakout": MomentumLive,
    "rsi": RSILive,
}

def get_strategy_class(strategy_id: str) -> Optional[type]:
    if strategy_id in BUILTIN_STRATEGIES:
        return BUILTIN_STRATEGIES[strategy_id]
    # Try dynamic import from strategies module
    try:
        module = importlib.import_module(f"strategies.{strategy_id.replace('-', '_')}")
        return getattr(module, "Strategy", None)
    except (ImportError, AttributeError):
        return None

# ── LiveEngine ────────────────────────────────────────────────────

class LiveEngine:
    def __init__(self):
        self._strategies: Dict[str, LiveStrategy] = {}  # live_strategy_id → instance

    def add_strategy(self, params: dict) -> dict:
        """Register a strategy for live signal generation."""
        ls_id = params["id"]                   # e.g. "ls-001"
        strategy_id = params["strategy_id"]    # e.g. "dual-ma-crossover"
        symbols = params.get("symbols", [])
        parameters = params.get("parameters", {})

        cls = get_strategy_class(strategy_id)
        if cls is None:
            raise ValueError(f"Unknown strategy: {strategy_id}")

        instance = cls(symbols, parameters)
        instance.strategy_id = strategy_id
        self._strategies[ls_id] = instance
        return {"added": ls_id, "strategy_id": strategy_id, "symbols": symbols}

    def remove_strategy(self, params: dict) -> dict:
        ls_id = params["id"]
        self._strategies.pop(ls_id, None)
        return {"removed": ls_id}

    def list_strategies(self, _params: dict = {}) -> list:
        return [
            {
                "id": ls_id,
                "strategy_id": s.strategy_id,
                "symbols": s.symbols,
                "bar_count": {sym: len(s._bars.get(sym, [])) for sym in s.symbols},
            }
            for ls_id, s in self._strategies.items()
        ]

    def feed_bar(self, params: dict) -> dict:
        """
        Feed a new bar to all strategies watching the given symbol.
        Emits 'signal' events for any signals produced.
        """
        bar_data = params.get("bar", params)
        bar = Bar(
            symbol=bar_data["symbol"],
            timestamp=int(bar_data.get("timestamp", time.time() * 1000)),
            open=float(bar_data.get("open", 0)),
            high=float(bar_data.get("high", 0)),
            low=float(bar_data.get("low", 0)),
            close=float(bar_data.get("close", 0)),
            volume=float(bar_data.get("volume", 0)),
            amount=float(bar_data.get("amount", 0)),
            interval=bar_data.get("interval", "1d"),
        )

        signals_emitted = 0
        for ls_id, strategy in self._strategies.items():
            if bar.symbol not in strategy.symbols:
                continue
            try:
                signals = strategy.feed(bar)
                for sig in signals:
                    push_event("signal", {**asdict(sig), "live_strategy_id": ls_id})
                    signals_emitted += 1
            except Exception as exc:
                log.exception("Strategy %s error on %s bar", ls_id, bar.symbol)
                push_event("error", {"live_strategy_id": ls_id, "error": str(exc)})

        return {"signals_emitted": signals_emitted, "symbol": bar.symbol}

    def list_builtin(self, _params: dict = {}) -> list:
        return [
            {"id": sid, "name": cls.name}
            for sid, cls in BUILTIN_STRATEGIES.items()
        ]

    def load_strategy_file(self, params: dict) -> dict:
        """
        Dynamically load a custom strategy from an absolute .py file path.

        Params:
          file_path   (str) — absolute path to the .py file
          strategy_id (str, optional) — override the strategy ID;
                       if omitted, uses cls.strategy_id or the filename stem

        The file must define a top-level class named ``Strategy`` that extends
        ``LiveStrategy``.  After loading, the class is registered into
        BUILTIN_STRATEGIES so subsequent ``add_strategy`` calls can reference it.
        """
        import importlib.util as _ilu
        import os as _os

        file_path = params["file_path"]
        strategy_id_override = params.get("strategy_id")

        spec = _ilu.spec_from_file_location("_custom_strategy", file_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load spec from: {file_path}")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]

        cls = getattr(mod, "Strategy", None)
        if cls is None:
            raise ValueError(f"No 'Strategy' class found in {file_path}")

        sid = (
            strategy_id_override
            or getattr(cls, "strategy_id", None)
            or _os.path.splitext(_os.path.basename(file_path))[0]
        )
        # Normalise to kebab-case so it matches the rest of the registry
        sid = sid.replace("_", "-")
        cls.strategy_id = sid
        BUILTIN_STRATEGIES[sid] = cls

        log.info("Loaded custom strategy '%s' from %s", sid, file_path)
        return {
            "loaded": sid,
            "name": getattr(cls, "name", sid),
            "file": file_path,
        }


# ── Dispatch loop ─────────────────────────────────────────────────

METHODS = {
    "add_strategy": "add_strategy",
    "remove_strategy": "remove_strategy",
    "list_strategies": "list_strategies",
    "feed_bar": "feed_bar",
    "list_builtin": "list_builtin",
    "load_strategy_file": "load_strategy_file",
}

def main():
    engine = LiveEngine()
    push_event("ready", {"module": "live_engine"})

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        request_id = msg.get("requestId", "")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "__init__":
            reply_ok(request_id, {"status": "ok"})
            continue

        if method not in METHODS:
            reply_err(request_id, f"Unknown method: {method}")
            continue

        try:
            fn = getattr(engine, METHODS[method])
            result = fn(params)
            reply_ok(request_id, result)
        except Exception as exc:
            log.exception("Method %s failed", method)
            reply_err(request_id, str(exc))


if __name__ == "__main__":
    main()
