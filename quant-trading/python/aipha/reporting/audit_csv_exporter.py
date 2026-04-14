"""审计日志 CSV 导出工具 — R56 A8 行动项。

将 JSONL 审计日志解析并导出为两类标准 CSV 附件:
1. daily_capital.csv: 日度资金链 (date, nav, cash, equity, drawdown)
2. trade_blotter.csv: 交易流水 (datetime, symbol, side, qty, price, amount, fee)

用法:
    from aipha.reporting.audit_csv_exporter import AuditCSVExporter
    exporter = AuditCSVExporter(audit_dir="logs/audit")
    exporter.export_daily_capital("output/daily_capital.csv")
    exporter.export_trade_blotter("output/trade_blotter.csv")
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _DailyCapitalRow:
    """日度资金快照。"""
    date: date
    nav: float
    cash: float
    equity: float
    drawdown: float


@dataclass
class _TradeBlotterRow:
    """交易流水条目。"""
    datetime: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    amount: float
    fee: float
    order_id: str


class AuditCSVExporter:
    """审计日志 CSV 导出器。"""

    def __init__(self, audit_dir: str | Path) -> None:
        self._audit_dir = Path(audit_dir)
        self._events: list[dict[str, Any]] = []
        self._loaded = False

    def _load_events(self) -> None:
        """加载所有 JSONL 审计日志文件。"""
        if self._loaded:
            return
        if not self._audit_dir.exists():
            logger.warning("审计日志目录不存在: %s", self._audit_dir)
            self._loaded = True
            return
        jsonl_files = sorted(self._audit_dir.glob("*.jsonl"))
        if not jsonl_files:
            logger.warning("无审计日志文件: %s", self._audit_dir)
            self._loaded = True
            return
        for f in jsonl_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                            self._events.append(evt)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning("读取审计日志失败 %s: %s", f.name, e)
        self._loaded = True
        logger.info("加载 %d 条审计事件 (from %d files)",
                     len(self._events), len(jsonl_files))

    def export_daily_capital(self, output_path: str | Path) -> int:
        """导出日度资金链 CSV。返回行数。"""
        self._load_events()
        snapshots: dict[str, _DailyCapitalRow] = {}
        peak_nav = 0.0
        for evt in self._events:
            if evt.get("event_type") != "ACCOUNT_SNAPSHOT":
                continue
            data = evt.get("data", {})
            ts = evt.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                d = dt.date()
            except (ValueError, TypeError):
                continue
            nav = float(data.get("nav", data.get("total_value", 0)))
            cash = float(data.get("cash", data.get("available", 0)))
            equity = nav - cash
            peak_nav = max(peak_nav, nav)
            dd = (peak_nav - nav) / peak_nav if peak_nav > 0 else 0.0
            ds = d.isoformat()
            snapshots[ds] = _DailyCapitalRow(
                date=d, nav=nav, cash=cash, equity=equity, drawdown=dd,
            )
        if not snapshots:
            logger.warning("无 ACCOUNT_SNAPSHOT 事件，日度资金链为空")
            return 0
        rows = sorted(snapshots.values(), key=lambda r: r.date)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "nav", "cash", "equity", "drawdown"])
            for r in rows:
                w.writerow([r.date.isoformat(), f"{r.nav:.2f}",
                            f"{r.cash:.2f}", f"{r.equity:.2f}",
                            f"{r.drawdown:.4f}"])
        logger.info("日度资金链已导出: %s (%d 行)", out, len(rows))
        return len(rows)

    def export_trade_blotter(self, output_path: str | Path) -> int:
        """导出交易流水 CSV。返回行数。"""
        self._load_events()
        trades: list[_TradeBlotterRow] = []
        fill_types = {"ORDER_FILLED", "ORDER_PARTIAL_FILLED"}
        for evt in self._events:
            if evt.get("event_type") not in fill_types:
                continue
            data = evt.get("data", {})
            ts = evt.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            symbol = evt.get("symbol", data.get("symbol", ""))
            side = data.get("side", data.get("direction", ""))
            qty = float(data.get("quantity", data.get("filled_qty", 0)))
            price = float(data.get("price", data.get("fill_price", 0)))
            amount = qty * price
            fee = float(data.get("fee", data.get("commission", 0)))
            oid = evt.get("order_id", data.get("order_id", ""))
            trades.append(_TradeBlotterRow(
                datetime=dt, symbol=symbol, side=side,
                quantity=qty, price=price, amount=amount,
                fee=fee, order_id=str(oid),
            ))
        if not trades:
            logger.warning("无 ORDER_FILLED 事件，交易流水为空")
            return 0
        trades.sort(key=lambda r: r.datetime)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["datetime", "symbol", "side", "quantity",
                         "price", "amount", "fee", "order_id"])
            for r in trades:
                w.writerow([r.datetime.isoformat(), r.symbol, r.side,
                            f"{r.quantity:.0f}", f"{r.price:.4f}",
                            f"{r.amount:.2f}", f"{r.fee:.2f}", r.order_id])
        logger.info("交易流水已导出: %s (%d 行)", out, len(trades))
        return len(trades)

    def export_all(self, output_dir: str | Path) -> dict[str, int]:
        """导出所有 CSV 附件到指定目录。"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        result = {
            "daily_capital": self.export_daily_capital(out / "daily_capital.csv"),
            "trade_blotter": self.export_trade_blotter(out / "trade_blotter.csv"),
        }
        logger.info("审计CSV导出完成: %s", result)
        return result
