"""实时指标收集器 — NAV/回撤/滚动Sharpe/成交统计"""
from __future__ import annotations

import json
import math
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricsConfig:
    output_dir: Path = field(default_factory=lambda: Path("data/metrics"))
    output_file: str = "live_metrics.json"
    write_interval_sec: int = 60
    max_nav_history: int = 5000
    sharpe_window: int = 252
    risk_free_daily: float = 0.02 / 252


class MetricsCollector:
    """实时指标收集器

    用法:
        collector = MetricsCollector(initial_capital=1_000_000)
        collector.update(nav=1_020_000, n_positions=15, n_fills=3)
        summary = collector.get_summary()
    """

    def __init__(
        self,
        config: MetricsConfig | None = None,
        initial_capital: float = 1_000_000,
        strategy_name: str = "",
    ):
        self._config = config or MetricsConfig()
        self._initial_capital = initial_capital
        self._strategy_name = strategy_name
        self._nav_history: deque[tuple[str, float]] = deque(maxlen=self._config.max_nav_history)
        self._returns: deque[float] = deque(maxlen=self._config.sharpe_window)
        self._peak_nav = initial_capital
        self._max_drawdown = 0.0
        self._current_drawdown = 0.0
        self._total_fills = 0
        self._total_orders = 0
        self._total_bars = 0
        self._last_nav = initial_capital
        self._last_write: datetime | None = None
        self._update_count = 0
        self._day_start_nav = initial_capital
        self._current_day = ""
        self._config.output_dir.mkdir(parents=True, exist_ok=True)

    def update(
        self,
        nav: float,
        cash: float = 0.0,
        n_positions: int = 0,
        n_fills: int = 0,
        n_orders: int = 0,
        n_bars: int = 0,
        timestamp: datetime | None = None,
    ) -> None:
        now = timestamp or datetime.now()
        ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        if today_str != self._current_day:
            self._current_day = today_str
            self._day_start_nav = self._last_nav if self._last_nav > 0 else nav

        self._nav_history.append((ts_str, nav))

        if self._last_nav > 0 and nav > 0:
            self._returns.append((nav - self._last_nav) / self._last_nav)
        self._last_nav = nav

        self._peak_nav = max(self._peak_nav, nav)
        if self._peak_nav > 0:
            self._current_drawdown = (self._peak_nav - nav) / self._peak_nav
            self._max_drawdown = max(self._max_drawdown, self._current_drawdown)

        self._total_fills = n_fills
        self._total_orders = n_orders
        self._total_bars = n_bars
        self._update_count += 1

        self._maybe_write(now)

    def get_summary(self) -> dict[str, Any]:
        nav = self._last_nav
        total_return = (nav - self._initial_capital) / self._initial_capital if self._initial_capital > 0 else 0
        daily_return = (nav - self._day_start_nav) / self._day_start_nav if self._day_start_nav > 0 else 0

        return {
            "strategy_name": self._strategy_name,
            "timestamp": datetime.now().isoformat(),
            "nav": {
                "current": round(nav, 2),
                "initial": round(self._initial_capital, 2),
                "peak": round(self._peak_nav, 2),
                "total_return_pct": round(total_return * 100, 4),
                "daily_return_pct": round(daily_return * 100, 4),
            },
            "risk": {
                "current_drawdown_pct": round(self._current_drawdown * 100, 4),
                "max_drawdown_pct": round(self._max_drawdown * 100, 4),
                "rolling_sharpe": round(self._calc_rolling_sharpe(), 4),
                "rolling_volatility_pct": round(self._calc_rolling_volatility() * 100, 4),
            },
            "trading": {
                "total_fills": self._total_fills,
                "total_orders": self._total_orders,
                "total_bars": self._total_bars,
                "update_count": self._update_count,
            },
        }

    def get_nav_series(self, last_n: int = 0) -> list[tuple[str, float]]:
        if last_n > 0:
            return list(self._nav_history)[-last_n:]
        return list(self._nav_history)

    def _calc_rolling_sharpe(self) -> float:
        if len(self._returns) < 10:
            return 0.0
        returns = list(self._returns)
        mean_r = sum(returns) / len(returns)
        excess = mean_r - self._config.risk_free_daily
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0
        return (excess / std) * math.sqrt(252) if std > 0 else 0.0

    def _calc_rolling_volatility(self) -> float:
        if len(self._returns) < 10:
            return 0.0
        returns = list(self._returns)
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(variance * 252) if variance > 0 else 0.0

    def _maybe_write(self, now: datetime) -> None:
        interval = self._config.write_interval_sec
        if interval <= 0:
            self._write_metrics()
            return
        if self._last_write is None or (now - self._last_write).total_seconds() >= interval:
            self._write_metrics()
            self._last_write = now

    def _write_metrics(self) -> None:
        try:
            path = self._config.output_dir / self._config.output_file
            path.write_text(
                json.dumps(self.get_summary(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("写入指标文件失败: %s", e)

    def reset(self) -> None:
        self._nav_history.clear()
        self._returns.clear()
        self._peak_nav = self._initial_capital
        self._max_drawdown = 0.0
        self._current_drawdown = 0.0
        self._last_nav = self._initial_capital
        self._update_count = 0
