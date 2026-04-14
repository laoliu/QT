"""合规配置 — 从 YAML 加载算法交易合规参数"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).parents[3] / "configs" / "algo_trading_compliance.yml"


@dataclass(frozen=True)
class ComplianceConfig:
    version: str = "1.0"
    max_cancel_rate: float = 0.50
    max_order_rate_per_second: float = 5.0
    cancel_rate_alert_threshold: float = 0.40
    max_participation_rate: float = 0.10
    crowding_overlap_max_jaccard: float = 0.30
    crowding_high_participation_rate: float = 0.05
    t1_discount_factor: float = 0.90
    flag_limit_hit_threshold: float = 0.80
    min_tradeable_universe_fraction: float = 0.60
    require_registration: bool = False
    registration_status: str = "unregistered"
    cb_lot_size: int = 10
    cb_t_plus_n: int = 0
    cb_force_redeem_max_exposure: float = 0.05
    _raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def raw(self) -> dict:
        return self._raw


def load_compliance_config(path: str | Path | None = None) -> ComplianceConfig:
    """加载合规配置

    Parameters
    ----------
    path : YAML 文件路径; None 使用默认路径
    """
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not p.exists():
        return ComplianceConfig()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    field_names = {f.name for f in ComplianceConfig.__dataclass_fields__.values() if f.name != "_raw"}
    kwargs = {k: v for k, v in raw.items() if k in field_names}
    return ComplianceConfig(**kwargs, _raw=raw)
