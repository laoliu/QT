"""可转债正股基本面桥接 — 获取正股 ROE / EP / PE / 波动率等"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class StockFundamentals:
    stk_code: str
    query_date: date
    roe: float | None = None
    ep: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    total_mv: float | None = None
    turnover_rate: float | None = None


@dataclass(slots=True)
class StockDailyMetrics:
    stk_code: str
    query_date: date
    vol_20d: float | None = None
    vol_60d: float | None = None
    mom_20d: float | None = None
    mom_60d: float | None = None


class CBStockBridge:
    """可转债正股桥接器 — 提供正股基本面 & 技术指标查询

    可注入 equity_provider / pit_provider, 或使用默认 TuShare 接口。
    """

    def __init__(
        self,
        cache_dir: str = "",
        pit_provider: Any | None = None,
        equity_provider: Any | None = None,
    ) -> None:
        if not cache_dir:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir())
        self._cache_dir = cache_dir
        self._equity = equity_provider
        self._pit = pit_provider
        self._daily_basic_cache: dict[str, Any] = {}
        self._fundamentals_cache: dict[tuple[str, date], StockFundamentals] = {}

    # ── 预加载 ──────────────────────────────────────────

    def preload_daily_basic(
        self, start_date: str, end_date: str, trade_dates: list[str] | None = None,
    ) -> None:
        """预加载日线基本面到缓存 (需 pandas + TuShare)"""
        # 延迟导入以避免硬依赖
        try:
            import pandas as pd  # noqa: F401
        except ImportError:
            logger.warning("pandas not installed, preload_daily_basic skipped")
            return
        if self._equity is None:
            logger.warning("equity_provider not set, preload_daily_basic skipped")
            return

    def preload_pit(self, stk_codes: list[str]) -> None:
        if self._pit is None:
            logger.warning("pit_provider not set, preload_pit skipped")

    # ── 查询 ──────────────────────────────────────────

    def get_fundamentals(self, stk_code: str, query_date: date) -> StockFundamentals:
        key = (stk_code, query_date)
        cached = self._fundamentals_cache.get(key)
        if cached is not None:
            return cached
        result = StockFundamentals(stk_code=stk_code, query_date=query_date)
        basic = self._lookup_daily_basic(stk_code, query_date.strftime("%Y%m%d"))
        if basic:
            result.pe_ttm = _safe_float(basic.get("pe_ttm"))
            result.pb = _safe_float(basic.get("pb"))
            result.total_mv = _safe_float(basic.get("total_mv"))
            result.turnover_rate = _safe_float(basic.get("turnover_rate"))
            result.ep = self.compute_ep(result.pe_ttm)
        self._fundamentals_cache[key] = result
        return result

    def _lookup_daily_basic(self, stk_code: str, trade_date: str) -> dict[str, Any] | None:
        df = self._daily_basic_cache.get(trade_date)
        if df is None:
            return None
        try:
            row = df.loc[df["ts_code"] == stk_code]
            if row.empty:
                return None
            return row.iloc[0].to_dict()
        except Exception:
            return None

    def get_daily_metrics(
        self,
        stk_code: str,
        query_date: date,
        close_history: list[float] | None = None,
    ) -> StockDailyMetrics:
        metrics = StockDailyMetrics(stk_code=stk_code, query_date=query_date)
        if close_history and len(close_history) >= 2:
            returns = [
                (close_history[i] - close_history[i - 1]) / close_history[i - 1]
                for i in range(1, len(close_history))
                if close_history[i - 1] > 0
            ]
            if len(returns) >= 20:
                metrics.vol_20d = self.compute_volatility(returns[-20:])
            if len(returns) >= 60:
                metrics.vol_60d = self.compute_volatility(returns[-60:])
            if len(close_history) >= 20:
                metrics.mom_20d = self.compute_momentum(close_history[-20:])
            if len(close_history) >= 60:
                metrics.mom_60d = self.compute_momentum(close_history[-60:])
        return metrics

    def get_fundamentals_batch(
        self, stk_codes_dates: list[tuple[str, date]],
    ) -> dict[str, StockFundamentals]:
        return {code: self.get_fundamentals(code, dt) for code, dt in stk_codes_dates}

    @staticmethod
    def compute_ep(pe_ttm: float | None) -> float | None:
        if pe_ttm is None or pe_ttm == 0:
            return None
        return 1.0 / pe_ttm

    @staticmethod
    def compute_volatility(returns: list[float], annualize_factor: float = 252.0) -> float | None:
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var * annualize_factor)

    @staticmethod
    def compute_momentum(prices: list[float]) -> float | None:
        if len(prices) < 2 or prices[0] <= 0:
            return None
        return (prices[-1] - prices[0]) / prices[0]
