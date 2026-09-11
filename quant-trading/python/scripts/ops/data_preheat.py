#!/usr/bin/env python3
"""行情缓存预热（cron 盘前）

盘前把当天要用的行情缓存拉好，避免开盘后策略首次取数时才发现 TuShare
限流/超时 —— 那个时点重试的代价是**错过开盘**，而盘前重试只是慢几分钟。

覆盖三条缓存：

| 检查项          | 缓存目录                      | 文件前缀        |
|-----------------|-------------------------------|-----------------|
| equity_daily    | data/cache/equity_tushare     | ``daily_``      |
| daily_basic     | data/cache/equity_tushare     | ``daily_basic_``|
| cb_daily        | data/cache/cb_tushare         | ``cb_daily_``   |

单位口径不在这里换算：各 provider 写进缓存的就是它们各自的原生单位
（``cb_daily.amount`` 万元；TuShare ``daily.amount`` 千元），预热只负责
把文件拉下来，换算仍由下游读取方负责。这里多做一次换算会导致同一份缓存
被两套口径解释。

注意 ``TuShareEquityProvider.get_daily_snapshot()`` 的设计是**无限重试**
（缓存优先、永不放弃），所以 cron 条目要在调度层加超时，
见 ``docker/paper/crontab`` 里的 ``timeout``。

用法：
    python scripts/ops/data_preheat.py --dry-run
    python scripts/ops/data_preheat.py
    python scripts/ops/data_preheat.py --only cb_daily --max-days 3
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aipha.utils.paths import LOGS_DIR, PROJECT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

EQUITY_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "equity_tushare"
CB_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "cb_tushare"

# 审计日志目录（测试会 patch 这个模块级变量）
_AUDIT_DIR = LOGS_DIR / "preheat"

# 缓存落后多少个交易日才算需要更新。默认 1：当天盘中缓存最新到「上一个
# 交易日」是正常的（今天还没收盘），落后 2 个交易日以上才是真的缺数据。
_MAX_STALE_TRADING_DAYS = 1

# 单次预热最多补几个交易日 —— 缺了半年的历史不该由盘前 cron 顺手补，
# 那是回填任务（scripts/backfill_*）的事，在盘前拉半年数据只会拖到开盘。
_DEFAULT_MAX_DAYS = 5

_calendar_cache: Any = None


def _get_calendar() -> Any:
    """惰性加载交易日历（失败返回 None，由调用方降级）。"""
    global _calendar_cache
    if _calendar_cache is None:
        try:
            from aipha.data.calendar import CNStockCalendar

            _calendar_cache = CNStockCalendar()
        except Exception as exc:  # noqa: BLE001 - 日历不可用要能降级
            logger.warning("交易日历加载失败，退回自然日规则: %s", exc)
            _calendar_cache = False
    return _calendar_cache or None


def _get_latest_cache_date(
    cache_dir: str | Path,
    prefix: str,
    exclude_prefixes: tuple[str, ...] = (),
) -> date | None:
    """返回缓存目录里最新的 ``trade_date``；没有可读数据时 None。

    日期取自 parquet 的 ``trade_date`` **列**而不是文件名：月度文件
    （``daily_basic_202604.parquet``）的文件名只到月份，按文件名判断会
    把「4 月只拉了 2 天」当成整个 4 月都齐了。

    Args:
        cache_dir: 缓存目录
        prefix: 文件名前缀，如 ``daily_``
        exclude_prefixes: 需要排除的更长前缀，如 ``("daily_basic_",)``
            —— ``daily_*`` 会同时匹配到 ``daily_basic_*``
    """
    d = Path(cache_dir)
    if not d.is_dir():
        return None

    latest: date | None = None
    for f in sorted(d.glob(f"{prefix}*.parquet")):
        if any(f.name.startswith(ex) for ex in exclude_prefixes):
            continue
        value = _max_trade_date(f)
        if value is None:
            continue
        if latest is None or value > latest:
            latest = value
    return latest


def _max_trade_date(path: Path) -> date | None:
    """读单个 parquet 的 trade_date 列最大值。读不了就跳过，不抛。"""
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["trade_date"])
    except Exception as exc:  # noqa: BLE001 - 坏文件不能让整个预热失败
        logger.warning("读取 %s 失败，跳过: %s", path.name, exc)
        return None

    if df.empty:
        return None

    values: list[date] = []
    for raw in df["trade_date"].dropna().unique():
        parsed = _parse_trade_date(raw)
        if parsed is not None:
            values.append(parsed)
    return max(values) if values else None


def _parse_trade_date(raw: Any) -> date | None:
    """把 ``20260402`` / ``2026-04-02`` / Timestamp 统一成 date。"""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _needs_update(
    latest: date | None,
    max_stale_trading_days: int = _MAX_STALE_TRADING_DAYS,
    today: date | None = None,
) -> bool:
    """判断缓存是否需要更新。

    按**交易日**而不是自然日计算落后程度：春节 / 国庆连休 7~9 个自然日，
    按自然日判断会在每个长假后误报「数据陈旧」，天天误报的监控等于没有
    监控。日历不可用时才退回自然日规则（>5 天算陈旧）。
    """
    if latest is None:
        return True

    ref = today or date.today()
    if latest >= ref:
        return False

    cal = _get_calendar()
    if cal is None:
        return (ref - latest).days > 5

    try:
        from datetime import timedelta

        elapsed = cal.get_trading_days(latest + timedelta(days=1), ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("交易日历查询失败，退回自然日规则: %s", exc)
        return (ref - latest).days > 5

    return len(elapsed) > max_stale_trading_days


def _pending_trading_days(
    latest: date | None,
    max_days: int,
    today: date | None = None,
) -> list[str]:
    """列出需要补的交易日（``YYYYMMDD``，升序，最多 max_days 个）。"""
    ref = today or date.today()
    cal = _get_calendar()
    if cal is None:
        return [ref.strftime("%Y%m%d")]

    from datetime import timedelta

    start = (latest + timedelta(days=1)) if latest else (
        ref - timedelta(days=max_days * 3)
    )
    try:
        days = cal.get_trading_days(start, ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("交易日历查询失败: %s", exc)
        return [ref.strftime("%Y%m%d")]

    # 只补最近的 max_days 个交易日：更早的缺口交给回填脚本
    return [d.strftime("%Y%m%d") for d in days[-max_days:]]


# ---------------------------------------------------------------------------
# 三条预热流程
# ---------------------------------------------------------------------------


def preheat_equity_daily(
    cache_dir: str | Path = EQUITY_CACHE_DIR,
    dry_run: bool = False,
    max_days: int = _DEFAULT_MAX_DAYS,
) -> dict[str, Any]:
    """预热 A 股全市场日线快照缓存（``daily_YYYYMMDD.parquet``）。"""
    latest = _get_latest_cache_date(
        cache_dir, "daily_", exclude_prefixes=("daily_basic_",),
    )
    return _run_preheat(
        check="equity_daily",
        latest=latest,
        dry_run=dry_run,
        max_days=max_days,
        fetch=lambda day: _fetch_equity_daily(cache_dir, day),
    )


def preheat_daily_basic(
    cache_dir: str | Path = EQUITY_CACHE_DIR,
    dry_run: bool = False,
    max_days: int = _DEFAULT_MAX_DAYS,
) -> dict[str, Any]:
    """预热每日指标缓存（``daily_basic_YYYYMM.parquet``）。"""
    latest = _get_latest_cache_date(cache_dir, "daily_basic_")
    return _run_preheat(
        check="daily_basic",
        latest=latest,
        dry_run=dry_run,
        max_days=max_days,
        fetch=lambda day: _fetch_daily_basic(cache_dir, day),
    )


def preheat_cb_daily(
    cache_dir: str | Path = CB_CACHE_DIR,
    dry_run: bool = False,
    max_days: int = _DEFAULT_MAX_DAYS,
) -> dict[str, Any]:
    """预热可转债日行情缓存（``cb_daily_YYYYMMDD.parquet``）。"""
    latest = _get_latest_cache_date(cache_dir, "cb_daily_")
    return _run_preheat(
        check="cb_daily",
        latest=latest,
        dry_run=dry_run,
        max_days=max_days,
        fetch=lambda day: _fetch_cb_daily(cache_dir, day),
    )


def _run_preheat(
    check: str,
    latest: date | None,
    dry_run: bool,
    max_days: int,
    fetch: Any,
) -> dict[str, Any]:
    """三条流程的公共骨架：判定 → （dry-run 就此返回）→ 逐日补齐。"""
    latest_str = latest.isoformat() if latest else None

    if not _needs_update(latest):
        return {
            "check": check,
            "status": "OK",
            "latest": latest_str,
            "msg": f"缓存已最新（{latest_str}）",
        }

    days = _pending_trading_days(latest, max_days)
    if dry_run:
        return {
            "check": check,
            "status": "NEED_UPDATE",
            "latest": latest_str,
            "pending": days,
            "msg": (
                f"缓存最新到 {latest_str or '（无数据）'}，"
                f"待补 {len(days)} 个交易日"
            ),
        }

    fetched: list[str] = []
    failed: list[str] = []
    for day in days:
        try:
            fetch(day)
        except Exception as exc:  # noqa: BLE001 - 单日失败不该中断其余日期
            logger.error("%s %s 拉取失败: %s", check, day, exc)
            failed.append(day)
            continue
        fetched.append(day)

    return {
        "check": check,
        "status": "FAIL" if failed else "UPDATED",
        "latest": latest_str,
        "fetched": fetched,
        "failed": failed,
        "msg": (
            f"补齐 {len(fetched)} 个交易日"
            + (f"，失败 {len(failed)} 个: {','.join(failed)}" if failed else "")
        ),
    }


def _fetch_equity_daily(cache_dir: str | Path, trade_date: str) -> None:
    from aipha.data.tushare_equity_provider import TuShareEquityProvider

    provider = TuShareEquityProvider(cache_dir=str(cache_dir))
    provider.get_daily_snapshot(trade_date)


def _fetch_daily_basic(cache_dir: str | Path, trade_date: str) -> None:
    from aipha.data.tushare_equity_provider import TuShareEquityProvider

    provider = TuShareEquityProvider(cache_dir=str(cache_dir))
    provider.load_daily_basic(trade_date)


def _fetch_cb_daily(cache_dir: str | Path, trade_date: str) -> None:
    from aipha.data.tushare_cb_provider import TuShareCBProvider

    provider = TuShareCBProvider(cache_dir=str(cache_dir))
    provider.load_cb_daily(trade_date=trade_date)


# ---------------------------------------------------------------------------
# 审计与告警
# ---------------------------------------------------------------------------


def _write_audit(rows: list[dict[str, Any]]) -> Path | None:
    """把本次预热结果按行写入审计 jsonl，返回文件路径。

    审计文件是事后追查「那天到底有没有预热成功」的唯一依据，
    所以写失败只告警、不抛异常 —— 但也绝不静默跳过。
    """
    audit_dir = Path(_AUDIT_DIR)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = audit_dir / f"data_preheat_{ts}.jsonl"
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                payload = dict(row)
                payload.setdefault("ts", datetime.now().isoformat(
                    timespec="seconds",
                ))
                fh.write(json.dumps(payload, ensure_ascii=False, default=str))
                fh.write("\n")
    except OSError as exc:
        logger.warning("写入预热审计失败 (%s): %s", path, exc)
        return None
    return path


def _send_alert(title: str, rows: list[dict[str, Any]]) -> None:
    """发送告警 —— 统一飞书通道（未配置 token 时由该通道自行跳过）。"""
    body = "\n".join(
        f"- {r.get('check', '?')}: {r.get('msg', '')}" for r in rows
    )
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert

        send_feishu_alert(title, body, level="WARNING")
    except Exception as exc:  # noqa: BLE001 - 告警失败不影响预热结论
        logger.warning("飞书告警失败: %s", exc)


_CHECKS = {
    "equity_daily": preheat_equity_daily,
    "daily_basic": preheat_daily_basic,
    "cb_daily": preheat_cb_daily,
}


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（有检查项失败则 1）。"""
    parser = argparse.ArgumentParser(description="行情缓存预热")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只检查缓存新鲜度，不拉取数据",
    )
    parser.add_argument(
        "--only", choices=sorted(_CHECKS), action="append",
        help="只跑指定检查项（可重复）",
    )
    parser.add_argument(
        "--max-days", type=int, default=_DEFAULT_MAX_DAYS,
        help=f"单次最多补几个交易日（默认 {_DEFAULT_MAX_DAYS}）",
    )
    parser.add_argument(
        "--notify", action="store_true", help="有异常时发送飞书告警",
    )
    args = parser.parse_args(argv)

    names = args.only or sorted(_CHECKS)
    rows = [
        _CHECKS[name](dry_run=args.dry_run, max_days=args.max_days)
        for name in names
    ]

    for row in rows:
        level = logging.WARNING if row["status"] == "FAIL" else logging.INFO
        logger.log(level, "[%s] %s — %s",
                   row["status"], row["check"], row["msg"])

    _write_audit(rows)

    bad = [r for r in rows if r["status"] == "FAIL"]
    if bad and args.notify:
        _send_alert(f"行情预热失败（{len(bad)} 项）", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
