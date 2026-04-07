"""
trade_record_matcher.py — Symbol normalization and trade record grouping engine.

Provides:
  - normalize_symbol(): Unify stock code formats across markets
    (A-share sh/sz prefixes, HK .HK suffixes, US ticker formats)
  - detect_market(): Infer market type from raw symbol string
  - group_records_by_symbol(): Group live and backtest trade records by
    normalized symbol code, producing a dict keyed by canonical symbol

Supported code formats:
  A-share:  600519, sh600519, SH600519, 600519.SH, 600519.SS → 600519
  HK:       00700, 00700.HK, HK00700, 0700.HK → 00700
  US:       AAPL, AAPL.US, US_AAPL → AAPL
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Market type constants ────────────────────────────────────────

MARKET_CN = "cn_stock"
MARKET_HK = "hk_stock"
MARKET_US = "us_stock"
MARKET_FUND = "fund"
MARKET_FUTURES = "futures"
MARKET_UNKNOWN = "unknown"


# ── Symbol normalization ─────────────────────────────────────────

# Regex patterns for various code formats
_RE_CN_PREFIXED = re.compile(r"^(sh|sz|SH|SZ)(\d{6})$")              # sh600519
_RE_CN_SUFFIXED = re.compile(r"^(\d{6})\.(SH|SZ|SS|sh|sz|ss)$")     # 600519.SH / 600519.SS
_RE_CN_BARE = re.compile(r"^(\d{6})$")                                # 600519
_RE_HK_SUFFIXED = re.compile(r"^(\d{4,5})\.HK$", re.IGNORECASE)      # 00700.HK
_RE_HK_PREFIXED = re.compile(r"^HK(\d{4,5})$", re.IGNORECASE)        # HK00700
_RE_US_SUFFIXED = re.compile(r"^([A-Z]{1,5})\.US$", re.IGNORECASE)   # AAPL.US
_RE_US_PREFIXED = re.compile(r"^US[_.]([A-Z]{1,5})$", re.IGNORECASE) # US_AAPL / US.AAPL
_RE_US_BARE = re.compile(r"^[A-Z]{1,5}$")                             # AAPL


def normalize_symbol(raw: str) -> str:
    """Normalize a stock symbol to its canonical form.

    Canonical forms:
      - A-share: bare 6-digit code, e.g. "600519"
      - HK: 5-digit zero-padded code, e.g. "00700"
      - US: uppercase ticker, e.g. "AAPL"

    Returns the cleaned canonical code.
    """
    s = raw.strip()
    if not s:
        return s

    # A-share: prefixed format (sh600519 / SZ000001)
    m = _RE_CN_PREFIXED.match(s)
    if m:
        return m.group(2)

    # A-share: suffixed format (600519.SH / 600519.SS)
    m = _RE_CN_SUFFIXED.match(s)
    if m:
        return m.group(1)

    # HK: suffixed format (00700.HK)
    m = _RE_HK_SUFFIXED.match(s)
    if m:
        return m.group(1).zfill(5)

    # HK: prefixed format (HK00700)
    m = _RE_HK_PREFIXED.match(s)
    if m:
        return m.group(1).zfill(5)

    # US: suffixed format (AAPL.US)
    m = _RE_US_SUFFIXED.match(s)
    if m:
        return m.group(1).upper()

    # US: prefixed format (US_AAPL / US.AAPL)
    m = _RE_US_PREFIXED.match(s)
    if m:
        return m.group(1).upper()

    # A-share: bare 6-digit code
    m = _RE_CN_BARE.match(s)
    if m:
        return m.group(0)

    # US: bare uppercase ticker
    m = _RE_US_BARE.match(s)
    if m:
        return m.group(0).upper()

    # Fallback: return stripped original
    return s


def detect_market(raw: str) -> str:
    """Detect the market type from a raw symbol string.

    Returns one of: cn_stock, hk_stock, us_stock, fund, futures, unknown.
    """
    s = raw.strip()
    if not s:
        return MARKET_UNKNOWN

    # Explicit market markers
    if _RE_CN_PREFIXED.match(s) or _RE_CN_SUFFIXED.match(s):
        code = normalize_symbol(s)
        return _classify_cn_code(code)

    if _RE_HK_SUFFIXED.match(s) or _RE_HK_PREFIXED.match(s):
        return MARKET_HK

    if _RE_US_SUFFIXED.match(s) or _RE_US_PREFIXED.match(s):
        return MARKET_US

    # Bare 6-digit → classify as CN stock or fund
    if _RE_CN_BARE.match(s):
        return _classify_cn_code(s)

    # Bare uppercase letters → US stock
    if _RE_US_BARE.match(s):
        return MARKET_US

    # HK bare: 4-5 digits but not 6
    if re.match(r"^\d{4,5}$", s):
        return MARKET_HK

    return MARKET_UNKNOWN


def _classify_cn_code(code: str) -> str:
    """Classify a bare 6-digit A-share code into stock, fund, or futures."""
    if code.startswith(("00", "30", "60", "68")):
        return MARKET_CN
    if code.startswith(("50", "51", "15", "16", "18")):
        return MARKET_FUND
    if code.startswith(("11", "12", "13")):
        return MARKET_CN  # convertible bonds, treated as CN market
    return MARKET_CN


# ── Trade record dataclass ───────────────────────────────────────

@dataclass
class TradeRecord:
    """Unified trade record representation for both live and backtest trades.

    Fields align with the TypeScript TradeRecord type in src/types.ts.
    """
    trade_id: str
    symbol: str               # raw symbol as received
    side: str                 # "buy" / "sell"
    entry_time: str           # ISO datetime
    exit_time: Optional[str] = None
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    quantity: int = 0
    gross_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0
    holding_days: Optional[int] = None
    strategy_id: Optional[str] = None
    source: str = "unknown"   # "live" / "backtest"
    market: str = ""
    name: Optional[str] = None

    # Computed after normalization
    normalized_symbol: str = ""


# ── Grouping engine ──────────────────────────────────────────────

@dataclass
class SymbolGroup:
    """A group of trade records for a single normalized symbol."""
    canonical_symbol: str
    market: str
    live_records: list[TradeRecord] = field(default_factory=list)
    backtest_records: list[TradeRecord] = field(default_factory=list)

    @property
    def all_records(self) -> list[TradeRecord]:
        return self.live_records + self.backtest_records

    @property
    def total_count(self) -> int:
        return len(self.live_records) + len(self.backtest_records)


def group_records_by_symbol(
    records: list[TradeRecord],
) -> dict[str, SymbolGroup]:
    """Group trade records by normalized symbol code.

    Normalizes each record's symbol, then groups into SymbolGroup instances
    keyed by the canonical symbol. Records are separated into live_records
    and backtest_records within each group.

    Args:
        records: List of TradeRecord objects (mixed live and backtest).

    Returns:
        Dict mapping canonical symbol → SymbolGroup.
    """
    groups: dict[str, SymbolGroup] = {}

    for rec in records:
        canonical = normalize_symbol(rec.symbol)
        rec.normalized_symbol = canonical

        if not rec.market:
            rec.market = detect_market(rec.symbol)

        if canonical not in groups:
            groups[canonical] = SymbolGroup(
                canonical_symbol=canonical,
                market=rec.market,
            )

        group = groups[canonical]
        if rec.source == "live":
            group.live_records.append(rec)
        else:
            group.backtest_records.append(rec)

    # Sort records within each group by entry_time
    for group in groups.values():
        group.live_records.sort(key=lambda r: r.entry_time)
        group.backtest_records.sort(key=lambda r: r.entry_time)

    return groups


# ── Helper: create TradeRecord from dict ─────────────────────────

def trade_record_from_dict(d: dict[str, Any], source: str = "unknown") -> TradeRecord:
    """Create a TradeRecord from a dictionary (e.g. from JSON).

    Handles both Python-style snake_case and TypeScript-style camelCase keys.
    """
    return TradeRecord(
        trade_id=d.get("trade_id") or d.get("tradeId", ""),
        symbol=d.get("symbol", ""),
        side=d.get("side", ""),
        entry_time=d.get("entry_time") or d.get("entryTime", ""),
        exit_time=d.get("exit_time") or d.get("exitTime"),
        entry_price=float(d.get("entry_price") or d.get("entryPrice", 0)),
        exit_price=_opt_float(d.get("exit_price") or d.get("exitPrice")),
        quantity=int(d.get("quantity", 0)),
        gross_pnl=float(d.get("gross_pnl") or d.get("grossPnl", 0)),
        commission=float(d.get("commission", 0)),
        net_pnl=float(d.get("net_pnl") or d.get("netPnl", 0)),
        holding_days=_opt_int(d.get("holding_days") or d.get("holdingDays")),
        strategy_id=d.get("strategy_id") or d.get("strategyId"),
        source=source,
        market=d.get("market", ""),
        name=d.get("name"),
    )


def _opt_float(v: Any) -> Optional[float]:
    return float(v) if v is not None else None


def _opt_int(v: Any) -> Optional[int]:
    return int(v) if v is not None else None
