"""TuShare-only inputs for unattended research Paper operation.

Daily financial requests have an explicit end date, so yesterday's bounded
cache cannot hide today's disclosures. Market iteration uses the fetched SSE
calendar, never the separate legacy weekday/calendar fallback.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

import pandas as pd

from aipha.data.tushare_equity_provider import TuShareEquityProvider
from aipha.universe.historical import normalize_trade_calendar


class OperatingEquityProvider(TuShareEquityProvider):
    def __init__(self, *, cache_dir: str, archive_dir: str, calendar_end: str):
        self.archive_dir = Path(archive_dir)
        super().__init__(cache_dir=cache_dir, max_retries=3)
        self.calendar = super().load_trade_calendar("19901219", calendar_end)
        self.calendar = normalize_trade_calendar(self.calendar, "19901219", calendar_end)
        self._names = None

    def _atomic_write_parquet(self, frame, path):
        # Updating a shared raw cache must not discard the previous capture.
        path = Path(path)
        if path.is_file():
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            backup = self.archive_dir / digest / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
        super()._atomic_write_parquet(frame, path)

    def _sessions(self, start, end):
        calendar = normalize_trade_calendar(self.calendar, start, end)
        return calendar.loc[calendar.is_open.eq(1), "cal_date"].dt.strftime("%Y%m%d").tolist()

    def load_trade_calendar(self, start_date, end_date, use_cache=True):
        return normalize_trade_calendar(self.calendar, start_date, end_date)

    def load_daily_bars_range(self, start_date, end_date, use_cache=True, **kwargs):
        return pd.concat([self.load_daily_bars(d, use_cache=use_cache)
                          for d in self._sessions(start_date, end_date)], ignore_index=True)

    def load_daily_basic_range(self, start_date, end_date, use_cache=True):
        return pd.concat([self.load_daily_basic(d, use_cache=use_cache)
                          for d in self._sessions(start_date, end_date)], ignore_index=True)

    def _daily_table(self, prefix, endpoint, fields, start_date, end_date, use_cache=True):
        rows = []
        for day in self._sessions(start_date, end_date):
            path = self.cache_dir / f"{prefix}_{day}.parquet"
            # Adjustment factors also have existing monthly research caches.
            month = self.cache_dir / f"{prefix}_{day[:6]}.parquet"
            frame = self._safe_read_parquet(path) if use_cache and path.exists() else None
            if frame is None and use_cache and month.exists():
                cached = self._safe_read_parquet(month)
                if cached is not None:
                    frame = cached.loc[cached.trade_date.astype(str).eq(day)]
            if frame is None or frame.empty:
                frame = self._retry_call(getattr(self.api, endpoint), trade_date=day, fields=fields)
                self._validate_daily(frame, endpoint, day)
                self._atomic_write_parquet(frame, path)
            self._validate_daily(frame, endpoint, day)
            rows.append(frame)
        return pd.concat(rows, ignore_index=True)

    @staticmethod
    def _validate_daily(frame, endpoint, day):
        if (frame is None or frame.empty or not {"trade_date", "ts_code"} <= set(frame)
                or not frame.trade_date.astype(str).eq(day).all()
                or frame.ts_code.isna().any() or frame.ts_code.duplicated().any()):
            raise ValueError(f"incomplete {endpoint} response for {day}")

    def load_adj_factor_bulk(self, start_date, end_date, use_cache=True):
        return self._daily_table("adj_factor", "adj_factor", "ts_code,trade_date,adj_factor", start_date, end_date, use_cache)

    def load_limit_prices_range(self, start_date, end_date, use_cache=True):
        return self._daily_table("stk_limit", "stk_limit", "ts_code,trade_date,up_limit,down_limit", start_date, end_date, use_cache)

    def load_namechange(self, ts_code=None, use_cache=True):
        # Refresh all pages once per daily process; a permanent per-symbol
        # cache would silently miss a newly effective ST name.
        if self._names is None:
            parts, seen = [], set()
            for offset in range(0, 100000, 5000):
                frame = self._retry_call(self.api.namechange, fields="ts_code,name,start_date,end_date,ann_date,change_reason",
                                         limit=5000, offset=offset)
                digest = hashlib.sha256(frame.reset_index(drop=True).to_json(orient="split", date_format="iso").encode()).hexdigest()
                if not frame.empty:
                    if digest in seen:
                        raise ValueError("namechange pagination repeated a page")
                    seen.add(digest)
                    parts.append(frame)
                if len(frame) < 5000:
                    break
            else:
                raise ValueError("namechange pagination did not terminate")
            if not parts:
                raise ValueError("namechange refresh returned no history")
            self._names = pd.concat(parts, ignore_index=True).drop_duplicates()
            for key in ("start_date", "end_date", "ann_date"):
                self._names[key] = pd.to_datetime(self._names[key], format="%Y%m%d", errors="coerce")
        return self._names if ts_code is None else self._names.loc[self._names.ts_code.eq(ts_code)]

    def load_sw_industry_history(self, level="L1", use_cache=True):
        return super().load_sw_industry_history(level=level, use_cache=False)

    def load_stock_basic(self, use_cache=True):
        return super().load_stock_basic(use_cache=False)
