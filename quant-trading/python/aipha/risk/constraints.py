"""
风险约束实现

提供：
- RiskConstraint: 风控约束抽象基类
- PositionLimit: 单票持仓限制
- SectorLimit: 行业暴露限制
- IndustryNeutralConstraint: 行业中性约束（相对基准偏离控制）
- CompositeRiskManager: 组合风控管理器
- DrawdownLimit: 回撤限制
- TurnoverLimit: 换手限制
- ConcentrationLimit: 集中度限制
- StyleExposureLimit: 风格暴露限制
- ForceRedeemExposureLimit: 强赎暴露限制
- LiquidityConstraint: 流动性约束
- PriceDeviationGuard: 价格偏离MA5防护（Fat-finger #2, §14.1）
- MarketRegimeDetector: 市场状态检测器（风险覆盖层）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RiskViolationType(str, Enum):
    """风险违规类型"""

    POSITION_LIMIT = "POSITION_LIMIT"  # 单票限制
    SECTOR_LIMIT = "SECTOR_LIMIT"  # 行业限制
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"  # 回撤限制
    TURNOVER_LIMIT = "TURNOVER_LIMIT"  # 换手限制
    FORCE_REDEEM_EXPOSURE = "FORCE_REDEEM_EXPOSURE"  # 强赎暴露
    LIQUIDITY_LIMIT = "LIQUIDITY_LIMIT"  # 流动性限制
    PRICE_DEVIATION = "PRICE_DEVIATION"  # 价格偏离(Fat-finger)
    OTHER = "OTHER"


@dataclass(frozen=True)
class RiskCheckResult:
    """风控检查结果

    Attributes:
        passed: 是否通过
        violation_type: 违规类型（如果未通过）
        message: 详细信息
        adjusted_qty: 调整后的数量（如果有）
        metadata: 额外元数据
    """

    passed: bool
    violation_type: Optional[RiskViolationType] = None
    message: str = ""
    adjusted_qty: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RiskConstraint(ABC):
    """风控约束抽象基类"""

    @abstractmethod
    def check(
        self,
        symbol: str,
        qty: int,
        side: str,  # "BUY" or "SELL"
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """
        检查是否满足风控约束

        Args:
            symbol: 标的代码
            qty: 交易数量
            side: 交易方向
            current_positions: 当前持仓
            nav: 当前净值
            prices: 当前价格

        Returns:
            RiskCheckResult: 检查结果
        """
        raise NotImplementedError


@dataclass
class PositionLimit(RiskConstraint):
    """单票持仓限制

    限制单个标的的持仓占比不超过 NAV 的指定比例。

    Attributes:
        max_weight: 单票最大权重（0.1 = 10%）
        lot_size: 最小交易单位（A股=100, 可转债=10）
    """

    max_weight: float = 0.10  # 默认 10%
    lot_size: int = 100

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查单票持仓限制"""
        if side == "SELL":
            # 卖出不检查
            return RiskCheckResult(passed=True)

        if nav <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.POSITION_LIMIT,
                message="NAV 必须为正数",
            )

        price = prices.get(symbol)
        if not price:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.POSITION_LIMIT,
                message=f"未找到 {symbol} 价格",
            )

        # 计算交易后持仓
        current_qty = current_positions.get(symbol, 0)
        new_qty = current_qty + qty
        new_value = new_qty * price
        new_weight = new_value / nav

        if new_weight > self.max_weight:
            # 计算允许的最大数量
            max_value = nav * self.max_weight
            max_qty = int(max_value / price / self.lot_size) * self.lot_size
            allowed_qty = max(0, max_qty - current_qty)

            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.POSITION_LIMIT,
                message=f"{symbol} 持仓权重 {new_weight:.2%} 超过限制 {self.max_weight:.2%}",
                adjusted_qty=allowed_qty if allowed_qty >= self.lot_size else 0,
                metadata={"current_weight": current_qty * price / nav, "new_weight": new_weight},
            )

        return RiskCheckResult(passed=True)


@dataclass
class SectorLimit(RiskConstraint):
    """行业暴露限制

    限制单个行业的持仓占比不超过指定比例。

    Attributes:
        max_weight: 行业最大权重（0.3 = 30%）
        sector_mapping: 标的 -> 行业映射
    """

    max_weight: float = 0.30  # 默认 30%
    sector_mapping: dict[str, str] = field(default_factory=dict)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查行业暴露限制"""
        if side == "SELL":
            return RiskCheckResult(passed=True)

        if nav <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.SECTOR_LIMIT,
                message="NAV 必须为正数",
            )

        # 获取标的所属行业
        sector = self.sector_mapping.get(symbol)
        if not sector:
            # 无行业映射，通过
            return RiskCheckResult(passed=True)

        price = prices.get(symbol)
        if not price:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.SECTOR_LIMIT,
                message=f"未找到 {symbol} 价格",
            )

        # 计算当前行业暴露
        current_sector_value = 0.0
        for pos_symbol, pos_qty in current_positions.items():
            if self.sector_mapping.get(pos_symbol) == sector:
                pos_price = prices.get(pos_symbol, 0)
                current_sector_value += pos_qty * pos_price

        # 计算交易后行业暴露
        current_qty = current_positions.get(symbol, 0)
        new_sector_value = current_sector_value + (qty + current_qty) * price - current_qty * price
        new_sector_weight = new_sector_value / nav

        if new_sector_weight > self.max_weight:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.SECTOR_LIMIT,
                message=f"行业 {sector} 暴露 {new_sector_weight:.2%} 超过限制 {self.max_weight:.2%}",
                metadata={
                    "sector": sector,
                    "current_weight": current_sector_value / nav,
                    "new_weight": new_sector_weight,
                },
            )

        return RiskCheckResult(passed=True)


@dataclass
class CompositeRiskManager:
    """组合风控管理器

    组合多个风控约束，依次检查。

    Attributes:
        constraints: 风控约束列表
    """

    constraints: list[RiskConstraint] = field(default_factory=list)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查所有风控约束（聚合模式）

        遍历所有约束，收集全部违规结果，取最严格的 adjusted_qty。
        不再在第一个失败处短路，以便调用方看到完整的约束情况。
        """
        worst_result: RiskCheckResult | None = None
        tightest_qty: int | None = None
        all_messages: list[str] = []

        for constraint in self.constraints:
            result = constraint.check(
                symbol=symbol,
                qty=qty,
                side=side,
                current_positions=current_positions,
                nav=nav,
                prices=prices,
            )
            if not result.passed:
                all_messages.append(result.message)
                adj = result.adjusted_qty
                if adj is not None:
                    if tightest_qty is None:
                        tightest_qty = adj
                    else:
                        tightest_qty = min(tightest_qty, adj)
                else:
                    # 无 adjusted_qty 意味着完全拒绝
                    tightest_qty = 0

                if worst_result is None:
                    worst_result = result

        if worst_result is not None:
            return RiskCheckResult(
                passed=False,
                violation_type=worst_result.violation_type,
                message=" | ".join(all_messages),
                adjusted_qty=tightest_qty,
                metadata=worst_result.metadata,
            )

        return RiskCheckResult(passed=True)

    def check_batch(
        self,
        orders: list[tuple[str, int, str]],  # (symbol, qty, side)
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> dict[str, RiskCheckResult]:
        """批量检查订单

        Args:
            orders: 订单列表 (symbol, qty, side)
            current_positions: 当前持仓
            nav: 当前净值
            prices: 当前价格

        Returns:
            symbol -> RiskCheckResult 映射
        """
        results = {}
        for symbol, qty, side in orders:
            results[symbol] = self.check(
                symbol=symbol,
                qty=qty,
                side=side,
                current_positions=current_positions,
                nav=nav,
                prices=prices,
            )
        return results


@dataclass
class DrawdownLimit(RiskConstraint):
    """回撤限制约束

    当组合回撤超过阈值时，限制或禁止新开仓。

    Attributes:
        max_drawdown: 最大回撤阈值（0.1 = 10%）
        reduce_position_at: 开始减仓的回撤阈值
        allow_reduce_only: 超限时是否只允许减仓
        scale_factor_floor: 线性减仓时允许的最小缩放比例（默认 0.0）
            对以轮转为主的 CB 策略建议设置 0.3~0.5，防止仓位线性缩减至近零
            产生自我强化的回撤反馈循环。
        allow_rotation: 当 True 时，如果当前已有持仓（策略正在做轮转），
            则在减仓区间（reduce_position_at <= dd < max_drawdown）跳过
            比例缩减，允许以至少 scale_factor_floor 的比例开仓；
            超过 max_drawdown 时仍完全阻止新开仓。
    """

    max_drawdown: float = 0.20  # 默认 20% 最大回撤
    reduce_position_at: float = 0.10  # 10% 时开始减仓
    allow_reduce_only: bool = True
    lot_size: int = 100  # 最小交易单位
    scale_factor_floor: float = 0.0  # 线性减仓最小缩放比例（0 = 全减，1 = 不减）
    allow_rotation: bool = False       # 有持仓时允许跳过减仓区间缩减
    peak_reset_after_flat_days: int = 0  # 全仓现金 N 天后重置 peak_nav（0 = 禁用）

    # 运行时状态（需外部更新）
    peak_nav: float = 0.0
    current_nav: float = 0.0
    _no_position_days: int = 0  # 连续无持仓天数计数器

    def update_nav(self, nav: float) -> None:
        """更新NAV（需在每次计算后调用）"""
        self.current_nav = nav
        if nav > self.peak_nav:
            self.peak_nav = nav

    def update_position_state(self, has_any_position: bool) -> None:
        """更新持仓状态，用于 peak_nav 重置判断。

        当组合因回撤限制被迫全部清仓后，如果连续
        ``peak_reset_after_flat_days`` 个交易日无持仓（100% 现金），
        则重置 peak_nav = current_nav，打破"回撤拦截→全现金→永久
        无法恢复"的死锁。

        应由引擎在每个 bar 处理后（update_nav 之后）调用一次。
        """
        if self.peak_reset_after_flat_days <= 0:
            return  # 功能未启用

        current_dd = self.get_current_drawdown()
        if not has_any_position and current_dd >= self.reduce_position_at:
            self._no_position_days += 1
            if self._no_position_days >= self.peak_reset_after_flat_days:
                import logging
                _logger = logging.getLogger(__name__)
                _logger.warning(
                    f"DrawdownLimit: 连续 {self._no_position_days} 天无持仓且"
                    f"回撤 {current_dd:.2%} >= {self.reduce_position_at:.2%}，"
                    f"重置 peak_nav {self.peak_nav:.0f} → {self.current_nav:.0f}"
                )
                self.peak_nav = self.current_nav
                self._no_position_days = 0
        else:
            self._no_position_days = 0

    def get_current_drawdown(self) -> float:
        """获取当前回撤"""
        if self.peak_nav <= 0:
            return 0.0
        return (self.peak_nav - self.current_nav) / self.peak_nav

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查回撤限制"""
        # 仅更新 current_nav（peak_nav 由 update_nav 在 bar_event 级别维护，
        # 避免 check 被多次调用时提前刷新 peak）
        self.current_nav = nav
        current_dd = self.get_current_drawdown()

        # 卖出始终允许
        if side == "SELL":
            return RiskCheckResult(passed=True)

        # 超过最大回撤，禁止开仓
        if current_dd >= self.max_drawdown:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.DRAWDOWN_LIMIT,
                message=f"当前回撤 {current_dd:.2%} 超过最大限制 {self.max_drawdown:.2%}，禁止开仓",
                adjusted_qty=0,
                metadata={"current_drawdown": current_dd, "peak_nav": self.peak_nav},
            )

        # 超过减仓阈值，限制开仓规模
        if current_dd >= self.reduce_position_at:
            # allow_rotation: 有持仓时跳过比例缩减（轮转策略防反馈循环）
            has_positions = bool(current_positions)
            if self.allow_rotation and has_positions:
                # 轮转模式：直接通过，不缩减规模
                return RiskCheckResult(
                    passed=True,
                    message=f"当前回撤 {current_dd:.2%}，轮转模式允许开仓",
                    metadata={"current_drawdown": current_dd, "rotation_bypass": True},
                )

            # 根据回撤程度线性减少允许的仓位
            reduction_ratio = (current_dd - self.reduce_position_at) / (
                self.max_drawdown - self.reduce_position_at
            )
            # scale_factor_floor 防止线性减仓缩减至近零（CB策略专用）
            allowed_ratio = max(
                self.scale_factor_floor,
                1.0 - reduction_ratio,
            )
            adjusted_qty = int(qty * allowed_ratio / self.lot_size) * self.lot_size

            if adjusted_qty < self.lot_size:
                return RiskCheckResult(
                    passed=False,
                    violation_type=RiskViolationType.DRAWDOWN_LIMIT,
                    message=f"当前回撤 {current_dd:.2%}，已减少开仓规模至 0",
                    adjusted_qty=0,
                    metadata={"current_drawdown": current_dd, "allowed_ratio": allowed_ratio},
                )

            return RiskCheckResult(
                passed=True,
                message=f"当前回撤 {current_dd:.2%}，已调整开仓规模",
                adjusted_qty=adjusted_qty,
                metadata={"current_drawdown": current_dd, "allowed_ratio": allowed_ratio},
            )

        return RiskCheckResult(passed=True)


@dataclass
class TurnoverLimit(RiskConstraint):
    """换手率限制约束

    限制每日/周期内的换手率不超过阈值。

    Attributes:
        max_daily_turnover: 日最大换手率（相对于NAV）
        max_single_trade: 单笔最大交易金额占比
    """

    max_daily_turnover: float = 0.50  # 默认 50% 日换手
    max_single_trade: float = 0.10  # 单笔最大 10%
    lot_size: int = 100  # 最小交易单位

    # 运行时状态
    daily_traded_value: float = 0.0
    last_reset_date: str = ""

    def reset_daily(self, date: str) -> None:
        """重置每日计数器"""
        if date != self.last_reset_date:
            self.daily_traded_value = 0.0
            self.last_reset_date = date

    def record_trade(self, value: float) -> None:
        """记录交易金额"""
        self.daily_traded_value += abs(value)

    def get_remaining_capacity(self, nav: float) -> float:
        """获取剩余可交易金额"""
        max_value = nav * self.max_daily_turnover
        return max(0.0, max_value - self.daily_traded_value)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查换手率限制"""
        if nav <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.TURNOVER_LIMIT,
                message="NAV 必须为正数",
            )

        price = prices.get(symbol)
        if not price:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.TURNOVER_LIMIT,
                message=f"未找到 {symbol} 价格",
            )

        trade_value = qty * price
        trade_weight = trade_value / nav

        # 检查单笔交易限制
        if trade_weight > self.max_single_trade:
            max_qty = int(nav * self.max_single_trade / price / self.lot_size) * self.lot_size
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.TURNOVER_LIMIT,
                message=f"单笔交易 {trade_weight:.2%} 超过限制 {self.max_single_trade:.2%}",
                adjusted_qty=max_qty if max_qty >= self.lot_size else 0,
                metadata={"trade_weight": trade_weight},
            )

        # 检查日换手限制
        remaining = self.get_remaining_capacity(nav)
        if trade_value > remaining:
            max_qty = int(remaining / price / self.lot_size) * self.lot_size
            current_turnover = self.daily_traded_value / nav

            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.TURNOVER_LIMIT,
                message=f"日换手已达 {current_turnover:.2%}，剩余额度不足",
                adjusted_qty=max_qty if max_qty >= self.lot_size else 0,
                metadata={
                    "current_turnover": current_turnover,
                    "remaining_capacity": remaining,
                },
            )

        return RiskCheckResult(passed=True)


@dataclass
class ConcentrationLimit(RiskConstraint):
    """集中度限制约束

    限制持仓集中度，确保分散化。

    Attributes:
        max_top_n_weight: 前N大持仓最大权重
        top_n: 前N大持仓数量
        min_positions: 最少持仓数量
    """

    max_top_n_weight: float = 0.50  # 前N大持仓最多占 50%
    top_n: int = 5
    min_positions: int = 10

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查集中度限制"""
        if side == "SELL":
            # 检查是否会导致持仓数量低于最小值
            if symbol in current_positions and current_positions[symbol] <= qty:
                # 将完全清仓
                remaining_positions = len([k for k, v in current_positions.items() if v > 0 and k != symbol])
                if remaining_positions < self.min_positions:
                    return RiskCheckResult(
                        passed=False,
                        violation_type=RiskViolationType.OTHER,
                        message=f"清仓后持仓数量 {remaining_positions} 低于最低要求 {self.min_positions}",
                        metadata={"remaining_positions": remaining_positions},
                    )
            return RiskCheckResult(passed=True)

        if nav <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.OTHER,
                message="NAV 必须为正数",
            )

        # 计算交易后的持仓权重
        position_values = []
        for pos_symbol, pos_qty in current_positions.items():
            pos_price = prices.get(pos_symbol, 0)
            if pos_symbol == symbol:
                position_values.append((pos_symbol, (pos_qty + qty) * pos_price))
            else:
                position_values.append((pos_symbol, pos_qty * pos_price))

        # 如果是新持仓
        if symbol not in current_positions:
            price = prices.get(symbol, 0)
            position_values.append((symbol, qty * price))

        # 按价值排序
        position_values.sort(key=lambda x: x[1], reverse=True)

        # 计算前N大权重
        top_n_value = sum(v for _, v in position_values[: self.top_n])
        top_n_weight = top_n_value / nav

        if top_n_weight > self.max_top_n_weight:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.OTHER,
                message=f"前 {self.top_n} 大持仓权重 {top_n_weight:.2%} 超过限制 {self.max_top_n_weight:.2%}",
                metadata={"top_n_weight": top_n_weight},
            )

        return RiskCheckResult(passed=True)


@dataclass
class StyleExposureLimit(RiskConstraint):
    """风格暴露限制约束

    限制组合对特定风格因子的暴露。

    Attributes:
        max_exposure: 各风格因子最大暴露
        style_scores: 标的 -> 风格分数映射
    """

    max_exposure: dict[str, float] = field(default_factory=dict)  # {style: max_exposure}
    style_scores: dict[str, dict[str, float]] = field(default_factory=dict)  # {symbol: {style: score}}

    def update_style_scores(self, scores: dict[str, dict[str, float]]) -> None:
        """更新风格分数映射（每个交易日调用一次）。

        Args:
            scores: {symbol: {style_name: score}} 映射
        """
        self.style_scores.update(scores)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查风格暴露限制"""
        if side == "SELL":
            return RiskCheckResult(passed=True)

        if nav <= 0 or not self.max_exposure:
            return RiskCheckResult(passed=True)

        price = prices.get(symbol)
        if not price:
            return RiskCheckResult(passed=True)

        # 计算交易后各风格暴露
        for style, max_exp in self.max_exposure.items():
            # 计算当前暴露
            current_exposure = 0.0
            total_weight = 0.0

            for pos_symbol, pos_qty in current_positions.items():
                pos_price = prices.get(pos_symbol, 0)
                weight = pos_qty * pos_price / nav
                total_weight += weight

                score = self.style_scores.get(pos_symbol, {}).get(style, 0.0)
                current_exposure += weight * score

            # 计算新增暴露
            new_weight = qty * price / nav
            new_score = self.style_scores.get(symbol, {}).get(style, 0.0)
            new_exposure = current_exposure + new_weight * new_score

            if abs(new_exposure) > max_exp:
                return RiskCheckResult(
                    passed=False,
                    violation_type=RiskViolationType.OTHER,
                    message=f"风格 {style} 暴露 {new_exposure:.3f} 超过限制 ±{max_exp:.3f}",
                    metadata={
                        "style": style,
                        "current_exposure": current_exposure,
                        "new_exposure": new_exposure,
                    },
                )

        return RiskCheckResult(passed=True)


# ============================================================================
# 可转债专用风控约束
# ============================================================================


@dataclass
class ForceRedeemExposureLimit(RiskConstraint):
    """强赎暴露限制

    限制 cb_value >= threshold（接近强赎触发）的持仓占 NAV 的比例。
    当可转债的转股价值接近或超过强赎触发线（通常 130%），
    存在被强制赎回的风险，需要限制此类持仓的聚集度。

    Attributes:
        max_weight: 强赎暴露区间持仓最大权重（默认 0.30 = 30%）
        cb_value_threshold: 转股价值阈值（默认 125.0）
        lot_size: 最小交易单位（可转债=10）
        cb_values: 标的 -> 当前转股价值映射（运行时更新）
    """

    max_weight: float = 0.30
    cb_value_threshold: float = 125.0
    lot_size: int = 10
    cb_values: dict[str, float] = field(default_factory=dict)

    def update_cb_values(self, values: dict[str, float]) -> None:
        """更新转股价值映射（每个 bar 调用一次）"""
        self.cb_values.update(values)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查强赎暴露限制"""
        if side == "SELL":
            return RiskCheckResult(passed=True)

        if nav <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.FORCE_REDEEM_EXPOSURE,
                message="NAV 必须为正数",
            )

        # 该标的的 cb_value 是否在强赎区间
        target_cb_value = self.cb_values.get(symbol, 0.0)
        target_in_zone = target_cb_value >= self.cb_value_threshold

        # 计算当前强赎区间暴露
        current_exposure_value = 0.0
        for pos_symbol, pos_qty in current_positions.items():
            pos_cb_value = self.cb_values.get(pos_symbol, 0.0)
            if pos_cb_value >= self.cb_value_threshold:
                pos_price = prices.get(pos_symbol, 0)
                current_exposure_value += pos_qty * pos_price

        # 如果目标标的在强赎区间，加上新买入量
        if target_in_zone:
            price = prices.get(symbol, 0)
            if not price:
                return RiskCheckResult(
                    passed=False,
                    violation_type=RiskViolationType.FORCE_REDEEM_EXPOSURE,
                    message=f"未找到 {symbol} 价格",
                )
            new_exposure_value = current_exposure_value + qty * price
            new_exposure_weight = new_exposure_value / nav

            if new_exposure_weight > self.max_weight:
                # 计算可接受的最大买入量
                allowed_value = nav * self.max_weight - current_exposure_value
                max_qty = int(allowed_value / price / self.lot_size) * self.lot_size

                return RiskCheckResult(
                    passed=False,
                    violation_type=RiskViolationType.FORCE_REDEEM_EXPOSURE,
                    message=(
                        f"强赎暴露 {new_exposure_weight:.2%} 超过限制 {self.max_weight:.2%} "
                        f"(cb_value={target_cb_value:.1f} >= {self.cb_value_threshold:.1f})"
                    ),
                    adjusted_qty=max_qty if max_qty >= self.lot_size else 0,
                    metadata={
                        "current_exposure": current_exposure_value / nav,
                        "new_exposure": new_exposure_weight,
                        "cb_value": target_cb_value,
                    },
                )

        return RiskCheckResult(passed=True)


@dataclass
class LiquidityConstraint(RiskConstraint):
    """流动性约束

    限制单个标的的持仓不超过其日均成交量的指定比例。
    防止持仓过大导致难以在合理价格内出清。

    Attributes:
        max_participation_rate: 最大参与率（默认 0.10 = 10%）
        lot_size: 最小交易单位
        avg_volumes: 标的 -> 日均成交量映射（运行时更新）
    """

    max_participation_rate: float = 0.10
    lot_size: int = 10
    avg_volumes: dict[str, float] = field(default_factory=dict)

    def update_volumes(self, volumes: dict[str, float]) -> None:
        """更新日均成交量映射"""
        self.avg_volumes.update(volumes)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查流动性约束"""
        if side == "SELL":
            return RiskCheckResult(passed=True)

        avg_vol = self.avg_volumes.get(symbol, 0)
        # 处理 NaN: math.isnan 检测浮点 NaN
        import math
        if isinstance(avg_vol, float) and math.isnan(avg_vol):
            avg_vol = 0
        if avg_vol <= 0:
            # P1-2修复: 无成交量数据时阻止买入而非静默放行
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.LIQUIDITY_LIMIT,
                message=f"{symbol} 无日均成交量数据，阻止买入",
                adjusted_qty=0,
            )

        # 交易后持仓 vs 日均成交量
        current_qty = current_positions.get(symbol, 0)
        new_qty = current_qty + qty

        if new_qty > avg_vol * self.max_participation_rate:
            max_allowed_qty = int(
                avg_vol * self.max_participation_rate / self.lot_size
            ) * self.lot_size
            allowed_buy = max(0, max_allowed_qty - current_qty)

            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.LIQUIDITY_LIMIT,
                message=(
                    f"{symbol} 持仓 {new_qty} 超过日均成交量 "
                    f"{avg_vol:.0f} 的 {self.max_participation_rate:.0%}"
                ),
                adjusted_qty=allowed_buy if allowed_buy >= self.lot_size else 0,
                metadata={
                    "avg_volume": avg_vol,
                    "current_qty": current_qty,
                    "max_participation_rate": self.max_participation_rate,
                },
            )

        return RiskCheckResult(passed=True)


# ---------------------------------------------------------------------------
# 行业中性约束（相对基准偏离控制）
# ---------------------------------------------------------------------------

@dataclass
class IndustryNeutralConstraint(RiskConstraint):
    """行业中性约束 — 控制组合相对于基准的行业偏离。

    与 SectorLimit（绝对上限）不同，此约束计算组合行业权重与基准行业权重的
    偏离，并限制偏离不超过指定阈值。

    适用于指数增强策略（如跟踪沪深300/中证500），避免行业暴露过大
    导致跟踪误差不可控。

    Attributes:
        max_deviation: 单一行业最大偏离（如 0.05 = 5%）
        sector_mapping: {symbol: industry} 映射
        benchmark_weights: {industry: weight} 基准行业权重
        lot_size: 最小交易单位
    """

    max_deviation: float = 0.05
    sector_mapping: dict[str, str] = field(default_factory=dict)
    benchmark_weights: dict[str, float] = field(default_factory=dict)
    lot_size: int = 100

    def update_benchmark(self, weights: dict[str, float]) -> None:
        """更新基准行业权重（可定期刷新）。"""
        self.benchmark_weights = dict(weights)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查行业偏离约束。"""
        if side == "SELL":
            return RiskCheckResult(passed=True)

        if nav <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.SECTOR_LIMIT,
                message="NAV 必须为正数",
            )

        sector = self.sector_mapping.get(symbol)
        if not sector:
            return RiskCheckResult(passed=True)

        price = prices.get(symbol, 0)
        if price <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.SECTOR_LIMIT,
                message=f"未找到 {symbol} 价格",
            )

        # 计算当前行业暴露
        sector_values: dict[str, float] = {}
        for pos_sym, pos_qty in current_positions.items():
            sec = self.sector_mapping.get(pos_sym)
            if sec:
                pos_price = prices.get(pos_sym, 0)
                sector_values[sec] = sector_values.get(sec, 0) + pos_qty * pos_price

        # 模拟买入后的行业暴露
        additional_value = qty * price
        sector_values[sector] = sector_values.get(sector, 0) + additional_value

        new_portfolio_weight = sector_values[sector] / nav
        benchmark_weight = self.benchmark_weights.get(sector, 0)
        deviation = new_portfolio_weight - benchmark_weight

        if deviation > self.max_deviation:
            # 计算允许的最大买入量
            max_sector_value = (benchmark_weight + self.max_deviation) * nav
            current_sector_value = sector_values[sector] - additional_value
            allowed_value = max(0, max_sector_value - current_sector_value)
            allowed_qty = int(allowed_value / price / self.lot_size) * self.lot_size

            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.SECTOR_LIMIT,
                message=(
                    f"行业 {sector} 偏离 {deviation:.2%} 超过限制 {self.max_deviation:.2%}"
                    f" (组合={new_portfolio_weight:.2%}, 基准={benchmark_weight:.2%})"
                ),
                adjusted_qty=allowed_qty if allowed_qty >= self.lot_size else 0,
                metadata={
                    "sector": sector,
                    "portfolio_weight": new_portfolio_weight,
                    "benchmark_weight": benchmark_weight,
                    "deviation": deviation,
                },
            )

        return RiskCheckResult(passed=True)

    def get_all_deviations(
        self,
        positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> dict[str, float]:
        """计算所有行业的偏离度（用于报告/监控）。"""
        sector_values: dict[str, float] = {}
        for sym, qty in positions.items():
            sec = self.sector_mapping.get(sym)
            if sec:
                pos_price = prices.get(sym, 0)
                sector_values[sec] = sector_values.get(sec, 0) + qty * pos_price

        deviations: dict[str, float] = {}
        all_sectors = set(sector_values.keys()) | set(self.benchmark_weights.keys())
        for sec in all_sectors:
            portfolio_w = sector_values.get(sec, 0) / nav if nav > 0 else 0
            benchmark_w = self.benchmark_weights.get(sec, 0)
            deviations[sec] = portfolio_w - benchmark_w

        return deviations


# ---------------------------------------------------------------------------
# Fat-finger 防护: 价格偏离约束 (§14.1 #2)
# ---------------------------------------------------------------------------


@dataclass
class PriceDeviationGuard(RiskConstraint):
    """价格偏离 MA5 防护 (Fat-finger #2)。

    当标的当前价格偏离其 5 日均线 ≥ max_deviation_pct 时阻止买入。
    用于防止 flash crash / 数据错误 / 胖手指导致的异常价格交易。

    Attributes:
        max_deviation_pct: 最大允许偏离比例（默认 0.20 = 20%）
        ma_prices: 标的 -> MA5 价格映射（由引擎或调用方在每 bar 更新）
    """

    max_deviation_pct: float = 0.20
    ma_prices: dict[str, float] = field(default_factory=dict)

    def update_ma_prices(self, ma5: dict[str, float]) -> None:
        """更新标的的 MA5 价格映射。"""
        self.ma_prices.update(ma5)

    def check(
        self,
        symbol: str,
        qty: int,
        side: str,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查价格偏离 MA5 是否超限。"""
        if side == "SELL":
            return RiskCheckResult(passed=True)

        current_price = prices.get(symbol, 0.0)
        ma5 = self.ma_prices.get(symbol, 0.0)

        if ma5 <= 0 or current_price <= 0:
            # 数据缺失时不阻止（由其他约束兜底）
            return RiskCheckResult(passed=True)

        deviation = abs(current_price - ma5) / ma5

        if deviation >= self.max_deviation_pct:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.PRICE_DEVIATION,
                message=(
                    f"{symbol} 价格偏离 MA5 {deviation:.1%} "
                    f"≥ {self.max_deviation_pct:.0%} 阈值 "
                    f"(price={current_price:.3f}, MA5={ma5:.3f})"
                ),
                adjusted_qty=0,
                metadata={"deviation_pct": deviation, "price": current_price, "ma5": ma5},
            )

        return RiskCheckResult(passed=True)


# ---------------------------------------------------------------------------
# 市场状态检测器
# ---------------------------------------------------------------------------

@dataclass
class MarketRegimeDetector:
    """市场状态检测器 — 多信号确认机制。

    使用三到四种独立信号的投票机制判断市场状态（牛市/震荡/熊市）：
    1. **趋势动量**：中期价格回报率（经典趋势跟踪）
    2. **均线交叉**：短期均线 vs 长期均线（趋势确认）
    3. **波动率状态**：波动率高低（尾部风险信号）
    4. **RSRS 择时**（可选）：支撑/阻力相对强度（光大证券研报方法）

    多信号投票规则：
    - 2/3 或 3/4 信号一致 → 采纳该状态
    - 信号各不相同 → NEUTRAL
    - 高波动率在任何状态下都施加仓位惩罚

    学术参考：
    - Moskowitz, Ooi, Pedersen (2012) "Time Series Momentum"
    - 趋势跟踪 + 波动率目标机制（AQR 风险平价方法论）
    - 光大证券《技术择时系列报告》 — RSRS 标准分指标择时

    Attributes:
        bull_threshold: 动量阈值（高于此为牛市信号）
        bear_threshold: 动量阈值（低于此为熊市信号）
        vol_lookback: 波动率计算回看期
        momentum_lookback: 趋势动量回看期
        ma_short: 短期均线窗口（默认 10 日）
        ma_long: 长期均线窗口（默认 30 日）
        high_vol_threshold: 高波动率阈值（日收益率标准差）
        vol_scale_factor: 高波动环境下的仓位缩放因子
        use_rsrs: 是否启用 RSRS 择时信号（默认 False）
    """

    bull_threshold: float = 0.10
    bear_threshold: float = -0.08
    vol_lookback: int = 20
    momentum_lookback: int = 60
    ma_short: int = 10
    ma_long: int = 30
    high_vol_threshold: float = 0.03
    vol_scale_factor: float = 0.8
    use_rsrs: bool = False
    # 仓位缩放系数（可配置）
    bear_scale: float = 0.50
    neutral_scale: float = 0.80

    # 内部状态
    _index_history: list[float] = field(default_factory=list)
    _rsrs_factor: Any = field(default=None, repr=False)
    _high_history: list[float] = field(default_factory=list)
    _low_history: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.use_rsrs:
            try:
                from aipha.factors.rsrs_timing import RSRSTimingFactor
                self._rsrs_factor = RSRSTimingFactor()
            except ImportError:
                logger.warning("RSRS 模块不可用, 降级为 3 信号模式")
                self.use_rsrs = False

    def update(self, index_close: float,
               index_high: float | None = None,
               index_low: float | None = None) -> None:
        """每日更新基准指数收盘价（及可选的最高/最低价）。"""
        self._index_history.append(index_close)
        max_len = max(self.momentum_lookback, self.vol_lookback, self.ma_long) + 10
        if len(self._index_history) > max_len:
            del self._index_history[:-max_len]

        # 更新 RSRS
        if self.use_rsrs and self._rsrs_factor is not None:
            h = index_high if index_high is not None else index_close
            l = index_low if index_low is not None else index_close
            self._rsrs_factor.update(h, l)

    def _momentum_signal(self) -> str:
        """信号 1：趋势动量"""
        if len(self._index_history) < self.momentum_lookback + 1:
            return "NEUTRAL"
        old = self._index_history[-(self.momentum_lookback + 1)]
        new = self._index_history[-1]
        momentum = (new - old) / old if old > 0 else 0
        if momentum > self.bull_threshold:
            return "BULL"
        elif momentum < self.bear_threshold:
            return "BEAR"
        return "NEUTRAL"

    def _ma_crossover_signal(self) -> str:
        """信号 2：均线交叉（短均线 vs 长均线）

        短均线 > 长均线 → BULL；短均线 < 长均线 × 0.98 → BEAR；否则 NEUTRAL
        """
        if len(self._index_history) < self.ma_long:
            return "NEUTRAL"
        short_ma = sum(self._index_history[-self.ma_short:]) / self.ma_short
        long_ma = sum(self._index_history[-self.ma_long:]) / self.ma_long
        if long_ma <= 0:
            return "NEUTRAL"
        ratio = short_ma / long_ma
        if ratio > 1.0:
            return "BULL"
        elif ratio < 0.98:
            return "BEAR"
        return "NEUTRAL"

    def _volatility_signal(self) -> str:
        """信号 3：波动率状态

        高波动 → BEAR（尾部风险）；低波动 → NEUTRAL
        """
        vol = self.current_volatility()
        if vol > self.high_vol_threshold:
            return "BEAR"
        return "NEUTRAL"

    def _rsrs_signal(self) -> str:
        """信号 4（可选）：RSRS 择时。

        通过 RSRSTimingFactor 获取最新信号并映射到字符串。
        """
        if self._rsrs_factor is None:
            return "NEUTRAL"
        from aipha.factors.rsrs_timing import MarketSignal
        sig = self._rsrs_factor.get_signal()
        if sig == MarketSignal.BULL:
            return "BULL"
        elif sig == MarketSignal.BEAR:
            return "BEAR"
        return "NEUTRAL"

    def detect(self) -> str:
        """多信号投票检测当前市场状态。返回 'BULL' / 'NEUTRAL' / 'BEAR'。

        投票规则：需要至少半数以上信号一致才确认为该状态。
        """
        signals = [
            self._momentum_signal(),
            self._ma_crossover_signal(),
            self._volatility_signal(),
        ]
        if self.use_rsrs and self._rsrs_factor is not None:
            signals.append(self._rsrs_signal())

        bull_count = signals.count("BULL")
        bear_count = signals.count("BEAR")
        threshold = len(signals) // 2 + 1  # 过半数

        if bull_count >= threshold:
            return "BULL"
        elif bear_count >= threshold:
            return "BEAR"
        return "NEUTRAL"

    def signal_detail(self) -> dict[str, str]:
        """返回各信号的详细状态（用于日志/审计）。"""
        detail = {
            "momentum": self._momentum_signal(),
            "ma_crossover": self._ma_crossover_signal(),
            "volatility": self._volatility_signal(),
            "composite": self.detect(),
        }
        if self.use_rsrs and self._rsrs_factor is not None:
            detail["rsrs"] = self._rsrs_signal()
        return detail

    def current_volatility(self) -> float:
        """计算近期波动率（日收益率标准差）。"""
        if len(self._index_history) < self.vol_lookback + 1:
            return 0.0

        prices = self._index_history[-(self.vol_lookback + 1):]
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])

        if len(returns) < 2:
            return 0.0

        import statistics as _stats
        return _stats.stdev(returns)

    def is_high_volatility(self) -> bool:
        """当前是否处于高波动率环境。"""
        return self.current_volatility() > self.high_vol_threshold

    def position_scale(self) -> float:
        """根据市场状态建议仓位缩放系数（0.0~1.0）。

        高波动环境下额外施加缩放惩罚。
        """
        regime = self.detect()
        vol_penalty = self.vol_scale_factor if self.is_high_volatility() else 1.0

        if regime == "BULL":
            return min(1.0, 1.0 * vol_penalty)
        elif regime == "BEAR":
            return min(self.bear_scale, self.bear_scale * vol_penalty)
        else:
            return min(self.neutral_scale, self.neutral_scale * vol_penalty)