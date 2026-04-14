"""因子中性化 — 市值 / 行业截面回归去偏"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FactorPanel = dict[date, dict[str, float]]
IndustryPanel = dict[date, dict[str, str]]


@dataclass
class NeutralizationConfig:
    neutralize_market: bool = True
    neutralize_industry: bool = True
    min_samples: int = 10


def _neutralize_cross_section(
    factor_vals: dict[str, float],
    *,
    market_caps: dict[str, float] | None = None,
    industries: dict[str, str] | None = None,
    min_samples: int = 10,
) -> dict[str, float]:
    """单截面 OLS residual 中性化"""
    symbols = list(factor_vals.keys())
    if len(symbols) < min_samples:
        return factor_vals

    y = np.array([factor_vals[s] for s in symbols], dtype=float)
    mask = np.isfinite(y)
    if mask.sum() < min_samples:
        return factor_vals

    cols: list[np.ndarray] = [np.ones(len(symbols))]  # intercept

    if market_caps is not None:
        mc = np.array([np.log(max(1, market_caps.get(s, 1))) for s in symbols])
        cols.append(mc)

    if industries is not None:
        unique_ind = sorted({industries.get(s, "other") for s in symbols})
        if len(unique_ind) > 1:
            ind_map = {v: i for i, v in enumerate(unique_ind[1:])}  # drop first
            for ind in unique_ind[1:]:
                dummy = np.array([1.0 if industries.get(s, "other") == ind else 0.0
                                  for s in symbols])
                cols.append(dummy)

    X = np.column_stack(cols)
    valid = mask
    try:
        beta, *_ = np.linalg.lstsq(X[valid], y[valid], rcond=None)
        residuals = y - X @ beta
    except np.linalg.LinAlgError:
        return factor_vals

    return {s: float(residuals[i]) for i, s in enumerate(symbols)}


def neutralize_factor_panel(
    factor_panel: FactorPanel,
    *,
    market_cap_panel: FactorPanel | None = None,
    industry_panel: IndustryPanel | None = None,
    config: NeutralizationConfig | None = None,
) -> FactorPanel:
    """逐日截面中性化因子面板"""
    cfg = config or NeutralizationConfig()
    result: FactorPanel = {}
    for dt, vals in factor_panel.items():
        mc = market_cap_panel.get(dt) if cfg.neutralize_market and market_cap_panel else None
        ind = industry_panel.get(dt) if cfg.neutralize_industry and industry_panel else None
        result[dt] = _neutralize_cross_section(vals, market_caps=mc, industries=ind,
                                                min_samples=cfg.min_samples)
    return result


def neutralize_multi_factors(
    factor_panels: dict[str, FactorPanel],
    *,
    market_cap_panel: FactorPanel | None = None,
    industry_panel: IndustryPanel | None = None,
    config: NeutralizationConfig | None = None,
) -> dict[str, FactorPanel]:
    """对多个因子面板分别执行中性化"""
    return {
        name: neutralize_factor_panel(
            panel, market_cap_panel=market_cap_panel,
            industry_panel=industry_panel, config=config,
        )
        for name, panel in factor_panels.items()
    }
