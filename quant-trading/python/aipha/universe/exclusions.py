"""股票池排除规则 — ST / 停牌 / 涨跌停 / 上市不足 / 退市风险"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class ExclusionRule:
    name: str
    enabled: bool = True


@dataclass
class ExclusionConfig:
    exclude_st: bool = True
    exclude_suspended: bool = True
    exclude_limit_up: bool = True      # 一字涨停不买
    exclude_limit_down: bool = True    # 一字跌停不卖
    min_listed_days: int = 60
    min_price: float = 1.0             # 排除低价股
    min_mv: float = 0                  # 最小市值 (元)
    max_mv: float = float("inf")
    exclude_symbols: set[str] = field(default_factory=set)  # 黑名单
    exclude_industries: set[str] = field(default_factory=set)


class UniverseExclusions:
    """股票池排除引擎。"""

    def __init__(self, config: ExclusionConfig | None = None) -> None:
        self.config = config or ExclusionConfig()

    def filter(self, universe: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """过滤股票列表。

        Parameters
        ----------
        universe : list of dict
            每个 dict 至少含 symbol, close; 可选 is_st, is_suspended,
            listed_date, total_mv, industry, is_limit_up, is_limit_down
        """
        cfg = self.config
        result = []
        for stock in universe:
            sym = stock.get("symbol", "")
            if sym in cfg.exclude_symbols:
                continue
            if cfg.exclude_st and stock.get("is_st", False):
                continue
            if cfg.exclude_suspended and stock.get("is_suspended", False):
                continue
            if cfg.exclude_limit_up and stock.get("is_limit_up", False):
                continue
            if cfg.exclude_limit_down and stock.get("is_limit_down", False):
                continue

            # 上市天数
            listed = stock.get("listed_date")
            if listed and cfg.min_listed_days > 0:
                if isinstance(listed, str):
                    from datetime import datetime
                    listed = datetime.strptime(listed, "%Y%m%d").date()
                days = (date.today() - listed).days if isinstance(listed, date) else 0
                if days < cfg.min_listed_days:
                    continue

            price = stock.get("close", 0)
            if price < cfg.min_price:
                continue

            mv = stock.get("total_mv", float("inf"))
            if mv < cfg.min_mv or mv > cfg.max_mv:
                continue

            ind = stock.get("industry", "")
            if ind and ind in cfg.exclude_industries:
                continue

            result.append(stock)
        return result
