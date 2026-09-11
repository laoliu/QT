"""§14.2 Kill Switch 巡检 / Deadman Checker.

独立于 cron 调度的健康探针：
- 检查上次成功调仓的时间戳，超期未执行 → CRITICAL 告警
- 检查 Kill Switch flag 是否被遗留激活
- 检查状态文件最后修改时间是否过期

调用方式（建议每 30 分钟由独立 cron 触发）：
    python -m aipha.monitor.deadman_checker
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aipha.utils.paths import KILL_SWITCH_PATH, PROJECT_ROOT

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8))
_PROJ_ROOT = PROJECT_ROOT


class DeadmanCheckResult:
    """巡检结果。"""

    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.critical_count: int = 0
        self.warning_count: int = 0

    @property
    def healthy(self) -> bool:
        return self.critical_count == 0

    def add(self, name: str, status: str, message: str) -> None:
        self.checks.append({"name": name, "status": status, "message": message})
        if status == "CRITICAL":
            self.critical_count += 1
        elif status == "WARNING":
            self.warning_count += 1


def run_deadman_check(
    *,
    state_dir: Path | None = None,
    max_silence_hours: float = 26.0,
    profiles: list[str] | None = None,
) -> DeadmanCheckResult:
    """执行全套巡检。

    Args:
        state_dir: 状态文件目录（默认 data/paper_state/）
        max_silence_hours: 状态文件最后更新超过此小时数 → 告警
        profiles: 需要检查的 profile 列表（默认自动检测）

    Returns:
        DeadmanCheckResult 包含所有检查结果
    """
    result = DeadmanCheckResult()

    # 1. Kill Switch flag 检查
    _check_kill_switch_flag(result)

    # 2. 状态文件新鲜度检查
    if state_dir is None:
        state_dir = _PROJ_ROOT / "data" / "paper_state"
    _check_state_freshness(result, state_dir, max_silence_hours, profiles)

    # 3. system_settings 一致性检查
    _check_system_settings(result)

    # 发送告警（如有）
    if result.critical_count > 0:
        _dispatch_alert(result)

    return result


def _check_kill_switch_flag(result: DeadmanCheckResult) -> None:
    """检查 Kill Switch 是否被遗留激活。"""
    kill_flag = KILL_SWITCH_PATH
    if kill_flag.exists():
        try:
            content = kill_flag.read_text(encoding="utf-8").strip()
            mtime = datetime.fromtimestamp(
                kill_flag.stat().st_mtime, tz=_CN_TZ
            )
            age_hours = (
                datetime.now(tz=_CN_TZ) - mtime
            ).total_seconds() / 3600
        except OSError:
            content = "unknown"
            age_hours = 0.0

        if age_hours > 24:
            result.add(
                "kill_switch_stale",
                "CRITICAL",
                f"Kill Switch 激活超过 {age_hours:.1f} 小时未处理: {content}",
            )
        else:
            result.add(
                "kill_switch_active",
                "WARNING",
                f"Kill Switch 当前激活({age_hours:.1f}h): {content}",
            )
    else:
        result.add("kill_switch", "OK", "Kill Switch 未激活")


def _check_state_freshness(
    result: DeadmanCheckResult,
    state_dir: Path,
    max_silence_hours: float,
    profiles: list[str] | None,
) -> None:
    """检查状态文件最后更新时间。"""
    if not state_dir.exists():
        result.add(
            "state_dir",
            "WARNING",
            f"状态目录不存在: {state_dir}",
        )
        return

    state_files = list(state_dir.glob("*_state.json"))
    if profiles:
        state_files = [
            f for f in state_files
            if any(p in f.stem for p in profiles)
        ]
    else:
        # 单一来源过滤：仅保留活跃组合的状态文件
        try:
            from _active_portfolios import get_active_portfolios
            _active = get_active_portfolios()
            if _active:
                state_files = [
                    f for f in state_files
                    if f.stem.replace("_state", "") in _active
                ]
        except ImportError:
            pass  # 非 scripts/ops 环境下 fallback 到全量检查

    if not state_files:
        result.add("state_files", "WARNING", "未找到任何状态文件")
        return

    now = time.time()
    for sf in state_files:
        try:
            age_hours = (now - sf.stat().st_mtime) / 3600
        except OSError:
            result.add(
                f"state_{sf.stem}",
                "CRITICAL",
                f"无法读取状态文件: {sf}",
            )
            continue

        if age_hours > max_silence_hours:
            result.add(
                f"state_{sf.stem}",
                "CRITICAL",
                f"状态文件 {sf.name} 已 {age_hours:.1f}h 未更新"
                f"（阈值 {max_silence_hours}h）— cron 可能中断",
            )
        else:
            result.add(
                f"state_{sf.stem}",
                "OK",
                f"{sf.name} 最后更新 {age_hours:.1f}h 前",
            )


def _check_system_settings(result: DeadmanCheckResult) -> None:
    """检查 system_settings.json 是否存在且格式正确。"""
    settings_path = _PROJ_ROOT / "configs" / "paper" / "system_settings.json"
    if not settings_path.exists():
        result.add(
            "system_settings",
            "WARNING",
            "system_settings.json 不存在（默认允许交易）",
        )
        return
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if not data.get("paper_trading_enabled", True):
            result.add(
                "system_settings",
                "WARNING",
                "paper_trading_enabled=false — 交易已全局禁用",
            )
        else:
            result.add("system_settings", "OK", "配置正常")
    except (json.JSONDecodeError, OSError) as exc:
        result.add(
            "system_settings",
            "CRITICAL",
            f"system_settings.json 损坏: {exc}",
        )


def _dispatch_alert(result: DeadmanCheckResult) -> None:
    """发送飞书告警。"""
    lines = [
        f"🚨 Deadman Check: {result.critical_count} CRITICAL, "
        f"{result.warning_count} WARNING"
    ]
    for c in result.checks:
        if c["status"] in ("CRITICAL", "WARNING"):
            lines.append(f"  [{c['status']}] {c['name']}: {c['message']}")
    msg = "\n".join(lines)
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert
        send_feishu_alert(msg, level="CRITICAL")
    except Exception as exc:
        logger.error("Deadman alert dispatch failed: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = run_deadman_check()
    print(f"Healthy: {res.healthy}")
    for c in res.checks:
        print(f"  [{c['status']}] {c['name']}: {c['message']}")
    raise SystemExit(0 if res.healthy else 1)
