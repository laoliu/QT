"""Heartbeat SDK v2 — 心跳写入统一接口.

所有 cron 任务通过本 SDK 写入心跳信号（start/success/fail），
由 heartbeat_checker_v2 统一检查。

Usage::

    from aipha.monitor.heartbeat_sdk import write_start, write_success, write_fail

    write_start("paper_daily_runner")
    try:
        results = run_all_portfolios()
        write_success("paper_daily_runner", components=results)
    except Exception as e:
        write_fail("paper_daily_runner", error=str(e))
        raise
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

# 心跳文件目录 — 容器内 /app/logs/heartbeat，本地 logs/heartbeat
_HEARTBEAT_DIR: Path | None = None

HEARTBEAT_VERSION = 2


def _get_heartbeat_dir() -> Path:
    """获取心跳文件目录（延迟初始化）."""
    global _HEARTBEAT_DIR
    if _HEARTBEAT_DIR is not None:
        return _HEARTBEAT_DIR

    # 优先使用环境变量
    env_dir = os.environ.get("HEARTBEAT_DIR")
    if env_dir:
        _HEARTBEAT_DIR = Path(env_dir)
    else:
        # 自动检测：容器内 /app/logs/heartbeat，否则项目根目录
        app_path = Path("/app/logs/heartbeat")
        if app_path.exists():
            _HEARTBEAT_DIR = app_path
        else:
            # 从当前文件位置推导项目根目录
            project_root = PROJECT_ROOT
            _HEARTBEAT_DIR = project_root / "logs" / "heartbeat"

    _HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    return _HEARTBEAT_DIR


def set_heartbeat_dir(path: Path | str) -> None:
    """显式设置心跳目录（用于测试）."""
    global _HEARTBEAT_DIR
    _HEARTBEAT_DIR = Path(path)
    _HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    """当前时间 ISO 格式."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _atomic_write(filepath: Path, data: dict) -> None:
    """原子写入 JSON 文件（先写 .tmp 再 rename，§13.13 幂等安全）."""
    content = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent), suffix=".tmp", prefix=filepath.stem
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        # Windows: 目标存在时需先删除
        if filepath.exists():
            filepath.unlink()
        Path(tmp_path).rename(filepath)
    except Exception:
        # 清理临时文件
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _heartbeat_path(task: str) -> Path:
    """获取任务心跳文件路径."""
    return _get_heartbeat_dir() / f"{task}.json"


def write_start(task: str, *, metadata: dict[str, Any] | None = None) -> None:
    """写入任务开始信号.

    Args:
        task: 任务名称（如 'paper_daily_runner', 'data_preheat'）
        metadata: 附加元数据（如 trade_date）
    """
    hb_path = _heartbeat_path(task)
    data = {
        "task": task,
        "version": HEARTBEAT_VERSION,
        "state": "running",
        "start_ts": _now_iso(),
        "end_ts": None,
        "duration_seconds": None,
        "pid": os.getpid(),
        "exit_code": None,
        "components": {},
        "metadata": metadata or {},
    }
    _atomic_write(hb_path, data)
    logger.info("Heartbeat START: %s (pid=%d)", task, os.getpid())


def write_success(
    task: str,
    *,
    components: dict[str, dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """写入任务成功完成信号.

    Args:
        task: 任务名称
        components: 组件状态字典，如 {"v5a": {"status": "ok", "trades": 2}}
        metadata: 附加元数据
    """
    hb_path = _heartbeat_path(task)
    now = _now_iso()

    # 尝试读取 start_ts 计算 duration
    start_ts = None
    duration = None
    if hb_path.exists():
        try:
            prev = json.loads(hb_path.read_text(encoding="utf-8"))
            start_ts = prev.get("start_ts")
            if start_ts:
                start_dt = datetime.fromisoformat(start_ts)
                now_dt = datetime.fromisoformat(now)
                duration = (now_dt - start_dt).total_seconds()
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    data = {
        "task": task,
        "version": HEARTBEAT_VERSION,
        "state": "success",
        "start_ts": start_ts or now,
        "end_ts": now,
        "duration_seconds": round(duration, 1) if duration else None,
        "pid": os.getpid(),
        "exit_code": 0,
        "components": components or {},
        "metadata": metadata or {},
    }
    _atomic_write(hb_path, data)
    logger.info(
        "Heartbeat SUCCESS: %s (duration=%.1fs, components=%d)",
        task,
        duration or 0,
        len(components or {}),
    )


def write_fail(
    task: str,
    *,
    error: str,
    components: dict[str, dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """写入任务失败信号.

    Args:
        task: 任务名称
        error: 错误信息
        components: 组件状态字典
        metadata: 附加元数据
    """
    hb_path = _heartbeat_path(task)
    now = _now_iso()

    # 尝试读取 start_ts
    start_ts = None
    duration = None
    if hb_path.exists():
        try:
            prev = json.loads(hb_path.read_text(encoding="utf-8"))
            start_ts = prev.get("start_ts")
            if start_ts:
                start_dt = datetime.fromisoformat(start_ts)
                now_dt = datetime.fromisoformat(now)
                duration = (now_dt - start_dt).total_seconds()
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    data = {
        "task": task,
        "version": HEARTBEAT_VERSION,
        "state": "failed",
        "start_ts": start_ts or now,
        "end_ts": now,
        "duration_seconds": round(duration, 1) if duration else None,
        "pid": os.getpid(),
        "exit_code": 1,
        "error": error,
        "components": components or {},
        "metadata": metadata or {},
    }
    _atomic_write(hb_path, data)
    logger.warning("Heartbeat FAIL: %s — %s", task, error)


def read_heartbeat(task: str) -> dict[str, Any] | None:
    """读取任务心跳文件.

    Returns:
        心跳数据字典，文件不存在或解析失败返回 None
    """
    hb_path = _heartbeat_path(task)
    if not hb_path.exists():
        return None
    try:
        return json.loads(hb_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read heartbeat %s: %s", task, e)
        return None
