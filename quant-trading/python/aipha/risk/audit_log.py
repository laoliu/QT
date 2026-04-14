"""审计日志 — 记录所有交易决策和风控操作。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLogger:
    """合规审计日志"""

    def __init__(self, log_dir: str | Path | None = None):
        self._log_dir = Path(log_dir) if log_dir else None
        self._entries: list[dict[str, Any]] = []

    def log_trade(
        self,
        timestamp: datetime,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        reason: str = "",
        constraint_name: str = "",
    ) -> None:
        entry = {
            "type": "trade",
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "reason": reason,
            "constraint": constraint_name,
        }
        self._entries.append(entry)
        logger.debug("审计: %s %s %s x%d @%.2f (%s)", side, symbol, constraint_name, qty, price, reason)

    def log_risk_event(
        self,
        timestamp: datetime,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "type": "risk_event",
            "timestamp": timestamp.isoformat(),
            "event_type": event_type,
            "message": message,
            "details": details or {},
        }
        self._entries.append(entry)

    def get_entries(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            return self._entries.copy()
        return [e for e in self._entries if e.get("type") == event_type]

    def save(self, path: str | Path | None = None) -> Path | None:
        if not self._entries:
            return None
        target = Path(path) if path else (self._log_dir / "audit_log.json" if self._log_dir else None)
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)
        return target

    def clear(self) -> None:
        self._entries.clear()
