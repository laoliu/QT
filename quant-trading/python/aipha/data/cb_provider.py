"""可转债数据提供者 — 基于 TuShare / AKShare 获取 CB 数据"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from aipha.domain.convertible_bond import (
    BondCorporateAction,
    BondStatus,
    ConversionMetrics,
    ConvertibleBond,
    ConvertibleBondBar,
    CorporateActionType,
)

logger = logging.getLogger(__name__)

# ── 评级映射 ────────────────────────────────────────────────────────

RATING_ORDER = {
    "AAA": 10, "AA+": 9, "AA": 8, "AA-": 7, "A+": 6, "A": 5,
    "A-": 4, "BBB+": 3, "BBB": 2, "BBB-": 1, "": 0,
}


def _parse_date(s: Any, fmt: str = "%Y%m%d") -> date | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if isinstance(s, (date, datetime)):
        return s if isinstance(s, date) else s.date()
    try:
        return datetime.strptime(str(s).strip()[:8], fmt).date()
    except (ValueError, TypeError):
        return None


class ConvertibleBondProvider:
    """可转债数据提供者

    支持 TuShare Pro (cb_basic / cb_daily) 和 AKShare (bond_zh_hs_cov*)。
    """

    def __init__(
        self,
        source: str = "tushare",
        token: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._source = source
        self._token = token
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._api: Any = None

        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def _get_tushare(self) -> Any:
        if self._api is None:
            import tushare as ts
            self._api = ts.pro_api(self._token)
        return self._api

    # ── 转债列表 ────────────────────────────────────────────────────

    def load_bond_list(self, status: str = "L") -> list[ConvertibleBond]:
        """获取可转债列表。status: L=上市 D=退市 P=待上市"""
        if self._source == "tushare":
            return self._load_bond_list_tushare(status)
        return self._load_bond_list_akshare()

    def _load_bond_list_tushare(self, status: str) -> list[ConvertibleBond]:
        api = self._get_tushare()
        df = api.cb_basic(list_status=status)
        if df is None or df.empty:
            return []
        bonds = []
        for _, row in df.iterrows():
            try:
                bonds.append(self._parse_tushare_bond(row))
            except Exception:
                continue
        return bonds

    def _load_bond_list_akshare(self) -> list[ConvertibleBond]:
        import akshare as ak
        df = ak.bond_zh_hs_cov_spot()
        if df is None or df.empty:
            return []
        bonds = []
        for _, row in df.iterrows():
            try:
                bonds.append(self._parse_akshare_bond(row))
            except Exception:
                continue
        return bonds

    def _parse_tushare_bond(self, row: pd.Series) -> ConvertibleBond:
        return ConvertibleBond(
            ts_code=str(row.get("ts_code", "")),
            bond_short_name=str(row.get("bond_short_name", "")),
            stk_code=str(row.get("stk_code", "")),
            stk_short_name=str(row.get("stk_short_name", "")),
            conv_price=Decimal(str(row.get("conv_price", 0))),
            conv_start_date=_parse_date(row.get("conv_start_date")) or date.today(),
            conv_end_date=_parse_date(row.get("conv_end_date")) or date.today(),
            maturity_date=_parse_date(row.get("maturity_date")) or date.today(),
            issue_size=Decimal(str(row.get("issue_size", 0))),
            list_date=_parse_date(row.get("list_date")),
            rating=str(row.get("rating", "")),
        )

    def _parse_akshare_bond(self, row: pd.Series) -> ConvertibleBond:
        code = str(row.get("代码", row.get("bond_code", "")))
        return ConvertibleBond(
            ts_code=code,
            bond_short_name=str(row.get("名称", row.get("bond_name", ""))),
            stk_code=str(row.get("正股代码", "")),
            stk_short_name=str(row.get("正股名称", "")),
            conv_price=Decimal(str(row.get("转股价", 0))),
            conv_start_date=date.today(),
            conv_end_date=date.today(),
            maturity_date=date.today(),
            issue_size=Decimal(str(row.get("发行规模", 0))),
        )

    # ── 转债行情 ────────────────────────────────────────────────────

    def load_bond_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[ConvertibleBondBar]:
        """获取可转债日线行情"""
        if self._source == "tushare":
            return self._load_bars_tushare(symbol, start, end)
        return self._load_bars_akshare(symbol, start, end)

    def _load_bars_tushare(self, symbol: str, start: date, end: date) -> list[ConvertibleBondBar]:
        api = self._get_tushare()
        df = api.cb_daily(
            ts_code=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        bars = []
        for _, r in df.sort_values("trade_date").iterrows():
            dt = datetime.strptime(str(r["trade_date"]), "%Y%m%d")
            bars.append(ConvertibleBondBar(
                symbol=symbol,
                start_ts=dt,
                end_ts=dt,
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=float(r.get("vol", 0)),
                amount=float(r.get("amount", 0)),
            ))
        return bars

    def _load_bars_akshare(self, symbol: str, start: date, end: date) -> list[ConvertibleBondBar]:
        import akshare as ak
        try:
            df = ak.bond_zh_hs_cov_daily(symbol=symbol)
        except Exception:
            return []
        if df is None or df.empty:
            return []
        df["date"] = pd.to_datetime(df["date"])
        mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
        bars = []
        for _, r in df[mask].iterrows():
            dt = r["date"].to_pydatetime()
            bars.append(ConvertibleBondBar(
                symbol=symbol, start_ts=dt, end_ts=dt,
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=float(r.get("volume", 0)),
                amount=float(r.get("amount", 0)),
            ))
        return bars

    # ── 转股指标 ────────────────────────────────────────────────────

    def compute_metrics(
        self,
        bond: ConvertibleBond,
        bond_price: float,
        stock_price: float,
        risk_free_rate: float = 0.02,
    ) -> ConversionMetrics:
        """计算转股指标"""
        conv_value = float(bond.conversion_value(Decimal(str(stock_price))))
        premium = float(bond.conversion_premium(Decimal(str(bond_price)), Decimal(str(stock_price))))
        par = float(bond.par_value)
        delta = max(0.0, min(1.0, conv_value / par)) if par > 0 else 0.0
        double_low = bond_price + premium
        years = bond.days_to_maturity / 365.0
        if years > 0 and bond_price > 0:
            ytm = (par / bond_price) ** (1.0 / years) - 1.0
        else:
            ytm = 0.0
        # 简化纯债价值（假设面值到期偿还）
        pure_bond_value = par / ((1 + risk_free_rate) ** years) if years > 0 else par

        return ConversionMetrics(
            ts_code=bond.ts_code,
            trade_date=date.today(),
            conv_value=conv_value,
            conv_premium=premium,
            delta_proxy=delta,
            double_low=double_low,
            pure_bond_value=pure_bond_value,
            ytm_estimate=ytm,
        )
