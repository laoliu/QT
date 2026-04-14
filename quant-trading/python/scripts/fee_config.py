"""
A股交易费率配置加载器。

从 .env 文件读取费率参数，提供给所有回测/回放脚本使用。
优先读取环境变量，回退到默认值。

用法:
    from fee_config import (
        COMMISSION_RATE, COMMISSION_MIN, STAMP_TAX_SELL,
        TRANSFER_FEE, SLIPPAGE_SPREAD_BPS, IMPACT_COEFF,
        MAX_PARTICIPATION, LOT_SIZE,
    )
"""

from __future__ import annotations

import os
from pathlib import Path

# 加载 .env 文件（项目根目录）
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_env_path, override=False)
except ImportError:
    pass  # python-dotenv 未安装时使用默认值


def _env_float(key: str, default: float) -> float:
    """从环境变量读取 float，不存在则用默认值。"""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    """从环境变量读取 int，不存在则用默认值。"""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(float(val))
    except ValueError:
        return default


# ---- 费率常量（从 .env 加载，回退到保守默认值）----
# NOTE: 默认值与 .env 推荐值对齐 (R115 审查后修正)
COMMISSION_RATE: float = _env_float("COMMISSION_RATE", 8.54e-05)  # 万0.854
COMMISSION_MIN: float = _env_float("COMMISSION_MIN", 5.0)
STAMP_TAX_SELL: float = _env_float("STAMP_TAX_SELL", 0.0005)
TRANSFER_FEE: float = _env_float("TRANSFER_FEE", 0.00001)
SLIPPAGE_SPREAD_BPS: float = _env_float("SLIPPAGE_SPREAD_BPS", 3.0)  # 与 .env 实际值一致
IMPACT_COEFF: float = _env_float("IMPACT_COEFF", 10.0)
MAX_PARTICIPATION: float = _env_float("MAX_PARTICIPATION", 0.10)  # CSI2000 参与率上限
LOT_SIZE: int = _env_int("LOT_SIZE", 100)
