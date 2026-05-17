"""
因子驱动策略

基于因子得分排序选股的策略框架。
"""

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional

from aipha.domain.models import Bar, Side
from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.factors.composite import (
    CompositeFactor,
    RankNormalizer,
)
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
from aipha.strategy.registry import StrategyRegistry


WeightMethod = Literal[
    "equal",        # 等权
    "factor_score", # 因子得分加权
    "min_variance", # 最小方差
    "risk_parity",  # 风险平价
    "max_sharpe",   # 最大夏普
]


@dataclass
class FactorStrategyConfig:
    """因子策略配置

    Attributes:
        top_k: 选择排名前 K 的股票
        top_pct: 选择排名前百分比（与 top_k 二选一）
        rebalance_days: 调仓周期（天数）
        weight_method: 权重计算方法 ("equal", "factor_score", "min_variance", "risk_parity", "max_sharpe")
        equal_weight: 是否等权重（弃用，使用 weight_method="equal" 替代）
        normalize_method: 因子标准化方法
        ascending: True 表示因子值越小越好
        lookback_days: 优化器回看天数（用于协方差估计）
    """

    top_k: Optional[int] = 10
    top_pct: Optional[float] = None
    rebalance_days: int = 20
    weight_method: WeightMethod = "equal"
    equal_weight: bool = True  # 向后兼容
    normalize_method: str = "rank"
    ascending: bool = False  # 默认因子值越大越好
    lookback_days: int = 60  # 优化器回看天数

    def __post_init__(self):
        if self.top_k is None and self.top_pct is None:
            raise ValueError("必须指定 top_k 或 top_pct")
        if self.top_k is not None and self.top_pct is not None:
            raise ValueError("top_k 和 top_pct 只能指定一个")
        # 向后兼容：equal_weight=True 等价于 weight_method="equal"
        if self.equal_weight and self.weight_method == "equal":
            pass  # 默认等权
        elif not self.equal_weight and self.weight_method == "equal":
            self.weight_method = "factor_score"


@StrategyRegistry().register("factor_based")
class FactorBasedStrategy(Strategy):
    """因子驱动策略

    基于因子得分排序选股，支持单因子和多因子组合。

    Example:
        # 单因子策略
        strategy = FactorBasedStrategy(
            factors=[PEFactor(invert=True)],
            config=FactorStrategyConfig(top_k=10),
            name="EP_STRATEGY",
        )

        # 多因子策略
        strategy = FactorBasedStrategy(
            factors=[PEFactor(invert=True), VolatilityFactor()],
            weights={"EP": 0.5, "LOW_VOL_20": 0.5},
            config=FactorStrategyConfig(top_k=20),
            name="VALUE_LOWVOL",
        )
    """

    def __init__(
        self,
        factors: list[Factor],
        config: Optional[FactorStrategyConfig] = None,
        weights: Optional[dict[str, float]] = None,
        name: str = "FACTOR_STRATEGY",
    ):
        self.factors = factors
        self.weights = weights
        self.config = config or FactorStrategyConfig()
        self._name = name
        self._last_rebalance_date: Optional[date] = None
        self._days_since_rebalance: int = 0
        self._current_targets: dict[str, float] = {}
        self._price_history: dict[str, list[float]] = {}  # 用于优化器的价格历史

    @classmethod
    def param_space(cls) -> ParamSpace:
        """因子驱动策略参数空间"""
        return ParamSpace(
            strategy_name="factor_based",
            specs=[
                ParamSpec(
                    name="top_k", param_type=ParamType.INT,
                    default=10, low=5, high=50, step=5,
                    description="选股数量", is_critical=True,
                ),
                ParamSpec(
                    name="rebalance_days", param_type=ParamType.INT,
                    default=20, low=5, high=60, step=5,
                    description="调仓周期（天）", is_critical=True,
                ),
                ParamSpec(
                    name="lookback_days", param_type=ParamType.INT,
                    default=60, low=20, high=120, step=10,
                    description="优化器回看天数",
                ),
                ParamSpec(
                    name="weight_method", param_type=ParamType.CHOICE,
                    default="equal",
                    choices=["equal", "factor_score", "min_variance",
                             "risk_parity", "max_sharpe"],
                    description="权重计算方法", is_critical=True,
                ),
            ],
        )

    @property
    def name(self) -> str:
        """策略名称。"""
        return self._name

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """处理 Bar 数据，生成订单意图

        Args:
            bars: 当前 Bar 数据
            context: 策略上下文

        Returns:
            订单意图列表
        """
        intents: list[OrderIntent] = []

        # 提取当前日期
        current_date = self._get_current_date(bars, context)
        if current_date is None:
            return intents

        # 检查是否需要调仓
        if not self._should_rebalance(current_date):
            return intents

        # 更新价格历史（用于优化器）
        self._update_price_history(bars)

        # 获取可交易股票池
        universe = list(bars.keys())
        if not universe:
            return intents

        # 构建增强的上下文（包含 bars 中的价格信息）
        enhanced_context = _EnhancedContext(context, bars)

        # 计算因子值
        factor_results = self._compute_factors(enhanced_context, universe, current_date)
        if not factor_results:
            return intents

        # 合成最终得分
        final_scores = self._combine_factors(factor_results)
        if not final_scores:
            return intents

        # 选股
        selected = self._select_stocks(final_scores)

        # 计算目标权重
        target_weights = self._calculate_weights(selected)

        # 生成订单意图
        intents = self._generate_intents_from_targets(
            bars, context, target_weights
        )

        # 更新状态
        self._last_rebalance_date = current_date
        self._days_since_rebalance = 0
        self._current_targets = target_weights

        return intents

    def _get_current_date(
        self, bars: dict[str, Bar], context: StrategyContext
    ) -> Optional[date]:
        """获取当前交易日期"""
        if context.current_time:
            return context.current_time.date()
        # 从 bars 中获取日期
        for bar in bars.values():
            if hasattr(bar, "trade_date"):
                return bar.trade_date
            if hasattr(bar, "end_ts"):
                return bar.end_ts.date()
            if hasattr(bar, "start_ts"):
                return bar.start_ts.date()
        return None

    def _should_rebalance(self, current_date: date) -> bool:
        """判断是否需要调仓"""
        if self._last_rebalance_date is None:
            return True

        self._days_since_rebalance += 1
        return self._days_since_rebalance >= self.config.rebalance_days

    def _update_price_history(self, bars: dict[str, Bar]) -> None:
        """更新价格历史（用于优化器的协方差估计）"""
        max_history = self.config.lookback_days + 10  # 保留足够的历史

        for symbol, bar in bars.items():
            if symbol not in self._price_history:
                self._price_history[symbol] = []
            self._price_history[symbol].append(bar.close)

            # 限制历史长度
            if len(self._price_history[symbol]) > max_history:
                self._price_history[symbol] = self._price_history[symbol][-max_history:]

    def _compute_factors(
        self,
        context: "_EnhancedContext",
        universe: list[str],
        current_date: date,
    ) -> dict[str, list[FactorResult]]:
        """计算所有因子"""
        results: dict[str, list[FactorResult]] = {}

        for factor in self.factors:
            factor_results = []

            for symbol in universe:
                # 构建因子输入数据
                factor_data = self._build_factor_data(context, symbol, current_date)
                if factor_data is None:
                    continue

                # 计算因子
                result = factor.compute(factor_data)
                factor_results.append(result)

            if factor_results:
                results[factor.name] = factor_results

        return results

    def _build_factor_data(
        self,
        context: "_EnhancedContext",
        symbol: str,
        current_date: date,
    ) -> Optional[FactorData]:
        """构建因子输入数据"""
        # 获取当前价格
        close = context.get_price(symbol)
        if close is None or close <= 0:
            return None

        # 获取历史价格（用于动量/波动率等因子）
        historical_close = context.get_historical_prices(symbol)

        # 获取财务数据
        fundamentals = context.get_fundamentals(symbol)

        return FactorData(
            symbol=symbol,
            trade_date=current_date,
            close=close,
            historical_close=historical_close,
            fundamentals=fundamentals,
        )

    def _combine_factors(
        self, factor_results: dict[str, list[FactorResult]]
    ) -> list[FactorResult]:
        """合成因子得分"""
        if len(factor_results) == 1:
            # 单因子：直接使用标准化后的结果
            factor_name = next(iter(factor_results))
            results = factor_results[factor_name]

            normalizer = RankNormalizer(ascending=self.config.ascending)
            return normalizer.normalize(results)

        # 多因子：使用复合因子
        weights = self.weights or {name: 1.0 for name in factor_results}

        composite = CompositeFactor(
            weights=weights,
            normalize_method=self.config.normalize_method,
            name="COMPOSITE",
        )

        return composite.combine(factor_results)

    def _select_stocks(
        self, scores: list[FactorResult]
    ) -> list[FactorResult]:
        """根据得分选股"""
        # 过滤无效得分
        valid_scores = [s for s in scores if not math.isnan(s.value)]

        if not valid_scores:
            return []

        # 检查是否有 rank 值
        has_rank = any(
            r.rank is not None and not math.isnan(r.rank) for r in valid_scores
        )

        if has_rank:
            # 有 rank 值：rank 越小表示排名越靠前（无论 ascending 设置如何）
            # RankNormalizer 已经根据 ascending 处理了排名方向
            sorted_scores = sorted(
                valid_scores,
                key=lambda r: r.rank if r.rank is not None else float("inf"),
            )
        else:
            # 没有 rank，使用原始 value
            # ascending=True: 值越小越好，升序排序
            # ascending=False: 值越大越好，降序排序
            sorted_scores = sorted(
                valid_scores,
                key=lambda r: r.value,
                reverse=not self.config.ascending,
            )

        # 选择前 K 或前 N%
        if self.config.top_k is not None:
            n_select = min(self.config.top_k, len(sorted_scores))
        else:
            n_select = max(1, int(len(sorted_scores) * (self.config.top_pct or 0.1)))

        return sorted_scores[:n_select]

    def _calculate_weights(
        self, selected: list[FactorResult]
    ) -> dict[str, float]:
        """计算目标权重

        支持多种权重计算方法：
        - equal: 等权分配
        - factor_score: 按因子得分加权
        - min_variance: 最小方差优化
        - risk_parity: 风险平价
        - max_sharpe: 最大夏普比
        """
        if not selected:
            return {}

        symbols = [s.symbol for s in selected]
        method = self.config.weight_method

        # 等权分配
        if method == "equal" or (self.config.equal_weight and method == "equal"):
            weight = 1.0 / len(selected)
            return {s.symbol: weight for s in selected}

        # 因子得分加权
        if method == "factor_score":
            total_score = sum(
                abs(s.value) for s in selected if not math.isnan(s.value)
            )
            if total_score == 0:
                weight = 1.0 / len(selected)
                return {s.symbol: weight for s in selected}
            return {
                s.symbol: abs(s.value) / total_score
                for s in selected
                if not math.isnan(s.value)
            }

        # 高级优化方法：需要收益率数据
        if method in ("min_variance", "risk_parity", "max_sharpe"):
            returns = self._get_returns_for_optimizer(symbols)
            if returns is None:
                # 回退到等权
                weight = 1.0 / len(selected)
                return {s.symbol: weight for s in selected}

            from aipha.portfolio.convex_optimizer import create_optimizer

            optimizer = create_optimizer(method)
            return optimizer.optimize(symbols=symbols, returns=returns)

        # 默认等权
        weight = 1.0 / len(selected)
        return {s.symbol: weight for s in selected}

    def _get_returns_for_optimizer(
        self, symbols: list[str]
    ) -> Optional[dict[str, list[float]]]:
        """获取用于优化器的收益率数据

        从价格历史计算收益率。
        """
        if not hasattr(self, "_price_history") or not self._price_history:
            return None

        lookback = self.config.lookback_days
        returns: dict[str, list[float]] = {}

        for symbol in symbols:
            if symbol not in self._price_history:
                continue
            prices = self._price_history[symbol]
            if len(prices) < lookback + 1:
                continue

            # 取最近的 lookback 个价格计算收益率
            recent_prices = prices[-(lookback + 1):]
            symbol_returns = []
            for i in range(1, len(recent_prices)):
                if recent_prices[i - 1] > 0:
                    ret = (recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
                    symbol_returns.append(ret)
            if symbol_returns:
                returns[symbol] = symbol_returns

        if len(returns) < len(symbols):
            return None  # 数据不完整

        return returns

    def _generate_intents_from_targets(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
        target_weights: dict[str, float],
    ) -> list[OrderIntent]:
        """根据目标权重生成订单意图"""
        intents: list[OrderIntent] = []

        # 当前持仓
        current_positions = set(context.positions.keys()) if context.positions else set()
        target_positions = set(target_weights.keys())

        # 需要卖出的持仓
        to_sell = current_positions - target_positions
        for symbol in to_sell:
            qty = context.get_position(symbol)
            if qty > 0:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        intent_type=IntentType.TARGET_POSITION,
                        reason="不在目标持仓中，清仓",
                    )
                )

        # 需要买入/调整的持仓
        for symbol, weight in target_weights.items():
            # 计算目标数量
            if symbol not in bars:
                continue

            price = bars[symbol].close
            if price <= 0:
                continue

            target_value = context.nav * weight
            target_qty = int(target_value / price / 100) * 100  # A股按手

            current_qty = context.get_position(symbol)

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
                            reason="因子选股买入",
                            signal_strength=weight,
                        )
                    )
            elif target_qty < current_qty:
                # 需要卖出
                sell_qty = current_qty - target_qty
                if sell_qty >= 100:
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.SELL,
                            qty=sell_qty,
                            intent_type=IntentType.ORDER,
                            reason="因子选股减仓",
                            signal_strength=weight,
                        )
                    )

        return intents

    def reset(self) -> None:
        """重置策略状态"""
        self._last_rebalance_date = None
        self._days_since_rebalance = 0
        self._current_targets = {}

    def on_start(self) -> None:
        """策略启动"""
        self.reset()

    def on_end(self) -> None:
        """策略结束"""
        pass


class _EnhancedContext:
    """增强的策略上下文

    包装原始 StrategyContext，添加从 bars 获取价格的能力
    """

    def __init__(self, context: StrategyContext, bars: dict[str, Bar]):
        self._context = context
        self._bars = bars

    def get_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        if symbol in self._bars:
            return self._bars[symbol].close
        return None

    def get_historical_prices(self, symbol: str) -> Optional[dict[date, float]]:
        """获取历史价格"""
        if hasattr(self._context, "metadata") and self._context.metadata:
            hist = self._context.metadata.get("historical_prices", {})
            return hist.get(symbol)
        return None

    def get_fundamentals(self, symbol: str) -> Optional[dict[str, Any]]:
        """获取财务数据"""
        if hasattr(self._context, "metadata") and self._context.metadata:
            fund = self._context.metadata.get("fundamentals", {})
            return fund.get(symbol)
        return None
