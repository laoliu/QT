#!/usr/bin/env python3
"""模拟盘批量启动器（Docker cron 每交易日盘后调用）

``aipha/runner/paper_engine_eod.py`` 是**单个**组合的引擎入口
（``python -m aipha.runner.paper_engine_eod --config configs/paper/x.yml``）。
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
``disabled_portfolios``。找不到对应 YAML 的名字跳过并告警，
不去猜文件名。

## 为什么串行 + 超时

串行：多个组合共用同一个 TuShare 账号（有 QPS 限制）和同一份行情缓存，
并行拉起只会互相抢配额、把缓存写花。

超时：``TuShareEquityProvider.get_daily_snapshot()`` 是**无限重试**设计，
盘后接口抽风时单个组合能挂住一整夜，把后面的组合全饿死。因此每个组合
都套 ``--per-timeout``（默认 30 分钟），超时按失败记账后继续下一个。

用法::

    python scripts/paper_launcher.py --list        # 只打印计划，不启动
    python scripts/paper_launcher.py --dry-run     # 引擎只更新 NAV 不调仓
    python scripts/paper_launcher.py --force       # 忽略 paper_auto_start
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# cron 环境里 PYTHONPATH 可能是空的，见 scripts/ops/daily_ops.py 的说明。
# 层数：scripts/paper_launcher.py → scripts → <项目根>。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from aipha.utils.paths import CONFIGS_DIR, KILL_SWITCH_PATH  # noqa: E402

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
    selected = list(settings.get("selected_portfolios") or [])
    batch = list(settings.get("daily_batch_portfolios") or [])
    names = selected or batch

    disabled = settings.get("disabled_portfolios") or {}
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
        result.append(name)
    return result


def config_path_for(name: str) -> Path | None:
    """组合名 → YAML 路径；文件不存在返回 None。"""
    for suffix in (".yml", ".yaml"):
        p = PAPER_CONFIG_DIR / f"{name}{suffix}"
        if p.is_file():
            return p
    return None


def check_gates(settings: dict[str, Any], force: bool = False) -> str | None:
    """检查三道闸门。返回拒绝原因；全部通过返回 None。"""
    if KILL_SWITCH_PATH.exists():
        return f"Kill Switch 已激活: {KILL_SWITCH_PATH}"
    if not settings.get("paper_trading_enabled", False):
        return "paper_trading_enabled=false"
    if not settings.get("paper_auto_start", False) and not force:
        return (
            "paper_auto_start=false（安全默认）；"
            "确认要自动启动请改设置或加 --force"
        )
    return None


def launch_one(
    config: Path,
    dry_run: bool = False,
    timeout: int = DEFAULT_PER_TIMEOUT,
) -> dict[str, Any]:
    """拉起单个组合的 EOD 引擎，返回结果记录。"""
    cmd = [
        sys.executable, "-m", ENGINE_MODULE, "--config", str(config),
    ]
    if dry_run:
        cmd.append("--dry-run")

    logger.info("启动: %s", " ".join(cmd))
    started = datetime.now()
    try:
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
    except OSError as exc:
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
    parser.add_argument(
        "--dry-run", action="store_true", help="引擎只更新 NAV 不调仓",
    )
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

    names = resolve_portfolios(settings)
    if args.only:
        wanted = set(args.only)
        skipped = wanted - set(names)
        for name in sorted(skipped):
            logger.warning("--only %s 不在活跃清单里，忽略", name)
        names = [n for n in names if n in wanted]

    plan: list[Path] = []
    for name in names:
        cfg = config_path_for(name)
        if cfg is None:
            logger.error("组合 %s 没有对应的 YAML，跳过", name)
            continue
        plan.append(cfg)

    if args.list:
        print(f"活跃组合 {len(plan)} 个:")
        for cfg in plan:
            print(f"  - {cfg.stem}  ({cfg})")
        reason = check_gates(settings, force=args.force)
        print(f"闸门: {'通过' if reason is None else '拒绝 — ' + reason}")
        return 0

    reason = check_gates(settings, force=args.force)
    if reason is not None:
        logger.warning("未启动任何组合: %s", reason)
        return 0

    if not plan:
        logger.warning("没有可启动的组合")
        return 0

    results = [
        launch_one(cfg, dry_run=args.dry_run, timeout=args.per_timeout)
        for cfg in plan
    ]

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
