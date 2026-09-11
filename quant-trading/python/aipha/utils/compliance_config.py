"""
aipha/utils/compliance_config.py
——————————————————————————————————
加载 configs/algo_trading_compliance.yml，提供便捷访问接口。

使用方式
--------
>>> cfg = load_compliance_config()
>>> cfg.max_participation_rate
0.1
>>> cfg.max_cancel_rate
0.8
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 默认配置文件路径（相对于本文件，不依赖进程 CWD）
# parents[0]=aipha/utils, parents[1]=aipha, parents[2]=python ← configs/ 在这一层。
# 曾经写成 parents[3]（= quant-trading），多跳一层，导致无参调用 load_compliance_config()
# 永远抛 FileNotFoundError —— 合规检查在生产里静默不可用。
#
# 公开这个常量是为了让调用方别再各自写 Path("configs/algo_trading_compliance.yml")：
# 那是相对 CWD 的，从 python/ 以外任何目录启动都读不到，而调用方普遍写成
# `if path.exists(): from_config() else ComplianceEngine()`，于是静默退回默认限额。
DEFAULT_COMPLIANCE_CONFIG_PATH = (
    Path(__file__).parents[2] / "configs" / "algo_trading_compliance.yml"
)

# 旧私有名保留为别名，避免外部引用断裂
_DEFAULT_CONFIG_PATH = DEFAULT_COMPLIANCE_CONFIG_PATH


@dataclass(frozen=True)
class ComplianceConfig:
    """程序化交易合规配置（不可变）。"""

    version: str
    max_cancel_rate: float
    max_order_rate_per_second: int
    cancel_rate_alert_threshold: float
    max_participation_rate: float
    crowding_overlap_max_jaccard: float
    crowding_high_participation_rate: float
    t1_discount_factor: float
    flag_limit_hit_threshold: float
    min_tradeable_universe_fraction: float
    require_registration: bool
    registration_status: str
    # CB 特有
    cb_lot_size: int
    cb_t_plus_n: int
    cb_force_redeem_max_exposure: float
    # 完整原始 dict，供扩展访问
    _raw: dict[str, Any] = field(repr=False, hash=False, compare=False)

    def raw(self) -> dict[str, Any]:
        """返回完整原始配置字典（只读视图）。"""
        return dict(self._raw)


def load_compliance_config(path: str | Path | None = None) -> ComplianceConfig:
    """
    加载并解析 algo_trading_compliance.yml。

    Parameters
    ----------
    path : str | Path | None
        配置文件路径。None 时使用默认路径。

    Returns
    -------
    ComplianceConfig

    Raises
    ------
    FileNotFoundError
        配置文件不存在时。
    ValueError
        缺失必要字段时。
    """
    cfg_path = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"Compliance config not found: {cfg_path}")

    with open(cfg_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    try:
        order = raw["order_limits"]
        part = raw["participation"]
        sig = raw["signal_quality"]
        rep = raw["reporting"]
        cb = raw["convertible_bond"]
    except KeyError as exc:
        raise ValueError(f"Compliance config missing section: {exc}") from exc

    return ComplianceConfig(
        version=str(raw.get("version", "unknown")),
        max_cancel_rate=float(order["max_cancel_rate"]),
        max_order_rate_per_second=int(order["max_order_rate_per_second"]),
        cancel_rate_alert_threshold=float(order["cancel_rate_alert_threshold"]),
        max_participation_rate=float(part["max_participation_rate"]),
        crowding_overlap_max_jaccard=float(part["crowding_overlap_max_jaccard"]),
        crowding_high_participation_rate=float(part["crowding_high_participation_rate"]),
        t1_discount_factor=float(sig["t1_discount_factor"]),
        flag_limit_hit_threshold=float(sig["flag_limit_hit_threshold"]),
        min_tradeable_universe_fraction=float(sig["min_tradeable_universe_fraction"]),
        require_registration=bool(rep["require_registration"]),
        registration_status=str(rep["registration_status"]),
        cb_lot_size=int(cb["lot_size"]),
        cb_t_plus_n=int(cb["t_plus_n"]),
        cb_force_redeem_max_exposure=float(cb["force_redeem_max_exposure"]),
        _raw=raw,
    )
