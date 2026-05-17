"""Multi-factor ranking utilities.

Provides RankObject/RankGroup for per-attribute ranking, relative
price strength (RPS), market-cap ranking, fluctuation ranking.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "RankObject", "RankGroup",
    "compute_rps_ranking", "get_rps_percentile",
    "multi_factor_rank", "compute_market_cap_ranking",
]


class RankObject:
    """An entity with named numeric attributes and per-attr ranks."""

    def __init__(self, item_id: str, name: str = "") -> None:
        self.item_id = item_id
        self.name = name
        self._attrs: Dict[str, float] = {}
        self._ranks: Dict[str, int] = {}

    def set_attr(self, key: str, value: float) -> None:
        self._attrs[key] = value

    def get_attr(self, key: str, default: float = 0.0) -> float:
        return self._attrs.get(key, default)

    def set_rank(self, key: str, rank: int) -> None:
        self._ranks[key] = rank

    def get_rank(self, key: str, default: int = 0) -> int:
        return self._ranks.get(key, default)

    def total_rank(self, weights: Optional[Dict[str, float]] = None) -> float:
        if not self._ranks:
            return 0.0
        if weights:
            return sum(self._ranks.get(k, 0) * w for k, w in weights.items())
        return sum(self._ranks.values())


class RankGroup:
    """Manages groups of RankObjects with automatic ranking."""

    def __init__(self) -> None:
        self._groups: Dict[str, List[RankObject]] = {}
        self._rank_types: Dict[str, bool] = {}  # name -> descending

    def set_rank_type(self, name: str, descending: bool = True) -> None:
        self._rank_types[name] = descending

    def add_group(self, key: str, objects: List[RankObject]) -> None:
        self._groups[key] = objects
        self._apply_rankings(key)

    def get_top_n(self, key: str, n: int) -> List[RankObject]:
        objs = self._groups.get(key, [])
        sorted_objs = sorted(objs, key=lambda o: o.total_rank())
        return sorted_objs[:n]

    def get_ranking(
        self, key: str, item_id: str, attr_name: str,
    ) -> Optional[int]:
        for obj in self._groups.get(key, []):
            if obj.item_id == item_id:
                return obj.get_rank(attr_name)
        return None

    def _apply_rankings(self, key: str) -> None:
        objs = self._groups.get(key, [])
        for attr, desc in self._rank_types.items():
            valid = [(o, o.get_attr(attr)) for o in objs if attr in o._attrs]
            valid.sort(key=lambda x: x[1], reverse=desc)
            for rank, (obj, _) in enumerate(valid, 1):
                obj.set_rank(attr, rank)


def compute_rps_ranking(
    stock_prices: Dict[str, Tuple[float, float]],
    *,
    descending: bool = True,
) -> Dict[str, float]:
    """Compute Relative Price Strength ranking.

    Parameters
    ----------
    stock_prices : dict
        stock_id -> (current_price, lookback_price).

    Returns
    -------
    dict of stock_id -> RPS value (percent change), ranked.
    """
    rps: Dict[str, float] = {}
    for sid, (cur, ref) in stock_prices.items():
        if ref > 0:
            rps[sid] = (cur / ref - 1) * 100
        else:
            rps[sid] = 0.0
    return dict(sorted(rps.items(), key=lambda x: x[1], reverse=descending))


def get_rps_percentile(
    rps_ranking: Dict[str, float],
    stock_id: str,
    total_groups: int = 100,
) -> Optional[int]:
    """Get RPS percentile (1-100) for a stock."""
    keys = list(rps_ranking.keys())
    if stock_id not in keys:
        return None
    idx = keys.index(stock_id)
    return max(1, int((1 - idx / len(keys)) * total_groups)) if keys else None


def compute_market_cap_ranking(
    stocks: List[Dict[str, Any]], top_n: int = 10,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rank stocks by market cap: (large_cap_top_n, small_cap_top_n)."""
    sorted_stocks = sorted(
        stocks, key=lambda s: s.get("market_cap", 0), reverse=True,
    )
    return sorted_stocks[:top_n], sorted_stocks[-top_n:]


def multi_factor_rank(
    stocks: List[Dict[str, Any]],
    factors: Dict[str, Dict[str, Any]],
    top_n: int = 20,
) -> List[Dict[str, Any]]:
    """Composite multi-factor ranking.

    Parameters
    ----------
    stocks : list of dict
        Each must have 'ts_code' and factor value keys.
    factors : dict
        factor_name -> {'weight': float, 'descending': bool}.
    """
    group = RankGroup()
    for fname, cfg in factors.items():
        group.set_rank_type(fname, cfg.get("descending", True))

    objs = []
    for s in stocks:
        obj = RankObject(s.get("ts_code", ""), s.get("name", ""))
        for fname in factors:
            val = s.get(fname, 0)
            if isinstance(val, (int, float)):
                obj.set_attr(fname, float(val))
        objs.append(obj)

    group.add_group("main", objs)

    weights = {f: cfg.get("weight", 1.0) for f, cfg in factors.items()}
    ranked = sorted(objs, key=lambda o: o.total_rank(weights))

    return [
        {
            "ts_code": o.item_id,
            "name": o.name,
            "composite_rank": o.total_rank(weights),
            **{f: o.get_rank(f) for f in factors},
        }
        for o in ranked[:top_n]
    ]
