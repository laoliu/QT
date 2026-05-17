"""Shared CB daily enrichment helpers.

Provides:
- fetch_cb_daily_enriched: patch cb_value/cb_over_rate when TuShare returns NaN
- score_cb_double_low: shared double-low ranking logic for paper scripts
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CB_BASIC_CACHE: pd.DataFrame | None = None
_CB_BASIC_TS: float = 0.0
_CB_BASIC_TTL = 600


def _get_cb_basic(gw: Any) -> pd.DataFrame:
    global _CB_BASIC_CACHE, _CB_BASIC_TS
    if _CB_BASIC_CACHE is not None and (time.time() - _CB_BASIC_TS) < _CB_BASIC_TTL:
        return _CB_BASIC_CACHE
    df = gw.call(
        "cb_basic",
        fields="ts_code,stk_code,conv_price,maturity_date,list_date",
    )
    if df is None or df.empty:
        logger.error("cb_basic returned empty")
        if _CB_BASIC_CACHE is not None:
            staleness = time.time() - _CB_BASIC_TS
            logger.error(
                "cb_basic 加载失败，使用过期缓存（缓存年龄 %.0fs，数据可能已过时）",
                staleness,
            )
            return _CB_BASIC_CACHE
        return pd.DataFrame()
    df = df.dropna(subset=["conv_price", "stk_code"])
    before = len(df)
    df = df[df["conv_price"] > 0]
    if before - len(df) > 0:
        logger.info("filtered cb_basic conv_price<=0: %d", before - len(df))
    _CB_BASIC_CACHE = df
    _CB_BASIC_TS = time.time()
    logger.info("loaded cb_basic rows: %d", len(df))
    return df


def _fetch_stock_closes(gw: Any, stk_codes: list[str], trade_date: str) -> dict[str, float]:
    if not stk_codes:
        return {}
    df = gw.call("daily", trade_date=trade_date)
    if df is None or df.empty:
        return {}
    df = df[df["ts_code"].isin(stk_codes)]
    return dict(zip(df["ts_code"], df["close"]))


def fetch_cb_daily_enriched(
    gw: Any,
    trade_date: str,
    *,
    max_retries: int = 1,
) -> pd.DataFrame:
    del max_retries

    # --- 优先通过 Provider 缓存获取（§Plan Phase 2） ---
    try:
        from aipha.data.tushare_cb_provider import get_cb_provider
        provider = get_cb_provider()
        df = provider.get_daily_snapshot(trade_date)
    except Exception as exc:
        logger.warning("get_daily_snapshot 失败，降级到 gw.call: %s", exc)
        df = gw.call(
            "cb_daily",
            trade_date=trade_date,
            fields="ts_code,trade_date,close,vol,amount,cb_value,cb_over_rate",
        )
    if df is None or df.empty:
        logger.warning("cb_daily returned empty: %s", trade_date)
        return pd.DataFrame()

    if "trade_date" in df.columns:
        bad_dates = df[df["trade_date"].astype(str) != trade_date]
        if not bad_dates.empty:
            logger.warning("[%s] filtered %d rows with mismatched trade_date", trade_date, len(bad_dates))
            df = df[df["trade_date"].astype(str) == trade_date]

    for col in ("cb_value", "cb_over_rate"):
        if col not in df.columns:
            df[col] = np.nan

    df = df.dropna(subset=["close"])
    total = len(df)
    has_rate = df["cb_over_rate"].notna().sum()
    logger.info("[%s] cb_daily rows=%d cb_over_rate=%d/%d", trade_date, total, has_rate, total)

    if has_rate > total * 0.5:
        df = _patch_missing_rate(gw, df, trade_date)
        for col in ("cb_value", "cb_over_rate"):
            if col in df.columns:
                df[col] = df[col].replace([np.inf, -np.inf], np.nan)
        return df

    logger.info("[%s] cb_over_rate mostly missing, computing from cb_basic", trade_date)
    return _compute_premium_from_basic(gw, df, trade_date)


def _patch_missing_rate(gw: Any, df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    del gw
    if "cb_value" not in df.columns:
        return df
    mask = df["cb_over_rate"].isna() & df["cb_value"].notna()
    mask = mask & (df["cb_value"] > 0)
    if mask.any():
        df = df.copy()
        df.loc[mask, "cb_over_rate"] = (
            df.loc[mask, "close"] / df.loc[mask, "cb_value"] - 1
        ) * 100
        logger.info("[%s] patched cb_over_rate rows: %d", trade_date, int(mask.sum()))
    return df


def _compute_premium_from_basic(gw: Any, df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    basic = _get_cb_basic(gw)
    if basic.empty:
        logger.error("cb_basic unavailable, cannot enrich cb premium")
        return df

    df = df.copy()
    mapping = basic[["ts_code", "conv_price", "stk_code"]].copy()
    df = df.merge(mapping, on="ts_code", how="left")

    stk_codes = df["stk_code"].dropna().unique().tolist()
    stk_prices = _fetch_stock_closes(gw, stk_codes, trade_date)
    df["stock_close"] = df["stk_code"].map(stk_prices)

    valid = (
        df["conv_price"].notna()
        & (df["conv_price"] > 0)
        & df["stock_close"].notna()
        & (df["stock_close"] > 0)
    )
    df.loc[valid, "cb_value"] = (
        df.loc[valid, "stock_close"] / df.loc[valid, "conv_price"] * 100
    )
    has_cb_value = valid & df["cb_value"].notna() & (df["cb_value"] > 0)
    df.loc[has_cb_value, "cb_over_rate"] = (
        df.loc[has_cb_value, "close"] / df.loc[has_cb_value, "cb_value"] - 1
    ) * 100

    logger.info(
        "[%s] enriched cb premium rows: %d/%d",
        trade_date,
        int(has_cb_value.sum()),
        len(df),
    )

    df.drop(columns=["conv_price", "stk_code", "stock_close"], errors="ignore", inplace=True)
    for col in ("close", "vol", "amount", "cb_value", "cb_over_rate"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("cb_value", "cb_over_rate"):
        if col in df.columns:
            inf_count = (~np.isfinite(df[col].fillna(0))).sum()
            if inf_count > 0:
                logger.warning("[%s] %s contains %d inf values, converting to NaN", trade_date, col, int(inf_count))
                df[col] = df[col].replace([np.inf, -np.inf], np.nan)
    return df


def score_cb_double_low(
    gw: Any,
    trade_date: str,
    *,
    top_k: int = 10,
    max_price: float = 135.0,
    min_vol: float = 50000,
    min_days_to_maturity: int = 30,
) -> list[str]:
    from datetime import datetime as _dt

    df = fetch_cb_daily_enriched(gw, trade_date)
    if df.empty:
        logger.warning("cb_daily empty for %s", trade_date)
        return []
    if "cb_over_rate" not in df.columns:
        logger.warning("cb_over_rate missing for %s", trade_date)
        return []

    raw = len(df)
    df = df.dropna(subset=["cb_over_rate"])
    after_rate = len(df)
    df = df[df["close"] <= max_price]
    after_price = len(df)
    df = df[df["vol"] >= min_vol]
    after_vol = len(df)
    logger.info(
        "[%s] cb filters raw=%d dropna=%d price=%d vol=%d",
        trade_date,
        raw,
        after_rate,
        after_price,
        after_vol,
    )
    if after_vol == 0:
        logger.warning("[%s] no CB candidates after filtering", trade_date)
        return []

    df = df.copy()
    try:
        basic = gw.call("cb_basic", fields="ts_code,maturity_date")
        if basic is not None and not basic.empty:
            df = df.merge(basic[["ts_code", "maturity_date"]], on="ts_code", how="left")
            trade_day = _dt.strptime(trade_date, "%Y%m%d").date()
            before = len(df)

            def _days_to_maturity(raw_date: object) -> int:
                if pd.isna(raw_date) or not raw_date:
                    return 9999
                text = str(raw_date).strip()[:10]
                for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        return (_dt.strptime(text, fmt).date() - trade_day).days
                    except ValueError:
                        continue
                return 9999

            df["_dtm"] = df["maturity_date"].apply(_days_to_maturity)
            df = df[df["_dtm"] >= min_days_to_maturity]
            df = df.drop(columns=["_dtm", "maturity_date"])
            if before > len(df):
                logger.info("[CB] maturity filter removed %d bonds", before - len(df))
    except Exception as exc:
        logger.error(
            "[CB] maturity filter 异常（近到期债券可能未被过滤，存在强赎风险）: %s",
            exc,
            exc_info=True,
        )

    df["double_low"] = df["close"] + df["cb_over_rate"]
    df = df.nsmallest(top_k, "double_low")
    selected = df["ts_code"].tolist()
    logger.info("[%s] selected %d CB double-low symbols", trade_date, len(selected))
    return selected