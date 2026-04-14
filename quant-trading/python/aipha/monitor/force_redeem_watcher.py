"""强赎公告 T+0 实时监控"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

IMPLEMENTED_KEYWORDS = frozenset(["公告实施强赎", "强赎执行"])
CONDITION_MET_KEYWORDS = frozenset(["已满足强赎条件", "公告提示强赎"])
NO_REDEEM_KEYWORDS = frozenset(["公告不强赎"])


@dataclass(frozen=True)
class RedeemEvent:
    ts_code: str
    call_type: str
    is_call: str
    ann_date: str
    call_date: str
    bond_short_name: str = ""

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        return (self.ts_code, self.ann_date, self.is_call)

    @property
    def is_implemented(self) -> bool:
        return any(kw in self.is_call for kw in IMPLEMENTED_KEYWORDS)

    @property
    def is_condition_met(self) -> bool:
        return any(kw in self.is_call for kw in CONDITION_MET_KEYWORDS)


class ForceRedeemWatcher:
    """强赎公告监控器

    轮询数据源获取最新强赎公告, 触发告警通知。
    """

    def __init__(
        self,
        provider: Any = None,
        notifiers: list | None = None,
        poll_interval_sec: int = 300,
        lookback_days: int = 1,
    ):
        self._provider = provider
        self._poll_interval = poll_interval_sec
        self._lookback_days = lookback_days
        self._seen: set[tuple[str, str, str]] = set()
        self._notifiers = notifiers or []

    def check_once(self) -> list[RedeemEvent]:
        """执行一次检查, 返回新增强赎事件"""
        if self._provider is None:
            return []

        new_events = []
        try:
            today = date.today()
            events = self._fetch_events(today)
            for ev in events:
                if ev.dedup_key not in self._seen:
                    self._seen.add(ev.dedup_key)
                    new_events.append(ev)
                    self._notify(ev)
        except Exception as e:
            logger.error("强赎公告查询失败: %s", e)

        return new_events

    def _fetch_events(self, target_date: date) -> list[RedeemEvent]:
        """从数据源获取强赎事件"""
        try:
            if hasattr(self._provider, "load_cb_call"):
                df = self._provider.load_cb_call()
                if df is None or df.empty:
                    return []
                events = []
                for _, row in df.iterrows():
                    events.append(RedeemEvent(
                        ts_code=row.get("ts_code", ""),
                        call_type=row.get("call_type", ""),
                        is_call=row.get("is_call", ""),
                        ann_date=row.get("ann_date", ""),
                        call_date=row.get("call_date", ""),
                        bond_short_name=row.get("bond_short_name", ""),
                    ))
                return events
        except Exception as e:
            logger.warning("load_cb_call 失败: %s", e)
        return []

    def _notify(self, event: RedeemEvent) -> None:
        level = "CRITICAL" if event.is_implemented else "WARNING"
        msg = f"[{level}] 强赎公告: {event.bond_short_name}({event.ts_code}) — {event.is_call}"
        logger.warning(msg)

        for notifier in self._notifiers:
            try:
                from aipha.monitor.alerts import Alert, AlertLevel
                alert = Alert(
                    id=f"force_redeem_{event.ts_code}_{event.ann_date}",
                    level=AlertLevel.CRITICAL if event.is_implemented else AlertLevel.WARNING,
                    title="强赎公告",
                    message=msg,
                    source="ForceRedeemWatcher",
                )
                notifier.send(alert)
            except Exception:
                pass

    @property
    def seen_count(self) -> int:
        return len(self._seen)
