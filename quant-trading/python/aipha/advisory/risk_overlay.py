"""风控叠加层。

对推荐结果施加持仓限制、行业暴露、流动性约束。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aipha.advisory.models import ActionType, Recommendation


@dataclass
class RiskConfig:
    """风控参数。"""
    max_single_weight: float = 0.10
    max_sector_weight: float = 0.30
    min_daily_volume: float = 1_000_000
    min_market_cap: float = 5e8
    max_recommendations: int = 20
    blocked_codes: list[str] = field(default_factory=list)


class RiskOverlay:
    """风控叠加器——过滤不满足约束的推荐。"""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self._config = config or RiskConfig()

    def apply(
        self,
        recommendations: list[Recommendation],
        market_data: dict[str, dict[str, Any]] | None = None,
    ) -> list[Recommendation]:
        """过滤并调整推荐列表。"""
        market_data = market_data or {}
        filtered: list[Recommendation] = []

        for rec in recommendations:
            # 黑名单
            if rec.ts_code in self._config.blocked_codes:
                continue

            md = market_data.get(rec.ts_code, {})

            # 流动性过滤
            vol = md.get("daily_volume", float("inf"))
            if vol < self._config.min_daily_volume:
                continue

            # 市值过滤
            mcap = md.get("market_cap", float("inf"))
            if mcap < self._config.min_market_cap:
                continue

            filtered.append(rec)

        # 行业集中度限制
        filtered = self._apply_sector_limit(filtered, market_data)

        # 数量限制
        return filtered[: self._config.max_recommendations]

    def _apply_sector_limit(
        self,
        recs: list[Recommendation],
        market_data: dict[str, dict[str, Any]],
    ) -> list[Recommendation]:
        """限制同一行业推荐数量。"""
        sector_count: dict[str, int] = {}
        max_per_sector = max(
            1,
            int(self._config.max_recommendations
                * self._config.max_sector_weight),
        )
        result: list[Recommendation] = []

        for rec in recs:
            md = market_data.get(rec.ts_code, {})
            sector = md.get("sector", "unknown")
            count = sector_count.get(sector, 0)
            if count < max_per_sector:
                result.append(rec)
                sector_count[sector] = count + 1
            elif rec.action in (
                ActionType.STRONG_BUY,
                ActionType.BUY,
            ):
                # 强信号可突破限制 1 个名额
                if count < max_per_sector + 1:
                    result.append(rec)
                    sector_count[sector] = count + 1

        return result
