"""
多策略组合运行器

支持同时运行多个策略：
- 资金分配与隔离
- 独立的风控规则
- 聚合报告

设计原则：
- 每个策略独立运行
- 可配置资金分配比例
- 支持策略级止损
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from aipha.domain.models import Bar
from aipha.execution.gateway import AccountSnapshot

from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode
from aipha.strategy.base import Strategy


logger = logging.getLogger(__name__)


@dataclass
class StrategyAllocation:
    """策略资金分配"""

    strategy: Strategy
    weight: float  # 资金权重 (0-1)
    max_drawdown: float = 0.2  # 最大回撤限制
    enabled: bool = True  # 是否启用

    def __post_init__(self):
        if not 0 < self.weight <= 1:
            raise ValueError(f"Weight must be in (0, 1], got {self.weight}")
        if not 0 < self.max_drawdown <= 1:
            raise ValueError(
                f"max_drawdown must be in (0, 1], got {self.max_drawdown}"
            )


@dataclass
class StrategyStatus:
    """策略运行状态"""

    name: str
    allocated_capital: Decimal
    current_value: Decimal
    pnl: Decimal
    pnl_pct: float
    max_drawdown: float
    current_drawdown: float
    is_active: bool
    bars_processed: int
    orders_submitted: int


@dataclass
class PortfolioConfig:
    """组合运行器配置"""

    # 总资金
    total_capital: Decimal = Decimal("1000000")

    # 交易规则
    lot_size: int = 100
    t_plus_n: int = 1

    # 费用 — §13.1: 默认值由 __post_init__ 从 MarketRulesCNEquity 派生
    commission_rate: Decimal = Decimal("-1")  # sentinel
    commission_min: Decimal = Decimal("-1")
    stamp_tax_rate: Decimal = Decimal("-1")
    transfer_fee_rate: Decimal = Decimal("-1")

    # 滑点 — §13.1: 默认由 __post_init__ 从 MarketRulesCNEquity 派生
    slippage_bps: float = -1.0

    # 风控
    portfolio_max_drawdown: float = 0.25  # 组合最大回撤
    stop_on_strategy_breach: bool = True  # 策略触发止损时是否停止

    def __post_init__(self) -> None:
        """§13.1: 从 MarketRulesCNEquity 动态派生费率默认值."""
        if self.commission_rate == Decimal("-1"):
            from aipha.market.rules_cn_equity import MarketRulesCNEquity
            rules = MarketRulesCNEquity()
            object.__setattr__(self, "commission_rate", rules.commission_rate)
            object.__setattr__(self, "commission_min", rules.commission_min)
            object.__setattr__(self, "stamp_tax_rate", rules.stamp_tax_rate)
            object.__setattr__(self, "transfer_fee_rate", rules.transfer_fee_rate)
            object.__setattr__(self, "slippage_bps", rules.slippage_bps_default)


class MultiStrategyRunner:
    """多策略组合运行器

    管理多个策略的同时运行：
    - 按权重分配资金
    - 独立跟踪每个策略表现
    - 提供聚合视图

    使用方式：
        runner = MultiStrategyRunner(config)
        runner.add_strategy(momentum_strategy, weight=0.5)
        runner.add_strategy(mean_revert_strategy, weight=0.3)
        runner.add_strategy(arbitrage_strategy, weight=0.2)
        runner.run_replay(bars_by_date)

    与 PaperEngine 集成（从 YAML 配置）：
        runner = MultiStrategyRunner.create_from_yaml(config_path)
        runner.start()
    """

    def __init__(self, config: PortfolioConfig):
        """初始化组合运行器

        Args:
            config: 组合配置
        """
        self.config = config
        self._allocations: list[StrategyAllocation] = []
        self._runners: dict[str, LiveRunner] = {}
        self._peak_values: dict[str, Decimal] = {}
        self._is_running: bool = False

    @classmethod
    def create_from_yaml(cls, config_path: str | Path) -> "MultiStrategyRunner":
        """从 YAML 配置创建多策略运行器

        YAML 格式示例::

            multi_strategy:
              total_capital: 1000000
              lot_size: 10
              commission_rate: 0.0000854   # §13.1 CB权威值 万0.854
              slippage_bps: 5.0
              strategies:
                - name: small_size
                  weight: 0.5
                  max_drawdown: 0.15
                  params:
                    top_k: 15
                    rebalance_days: 5
                - name: new_bond
                  weight: 0.5
                  max_drawdown: 0.15
                  params:
                    top_k: 10
                    rebalance_days: 5

        Args:
            config_path: YAML 配置路径

        Returns:
            已加载策略的 MultiStrategyRunner 实例
        """
        import yaml
        from aipha.strategy.registry import StrategyRegistry

        config_path = Path(config_path)
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        ms_data = data.get("multi_strategy", data)

        config = PortfolioConfig(
            total_capital=Decimal(str(ms_data.get("total_capital", 1_000_000))),
            lot_size=ms_data.get("lot_size", 10),
            t_plus_n=ms_data.get("t_plus_n", 0),
            # §13.1: 未指定时 sentinel=-1 → __post_init__ 从 MarketRules 派生
            commission_rate=Decimal(str(ms_data["commission_rate"])) if "commission_rate" in ms_data else Decimal("-1"),
            commission_min=Decimal(str(ms_data["commission_min"])) if "commission_min" in ms_data else Decimal("-1"),
            stamp_tax_rate=Decimal(str(ms_data["stamp_tax_rate"])) if "stamp_tax_rate" in ms_data else Decimal("-1"),
            transfer_fee_rate=Decimal(str(ms_data["transfer_fee_rate"])) if "transfer_fee_rate" in ms_data else Decimal("-1"),
            slippage_bps=ms_data.get("slippage_bps", -1.0),
        )

        runner = cls(config)
        registry = StrategyRegistry()

        for strat_cfg in ms_data.get("strategies", []):
            name = strat_cfg["name"]
            weight = strat_cfg.get("weight", 0.5)
            max_dd = strat_cfg.get("max_drawdown", 0.2)
            params = strat_cfg.get("params", {})

            strategy_cls = registry.get(name)
            if strategy_cls is None:
                raise ValueError(
                    f"策略 '{name}' 未注册。可用: {registry.list_strategies()}"
                )

            strategy = strategy_cls(**params) if params else strategy_cls()
            runner.add_strategy(strategy, weight=weight, max_drawdown=max_dd)
            logger.info(f"加载策略: {name} (weight={weight:.1%}, params={params})")

        return runner

    def add_strategy(
        self,
        strategy: Strategy,
        weight: float,
        max_drawdown: float = 0.2,
    ) -> "MultiStrategyRunner":
        """添加策略

        Args:
            strategy: 策略实例
            weight: 资金权重
            max_drawdown: 最大回撤限制

        Returns:
            self (支持链式调用)
        """
        allocation = StrategyAllocation(
            strategy=strategy,
            weight=weight,
            max_drawdown=max_drawdown,
        )
        self._allocations.append(allocation)
        logger.info(
            f"Added strategy: {strategy.name} "
            f"(weight={weight:.1%}, max_dd={max_drawdown:.1%})"
        )
        return self

    def _validate_allocations(self) -> None:
        """验证资金分配"""
        if not self._allocations:
            raise ValueError("No strategies added")

        total_weight = sum(a.weight for a in self._allocations)
        if abs(total_weight - 1.0) > 0.001:
            logger.warning(
                f"Total weight {total_weight:.2%} != 100%, "
                "will normalize automatically"
            )

    def _create_runners(self) -> None:
        """创建各策略的运行器"""
        for alloc in self._allocations:
            allocated = self.config.total_capital * Decimal(str(alloc.weight))

            runner_config = RunnerConfig(
                mode=RunnerMode.PAPER,
                initial_capital=allocated,
                lot_size=self.config.lot_size,
                t_plus_n=self.config.t_plus_n,
                commission_rate=self.config.commission_rate,
                commission_min=self.config.commission_min,
                stamp_tax_rate=self.config.stamp_tax_rate,
                transfer_fee_rate=self.config.transfer_fee_rate,
                slippage_bps=self.config.slippage_bps,
            )

            runner = LiveRunner.from_paper_mode(runner_config, alloc.strategy)

            self._runners[alloc.strategy.name] = runner
            self._peak_values[alloc.strategy.name] = allocated

            logger.info(
                f"Created runner for {alloc.strategy.name}: "
                f"capital={allocated:,.0f}"
            )

    # ==========================================================================
    # 运行控制
    # ==========================================================================

    def start(self) -> None:
        """启动所有策略"""
        self._validate_allocations()
        self._create_runners()

        for name, runner in self._runners.items():
            runner.start()
            logger.info(f"Started strategy: {name}")

        self._is_running = True

    def stop(self) -> None:
        """停止所有策略"""
        for name, runner in self._runners.items():
            runner.stop()
            logger.info(f"Stopped strategy: {name}")

        self._is_running = False

    def on_bar(self, bar: Bar) -> None:
        """处理新 Bar（单标的）

        Args:
            bar: K线数据
        """
        if not self._is_running:
            return

        for alloc in self._allocations:
            if not alloc.enabled:
                continue

            name = alloc.strategy.name
            runner = self._runners[name]

            # 检查是否触发止损
            status = self._get_strategy_status(alloc, runner)
            if status.current_drawdown >= alloc.max_drawdown:
                logger.warning(
                    f"Strategy {name} breached max drawdown "
                    f"({status.current_drawdown:.1%} >= {alloc.max_drawdown:.1%})"
                )
                if self.config.stop_on_strategy_breach:
                    alloc.enabled = False
                    runner.stop()
                    continue

            # 正常处理
            runner.on_bar(bar)

            # 更新峰值
            snapshot = runner.get_account_snapshot()
            current = snapshot.total_value
            if current > self._peak_values[name]:
                self._peak_values[name] = current

    def on_bars(self, bars: list[Bar]) -> None:
        """处理一批 Bar（多标的同批次）

        与 PaperEngine 集成的推荐入口：provider 可能一次推送多个标的的 bar。

        Args:
            bars: 同一时刻的多个标的 Bar 列表
        """
        for bar in bars:
            self.on_bar(bar)

    def run_replay_sync(
        self,
        bars_by_date: dict[date, list[Bar]],
    ) -> dict[str, AccountSnapshot]:
        """同步运行历史数据回放

        Args:
            bars_by_date: 按日期组织的 Bar 数据

        Returns:
            各策略最终账户快照
        """
        self.start()

        try:
            sorted_dates = sorted(bars_by_date.keys())

            for trade_date in sorted_dates:
                bars = bars_by_date[trade_date]
                for bar in bars:
                    self.on_bar(bar)

            return {
                name: runner.get_account_snapshot()
                for name, runner in self._runners.items()
            }

        finally:
            self.stop()

    # ==========================================================================
    # 状态查询
    # ==========================================================================

    def _get_strategy_status(
        self,
        alloc: StrategyAllocation,
        runner: LiveRunner,
    ) -> StrategyStatus:
        """获取单个策略状态"""
        name = alloc.strategy.name
        snapshot = runner.get_account_snapshot()
        state = runner.state

        allocated = self.config.total_capital * Decimal(str(alloc.weight))
        current = snapshot.total_value
        pnl = current - allocated
        pnl_pct = float(pnl / allocated) if allocated > 0 else 0.0

        peak = self._peak_values.get(name, allocated)
        drawdown = float((peak - current) / peak) if peak > 0 else 0.0

        return StrategyStatus(
            name=name,
            allocated_capital=allocated,
            current_value=current,
            pnl=pnl,
            pnl_pct=pnl_pct,
            max_drawdown=alloc.max_drawdown,
            current_drawdown=drawdown,
            is_active=alloc.enabled and runner.is_running,
            bars_processed=state.total_bars_processed,
            orders_submitted=state.total_orders_submitted,
        )

    def get_portfolio_status(self) -> dict[str, Any]:
        """获取组合状态

        Returns:
            组合状态字典
        """
        statuses = []
        total_value = Decimal("0")
        total_pnl = Decimal("0")

        for alloc in self._allocations:
            runner = self._runners.get(alloc.strategy.name)
            if runner is None:
                continue

            status = self._get_strategy_status(alloc, runner)
            statuses.append({
                "name": status.name,
                "allocated": float(status.allocated_capital),
                "current": float(status.current_value),
                "pnl": float(status.pnl),
                "pnl_pct": status.pnl_pct,
                "drawdown": status.current_drawdown,
                "is_active": status.is_active,
                "bars": status.bars_processed,
                "orders": status.orders_submitted,
            })

            total_value += status.current_value
            total_pnl += status.pnl

        portfolio_pnl_pct = (
            float(total_pnl / self.config.total_capital)
            if self.config.total_capital > 0
            else 0.0
        )

        return {
            "is_running": self._is_running,
            "total_capital": float(self.config.total_capital),
            "total_value": float(total_value),
            "total_pnl": float(total_pnl),
            "total_pnl_pct": portfolio_pnl_pct,
            "strategies": statuses,
        }

    def get_strategy_snapshots(self) -> dict[str, AccountSnapshot]:
        """获取各策略账户快照

        Returns:
            {策略名: 账户快照}
        """
        return {
            name: runner.get_account_snapshot()
            for name, runner in self._runners.items()
        }
