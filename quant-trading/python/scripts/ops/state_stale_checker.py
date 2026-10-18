#!/usr/bin/env python3
"""模拟盘状态文件陈旧检查（cron 每日）

每个组合在 ``data/paper_state/<组合名>/`` 下维护 ``state.json``（持仓、
现金、NAV 历史）。引擎每个交易日跑完都会重写它，所以**这个文件的修改时间
就是「引擎上次真的跑成功」的时间**。

为什么单独盯这个：cron 任务失败是静默的。引擎因为 token 过期 / 依赖装坏 /
容器 OOM 连着几天没跑，日志在容器里、没人看，账面上却仍是几天前的持仓；
等有人打开面板才发现「这周的调仓一次都没执行」。状态文件的 mtime 是唯一
不依赖引擎自身健康度的旁证 —— 引擎挂了它就不动。

判定：
- 目录下有 ``state.json`` 或 ``paper_state.json``，且 mtime 在阈值内 → ok
- 超过阈值 → stale
- 目录存在但两个文件都没有 → missing（同样算 stale：目录建了却没写过
  状态，通常是引擎第一次就没跑起来）

用法::

    python scripts/ops/state_stale_checker.py
    python scripts/ops/state_stale_checker.py --state-dir data/paper_state \\
        --threshold-hours 48 --notify
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from aipha.utils.paths import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = DATA_DIR / "paper_state"

# 默认 24 小时：EOD 引擎每交易日跑一次。周末与长假会让所有组合一起报
# 陈旧，那属于预期噪音 —— cron 里按需给 --threshold-hours（如节后第一天
# 给 96）比在这里内建日历要透明得多，本模块不猜交易日。
DEFAULT_THRESHOLD_HOURS = 24.0

# 状态文件名候选，按优先级：引擎写 state.json，早期版本写 paper_state.json
STATE_FILENAMES = ("state.json", "paper_state.json")


def check_state_stale(
    state_dir: str | Path = DEFAULT_STATE_DIR,
    threshold_hours: float = DEFAULT_THRESHOLD_HOURS,
    now: datetime | None = None,
    book_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """逐个组合检查状态文件是否陈旧。

    Args:
        state_dir: 模拟盘状态根目录（其下每个子目录是一个组合）
        threshold_hours: 允许落后的小时数
        now: 参考时刻（测试注入用）

    Returns:
        每个组合一条记录：``{"portfolio_id", "status", "stale",
        "state_file", "age_hours", "threshold_hours", "mtime", "message"}``。
        目录不存在或没有任何子目录时返回 ``[]`` —— 空列表表示
        「没什么可检查」，与「检查了且有问题」区分开。
    """
    root = Path(state_dir)
    ref = now or datetime.now()
    results: list[dict[str, Any]] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        results.append(_check_one(d, threshold_hours, ref))
    if book_dir is not None:
        from aipha.research.paper_monitor import inspect_paper_books

        for row in inspect_paper_books(book_dir, threshold_hours=threshold_hours, now=now):
            results.append({key: row[key] for key in (
                "portfolio_id", "source", "status", "state_file", "age_hours", "threshold_hours", "mtime", "message",
            )} | {"stale": row["status"] != "ok"})
    return results


def _check_one(
    portfolio_dir: Path,
    threshold_hours: float,
    ref: datetime,
) -> dict[str, Any]:
    name = portfolio_dir.name
    state_file = _find_state_file(portfolio_dir)

    if state_file is None:
        return {
            "portfolio_id": name,
            "status": "missing",
            "stale": True,
            "state_file": None,
            "age_hours": None,
            "threshold_hours": threshold_hours,
            "mtime": None,
            "message": (
                f"{name}: 目录存在但没有 "
                f"{' / '.join(STATE_FILENAMES)}，引擎可能从未成功运行"
            ),
        }

    try:
        mtime = datetime.fromtimestamp(state_file.stat().st_mtime)
    except OSError as exc:
        # 读不到 mtime 按陈旧处理：宁可多报一次，也不要把「查不了」当成正常
        return {
            "portfolio_id": name,
            "status": "missing",
            "stale": True,
            "state_file": str(state_file),
            "age_hours": None,
            "threshold_hours": threshold_hours,
            "mtime": None,
            "message": f"{name}: 无法读取状态文件时间: {exc}",
        }

    age_hours = (ref - mtime).total_seconds() / 3600.0
    stale = age_hours > threshold_hours
    return {
        "portfolio_id": name,
        "status": "stale" if stale else "ok",
        "stale": stale,
        "state_file": str(state_file),
        "age_hours": round(age_hours, 2),
        "threshold_hours": threshold_hours,
        "mtime": mtime.isoformat(timespec="seconds"),
        "message": (
            f"{name}: 状态已 {age_hours:.1f} 小时未更新"
            f"（阈值 {threshold_hours:.0f}h，最后更新 "
            f"{mtime.isoformat(timespec='minutes')}）"
            if stale else
            f"{name}: 状态 {age_hours:.1f} 小时前更新，正常"
        ),
    }


def _find_state_file(portfolio_dir: Path) -> Path | None:
    for name in STATE_FILENAMES:
        p = portfolio_dir / name
        if p.is_file():
            return p
    return None


def _send_alert(title: str, body: str) -> None:
    """发送告警 —— 统一飞书通道（与 aipha/runner/daily_utils.py 同口径）。"""
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert

        send_feishu_alert(title, body, level="WARNING")
    except Exception as exc:  # noqa: BLE001 - 告警失败不影响检查结论
        logger.warning("飞书告警失败: %s", exc)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（存在陈旧/缺失则 1）。"""
    parser = argparse.ArgumentParser(description="模拟盘状态文件陈旧检查")
    parser.add_argument(
        "--state-dir", default=str(DEFAULT_STATE_DIR), help="模拟盘状态根目录",
    )
    parser.add_argument(
        "--threshold-hours", type=float, default=DEFAULT_THRESHOLD_HOURS,
        help=f"允许落后的小时数（默认 {DEFAULT_THRESHOLD_HOURS:.0f}）",
    )
    parser.add_argument("--book-dir", default=str(DATA_DIR / "research/paper"), help="规范 Paper SQLite 目录或单个账本")
    parser.add_argument(
        "--notify", action="store_true", help="有陈旧时发送飞书告警",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    results = check_state_stale(
        state_dir=args.state_dir, threshold_hours=args.threshold_hours, book_dir=args.book_dir,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    if not results:
        logger.info("没有可检查的组合状态目录: %s", args.state_dir)
        return 0

    stale = [r for r in results if r["stale"]]
    for r in results:
        (logger.warning if r["stale"] else logger.info)("%s", r["message"])

    if not stale:
        logger.info("全部 %d 个组合状态新鲜", len(results))
        return 0

    if args.notify:
        body = "\n".join(f"- {r['message']}" for r in stale)
        _send_alert(f"模拟盘状态陈旧（{len(stale)} 个组合）", body)
    logger.error("%d/%d 个组合状态陈旧", len(stale), len(results))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
