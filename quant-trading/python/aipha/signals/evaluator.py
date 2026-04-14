"""
信号评估模块

提供策略信号质量分析功能：
- 信号统计：总数、买卖比例、持有期
- 收益分析：信号收益率、胜率、盈亏比
- 时序分析：信号集中度、过度交易检测
- 信号衰减分析：不同持有期的收益变化

使用示例：
    evaluator = SignalEvaluator(signals, prices)
    report = evaluator.evaluate()
    print(report.summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import numpy as np
from numpy.typing import NDArray


class SignalType(Enum):
    """信号类型"""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Signal:
    """交易信号"""

    symbol: str
    signal_type: SignalType
    timestamp: datetime
    price: float
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalStats:
    """信号统计"""

    total_signals: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0
    unique_symbols: int = 0
    avg_confidence: float = 0.0
    signals_per_day: float = 0.0


@dataclass
class ReturnStats:
    """收益统计"""

    avg_return: float = 0.0
    median_return: float = 0.0
    std_return: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_return: float = 0.0
    worst_return: float = 0.0
    total_pnl: float = 0.0


@dataclass
class DecayStats:
    """信号衰减统计"""

    holding_periods: list[int] = field(default_factory=list)
    avg_returns: list[float] = field(default_factory=list)
    win_rates: list[float] = field(default_factory=list)
    optimal_period: int = 1


@dataclass
class SignalEvaluationReport:
    """信号评估报告"""

    signal_stats: SignalStats
    return_stats: ReturnStats
    decay_stats: DecayStats
    by_symbol: dict[str, ReturnStats] = field(default_factory=dict)
    evaluation_time: datetime = field(default_factory=datetime.now)

    def summary(self) -> str:
        """生成摘要报告"""
        lines = [
            "=" * 50,
            "信号评估报告",
            "=" * 50,
            "",
            "【信号统计】",
            f"  总信号数: {self.signal_stats.total_signals}",
            f"  买入信号: {self.signal_stats.buy_signals} "
            f"({self._pct(self.signal_stats.buy_signals, self.signal_stats.total_signals)})",
            f"  卖出信号: {self.signal_stats.sell_signals} "
            f"({self._pct(self.signal_stats.sell_signals, self.signal_stats.total_signals)})",
            f"  覆盖标的: {self.signal_stats.unique_symbols}",
            f"  平均置信度: {self.signal_stats.avg_confidence:.2%}",
            f"  日均信号数: {self.signal_stats.signals_per_day:.1f}",
            "",
            "【收益分析】",
            f"  平均收益: {self.return_stats.avg_return:.2%}",
            f"  中位数收益: {self.return_stats.median_return:.2%}",
            f"  收益标准差: {self.return_stats.std_return:.2%}",
            f"  胜率: {self.return_stats.win_rate:.2%}",
            f"  盈亏比: {self.return_stats.profit_factor:.2f}",
            f"  平均盈利: {self.return_stats.avg_win:.2%}",
            f"  平均亏损: {self.return_stats.avg_loss:.2%}",
            f"  最佳收益: {self.return_stats.best_return:.2%}",
            f"  最差收益: {self.return_stats.worst_return:.2%}",
            "",
            "【信号衰减】",
            f"  最优持有期: {self.decay_stats.optimal_period} 天",
        ]

        if self.decay_stats.holding_periods:
            lines.append("  各持有期收益:")
            for i, period in enumerate(self.decay_stats.holding_periods):
                ret = self.decay_stats.avg_returns[i] if i < len(self.decay_stats.avg_returns) else 0
                wr = self.decay_stats.win_rates[i] if i < len(self.decay_stats.win_rates) else 0
                lines.append(f"    {period}日: 收益={ret:.2%}, 胜率={wr:.2%}")

        lines.extend([
            "",
            "=" * 50,
        ])

        return "\n".join(lines)

    def _pct(self, part: int, total: int) -> str:
        """计算百分比"""
        if total == 0:
            return "0.00%"
        return f"{part / total:.2%}"


class SignalEvaluator:
    """信号评估器"""

    def __init__(
        self,
        signals: list[Signal],
        price_data: dict[str, dict[datetime, float]],
        holding_periods: list[int] | None = None,
    ):
        """初始化信号评估器

        Args:
            signals: 信号列表
            price_data: 价格数据 {symbol: {datetime: price}}
            holding_periods: 持有期列表（天数），默认 [1, 3, 5, 10, 20]
        """
        self.signals = signals
        self.price_data = price_data
        self.holding_periods = holding_periods or [1, 3, 5, 10, 20]

    def evaluate(self) -> SignalEvaluationReport:
        """执行信号评估

        Returns:
            评估报告
        """
        signal_stats = self._compute_signal_stats()
        return_stats, by_symbol = self._compute_return_stats()
        decay_stats = self._compute_decay_stats()

        return SignalEvaluationReport(
            signal_stats=signal_stats,
            return_stats=return_stats,
            decay_stats=decay_stats,
            by_symbol=by_symbol,
        )

    def _compute_signal_stats(self) -> SignalStats:
        """计算信号统计"""
        if not self.signals:
            return SignalStats()

        buy_count = sum(1 for s in self.signals if s.signal_type == SignalType.BUY)
        sell_count = sum(1 for s in self.signals if s.signal_type == SignalType.SELL)
        hold_count = sum(1 for s in self.signals if s.signal_type == SignalType.HOLD)

        symbols = set(s.symbol for s in self.signals)
        avg_conf = sum(s.confidence for s in self.signals) / len(self.signals)

        # 计算日均信号数
        if len(self.signals) >= 2:
            timestamps = sorted(s.timestamp for s in self.signals)
            days = (timestamps[-1] - timestamps[0]).days or 1
            signals_per_day = len(self.signals) / days
        else:
            signals_per_day = float(len(self.signals))

        return SignalStats(
            total_signals=len(self.signals),
            buy_signals=buy_count,
            sell_signals=sell_count,
            hold_signals=hold_count,
            unique_symbols=len(symbols),
            avg_confidence=avg_conf,
            signals_per_day=signals_per_day,
        )

    def _compute_return_stats(self, holding_period: int = 1) -> tuple[ReturnStats, dict[str, ReturnStats]]:
        """计算收益统计

        Args:
            holding_period: 持有期（天数）

        Returns:
            (总体收益统计, 各标的收益统计)
        """
        returns = self._calculate_signal_returns(holding_period)

        if not returns:
            return ReturnStats(), {}

        # 总体统计
        all_returns = np.array(list(returns.values()))
        overall = self._compute_return_stats_from_array(all_returns)

        # 按标的统计
        by_symbol: dict[str, ReturnStats] = {}
        symbol_returns: dict[str, list[float]] = {}

        for (symbol, _), ret in returns.items():
            if symbol not in symbol_returns:
                symbol_returns[symbol] = []
            symbol_returns[symbol].append(ret)

        for symbol, rets in symbol_returns.items():
            by_symbol[symbol] = self._compute_return_stats_from_array(np.array(rets))

        return overall, by_symbol

    def _compute_return_stats_from_array(self, returns: NDArray[np.float64]) -> ReturnStats:
        """从收益数组计算统计"""
        if len(returns) == 0:
            return ReturnStats()

        wins = returns[returns > 0]
        losses = returns[returns < 0]

        win_rate = len(wins) / len(returns) if len(returns) > 0 else 0
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0

        # 盈亏比 = 总盈利 / 总亏损
        total_win = float(np.sum(wins)) if len(wins) > 0 else 0
        total_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 0
        profit_factor = total_win / total_loss if total_loss > 0 else float("inf") if total_win > 0 else 0

        return ReturnStats(
            avg_return=float(np.mean(returns)),
            median_return=float(np.median(returns)),
            std_return=float(np.std(returns, ddof=1)) if len(returns) > 1 else 0,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            best_return=float(np.max(returns)),
            worst_return=float(np.min(returns)),
            total_pnl=float(np.sum(returns)),
        )

    def _calculate_signal_returns(self, holding_period: int) -> dict[tuple[str, datetime], float]:
        """计算信号收益

        Args:
            holding_period: 持有期（天数）

        Returns:
            {(symbol, timestamp): return}
        """
        returns: dict[tuple[str, datetime], float] = {}

        for signal in self.signals:
            if signal.signal_type == SignalType.HOLD:
                continue

            symbol = signal.symbol
            if symbol not in self.price_data:
                continue

            prices = self.price_data[symbol]
            entry_time = signal.timestamp

            # 查找入场价格
            entry_price = prices.get(entry_time)
            if entry_price is None:
                continue

            # 查找出场价格（持有期后）
            exit_time = entry_time + timedelta(days=holding_period)
            exit_price = self._find_nearest_price(prices, exit_time)
            if exit_price is None:
                continue

            # 计算收益
            if signal.signal_type == SignalType.BUY:
                ret = (exit_price - entry_price) / entry_price
            else:  # SELL
                ret = (entry_price - exit_price) / entry_price

            returns[(symbol, entry_time)] = ret

        return returns

    def _find_nearest_price(
        self,
        prices: dict[datetime, float],
        target_time: datetime,
        max_days: int = 5,
    ) -> float | None:
        """查找最近的价格

        Args:
            prices: 价格字典
            target_time: 目标时间
            max_days: 最大搜索天数

        Returns:
            最近的价格，或 None
        """
        # 精确匹配
        if target_time in prices:
            return prices[target_time]

        # 向后搜索
        for days in range(1, max_days + 1):
            check_time = target_time + timedelta(days=days)
            if check_time in prices:
                return prices[check_time]

        return None

    def _compute_decay_stats(self) -> DecayStats:
        """计算信号衰减统计"""
        avg_returns = []
        win_rates = []

        for period in self.holding_periods:
            returns = self._calculate_signal_returns(period)
            if returns:
                ret_array = np.array(list(returns.values()))
                avg_returns.append(float(np.mean(ret_array)))
                win_rates.append(len(ret_array[ret_array > 0]) / len(ret_array))
            else:
                avg_returns.append(0.0)
                win_rates.append(0.0)

        # 找最优持有期（最高平均收益）
        optimal_idx = int(np.argmax(avg_returns)) if avg_returns else 0
        optimal_period = self.holding_periods[optimal_idx] if self.holding_periods else 1

        return DecayStats(
            holding_periods=self.holding_periods,
            avg_returns=avg_returns,
            win_rates=win_rates,
            optimal_period=optimal_period,
        )


def evaluate_signals(
    signals: list[Signal],
    price_data: dict[str, dict[datetime, float]],
    holding_periods: list[int] | None = None,
) -> SignalEvaluationReport:
    """便捷函数：评估信号质量

    Args:
        signals: 信号列表
        price_data: 价格数据
        holding_periods: 持有期列表

    Returns:
        评估报告
    """
    evaluator = SignalEvaluator(signals, price_data, holding_periods)
    return evaluator.evaluate()
