"""
均值回归策略实现（演示策略 / Demo Only）

.. warning::
    本策略仅用于验证 A 股回测框架功能，尚未经过实盘数据回测验证，
    不建议直接用于实盘交易。生产环境请参考 CB 策略系列或自行
    添加风控/成本/滑点等约束后进行充分的样本外验证。

策略逻辑：
- 计算股票过去 N 日的均价和标准差
- 当价格低于均价 M 个标准差时买入（超跌反弹）
- 当价格高于均价 M 个标准差时卖出（超涨回调）
- 或者价格回归均值时平仓

适用场景：震荡市场、区间波动明显的标的
"""

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from aipha.domain.models import Bar, Side
from aipha.strategy.base import (
    IntentType,
    OrderIntent,
    Strategy,
    StrategyContext,
)
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
from aipha.strategy.registry import StrategyRegistry


@StrategyRegistry().register("demo_mean_reversion")
@dataclass
class MeanReversionStrategy(Strategy):
    """均值回归策略

    Attributes:
        name: 策略名称
        lookback_days: 均值计算回望期
        entry_std: 入场阈值（几倍标准差）
        exit_std: 出场阈值（几倍标准差，回归到均值附近）
        max_positions: 最大持仓数量
        position_pct: 单票仓位百分比
        target_symbols: 股票池
    """

    name: str = "MeanReversion"
    lookback_days: int = 20
    entry_std: float = 2.0  # 价格低于均值 2 个标准差时买入
    exit_std: float = 0.5   # 价格回归到均值 0.5 个标准差内时卖出
    max_positions: int = 10
    position_pct: float = 0.10  # 单票仓位 10%
    target_symbols: list[str] = field(default_factory=list)

    # 内部状态
    _price_history: dict[str, list[tuple[date, float]]] = field(
        default_factory=dict, repr=False
    )
    _entry_prices: dict[str, float] = field(
        default_factory=dict, repr=False
    )  # 记录入场价格
    _bar_count: int = field(default=0, repr=False)

    @classmethod
    def param_space(cls) -> ParamSpace:
        """均值回归策略参数空间"""
        return ParamSpace(
            strategy_name="mean_reversion",
            specs=[
                ParamSpec(
                    name="lookback_days", param_type=ParamType.INT,
                    default=20, low=5, high=60, step=5,
                    description="均值计算回望期", is_critical=True,
                ),
                ParamSpec(
                    name="entry_std", param_type=ParamType.FLOAT,
                    default=2.0, low=1.0, high=3.5, step=0.5,
                    description="入场阈值（标准差倍数）", is_critical=True,
                ),
                ParamSpec(
                    name="exit_std", param_type=ParamType.FLOAT,
                    default=0.5, low=0.1, high=1.5, step=0.2,
                    description="出场阈值（标准差倍数）", is_critical=True,
                ),
                ParamSpec(
                    name="max_positions", param_type=ParamType.INT,
                    default=10, low=5, high=30, step=5,
                    description="最大持仓数量",
                ),
                ParamSpec(
                    name="position_pct", param_type=ParamType.FLOAT,
                    default=0.10, low=0.02, high=0.25, step=0.02,
                    description="单票仓位百分比",
                ),
            ],
        )

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """处理新 bar 数据"""
        self._bar_count += 1

        # 更新价格历史
        for symbol, bar in bars.items():
            if symbol not in self.target_symbols:
                continue
            if symbol not in self._price_history:
                self._price_history[symbol] = []
            self._price_history[symbol].append(
                (context.current_time.date(), bar.close)
            )
            # 只保留必要的历史
            max_len = self.lookback_days + 10
            if len(self._price_history[symbol]) > max_len:
                self._price_history[symbol] = self._price_history[symbol][-max_len:]

        # 等待足够的历史数据
        if self._bar_count < self.lookback_days:
            return []

        # 计算信号并生成订单
        return self._generate_signals(bars, context)

    def _generate_signals(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """生成交易信号"""
        intents = []
        current_positions = context.positions
        current_position_count = sum(1 for qty in current_positions.values() if qty > 0)

        for symbol in self.target_symbols:
            if symbol not in bars:
                continue

            bar = bars[symbol]
            stats = self._calculate_statistics(symbol)
            if stats is None:
                continue

            mean_price, std_price = stats
            current_price = bar.close
            current_qty = current_positions.get(symbol, 0)

            # 计算 z-score
            if std_price > 0:
                z_score = (current_price - mean_price) / std_price
            else:
                continue

            # 持有仓位时的出场逻辑
            if current_qty > 0:
                # 价格回归均值附近时卖出
                if abs(z_score) < self.exit_std:
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.SELL,
                            qty=current_qty,
                            intent_type=IntentType.ORDER,
                        )
                    )
                    self._entry_prices.pop(symbol, None)

            # 没有仓位时的入场逻辑
            elif current_qty == 0:
                # 价格低于均值 N 个标准差时买入
                if z_score < -self.entry_std:
                    if current_position_count < self.max_positions:
                        buy_amount = context.nav * self.position_pct
                        buy_qty = int(buy_amount / current_price / 100) * 100
                        if buy_qty >= 100:
                            intents.append(
                                OrderIntent(
                                    symbol=symbol,
                                    side=Side.BUY,
                                    qty=buy_qty,
                                    intent_type=IntentType.ORDER,
                                )
                            )
                            self._entry_prices[symbol] = current_price
                            current_position_count += 1

        return intents

    def _calculate_statistics(self, symbol: str) -> Optional[tuple[float, float]]:
        """计算均值和标准差

        Returns:
            (mean, std) 或 None（数据不足）
        """
        history = self._price_history.get(symbol, [])
        if len(history) < self.lookback_days:
            return None

        prices = [p for _, p in history[-self.lookback_days:]]
        if not prices:
            return None

        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_price = math.sqrt(variance)

        return mean_price, std_price

    def on_start(self) -> None:
        """策略启动时重置状态"""
        self._price_history = {}
        self._entry_prices = {}
        self._bar_count = 0

    def on_end(self) -> None:
        """策略结束"""
        pass
