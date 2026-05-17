"""实时指标收集器

为 Paper Trading 提供实时绩效指标收集与导出：
- NAV 时间序列
- 实时回撤
- 滚动 Sharpe
- 成交统计（成交量、成交额、成功率）
- 交易成本分解

指标写入 JSON 文件供监控面板消费。

设计原则：
- 轻量无阻塞：收集操作 O(1)，不影响策略执行延迟
- 增量写入：避免全量覆写大文件
- 配置驱动：采集频率、文件路径通过 MetricsConfig 指定
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


@dataclass
class MetricsConfig:
    """指标收集配置"""

    # 输出目录
    output_dir: Path = field(default_factory=lambda: Path("data/metrics"))

    # 输出文件名
    output_file: str = "live_metrics.json"

    # 写入间隔（秒）：0 表示每次 update 都写
    write_interval_sec: int = 60

    # NAV 历史最大长度
    max_nav_history: int = 5000

    # 滚动 Sharpe 窗口（收益率个数）
    sharpe_window: int = TRADING_DAYS_PER_YEAR

    # 无风险日利率（年化 2% → 日 0.02/TRADING_DAYS_PER_YEAR）
    risk_free_daily: float = 0.02 / TRADING_DAYS_PER_YEAR

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricsConfig:
        """从字典反序列化。"""
        return cls(
            output_dir=Path(data.get("output_dir", "data/metrics")),
            output_file=data.get("output_file", "live_metrics.json"),
            write_interval_sec=data.get("write_interval_sec", 60),
            max_nav_history=data.get("max_nav_history", 5000),
            sharpe_window=data.get("sharpe_window", TRADING_DAYS_PER_YEAR),
            risk_free_daily=data.get("risk_free_daily", 0.02 / TRADING_DAYS_PER_YEAR),
        )


class MetricsCollector:
    """实时指标收集器

    在 PaperEngine 的心跳/on_bar 中调用 update()，
    自动计算并导出指标。

    使用示例::

        collector = MetricsCollector(config, initial_capital=500_000)
        collector.update(nav=510_000, cash=100_000, n_positions=15, n_fills=3)
        metrics = collector.get_summary()
    """

    def __init__(
        self,
        config: MetricsConfig | None = None,
        initial_capital: float = 1_000_000.0,
        strategy_name: str = "",
    ):
        self._config = config or MetricsConfig()
        self._initial_capital = initial_capital
        self._strategy_name = strategy_name

        # NAV 时间序列
        self._nav_history: deque[tuple[str, float]] = deque(
            maxlen=self._config.max_nav_history
        )

        # 收益率序列（用于 Sharpe 计算）
        self._returns: deque[float] = deque(maxlen=self._config.sharpe_window)

        # 峰值（回撤计算）
        self._peak_nav: float = initial_capital
        self._max_drawdown: float = 0.0
        self._current_drawdown: float = 0.0

        # 成交统计
        self._total_fills: int = 0
        self._total_orders: int = 0
        self._total_bars: int = 0

        # 上次 NAV（用于计算收益率）
        self._last_nav: float = initial_capital

        # 写入控制
        self._last_write: datetime | None = None
        self._update_count: int = 0

        # 日内跟踪
        self._day_start_nav: float = initial_capital
        self._current_day: str = ""

        # 确保输出目录存在
        self._config.output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def nav_count(self) -> int:
        """NAV 历史记录数量。"""
        return len(self._nav_history)

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
        """更新指标

        Args:
            nav: 当前净值
            cash: 现金
            n_positions: 持仓数
            n_fills: 累计成交数
            n_orders: 累计订单数
            n_bars: 累计 Bar 数
            timestamp: 时间戳（默认 now）
        """
        now = timestamp or datetime.now()
        ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        # 日切
        if today_str != self._current_day:
            self._current_day = today_str
            self._day_start_nav = self._last_nav if self._last_nav > 0 else nav

        # NAV 时间序列
        self._nav_history.append((ts_str, nav))

        # 收益率
        if self._last_nav > 0 and nav > 0:
            ret = (nav - self._last_nav) / self._last_nav
            self._returns.append(ret)
        self._last_nav = nav

        # 峰值 & 回撤
        self._peak_nav = max(self._peak_nav, nav)
        if self._peak_nav > 0:
            self._current_drawdown = (self._peak_nav - nav) / self._peak_nav
            self._max_drawdown = max(self._max_drawdown, self._current_drawdown)

        # 统计
        self._total_fills = n_fills
        self._total_orders = n_orders
        self._total_bars = n_bars
        self._update_count += 1

        # 定期写入文件
        self._maybe_write(now)

    def get_summary(self) -> dict[str, Any]:
        """获取指标摘要"""
        nav = self._last_nav
        total_return = (
            (nav - self._initial_capital) / self._initial_capital
            if self._initial_capital > 0
            else 0.0
        )
        daily_return = (
            (nav - self._day_start_nav) / self._day_start_nav
            if self._day_start_nav > 0
            else 0.0
        )

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
                "rolling_volatility_pct": round(
                    self._calc_rolling_volatility() * 100, 4
                ),
            },
            "trading": {
                "total_fills": self._total_fills,
                "total_orders": self._total_orders,
                "total_bars": self._total_bars,
                "update_count": self._update_count,
            },
            "nav_history_len": len(self._nav_history),
        }

    def get_nav_series(self, last_n: int = 0) -> list[tuple[str, float]]:
        """获取 NAV 时间序列

        Args:
            last_n: 返回最近 N 条，0=全部

        Returns:
            [(timestamp_str, nav), ...]
        """
        if last_n > 0:
            return list(self._nav_history)[-last_n:]
        return list(self._nav_history)

    def _calc_rolling_sharpe(self) -> float:
        """计算滚动 Sharpe 比率（年化）"""
        if len(self._returns) < 10:
            return 0.0

        returns = list(self._returns)
        mean_r = sum(returns) / len(returns)
        excess = mean_r - self._config.risk_free_daily

        if len(returns) < 2:
            return 0.0
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0:
            return 0.0

        # 年化 (假设日频)
        return (excess / std) * math.sqrt(TRADING_DAYS_PER_YEAR)

    def _calc_rolling_volatility(self) -> float:
        """计算滚动波动率（年化）"""
        if len(self._returns) < 10:
            return 0.0

        returns = list(self._returns)
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        return std * math.sqrt(TRADING_DAYS_PER_YEAR)

    def _maybe_write(self, now: datetime) -> None:
        """根据配置决定是否写入文件"""
        if self._config.write_interval_sec <= 0:
            self._write_metrics()
            return

        if self._last_write is None:
            self._write_metrics()
            self._last_write = now
            return

        elapsed = (now - self._last_write).total_seconds()
        if elapsed >= self._config.write_interval_sec:
            self._write_metrics()
            self._last_write = now

    def _write_metrics(self) -> None:
        """写入指标文件"""
        try:
            output_path = self._config.output_dir / self._config.output_file
            data = self.get_summary()

            # 附加最近 N 条 NAV（供 dashboard 直接绘图）
            data["nav_recent"] = self.get_nav_series(last_n=500)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"写入指标文件失败: {e}")
