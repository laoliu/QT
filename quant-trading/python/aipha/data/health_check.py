"""数据源健康检查 — 连通性 / 延迟 / 数据新鲜度"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    name: str
    healthy: bool
    latency_ms: float = 0.0
    message: str = ""
    last_check: str = field(default_factory=lambda: datetime.now().isoformat())
    details: dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """数据源健康检查器"""

    def __init__(self):
        self._checks: dict[str, Callable[[], HealthStatus]] = {}

    def register(self, name: str, check_fn: Callable[[], HealthStatus]):
        self._checks[name] = check_fn

    def check_all(self) -> list[HealthStatus]:
        results = []
        for name, fn in self._checks.items():
            try:
                t0 = time.time()
                status = fn()
                status.latency_ms = (time.time() - t0) * 1000
                results.append(status)
            except Exception as e:
                results.append(HealthStatus(
                    name=name, healthy=False, message=str(e)))
        return results

    def check_one(self, name: str) -> HealthStatus:
        fn = self._checks.get(name)
        if fn is None:
            return HealthStatus(name=name, healthy=False, message="未注册")
        try:
            t0 = time.time()
            status = fn()
            status.latency_ms = (time.time() - t0) * 1000
            return status
        except Exception as e:
            return HealthStatus(name=name, healthy=False, message=str(e))

    def summary(self) -> dict[str, Any]:
        results = self.check_all()
        return {
            "total": len(results),
            "healthy": sum(1 for r in results if r.healthy),
            "unhealthy": sum(1 for r in results if not r.healthy),
            "checks": [{
                "name": r.name, "healthy": r.healthy,
                "latency_ms": round(r.latency_ms, 1), "message": r.message,
            } for r in results],
        }


def make_tushare_check(token: str = "") -> Callable[[], HealthStatus]:
    def check() -> HealthStatus:
        try:
            import tushare as ts
            if token:
                ts.set_token(token)
            pro = ts.pro_api()
            df = pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20240102")
            if df is not None and len(df) > 0:
                return HealthStatus(name="tushare", healthy=True, message="OK")
            return HealthStatus(name="tushare", healthy=False, message="空数据")
        except Exception as e:
            return HealthStatus(name="tushare", healthy=False, message=str(e))
    return check
