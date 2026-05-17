"""涨跌停容差判断 — 唯一实现（§13.2 单一实现原则）

本模块是涨跌停容差常量与比较函数的**唯一权威来源**。
backtest 引擎（MarketRules.precheck_order）和 paper 交易
（trading_ops.execute_rebalance）必须从此模块导入，
**禁止**在其他位置硬编码 0.999 / 1.001 等容差值。

容差逻辑说明：
  - 买入方向：当前价 ≥ 涨停价 × 0.999 时判定为"实质涨停"，拒绝买入
  - 卖出方向：当前价 ≤ 跌停价 × 1.001 时判定为"实质跌停"，拒绝卖出
  - 0.1% 容差用于处理浮点精度和四舍五入偏差
"""

from __future__ import annotations

# ────────────────────────────────────────────
#  容差常量（§13.2 唯一定义点）
# ────────────────────────────────────────────
LIMIT_UP_TOLERANCE: float = 0.999
"""买入涨停容差：price >= limit_up * LIMIT_UP_TOLERANCE 即判定涨停"""

LIMIT_DOWN_TOLERANCE: float = 1.001
"""卖出跌停容差：price <= limit_down * LIMIT_DOWN_TOLERANCE 即判定跌停"""


def is_at_limit_up(price: float, limit_up: float | None) -> bool:
    """判断当前价格是否实质涨停（买入方向应拒绝）。

    Args:
        price: 当前价格
        limit_up: 涨停价；None 表示无涨停限制

    Returns:
        True 表示已涨停，应拒绝买入
    """
    if limit_up is None:
        return False
    return price >= limit_up * LIMIT_UP_TOLERANCE


def is_at_limit_down(price: float, limit_down: float | None) -> bool:
    """判断当前价格是否实质跌停（卖出方向应拒绝）。

    Args:
        price: 当前价格
        limit_down: 跌停价；None 表示无跌停限制

    Returns:
        True 表示已跌停，应拒绝卖出
    """
    if limit_down is None:
        return False
    return price <= limit_down * LIMIT_DOWN_TOLERANCE
