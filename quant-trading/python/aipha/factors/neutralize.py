"""因子中性化适配器

将 :class:`aipha.models.feature_eng.Neutralizer`（DataFrame 接口）
桥接到因子模块的 Panel 数据格式（``{date: {symbol: value}}``），
使因子流水线可以便捷地做市值/行业中性化。

使用示例::

    result = neutralize_factor_panel(
        factor_panel={"2024-01-01": {"000001.SZ": 0.5, ...}, ...},
        market_cap_panel={"2024-01-01": {"000001.SZ": 1e9, ...}, ...},
        industry_panel={"2024-01-01": {"000001.SZ": "银行", ...}, ...},
    )

    # 多因子批量中性化
    results = neutralize_multi_factors(
        factor_panels={"pe": pe_panel, "momentum": mom_panel},
        market_cap_panel=cap_panel,
        industry_panel=ind_panel,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 复用 models 层的 Neutralizer 实现
from aipha.models.feature_eng import Neutralizer

# 因子面板类型
FactorPanel = dict[date, dict[str, float]]
IndustryPanel = dict[date, dict[str, str]]


# ============================================================================
# 配置
# ============================================================================


@dataclass
class NeutralizationConfig:
    """中性化配置

    Attributes:
        neutralize_market: 是否做市值中性化
        neutralize_industry: 是否做行业中性化
        min_samples: 每日最少有效标的数（不足则跳过该日）
    """

    neutralize_market: bool = True
    neutralize_industry: bool = True
    min_samples: int = 10


# ============================================================================
# 单因子中性化
# ============================================================================


def neutralize_factor_panel(
    factor_panel: FactorPanel,
    *,
    market_cap_panel: FactorPanel | None = None,
    industry_panel: IndustryPanel | None = None,
    config: NeutralizationConfig | None = None,
) -> FactorPanel:
    """对因子面板做市值/行业中性化

    按交易日逐日中性化：对每日截面数据构建 OLS 回归（自变量 = 对数市值 +
    行业哑变量），取残差作为中性化后的因子值。

    Args:
        factor_panel: 原始因子面板 ``{date: {symbol: value}}``
        market_cap_panel: 市值面板（market 中性化时必须提供）
        industry_panel: 行业面板 ``{date: {symbol: industry_name}}``
        config: 中性化配置

    Returns:
        中性化后的因子面板

    Raises:
        ValueError: 需要市值中性化但未提供 market_cap_panel
    """
    if config is None:
        config = NeutralizationConfig()

    if config.neutralize_market and market_cap_panel is None:
        raise ValueError(
            "neutralize_market=True 但未提供 market_cap_panel"
        )

    if config.neutralize_industry and industry_panel is None:
        raise ValueError(
            "neutralize_industry=True 但未提供 industry_panel"
        )

    result: FactorPanel = {}
    factor_col = "_factor"

    for d, day_factors in sorted(factor_panel.items()):
        symbols = sorted(day_factors.keys())
        if len(symbols) < config.min_samples:
            result[d] = dict(day_factors)  # 保留原值
            continue

        # 构建 DataFrame
        rows: list[dict[str, Any]] = []
        for s in symbols:
            row: dict[str, Any] = {"symbol": s, factor_col: day_factors[s]}

            if config.neutralize_market and market_cap_panel is not None:
                cap = market_cap_panel.get(d, {}).get(s)
                row["market_cap"] = cap if cap is not None else np.nan

            if config.neutralize_industry and industry_panel is not None:
                ind = industry_panel.get(d, {}).get(s)
                row["industry"] = ind if ind is not None else "UNKNOWN"

            rows.append(row)

        df = pd.DataFrame(rows)

        # 过滤 NaN factor
        valid_mask = df[factor_col].notna()
        if valid_mask.sum() < config.min_samples:
            result[d] = dict(day_factors)
            continue

        # 调用 Neutralizer
        neutralizer = Neutralizer(
            feature_cols=[factor_col],
            neutralize_market=config.neutralize_market,
            neutralize_industry=config.neutralize_industry,
        )
        df_out = neutralizer.transform(df)

        # 回写到 panel
        day_result: dict[str, float] = {}
        for _, row_data in df_out.iterrows():
            sym = row_data["symbol"]
            val = row_data[factor_col]
            day_result[sym] = float(val) if pd.notna(val) else day_factors.get(sym, 0.0)
        result[d] = day_result

    return result


# ============================================================================
# 多因子批量中性化
# ============================================================================


def neutralize_multi_factors(
    factor_panels: dict[str, FactorPanel],
    *,
    market_cap_panel: FactorPanel | None = None,
    industry_panel: IndustryPanel | None = None,
    config: NeutralizationConfig | None = None,
) -> dict[str, FactorPanel]:
    """批量中性化多个因子面板

    Args:
        factor_panels: ``{factor_name: factor_panel}``
        market_cap_panel: 市值面板
        industry_panel: 行业面板
        config: 中性化配置

    Returns:
        ``{factor_name: neutralized_panel}``
    """
    results: dict[str, FactorPanel] = {}
    for name, panel in factor_panels.items():
        results[name] = neutralize_factor_panel(
            panel,
            market_cap_panel=market_cap_panel,
            industry_panel=industry_panel,
            config=config,
        )
    return results
