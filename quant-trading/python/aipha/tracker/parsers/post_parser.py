"""Parse stock codes and trading signals from VIP post text.

Handles various mentions:
- Stock codes: $贵州茅台(SH600519)$, $SH600519$, #600519#
- Trading keywords: 买入/加仓/建仓/减仓/清仓/卖出
- Percentage mentions: 仓位30%, 加到20%
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StockMention:
    """A stock mentioned in a post."""

    symbol: str         # e.g. '600519.SH'
    name: str           # e.g. '贵州茅台'
    raw: str            # original matched text


@dataclass(frozen=True, slots=True)
class TradingSignal:
    """A trading signal extracted from post text."""

    symbol: str
    name: str
    action: str               # BUY / SELL / INCREASE / DECREASE / CLEAR
    weight_hint: float | None  # e.g. 0.3 if "30%" mentioned
    context: str               # surrounding text for reference


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Xueqiu's stock mention format: $贵州茅台(SH600519)$ or $SH600519$
_XUEQIU_STOCK_PATTERN = re.compile(
    r'\$([^$]*?)\(([A-Z]{2}\d{6})\)\$'    # $Name(SH600519)$
    r'|\$([A-Z]{2}\d{6})\$'               # $SH600519$
)

# Bare stock codes in text
_BARE_CODE_PATTERN = re.compile(
    r'(?:^|[\s,，、;；(（])(\d{6})(?:[\s,，、;；)）]|$)'
)

# Common stock code with exchange prefix
_PREFIXED_CODE_PATTERN = re.compile(
    r'\b(SH|SZ|BJ)(\d{6})\b', re.IGNORECASE
)

# Trading action keywords grouped by category.
# NOTE: CLEAR must appear before SELL so that "全部卖出" matches CLEAR, not SELL.
# Within each list, longer keywords come first for the same reason.
_ACTION_PATTERNS: dict[str, list[str]] = {
    "CLEAR": ["全部卖出", "清仓", "全卖", "清掉", "割肉"],
    "BUY": ["买入", "建仓", "新买", "入手", "申购"],
    "SELL": ["卖出", "止盈", "止损"],
    "INCREASE": ["加仓", "加买", "补仓", "增持", "增加"],
    "DECREASE": ["减仓", "减持", "减少", "降仓"],
}

# Percentage patterns (supports half-width % and full-width ％)
_PERCENT_PATTERN = re.compile(
    r'(\d+(?:\.\d+)?)\s*[%\uff05]'
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_stock_mentions(text: str) -> list[StockMention]:
    """Extract all stock mentions from text.

    Returns:
        List of StockMention, deduplicated by symbol.
    """
    from ..utils import normalize_symbol as _normalize_symbol

    seen: set[str] = set()
    results: list[StockMention] = []

    # 1. Xueqiu format: $Name(SH600519)$
    for match in _XUEQIU_STOCK_PATTERN.finditer(text):
        if match.group(2):  # $Name(SH600519)$
            name = match.group(1).strip()
            code = match.group(2)
        else:  # $SH600519$
            name = ""
            code = match.group(3)

        symbol = _normalize_symbol(code)
        if symbol not in seen:
            seen.add(symbol)
            results.append(StockMention(symbol=symbol, name=name, raw=match.group(0)))

    # 2. Prefixed codes: SH600519
    for match in _PREFIXED_CODE_PATTERN.finditer(text):
        code = f"{match.group(1).upper()}{match.group(2)}"
        symbol = _normalize_symbol(code)
        if symbol not in seen:
            seen.add(symbol)
            results.append(StockMention(symbol=symbol, name="", raw=match.group(0)))

    # 3. Bare 6-digit codes (lower confidence — only if no better matches)
    if not results:
        for match in _BARE_CODE_PATTERN.finditer(text):
            code = match.group(1)
            symbol = _normalize_symbol(code)
            if symbol not in seen:
                seen.add(symbol)
                results.append(StockMention(symbol=symbol, name="", raw=match.group(0)))

    return results


def extract_trading_signals(text: str) -> list[TradingSignal]:
    """Extract trading signals from text by combining stock mentions with action keywords.

    Algorithm:
    1. Find all stock mentions
    2. Find all action keywords
    3. Match keywords to nearby stock mentions (within a window)
    """
    mentions = extract_stock_mentions(text)
    if not mentions:
        return []

    signals: list[TradingSignal] = []

    for mention in mentions:
        # Get context around the mention
        pos = text.find(mention.raw)
        if pos < 0:
            pos = 0
        start = max(0, pos - 50)
        end = min(len(text), pos + len(mention.raw) + 50)
        context = text[start:end]

        # Check for action keywords in the context
        action = _detect_action(context)
        if action:
            weight = _detect_weight(context)
            signals.append(
                TradingSignal(
                    symbol=mention.symbol,
                    name=mention.name,
                    action=action,
                    weight_hint=weight,
                    context=context.strip(),
                )
            )

    return signals


def parse_post(text: str) -> dict[str, Any]:
    """Full analysis of a post text.

    Returns:
        Dict with keys: mentions, signals, summary.
    """
    # Strip HTML tags if present
    clean = _strip_html(text)

    mentions = extract_stock_mentions(clean)
    signals = extract_trading_signals(clean)

    return {
        "mentions": [{"symbol": m.symbol, "name": m.name} for m in mentions],
        "signals": [
            {
                "symbol": s.symbol,
                "name": s.name,
                "action": s.action,
                "weight_hint": s.weight_hint,
                "context": s.context,
            }
            for s in signals
        ],
        "mention_count": len(mentions),
        "signal_count": len(signals),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_action(context: str) -> str | None:
    """Detect trading action from text context."""
    for action, keywords in _ACTION_PATTERNS.items():
        for kw in keywords:
            if kw in context:
                return action
    return None


def _detect_weight(context: str) -> float | None:
    """Detect weight/percentage from text context."""
    match = _PERCENT_PATTERN.search(context)
    if match:
        pct = float(match.group(1))
        if 0 < pct <= 100:
            return pct / 100.0
    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r'<[^>]+>', '', text)
