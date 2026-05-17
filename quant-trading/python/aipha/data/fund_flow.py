"""Fund flow analysis module.

Provides fund flow data for individual stocks, sectors, rankings,
and northbound (Hong Kong → mainland) capital flows using akshare.

Adapted from QT fund_flow.py for AlphQ architecture.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FlowAction(str, Enum):
    """Available fund flow query actions."""

    STOCK = "stock"
    SECTOR = "sector"
    RANK = "rank"
    NORTHBOUND = "northbound"


@dataclass
class FlowResult:
    """Standard fund flow query result."""

    action: str
    data: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _safe_float(val: Any) -> float:
    """Convert value to float, returning 0.0 for NaN/None."""
    if val is None:
        return 0.0
    try:
        f = float(val)
        return 0.0 if math.isnan(f) else f
    except (ValueError, TypeError):
        return 0.0


def query_stock_flow(symbol: str) -> FlowResult:
    """个股资金流向 (individual stock fund flow)."""
    try:
        import akshare as ak
    except ImportError:
        return FlowResult(action="stock", error="akshare not installed")
    try:
        df = ak.stock_individual_fund_flow(stock=symbol, market="sh")
        if df is None or df.empty:
            # Try sz market
            df = ak.stock_individual_fund_flow(stock=symbol, market="sz")
        if df is None or df.empty:
            return FlowResult(action="stock", data=[],
                              meta={"symbol": symbol})
        records = []
        for _, row in df.tail(30).iterrows():
            records.append({
                "date": str(row.get("日期", "")),
                "main_net": _safe_float(row.get("主力净流入-净额")),
                "main_pct": _safe_float(row.get("主力净流入-净占比")),
                "large_net": _safe_float(row.get("超大单净流入-净额")),
                "big_net": _safe_float(row.get("大单净流入-净额")),
                "mid_net": _safe_float(row.get("中单净流入-净额")),
                "small_net": _safe_float(row.get("小单净流入-净额")),
                "close": _safe_float(row.get("收盘价")),
                "pct_change": _safe_float(row.get("涨跌幅")),
            })
        return FlowResult(action="stock", data=records,
                          meta={"symbol": symbol, "count": len(records)})
    except Exception as exc:
        logger.warning("query_stock_flow(%s) failed: %s", symbol, exc)
        return FlowResult(action="stock", error=str(exc))


def query_sector_flow(sector: str = "行业资金流") -> FlowResult:
    """板块资金流向排名 (sector fund flow ranking)."""
    try:
        import akshare as ak
    except ImportError:
        return FlowResult(action="sector", error="akshare not installed")
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type=sector)
        if df is None or df.empty:
            return FlowResult(action="sector", data=[])
        records = []
        for _, row in df.iterrows():
            records.append({
                "name": str(row.get("名称", "")),
                "main_net": _safe_float(row.get("主力净流入-净额")),
                "main_pct": _safe_float(row.get("主力净流入-净占比")),
                "rise_count": int(row.get("上涨家数", 0)),
                "fall_count": int(row.get("下跌家数", 0)),
                "pct_change": _safe_float(row.get("涨跌幅")),
            })
        return FlowResult(action="sector", data=records,
                          meta={"sector_type": sector})
    except Exception as exc:
        logger.warning("query_sector_flow failed: %s", exc)
        return FlowResult(action="sector", error=str(exc))


def query_rank_flow(indicator: str = "今日") -> FlowResult:
    """个股资金流向排名 (stock fund flow ranking)."""
    try:
        import akshare as ak
    except ImportError:
        return FlowResult(action="rank", error="akshare not installed")
    try:
        df = ak.stock_individual_fund_flow_rank(indicator=indicator)
        if df is None or df.empty:
            return FlowResult(action="rank", data=[])
        records = []
        for _, row in df.head(50).iterrows():
            records.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "close": _safe_float(row.get("最新价")),
                "pct_change": _safe_float(row.get("涨跌幅")),
                "main_net": _safe_float(row.get("主力净流入-净额")),
                "main_pct": _safe_float(row.get("主力净流入-净占比")),
            })
        return FlowResult(action="rank", data=records,
                          meta={"indicator": indicator, "count": len(records)})
    except Exception as exc:
        logger.warning("query_rank_flow failed: %s", exc)
        return FlowResult(action="rank", error=str(exc))


def query_northbound_flow() -> FlowResult:
    """北向资金流向 (northbound / HKEX → mainland).

    Note: HKEX stopped real-time northbound disclosure on 2024-08-19.
    Data may be delayed or unavailable for recent dates.
    """
    try:
        import akshare as ak
    except ImportError:
        return FlowResult(action="northbound", error="akshare not installed")
    try:
        records = []
        # Summary data
        try:
            summary = ak.stock_hsgt_fund_flow_summary_em()
            if summary is not None and not summary.empty:
                for _, row in summary.tail(10).iterrows():
                    records.append({
                        "date": str(row.get("日期", "")),
                        "north_net": _safe_float(row.get("北向资金-净流入")),
                        "south_net": _safe_float(row.get("南向资金-净流入")),
                    })
        except Exception:
            logger.debug("hsgt summary unavailable")

        # Market-wide flow
        try:
            market_flow = ak.stock_market_fund_flow()
            if market_flow is not None and not market_flow.empty:
                for _, row in market_flow.tail(5).iterrows():
                    records.append({
                        "date": str(row.get("日期", "")),
                        "type": "market",
                        "main_net": _safe_float(row.get("主力净流入-净额")),
                    })
        except Exception:
            logger.debug("market fund flow unavailable")

        return FlowResult(
            action="northbound", data=records,
            meta={"note": "HKEX stopped real-time disclosure 2024-08-19"})
    except Exception as exc:
        logger.warning("query_northbound_flow failed: %s", exc)
        return FlowResult(action="northbound", error=str(exc))


def query_flow(action: str, **kwargs: Any) -> FlowResult:
    """Unified entry point for fund flow queries.

    Args:
        action: One of 'stock', 'sector', 'rank', 'northbound'.
        **kwargs: Action-specific parameters.
    """
    dispatch = {
        FlowAction.STOCK: lambda: query_stock_flow(kwargs.get("symbol", "")),
        FlowAction.SECTOR: lambda: query_sector_flow(
            kwargs.get("sector", "行业资金流")),
        FlowAction.RANK: lambda: query_rank_flow(
            kwargs.get("indicator", "今日")),
        FlowAction.NORTHBOUND: lambda: query_northbound_flow(),
    }
    try:
        flow_action = FlowAction(action)
    except ValueError:
        return FlowResult(action=action,
                          error=f"Unknown action: {action}")
    return dispatch[flow_action]()
