"""
Base strategy class and strategy registry for the quant-trading strategy library.

All concrete strategies must:
  1. Extend BaseStrategy (which extends bt.Strategy)
  2. Set the class attribute STRATEGY_META with id, name, description, category, parameters
  3. Implement __init__() for indicators and next() for trading logic
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyParameter:
    """Describes a strategy parameter."""
    name: str
    type: str  # "number", "string", "boolean"
    default: Any
    description: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None


@dataclass
class StrategyMeta:
    """Metadata for a strategy."""
    id: str
    name: str
    description: str
    category: str  # "trend", "oscillator", "arbitrage", "range", "composite"
    parameters: list[StrategyParameter] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)  # empty = all markets


# ── Base Strategy ─────────────────────────────────────────────────

try:
    import backtrader as bt

    class BaseStrategy(bt.Strategy):
        """Base class for all strategies in the library.

        Provides common utilities:
          - Signal recording for JSON output
          - Position sizing helpers
          - Stop-loss / take-profit management
          - Logging to stderr (does not conflict with JSON stdout)
        """

        # Subclasses MUST set this
        STRATEGY_META: StrategyMeta | None = None

        def __init__(self):
            super().__init__()
            self._signals: list[dict[str, Any]] = []
            self._trade_count = 0
            self._win_count = 0

        # ── Signal Recording ──────────────────────────────────────

        def record_signal(
            self,
            direction: str,
            price: float,
            reason: str = "",
            symbol: str = "",
        ):
            """Record a trading signal for JSON output."""
            dt = self.data.datetime.date(0)
            self._signals.append({
                "date": str(dt),
                "timestamp": int(self.data.datetime.datetime(0).timestamp() * 1000),
                "symbol": symbol or (self.data._name if hasattr(self.data, "_name") else ""),
                "direction": direction,
                "price": round(price, 4),
                "reason": reason,
                "strategyId": self.STRATEGY_META.id if self.STRATEGY_META else "",
            })

        def get_signals(self) -> list[dict[str, Any]]:
            """Return all recorded signals."""
            return self._signals

        # ── Position Sizing ───────────────────────────────────────

        def calc_position_size(
            self,
            price: float,
            risk_pct: float = 0.02,
            stop_distance: float = 0,
        ) -> int:
            """Calculate position size based on risk percentage.

            Args:
                price: Entry price
                risk_pct: Max % of portfolio to risk (default 2%)
                stop_distance: Distance to stop-loss in price units

            Returns:
                Number of shares to trade (rounded to 100 for A-shares)
            """
            portfolio_value = self.broker.getvalue()
            max_risk = portfolio_value * risk_pct

            if stop_distance > 0:
                # Risk-based sizing: risk_amount / stop_distance
                size = int(max_risk / stop_distance)
            else:
                # Fixed fraction: use risk_pct of portfolio value
                size = int(portfolio_value * risk_pct / price)

            # Round down to lot of 100 for A-shares (price < 5000 CNY),
            # but use raw size for global stocks to avoid zero-rounding
            if size >= 100:
                size = (size // 100) * 100
            return max(size, 0)

        def calc_position_size_pct(self, price: float, pct: float = 0.95) -> int:
            """Calculate position size as percentage of available cash.

            Args:
                price: Entry price
                pct: Fraction of cash to use (default 95%)

            Returns:
                Number of shares (rounded to 100 for A-shares)
            """
            cash = self.broker.getcash()
            size = int(cash * pct / price)
            if size >= 100:
                size = (size // 100) * 100
            return max(size, 0)

        # ── Trade Tracking ────────────────────────────────────────

        def notify_trade(self, trade):
            """Track completed trades for win rate calculation."""
            if trade.isclosed:
                self._trade_count += 1
                if trade.pnl > 0:
                    self._win_count += 1

        def get_win_rate(self) -> float:
            """Return win rate as percentage."""
            if self._trade_count == 0:
                return 0.0
            return self._win_count / self._trade_count * 100

        # ── Logging ───────────────────────────────────────────────

        def log(self, msg: str):
            """Log to stderr (safe for JSON-stdout protocols)."""
            dt = self.data.datetime.date(0)
            print(f"[{dt}] {msg}", file=sys.stderr)

except ImportError:
    # backtrader not installed — provide stub for type checking
    class BaseStrategy:  # type: ignore[no-redef]
        STRATEGY_META = None

        def __init__(self):
            self._signals = []


# ── Strategy Registry ─────────────────────────────────────────────

# Auto-discover strategy modules by scanning for *_strategy.py files
# in this directory and optionally in user's custom strategies directory.
# No need to maintain a hardcoded list — just drop a file and it's registered.

_BUILTIN_STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
_USER_STRATEGY_DIR = os.path.join(
    os.path.expanduser("~"), ".openclaw", "quant-trading", "strategies"
)

_registry: dict[str, type] | None = None


def _discover_strategy_modules() -> list[tuple[str, str]]:
    """Discover all *_strategy.py files in builtin and user dirs.

    Returns list of (module_name, file_path) tuples.
    """
    import glob

    results: list[tuple[str, str]] = []

    # 1. Builtin strategies: strategies/*_strategy.py
    builtin_pattern = os.path.join(_BUILTIN_STRATEGY_DIR, "*_strategy.py")
    for fpath in sorted(glob.glob(builtin_pattern)):
        basename = os.path.splitext(os.path.basename(fpath))[0]
        results.append((basename, fpath))

    # 2. User-defined strategies: ~/.openclaw/quant-trading/strategies/*_strategy.py
    if os.path.isdir(_USER_STRATEGY_DIR):
        user_pattern = os.path.join(_USER_STRATEGY_DIR, "*_strategy.py")
        for fpath in sorted(glob.glob(user_pattern)):
            basename = os.path.splitext(os.path.basename(fpath))[0]
            results.append((f"user_{basename}", fpath))

    return results


def _load_registry() -> dict[str, type]:
    """Lazily load all strategy classes via filesystem auto-discovery."""
    global _registry
    if _registry is not None:
        return _registry

    _registry = {}

    for module_name, fpath in _discover_strategy_modules():
        try:
            # For builtin strategies, use package-relative import
            if fpath.startswith(_BUILTIN_STRATEGY_DIR):
                basename = os.path.splitext(os.path.basename(fpath))[0]
                mod = importlib.import_module(f".{basename}", package="strategies")
            else:
                # For user strategies, import from file path directly
                import importlib.util as ilu
                spec = ilu.spec_from_file_location(module_name, fpath)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)

            # Find all BaseStrategy subclasses in the module
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and hasattr(attr, "STRATEGY_META")
                    and attr.STRATEGY_META is not None
                    and attr is not BaseStrategy
                ):
                    _registry[attr.STRATEGY_META.id] = attr
        except Exception as e:
            print(f"[strategy-registry] Failed to load {module_name}: {e}", file=sys.stderr)

    return _registry


def get_all_strategies() -> list[dict[str, Any]]:
    """Return metadata for all registered strategies."""
    registry = _load_registry()
    result = []
    for strategy_id, cls in registry.items():
        meta: StrategyMeta = cls.STRATEGY_META
        result.append({
            "id": meta.id,
            "name": meta.name,
            "description": meta.description,
            "category": meta.category,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "default": p.default,
                    "description": p.description,
                    "min": p.min,
                    "max": p.max,
                    "step": p.step,
                }
                for p in meta.parameters
            ],
            "markets": meta.markets,
        })
    return result


def get_strategy_class(strategy_id: str) -> type | None:
    """Get strategy class by ID."""
    registry = _load_registry()
    return registry.get(strategy_id)


def list_strategy_ids() -> list[str]:
    """Return all registered strategy IDs."""
    return list(_load_registry().keys())
