"""
双均线突破策略实现（演示策略 / Demo Only）

.. warning::
    本策略仅用于验证 A 股回测框架功能，尚未经过实盘数据回测验证，
    不建议直接用于实盘交易。生产环境请参考 CB 策略系列或自行
    添加风控/成本/滑点等约束后进行充分的样本外验证。

策略逻辑：
- 计算短期均线和长期均线
- 短期均线上穿长期均线时买入（金叉）
- 短期均线下穿长期均线时卖出（死叉）

适用场景：趋势行情、趋势明确的标的
"""

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


@StrategyRegistry().register("demo_dual_ma")
@dataclass
class DualMAStrategy(Strategy):
    """双均线突破策略

    Attributes:
        name: 策略名称
        short_window: 短期均线窗口
        long_window: 长期均线窗口
        position_pct: 单票仓位百分比
        max_positions: 最大持仓数量
        target_symbols: 股票池
    """

    name: str = "DualMA"
    short_window: int = 5      # 5日均线
    long_window: int = 20      # 20日均线
    position_pct: float = 0.10  # 单票仓位 10%
    max_positions: int = 10
    target_symbols: list[str] = field(default_factory=list)

    # 内部状态
    _price_history: dict[str, list[tuple[date, float]]] = field(
        default_factory=dict, repr=False
    )
    _prev_ma_state: dict[str, str] = field(
        default_factory=dict, repr=False
    )  # "above" / "below" / None
    _bar_count: int = field(default=0, repr=False)

    @classmethod
    def param_space(cls) -> ParamSpace:
        """双均线突破策略参数空间"""
        return ParamSpace(
            strategy_name="dual_ma",
            specs=[
                ParamSpec(
                    name="short_window", param_type=ParamType.INT,
                    default=5, low=2, high=20, step=1,
                    description="短期均线窗口", is_critical=True,
                ),
                ParamSpec(
                    name="long_window", param_type=ParamType.INT,
                    default=20, low=10, high=120, step=5,
                    description="长期均线窗口", is_critical=True,
                ),
                ParamSpec(
                    name="position_pct", param_type=ParamType.FLOAT,
                    default=0.10, low=0.02, high=0.25, step=0.02,
                    description="单票仓位百分比",
                ),
                ParamSpec(
                    name="max_positions", param_type=ParamType.INT,
                    default=10, low=5, high=30, step=5,
                    description="最大持仓数量",
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
            max_len = self.long_window + 10
            if len(self._price_history[symbol]) > max_len:
                self._price_history[symbol] = self._price_history[symbol][-max_len:]

        # 等待足够的历史数据
        if self._bar_count < self.long_window:
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
            ma_short = self._calculate_ma(symbol, self.short_window)
            ma_long = self._calculate_ma(symbol, self.long_window)

            if ma_short is None or ma_long is None:
                continue

            current_qty = current_positions.get(symbol, 0)
            current_price = bar.close

            # 判断当前均线状态
            if ma_short > ma_long:
                current_state = "above"
            else:
                current_state = "below"

            prev_state = self._prev_ma_state.get(symbol)
            self._prev_ma_state[symbol] = current_state

            # 金叉：短均线从下穿上
            if prev_state == "below" and current_state == "above":
                if current_qty == 0 and current_position_count < self.max_positions:
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
                        current_position_count += 1

            # 死叉：短均线从上穿下
            elif prev_state == "above" and current_state == "below":
                if current_qty > 0:
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.SELL,
                            qty=current_qty,
                            intent_type=IntentType.ORDER,
                        )
                    )

        return intents

    def _calculate_ma(self, symbol: str, window: int) -> Optional[float]:
        """计算移动平均

        Args:
            symbol: 标的代码
            window: 窗口大小

        Returns:
            移动平均值，数据不足返回 None
        """
        history = self._price_history.get(symbol, [])
        if len(history) < window:
            return None

        prices = [p for _, p in history[-window:]]
        return sum(prices) / len(prices)

    def on_start(self) -> None:
        """策略启动时重置状态"""
        self._price_history = {}
        self._prev_ma_state = {}
        self._bar_count = 0

    def on_end(self) -> None:
        """策略结束"""
        pass
