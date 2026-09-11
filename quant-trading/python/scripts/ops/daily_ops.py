#!/usr/bin/env python3
"""模拟盘每日运维任务总入口（Docker cron 调用）

把散落的运维动作收在一个入口里，cron 只需要一条 crontab 记录：

- ``backup``  回测索引库在线备份 + 完整性校验 + 过期清理（``db_backup``）
- ``nav``     各策略 NAV 日间跳变检查（``nav_monitor``）
- ``health``  容器自身的健康体检：磁盘、日志可写、DB 可读、Kill Switch
              状态、模拟盘状态文件新鲜度

退出码：全部任务通过 0，任一任务判定失败 1（cron / 容器健康检查据此告警）。

用法::

    python scripts/ops/daily_ops.py                 # 全部任务
    python scripts/ops/daily_ops.py --task health
    python scripts/ops/daily_ops.py --task all --notify --json

设计要点见下方 sys.path 注释与 ``run_health_check`` 的分级说明。
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# --- sys.path 修正（必须在 import aipha.* 之前）-----------------------------
# 容器里的 cron 以极简环境运行：没有 login shell，profile 不生效，
# PYTHONPATH 很容易丢。此时 `python /app/scripts/ops/daily_ops.py` 的
# sys.path[0] 是脚本所在目录 scripts/ops/，``aipha`` 包不在路径上，
# 直接 ImportError —— 而 cron 的 ImportError 通常没人看得见，
# 表现就是「备份和监控静默地一天都没跑」。所以这里自己把项目根塞进去，
# 不依赖外部环境变量。
#
# 层数：scripts/ops/daily_ops.py → ops → scripts → <项目根>。
# 这个数法在当前布局下是对的（aipha/utils/paths.py 记录了树里另外 45 处
# 数错一层的同类写法，那些是 src/ 被移除后遗留的）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from aipha.utils.paths import DATA_DIR, KILL_SWITCH_PATH, LOGS_DIR  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TASKS = ("backup", "nav", "health")

# 审计目录：每次运行落一行 JSON，cron 日志轮转掉之后这里还查得到
_AUDIT_DIR = LOGS_DIR / "ops"

DEFAULT_PAPER_STATE_DIR = DATA_DIR / "paper_state"
DEFAULT_NAV_THRESHOLD = 0.05
DEFAULT_KEEP_DAYS = 30

# 磁盘余量分级（容器磁盘打满时，写状态文件会写出半截 JSON，
# 那比不写更糟 —— 下次启动按坏状态恢复仓位）
_DISK_FAIL_GB = 1.0
_DISK_WARN_GB = 5.0

# 模拟盘状态文件允许落后几个**交易日**
_MAX_STATE_STALE_TRADING_DAYS = 1

_calendar_cache: Any = None


# ---------------------------------------------------------------------------
# 同目录模块加载
# ---------------------------------------------------------------------------
def _load_ops_module(name: str) -> Any:
    """加载 ``scripts/ops`` 下的兄弟模块，兼容两种导入方式。

    这个包被两种方式用：``from scripts.ops.db_backup import ...``（包路径）
    和把 ``scripts/ops`` 挂到 sys.path 后 ``import db_backup``（扁平）。
    因此**不写相对导入** —— 相对导入在扁平方式下直接崩。先试包路径
    （上面已把项目根放进 sys.path，一定可达），失败再退回扁平。
    """
    try:
        return importlib.import_module(f"scripts.ops.{name}")
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        return importlib.import_module(name)


# ---------------------------------------------------------------------------
# 任务：数据库备份
# ---------------------------------------------------------------------------
def run_db_backup(
    db_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    keep_days: int = DEFAULT_KEEP_DAYS,
    notify: bool = False,
) -> dict[str, Any]:
    """回测索引库在线备份。

    Returns:
        ``{"task": "backup", "ok": bool, "backup_path": str | None,
        "message": str}``
    """
    mod = _load_ops_module("db_backup")
    src = Path(db_path) if db_path else Path(mod.DEFAULT_DB_PATH)
    dest_dir = Path(backup_dir) if backup_dir else Path(mod.DEFAULT_BACKUP_DIR)

    if not src.is_file():
        # 容器里可能根本没挂回测库（纯模拟盘节点），这不算故障
        msg = f"数据库不存在，跳过备份: {src}"
        logger.info(msg)
        return {
            "task": "backup",
            "ok": True,
            "skipped": True,
            "backup_path": None,
            "message": msg,
        }

    result = mod.backup_database(
        db_path=src,
        backup_dir=dest_dir,
        keep_days=keep_days,
        notify=notify,
    )
    ok = result is not None
    return {
        "task": "backup",
        "ok": ok,
        "backup_path": str(result) if result else None,
        "message": "备份完成" if ok else "备份失败（详见日志）",
    }


# ---------------------------------------------------------------------------
# 任务：NAV 跳变检查
# ---------------------------------------------------------------------------
def run_nav_check(
    paper_state_dir: str | Path | None = None,
    threshold: float = DEFAULT_NAV_THRESHOLD,
    notify: bool = False,
) -> dict[str, Any]:
    """各策略 NAV 日间跳变检查。

    Returns:
        ``{"task": "nav", "ok": bool, "checked": int, "failed": [dict], ...}``
    """
    mod = _load_ops_module("nav_monitor")
    root = Path(paper_state_dir) if paper_state_dir else DEFAULT_PAPER_STATE_DIR

    results = mod.run_nav_check(root, threshold=threshold)
    failed = [r for r in results if not r.passed]

    if failed and notify:
        body = "\n".join(f"- {r.strategy_name}: {r.message}" for r in failed)
        _send_alert(f"模拟盘 NAV 异常（{len(failed)} 个策略）", body, "WARNING")

    return {
        "task": "nav",
        "ok": not failed,
        "checked": len(results),
        "failed": [r.to_dict() for r in failed],
        "message": (
            f"{len(results)} 个策略全部正常" if not failed
            else f"{len(failed)}/{len(results)} 个策略 NAV 偏差超阈值"
        ),
    }


# ---------------------------------------------------------------------------
# 任务：健康体检
# ---------------------------------------------------------------------------
def run_health_check(
    paper_state_dir: str | Path | None = None,
    notify: bool = False,
) -> dict[str, Any]:
    """容器自身的健康体检。

    每项返回三档状态，只有 ``FAIL`` 才影响退出码：

    - ``OK``   正常
    - ``WARN`` 需要留意但不该半夜叫人（余量偏低、状态文件略陈旧）
    - ``SKIP`` 这个节点上本来就没有这项东西（比如没挂回测库）
    - ``FAIL`` 已经会导致数据写坏或任务跑不动

    Kill Switch 存在时**只报告不判失败**：它通常是运维自己按下去的，
    按失败处理会让每次 cron 都告警一次，真正的故障就被淹没了。
    """
    checks: list[dict[str, Any]] = [
        _check_disk(),
        _check_logs_writable(),
        _check_db_readable(),
        _check_kill_switch(),
        _check_paper_state_fresh(paper_state_dir),
    ]

    failed = [c for c in checks if c["status"] == "FAIL"]
    warned = [c for c in checks if c["status"] == "WARN"]

    for c in checks:
        line = "%s: %s" % (c["name"], c["message"])
        if c["status"] == "FAIL":
            logger.error(line)
        elif c["status"] == "WARN":
            logger.warning(line)
        else:
            logger.info(line)

    if failed and notify:
        body = "\n".join(f"- {c['name']}: {c['message']}" for c in failed)
        _send_alert(f"运维体检失败（{len(failed)} 项）", body, "ERROR")

    return {
        "task": "health",
        "ok": not failed,
        "checks": checks,
        "message": (
            "全部通过" if not failed and not warned
            else f"{len(failed)} 项失败 / {len(warned)} 项告警"
        ),
    }


def _check_disk() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(_PROJECT_ROOT))
    except OSError as exc:
        return _check("disk", "WARN", f"磁盘用量读取失败: {exc}")

    free_gb = usage.free / 1024 ** 3
    detail = f"可用 {free_gb:.1f} GB / 共 {usage.total / 1024 ** 3:.1f} GB"
    if free_gb < _DISK_FAIL_GB:
        return _check("disk", "FAIL", f"磁盘余量不足（{detail}）", free_gb=free_gb)
    if free_gb < _DISK_WARN_GB:
        return _check("disk", "WARN", f"磁盘余量偏低（{detail}）", free_gb=free_gb)
    return _check("disk", "OK", detail, free_gb=free_gb)


def _check_logs_writable() -> dict[str, Any]:
    """日志目录不可写 = 后续所有任务的结果都无从查证。"""
    probe = LOGS_DIR / ".daily_ops_write_probe"
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        return _check("logs_writable", "FAIL", f"日志目录不可写 {LOGS_DIR}: {exc}")
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    return _check("logs_writable", "OK", f"日志目录可写: {LOGS_DIR}")


def _check_db_readable() -> dict[str, Any]:
    mod = _load_ops_module("db_backup")
    db_path = Path(mod.DEFAULT_DB_PATH)
    if not db_path.is_file():
        return _check("db_readable", "SKIP", f"未挂载回测库: {db_path}")
    if not mod.verify_db_integrity(db_path):
        return _check(
            "db_readable", "FAIL", f"回测库完整性校验未通过: {db_path}",
        )
    size_mb = db_path.stat().st_size / 1024 / 1024
    return _check("db_readable", "OK", f"回测库正常（{size_mb:.1f} MB）")


def _check_kill_switch() -> dict[str, Any]:
    if KILL_SWITCH_PATH.exists():
        return _check(
            "kill_switch", "WARN",
            f"Kill Switch 已激活，所有交易停止: {KILL_SWITCH_PATH}",
            engaged=True,
        )
    return _check("kill_switch", "OK", "Kill Switch 未激活", engaged=False)


def _check_paper_state_fresh(
    paper_state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """模拟盘状态文件的新鲜度（按交易日算，不按自然日）。"""
    root = Path(paper_state_dir) if paper_state_dir else DEFAULT_PAPER_STATE_DIR
    if not root.is_dir():
        return _check("paper_state", "SKIP", f"无模拟盘状态目录: {root}")

    newest: date | None = None
    newest_path: Path | None = None
    for f in root.rglob("state.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).date()
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest, newest_path = mtime, f

    if newest is None:
        return _check("paper_state", "SKIP", f"目录下没有 state.json: {root}")

    stale = _stale_trading_days(newest)
    detail = f"最新状态 {newest.isoformat()}（{newest_path}）"
    if stale > _MAX_STATE_STALE_TRADING_DAYS:
        return _check(
            "paper_state", "WARN",
            f"状态文件落后 {stale} 个交易日 —— {detail}",
            stale_trading_days=stale,
        )
    return _check("paper_state", "OK", detail, stale_trading_days=stale)


def _stale_trading_days(latest: date, today: date | None = None) -> int:
    """``latest`` 之后已经过去几个交易日。

    按交易日算：春节 / 国庆连休 7~9 个自然日，按自然日判断会在每个长假后
    误报「状态陈旧」，天天误报的监控等于没有监控。日历不可用时退回自然日
    并砍掉一个宽限（>5 自然日才算落后）。
    """
    ref = today or date.today()
    if latest >= ref:
        return 0

    cal = _get_calendar()
    if cal is None:
        natural = (ref - latest).days
        return 0 if natural <= 5 else natural

    try:
        days = cal.get_trading_days(latest + timedelta(days=1), ref)
    except Exception as exc:  # noqa: BLE001 - 日历查询失败要能降级
        logger.warning("交易日历查询失败，退回自然日规则: %s", exc)
        natural = (ref - latest).days
        return 0 if natural <= 5 else natural
    return len(days)


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


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, **extra}


# ---------------------------------------------------------------------------
# 审计 / 告警
# ---------------------------------------------------------------------------
def _write_audit(payload: dict[str, Any]) -> None:
    """每次运行落一行 JSON。cron 日志会被轮转掉，这里是事后唯一线索。"""
    try:
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        path = _AUDIT_DIR / f"daily_ops_{date.today().strftime('%Y%m%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("写审计日志失败: %s", exc)


def _send_alert(title: str, body: str, level: str = "WARNING") -> None:
    """发送告警 —— 统一飞书通道（与 aipha/runner/daily_utils.py 同口径）。"""
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert

        send_feishu_alert(title, body, level=level)
    except Exception as exc:  # noqa: BLE001 - 告警失败不影响任务结论
        logger.warning("飞书告警失败: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（任一任务失败则 1）。"""
    parser = argparse.ArgumentParser(description="模拟盘每日运维任务")
    parser.add_argument(
        "--task", choices=("all", *TASKS), default="all", help="要执行的任务",
    )
    parser.add_argument(
        "--paper-state", default=None, help="模拟盘状态目录",
    )
    parser.add_argument(
        "--nav-threshold", type=float, default=DEFAULT_NAV_THRESHOLD,
        help="NAV 日间偏差阈值（默认 0.05）",
    )
    parser.add_argument(
        "--backup-dir", default=None, help="数据库备份目录",
    )
    parser.add_argument(
        "--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
        help="备份保留天数（默认 30）",
    )
    parser.add_argument(
        "--notify", action="store_true", help="异常时发送飞书告警",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出汇总")
    args = parser.parse_args(argv)

    selected = TASKS if args.task == "all" else (args.task,)
    results: list[dict[str, Any]] = []

    for task in selected:
        # 一个任务炸了不能带走其余任务：备份失败也要继续查 NAV
        try:
            if task == "backup":
                results.append(run_db_backup(
                    backup_dir=args.backup_dir,
                    keep_days=args.keep_days,
                    notify=args.notify,
                ))
            elif task == "nav":
                results.append(run_nav_check(
                    paper_state_dir=args.paper_state,
                    threshold=args.nav_threshold,
                    notify=args.notify,
                ))
            elif task == "health":
                results.append(run_health_check(
                    paper_state_dir=args.paper_state,
                    notify=args.notify,
                ))
        except Exception as exc:  # noqa: BLE001 - cron 入口兜底
            logger.exception("任务 %s 异常", task)
            results.append({
                "task": task, "ok": False, "message": f"任务异常: {exc}",
            })

    failed = [r for r in results if not r.get("ok")]
    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tasks": results,
        "ok": not failed,
    }
    _write_audit(summary)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if failed:
        logger.error(
            "运维任务失败: %s", ", ".join(str(r["task"]) for r in failed),
        )
        return 1
    logger.info("运维任务全部通过（%d 项）", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
