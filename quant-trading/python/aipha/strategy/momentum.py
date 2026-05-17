"""
动量策略实现（演示策略 / Demo Only）

.. warning::
    本策略仅用于验证 A 股回测框架功能，尚未经过实盘数据回测验证，
    不建议直接用于实盘交易。生产环境请参考 CB 策略系列或自行
    添加风控/成本/滑点等约束后进行充分的样本外验证。

策略逻辑：
- 基于过去 N 日收益率排序，买入收益率最高的 K 只股票
- 每 M 日调仓一次
- 等权分配资金
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


@StrategyRegistry().register("demo_momentum")
@dataclass
class MomentumStrategy(Strategy):
    """简单动量策略

    Attributes:
        name: 策略名称
        lookback_days: 回望期（计算收益率的天数）
        top_k: 选股数量
        rebalance_days: 调仓周期（天）
        target_symbols: 股票池
    """

    name: str = "Momentum"
    lookback_days: int = 20
    top_k: int = 5
    rebalance_days: int = 5
    target_symbols: list[str] = field(default_factory=list)

    # 内部状态
    _price_history: dict[str, list[tuple[date, float]]] = field(
        default_factory=dict, repr=False
    )
    _last_rebalance: Optional[date] = field(default=None, repr=False)
    _bar_count: int = field(default=0, repr=False)

    @classmethod
    def param_space(cls) -> ParamSpace:
        """动量策略参数空间"""
        return ParamSpace(
            strategy_name="momentum",
            specs=[
                ParamSpec(
                    name="lookback_days", param_type=ParamType.INT,
                    default=20, low=5, high=60, step=5,
                    description="回望期天数", is_critical=True,
                ),
                ParamSpec(
                    name="top_k", param_type=ParamType.INT,
                    default=5, low=5, high=30, step=5,
                    description="持仓数量", is_critical=True,
                ),
                ParamSpec(
                    name="rebalance_days", param_type=ParamType.INT,
                    default=5, low=5, high=30, step=5,
                    description="调仓周期天数", is_critical=False,
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
        current_date = context.current_time.date()

        # 更新价格历史
        for symbol, bar in bars.items():
            if symbol not in self.target_symbols:
                continue
            if symbol not in self._price_history:
                self._price_history[symbol] = []
            self._price_history[symbol].append((current_date, bar.close))
            # 只保留必要的历史
            if len(self._price_history[symbol]) > self.lookback_days + 10:
                self._price_history[symbol] = self._price_history[symbol][-self.lookback_days - 5 :]

        # 检查是否需要调仓
        if not self._should_rebalance(current_date):
            return []

        self._last_rebalance = current_date

        # 计算动量得分
        momentum_scores = self._calculate_momentum_scores()
        if not momentum_scores:
            return []

        # 选择 top K
        sorted_symbols = sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)
        target_holdings = [s for s, _ in sorted_symbols[: self.top_k]]

        # 生成订单意图
        return self._generate_rebalance_intents(target_holdings, bars, context)

    def _should_rebalance(self, current_date: date) -> bool:
        """判断是否需要调仓"""
        # 第一次调仓需要足够的历史数据
        if self._bar_count < self.lookback_days:
            return False

        # 第一次调仓
        if self._last_rebalance is None:
            return True

        # 检查是否到达调仓周期
        days_since_last = (current_date - self._last_rebalance).days
        return days_since_last >= self.rebalance_days

    def _calculate_momentum_scores(self) -> dict[str, float]:
        """计算动量得分（过去 N 日收益率）"""
        scores = {}

        for symbol in self.target_symbols:
            history = self._price_history.get(symbol, [])
            if len(history) < self.lookback_days:
                continue

            # 获取 lookback_days 前的价格和当前价格
            recent_prices = history[-self.lookback_days :]
            if len(recent_prices) < 2:
                continue

            old_price = recent_prices[0][1]
            new_price = recent_prices[-1][1]

            if old_price > 0:
                momentum = (new_price - old_price) / old_price
                scores[symbol] = momentum

        return scores

    def _generate_rebalance_intents(
        self,
        target_holdings: list[str],
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """生成调仓订单意图"""
        intents = []
        current_positions = context.positions

        # 1. 卖出不在目标持仓中的股票
        for symbol, qty in current_positions.items():
            if qty > 0 and symbol not in target_holdings:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                    )
                )

        # 2. 计算目标持仓金额
        # 估算卖出后的现金
        sell_proceeds = sum(
            bars[symbol].close * current_positions[symbol]
            for symbol in current_positions
            if symbol not in target_holdings and symbol in bars
        )
        available_cash = context.cash + sell_proceeds * 0.99  # 预留费用

        # 3. 计算每只股票的目标金额
        num_targets = len(target_holdings)
        if num_targets == 0:
            return intents

        per_stock = available_cash * 0.95 / num_targets  # 预留一些现金

        # 4. 生成买入订单
        for symbol in target_holdings:
            if symbol not in bars:
                continue

            current_qty = current_positions.get(symbol, 0)
            target_qty = int(per_stock / bars[symbol].close / 100) * 100

            if target_qty > current_qty:
                # 需要买入
                buy_qty = target_qty - current_qty
                if buy_qty >= 100:
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.BUY,
                            qty=buy_qty,
                            intent_type=IntentType.ORDER,
                        )
                    )
            elif target_qty < current_qty:
                # 需要卖出（减仓）
                sell_qty = current_qty - target_qty
                if sell_qty > 0:
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.SELL,
                            qty=sell_qty,
                            intent_type=IntentType.ORDER,
                        )
                    )

        return intents

    def on_start(self) -> None:
        """策略启动时重置状态"""
        self._price_history = {}
        self._last_rebalance = None
        self._bar_count = 0

    def on_end(self) -> None:
        """策略结束"""
        pass
