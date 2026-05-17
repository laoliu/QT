"""Advisory 定时任务调度器 — 盘前/盘中/盘后。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class TaskPhase(str, Enum):
    PRE_MARKET = "pre_market"    # 08:30-09:15
    INTRADAY = "intraday"        # 09:30-15:00
    POST_MARKET = "post_market"  # 15:30-17:00


@dataclass
class ScheduledTask:
    name: str
    phase: TaskPhase
    run_time: time  # 预定执行时间
    func: Callable[..., Coroutine[Any, Any, Any]]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class AdvisoryScheduler:
    """Advisory 定时调度器。"""

    def __init__(self) -> None:
        self._tasks: list[ScheduledTask] = []
        self._running = False

    def register(self, task: ScheduledTask) -> None:
        self._tasks.append(task)
        logger.info("Registered task: %s at %s", task.name, task.run_time)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "phase": t.phase.value,
                "run_time": t.run_time.isoformat(),
                "enabled": t.enabled,
            }
            for t in self._tasks
        ]

    async def run_phase(self, phase: TaskPhase) -> list[dict[str, Any]]:
        """手动触发指定阶段任务。"""
        results: list[dict[str, Any]] = []
        tasks = [t for t in self._tasks if t.phase == phase and t.enabled]
        for task in tasks:
            try:
                await task.func(*task.args, **task.kwargs)
                results.append({"name": task.name, "status": "ok"})
            except Exception as e:
                logger.error("Task %s failed: %s", task.name, e)
                results.append({"name": task.name, "status": "error", "msg": str(e)})
        return results

    def get_current_phase(self) -> TaskPhase | None:
        now = datetime.now().time()
        if time(8, 30) <= now < time(9, 15):
            return TaskPhase.PRE_MARKET
        if time(9, 30) <= now < time(15, 0):
            return TaskPhase.INTRADAY
        if time(15, 30) <= now < time(17, 0):
            return TaskPhase.POST_MARKET
        return None

    def stop(self) -> None:
        self._running = False
