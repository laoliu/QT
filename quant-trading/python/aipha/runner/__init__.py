"""策略运行器 — 统一调度策略执行、数据分发、信号收集

核心流程:
  on_bar → universe_filter → strategy.on_bar → risk_check → order_validate → collect
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aipha.strategy.base import Strategy, StrategyContext, OrderIntent
from aipha.universe.exclusions import UniverseExclusions
from aipha.market.halt_checker import HaltChecker, OrderValidator
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    initial_cash: float = 1_000_000.0
    enable_risk_check: bool = True
    enable_order_validate: bool = True
    max_strategies: int = 50
    log_signals: bool = True


@dataclass
class SignalRecord:
    datetime: datetime
    strategy_name: str
    intents: list[OrderIntent]
    metadata: dict[str, Any] = field(default_factory=dict)


class StrategyRunner:
    """多策略并行运行器。

    用法:
        runner = StrategyRunner(config)
        runner.add_strategy(my_strategy)
        for bars in data_stream:
            signals = runner.on_bar(bars, current_time)
    """

    def __init__(self, config: RunnerConfig | None = None) -> None:
        self.config = config or RunnerConfig()
        self._strategies: list[Strategy] = []
        self._halt_checker = HaltChecker()
        self._order_validator = OrderValidator()
        self._signal_log: list[SignalRecord] = []
        self._positions: dict[str, int] = {}
        self._cash: float = self.config.initial_cash
        self._nav: float = self.config.initial_cash

    def add_strategy(self, strategy: Strategy) -> None:
        if len(self._strategies) >= self.config.max_strategies:
            raise ValueError(f"已达策略上限 {self.config.max_strategies}")
        self._strategies.append(strategy)
        strategy.on_start()
        logger.info("注册策略: %s", strategy.name)

    def remove_strategy(self, name: str) -> bool:
        for i, s in enumerate(self._strategies):
            if s.name == name:
                self._strategies.pop(i)
                return True
        return False

    def set_positions(self, positions: dict[str, int]) -> None:
        self._positions = dict(positions)

    def set_cash(self, cash: float) -> None:
        self._cash = cash

    def update_nav(self, bars: dict[str, Bar]) -> None:
        """根据当前持仓和行情更新 NAV。"""
        market_value = sum(
            self._positions.get(sym, 0) * bar.close
            for sym, bar in bars.items()
        )
        self._nav = self._cash + market_value

    def on_bar(
        self,
        bars: dict[str, Bar],
        current_time: datetime,
    ) -> list[OrderIntent]:
        """处理一根 bar，收集所有策略信号。"""
        self.update_nav(bars)

        # 过滤不可交易标的
        tradable_bars = self._halt_checker.filter_tradable(
            {sym: {"open": b.open, "high": b.high, "low": b.low,
                   "close": b.close, "volume": b.volume,
                   "pct_chg": getattr(b, "pct_chg", None)}
             for sym, b in bars.items()}
        )
        # 但策略仍接收全量 bars 用于指标计算

        ctx = StrategyContext(
            current_time=current_time,
            positions=dict(self._positions),
            cash=self._cash,
            nav=self._nav,
        )

        all_intents: list[OrderIntent] = []
        for strategy in self._strategies:
            try:
                intents = strategy.on_bar(bars, ctx)
            except Exception:
                logger.exception("策略 %s on_bar 异常", strategy.name)
                continue

            # 过滤不可交易标的
            intents = [i for i in intents if i.symbol in tradable_bars]

            # 委托校验
            if self.config.enable_order_validate:
                validated = []
                for intent in intents:
                    ok, reason = self._order_validator.validate(
                        symbol=intent.symbol,
                        side=intent.side.value if hasattr(intent.side, "value") else str(intent.side),
                        qty=intent.qty,
                        price=bars[intent.symbol].close if intent.symbol in bars else 0,
                        position=self._positions.get(intent.symbol, 0),
                    )
                    if ok:
                        validated.append(intent)
                    else:
                        logger.debug("委托被拒: %s %s — %s", strategy.name, intent.symbol, reason)
                intents = validated

            if intents and self.config.log_signals:
                self._signal_log.append(SignalRecord(
                    datetime=current_time,
                    strategy_name=strategy.name,
                    intents=intents,
                ))

            all_intents.extend(intents)

        return all_intents

    @property
    def signal_log(self) -> list[SignalRecord]:
        return self._signal_log

    @property
    def strategies(self) -> list[str]:
        return [s.name for s in self._strategies]
