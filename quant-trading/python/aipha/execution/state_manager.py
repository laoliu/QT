"""Shared JSON state file persistence for paper/live scripts.

Centralizes atomic writes, backup rotation, and JSON encoding safeguards so
scripts/ops do not maintain divergent state write paths.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _send_critical_alert(title: str, body: str) -> None:
    """发送 CRITICAL 级飞书告警 — state 损坏时使用."""
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert
        send_feishu_alert(title, body, level="CRITICAL")
    except Exception as exc:
        if logging.getLogger(__name__).isEnabledFor(logging.WARNING):
            logging.getLogger(__name__).warning("飞书告警失败: %s", exc)


def cleanup_state_backups(
    state_dir: Path,
    *,
    file_name: str = "state.json",
    max_backups: int = 20,
    logger: logging.Logger | None = None,
) -> None:
    """Keep only the newest N timestamped state backups."""
    backups = sorted(state_dir.glob(f"{file_name}.*.bak"))
    while len(backups) > max_backups:
        oldest = backups.pop(0)
        try:
            oldest.unlink()
        except OSError as exc:
            if logger is not None:
                logger.warning("备份清理失败 %s: %s", oldest.name, exc)


def save_state_json_atomic(
    file_path: Path,
    payload: Mapping[str, Any],
    *,
    max_backups: int = 20,
    logger: logging.Logger | None = None,
) -> bool:
    """Persist JSON payload using tmp+replace with timestamped backups.

    Returns:
        True when the save succeeds, otherwise False.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        if logger is not None:
            logger.error("State JSON 编码失败: %s, 跳过此次保存", exc)
        return False

    tmp_path = file_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        if logger is not None:
            logger.error("State .tmp 写入失败 (磁盘满/权限?): %s", exc)
        return False

    if file_path.exists():
        backup_path = file_path.parent / (
            f"{file_path.name}.{datetime.now():%Y%m%d_%H%M%S}.bak"
        )
        try:
            shutil.copy2(file_path, backup_path)
        except OSError as exc:
            if logger is not None:
                logger.warning("备份失败: %s", exc)

    try:
        tmp_path.replace(file_path)
    except OSError as exc:
        if logger is not None:
            logger.error("State 替换失败: %s, 清理 .tmp", exc)
        tmp_path.unlink(missing_ok=True)
        return False

    cleanup_state_backups(
        file_path.parent,
        file_name=file_path.name,
        max_backups=max_backups,
        logger=logger,
    )
    return True


def restore_state_from_backup(
    file_path: Path,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """Attempt to load state from the main file, falling back to newest valid backup.

    Recovery chain:
      1. Try main file → success → return data
      2. If main file is missing/corrupt → scan .bak files newest-first
      3. If a valid backup found → copy it to main file → return data
      4. If all backups corrupt → return None (caller must raise CRITICAL alert)

    Returns:
        Parsed state dict, or None if unrecoverable.
    """
    # Step 1: try main file
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            if logger is not None:
                logger.warning("State 主文件损坏: %s, 尝试从备份恢复", exc)
            _send_critical_alert(
                f"[StateManager] State 主文件损坏",
                f"文件: {file_path}\n异常: {exc}\n正在尝试从备份恢复...",
            )

    # Step 2: scan backups newest-first
    backups = sorted(
        file_path.parent.glob(f"{file_path.name}.*.bak"),
        reverse=True,
    )
    if not backups:
        if logger is not None:
            logger.error("State 无可用备份: %s", file_path)
        return None

    for bak in backups:
        try:
            data = json.loads(bak.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if logger is not None:
                    logger.info("State 从备份恢复: %s", bak.name)
                # Step 3: restore backup to main file
                try:
                    shutil.copy2(bak, file_path)
                except OSError as exc:
                    if logger is not None:
                        logger.warning("备份复制到主文件失败: %s", exc)
                return data
        except (json.JSONDecodeError, OSError):
            continue

    # Step 4: all corrupt
    if logger is not None:
        logger.error("State 所有备份均损坏, 无法恢复: %s", file_path)
    _send_critical_alert(
        "[StateManager] State 全部损坏, 无法恢复!",
        f"文件: {file_path}\n已尝试所有备份均无效。需人工干预！",
    )
    return None