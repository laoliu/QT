"""Core data models for the VIP portfolio tracker.

Design principles:
- All models are immutable (frozen dataclass) where possible
- Serialization via to_dict() / from_dict() for JSON/SQLite storage
- No external dependencies beyond stdlib + decimal
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PortfolioSource(str, Enum):
    """Platform source for portfolio data."""

    XUEQIU = "xueqiu"
    WEIBO = "weibo"
    MANUAL = "manual"


class TradeDirection(str, Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class PlanStatus(str, Enum):
    """Lifecycle status of a trade plan."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class Confidence(str, Enum):
    """Confidence level for a trade plan."""

    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class ChangeType(str, Enum):
    """Type of holding change between snapshots."""

    ADDED = "ADDED"
    REMOVED = "REMOVED"
    INCREASED = "INCREASED"
    DECREASED = "DECREASED"
    UNCHANGED = "UNCHANGED"


# ---------------------------------------------------------------------------
# Holding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Holding:
    """Single holding in a portfolio snapshot.

    Attributes:
        symbol: Stock code, e.g. '600519.SH'
        name: Stock name, e.g. '贵州茅台'
        weight: Portfolio weight (0.0 – 1.0)
        shares: Number of shares held (optional)
        cost: Average cost price (optional)
        note: Free-form note
    """

    symbol: str
    name: str
    weight: Decimal
    shares: int | None = None
    cost: Decimal | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must not be empty")
        if self.weight < 0 or self.weight > 1:
            raise ValueError(f"weight must be 0–1, got {self.weight}")

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "weight": str(self.weight),
            "shares": self.shares,
            "cost": str(self.cost) if self.cost is not None else None,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Holding:
        """从字典反序列化。"""
        return cls(
            symbol=d["symbol"],
            name=d["name"],
            weight=Decimal(d["weight"]),
            shares=d.get("shares"),
            cost=Decimal(d["cost"]) if d.get("cost") is not None else None,
            note=d.get("note", ""),
        )


# ---------------------------------------------------------------------------
# PortfolioSnapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """A point-in-time snapshot of a VIP author's portfolio.

    Attributes:
        snapshot_id: Unique identifier (UUID).
        source: Data source platform.
        author_id: Config-level author identifier.
        author_name: Human-readable author name.
        portfolio_code: Xueqiu portfolio code (ZHxxxxxx), if any.
        snapshot_time: The point in time the holdings represent.
        fetch_time: When the data was fetched.
        holdings: Tuple of Holding objects.
        total_value: Total portfolio value (optional).
        raw_text: Raw post text (if parsed from a post).
    """

    snapshot_id: str
    source: PortfolioSource
    author_id: str
    author_name: str
    snapshot_time: datetime
    fetch_time: datetime
    holdings: tuple[Holding, ...]
    portfolio_code: str | None = None
    total_value: Decimal | None = None
    raw_text: str | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id must not be empty")
        if not self.author_id:
            raise ValueError("author_id must not be empty")

    @staticmethod
    def new_id() -> str:
        """Generate a new unique snapshot ID."""
        return uuid.uuid4().hex[:16]

    @property
    def symbols(self) -> set[str]:
        """Set of symbols in this snapshot."""
        return {h.symbol for h in self.holdings}

    @property
    def weight_map(self) -> dict[str, Decimal]:
        """symbol -> weight mapping."""
        return {h.symbol: h.weight for h in self.holdings}

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "snapshot_id": self.snapshot_id,
            "source": self.source.value,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "portfolio_code": self.portfolio_code,
            "snapshot_time": self.snapshot_time.isoformat(),
            "fetch_time": self.fetch_time.isoformat(),
            "holdings": [h.to_dict() for h in self.holdings],
            "total_value": str(self.total_value) if self.total_value is not None else None,
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortfolioSnapshot:
        """从字典反序列化。"""
        return cls(
            snapshot_id=d["snapshot_id"],
            source=PortfolioSource(d["source"]),
            author_id=d["author_id"],
            author_name=d["author_name"],
            portfolio_code=d.get("portfolio_code"),
            snapshot_time=datetime.fromisoformat(d["snapshot_time"]),
            fetch_time=datetime.fromisoformat(d["fetch_time"]),
            holdings=tuple(Holding.from_dict(h) for h in d["holdings"]),
            total_value=Decimal(d["total_value"]) if d.get("total_value") is not None else None,
            raw_text=d.get("raw_text"),
        )


# ---------------------------------------------------------------------------
# HoldingChange
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HoldingChange:
    """A single holding-level change between two snapshots."""

    symbol: str
    name: str
    change_type: ChangeType
    old_weight: Decimal | None = None
    new_weight: Decimal | None = None
    weight_delta: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "change_type": self.change_type.value,
            "old_weight": str(self.old_weight) if self.old_weight is not None else None,
            "new_weight": str(self.new_weight) if self.new_weight is not None else None,
            "weight_delta": str(self.weight_delta) if self.weight_delta is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HoldingChange:
        """从字典反序列化。"""
        return cls(
            symbol=data["symbol"],
            name=data["name"],
            change_type=ChangeType(data["change_type"]),
            old_weight=Decimal(data["old_weight"]) if data.get("old_weight") is not None else None,
            new_weight=Decimal(data["new_weight"]) if data.get("new_weight") is not None else None,
            weight_delta=Decimal(data["weight_delta"]) if data.get("weight_delta") is not None else None,
        )


# ---------------------------------------------------------------------------
# PortfolioDiff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioDiff:
    """Diff between two portfolio snapshots for the same author.

    Attributes:
        author_id: Config-level author identifier.
        author_name: Human-readable author name.
        from_snapshot_id: Earlier snapshot ID.
        to_snapshot_id: Later snapshot ID.
        from_time: Earlier snapshot time.
        to_time: Later snapshot time.
        changes: List of individual holding changes.
    """

    author_id: str
    author_name: str
    from_snapshot_id: str
    to_snapshot_id: str
    from_time: datetime
    to_time: datetime
    changes: tuple[HoldingChange, ...]

    @property
    def has_changes(self) -> bool:
        """True if there are any non-UNCHANGED entries."""
        return any(c.change_type != ChangeType.UNCHANGED for c in self.changes)

    @property
    def added(self) -> list[HoldingChange]:
        """新增持仓列表。"""
        return [c for c in self.changes if c.change_type == ChangeType.ADDED]

    @property
    def removed(self) -> list[HoldingChange]:
        """清仓持仓列表。"""
        return [c for c in self.changes if c.change_type == ChangeType.REMOVED]

    @property
    def increased(self) -> list[HoldingChange]:
        """加仓持仓列表。"""
        return [c for c in self.changes if c.change_type == ChangeType.INCREASED]

    @property
    def decreased(self) -> list[HoldingChange]:
        """减仓持仓列表。"""
        return [c for c in self.changes if c.change_type == ChangeType.DECREASED]

    def summary(self) -> str:
        """Human-readable summary of the diff."""
        parts: list[str] = []
        parts.append(f"【{self.author_name}】持仓变化 ({self.from_time:%Y-%m-%d} → {self.to_time:%Y-%m-%d})")
        if self.added:
            parts.append(f"  新增: {', '.join(c.name for c in self.added)}")
        if self.removed:
            parts.append(f"  清仓: {', '.join(c.name for c in self.removed)}")
        if self.increased:
            parts.append(f"  加仓: {', '.join(c.name for c in self.increased)}")
        if self.decreased:
            parts.append(f"  减仓: {', '.join(c.name for c in self.decreased)}")
        if not self.has_changes:
            parts.append("  无变化")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "author_id": self.author_id,
            "author_name": self.author_name,
            "from_snapshot_id": self.from_snapshot_id,
            "to_snapshot_id": self.to_snapshot_id,
            "from_time": self.from_time.isoformat(),
            "to_time": self.to_time.isoformat(),
            "changes": [c.to_dict() for c in self.changes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortfolioDiff:
        """从字典反序列化。"""
        return cls(
            author_id=data["author_id"],
            author_name=data["author_name"],
            from_snapshot_id=data["from_snapshot_id"],
            to_snapshot_id=data["to_snapshot_id"],
            from_time=datetime.fromisoformat(data["from_time"]),
            to_time=datetime.fromisoformat(data["to_time"]),
            changes=tuple(HoldingChange.from_dict(c) for c in data.get("changes", [])),
        )


# ---------------------------------------------------------------------------
# TradePlan
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TradePlan:
    """A trade plan — must be filled before executing a trade.

    Key design: force the user to think before trading.
    - reasons must have >= 3 items
    - stop_loss_price is mandatory
    """

    plan_id: str
    symbol: str
    name: str
    direction: TradeDirection
    reasons: list[str]
    stop_loss_price: float
    stop_loss_pct: float
    target_position_pct: float
    confidence: Confidence
    status: PlanStatus = PlanStatus.DRAFT
    vip_references: list[str] = field(default_factory=list)
    max_cost: float | None = None
    target_price: float | None = None
    expected_hold_days: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    executed_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    actual_return_pct: float | None = None

    def __post_init__(self) -> None:
        if len(self.reasons) < 3:
            raise ValueError(
                f"Trade plan requires at least 3 reasons, got {len(self.reasons)}. "
                "This forces you to think before trading."
            )
        if self.stop_loss_price <= 0:
            raise ValueError("stop_loss_price must be > 0")
        if self.stop_loss_pct <= 0 or self.stop_loss_pct >= 1:
            raise ValueError("stop_loss_pct must be between 0 and 1 (exclusive)")

    @staticmethod
    def new_id() -> str:
        """生成唯一交易计划 ID。"""
        return f"TP-{uuid.uuid4().hex[:12]}"

    def approve(self) -> None:
        """Mark plan as approved for execution."""
        if self.status != PlanStatus.DRAFT:
            raise ValueError(f"Can only approve DRAFT plans, current status: {self.status}")
        self.status = PlanStatus.APPROVED

    def execute(self) -> None:
        """Mark plan as executed."""
        if self.status != PlanStatus.APPROVED:
            raise ValueError(f"Can only execute APPROVED plans, current status: {self.status}")
        self.status = PlanStatus.EXECUTED
        self.executed_at = datetime.now()

    def cancel(self, reason: str = "") -> None:
        """Cancel the plan."""
        if self.status in (PlanStatus.EXECUTED,):
            raise ValueError("Cannot cancel an already executed plan")
        self.status = PlanStatus.CANCELLED
        self.close_reason = reason
        self.closed_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "name": self.name,
            "direction": self.direction.value,
            "reasons": self.reasons,
            "vip_references": self.vip_references,
            "target_position_pct": self.target_position_pct,
            "max_cost": self.max_cost,
            "stop_loss_price": self.stop_loss_price,
            "stop_loss_pct": self.stop_loss_pct,
            "target_price": self.target_price,
            "expected_hold_days": self.expected_hold_days,
            "confidence": self.confidence.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_reason": self.close_reason,
            "actual_return_pct": self.actual_return_pct,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TradePlan:
        """从字典反序列化。"""
        return cls(
            plan_id=d["plan_id"],
            symbol=d["symbol"],
            name=d["name"],
            direction=TradeDirection(d["direction"]),
            reasons=d["reasons"],
            vip_references=d.get("vip_references", []),
            target_position_pct=d["target_position_pct"],
            max_cost=d.get("max_cost"),
            stop_loss_price=d["stop_loss_price"],
            stop_loss_pct=d["stop_loss_pct"],
            target_price=d.get("target_price"),
            expected_hold_days=d.get("expected_hold_days"),
            confidence=Confidence(d["confidence"]),
            status=PlanStatus(d["status"]),
            created_at=datetime.fromisoformat(d["created_at"]),
            executed_at=datetime.fromisoformat(d["executed_at"]) if d.get("executed_at") else None,
            closed_at=datetime.fromisoformat(d["closed_at"]) if d.get("closed_at") else None,
            close_reason=d.get("close_reason"),
            actual_return_pct=d.get("actual_return_pct"),
        )


# ---------------------------------------------------------------------------
# VIPAuthor (config model, not persisted in DB)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VIPAuthor:
    """Configuration for a tracked VIP author.

    Loaded from configs/tracker/authors.yml
    """

    author_id: str
    name: str
    platform: PortfolioSource
    platform_uid: str
    portfolio_codes: tuple[str, ...] = ()
    track_posts: bool = True
    focus_assets: tuple[str, ...] = ("stock",)
    check_interval: str = "daily"
    weight: float = 1.0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "author_id": self.author_id,
            "name": self.name,
            "platform": self.platform.value,
            "platform_uid": self.platform_uid,
            "portfolio_codes": list(self.portfolio_codes),
            "track_posts": self.track_posts,
            "focus_assets": list(self.focus_assets),
            "check_interval": self.check_interval,
            "weight": self.weight,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VIPAuthor:
        """从字典反序列化。"""
        return cls(
            author_id=d["author_id"],
            name=d["name"],
            platform=PortfolioSource(d["platform"]),
            platform_uid=d["platform_uid"],
            portfolio_codes=tuple(d.get("portfolio_codes", [])),
            track_posts=d.get("track_posts", True),
            focus_assets=tuple(d.get("focus_assets", ["stock"])),
            check_interval=d.get("check_interval", "daily"),
            weight=d.get("weight", 1.0),
            enabled=d.get("enabled", True),
        )
