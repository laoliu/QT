"""特征集定义 — 标准化分组便于因子选择和消融实验。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeatureGroup:
    name: str
    features: tuple[str, ...]
    description: str = ""


QUALITY = FeatureGroup(
    name="quality",
    features=("roe", "roa", "gross_margin", "net_margin", "current_ratio",
              "debt_to_equity", "interest_coverage", "asset_turnover"),
    description="质量因子：盈利能力 + 偿债能力",
)

VALUATION = FeatureGroup(
    name="valuation",
    features=("pe", "pb", "ps", "pcf", "ev_ebitda", "dividend_yield",
              "earnings_yield", "fcf_yield"),
    description="估值因子",
)

LIQUIDITY = FeatureGroup(
    name="liquidity",
    features=("turnover_rate", "avg_volume_20d", "avg_amount_20d",
              "bid_ask_spread", "amihud_illiquidity"),
    description="流动性因子",
)

MOMENTUM = FeatureGroup(
    name="momentum",
    features=("momentum_5d", "momentum_10d", "momentum_20d", "momentum_60d",
              "momentum_120d", "momentum_250d", "residual_momentum",
              "52w_high_pct", "rs_relative_strength"),
    description="动量因子",
)

VOLATILITY_GROUP = FeatureGroup(
    name="volatility",
    features=("volatility_20d", "volatility_60d", "realized_vol_252d",
              "downside_vol", "beta_market", "idiosyncratic_vol",
              "max_dd_60d", "skewness", "kurtosis"),
    description="波动率/风险因子",
)

TECHNICAL = FeatureGroup(
    name="technical",
    features=("rsi_14", "macd_signal", "ma5_vs_ma20", "ma20_vs_ma60",
              "bollinger_pct", "atr_14", "obv_slope", "volume_ratio_5_20"),
    description="技术因子",
)

SIZE = FeatureGroup(
    name="size",
    features=("market_cap", "log_market_cap", "float_market_cap",
              "total_shares", "float_shares"),
    description="市值/规模因子",
)

ALTERNATIVE = FeatureGroup(
    name="alternative",
    features=("analyst_rating_mean", "analyst_target_pct", "analyst_coverage",
              "northbound_holding_pct", "northbound_flow_20d",
              "fund_holding_pct", "social_sentiment_score"),
    description="另类因子",
)

ALL_GROUPS = [QUALITY, VALUATION, LIQUIDITY, MOMENTUM, VOLATILITY_GROUP, TECHNICAL, SIZE, ALTERNATIVE]


def get_feature_group(name: str) -> FeatureGroup | None:
    for g in ALL_GROUPS:
        if g.name == name:
            return g
    return None


def get_all_features() -> list[str]:
    """返回所有特征名（去重、保持顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for g in ALL_GROUPS:
        for f in g.features:
            if f not in seen:
                seen.add(f)
                result.append(f)
    return result


def get_features_by_groups(group_names: list[str]) -> list[str]:
    """按分组名获取特征列表。"""
    result: list[str] = []
    for name in group_names:
        g = get_feature_group(name)
        if g:
            result.extend(g.features)
    return result
