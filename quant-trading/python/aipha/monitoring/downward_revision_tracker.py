"""可转债下修事件追踪器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from aipha.domain.convertible_bond import BondCorporateAction, CorporateActionType

_REQUIRED_COLUMNS = {
    "ts_code", "bond_short_name", "publish_date", "change_date",
    "convertprice_bef", "convertprice_aft", "revision_pct",
}


@dataclass(frozen=True, slots=True)
class DownwardRevisionStatus:
    ts_code: str
    has_revision: bool
    event_count: int = 0
    latest_publish_date: str | None = None
    latest_change_date: str | None = None
    latest_revision_pct: float | None = None
    latest_old_price: float | None = None
    latest_new_price: float | None = None


class DownwardRevisionTracker:
    """基于下修事件 DataFrame 提供 PIT 安全查询。"""

    def __init__(self, events: pd.DataFrame | None = None) -> None:
        base = events if events is not None else pd.DataFrame(columns=sorted(_REQUIRED_COLUMNS))
        self._events = self._normalize(base)

    def set_events(self, events: pd.DataFrame) -> None:
        self._events = self._normalize(events)

    def build_lookup(self, as_of_date: str | None = None) -> dict[str, dict]:
        df = self._filter_as_of(as_of_date)
        if df.empty:
            return {}
        latest = df.groupby("ts_code", as_index=False).tail(1)
        return {
            row["ts_code"]: {
                "bond_short_name": row["bond_short_name"],
                "publish_date": row["publish_date"],
                "change_date": row["change_date"],
                "revision_pct": float(row["revision_pct"]),
                "old_conv_price": float(row["convertprice_bef"]),
                "new_conv_price": float(row["convertprice_aft"]),
            }
            for _, row in latest.iterrows()
        }

    def get_status(
        self,
        ts_code: str,
        as_of_date: str | None = None,
        lookback_days: int = 365,
    ) -> DownwardRevisionStatus:
        df = self._filter_as_of(as_of_date)
        code_df = df[df["ts_code"] == ts_code]
        if code_df.empty:
            return DownwardRevisionStatus(ts_code=ts_code, has_revision=False)
        end_date = self._to_date(as_of_date) or code_df["publish_dt"].max().date()
        recent_start = end_date - timedelta(days=lookback_days)
        recent_count = int((code_df["publish_dt"].dt.date >= recent_start).sum())
        latest = code_df.iloc[-1]
        return DownwardRevisionStatus(
            ts_code=ts_code,
            has_revision=True,
            event_count=recent_count,
            latest_publish_date=latest["publish_date"],
            latest_change_date=latest["change_date"],
            latest_revision_pct=float(latest["revision_pct"]),
            latest_old_price=float(latest["convertprice_bef"]),
            latest_new_price=float(latest["convertprice_aft"]),
        )

    def get_recent_actions(
        self,
        as_of_date: str | None = None,
        lookback_days: int = 365,
        ts_code: str | None = None,
    ) -> list[BondCorporateAction]:
        df = self._filter_as_of(as_of_date)
        if ts_code is not None:
            df = df[df["ts_code"] == ts_code]
        if df.empty:
            return []
        end_date = self._to_date(as_of_date) or df["publish_dt"].max().date()
        recent_start = end_date - timedelta(days=lookback_days)
        df = df[df["publish_dt"].dt.date >= recent_start]
        actions: list[BondCorporateAction] = []
        for _, row in df.iterrows():
            actions.append(BondCorporateAction(
                ts_code=row["ts_code"],
                action_type=CorporateActionType.CONVERSION_PRICE_DOWN,
                announce_date=self._to_date(row["publish_date"]),
                effective_date=self._to_date(row["change_date"]),
                old_value=Decimal(str(row["convertprice_bef"])),
                new_value=Decimal(str(row["convertprice_aft"])),
                memo=f"下修 {float(row['revision_pct']):.2%}",
            ))
        return actions

    def _filter_as_of(self, as_of_date: str | None) -> pd.DataFrame:
        if as_of_date is None:
            return self._events
        cutoff = pd.Timestamp(self._to_date(as_of_date))
        return self._events[self._events["publish_dt"] <= cutoff]

    def _normalize(self, events: pd.DataFrame) -> pd.DataFrame:
        missing = _REQUIRED_COLUMNS - set(events.columns)
        if missing:
            raise ValueError(f"missing downward revision columns: {sorted(missing)}")
        df = events.copy()
        df["publish_date"] = df["publish_date"].astype(str).str[:8]
        df["change_date"] = df["change_date"].astype(str).str[:8]
        df["publish_dt"] = pd.to_datetime(df["publish_date"], format="%Y%m%d", errors="coerce")
        df["change_dt"] = pd.to_datetime(df["change_date"], format="%Y%m%d", errors="coerce")
        for col in ("convertprice_bef", "convertprice_aft", "revision_pct"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["publish_dt", "change_dt", "convertprice_bef", "convertprice_aft", "revision_pct"])
        return df.sort_values(["publish_dt", "change_dt", "ts_code"]).reset_index(drop=True)

    @staticmethod
    def _to_date(value: str | date) -> date:
        if isinstance(value, date):
            return value
        return pd.Timestamp(str(value)[:8]).date()


# ---------------------------------------------------------------------------
# P0.3 — 监控工厂函数（PSI/decay 监控注册入口）
# ---------------------------------------------------------------------------

def make_revision_score_decay_monitor(
    window: int = 60,
    icir_threshold: float = 0.30,
) -> "HalflifeDecayMonitor":
    """返回预配置的下修评分因子衰减监控器（P0.3 drift/decay 集成入口）。

    用法::

        from aipha.monitoring.downward_revision_tracker import make_revision_score_decay_monitor
        monitor = make_revision_score_decay_monitor()
        for date_str, ic_val in ic_series:
            monitor.update(date_str, ic_val)
        snap = monitor.get_halflife_snapshot()

    Args:
        window: 滚动 ICIR 计算窗口（默认 60 期）
        icir_threshold: 衰减预警阈值（近期 ICIR 低于此值触发告警）

    Returns:
        HalflifeDecayMonitor，已命名为 "cb_downward_revision_score"
    """
    from aipha.monitoring.factor_decay_monitor import HalflifeDecayMonitor  # 延迟 import 避免循环
    return HalflifeDecayMonitor(
        factor_name="cb_downward_revision_score",
        window=window,
        icir_threshold=icir_threshold,
    )
