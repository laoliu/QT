"""股票池策略 — 预定义指数成分 / 自定义条件组合

支持:
  - 指数成分股 (沪深300/中证500/中证1000/全A)
  - 自定义条件组合 (市值范围 + 行业 + 基本面)
  - 动态更新 (跟随指数调整)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aipha.universe.exclusions import UniverseExclusions, ExclusionConfig


class UniverseType(str, Enum):
    CSI300 = "CSI300"
    CSI500 = "CSI500"
    CSI1000 = "CSI1000"
    ALL_A = "ALL_A"
    HSI = "HSI"
    HSCEI = "HSCEI"
    HSTECH = "HSTECH"
    ALL_HK = "ALL_HK"
    CUSTOM = "CUSTOM"


@dataclass
class PolicyConfig:
    universe_type: UniverseType = UniverseType.ALL_A
    exclusion: ExclusionConfig = field(default_factory=ExclusionConfig)
    # 自定义条件
    custom_filters: list[dict] = field(default_factory=list)
    # 行业白名单 (空 = 不限)
    include_industries: set[str] = field(default_factory=set)
    # 市值范围 (用于 CUSTOM)
    mv_range: tuple[float, float] = (0, float("inf"))


class UniversePolicy:
    """股票池管理器 — 组合排除规则 + 成分约束。"""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()
        self._exclusions = UniverseExclusions(self.config.exclusion)
        self._index_members: set[str] = set()

    def set_index_members(self, symbols: set[str]) -> None:
        """设置指数成分股列表 (外部提供)。"""
        self._index_members = symbols

    def build_universe(self, all_stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从全量股票中构建最终股票池。"""
        cfg = self.config

        # 1. 成分过滤
        if cfg.universe_type != UniverseType.ALL_A and self._index_members:
            filtered = [s for s in all_stocks if s.get("symbol") in self._index_members]
        else:
            filtered = all_stocks

        # 2. 市值范围 (CUSTOM 模式)
        if cfg.universe_type == UniverseType.CUSTOM:
            lo, hi = cfg.mv_range
            filtered = [s for s in filtered if lo <= (s.get("total_mv", 0) or 0) <= hi]

        # 3. 行业白名单
        if cfg.include_industries:
            filtered = [
                s for s in filtered
                if s.get("industry", "") in cfg.include_industries
            ]

        # 4. 自定义过滤
        for cond in cfg.custom_filters:
            col = cond.get("field")
            op = cond.get("op", ">=")
            val = cond.get("value", 0)
            if col:
                new = []
                for s in filtered:
                    v = s.get(col)
                    if v is None:
                        continue
                    if op == ">=" and v >= val:
                        new.append(s)
                    elif op == "<=" and v <= val:
                        new.append(s)
                    elif op == ">" and v > val:
                        new.append(s)
                    elif op == "<" and v < val:
                        new.append(s)
                    elif op == "==" and v == val:
                        new.append(s)
                filtered = new

        # 5. 排除规则
        return self._exclusions.filter(filtered)
