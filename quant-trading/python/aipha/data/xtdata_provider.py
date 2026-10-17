"""QMT/XTData 数据提供者 — 通过 HTTP Bridge 获取数据

通过 QMT Data Bridge (HTTP Server) 获取 K 线、实时报价、板块数据。
Bridge 服务器独立运行在 Windows 端 (默认 http://127.0.0.1:18888)。

当 Bridge 不可用时自动降级（返回空数据），不阻塞调用方。

环境变量:
  WINDOWS_XTDATA_URL: Bridge 服务器地址 (默认 http://127.0.0.1:18888)

使用示例::

    provider = XTDataProvider()
    if provider.is_available():
        bars = provider.load_bars(symbol="600519", ...)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime
from typing import Any, Optional

import requests

from aipha.data.provider_base import DataFingerprint
from aipha.data.provider_factory import normalize_to_qmt
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

# Bridge 默认地址
_DEFAULT_BRIDGE_URL = "http://127.0.0.1:18888"

# HTTP 超时 (秒)
_REQUEST_TIMEOUT = 15

# 健康检查缓存有效期 (秒)
_HEALTH_CACHE_TTL = 30


class XTDataProvider:
    """QMT/XTData 数据提供者 (HTTP Bridge 客户端)

    Args:
        bridge_url: Bridge HTTP 服务器地址
        timeout: HTTP 请求超时 (秒)
    """

    def __init__(
        self,
        bridge_url: str | None = None,
        timeout: float = _REQUEST_TIMEOUT,
    ):
        self._url = (
            bridge_url
            or os.environ.get("WINDOWS_XTDATA_URL")
            or _DEFAULT_BRIDGE_URL
        ).rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        # 健康检查缓存
        self._health_ok: bool = False
        self._health_ts: float = 0.0

    def fingerprint(self) -> DataFingerprint:
        return DataFingerprint(
            provider="xtdata_bridge",
            snapshot="realtime",
            calendar="cn_stock",
            adjust="none",
            schema="1",
        )

    # ----------------------------------------------------------
    # 健康检查
    # ----------------------------------------------------------

    def is_available(self) -> bool:
        """检查 Bridge 服务是否可用 (带缓存)。"""
        now = time.monotonic()
        if now - self._health_ts < _HEALTH_CACHE_TTL:
            return self._health_ok
        self._health_ok = self._check_health()
        self._health_ts = now
        return self._health_ok

    def _check_health(self) -> bool:
        """实际执行健康检查请求。"""
        try:
            resp = self._get("/health")
            return resp.get("status") == "ok"
        except Exception:
            return False

    # ----------------------------------------------------------
    # K 线数据
    # ----------------------------------------------------------

    def load_bars(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
        frequency: str = "1d",
    ) -> list[Bar]:
        """通过 Bridge 获取 K 线数据。"""
        if not self.is_available():
            logger.debug("XTData bridge not available, returning empty")
            return []

        qmt_code = normalize_to_qmt(symbol.split(".")[0])
        period_map = {"1d": "1d", "daily": "1d", "1w": "1w", "1m": "1m"}
        period = period_map.get(frequency, "1d")

        # 估算所需的 K 线条数 (交易日约占自然日的 70%)
        days = (end - start).days
        count = max(int(days * 0.72) + 10, 30)

        payload = {
            "codes": [qmt_code],
            "period": period,
            "count": count,
        }
        try:
            resp = self._post("/klines", payload)
        except Exception as e:
            logger.warning("XTData klines request failed: %s", e)
            return []

        return self._parse_bars(resp, symbol, start, end)

    # ----------------------------------------------------------
    # 实时报价
    # ----------------------------------------------------------

    def get_quote(self, codes: list[str]) -> dict[str, dict]:
        """获取实时报价。"""
        if not self.is_available():
            return {}
        qmt_codes = [normalize_to_qmt(c.split(".")[0]) for c in codes]
        try:
            resp = self._post("/quote", {"codes": qmt_codes})
            return resp.get("data", {})
        except Exception as e:
            logger.warning("XTData quote request failed: %s", e)
            return {}

    # ----------------------------------------------------------
    # 板块数据
    # ----------------------------------------------------------

    def get_sector_list(self, sector_type: str = "") -> list[str]:
        """获取板块列表。"""
        if not self.is_available():
            return []
        try:
            resp = self._post("/sector_list", {"sector_type": sector_type})
            return resp.get("data", [])
        except Exception as e:
            logger.warning("XTData sector_list failed: %s", e)
            return []

    def get_sector_members(self, sector: str) -> list[str]:
        """获取板块成分股。"""
        if not self.is_available():
            return []
        try:
            resp = self._post("/sector_members", {"sector": sector})
            return resp.get("data", [])
        except Exception as e:
            logger.warning("XTData sector_members failed: %s", e)
            return []

    # ----------------------------------------------------------
    # 内部请求方法
    # ----------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        """发送 POST 请求到 Bridge。"""
        url = f"{self._url}{path}"
        resp = self._session.post(
            url, json=payload, timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        """发送 GET 请求到 Bridge。"""
        url = f"{self._url}{path}"
        resp = self._session.get(url, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    # ----------------------------------------------------------
    # K 线解析
    # ----------------------------------------------------------

    def _parse_bars(
        self,
        resp: dict,
        symbol: str,
        start: date,
        end: date,
    ) -> list[Bar]:
        """将 Bridge 返回的 K 线 JSON 解析为 Bar 列表。"""
        bars: list[Bar] = []
        # Bridge 返回格式: {"data": {"600519.SH": [{"date":..., ...}, ...]}}
        all_data = resp.get("data", {})
        for _code, raw_bars in all_data.items():
            if not isinstance(raw_bars, list):
                continue
            for b in raw_bars:
                try:
                    dt_str = str(b.get("date", ""))
                    if len(dt_str) >= 8:
                        bar_date = datetime.strptime(
                            dt_str[:8], "%Y%m%d",
                        ).date()
                    else:
                        continue
                    if bar_date < start or bar_date > end:
                        continue
                    bars.append(Bar(
                        symbol=symbol,
                        date=bar_date,
                        open=float(b.get("open", 0)),
                        high=float(b.get("high", 0)),
                        low=float(b.get("low", 0)),
                        close=float(b.get("close", 0)),
                        volume=int(float(b.get("volume", 0))),
                    ))
                except (ValueError, TypeError, KeyError):
                    continue
        bars.sort(key=lambda x: x.date)
        return bars
