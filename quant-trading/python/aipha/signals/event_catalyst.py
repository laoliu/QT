"""US→A-share event catalyst signal engine.

Maps US tech giant price movements to A-share counterparts,
validates with domestic capital flow data, and produces catalyst
signals for potential gap-up or theme-driven moves.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["EventCatalystEngine", "CatalystSignal"]

logger = logging.getLogger(__name__)

# Default US→A-share mapping
_DEFAULT_MAPPING: Dict[str, Dict[str, Any]] = {
    "AAPL": {"a_codes": ["002475.SZ"], "theme": "消费电子"},
    "NVDA": {"a_codes": ["603986.SH"], "theme": "AI芯片"},
    "TSLA": {"a_codes": ["300750.SZ"], "theme": "新能源车"},
    "MSFT": {"a_codes": ["600588.SH"], "theme": "云计算"},
    "GOOGL": {"a_codes": ["300433.SZ"], "theme": "AI"},
    "AMZN": {"a_codes": ["002024.SZ"], "theme": "电商物流"},
    "META": {"a_codes": ["300413.SZ"], "theme": "VR/元宇宙"},
    "AMD": {"a_codes": ["603986.SH"], "theme": "半导体"},
}


@dataclass
class CatalystSignal:
    """A catalyst signal from US→A mapping."""

    us_symbol: str = ""
    us_change_pct: float = 0.0
    theme: str = ""
    a_codes: list = field(default_factory=list)
    signal_strength: str = "none"
    score: float = 0.0
    flow_confirmed: bool = False
    details: dict = field(default_factory=dict)


class EventCatalystEngine:
    """Monitor US market events and map to A-share opportunities."""

    def __init__(
        self,
        mapping: Optional[Dict[str, Dict[str, Any]]] = None,
        mapping_file: Optional[str] = None,
        change_threshold: float = 3.0,
    ) -> None:
        self.change_threshold = change_threshold
        if mapping is not None:
            self._mapping = mapping
        elif mapping_file and os.path.isfile(mapping_file):
            with open(mapping_file, "r", encoding="utf-8") as f:
                self._mapping = json.load(f)
        else:
            self._mapping = _DEFAULT_MAPPING

    def analyze_us_movers(
        self, us_quotes: Dict[str, Dict[str, float]],
    ) -> List[CatalystSignal]:
        """Analyze US quote changes and produce catalyst signals.

        Parameters
        ----------
        us_quotes : dict
            Mapping of US symbol -> {"change_pct": float, "close": float, ...}.

        Returns
        -------
        List of CatalystSignal for symbols exceeding threshold.
        """
        signals: List[CatalystSignal] = []
        for us_sym, quote in us_quotes.items():
            chg = quote.get("change_pct", 0.0)
            if abs(chg) < self.change_threshold:
                continue
            info = self._mapping.get(us_sym)
            if not info:
                continue
            strength = self._classify_strength(chg)
            score = min(100.0, abs(chg) * 10)
            signals.append(CatalystSignal(
                us_symbol=us_sym,
                us_change_pct=chg,
                theme=info.get("theme", ""),
                a_codes=info.get("a_codes", []),
                signal_strength=strength,
                score=score,
                details={"close": quote.get("close", 0)},
            ))
        signals.sort(key=lambda s: abs(s.us_change_pct), reverse=True)
        return signals

    def enrich_with_flow(
        self,
        signals: List[CatalystSignal],
        flow_data: Dict[str, Dict[str, float]],
    ) -> List[CatalystSignal]:
        """Enrich signals with A-share capital flow confirmation.

        Parameters
        ----------
        flow_data : dict
            Mapping of A-share ts_code -> {"main_net": float, ...}.
        """
        for sig in signals:
            confirmed = False
            for code in sig.a_codes:
                flow = flow_data.get(code, {})
                main_net = flow.get("main_net", 0.0)
                if sig.us_change_pct > 0 and main_net > 0:
                    confirmed = True
                elif sig.us_change_pct < 0 and main_net < 0:
                    confirmed = True
            sig.flow_confirmed = confirmed
            if confirmed:
                sig.score = min(100.0, sig.score * 1.2)
        return signals

    @staticmethod
    def _classify_strength(change_pct: float) -> str:
        absv = abs(change_pct)
        if absv >= 8.0:
            return "极强"
        if absv >= 5.0:
            return "强"
        if absv >= 3.0:
            return "中"
        return "弱"

    @property
    def mapping(self) -> Dict[str, Dict[str, Any]]:
        return self._mapping
