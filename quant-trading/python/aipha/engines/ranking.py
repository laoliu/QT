"""
Multi-factor ranking system and stock ranking tools.

Migrated from jtstock/stock_tools/rank_tool.py, stock_RPS.py,
stock_ranking.py, and stock_lowmartket_ranking.py, modernized for Python 3.

Architecture:
  RankObject   — an entity with named numeric attributes and per-attribute ranks
  RankGroup    — a collection of RankObjects that computes composite rankings
  StockRanker  — high-level API for RPS, fluctuation, and market-cap ranking
"""

from __future__ import annotations

import math
from typing import Optional


# ── Core Ranking Primitives ───────────────────────────────────────


class RankObject:
    """An item (typically a stock) with named attributes and their rankings.

    Migrated from jtstock/stock_tools/rank_tool.py.
    """

    def __init__(self, item_id: str = ""):
        self.id = item_id
        self.attrs: dict[str, float] = {}
        self.attr_rank: dict[str, int] = {}

    def set_attr(self, name: str, value: float) -> None:
        """Set a factor attribute value."""
        self.attrs[name] = value
        self.attr_rank[name] = 999999

    def set_rank(self, name: str, rank: int) -> None:
        self.attr_rank[name] = rank

    def get_rank(self, name: str) -> int:
        return self.attr_rank.get(name, 999999)

    def get_attr(self, name: str) -> Optional[float]:
        return self.attrs.get(name)

    def total_rank(self) -> int:
        """Sum of all attribute ranks (lower = better)."""
        return sum(self.attr_rank.values())

    def __repr__(self) -> str:
        parts = [f"id={self.id} rank={self.total_rank()}"]
        for k, v in self.attrs.items():
            parts.append(f"{k}={v:.4f}/R{self.get_rank(k)}")
        return " ".join(parts)


class RankGroup:
    """A time-indexed collection of RankObject sets with automatic ranking.

    Migrated from jtstock/stock_tools/rank_tool.py.
    """

    def __init__(self):
        self._groups: dict[int, dict[str, RankObject]] = {}
        self._rank_types: dict[str, bool] = {}  # name → sort_descending

    def set_rank_type(self, name: str, descending: bool = True) -> None:
        """Register a factor for ranking. descending=True → higher values get rank 1."""
        self._rank_types[name] = descending

    def add_group(self, group_key: int, objects: dict[str, RankObject]) -> None:
        """Add a set of RankObjects for a given time key and compute ranks."""
        self._compute_ranks(objects)
        self._groups[group_key] = objects

    def get_group(self, group_key: int) -> Optional[dict[str, RankObject]]:
        return self._groups.get(group_key)

    def get_group_size(self, group_key: int) -> int:
        group = self.get_group(group_key)
        return len(group) if group else 0

    def get_top_n(self, group_key: int, n: int) -> list[str]:
        """Get top N item IDs sorted by composite rank (lower rank = better)."""
        group = self.get_group(group_key)
        if not group:
            return []
        sorted_items = sorted(group.values(), key=lambda o: o.total_rank())
        return [o.id for o in sorted_items[:n]]

    def get_slice(self, group_key: int, slice_count: int, slice_index: int) -> list[str]:
        """Divide ranked items into `slice_count` equal slices and return `slice_index`-th."""
        group = self.get_group(group_key)
        if not group:
            return []
        sorted_items = sorted(group.values(), key=lambda o: o.total_rank())
        total = len(sorted_items)
        per_slice = total // slice_count
        start = slice_index * per_slice
        end = start + per_slice
        return [o.id for o in sorted_items[start:end]]

    def get_ranking(self, group_key: int, item_id: str, attr_name: str) -> Optional[int]:
        """Get the rank of a specific item for a specific attribute."""
        group = self.get_group(group_key)
        if not group or item_id not in group:
            return None
        return group[item_id].get_rank(attr_name)

    def get_ids_in_range(self, group_key: int, attr_name: str, min_rank: int, max_rank: int) -> list[str]:
        """Get item IDs whose rank for `attr_name` falls within [min_rank, max_rank]."""
        group = self.get_group(group_key)
        if not group:
            return []
        return sorted([
            obj.id
            for obj in group.values()
            if min_rank <= obj.get_rank(attr_name) <= max_rank
        ])

    def _compute_ranks(self, objects: dict[str, RankObject]) -> None:
        """Compute per-attribute ranks for all objects."""
        if not objects:
            return
        for attr_name, descending in self._rank_types.items():
            sortable = [
                obj for obj in objects.values()
                if attr_name in obj.attrs
            ]
            sortable.sort(key=lambda o: o.attrs[attr_name], reverse=descending)
            for i, obj in enumerate(sortable):
                obj.set_rank(attr_name, i + 1)

    def remove_group(self, group_key: int) -> None:
        self._groups.pop(group_key, None)


# ── Stock RPS (Relative Price Strength) ──────────────────────────


def compute_rps_ranking(
    stock_prices: dict[str, dict[str, float]],
    current_date: int,
    lookback_date: int,
) -> dict[str, float]:
    """Compute Relative Price Strength for all stocks.

    Args:
      stock_prices: { stock_id: { "current": price, "lookback": price } }
      current_date: Current date (YYYYMMDD), used for logging.
      lookback_date: Date from which growth is measured.

    Returns:
      { stock_id: growth_ratio } sorted by growth descending.
    """
    growth: dict[str, float] = {}
    for stock_id, prices in stock_prices.items():
        cur = prices.get("current", 0)
        prev = prices.get("lookback", 0)
        if prev and prev > 0 and cur and cur > 0:
            growth[stock_id] = (cur - prev) / prev
    return dict(sorted(growth.items(), key=lambda x: x[1], reverse=True))


def get_rps_percentile(
    rps_ranking: dict[str, float], stock_id: str, total_groups: int = 100
) -> Optional[int]:
    """Get the RPS percentile group (1 = strongest, 100 = weakest).

    Args:
      rps_ranking: output of compute_rps_ranking()
      stock_id: the stock to look up
      total_groups: number of percentile groups (default 100)

    Returns:
      Percentile group number, or None if stock not found.
    """
    ids = list(rps_ranking.keys())
    if stock_id not in ids:
        return None
    idx = ids.index(stock_id)
    total = len(ids)
    if total == 0:
        return None
    return int(idx / total * total_groups) + 1


# ── Price Fluctuation Ranking ────────────────────────────────────


def compute_fluctuation_ranking(
    stock_prices: dict[str, dict[str, float]],
    absolute: bool = False,
) -> dict[str, float]:
    """Rank stocks by price fluctuation (change ratio).

    Args:
      stock_prices: { stock_id: { "current": price, "lookback": price } }
      absolute: if True, use absolute value of fluctuation.

    Returns:
      { stock_id: fluctuation } sorted by fluctuation descending.
    """
    fluctuations: dict[str, float] = {}
    for stock_id, prices in stock_prices.items():
        cur = prices.get("current", 0)
        prev = prices.get("lookback", 0)
        if prev and prev > 0 and cur and cur > 0:
            change = (cur - prev) / prev
            if absolute:
                change = abs(change)
            fluctuations[stock_id] = change
    return dict(sorted(fluctuations.items(), key=lambda x: x[1], reverse=True))


def get_fluctuation_slice(
    fluctuations: dict[str, float], begin_ratio: float, end_ratio: float
) -> list[str]:
    """Get stocks whose fluctuation ranking falls within [begin_ratio, end_ratio].

    Ratios are 0.0 to 1.0 (0.0 = highest fluctuation, 1.0 = lowest).
    """
    ids = list(fluctuations.keys())
    total = len(ids)
    if total == 0:
        return []
    start = int(round(total * begin_ratio))
    end = int(round(total * end_ratio))
    return ids[start:end]


# ── Low Market Cap Ranking ───────────────────────────────────────


def compute_market_cap_ranking(
    stocks: dict[str, dict[str, float]],
    top_n: int = 10,
) -> tuple[float, list[str], dict[str, float]]:
    """Rank stocks by market capitalisation (ascending = lowest first).

    Args:
      stocks: { stock_id: { "price": float, "total_capital": float } }
      top_n: number of stocks to select

    Returns:
      (average_market_cap, selected_ids, weights)
      weights are proportional: weight[id] = market_cap[id] / sum_of_top_n_caps
    """
    ranking_group = RankGroup()
    ranking_group.set_rank_type("market_value", descending=False)  # ascending

    objects: dict[str, RankObject] = {}
    for stock_id, info in stocks.items():
        price = info.get("price", 0)
        total_cap = info.get("total_capital", 0)
        if not price or price <= 0 or not total_cap:
            continue
        market_cap = round(price * total_cap / 1e6, 2)  # in millions
        obj = RankObject(stock_id)
        obj.set_attr("price", price)
        obj.set_attr("total_capital", total_cap)
        obj.set_attr("market_value", market_cap)
        objects[stock_id] = obj

    ranking_group.add_group(0, objects)
    selected_ids = ranking_group.get_ids_in_range(0, "market_value", 1, top_n)

    # compute weights
    group = ranking_group.get_group(0)
    if not group:
        return 0.0, [], {}

    total_cap_sum = sum(group[sid].attrs["market_value"] for sid in selected_ids if sid in group)
    weights: dict[str, float] = {}
    for sid in selected_ids:
        if sid in group and total_cap_sum > 0:
            weights[sid] = round(group[sid].attrs["market_value"] / total_cap_sum, 4)

    avg_cap = round(total_cap_sum / max(len(selected_ids), 1), 2)
    return avg_cap, selected_ids, weights


# ── Multi-Factor Composite Ranking ───────────────────────────────


def multi_factor_rank(
    stocks: dict[str, dict[str, float]],
    factors: dict[str, bool],
    top_n: int = 20,
) -> list[dict]:
    """Rank stocks by multiple factors and return the top N.

    Args:
      stocks: { stock_id: { factor_name: value, ... } }
      factors: { factor_name: descending_bool }
        True = higher is better (e.g. ROE);
        False = lower is better (e.g. PE).
      top_n: number of stocks to return.

    Returns:
      List of { id, total_rank, **factor_values }.
    """
    group = RankGroup()
    objects: dict[str, RankObject] = {}

    for stock_id, data in stocks.items():
        obj = RankObject(stock_id)
        for factor_name in factors:
            if factor_name in data and data[factor_name] is not None:
                obj.set_attr(factor_name, data[factor_name])
        objects[stock_id] = obj

    for factor_name, desc in factors.items():
        group.set_rank_type(factor_name, desc)

    group.add_group(0, objects)
    top_ids = group.get_top_n(0, top_n)

    result = []
    group_data = group.get_group(0)
    if not group_data:
        return result

    for sid in top_ids:
        obj = group_data[sid]
        item: dict = {"id": sid, "total_rank": obj.total_rank()}
        for k, v in obj.attrs.items():
            item[k] = v
            item[f"{k}_rank"] = obj.get_rank(k)
        result.append(item)
    return result
