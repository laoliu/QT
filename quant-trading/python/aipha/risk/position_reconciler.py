"""持仓对账器 — 网关 vs 本地持仓一致性校验"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    matched: list[str] = field(default_factory=list)
    local_only: dict[str, int] = field(default_factory=dict)
    remote_only: dict[str, int] = field(default_factory=dict)
    quantity_mismatch: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def is_consistent(self) -> bool:
        return not self.local_only and not self.remote_only and not self.quantity_mismatch

    def summary(self) -> str:
        if self.is_consistent:
            return f"持仓一致: {len(self.matched)} 个标的"
        lines = [f"持仓不一致:"]
        if self.local_only:
            lines.append(f"  本地多余: {self.local_only}")
        if self.remote_only:
            lines.append(f"  远端多余: {self.remote_only}")
        if self.quantity_mismatch:
            for s, (local, remote) in self.quantity_mismatch.items():
                lines.append(f"  {s}: 本地={local} 远端={remote}")
        return "\n".join(lines)


class PositionReconciler:
    """持仓对账器"""

    def reconcile(
        self,
        local_positions: dict[str, int],
        remote_positions: dict[str, int],
    ) -> ReconcileResult:
        result = ReconcileResult()
        all_symbols = set(local_positions) | set(remote_positions)

        for sym in all_symbols:
            local_qty = local_positions.get(sym, 0)
            remote_qty = remote_positions.get(sym, 0)

            if local_qty == remote_qty:
                if local_qty > 0:
                    result.matched.append(sym)
            elif local_qty > 0 and remote_qty == 0:
                result.local_only[sym] = local_qty
            elif local_qty == 0 and remote_qty > 0:
                result.remote_only[sym] = remote_qty
            else:
                result.quantity_mismatch[sym] = (local_qty, remote_qty)

        if not result.is_consistent:
            logger.warning("持仓对账异常: %s", result.summary())
        else:
            logger.info("持仓对账通过: %d 个标的", len(result.matched))

        return result

    def auto_fix(
        self,
        local_positions: dict[str, int],
        remote_positions: dict[str, int],
        trust: str = "remote",
    ) -> dict[str, int]:
        """根据信任源自动修正持仓"""
        if trust == "remote":
            return dict(remote_positions)
        elif trust == "local":
            return dict(local_positions)
        else:
            # 取交集 + 取小值
            result = {}
            for sym in set(local_positions) & set(remote_positions):
                result[sym] = min(local_positions[sym], remote_positions[sym])
            return result
