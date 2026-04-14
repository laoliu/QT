"""§13.2 脚本侧费用计算共享模块

为 scripts/ 下的回放脚本提供统一的费用/滑点计算接口，
内部委托给 `src/aipha/execution/fees.py` 的权威实现。

解决问题：3+ 个 replay 脚本各自内联费用计算 (§13.2 违规)。

用法::

    from aipha.utils.replay_cost import calc_buy_cost, calc_sell_cost

    cost = calc_buy_cost(price=100.0, shares=1000, date="2024-01-15",
                         volume=500000, asset_type="cn_equity")
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    TimeVaryingStampTaxModel,
    TransferFeeModel,
    CN_EQUITY_STAMP_TAX_SCHEDULE,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond


AssetType = Literal["cn_equity", "convertible_bond"]


def _parse_date(d: str | date | datetime) -> date:
    """将多种日期格式统一为 date 对象"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    # 支持 "2024-01-15" / "20240115" 两种格式
    s = str(d).replace("-", "")
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ---- 预构建费用模型实例（按资产类型缓存） ----
_RULES_CN = MarketRulesCNEquity()
_RULES_CB = MarketRulesConvertibleBond()

_FEE_MODEL_CN = FeeModelCNEquity(
    commission=CommissionModel(
        rate=float(_RULES_CN.commission_rate),
        min_fee=float(_RULES_CN.commission_min),
    ),
    stamp_tax=TimeVaryingStampTaxModel(),
    transfer_fee=TransferFeeModel(rate=float(_RULES_CN.transfer_fee_rate)),
)

_FEE_MODEL_CB = FeeModelCNEquity(
    commission=CommissionModel(
        rate=float(_RULES_CB.commission_rate),
        min_fee=float(_RULES_CB.commission_min),
    ),
    stamp_tax=TimeVaryingStampTaxModel(
        default_rate=0.0, schedule=(),
    ),
    transfer_fee=TransferFeeModel(rate=0.0),
)


def _get_fee_model(asset_type: AssetType) -> FeeModelCNEquity:
    """根据资产类型获取费用模型"""
    if asset_type == "cn_equity":
        return _FEE_MODEL_CN
    return _FEE_MODEL_CB


def calc_buy_cost(
    price: float,
    shares: int,
    date: str | date | datetime,
    volume: float = 0.0,
    asset_type: AssetType = "cn_equity",
    impact_coeff: float = 10.0,  # §13.2 对齐 fee_config.IMPACT_COEFF
) -> float:
    """计算买入总费用（佣金 + 过户费 + 滑点冲击）

    Returns:
        总费用（float），单位：元
    """
    d = _parse_date(date)
    notional = price * shares
    model = _get_fee_model(asset_type)
    # 佣金
    comm = max(notional * model.commission.rate, model.commission.min_fee)
    # 过户费
    transfer = notional * model.transfer_fee.rate
    # 市场冲击滑点
    slippage = calc_slippage_impact(
        price, shares, volume, impact_coeff,
    ) if volume > 0 else 0.0
    return comm + transfer + slippage


def calc_sell_cost(
    price: float,
    shares: int,
    date: str | date | datetime,
    volume: float = 0.0,
    asset_type: AssetType = "cn_equity",
    impact_coeff: float = 10.0,  # §13.2 对齐 fee_config.IMPACT_COEFF
) -> float:
    """计算卖出总费用（佣金 + 印花税 + 过户费 + 滑点冲击）

    Returns:
        总费用（float），单位：元
    """
    d = _parse_date(date)
    notional = price * shares
    model = _get_fee_model(asset_type)
    # 佣金
    comm = max(notional * model.commission.rate, model.commission.min_fee)
    # 印花税（时变）
    if isinstance(model.stamp_tax, TimeVaryingStampTaxModel):
        stamp = notional * model.stamp_tax.rate_at(d)
    else:
        stamp = notional * model.stamp_tax.sell_rate
    # 过户费
    transfer = notional * model.transfer_fee.rate
    # 市场冲击滑点
    slippage = calc_slippage_impact(
        price, shares, volume, impact_coeff,
    ) if volume > 0 else 0.0
    return comm + stamp + transfer + slippage


def calc_slippage_impact(
    price: float,
    shares: int,
    volume: float,
    impact_coeff: float = 10.0,  # §13.2 对齐 fee_config.IMPACT_COEFF
    spread_bps: float = 3.0,
) -> float:
    """计算市场冲击滑点（square root impact model）

    impact = price * impact_coeff * sqrt(shares / volume)  (bps)
    加上固定半价差 spread_bps / 2

    Returns:
        滑点金额（float），单位：元
    """
    notional = price * shares
    if volume <= 0 or shares <= 0:
        return notional * spread_bps / 2.0 / 10000.0
    participation = shares / volume
    impact_bps = impact_coeff * math.sqrt(participation)
    total_bps = impact_bps + spread_bps / 2.0
    return notional * total_bps / 10000.0
