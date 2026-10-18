#!/usr/bin/env python3
"""模拟盘批量启动器（Docker cron 每交易日盘后调用）

规范 ``research_paper`` profile 调用版本化快照/计划的连续 SQLite 账本；
历史组合仍由 ``aipha.runner.paper_engine_eod`` 兼容执行。
容器里每天要跑一批组合，跑哪些由 ``configs/paper/system_settings.json``
决定 —— 这个启动器就是「读设置 → 逐个拉起引擎 → 汇总退出码」这一层，
不含任何交易逻辑。

## 三道闸门（按顺序，任一不过就什么都不启动）

1. ``configs/kill_switch.flag`` 存在 → 全部停止（§14.2 L3）。
2. ``paper_trading_enabled`` 为 false → 停止。
3. ``paper_auto_start`` 为 false → 停止，除非显式 ``--force``。

第 3 条是**安全默认**：当前 ``system_settings.json`` 里就是 false
（P0-5 之后刻意如此），所以把这个脚本放进 crontab 本身不会让任何组合
自动开跑；要让 cron 真的启动，得先有人把 ``paper_auto_start`` 改成 true。
本脚本不去改那个开关 —— 「无人值守脚本自己打开自己」是最不该有的行为。

## 跑哪些组合

``selected_portfolios`` 非空时用它，否则用 ``daily_batch_portfolios``
（后者是 EOD 日频批处理的活跃清单，与 ``paper_config_auditor`` /
``paper_backtest_reconciliation`` 同口径），再减去
``disabled_portfolios``。缺失或无效 YAML 按失败报告，不猜文件名。
默认预演，加 ``--execute`` 才提交；所有安全闸门仍然有效。

## 为什么串行 + 超时

串行：多个组合共用同一个 TuShare 账号（有 QPS 限制）和同一份行情缓存，
并行拉起只会互相抢配额、把缓存写花。

超时：``TuShareEquityProvider.get_daily_snapshot()`` 是**无限重试**设计，
盘后接口抽风时单个组合能挂住一整夜，把后面的组合全饿死。因此每个组合
都套 ``--per-timeout``（默认 30 分钟），超时按失败记账后继续下一个。

用法::

    python scripts/paper_launcher.py --list        # 只打印计划，不启动
    python scripts/paper_launcher.py --dry-run     # 规范账本不落盘；旧引擎只更新 NAV
    python scripts/paper_launcher.py --execute     # 显式提交每日 Paper 运行
    python scripts/paper_launcher.py --force       # 忽略 paper_auto_start
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import yaml

# cron 环境里 PYTHONPATH 可能是空的，见 scripts/ops/daily_ops.py 的说明。
# 层数：scripts/paper_launcher.py → scripts → <项目根>。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from aipha.utils.paths import CONFIGS_DIR, KILL_SWITCH_PATH, PROJECT_ROOT  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SETTINGS_PATH = CONFIGS_DIR / "paper" / "system_settings.json"
PAPER_CONFIG_DIR = CONFIGS_DIR / "paper"
ENGINE_MODULE = "aipha.runner.paper_engine_eod"

# 单个组合的墙钟上限（秒）。TuShare 快照是无限重试的，必须在调度层兜。
DEFAULT_PER_TIMEOUT = 1800


def load_settings(path: str | Path = SETTINGS_PATH) -> dict[str, Any] | None:
    """读 system_settings.json；读不到返回 None（调用方按「不启动」处理）。"""
    p = Path(path)
    if not p.is_file():
        logger.error("system_settings.json 不存在: %s", p)
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("读取 %s 失败: %s", p, exc)
        return None
    if not isinstance(data, dict):
        logger.error("%s 不是 JSON 对象", p)
        return None
    return data


def resolve_portfolios(settings: dict[str, Any]) -> list[str]:
    """按设置解析出今天要跑的组合名列表（已剔除禁用项）。"""
    selected = settings.get("selected_portfolios") or []
    batch = settings.get("daily_batch_portfolios") or []
    for value in (selected, batch):
        if not isinstance(value, list) or any(
            not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None
            for name in value
        ):
            raise ValueError("portfolio selections must be lists of simple profile names")
    names = selected or batch

    disabled = settings.get("disabled_portfolios") or {}
    if not isinstance(disabled, (dict, list)):
        raise ValueError("disabled_portfolios must be a mapping or list")
    # disabled_portfolios 现在是 {名字: 原因} 的 dict，历史上是 list，
    # 两种都要能读 —— `in` 对 dict 判的是键，正好通用。
    result: list[str] = []
    for name in names:
        if name in disabled:
            reason = (
                disabled.get(name) if isinstance(disabled, dict) else "已禁用"
            )
            logger.warning("跳过 %s（已禁用: %s）", name, reason)
            continue
        if name not in result:
            result.append(name)
    return result


def config_path_for(name: str) -> Path | None:
    """组合名 → YAML 路径；文件不存在返回 None。"""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None:
        raise ValueError("invalid portfolio profile name")
    for suffix in (".yml", ".yaml"):
        p = PAPER_CONFIG_DIR / f"{name}{suffix}"
        if p.is_file():
            return p
    return None


def check_gates(settings: dict[str, Any], force: bool = False) -> str | None:
    """检查三道闸门。返回拒绝原因；全部通过返回 None。"""
    if KILL_SWITCH_PATH.exists():
        return f"Kill Switch 已激活: {KILL_SWITCH_PATH}"
    if settings.get("paper_trading_enabled") is not True:
        return "paper_trading_enabled=false"
    if settings.get("paper_auto_start") is not True and not force:
        return (
            "paper_auto_start=false（安全默认）；"
            "确认要自动启动请改设置或加 --force"
        )
    return None


def _profile_path(value: Any, *, signal_date: date | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("profile paths must be nonempty strings")
    text = value.strip()
    if signal_date is not None:
        text = text.replace("{date}", signal_date.isoformat()).replace("{trade_date}", signal_date.strftime("%Y%m%d"))
    if "{" in text or "}" in text:
        raise ValueError("only snapshot/plan paths support {date} and {trade_date} templates")
    path = Path(text).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def prepare_launch(config: Path, *, dry_run: bool = True, trade_date: str | None = None) -> dict[str, Any]:
    """Resolve one deployment without reading models, writing books or sending orders."""
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Paper profile must be a YAML mapping")
    if "research_paper" not in raw:
        if any(key in raw for key in ("features", "label", "model")):
            raise ValueError("research YAML is not a launch profile; declare research_paper paths")
        if trade_date is not None:
            raise ValueError("--date applies only to research_paper profiles; legacy EOD cannot replay a selected date")
        cmd = [sys.executable, "-m", ENGINE_MODULE, "--config", str(config.resolve())]
        if dry_run:
            cmd.append("--dry-run")
        return {"command": cmd, "engine": "legacy_eod", "book_path": None}
    if set(raw) - {"research_paper", "profile_name", "description"}:
        raise ValueError("research_paper profile cannot mix legacy engine/strategy settings")
    profile = raw["research_paper"]
    known = {"schema_version", "research_config", "snapshot_path", "book_path", "plan_path", "plan_dir"}
    if not isinstance(profile, dict) or set(profile) - known:
        raise ValueError("invalid research_paper profile fields")
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("research_paper schema_version=1 required")
    if "plan_path" in profile and "plan_dir" in profile:
        raise ValueError("declare plan_path or plan_dir, not both")
    signal_date = date.fromisoformat(trade_date) if trade_date else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    research = _profile_path(profile.get("research_config"))
    snapshot = _profile_path(profile.get("snapshot_path"), signal_date=signal_date)
    book = _profile_path(profile.get("book_path"))
    if not research.is_file() or not snapshot.is_file():
        raise FileNotFoundError(f"research config and signed snapshot must exist: {research}, {snapshot}")
    if snapshot.suffix.lower() != ".parquet" or book.suffix.lower() != ".sqlite3":
        raise ValueError("research_paper requires a Parquet snapshot and a .sqlite3 book")
    from aipha.research.config import load_research_config

    load_research_config(research)  # Enforces TuShare PIT, monthly, paper policy.
    cmd = [sys.executable, str(_PROJECT_ROOT / "scripts/ops/v2_ml_paper_daily.py"),
           "--research-config", str(research), "--panel", str(snapshot),
           "--book-path", str(book), "--date", signal_date.isoformat(), "--json"]
    plan = None
    if "plan_path" in profile:
        plan = _profile_path(profile["plan_path"], signal_date=signal_date)
        if not plan.is_file():
            raise FileNotFoundError(f"explicit Paper plan not found: {plan}")
    elif "plan_dir" in profile:
        directory = _profile_path(profile["plan_dir"])
        if not directory.is_dir():
            raise FileNotFoundError(f"Paper plan directory not found: {directory}")
        dated = directory / f"{signal_date.isoformat()}.json"
        if (dated.exists() or dated.is_symlink()) and not dated.is_file():
            raise ValueError(f"dated Paper plan is not a file: {dated}")
        if dated.is_file():
            plan = dated
    if plan is not None:
        cmd.extend(["--plan-path", str(plan)])
    if not dry_run:
        cmd.append("--execute")
    return {"command": cmd, "engine": "research_paper", "book_path": str(book)}


def launch_one(
    config: Path,
    dry_run: bool = True,
    timeout: int = DEFAULT_PER_TIMEOUT,
    *,
    trade_date: str | None = None,
    prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """拉起单个组合的 EOD 引擎，返回结果记录。"""
    started = datetime.now()
    try:
        spec = prepared or prepare_launch(config, dry_run=dry_run, trade_date=trade_date)
        cmd = spec["command"]
        logger.info("启动: %s", " ".join(cmd))
        proc = subprocess.run(  # noqa: S603 - 命令由本地配置拼出
            cmd,
            cwd=str(_PROJECT_ROOT),
            timeout=timeout,
            check=False,
        )
        returncode = proc.returncode
        message = "完成" if returncode == 0 else f"引擎退出码 {returncode}"
    except subprocess.TimeoutExpired:
        returncode = -1
        message = f"超时 {timeout}s，已终止"
        logger.error("%s: %s", config.stem, message)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        returncode = -1
        message = f"启动失败: {exc}"
        logger.error("%s: %s", config.stem, message)

    elapsed = (datetime.now() - started).total_seconds()
    return {
        "name": config.stem,
        "config": str(config),
        "returncode": returncode,
        "elapsed_sec": round(elapsed, 1),
        "ok": returncode == 0,
        "message": message,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（任一组合失败则 1）。"""
    parser = argparse.ArgumentParser(description="模拟盘批量启动器")
    parser.add_argument(
        "--settings", default=str(SETTINGS_PATH), help="system_settings.json 路径",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="默认：规范账本不落盘；旧引擎只更新 NAV")
    mode.add_argument("--execute", action="store_true", help="显式提交 Paper 运行，仍需通过全部安全闸门")
    parser.add_argument("--date", type=lambda value: date.fromisoformat(value).isoformat(),
                        help="仅规范 profile：信号日期 YYYY-MM-DD；默认上海本日，不回退旧快照")
    parser.add_argument(
        "--force", action="store_true",
        help="忽略 paper_auto_start=false（Kill Switch 仍然生效）",
    )
    parser.add_argument(
        "--list", action="store_true", help="只打印今天的启动计划",
    )
    parser.add_argument(
        "--per-timeout", type=int, default=DEFAULT_PER_TIMEOUT,
        help=f"单个组合墙钟上限秒数（默认 {DEFAULT_PER_TIMEOUT}）",
    )
    parser.add_argument(
        "--only", action="append", default=None,
        help="只跑指定组合（可重复），仍受闸门与禁用清单约束",
    )
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    if settings is None:
        return 1

    if args.per_timeout <= 0:
        parser.error("--per-timeout must be positive")
    try:
        names = resolve_portfolios(settings)
    except ValueError as exc:
        logger.error("无效启动设置: %s", exc)
        return 1
    errors = []
    if args.only:
        wanted = set(args.only)
        skipped = wanted - set(names)
        for name in sorted(skipped):
            logger.warning("--only %s 不在活跃清单里，忽略", name)
            errors.append(name)
        names = [n for n in names if n in wanted]

    plan: list[Path] = []
    for name in names:
        cfg = config_path_for(name)
        if cfg is None:
            logger.error("组合 %s 没有对应的 YAML，跳过", name)
            errors.append(name)
            continue
        plan.append(cfg)

    reason = check_gates(settings, force=args.force)
    if reason is not None and not args.list:
        logger.warning("未启动任何组合: %s", reason)
        return 0

    # Prepare the full batch before starting anything. Two profiles may not
    # accidentally advance the same cash/position book in one batch.
    prepared = []
    books = set()
    for cfg in plan:
        try:
            spec = prepare_launch(cfg, dry_run=not args.execute, trade_date=args.date)
            if spec["book_path"] is not None:
                book = Path(spec["book_path"])
                if book in books:
                    raise ValueError(f"multiple profiles share one Paper book: {book}")
                books.add(book)
            prepared.append((cfg, spec))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            logger.error("%s: %s", cfg.stem, exc)
            errors.append(cfg.stem)
    if args.list:
        print(f"有效启动计划 {len(prepared)} 个:")
        for cfg, spec in prepared:
            print(f"  - {cfg.stem} [{spec['engine']}]: {json.dumps(spec['command'], ensure_ascii=False)}")
        if errors:
            print(f"无效配置: {', '.join(errors)}")
        print(f"闸门: {'通过' if reason is None else '拒绝 — ' + reason}")
        return 1 if errors else 0
    if errors:
        logger.error("启动计划无效，未启动任何组合: %s", ", ".join(errors))
        return 1
    results = []
    for cfg, spec in prepared:
        # Recheck between jobs: an operator may stop the remaining batch while
        # a previous subprocess is still running.
        current = load_settings(args.settings)
        reason = check_gates(current, force=args.force) if current is not None else "settings unavailable"
        if reason is not None:
            logger.error("批次中止: %s", reason)
            return 1
        try:
            if cfg.stem not in resolve_portfolios(current):
                logger.error("批次中止: %s 已从活跃清单移除", cfg.stem)
                return 1
        except ValueError as exc:
            logger.error("批次中止: %s", exc)
            return 1
        results.append(launch_one(cfg, timeout=args.per_timeout, prepared=spec))

    failed = [r for r in results if not r["ok"]]
    for r in results:
        logger.info(
            "%s: %s（%.1fs）", r["name"], r["message"], r["elapsed_sec"],
        )
    if failed:
        logger.error(
            "%d/%d 个组合失败: %s",
            len(failed), len(results),
            ", ".join(str(r["name"]) for r in failed),
        )
        return 1
    logger.info("全部 %d 个组合完成", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
