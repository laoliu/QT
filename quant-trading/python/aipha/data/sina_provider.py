"""新浪财经实时行情 Provider

通过新浪财经 HTTP API (hq.sinajs.cn) 获取 A 股实时报价。
免费、无需 Token、无需认证，但有频率限制。

返回字段: name, current, open, last_close, high, low, volume, amount, chg, percent

使用示例::

    provider = SinaRealtimeProvider()
    quote = provider.get_quote("600519")
    print(quote["current"])  # 最新价
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_SINA_HQ_URL = "https://hq.sinajs.cn/list="
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}
_REQUEST_TIMEOUT = 5


@dataclass(frozen=True, slots=True)
class SinaQuote:
    """新浪行情快照"""

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


class SinaRealtimeProvider:
    """新浪财经实时行情 Provider

    Args:
        timeout: HTTP 请求超时 (秒)
    """

    def __init__(self, timeout: float = _REQUEST_TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(_SINA_HEADERS)

    def get_quote(self, symbol: str) -> Optional[SinaQuote]:
        """获取单只标的实时报价。

        Args:
            symbol: 6 位代码 (如 600519)

        Returns:
            SinaQuote 或 None (失败时)
        """
        sina_code = self._to_sina_code(symbol)
        try:
            resp = self._session.get(
                f"{_SINA_HQ_URL}{sina_code}",
                timeout=self._timeout,
            )
            resp.encoding = "gbk"
            return self._parse_sina_line(resp.text.strip(), symbol)
        except Exception as e:
            logger.debug("Sina quote failed for %s: %s", symbol, e)
            return None

    def get_quotes_batch(
        self, symbols: list[str],
    ) -> dict[str, SinaQuote]:
        """批量获取实时报价 (单次请求最多约 50 只)。"""
        if not symbols:
            return {}
        sina_codes = [self._to_sina_code(s) for s in symbols]
        code_str = ",".join(sina_codes)
        result: dict[str, SinaQuote] = {}
        try:
            resp = self._session.get(
                f"{_SINA_HQ_URL}{code_str}",
                timeout=self._timeout,
            )
            resp.encoding = "gbk"
            for i, line in enumerate(resp.text.strip().split("\n")):
                line = line.strip()
                if not line:
                    continue
                if i < len(symbols):
                    q = self._parse_sina_line(line, symbols[i])
                    if q:
                        result[symbols[i]] = q
        except Exception as e:
            logger.warning("Sina batch quote failed: %s", e)
        return result

    @staticmethod
    def _to_sina_code(symbol: str) -> str:
        """将 6 位代码转为新浪格式 (sh600519 / sz000001)。"""
        code = symbol.strip().split(".")[0].zfill(6)
        prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
        return f"{prefix}{code}"

    @staticmethod
    def _parse_sina_line(
        line: str, symbol: str,
    ) -> Optional[SinaQuote]:
        """解析新浪返回的单行数据。

        新浪返回格式:
          var hq_str_sz000690="宝新能源,4.88,4.90,4.76,...";
        字段: name,open,prevClose,price,high,low,bid1,ask1,volume,amount,...
        """
        if '="' not in line:
            return None
        csv_part = line.split('="', 1)[1].rstrip('";')
        if not csv_part:
            return None
        fields = csv_part.split(",")
        if len(fields) < 10:
            return None
        try:
            price = float(fields[3])
            if price <= 0:
                return None
            last_close = float(fields[2] or 0)
            chg = round(price - last_close, 4)
            pct = (
                round(chg / last_close * 100, 4)
                if last_close > 0 else 0.0
            )
            return SinaQuote(
                symbol=symbol,
                name=fields[0],
                current=price,
                open=float(fields[1] or 0),
                last_close=last_close,
                high=float(fields[4] or 0),
                low=float(fields[5] or 0),
                volume=int(float(fields[8] or 0)),
                amount=float(fields[9] or 0),
                chg=chg,
                percent=pct,
            )
        except (ValueError, IndexError):
            return None
