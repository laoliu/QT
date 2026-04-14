"""Portfolio diff engine — compares two snapshots to identify changes."""

from __future__ import annotations

import logging
from decimal import Decimal

from .models import (
    ChangeType,
    HoldingChange,
    PortfolioDiff,
    PortfolioSnapshot,
)

logger = logging.getLogger(__name__)

# Minimum weight change (absolute) to consider as INCREASED/DECREASED
# vs. noise/rounding. Default 0.5% = 0.005.
DEFAULT_WEIGHT_THRESHOLD = Decimal("0.005")


def compute_diff(
    old: PortfolioSnapshot,
    new: PortfolioSnapshot,
    weight_threshold: Decimal = DEFAULT_WEIGHT_THRESHOLD,
) -> PortfolioDiff:
    """Compute the diff between two portfolio snapshots.

    Args:
        old: Earlier snapshot.
        new: Later snapshot.
        weight_threshold: Minimum absolute weight change to count as
            INCREASED / DECREASED (vs UNCHANGED). Default 0.5%.

    Returns:
        PortfolioDiff with all holding changes.

    Raises:
        ValueError: If snapshots are from different authors.
    """
    if old.author_id != new.author_id:
        raise ValueError(
            f"Cannot diff snapshots from different authors: "
            f"{old.author_id} vs {new.author_id}"
        )

    old_map = {h.symbol: h for h in old.holdings}
    new_map = {h.symbol: h for h in new.holdings}

    all_symbols = set(old_map.keys()) | set(new_map.keys())

    changes: list[HoldingChange] = []

    for symbol in sorted(all_symbols):
        old_h = old_map.get(symbol)
        new_h = new_map.get(symbol)

        if old_h is None and new_h is not None:
            # New position
            changes.append(
                HoldingChange(
                    symbol=symbol,
                    name=new_h.name,
                    change_type=ChangeType.ADDED,
                    old_weight=None,
                    new_weight=new_h.weight,
                    weight_delta=new_h.weight,
                )
            )
        elif old_h is not None and new_h is None:
            # Removed position
            changes.append(
                HoldingChange(
                    symbol=symbol,
                    name=old_h.name,
                    change_type=ChangeType.REMOVED,
                    old_weight=old_h.weight,
                    new_weight=None,
                    weight_delta=-old_h.weight,
                )
            )
        elif old_h is not None and new_h is not None:
            # Existing position — check weight change
            delta = new_h.weight - old_h.weight
            if abs(delta) >= weight_threshold:
                if delta > 0:
                    change_type = ChangeType.INCREASED
                else:
                    change_type = ChangeType.DECREASED
            else:
                change_type = ChangeType.UNCHANGED

            changes.append(
                HoldingChange(
                    symbol=symbol,
                    name=new_h.name,
                    change_type=change_type,
                    old_weight=old_h.weight,
                    new_weight=new_h.weight,
                    weight_delta=delta,
                )
            )

    diff = PortfolioDiff(
        author_id=old.author_id,
        author_name=old.author_name,
        from_snapshot_id=old.snapshot_id,
        to_snapshot_id=new.snapshot_id,
        from_time=old.snapshot_time,
        to_time=new.snapshot_time,
        changes=tuple(changes),
    )

    if diff.has_changes:
        logger.info(
            "Diff for %s: +%d -%d ↑%d ↓%d",
            diff.author_name,
            len(diff.added),
            len(diff.removed),
            len(diff.increased),
            len(diff.decreased),
        )
    else:
        logger.debug("No changes for %s", diff.author_name)

    return diff
