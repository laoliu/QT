"""雪球数据 Provider

通过雪球 (Xueqiu) HTTP API 获取:
- 实时行情报价
- 热股排行
- 社区情感

需要 xq_a_token cookie (通过 akshare 获取或手动配置)。

使用示例::

    provider = XueqiuProvider()
    quote = provider.get_quote("600519")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_XQ_BASE = "https://stock.xueqiu.com"
_REQUEST_TIMEOUT = 10
_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)


@dataclass(frozen=True, slots=True)
class XueqiuQuote:
    """雪球行情快照"""

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
    turnover_rate: float
    pe_ratio: float
    pb_ratio: float
    market_cap: float


class XueqiuProvider:
    """雪球数据 Provider

    Args:
        timeout: HTTP 请求超时 (秒)
    """

    def __init__(self, timeout: float = _REQUEST_TIMEOUT):
        self._timeout = timeout
        self._session: Optional[requests.Session] = None

    # ----------------------------------------------------------
    # Session 管理
    # ----------------------------------------------------------

    def _get_session(self) -> requests.Session:
        """获取带 cookie 的 session (懒初始化)。"""
        if self._session is not None:
            return self._session
        self._session = self._create_session()
        return self._session

    def _create_session(self) -> requests.Session:
        """创建雪球 session 并获取 cookie。"""
        s = requests.Session()
        s.headers.update({"User-Agent": _USER_AGENT})
        # 尝试从 akshare 获取 xq_a_token
        try:
            from akshare.stock.cons import xq_a_token
            s.headers["cookie"] = f"xq_a_token={xq_a_token};"
        except (ImportError, AttributeError):
            pass
        # 访问首页获取初始 cookie
        try:
            s.get("https://xueqiu.com", timeout=5)
        except Exception:
            pass
        return s

    # ----------------------------------------------------------
    # 实时行情
    # ----------------------------------------------------------

    @staticmethod
    def _to_xq_symbol(symbol: str) -> str:
        """转换为雪球代码格式: 600519 → SH600519"""
        code = symbol.split(".")[0]
        if code.startswith(("6", "5")):
            return f"SH{code}"
        if code.startswith(("0", "3", "1")):
            return f"SZ{code}"
        return code

    def get_quote(self, symbol: str) -> Optional[XueqiuQuote]:
        """获取单只标的实时行情"""
        xq_sym = self._to_xq_symbol(symbol)
        url = f"{_XQ_BASE}/v5/stock/quote.json"
        params = {
            "symbol": xq_sym,
            "extend": "detail",
        }
        try:
            resp = self._get_session().get(
                url, params=params, timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
            q = data.get("data", {}).get("quote", {})
            if not q:
                return None
            return XueqiuQuote(
                symbol=symbol,
                name=q.get("name", ""),
                current=float(q.get("current", 0)),
                open=float(q.get("open", 0)),
                last_close=float(q.get("last_close", 0)),
                high=float(q.get("high", 0)),
                low=float(q.get("low", 0)),
                volume=int(q.get("volume", 0)),
                amount=float(q.get("amount", 0)),
                chg=float(q.get("chg", 0)),
                percent=float(q.get("percent", 0)),
                turnover_rate=float(q.get("turnover_rate") or 0),
                pe_ratio=float(q.get("pe_ttm") or 0),
                pb_ratio=float(q.get("pb") or 0),
                market_cap=float(q.get("market_capital") or 0),
            )
        except Exception:
            logger.warning("xueqiu quote failed: %s", symbol, exc_info=True)
            return None

    def get_quotes_batch(
        self, symbols: list[str]
    ) -> dict[str, XueqiuQuote]:
        """批量获取行情 (逐只查询, 雪球无批量接口)"""
        result: dict[str, XueqiuQuote] = {}
        for sym in symbols:
            q = self.get_quote(sym)
            if q is not None:
                result[sym] = q
        return result
