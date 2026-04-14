"""Tests for ComplianceEngine — E-3.

T01  默认构造器：属性边界正确
T02  from_config：加载 YAML（跳过若无文件）
T03  停牌订单被拦截，passed=False
T04  涨停买入被拦截
T05  跌停卖出被拦截
T06  涨停卖出不拦截
T07  跌停买入不拦截
T08  参与率超限 → error
T09  参与率轻微超限 → warning 但 passed=True
T10  拥挤时参与率上限降至 5%
T11  CB lot 不整除 → warning 但 passed=True
T12  Equity lot 不整除 → warning 但 passed=True
T13  CB 强赎暴露超限 → error
T14  合规全通过时 violations 为空
T15  check_cancel_rate 超限 → (False, rate)
T16  check_cancel_rate 合规 → (True, rate)
T17  to_dict 结构正确
"""

from __future__ import annotations

import pytest
from aipha.portfolio.compliance_engine import (
    ComplianceEngine,
    ComplianceViolation,
    OrderIntent,
    ComplianceResult,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_order(
    symbol: str = "000001.SZ",
    side: str = "buy",
    quantity: int = 100,
    price: float = 10.0,
    daily_volume: float = 100_000.0,
    is_halted: bool = False,
    is_limit_up: bool = False,
    is_limit_down: bool = False,
    asset_type: str = "equity",
    force_redeem_value: float | None = None,
) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        daily_volume=daily_volume,
        is_halted=is_halted,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        asset_type=asset_type,
        force_redeem_value=force_redeem_value,
    )


def _engine() -> ComplianceEngine:
    return ComplianceEngine()


# ---------------------------------------------------------------------------
# T01 默认构造
# ---------------------------------------------------------------------------
def test_T01_default_constructor():
    eng = ComplianceEngine()
    assert eng._max_participation == 0.10
    assert eng._max_cancel_rate == 0.80
    assert eng._cb_lot == 10
    assert eng._equity_lot == 100
    assert eng._limit_up_untradeable is True
    assert eng._limit_down_untradeable is True
    assert eng._force_redeem_max == 0.30


# ---------------------------------------------------------------------------
# T02 from_config 加载
# ---------------------------------------------------------------------------
def test_T02_from_config(tmp_path):
    cfg = tmp_path / "compliance.yml"
    cfg.write_text(
        "order_limits:\n  max_cancel_rate: 0.75\n"
        "participation:\n  max_participation_rate: 0.08\n  crowding_high_participation_rate: 0.04\n"
        "convertible_bond:\n  lot_size: 10\n  force_redeem_max_exposure: 0.25\n"
        "limit_price:\n  treat_limit_up_as_untradeable: true\n  treat_limit_down_as_untradeable: true\n"
        "audit:\n  enable_audit_log: false\n",
        encoding="utf-8",
    )
    eng = ComplianceEngine.from_config(cfg)
    assert eng._max_cancel_rate == pytest.approx(0.75)
    assert eng._max_participation == pytest.approx(0.08)
    assert eng._force_redeem_max == pytest.approx(0.25)
    assert eng._enable_audit is False


# ---------------------------------------------------------------------------
# T03 停牌被拦截
# ---------------------------------------------------------------------------
def test_T03_halted_order_blocked():
    eng = _engine()
    orders = [_make_order(is_halted=True)]
    result = eng.check_rebalance(orders)
    assert result.passed is False
    assert result.n_orders_blocked == 1
    rules = [v.rule for v in result.violations]
    assert "halt_check" in rules


# ---------------------------------------------------------------------------
# T04 涨停买入被拦截
# ---------------------------------------------------------------------------
def test_T04_limit_up_buy_blocked():
    eng = _engine()
    orders = [_make_order(side="buy", is_limit_up=True)]
    result = eng.check_rebalance(orders)
    assert result.passed is False
    assert any(v.rule == "limit_up_buy" for v in result.violations)


# ---------------------------------------------------------------------------
# T05 跌停卖出被拦截
# ---------------------------------------------------------------------------
def test_T05_limit_down_sell_blocked():
    eng = _engine()
    orders = [_make_order(side="sell", is_limit_down=True)]
    result = eng.check_rebalance(orders)
    assert result.passed is False
    assert any(v.rule == "limit_down_sell" for v in result.violations)


# ---------------------------------------------------------------------------
# T06 涨停卖出不拦截
# ---------------------------------------------------------------------------
def test_T06_limit_up_sell_allowed():
    eng = _engine()
    orders = [_make_order(side="sell", is_limit_up=True)]
    result = eng.check_rebalance(orders)
    assert result.passed is True
    assert result.n_orders_blocked == 0


# ---------------------------------------------------------------------------
# T07 跌停买入不拦截
# ---------------------------------------------------------------------------
def test_T07_limit_down_buy_allowed():
    eng = _engine()
    orders = [_make_order(side="buy", is_limit_down=True)]
    result = eng.check_rebalance(orders)
    assert result.passed is True


# ---------------------------------------------------------------------------
# T08 参与率严重超限 → error
# ---------------------------------------------------------------------------
def test_T08_participation_rate_error():
    eng = _engine()
    # qty=50000, vol=100000 → participation=0.50 > 0.20 (2×0.10) → error
    orders = [_make_order(quantity=50_000, daily_volume=100_000)]
    result = eng.check_rebalance(orders)
    assert result.passed is False
    viol = [v for v in result.violations if v.rule == "participation_rate"]
    assert len(viol) == 1
    assert viol[0].severity == "error"


# ---------------------------------------------------------------------------
# T09 参与率轻微超限 → warning / passed
# ---------------------------------------------------------------------------
def test_T09_participation_rate_warning_passed():
    eng = _engine()
    # qty=1200, vol=10000 → participation=0.12, 0.10 < 0.12 < 0.20
    orders = [_make_order(quantity=1_200, daily_volume=10_000)]
    result = eng.check_rebalance(orders)
    # warning only → passed=True
    assert result.passed is True
    viol = [v for v in result.violations if v.rule == "participation_rate"]
    assert len(viol) == 1
    assert viol[0].severity == "warning"


# ---------------------------------------------------------------------------
# T10 拥挤模式参与率降至 5%
# ---------------------------------------------------------------------------
def test_T10_crowded_participation():
    eng = _engine()
    # qty=600, vol=10000 → participation=0.06 > 0.05 → triggers warning
    orders = [_make_order(quantity=600, daily_volume=10_000)]
    result = eng.check_rebalance(orders, is_crowded=True)
    assert any(v.rule == "participation_rate" for v in result.violations)


# ---------------------------------------------------------------------------
# T11 CB lot 不整除 → warning
# ---------------------------------------------------------------------------
def test_T11_cb_lot_warning():
    eng = _engine()
    orders = [_make_order(symbol="128001.SZ", quantity=15, asset_type="cb")]
    result = eng.check_rebalance(orders)
    assert result.passed is True  # warning only
    viol = [v for v in result.violations if v.rule == "lot_size"]
    assert len(viol) == 1
    assert viol[0].severity == "warning"


# ---------------------------------------------------------------------------
# T12 Equity lot 不整除 → warning
# ---------------------------------------------------------------------------
def test_T12_equity_lot_warning():
    eng = _engine()
    orders = [_make_order(quantity=150)]  # 150 % 100 = 50 → warning
    result = eng.check_rebalance(orders)
    assert result.passed is True
    viol = [v for v in result.violations if v.rule == "lot_size"]
    assert viol[0].severity == "warning"


# ---------------------------------------------------------------------------
# T13 CB 强赎暴露超限
# ---------------------------------------------------------------------------
def test_T13_force_redeem_exposure():
    eng = _engine()
    # CB price=110, qty=1000 → value=110000; portfolio_total=200000
    # exposure=110000/200000=0.55 > 0.30 → error
    orders = [
        _make_order(
            symbol="128001.SZ",
            quantity=1_000,
            price=110.0,
            daily_volume=500_000,
            asset_type="cb",
            force_redeem_value=130.0,  # >=125 → counts
        )
    ]
    result = eng.check_rebalance(orders, portfolio_total=200_000)
    assert any(v.rule == "force_redeem_exposure" for v in result.violations)


# ---------------------------------------------------------------------------
# T14 全通过 → violations 为空，passed=True
# ---------------------------------------------------------------------------
def test_T14_fully_compliant():
    eng = _engine()
    orders = [_make_order(quantity=100, daily_volume=100_000)]
    result = eng.check_rebalance(orders)
    assert result.passed is True
    assert result.violations == []


# ---------------------------------------------------------------------------
# T15 撤单率超限
# ---------------------------------------------------------------------------
def test_T15_cancel_rate_exceeded():
    eng = _engine()
    passed, rate = eng.check_cancel_rate(n_cancels=90, n_total_orders=100)
    assert passed is False
    assert rate == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# T16 撤单率合规
# ---------------------------------------------------------------------------
def test_T16_cancel_rate_ok():
    eng = _engine()
    passed, rate = eng.check_cancel_rate(n_cancels=50, n_total_orders=100)
    assert passed is True
    assert rate == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# T17 to_dict 结构
# ---------------------------------------------------------------------------
def test_T17_to_dict_structure():
    eng = _engine()
    orders = [_make_order(is_halted=True)]
    result = eng.check_rebalance(orders)
    d = result.to_dict()
    assert "passed" in d
    assert "violations" in d
    assert isinstance(d["violations"], list)
    assert d["violations"][0]["rule"] == "halt_check"


# ---------------------------------------------------------------------------
# ComplianceGuard 测试 (E-3 自动合规触发)
# ---------------------------------------------------------------------------
from aipha.portfolio.compliance_engine import ComplianceGuard


def test_T18_guard_pass_through():
    """合规订单全部通过。"""
    guard = ComplianceGuard(engine=_engine())
    orders = [_make_order()]
    approved, blocked = guard.filter_orders(orders)
    assert len(approved) == 1
    assert len(blocked) == 0
    assert guard.check_count == 1
    assert guard.block_count == 0


def test_T19_guard_blocks_halted():
    """停牌订单被自动拦截。"""
    guard = ComplianceGuard(engine=_engine())
    orders = [_make_order(is_halted=True)]
    approved, blocked = guard.filter_orders(orders)
    assert len(approved) == 0
    assert len(blocked) == 1
    assert "halted" in blocked[0][1].lower()
    assert guard.block_count == 1


def test_T20_guard_mixed_orders():
    """混合订单：合规的通过，违规的拦截。"""
    guard = ComplianceGuard(engine=_engine())
    orders = [
        _make_order(symbol="000001.SZ"),
        _make_order(symbol="000002.SZ", is_halted=True),
    ]
    approved, blocked = guard.filter_orders(orders)
    assert len(approved) == 1
    assert approved[0].symbol == "000001.SZ"
    assert len(blocked) == 1
    assert blocked[0][0].symbol == "000002.SZ"


def test_T21_guard_alert_callback():
    """告警回调正确触发。"""
    alerts = []
    guard = ComplianceGuard(
        engine=_engine(),
        alert_callback=lambda kind, data: alerts.append((kind, data)),
    )
    orders = [_make_order(is_halted=True)]
    guard.filter_orders(orders)
    assert len(alerts) == 1
    assert alerts[0][0] == "compliance_block"
    assert alerts[0][1]["blocked_count"] == 1


def test_T22_guard_from_config_path(tmp_path):
    """从配置路径创建 guard。"""
    cfg = tmp_path / "compliance.yml"
    cfg.write_text("participation:\n  max_participation_rate: 0.08\n")
    guard = ComplianceGuard(config_path=str(cfg))
    orders = [_make_order()]
    approved, _ = guard.filter_orders(orders)
    assert len(approved) == 1
