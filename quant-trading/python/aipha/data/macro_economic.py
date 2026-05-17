"""Macro economic data provider.

Fetches CN/US macro indicators: GDP, CPI, PPI, PMI, interest
rates, money supply, etc. Supports akshare for CN and optional
FRED API for US data.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

__all__ = ["MacroEconomicProvider", "INDICATOR_CATALOG"]

logger = logging.getLogger(__name__)

INDICATOR_CATALOG: Dict[str, Dict[str, str]] = {
    "cn": {
        "gdp": "国内生产总值",
        "cpi": "居民消费价格指数",
        "ppi": "工业品出厂价格指数",
        "pmi": "制造业采购经理指数",
        "shibor": "上海银行间同业拆放利率",
        "lpr": "贷款市场报价利率",
        "m2": "广义货币供应量",
        "social_finance": "社会融资规模",
    },
    "us": {
        "fed_rate": "联邦基金利率",
        "us_cpi": "美国CPI",
        "us_pmi": "美国PMI",
        "treasury_10y": "美国10年期国债收益率",
        "treasury_2y": "美国2年期国债收益率",
        "vix": "VIX恐慌指数",
    },
}


class MacroEconomicProvider:
    """Provider for macro economic data."""

    def __init__(self, fred_api_key: Optional[str] = None) -> None:
        self._fred_key = fred_api_key or os.environ.get("FRED_API_KEY")

    def list_indicators(self, market: str = "cn") -> Dict[str, str]:
        return INDICATOR_CATALOG.get(market, {})

    def get_indicator(
        self, indicator: str, market: str = "cn", limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch history for a specific macro indicator."""
        fetcher = self._get_fetcher(indicator, market)
        if fetcher is None:
            logger.warning("No fetcher for %s/%s", market, indicator)
            return []
        try:
            return fetcher(limit)
        except Exception as e:
            logger.warning("Macro fetch %s failed: %s", indicator, e)
            return []

    def _get_fetcher(self, indicator: str, market: str):
        cn_map = {
            "gdp": self._fetch_gdp,
            "cpi": self._fetch_cpi,
            "ppi": self._fetch_ppi,
            "pmi": self._fetch_pmi,
            "shibor": self._fetch_shibor,
            "lpr": self._fetch_lpr,
            "m2": self._fetch_m2,
        }
        if market == "cn":
            return cn_map.get(indicator)
        return None

    def _fetch_gdp(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.macro_china_gdp()
        return self._df_to_records(df, limit)

    def _fetch_cpi(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.macro_china_cpi_monthly()
        return self._df_to_records(df, limit)

    def _fetch_ppi(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.macro_china_ppi()
        return self._df_to_records(df, limit)

    def _fetch_pmi(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.macro_china_pmi()
        return self._df_to_records(df, limit)

    def _fetch_shibor(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.rate_interbank(market="上海银行间同业拆放利率")
        return self._df_to_records(df, limit)

    def _fetch_lpr(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.macro_china_lpr()
        return self._df_to_records(df, limit)

    def _fetch_m2(self, limit: int) -> List[Dict[str, Any]]:
        import akshare as ak
        df = ak.macro_china_money_supply()
        return self._df_to_records(df, limit)

    @staticmethod
    def _df_to_records(df, limit: int) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        records = df.tail(limit).to_dict("records")
        # convert non-serializable types
        clean = []
        for r in records:
            clean.append({k: str(v) if not isinstance(v, (int, float)) else v
                          for k, v in r.items()})
        return clean
