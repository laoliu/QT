"""
Base screener class and registry for stock screening strategies.

All screeners inherit from BaseScreener and implement the `screen()` method.
Screeners are auto-registered when imported.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ScreenerResult:
    """Result from a screener run."""

    stock_id: str
    name: str = ""
    market: str = ""
    score: float = 0.0  # Overall score (higher = better)
    rank: int = 0
    factors: dict[str, Any] = field(default_factory=dict)  # Factor values
    signals: list[str] = field(default_factory=list)  # Human-readable signal descriptions

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stock_id": self.stock_id,
            "name": self.name,
            "score": round(self.score, 4),
        }
        if self.market:
            d["market"] = self.market
        if self.rank > 0:
            d["rank"] = self.rank
        if self.factors:
            d["factors"] = {k: round(v, 4) if isinstance(v, float) else v
                            for k, v in self.factors.items()}
        if self.signals:
            d["signals"] = self.signals
        return d


class BaseScreener(ABC):
    """Abstract base class for stock screeners.

    Subclasses must implement:
    - screener_id: unique string identifier
    - screener_name: human-readable name
    - screen(): the actual screening logic
    """

    screener_id: str = ""
    screener_name: str = ""
    screener_description: str = ""
    supported_markets: list[str] = ["cn_stock", "us_stock"]

    def __init__(self, params: Optional[dict[str, Any]] = None):
        """Initialize screener with optional parameters."""
        self.params = params or {}

    @abstractmethod
    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Run the screening logic.

        Args:
            stock_data: {code: {name, price, pe, pb, ...financial data}}
            market: Target market
            limit: Max results to return

        Returns:
            List of ScreenerResult, sorted by score (best first).
        """
        ...

    def get_info(self) -> dict[str, Any]:
        """Return screener metadata."""
        return {
            "id": self.screener_id,
            "name": self.screener_name,
            "description": self.screener_description,
            "supported_markets": self.supported_markets,
            "params": self.params,
        }


# ── Registry ────────────────────────────────────────────────────

_SCREENER_REGISTRY: dict[str, type[BaseScreener]] = {}


def register_screener(cls: type[BaseScreener]) -> type[BaseScreener]:
    """Class decorator to register a screener."""
    if cls.screener_id:
        _SCREENER_REGISTRY[cls.screener_id] = cls
    return cls


def _load_all_screeners() -> None:
    """Import all screener modules to trigger registration."""
    if _SCREENER_REGISTRY:
        return  # Already loaded

    modules = [
        "screeners.canslim",
        "screeners.fscore",
        "screeners.magic_formula",
        "screeners.technical",
        "screeners.volume_price",
        "screeners.peg",
        "screeners.lianban_screener",
        "screeners.overnight_screener",
        "screeners.event_catalyst_screener",
        "screeners.eod_catalyst_screener",
        "screeners.sector_hotspot_screener",
        "screeners.dragon_master_screener",
        "screeners.cb_stock_quality",
        "screeners.ml_ranking_screener",
    ]
    for mod_name in modules:
        try:
            __import__(mod_name)
        except ImportError as e:
            print(f"[screener] Failed to import {mod_name}: {e}", file=sys.stderr)


def get_all_screeners() -> dict[str, type[BaseScreener]]:
    """Get all registered screeners."""
    _load_all_screeners()
    return dict(_SCREENER_REGISTRY)


def get_screener(screener_id: str, params: Optional[dict[str, Any]] = None) -> Optional[BaseScreener]:
    """Get an instantiated screener by ID."""
    _load_all_screeners()
    cls = _SCREENER_REGISTRY.get(screener_id)
    if cls is None:
        return None
    return cls(params)


def list_screener_ids() -> list[str]:
    """Return list of available screener IDs."""
    _load_all_screeners()
    return list(_SCREENER_REGISTRY.keys())
