"""
Data models and interfaces for the live-vs-backtest alignment engine.

Mirrors the TypeScript types defined in src/types.ts (§ Live vs Backtest
Alignment Engine) so that the Python engine can produce results consumable
by the TypeScript bridge via NDJSON.

All models are plain dataclasses — no Pydantic, no runtime validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Enums / Literal-like unions ──────────────────────────────────


class TradeSource(str, Enum):
    """Source of a trade record."""
    LIVE = "live"
    BACKTEST = "backtest"
    PAPER = "paper"


class SymbolMatchMode(str, Enum):
    """How to compare symbol codes across sources."""
    EXACT = "exact"
    NORMALIZED = "normalized"
    FUZZY = "fuzzy"


class MatchQuality(str, Enum):
    """Match quality classification for a paired trade."""
    EXACT = "exact"
    CLOSE = "close"
    LOOSE = "loose"
    UNMATCHED = "unmatched"


# ── Core data models ─────────────────────────────────────────────


@dataclass
class TradeFees:
    """Breakdown of trading fees for A-share and other markets."""

    commission: float
    stamp_duty: Optional[float] = None
    transfer_fee: Optional[float] = None
    other_fees: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"commission": self.commission}
        if self.stamp_duty is not None:
            d["stampDuty"] = self.stamp_duty
        if self.transfer_fee is not None:
            d["transferFee"] = self.transfer_fee
        if self.other_fees is not None:
            d["otherFees"] = self.other_fees
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TradeFees:
        return TradeFees(
            commission=d["commission"],
            stamp_duty=d.get("stampDuty"),
            transfer_fee=d.get("transferFee"),
            other_fees=d.get("otherFees"),
        )


@dataclass
class UnifiedTradeRecord:
    """A trade record in a common structure for alignment, regardless of source.

    Corresponds to the TypeScript ``UnifiedTradeRecord`` type.
    """

    trade_id: str
    source: TradeSource
    symbol: str
    market: str  # MarketType
    side: str  # OrderSide
    entry_time: str  # ISO datetime
    entry_price: float
    quantity: float
    gross_pnl: float
    commission: float
    net_pnl: float

    name: Optional[str] = None
    exit_time: Optional[str] = None  # ISO datetime; None if still open
    exit_price: Optional[float] = None
    holding_days: Optional[int] = None
    strategy_id: Optional[str] = None

    # Source-specific references
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    backtest_id: Optional[str] = None
    signal: Optional[dict[str, Any]] = None  # TradingSignal as dict
    fees: Optional[TradeFees] = None  # Detailed fee breakdown

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for NDJSON bridge output."""
        d: dict[str, Any] = {
            "tradeId": self.trade_id,
            "source": self.source.value,
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side,
            "entryTime": self.entry_time,
            "entryPrice": self.entry_price,
            "quantity": self.quantity,
            "grossPnl": self.gross_pnl,
            "commission": self.commission,
            "netPnl": self.net_pnl,
        }
        if self.name is not None:
            d["name"] = self.name
        if self.exit_time is not None:
            d["exitTime"] = self.exit_time
        if self.exit_price is not None:
            d["exitPrice"] = self.exit_price
        if self.holding_days is not None:
            d["holdingDays"] = self.holding_days
        if self.strategy_id is not None:
            d["strategyId"] = self.strategy_id
        if self.entry_order_id is not None:
            d["entryOrderId"] = self.entry_order_id
        if self.exit_order_id is not None:
            d["exitOrderId"] = self.exit_order_id
        if self.backtest_id is not None:
            d["backtestId"] = self.backtest_id
        if self.signal is not None:
            d["signal"] = self.signal
        if self.fees is not None:
            d["fees"] = self.fees.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> UnifiedTradeRecord:
        """Deserialize from a camelCase dict (as received from TS bridge)."""
        return UnifiedTradeRecord(
            trade_id=d["tradeId"],
            source=TradeSource(d["source"]),
            symbol=d["symbol"],
            market=d["market"],
            side=d["side"],
            entry_time=d["entryTime"],
            entry_price=d["entryPrice"],
            quantity=d["quantity"],
            gross_pnl=d["grossPnl"],
            commission=d["commission"],
            net_pnl=d["netPnl"],
            name=d.get("name"),
            exit_time=d.get("exitTime"),
            exit_price=d.get("exitPrice"),
            holding_days=d.get("holdingDays"),
            strategy_id=d.get("strategyId"),
            entry_order_id=d.get("entryOrderId"),
            exit_order_id=d.get("exitOrderId"),
            backtest_id=d.get("backtestId"),
            signal=d.get("signal"),
            fees=TradeFees.from_dict(d["fees"]) if d.get("fees") else None,
        )


@dataclass
class AlignmentConfig:
    """Configuration for the alignment engine.

    Controls how live and backtest trades are paired together.
    Corresponds to the TypeScript ``AlignmentConfig`` type.
    """

    time_tolerance_sec: float = 86400.0  # 24h — suitable for daily-bar backtests
    symbol_match_mode: SymbolMatchMode = SymbolMatchMode.NORMALIZED
    symbol_aliases: dict[str, list[str]] = field(default_factory=dict)
    require_same_direction: bool = True
    max_price_deviation_pct: Optional[float] = None
    max_quantity_deviation_pct: Optional[float] = None
    one_to_one: bool = True
    strategy_ids: list[str] = field(default_factory=list)

    # Split-fill grouping
    group_split_fills: bool = False
    split_fill_window_sec: float = 60.0

    # Timezone for cross-source date interpretation
    timezone: str = "Asia/Shanghai"

    # Date range filter
    date_range_start: Optional[str] = None  # ISO date, e.g. "2025-01-01"
    date_range_end: Optional[str] = None  # ISO date

    # Market type filter
    market_filter: list[str] = field(default_factory=list)  # MarketType values

    # Minimum match quality threshold
    min_match_quality: Optional[MatchQuality] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timeToleranceSec": self.time_tolerance_sec,
            "symbolMatchMode": self.symbol_match_mode.value,
            "requireSameDirection": self.require_same_direction,
            "oneToOne": self.one_to_one,
        }
        if self.symbol_aliases:
            d["symbolAliases"] = self.symbol_aliases
        if self.max_price_deviation_pct is not None:
            d["maxPriceDeviationPct"] = self.max_price_deviation_pct
        if self.max_quantity_deviation_pct is not None:
            d["maxQuantityDeviationPct"] = self.max_quantity_deviation_pct
        if self.strategy_ids:
            d["strategyIds"] = self.strategy_ids
        if self.group_split_fills:
            d["groupSplitFills"] = self.group_split_fills
            d["splitFillWindowSec"] = self.split_fill_window_sec
        if self.timezone != "Asia/Shanghai":
            d["timezone"] = self.timezone
        if self.date_range_start is not None or self.date_range_end is not None:
            date_range: dict[str, str] = {}
            if self.date_range_start is not None:
                date_range["startDate"] = self.date_range_start
            if self.date_range_end is not None:
                date_range["endDate"] = self.date_range_end
            d["dateRange"] = date_range
        if self.market_filter:
            d["marketFilter"] = self.market_filter
        if self.min_match_quality is not None:
            d["minMatchQuality"] = self.min_match_quality.value
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AlignmentConfig:
        date_range = d.get("dateRange", {})
        min_mq = d.get("minMatchQuality")
        return AlignmentConfig(
            time_tolerance_sec=d.get("timeToleranceSec", 86400),
            symbol_match_mode=SymbolMatchMode(d.get("symbolMatchMode", "normalized")),
            symbol_aliases=d.get("symbolAliases", {}),
            require_same_direction=d.get("requireSameDirection", True),
            max_price_deviation_pct=d.get("maxPriceDeviationPct"),
            max_quantity_deviation_pct=d.get("maxQuantityDeviationPct"),
            one_to_one=d.get("oneToOne", True),
            strategy_ids=d.get("strategyIds", []),
            group_split_fills=d.get("groupSplitFills", False),
            split_fill_window_sec=d.get("splitFillWindowSec", 60),
            timezone=d.get("timezone", "Asia/Shanghai"),
            date_range_start=date_range.get("startDate") if date_range else None,
            date_range_end=date_range.get("endDate") if date_range else None,
            market_filter=d.get("marketFilter", []),
            min_match_quality=MatchQuality(min_mq) if min_mq else None,
        )


# ── Alignment result types ───────────────────────────────────────


@dataclass
class PairDeviation:
    """Deviation metrics between a paired live trade and its backtest counterpart."""

    entry_time_delta_sec: float
    entry_price_deviation_pct: float
    quantity_deviation_pct: float
    pnl_delta: float

    exit_time_delta_sec: Optional[float] = None
    exit_price_deviation_pct: Optional[float] = None
    pnl_deviation_pct: Optional[float] = None
    slippage_pct: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "entryTimeDeltaSec": self.entry_time_delta_sec,
            "entryPriceDeviationPct": self.entry_price_deviation_pct,
            "quantityDeviationPct": self.quantity_deviation_pct,
            "pnlDelta": self.pnl_delta,
        }
        if self.exit_time_delta_sec is not None:
            d["exitTimeDeltaSec"] = self.exit_time_delta_sec
        if self.exit_price_deviation_pct is not None:
            d["exitPriceDeviationPct"] = self.exit_price_deviation_pct
        if self.pnl_deviation_pct is not None:
            d["pnlDeviationPct"] = self.pnl_deviation_pct
        if self.slippage_pct is not None:
            d["slippagePct"] = self.slippage_pct
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PairDeviation:
        return PairDeviation(
            entry_time_delta_sec=d["entryTimeDeltaSec"],
            entry_price_deviation_pct=d["entryPriceDeviationPct"],
            quantity_deviation_pct=d["quantityDeviationPct"],
            pnl_delta=d["pnlDelta"],
            exit_time_delta_sec=d.get("exitTimeDeltaSec"),
            exit_price_deviation_pct=d.get("exitPriceDeviationPct"),
            pnl_deviation_pct=d.get("pnlDeviationPct"),
            slippage_pct=d.get("slippagePct"),
        )


@dataclass
class PairedTrade:
    """A paired trade linking a live/paper trade to its backtest counterpart.

    When one side has no match, only that side is populated and
    ``match_quality`` is ``UNMATCHED``.
    """

    pair_id: str
    match_quality: MatchQuality
    matched_symbol: str

    live_trade: Optional[UnifiedTradeRecord] = None
    backtest_trade: Optional[UnifiedTradeRecord] = None
    deviation: Optional[PairDeviation] = None
    group_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "pairId": self.pair_id,
            "matchQuality": self.match_quality.value,
            "matchedSymbol": self.matched_symbol,
        }
        if self.live_trade is not None:
            d["liveTrade"] = self.live_trade.to_dict()
        if self.backtest_trade is not None:
            d["backtestTrade"] = self.backtest_trade.to_dict()
        if self.deviation is not None:
            d["deviation"] = self.deviation.to_dict()
        if self.group_id is not None:
            d["groupId"] = self.group_id
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PairedTrade:
        return PairedTrade(
            pair_id=d["pairId"],
            match_quality=MatchQuality(d["matchQuality"]),
            matched_symbol=d["matchedSymbol"],
            live_trade=UnifiedTradeRecord.from_dict(d["liveTrade"]) if d.get("liveTrade") else None,
            backtest_trade=UnifiedTradeRecord.from_dict(d["backtestTrade"]) if d.get("backtestTrade") else None,
            deviation=PairDeviation.from_dict(d["deviation"]) if d.get("deviation") else None,
            group_id=d.get("groupId"),
        )


@dataclass
class AlignmentSummary:
    """Summary statistics for an alignment result set."""

    total_live_trades: int
    total_backtest_trades: int
    paired_count: int
    unmatched_live_count: int
    unmatched_backtest_count: int
    quality_distribution: dict[str, int]  # MatchQuality value → count
    avg_entry_time_delta_sec: float
    avg_entry_price_deviation_pct: float
    live_total_pnl: float
    backtest_total_pnl: float
    pnl_gap: float
    match_rate: float
    live_total_fees: float
    backtest_total_fees: float
    avg_slippage_pct: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "totalLiveTrades": self.total_live_trades,
            "totalBacktestTrades": self.total_backtest_trades,
            "pairedCount": self.paired_count,
            "unmatchedLiveCount": self.unmatched_live_count,
            "unmatchedBacktestCount": self.unmatched_backtest_count,
            "qualityDistribution": self.quality_distribution,
            "avgEntryTimeDeltaSec": self.avg_entry_time_delta_sec,
            "avgEntryPriceDeviationPct": self.avg_entry_price_deviation_pct,
            "liveTotalPnl": self.live_total_pnl,
            "backtestTotalPnl": self.backtest_total_pnl,
            "pnlGap": self.pnl_gap,
            "matchRate": self.match_rate,
            "liveTotalFees": self.live_total_fees,
            "backtestTotalFees": self.backtest_total_fees,
        }
        if self.avg_slippage_pct is not None:
            d["avgSlippagePct"] = self.avg_slippage_pct
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AlignmentSummary:
        return AlignmentSummary(
            total_live_trades=d["totalLiveTrades"],
            total_backtest_trades=d["totalBacktestTrades"],
            paired_count=d["pairedCount"],
            unmatched_live_count=d["unmatchedLiveCount"],
            unmatched_backtest_count=d["unmatchedBacktestCount"],
            quality_distribution=d["qualityDistribution"],
            avg_entry_time_delta_sec=d["avgEntryTimeDeltaSec"],
            avg_entry_price_deviation_pct=d["avgEntryPriceDeviationPct"],
            live_total_pnl=d["liveTotalPnl"],
            backtest_total_pnl=d["backtestTotalPnl"],
            pnl_gap=d["pnlGap"],
            match_rate=d["matchRate"],
            live_total_fees=d["liveTotalFees"],
            backtest_total_fees=d["backtestTotalFees"],
            avg_slippage_pct=d.get("avgSlippagePct"),
        )


@dataclass
class AlignmentResult:
    """Complete result of an alignment run."""

    run_id: str
    config: AlignmentConfig
    pairs: list[PairedTrade]
    summary: AlignmentSummary
    aligned_at: str  # ISO datetime
    strategy_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "runId": self.run_id,
            "config": self.config.to_dict(),
            "pairs": [p.to_dict() for p in self.pairs],
            "summary": self.summary.to_dict(),
            "alignedAt": self.aligned_at,
        }
        if self.strategy_id is not None:
            d["strategyId"] = self.strategy_id
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AlignmentResult:
        return AlignmentResult(
            run_id=d["runId"],
            config=AlignmentConfig.from_dict(d["config"]),
            pairs=[PairedTrade.from_dict(p) for p in d["pairs"]],
            summary=AlignmentSummary.from_dict(d["summary"]),
            aligned_at=d["alignedAt"],
            strategy_id=d.get("strategyId"),
        )


# ── Engine interface ─────────────────────────────────────────────


class IAlignmentEngine(ABC):
    """Abstract interface for the alignment engine.

    Implementations must provide the ``align`` method that takes two lists
    of unified trade records and produces an ``AlignmentResult``.
    """

    @abstractmethod
    def align(
        self,
        live_trades: list[UnifiedTradeRecord],
        backtest_trades: list[UnifiedTradeRecord],
        config: AlignmentConfig,
    ) -> AlignmentResult:
        """Align live/paper trades against backtest trades.

        Args:
            live_trades: Trade records from live or paper execution.
            backtest_trades: Trade records from backtest runs.
            config: Alignment configuration controlling matching rules.

        Returns:
            Complete alignment result with paired trades and summary stats.
        """
        ...

    def normalize_symbol(self, symbol: str) -> str:
        """Strip exchange suffixes for normalized matching.

        Examples:
            "600519.SH" → "600519"
            "000001.SZ" → "000001"
            "AAPL.US"   → "AAPL"
        """
        for suffix in (".SH", ".SZ", ".SS", ".US", ".HK", ".BJ"):
            if symbol.upper().endswith(suffix):
                return symbol[: -len(suffix)]
        return symbol

    def symbols_match(
        self,
        symbol_a: str,
        symbol_b: str,
        config: AlignmentConfig,
    ) -> bool:
        """Check whether two symbols should be considered the same asset."""
        if config.symbol_match_mode == SymbolMatchMode.EXACT:
            return symbol_a == symbol_b

        norm_a = self.normalize_symbol(symbol_a)
        norm_b = self.normalize_symbol(symbol_b)

        if config.symbol_match_mode == SymbolMatchMode.NORMALIZED:
            return norm_a == norm_b

        # Fuzzy: normalized match OR alias match
        if norm_a == norm_b:
            return True
        for _canonical, aliases in config.symbol_aliases.items():
            alias_set = {self.normalize_symbol(a) for a in aliases}
            alias_set.add(self.normalize_symbol(_canonical))
            if norm_a in alias_set and norm_b in alias_set:
                return True
        return False
