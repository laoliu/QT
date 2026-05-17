"""东方财富数据 Provider

通过东方财富公开 HTTP API 获取:
- 实时行情报价
- 板块资金流向
- 板块成分股
- 新闻快讯

免费、无 Token、HTTP 轮询。

使用示例::

    provider = EastmoneyProvider()
    quote = provider.get_quote("600519")
    flow = provider.get_fund_flow("600519")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://quote.eastmoney.com",
}

# 东财市场代码: 1=沪, 0=深
_SECID_MAP = {
    "6": "1", "5": "1", "9": "1",  # 沪市
    "0": "0", "3": "0", "1": "0",  # 深市
}


@dataclass(frozen=True, slots=True)
class EastmoneyQuote:
    """东财行情快照"""

    symbol: str
    name: str
    current: float
    open: float
    last_close: float
    high: float
    low: float
    volume: int
    amount: float
    chg: float
    percent: float


@dataclass(frozen=True, slots=True)
class FundFlowItem:
    """资金流向数据"""

    symbol: str
    name: str
    main_net_inflow: float  # 主力净流入
    super_large_net: float  # 超大单净流入
    large_net: float        # 大单净流入
    medium_net: float       # 中单净流入
    small_net: float        # 小单净流入


class EastmoneyProvider:
    """东方财富数据 Provider"""

    def __init__(self, timeout: float = _REQUEST_TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # ----------------------------------------------------------
    # 实时报价
    # ----------------------------------------------------------

    def get_quote(self, symbol: str) -> Optional[EastmoneyQuote]:
        """获取单只标的实时报价。"""
        secid = self._to_secid(symbol)
        url = (
            "https://push2.eastmoney.com/api/qt/stock/get"
            f"?secid={secid}&fields=f43,f44,f45,f46,f47,f48,"
            "f57,f58,f60,f169,f170"
        )
        try:
            resp = self._session.get(url, timeout=self._timeout)
            data = resp.json().get("data")
            if not data:
                return None
            price = data.get("f43", 0) / 100
            prev = data.get("f60", 0) / 100
            if price <= 0:
                return None
            return EastmoneyQuote(
                symbol=symbol,
                name=data.get("f58", ""),
                current=price,
                open=data.get("f46", 0) / 100,
                last_close=prev,
                high=data.get("f44", 0) / 100,
                low=data.get("f45", 0) / 100,
                volume=data.get("f47", 0),
                amount=data.get("f48", 0),
                chg=round(price - prev, 4),
                percent=data.get("f170", 0) / 100,
            )
        except Exception as e:
            logger.debug("Eastmoney quote failed for %s: %s", symbol, e)
            return None

    # ----------------------------------------------------------
    # 资金流向
    # ----------------------------------------------------------

    def get_fund_flow(
        self, symbol: str,
    ) -> Optional[FundFlowItem]:
        """获取标的资金流向 (当日)。"""
        secid = self._to_secid(symbol)
        url = (
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
            f"?secid={secid}&klt=101&lmt=1"
            "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
        )
        try:
            resp = self._session.get(url, timeout=self._timeout)
            data = resp.json().get("data", {})
            klines = data.get("klines", [])
            if not klines:
                return None
            fields = klines[-1].split(",")
            if len(fields) < 6:
                return None
            return FundFlowItem(
                symbol=symbol,
                name="",
                main_net_inflow=float(fields[1]),
                super_large_net=float(fields[2]),
                large_net=float(fields[3]),
                medium_net=float(fields[4]),
                small_net=float(fields[5]),
            )
        except Exception as e:
            logger.debug("Eastmoney fund flow failed: %s", e)
            return None

    # ----------------------------------------------------------
    # 板块成分
    # ----------------------------------------------------------

    def get_sector_members(
        self, sector_code: str,
    ) -> list[str]:
        """获取板块成分股代码列表。

        Args:
            sector_code: 东财板块代码 (如 "BK0477")
        """
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?fid=f3&po=1&pz=500&pn=1&np=1"
            f"&fs=b:{sector_code}&fields=f12"
        )
        try:
            resp = self._session.get(url, timeout=self._timeout)
            data = resp.json().get("data", {})
            diff = data.get("diff", [])
            return [item.get("f12", "") for item in diff if item.get("f12")]
        except Exception as e:
            logger.debug("Eastmoney sector members failed: %s", e)
            return []

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------

    @staticmethod
    def _to_secid(symbol: str) -> str:
        """6 位代码 → 东财 secid (如 1.600519)。"""
        code = symbol.strip().split(".")[0].zfill(6)
        market = _SECID_MAP.get(code[0], "1")
        return f"{market}.{code}"
