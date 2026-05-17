"""force_redeem_watcher.py — 强赎公告 T+0 实时监控（H4）

职责：
  - 定期（默认 5 分钟）查询 TushareCbProvider.load_cb_call() 中的当日/最新强赎公告
  - 首次发现新公告时通过 FileAuditNotifier（必选）+ 可选通知渠道发出告警
  - 支持 check_once()（单次检查）与 start_polling()（持续轮询）两种模式

与现有架构的关系：
  - 数据层：TushareCbProvider（src/aipha/data/tushare_cb_provider.py）
  - 告警层：AlertLevel / FileAuditNotifier / LogNotifier（src/aipha/monitor/alerts.py）
  - 不依赖任何撮合 / 持仓 / 账户模块；只读数据 + 发告警
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aipha.data.tushare_cb_provider import TushareCbProvider
    from aipha.monitor.alerts import AlertNotifier

logger = logging.getLogger(__name__)

# ── is_call 关键词 ────────────────────────────────────────────────────────────
# 公告实施强赎 → CRITICAL
IMPLEMENTED_KEYWORDS: frozenset[str] = frozenset(["公告实施强赎", "强赎执行"])
# 满足条件 / 提示 → WARNING
CONDITION_MET_KEYWORDS: frozenset[str] = frozenset(["已满足强赎条件", "公告提示强赎"])
# 公告不强赎 → 可以忽略或 INFO
NO_REDEEM_KEYWORDS: frozenset[str] = frozenset(["公告不强赎"])


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RedeemEvent:
    """一条强赎公告记录（去重键由 ts_code + ann_date + is_call 决定）。"""

    ts_code: str
    call_type: str       # "强赎" | "到赎"
    is_call: str         # 公告内容描述
    ann_date: str        # 公告日 YYYYMMDD
    call_date: str       # 强赎日 YYYYMMDD（可能为空）
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

    @property
    def is_no_redeem(self) -> bool:
        return any(kw in self.is_call for kw in NO_REDEEM_KEYWORDS)


# ── 主类 ──────────────────────────────────────────────────────────────────────

class ForceRedeemWatcher:
    """强赎公告 T+0 监控器。

    参数
    ----
    provider:
        TushareCbProvider 实例（必须）。
    notifiers:
        告警通知渠道列表。若为空，使用默认 LogNotifier + FileAuditNotifier。
    poll_interval_sec:
        轮询间隔秒数，默认 300（5 分钟）。
    lookback_days:
        查询最近 N 天的公告（防止盘前首次运行遗漏昨日公告），默认 1。
    audit_dir:
        FileAuditNotifier 的落盘目录，默认 logs/audit/。
    include_no_redeem:
        是否对"公告不强赎"也产生 INFO 告警，默认 False。
    """

    def __init__(
        self,
        provider: "TushareCbProvider",
        notifiers: list["AlertNotifier"] | None = None,
        poll_interval_sec: int = 300,
        lookback_days: int = 1,
        audit_dir: str | Path | None = None,
        include_no_redeem: bool = False,
    ) -> None:
        self._provider = provider
        self._poll_interval = poll_interval_sec
        self._lookback_days = lookback_days
        self._include_no_redeem = include_no_redeem

        # 已处理的去重键（内存级别；进程重启后重置，不影响安全）
        self._seen: set[tuple[str, str, str]] = set()

        # 初始化通知链
        if notifiers is not None:
            self._notifiers: list["AlertNotifier"] = notifiers
        else:
            self._notifiers = self._build_default_notifiers(audit_dir)

        logger.info(
            "ForceRedeemWatcher 初始化完成 (poll_interval=%ds, lookback=%dd)",
            poll_interval_sec,
            lookback_days,
        )

    # ── 通知器构建 ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_default_notifiers(audit_dir: str | Path | None) -> list["AlertNotifier"]:
        from aipha.monitor.alerts import FileAuditNotifier, LogNotifier

        notifiers: list["AlertNotifier"] = [LogNotifier()]

        if audit_dir is None:
            _repo = Path(__file__).resolve().parents[3]
            audit_dir = _repo / "logs" / "audit"

        Path(audit_dir).mkdir(parents=True, exist_ok=True)
        notifiers.append(FileAuditNotifier(str(audit_dir)))
        return notifiers

    # ── 数据查询 ─────────────────────────────────────────────────────────────

    def _fetch_recent_events(self) -> list[RedeemEvent]:
        """查询最近 lookback_days 天内的强赎公告，返回 RedeemEvent 列表。"""
        try:
            df = self._provider.load_cb_call(use_cache=False)
        except Exception as exc:
            logger.error("load_cb_call 失败: %s", exc)
            return []

        if df is None or df.empty:
            return []

        cutoff = (date.today() - timedelta(days=self._lookback_days)).strftime("%Y%m%d")
        df = df[df["ann_date"].fillna("") >= cutoff]
        df = df[df["call_type"] == "强赎"]

        events = []
        for _, row in df.iterrows():
            events.append(RedeemEvent(
                ts_code=str(row.get("ts_code", "")),
                call_type=str(row.get("call_type", "")),
                is_call=str(row.get("is_call", "")),
                ann_date=str(row.get("ann_date", "")),
                call_date=str(row.get("call_date", "") or ""),
                bond_short_name=str(row.get("bond_short_name", "") or ""),
            ))
        return events

    # ── 告警分发 ─────────────────────────────────────────────────────────────

    def _dispatch(self, event: RedeemEvent) -> None:
        """根据 is_call 类型选择级别并分发到所有通知器。"""
        from aipha.monitor.alerts import Alert, AlertLevel, AlertStatus

        if event.is_no_redeem:
            if not self._include_no_redeem:
                return
            level = AlertLevel.INFO
            title = f"[强赎撤回] {event.bond_short_name or event.ts_code}"
        elif event.is_implemented:
            level = AlertLevel.CRITICAL
            title = f"[实施强赎] {event.bond_short_name or event.ts_code}"
        elif event.is_condition_met:
            level = AlertLevel.WARNING
            title = f"[满足强赎条件] {event.bond_short_name or event.ts_code}"
        else:
            level = AlertLevel.INFO
            title = f"[强赎提示] {event.bond_short_name or event.ts_code}"

        message = (
            f"代码: {event.ts_code}\n"
            f"类型: {event.call_type}\n"
            f"状态: {event.is_call}\n"
            f"公告日: {event.ann_date}\n"
            f"强赎日: {event.call_date or '未确定'}\n"
        )

        alert = Alert(
            id=f"force_redeem_{event.ts_code}_{event.ann_date}_{int(time.time())}",
            level=level,
            title=title,
            message=message,
            source="ForceRedeemWatcher",
            status=AlertStatus.ACTIVE,
            metadata={
                "ts_code": event.ts_code,
                "ann_date": event.ann_date,
                "is_call": event.is_call,
                "call_date": event.call_date,
                "source": "TushareCbProvider.load_cb_call",
            },
        )

        for notifier in self._notifiers:
            try:
                notifier.send(alert)
            except Exception as exc:
                logger.error("通知器 %s 失败: %s", notifier, exc)

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    def check_once(self) -> list[RedeemEvent]:
        """执行单次检查，对新发现的事件触发告警。

        返回本次告警的新事件列表（空列表表示无新发现）。
        """
        events = self._fetch_recent_events()
        new_events: list[RedeemEvent] = []

        for ev in events:
            if ev.dedup_key in self._seen:
                continue
            self._seen.add(ev.dedup_key)
            self._dispatch(ev)
            new_events.append(ev)
            logger.info(
                "新강赎公告: %s | %s | ann_date=%s",
                ev.ts_code, ev.is_call, ev.ann_date,
            )

        if not new_events:
            logger.debug("check_once: 无新强赎公告")

        return new_events

    def start_polling(self, max_iterations: int | None = None) -> None:
        """持续轮询模式（阻塞）。

        参数
        ----
        max_iterations:
            最大循环次数（None = 无限）。仅用于测试。
        """
        logger.info(
            "ForceRedeemWatcher 开始轮询 (interval=%ds)",
            self._poll_interval,
        )
        iteration = 0
        while True:
            try:
                new = self.check_once()
                if new:
                    logger.info("本次轮询发现 %d 条新公告", len(new))
            except Exception as exc:
                logger.error("轮询异常 (iteration=%d): %s", iteration, exc)

            iteration += 1
            if max_iterations is not None and iteration >= max_iterations:
                break

            time.sleep(self._poll_interval)

    def reset_seen(self) -> None:
        """清空已见集合（用于测试或跨日重置）。"""
        self._seen.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen)
