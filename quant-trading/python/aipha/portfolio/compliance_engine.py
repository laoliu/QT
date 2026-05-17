"""组合层合规检查引擎 — ComplianceEngine。

在 PortfolioManager.rebalance() 调用前拦截非合规调仓，
基于 `configs/algo_trading_compliance.yml` 配置核查订单合规性。

强制执行的合规规则（依据 2024-10 程序化交易管理办法 + 项目规范）：
1. 参与率约束：单票参与率 ≤ 10%（拥挤时降至 5%）
2. 价格约束：涨停不买入，跌停不卖出（合规配置 treat_limit_*）
3. 停牌过滤：停牌标的不得交易
4. 最小交易单位：CB lot=10，A股 lot=100
5. 强赎暴露：CB 转股价值 ≥ 125% 持仓占比 ≤ 30%（force_redeem）

输出：
- ComplianceResult（合规检查结果 + 拦截理由）
- 审计日志写入 logs/audit/compliance_alert_{YYYYMMDD}.jsonl

与现有架构的关系：
- 配置来自 algo_trading_compliance.yml（config_runner.py 加载）
- 与 risk/crowding_limit.py（Jaccard）互补（crowding 不在此层检查）
- AgentAudit 记录合规事件 trace
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 默认配置回退值（在 yml 未加载时使用）
_DEFAULT_MAX_PARTICIPATION = 0.10
_DEFAULT_MAX_CANCEL_RATE = 0.80
_DEFAULT_CB_LOT = 10
_DEFAULT_EQUITY_LOT = 100


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ComplianceOrderIntent:
    """合规检查用调仓意图（单票）。

    与 domain.models.OrderIntent（策略层）和
    trading_ops.ExecutionIntent（执行层）是不同的流水线阶段，
    携带合规检查所需的市场状态字段。

    Attributes:
        symbol: 证券代码
        side: 方向（"buy" / "sell"）
        quantity: 数量（股/张）
        price: 预计成交价
        daily_volume: 标的日均成交量（用于参与率计算）
        is_halted: 是否停牌
        is_limit_up: 今日是否已涨停
        is_limit_down: 今日是否已跌停
        is_st: 是否 ST/*ST 标的（§P0-B: Universe 过滤）
        list_status: 上市状态 "L"=正常 "D"=退市 "P"=暂停上市
        asset_type: "equity" 或 "cb"（可转债）
        force_redeem_value: CB 转股价值（仅 CB 使用，可选）
    """
    symbol: str
    side: str
    quantity: float
    price: float
    daily_volume: float = 0.0
    is_halted: bool = False
    is_limit_up: bool = False
    is_limit_down: bool = False
    is_st: bool = False
    list_status: str = "L"
    asset_type: str = "equity"
    force_redeem_value: Optional[float] = None


# 向后兼容别名
OrderIntent = ComplianceOrderIntent


@dataclass
class ComplianceViolation:
    """单条合规违规记录。"""
    rule: str
    symbol: str
    detail: str
    severity: str = "error"   # "error" → 阻止，"warning" → 告警


@dataclass
class ComplianceResult:
    """合规检查整体结果。

    Attributes:
        passed: True = 全部通过（无 error 级违规）
        violations: 违规列表
        checked_at: 检查时间
        n_orders_checked: 检查订单数
        n_orders_blocked: 被阻止订单数
    """
    passed: bool
    violations: list[ComplianceViolation] = field(default_factory=list)
    checked_at: str = ""
    n_orders_checked: int = 0
    n_orders_blocked: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checked_at": self.checked_at,
            "n_orders_checked": self.n_orders_checked,
            "n_orders_blocked": self.n_orders_blocked,
            "violations": [
                {
                    "rule": v.rule,
                    "symbol": v.symbol,
                    "detail": v.detail,
                    "severity": v.severity,
                }
                for v in self.violations
            ],
        }


# ---------------------------------------------------------------------------
# ComplianceEngine
# ---------------------------------------------------------------------------

class ComplianceEngine:
    """组合层调仓合规检查引擎。

    在 PortfolioManager.rebalance() 执行前调用，
    拦截违反合规配置的订单并记录审计日志。

    Usage::

        engine = ComplianceEngine.from_config("configs/algo_trading_compliance.yml")
        orders = [OrderIntent("000001.SZ", "buy", 100, 15.0, daily_volume=50000)]
        result = engine.check_rebalance(orders)
        if not result.passed:
            logger.warning("合规检查未通过: %s", result.violations)
    """

    def __init__(
        self,
        max_participation_rate: float = _DEFAULT_MAX_PARTICIPATION,
        max_participation_rate_crowded: float = 0.05,
        max_cancel_rate: float = _DEFAULT_MAX_CANCEL_RATE,
        cb_lot_size: int = _DEFAULT_CB_LOT,
        equity_lot_size: int = _DEFAULT_EQUITY_LOT,
        treat_limit_up_as_untradeable: bool = True,
        treat_limit_down_as_untradeable: bool = True,
        force_redeem_max_exposure: float = 0.30,
        max_order_rate_per_second: float = 5.0,
        audit_log_dir: str = "logs/audit",
        enable_audit_log: bool = True,
    ) -> None:
        self._max_participation = max_participation_rate
        self._max_participation_crowded = max_participation_rate_crowded
        self._max_cancel_rate = max_cancel_rate
        self._max_order_rate_per_second = max_order_rate_per_second
        self._cb_lot = cb_lot_size
        self._equity_lot = equity_lot_size
        self._limit_up_untradeable = treat_limit_up_as_untradeable
        self._limit_down_untradeable = treat_limit_down_as_untradeable
        self._force_redeem_max = force_redeem_max_exposure
        self._audit_dir = Path(audit_log_dir)
        self._enable_audit = enable_audit_log

    @classmethod
    def from_config(cls, config_path: str | Path) -> "ComplianceEngine":
        """从 algo_trading_compliance.yml 加载配置创建引擎。"""
        import yaml
        path = Path(config_path)
        if not path.exists():
            logger.warning("Compliance config not found: %s, using defaults", path)
            return cls()
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        participation = cfg.get("participation", {})
        order_limits = cfg.get("order_limits", {})
        cb = cfg.get("convertible_bond", {})
        limit_price = cfg.get("limit_price", {})
        audit = cfg.get("audit", {})

        return cls(
            max_participation_rate=participation.get("max_participation_rate", _DEFAULT_MAX_PARTICIPATION),
            max_participation_rate_crowded=participation.get("crowding_high_participation_rate", 0.05),
            max_cancel_rate=order_limits.get("max_cancel_rate", _DEFAULT_MAX_CANCEL_RATE),
            max_order_rate_per_second=order_limits.get("max_order_rate_per_second", 5.0),
            cb_lot_size=cb.get("lot_size", _DEFAULT_CB_LOT),
            equity_lot_size=_DEFAULT_EQUITY_LOT,
            treat_limit_up_as_untradeable=limit_price.get("treat_limit_up_as_untradeable", True),
            treat_limit_down_as_untradeable=limit_price.get("treat_limit_down_as_untradeable", True),
            force_redeem_max_exposure=cb.get("force_redeem_max_exposure", 0.30),
            audit_log_dir=audit.get("audit_log_dir", "logs/audit"),
            enable_audit_log=audit.get("enable_audit_log", True),
        )

    def check_rebalance(
        self,
        orders: list[OrderIntent],
        portfolio_total: float = 0.0,
        is_crowded: bool = False,
    ) -> ComplianceResult:
        """对调仓订单列表执行合规检查。

        Args:
            orders: 调仓订单意图列表
            portfolio_total: 组合总市值（用于计算 CB 强赎暴露）
            is_crowded: 当前持仓是否处于拥挤状态（影响参与率上限）

        Returns:
            ComplianceResult（passed=True 表示无 error 级违规）
        """
        violations: list[ComplianceViolation] = []
        blocked_symbols: set[str] = set()

        max_part = self._max_participation_crowded if is_crowded else self._max_participation

        # CB 强赎暴露总计算
        cb_total_value = 0.0
        cb_force_redeem_value = 0.0

        for order in orders:
            sym = order.symbol

            # --- 规则0a: ST 标的不买入 (§P0-B Universe 过滤) ---
            if order.is_st and order.side == "buy":
                violations.append(ComplianceViolation(
                    rule="st_check",
                    symbol=sym,
                    detail=f"{sym} is ST/退市风险, buy blocked",
                    severity="error",
                ))
                blocked_symbols.add(sym)
                continue

            # --- 规则0b: 退市/暂停上市标的不交易 ---
            if order.list_status not in ("L", ""):
                violations.append(ComplianceViolation(
                    rule="delist_check",
                    symbol=sym,
                    detail=f"{sym} list_status={order.list_status}, order blocked",
                    severity="error",
                ))
                blocked_symbols.add(sym)
                continue

            # --- 规则1: 停牌不交易 ---
            if order.is_halted:
                violations.append(ComplianceViolation(
                    rule="halt_check",
                    symbol=sym,
                    detail=f"{sym} is halted, order blocked",
                    severity="error",
                ))
                blocked_symbols.add(sym)
                continue

            # --- 规则2: 涨跌停约束 ---
            if order.side == "buy" and order.is_limit_up and self._limit_up_untradeable:
                violations.append(ComplianceViolation(
                    rule="limit_up_buy",
                    symbol=sym,
                    detail=f"{sym} is limit_up, buy blocked",
                    severity="error",
                ))
                blocked_symbols.add(sym)
                continue

            if order.side == "sell" and order.is_limit_down and self._limit_down_untradeable:
                violations.append(ComplianceViolation(
                    rule="limit_down_sell",
                    symbol=sym,
                    detail=f"{sym} is limit_down, sell blocked",
                    severity="error",
                ))
                blocked_symbols.add(sym)
                continue

            # --- 规则3: 参与率约束 ---
            if order.daily_volume > 0:
                participation = order.quantity / order.daily_volume
                if participation > max_part:
                    violations.append(ComplianceViolation(
                        rule="participation_rate",
                        symbol=sym,
                        detail=(
                            f"{sym} participation={participation:.3f} > "
                            f"limit={max_part:.3f}"
                        ),
                        severity="warning" if participation < max_part * 2 else "error",
                    ))
                    if participation >= max_part * 2:
                        blocked_symbols.add(sym)

            # --- 规则4: 最小交易单位 ---
            lot = self._cb_lot if order.asset_type == "cb" else self._equity_lot
            if order.quantity > 0 and order.quantity % lot != 0:
                violations.append(ComplianceViolation(
                    rule="lot_size",
                    symbol=sym,
                    detail=f"{sym} qty={order.quantity} not a multiple of lot={lot}",
                    severity="warning",
                ))

            # --- 规则5: CB 强赎暴露 ---
            if order.asset_type == "cb" and order.force_redeem_value is not None:
                position_value = order.quantity * order.price
                cb_total_value += position_value
                if order.force_redeem_value >= 125.0:
                    cb_force_redeem_value += position_value

        # 强赎暴露检查（需要 portfolio_total > 0）
        if portfolio_total > 0 and cb_force_redeem_value > 0:
            exposure = cb_force_redeem_value / portfolio_total
            if exposure > self._force_redeem_max:
                violations.append(ComplianceViolation(
                    rule="force_redeem_exposure",
                    symbol="PORTFOLIO",
                    detail=(
                        f"CB force_redeem exposure={exposure:.2%} > "
                        f"limit={self._force_redeem_max:.2%}"
                    ),
                    severity="error",
                ))

        # 是否有 error 级违规
        error_violations = [v for v in violations if v.severity == "error"]
        passed = len(error_violations) == 0

        result = ComplianceResult(
            passed=passed,
            violations=violations,
            checked_at=datetime.now().isoformat(),
            n_orders_checked=len(orders),
            n_orders_blocked=len(blocked_symbols),
        )

        # 写审计日志
        if self._enable_audit and violations:
            self._write_audit_log(result)

        if not passed:
            logger.warning(
                "ComplianceEngine: FAILED (%d error violations, %d orders blocked)",
                len(error_violations), len(blocked_symbols),
            )
        else:
            logger.debug(
                "ComplianceEngine: PASSED (%d warnings)", len(violations)
            )

        return result

    def check_cancel_rate(
        self,
        n_cancels: int,
        n_total_orders: int,
    ) -> tuple[bool, float]:
        """检查当日撤单率是否超标。

        Returns:
            (passed, cancel_rate)
        """
        if n_total_orders == 0:
            return True, 0.0
        rate = n_cancels / n_total_orders
        return rate <= self._max_cancel_rate, rate

    def check_order_rate(
        self,
        n_orders_in_second: int,
    ) -> tuple[bool, float]:
        """检查每秒下单频率是否超标。

        Returns:
            (passed, rate)
        """
        return n_orders_in_second <= self._max_order_rate_per_second, float(n_orders_in_second)

    def _write_audit_log(self, result: ComplianceResult) -> None:
        """将合规检查结果写入审计日志文件（JSONL）。"""
        try:
            self._audit_dir.mkdir(parents=True, exist_ok=True)
            today = date.today().strftime("%Y%m%d")
            log_path = self._audit_dir / f"compliance_alert_{today}.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("ComplianceEngine: failed to write audit log: %s", exc)


# ---------------------------------------------------------------------------
# ComplianceGuard — 自动合规触发器（E-3）
# ---------------------------------------------------------------------------


class ComplianceGuard:
    """组合层自动合规触发器。

    在调仓流程中嵌入自动合规检查：
    1. 调仓前自动调用 ComplianceEngine.check_rebalance(orders)
    2. 合规检查未通过则拦截调仓 + 返回被阻止原因
    3. 审计日志自动写入 logs/audit/
    4. 可选集成 AlertSystem 发送告警

    使用示例::

        guard = ComplianceGuard(engine)
        approved_orders, blocked = guard.filter_orders(orders)
        # approved_orders 仅包含通过合规的订单
        # blocked 包含被拦截的订单及原因
    """

    def __init__(
        self,
        engine: ComplianceEngine | None = None,
        config_path: str | Path | None = None,
        alert_callback: Any | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        elif config_path is not None:
            self._engine = ComplianceEngine.from_config(config_path)
        else:
            self._engine = ComplianceEngine()
        self._alert_callback = alert_callback
        self._check_count = 0
        self._block_count = 0

    @property
    def check_count(self) -> int:
        """累计检查次数。"""
        return self._check_count

    @property
    def block_count(self) -> int:
        """累计拦截次数。"""
        return self._block_count

    def filter_orders(
        self,
        orders: list[OrderIntent],
        portfolio_total: float = 0.0,
        is_crowded: bool = False,
    ) -> tuple[list[OrderIntent], list[tuple[OrderIntent, str]]]:
        """过滤订单，返回合规订单和被拦截订单。

        Args:
            orders: 待检查的调仓订单
            portfolio_total: 组合总市值
            is_crowded: 是否处于拥挤状态

        Returns:
            (approved_orders, blocked_orders_with_reason)
        """
        self._check_count += 1
        result = self._engine.check_rebalance(
            orders, portfolio_total=portfolio_total, is_crowded=is_crowded,
        )

        if result.passed:
            return orders, []

        # 从 error 级违规中提取被阻止的标的
        blocked_symbols = {
            v.symbol for v in result.violations if v.severity == "error"
        }
        approved: list[OrderIntent] = []
        blocked: list[tuple[OrderIntent, str]] = []

        for order in orders:
            if order.symbol in blocked_symbols:
                reasons = [
                    v.detail
                    for v in result.violations
                    if v.symbol == order.symbol and v.severity == "error"
                ]
                reason_str = "; ".join(reasons) if reasons else "合规检查未通过"
                blocked.append((order, reason_str))
            else:
                approved.append(order)

        if blocked:
            self._block_count += 1
            logger.warning(
                "ComplianceGuard: 拦截 %d/%d 笔订单",
                len(blocked), len(orders),
            )
            # 触发告警回调
            if self._alert_callback is not None:
                try:
                    self._alert_callback(
                        "compliance_block",
                        {
                            "blocked_count": len(blocked),
                            "total_count": len(orders),
                            "blocked_symbols": [o.symbol for o, _ in blocked],
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception as exc:
                    logger.error("ComplianceGuard: alert callback failed: %s", exc)

        return approved, blocked
