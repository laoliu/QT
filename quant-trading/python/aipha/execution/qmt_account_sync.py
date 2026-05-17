"""QMT 持仓对账模块 — 内部状态 ↔ QMT 实际持仓同步。

职责:
  1. 从 QMT 查询实际持仓/资金
  2. 与内部 PortfolioState 比对
  3. 输出差异报告 (用于人工确认或自动修复)

注意: 自动修复仅做单向 sync (QMT → 内部)，
      不自动下单修复差异。差异超过阈值须人工介入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PositionRecord:
    """统一持仓记录。"""
    ts_code: str
    shares: int = 0
    avg_cost: float = 0.0
    market_value: float = 0.0
    frozen_shares: int = 0


@dataclass
class AccountRecord:
    """统一账户记录。"""
    broker_name: str = ""
    total_asset: float = 0.0
    cash: float = 0.0
    frozen_cash: float = 0.0
    market_value: float = 0.0


@dataclass
class DiffItem:
    """对账差异项。"""
    ts_code: str
    field: str  # shares / avg_cost / market_value
    internal_value: float = 0.0
    external_value: float = 0.0
    diff: float = 0.0
    severity: str = "info"  # info / warning / critical


@dataclass
class ReconcileResult:
    """对账结果。"""
    broker_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    matched: int = 0
    diffs: list[DiffItem] = field(default_factory=list)
    internal_only: list[str] = field(default_factory=list)  # 内部有但 QMT 无
    external_only: list[str] = field(default_factory=list)  # QMT 有但内部无
    is_clean: bool = True

    @property
    def critical_count(self) -> int:
        return sum(1 for d in self.diffs if d.severity == "critical")


class AccountSyncer:
    """QMT 持仓对账器。"""

    def __init__(
        self,
        share_tolerance: int = 0,
        value_tolerance_pct: float = 0.01,
    ) -> None:
        self._share_tol = share_tolerance
        self._value_tol = value_tolerance_pct

    def reconcile(
        self,
        broker_name: str,
        internal: dict[str, PositionRecord],
        external: dict[str, PositionRecord],
    ) -> ReconcileResult:
        """对比内部与 QMT 持仓。"""
        result = ReconcileResult(broker_name=broker_name)
        all_codes = set(internal) | set(external)
        for code in sorted(all_codes):
            i_pos = internal.get(code)
            e_pos = external.get(code)
            if i_pos and not e_pos:
                result.internal_only.append(code)
                result.is_clean = False
                continue
            if e_pos and not i_pos:
                result.external_only.append(code)
                result.is_clean = False
                continue
            # Both exist — compare
            assert i_pos is not None and e_pos is not None
            self._compare(code, i_pos, e_pos, result)
        result.matched = len(all_codes) - len(result.internal_only) - len(result.external_only) - len(result.diffs)
        logger.info(
            "Reconcile %s: matched=%d diffs=%d internal_only=%d external_only=%d",
            broker_name, result.matched, len(result.diffs),
            len(result.internal_only), len(result.external_only),
        )
        return result

    def _compare(
        self,
        code: str,
        i: PositionRecord,
        e: PositionRecord,
        result: ReconcileResult,
    ) -> None:
        if abs(i.shares - e.shares) > self._share_tol:
            severity = "critical" if abs(i.shares - e.shares) > 100 else "warning"
            result.diffs.append(DiffItem(
                ts_code=code, field="shares",
                internal_value=float(i.shares), external_value=float(e.shares),
                diff=float(i.shares - e.shares), severity=severity,
            ))
            result.is_clean = False
        if e.market_value > 0:
            mv_diff = abs(i.market_value - e.market_value) / e.market_value
            if mv_diff > self._value_tol:
                result.diffs.append(DiffItem(
                    ts_code=code, field="market_value",
                    internal_value=i.market_value, external_value=e.market_value,
                    diff=i.market_value - e.market_value, severity="warning",
                ))
                result.is_clean = False

    def apply_external(
        self,
        internal: dict[str, PositionRecord],
        external: dict[str, PositionRecord],
    ) -> dict[str, PositionRecord]:
        """以 QMT 为准覆盖内部持仓 (单向 sync)。"""
        synced: dict[str, PositionRecord] = {}
        for code, e_pos in external.items():
            synced[code] = PositionRecord(
                ts_code=code,
                shares=e_pos.shares,
                avg_cost=e_pos.avg_cost,
                market_value=e_pos.market_value,
                frozen_shares=e_pos.frozen_shares,
            )
        return synced
