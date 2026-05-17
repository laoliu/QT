"""Alert Router — 心跳检查结果的告警聚合、去重、恢复通知.

职责：
1. 将多个 CheckResult 聚合为结构化告警消息
2. 基于历史去重（同一告警 dedup_window 内不重发）
3. 恢复通知（DOWN→UP 推送恢复消息）
4. 组件级摘要（per_component 任务的组件状态汇总）

注：飞书限流保护暂不实现（用户决策：暂时不限制）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 去重历史文件
_DEDUP_HISTORY_FILE = "alert_dedup_history.json"


@dataclass
class AlertMessage:
    """一条聚合后的告警消息."""

    title: str
    content: str
    level: str = "WARNING"
    suppressed: bool = False


def format_check_alerts(
    results: list[Any],
    *,
    dedup_window_seconds: int = 3600,
    recovery_notify: bool = True,
    heartbeat_dir: Path | None = None,
) -> list[AlertMessage]:
    """将检查结果格式化为告警消息列表.

    Args:
        results: CheckResult 列表
        dedup_window_seconds: 去重窗口（秒）
        recovery_notify: 是否发送恢复通知
        heartbeat_dir: 心跳目录（用于存储去重历史）

    Returns:
        需要发送的告警消息列表
    """
    from aipha.monitor.heartbeat_checker_v2 import CheckState  # noqa: PLC0415

    # 分类结果
    critical_results = [
        r for r in results
        if r.state in (CheckState.DOWN, CheckState.HUNG, CheckState.MISSING)
        and not r.suppressed
    ]
    warning_results = [
        r for r in results
        if r.state == CheckState.LATE and not r.suppressed
    ]
    recovery_results = [
        r for r in results if r.is_recovery and not r.suppressed
    ]

    messages: list[AlertMessage] = []

    # 聚合 CRITICAL + WARNING 为单条消息
    if critical_results or warning_results:
        msg = _build_alert_message(critical_results, warning_results)
        # 去重检查
        if not _is_dedup_hit(msg.title, msg.level, dedup_window_seconds, heartbeat_dir):
            messages.append(msg)
            _record_dedup(msg.title, msg.level, heartbeat_dir)
        else:
            logger.info("Alert deduplicated: %s", msg.title)

    # 恢复通知
    if recovery_notify and recovery_results:
        for r in recovery_results:
            recovery_msg = _build_recovery_message(r)
            messages.append(recovery_msg)

    return messages


def _build_alert_message(
    critical_results: list[Any],
    warning_results: list[Any],
) -> AlertMessage:
    """构建聚合告警消息."""
    n_critical = len(critical_results)
    n_warning = len(warning_results)

    title = f"Deadman Check — {n_critical} CRITICAL, {n_warning} WARNING"
    level = "CRITICAL" if n_critical > 0 else "WARNING"

    lines: list[str] = []
    for r in critical_results:
        lines.append(f"[CRITICAL] {r.name}")
        lines.append(f"  状态: {r.state.value}")
        lines.append(f"  {r.message}")
        if r.last_success_ts:
            lines.append(f"  上次成功: {r.last_success_ts}")
        if r.components:
            comp_summary = _summarize_components(r.components)
            if comp_summary:
                lines.append(f"  组件: {comp_summary}")
        lines.append("")

    for r in warning_results:
        lines.append(f"[WARNING] {r.name}")
        lines.append(f"  {r.message}")
        lines.append("")

    # 操作建议
    lines.append("操作建议: 检查 scheduler 容器日志 (docker logs aipha-scheduler)")

    return AlertMessage(
        title=title,
        content="\n".join(lines),
        level=level,
    )


def _build_recovery_message(result: Any) -> AlertMessage:
    """构建恢复通知消息."""
    title = f"Deadman Recovery — {result.name}"
    lines = [
        f"✅ 任务恢复正常: {result.name}",
        f"  恢复时间: {result.last_success_ts or 'unknown'}",
    ]
    if result.components:
        comp_summary = _summarize_components(result.components)
        if comp_summary:
            lines.append(f"  组件: {comp_summary}")

    return AlertMessage(
        title=title,
        content="\n".join(lines),
        level="INFO",
    )


def _summarize_components(components: dict[str, Any]) -> str:
    """组件状态摘要（简洁单行）."""
    if not components:
        return ""

    ok_count = 0
    error_names = []
    for name, info in components.items():
        if isinstance(info, dict):
            status = info.get("status", "unknown")
        else:
            status = str(info)
        if status == "ok":
            ok_count += 1
        else:
            error_names.append(name)

    parts = []
    if ok_count > 0:
        parts.append(f"{ok_count}个正常")
    if error_names:
        parts.append(f"{len(error_names)}个异常({','.join(error_names[:3])})")
    return " / ".join(parts)


def _dedup_file_path(heartbeat_dir: Path | None) -> Path | None:
    """获取去重历史文件路径."""
    if heartbeat_dir is None:
        return None
    return heartbeat_dir / _DEDUP_HISTORY_FILE


def _is_dedup_hit(
    title: str,
    level: str,
    window_seconds: int,
    heartbeat_dir: Path | None,
) -> bool:
    """检查告警是否在去重窗口内."""
    dedup_path = _dedup_file_path(heartbeat_dir)
    if dedup_path is None or not dedup_path.exists():
        return False

    try:
        history = json.loads(dedup_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    key = f"{title}|{level}"
    last_sent = history.get(key, 0)
    return (time.time() - last_sent) < window_seconds


def _record_dedup(
    title: str,
    level: str,
    heartbeat_dir: Path | None,
) -> None:
    """记录告警发送时间（用于去重）."""
    dedup_path = _dedup_file_path(heartbeat_dir)
    if dedup_path is None:
        return

    history: dict[str, float] = {}
    if dedup_path.exists():
        try:
            history = json.loads(dedup_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    key = f"{title}|{level}"
    history[key] = time.time()

    # 清理过期条目（> 24h）
    now = time.time()
    history = {k: v for k, v in history.items() if now - v < 86400}

    try:
        dedup_path.write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("Failed to write dedup history: %s", e)


def dispatch_alerts(
    results: list[Any],
    *,
    dedup_window_seconds: int = 3600,
    recovery_notify: bool = True,
    heartbeat_dir: Path | None = None,
) -> int:
    """完整告警调度流程：格式化 → 去重 → 发送飞书.

    Returns:
        实际发送的告警数量
    """
    messages = format_check_alerts(
        results,
        dedup_window_seconds=dedup_window_seconds,
        recovery_notify=recovery_notify,
        heartbeat_dir=heartbeat_dir,
    )

    if not messages:
        logger.info("No alerts to dispatch")
        return 0

    sent_count = 0
    for msg in messages:
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert  # noqa: PLC0415

            success = send_feishu_alert(
                title=msg.title,
                content=msg.content,
                level=msg.level,
                dedup=False,  # 我们自己管理去重
            )
            if success:
                sent_count += 1
                logger.info("Alert sent: [%s] %s", msg.level, msg.title)
            else:
                logger.warning("Alert send failed: %s", msg.title)
        except Exception as e:
            logger.error("Alert dispatch error: %s — %s", msg.title, e)

    return sent_count
