"""Versioned column-to-group policy for canonical equity models."""
from __future__ import annotations

from functools import lru_cache
from typing import Mapping, Sequence

import pandas as pd


# A policy revision also invalidates models fitted with older PIT feature
# semantics, even when the public feature-set name and columns are unchanged.
FEATURE_POLICY_VERSION = "cn_equity_feature_groups/2"
FEATURE_GROUPS = ("value", "quality", "momentum", "volatility", "liquidity", "technical")
_RESERVED = {
    "datetime", "trade_date", "symbol", "open", "high", "low", "close", "volume", "amount",
    "vol", "pre_close", "change", "pct_chg", "adj_factor", "up_limit", "down_limit",
    "is_suspended", "is_limit_up", "is_limit_down", "is_index_member", "is_st", "listing_days",
    "listing_days_basis", "list_date", "benchmark_close", "benchmark_return", "score", "rank",
    "model_id", "run_id", "signal_id", "feature_version", "feature_set", "industry", "sector", "sw_industry",
    "fwd_return", "fwd_return_rank", "fwd_return_raw", "label", "corporate_actions_json",
}


def normalize_feature_groups(groups: Sequence[str]) -> tuple[str, ...]:
    if isinstance(groups, str) or not isinstance(groups, (list, tuple)) or not groups:
        raise ValueError("feature_groups must be a non-empty list of canonical groups")
    if not all(isinstance(group, str) and group in FEATURE_GROUPS for group in groups):
        raise ValueError("unknown feature_groups; use value, quality, momentum, volatility, liquidity or technical")
    if len(set(groups)) != len(groups):
        raise ValueError("feature_groups cannot contain duplicates")
    return tuple(group for group in FEATURE_GROUPS if group in groups)


@lru_cache(maxsize=1)
def _builtin_columns() -> dict[str, str]:
    from aipha.factors.qlib_alpha import Alpha158

    result = {f"factor_{metric}": "value" for metric in ("pe_ttm", "pb", "dv_ttm")}
    result.update({f"factor_{metric}": "quality" for metric in ("roe", "roa", "grossprofit_margin", "revenue_yoy")})
    result.update({f"momentum_{window}": "momentum" for window in (5, 20, 60, 120)})
    result.update({"reversal_5": "momentum", "volatility_20": "volatility",
                   "average_turnover_value_20": "liquidity", "log_market_cap": "liquidity"})
    result.update({name: "technical" for name in Alpha158().factor_names})
    return result


def normalize_column_groups(value: Mapping[str, str] | tuple = ()) -> tuple[tuple[str, str], ...]:
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, (tuple, list)):
        items = list(value)
    else:
        raise ValueError("feature column_groups must be a mapping of column names to groups")
    result = {}
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("feature column_groups must contain column/group pairs")
        name, group = item
        if not isinstance(name, str) or not name or name.strip() != name or not isinstance(group, str):
            raise ValueError("feature column_groups requires non-empty column names and canonical groups")
        if (name in _RESERVED or name.endswith("_raw") or name.startswith(("fwd_", "label_", "target_"))
                or group not in FEATURE_GROUPS):
            raise ValueError(f"invalid feature column_groups entry: {name!r} -> {group!r}")
        if name in result:
            raise ValueError(f"duplicate feature column_groups entry: {name}")
        if name in _builtin_columns() and _builtin_columns()[name] != group:
            raise ValueError(f"cannot reclassify registered feature: {name}")
        result[name] = group
    return tuple(sorted(result.items()))


def feature_catalog(column_groups=()) -> dict[str, str]:
    return {**_builtin_columns(), **dict(normalize_column_groups(column_groups))}


def select_feature_columns(frame: pd.DataFrame, groups=FEATURE_GROUPS, column_groups=()) -> tuple[list[str], dict]:
    active = normalize_feature_groups(groups)
    catalog = feature_catalog(column_groups)
    selected = sorted(name for name, group in catalog.items() if group in active and name in frame)
    non_numeric = [name for name in selected if not pd.api.types.is_numeric_dtype(frame[name])]
    if non_numeric:
        raise ValueError(f"model features must be numeric: {non_numeric}")
    if not selected:
        raise ValueError("no registered numeric feature columns found for configured feature_groups")
    by_group = {group: [name for name in selected if catalog[name] == group] for group in active}
    return selected, {"policy_version": FEATURE_POLICY_VERSION, "groups": list(active), "by_group": by_group,
                      "unavailable_groups": [group for group, columns in by_group.items() if not columns],
                      "column_groups": {name: catalog[name] for name in selected}}
