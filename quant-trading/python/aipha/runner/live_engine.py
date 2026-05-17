"""Live strategy engine — NDJSON protocol bridge.

Receives bar data via NDJSON stdin, runs registered strategies, and
emits trading signals back via NDJSON stdout.

Protocol (same as qmt_broker):
  stdin  -> {"requestId": "...", "method": "...", "params": {...}}
  stdout <- {"requestId": "...", "ok": true, "data": ...}
  stdout <- {"event": "signal"|"bar"|"error", "data": {...}}

Adapted from QT live_engine.py for AlphQ architecture.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NDJSON I/O helpers
# ---------------------------------------------------------------------------

def _send(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def reply_ok(request_id: str, data: Any) -> None:
    _send({"requestId": request_id, "ok": True, "data": data})


def reply_err(request_id: str, error: str) -> None:
    _send({"requestId": request_id, "ok": False, "error": error})


def push_event(event: str, data: Any) -> None:
    _send({"event": event, "data": data})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    symbol: str
    timestamp: int  # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    interval: str = "1d"


@dataclass
class Signal:
    timestamp: int
    symbol: str
    direction: str  # buy / sell / short / cover
    price: float
    quantity: Optional[int] = None
    reason: str = ""
    confidence: float = 1.0
    strategy_id: str = ""


# ---------------------------------------------------------------------------
# Base live strategy
# ---------------------------------------------------------------------------

class LiveStrategy:
    """Base class for live strategies.

    Subclasses override ``on_bar()`` and call ``emit()`` to produce signals.
    """

    strategy_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, symbols: list[str], parameters: dict[str, Any]) -> None:
        self.symbols = symbols
        self.parameters = parameters
        self._signals: list[Signal] = []
        self._bars: dict[str, list[Bar]] = {s: [] for s in symbols}

    def on_bar(self, bar: Bar) -> None:
        raise NotImplementedError

    def emit(self, symbol: str, direction: str, price: float,
             reason: str = "", confidence: float = 1.0) -> None:
        self._signals.append(Signal(
            timestamp=int(time.time() * 1000),
            symbol=symbol, direction=direction, price=price,
            reason=reason, confidence=confidence,
            strategy_id=self.strategy_id,
        ))

    def feed(self, bar: Bar) -> list[Signal]:
        self._bars.setdefault(bar.symbol, []).append(bar)
        self._signals.clear()
        self.on_bar(bar)
        return list(self._signals)

    # Indicator helpers
    def closes(self, symbol: str, n: int) -> list[float]:
        bars = self._bars.get(symbol, [])
        return [b.close for b in bars[-n:]] if bars else []

    def sma(self, symbol: str, period: int) -> Optional[float]:
        prices = self.closes(symbol, period)
        return sum(prices) / len(prices) if len(prices) >= period else None

    def ema(self, symbol: str, period: int) -> Optional[float]:
        bars = self._bars.get(symbol, [])
        if not bars or len(bars) < period:
            return None
        closes = [b.close for b in bars]
        k = 2 / (period + 1)
        ema_val = sum(closes[:period]) / period
        for c in closes[period:]:
            ema_val = c * k + ema_val * (1 - k)
        return ema_val

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
        return 100 - 100 / (1 + avg_gain / avg_loss)


# ---------------------------------------------------------------------------
# Built-in live strategies
# ---------------------------------------------------------------------------

class DualMaLive(LiveStrategy):
    """Dual moving-average crossover."""

    strategy_id = "dual-ma-crossover"
    name = "双均线交叉（实盘）"

    def on_bar(self, bar: Bar) -> None:
        fast = self.parameters.get("fast_period", 5)
        slow = self.parameters.get("slow_period", 20)
        closes = self.closes(bar.symbol, slow + 1)
        if len(closes) < slow + 1:
            return
        fast_prev = sum(closes[-fast - 1:-1]) / fast
        slow_prev = sum(closes[-slow - 1:-1]) / slow
        fast_curr = sum(closes[-fast:]) / fast
        slow_curr = sum(closes[-slow:]) / slow
        if fast_prev < slow_prev and fast_curr > slow_curr:
            self.emit(bar.symbol, "buy", bar.close,
                      f"快线({fast})上穿慢线({slow})")
        elif fast_prev > slow_prev and fast_curr < slow_curr:
            self.emit(bar.symbol, "sell", bar.close,
                      f"快线({fast})下穿慢线({slow})")


class MomentumLive(LiveStrategy):
    """N-day high breakout momentum."""

    strategy_id = "momentum-breakout"
    name = "动量突破（实盘）"

    def on_bar(self, bar: Bar) -> None:
        period = self.parameters.get("period", 20)
        closes = self.closes(bar.symbol, period)
        if len(closes) < period:
            return
        highest = max(closes[:-1])
        if bar.close > highest:
            self.emit(bar.symbol, "buy", bar.close, f"{period}日高点突破")
        lowest = min(closes[:-1])
        if bar.close < lowest:
            self.emit(bar.symbol, "sell", bar.close, f"{period}日低点跌破")


class RSILive(LiveStrategy):
    """RSI overbought/oversold."""

    strategy_id = "rsi"
    name = "RSI 超买超卖（实盘）"

    def on_bar(self, bar: Bar) -> None:
        period = self.parameters.get("period", 14)
        overbought = self.parameters.get("overbought", 70)
        oversold = self.parameters.get("oversold", 30)
        rsi_val = self.rsi(bar.symbol, period)
        if rsi_val is None:
            return
        if rsi_val < oversold:
            self.emit(bar.symbol, "buy", bar.close,
                      f"RSI({period})={rsi_val:.1f} 超卖", confidence=0.7)
        elif rsi_val > overbought:
            self.emit(bar.symbol, "sell", bar.close,
                      f"RSI({period})={rsi_val:.1f} 超买", confidence=0.7)


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

BUILTIN_STRATEGIES: dict[str, type] = {
    "dual-ma-crossover": DualMaLive,
    "momentum-breakout": MomentumLive,
    "rsi": RSILive,
}


def get_strategy_class(strategy_id: str) -> Optional[type]:
    """Resolve strategy class by ID — built-in or dynamic import."""
    if strategy_id in BUILTIN_STRATEGIES:
        return BUILTIN_STRATEGIES[strategy_id]
    try:
        mod = importlib.import_module(
            f"aipha.runner.strategies.{strategy_id.replace('-', '_')}")
        return getattr(mod, "Strategy", None)
    except (ImportError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# LiveEngine — strategy management + bar dispatch
# ---------------------------------------------------------------------------

class LiveEngine:
    """Manages live strategy instances and feeds bar data to them."""

    def __init__(self) -> None:
        self._strategies: dict[str, LiveStrategy] = {}

    def add_strategy(self, params: dict) -> dict:
        ls_id = params["id"]
        strategy_id = params["strategy_id"]
        symbols = params.get("symbols", [])
        parameters = params.get("parameters", {})
        cls = get_strategy_class(strategy_id)
        if cls is None:
            raise ValueError(f"Unknown strategy: {strategy_id}")
        instance = cls(symbols, parameters)
        instance.strategy_id = strategy_id
        self._strategies[ls_id] = instance
        return {"added": ls_id, "strategy_id": strategy_id,
                "symbols": symbols}

    def remove_strategy(self, params: dict) -> dict:
        ls_id = params["id"]
        self._strategies.pop(ls_id, None)
        return {"removed": ls_id}

    def list_strategies(self, _params: dict | None = None) -> list:
        return [
            {"id": ls_id, "strategy_id": s.strategy_id,
             "symbols": s.symbols,
             "bar_count": {sym: len(s._bars.get(sym, []))
                          for sym in s.symbols}}
            for ls_id, s in self._strategies.items()
        ]

    def feed_bar(self, params: dict) -> dict:
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
                    push_event("signal",
                               {**asdict(sig), "live_strategy_id": ls_id})
                    signals_emitted += 1
            except Exception as exc:
                logger.exception("Strategy %s error", ls_id)
                push_event("error",
                           {"live_strategy_id": ls_id, "error": str(exc)})
        return {"signals_emitted": signals_emitted, "symbol": bar.symbol}

    def list_builtin(self, _params: dict | None = None) -> list:
        return [{"id": sid, "name": cls.name}
                for sid, cls in BUILTIN_STRATEGIES.items()]

    def load_strategy_file(self, params: dict) -> dict:
        """Dynamically load a custom strategy from a .py file."""
        file_path = params["file_path"]
        sid_override = params.get("strategy_id")
        spec = importlib.util.spec_from_file_location(
            "_custom_strategy", file_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load spec from: {file_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Strategy", None)
        if cls is None:
            raise ValueError(f"No 'Strategy' class in {file_path}")
        sid = (sid_override or getattr(cls, "strategy_id", None)
               or os.path.splitext(os.path.basename(file_path))[0])
        sid = sid.replace("_", "-")
        cls.strategy_id = sid
        BUILTIN_STRATEGIES[sid] = cls
        return {"loaded": sid, "name": getattr(cls, "name", sid),
                "file": file_path}


# ---------------------------------------------------------------------------
# NDJSON dispatch methods
# ---------------------------------------------------------------------------

_ENGINE_METHODS = {
    "add_strategy": "add_strategy",
    "remove_strategy": "remove_strategy",
    "list_strategies": "list_strategies",
    "feed_bar": "feed_bar",
    "list_builtin": "list_builtin",
    "load_strategy_file": "load_strategy_file",
}


def run_ndjson_loop() -> None:
    """Main NDJSON dispatch loop — reads stdin, writes stdout."""
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

        if method not in _ENGINE_METHODS:
            reply_err(request_id, f"Unknown method: {method}")
            continue

        try:
            fn = getattr(engine, _ENGINE_METHODS[method])
            result = fn(params)
            reply_ok(request_id, result)
        except Exception as exc:
            logger.exception("Method %s failed", method)
            reply_err(request_id, str(exc))


if __name__ == "__main__":
    run_ndjson_loop()
