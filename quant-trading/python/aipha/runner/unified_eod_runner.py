"""统一 EOD 交易引擎 — 回测/模拟/实盘共享同一执行流程。

核心设计：
- TradingGateway 是唯一分叉点（PaperGateway / QMTGateway）
- Account + PositionManager 域模型管理资金/持仓
- FillModel / FeeModel / SlippageModel / MarketRules 与回测引擎共用

日执行周期：
  1. fill_pending_orders()  — 以今日开盘价撮合昨日挂单
  2. update_nav()           — 持仓估值 + NAV 记录
  3. rebalance()            — 信号 → 目标仓位 → 订单 → 提交
  4. day_end()              — T+1 解锁 + 过期清理
  5. save_state()           — 状态持久化

§4.3: 回测-Paper-Live 一致性契约
§13.7: 共享 trading_ops 基础设施
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from aipha.domain.models import Bar, Side
from aipha.execution.gateway import (
    GatewayOrder,
    OrderStatus,
    TradingGateway,
    TradingMode,
)
from aipha.execution.paper_gateway import PaperGateway
from aipha.portfolio.account import Account
from aipha.portfolio.position_manager import PositionManager

logger = logging.getLogger(__name__)


@dataclass
class NavRecord:
    """单日 NAV 记录。"""
    trade_date: str
    nav: float
    cash: float
    positions_value: float
    n_positions: int


@dataclass
class DayResult:
    """单日执行结果。"""
    trade_date: str
    fills: int = 0
    orders_submitted: int = 0
    nav: float = 0.0
    errors: list[str] = field(default_factory=list)


class UnifiedEODRunner:
    """统一 EOD 执行引擎。

    Args:
        gateway: 交易网关（PaperGateway 或 QMTGateway）
        account: 资金账户
        position_mgr: 持仓管理器
        lot_size: 最小交易单位
        state_dir: 状态持久化目录（可选）
    """

    def __init__(
        self,
        *,
        gateway: TradingGateway,
        account: Account,
        position_mgr: PositionManager,
        lot_size: int = 10,
        state_dir: Path | None = None,
    ) -> None:
        self._gw = gateway
        self._account = account
        self._position_mgr = position_mgr
        self._lot_size = lot_size
        self._state_dir = state_dir
        self._nav_history: list[NavRecord] = []
        self._order_counter = 0

    # -- 主流程 ---------------------------------------------------------------

    def run_day(
        self,
        trade_date: date,
        bars: dict[str, Bar],
        target_symbols: list[str] | None = None,
        target_weights: dict[str, float] | None = None,
    ) -> DayResult:
        """执行单日交易周期。"""
        result = DayResult(trade_date=trade_date.isoformat())

        if not self._gw.is_connected:
            self._gw.connect()

        # Phase A: 撮合昨日挂单
        result.fills = self.fill_pending_orders(bars)

        # 提取价格
        prices = {sym: bar.close for sym, bar in bars.items()}

        # 更新网关最新价格（用于买入资金估算 + 持仓市值）
        if isinstance(self._gw, PaperGateway):
            self._gw._latest_prices.update(prices)

        # Phase B: 更新 NAV
        result.nav = self.update_nav(trade_date, prices)

        # Phase C: 调仓
        if target_symbols is not None:
            result.orders_submitted = self.rebalance(
                trade_date, target_symbols, prices, target_weights,
            )

        # Phase D: 日终
        self.day_end(trade_date)

        return result

    # -- Phase A: 填充挂单 ---------------------------------------------------

    def fill_pending_orders(self, bars: dict[str, Bar]) -> int:
        """以今日行情撮合昨日挂单（仅 PaperGateway）。

        Returns:
            成交笔数
        """
        if not isinstance(self._gw, PaperGateway):
            return 0
        results = self._gw.fill_pending(bars)
        filled = sum(1 for r in results if r.fill is not None)
        logger.info("fill_pending: %d/%d orders filled", filled, len(results))
        return filled

    # -- Phase B: NAV --------------------------------------------------------

    def update_nav(self, trade_date: date, prices: dict[str, float]) -> float:
        """更新 NAV 并记录历史。"""
        pos_value = self._position_mgr.get_total_market_value(prices)
        nav = float(self._account.calculate_nav(Decimal(str(pos_value))))
        n_pos = len(self._position_mgr.positions)
        self._nav_history.append(NavRecord(
            trade_date=trade_date.isoformat(),
            nav=nav,
            cash=float(self._account.cash),
            positions_value=pos_value,
            n_positions=n_pos,
        ))
        return nav

    # -- Phase C: 调仓 -------------------------------------------------------

    def rebalance(
        self,
        trade_date: date,
        target_symbols: list[str],
        prices: dict[str, float],
        target_weights: dict[str, float] | None = None,
    ) -> int:
        """根据目标仓位生成并提交订单。

        Returns:
            提交的订单数
        """
        if not target_symbols and not self._position_mgr.positions:
            return 0
        target_symbols = target_symbols or []

        # 计算当前 NAV，考虑买入冻结缓冲（§13.2 与 BUY_FREEZE_BUFFER 对齐）
        from aipha.execution.trading_ops import BUY_FREEZE_BUFFER
        pos_value = self._position_mgr.get_total_market_value(prices)
        nav = float(self._account.calculate_nav(Decimal(str(pos_value))))
        investable = nav / BUY_FREEZE_BUFFER  # 确保 investable × buffer ≤ nav

        # 目标权重（默认等权）
        if target_weights is None:
            if target_symbols:
                w = 1.0 / len(target_symbols)
                target_weights = {s: w for s in target_symbols}
            else:
                target_weights = {}

        current_holdings = self._position_mgr.get_positions_dict()
        current_set = set(current_holdings.keys())
        target_set = set(target_symbols)

        orders_submitted = 0

        # 先生成卖出订单（释放资金）
        for sym in current_set - target_set:
            qty = self._position_mgr.get_available_to_sell(sym)
            if qty <= 0:
                continue
            oid = self._next_order_id()
            order = GatewayOrder(
                order_id=oid, symbol=sym, side="SELL",
                qty=qty, order_type="MARKET",
            )
            try:
                self._gw.submit_order(order)
                orders_submitted += 1
            except Exception as e:
                logger.warning("Sell order failed %s: %s", sym, e)

        # 买入订单
        for sym in target_set:
            target_value = investable * target_weights.get(sym, 0)
            current_qty = current_holdings.get(sym, 0)
            price = prices.get(sym)
            if not price or price <= 0:
                continue
            current_value = current_qty * price
            delta_value = target_value - current_value
            if delta_value < price * self._lot_size:
                continue  # 差额不足一手
            buy_qty = int(delta_value / price)
            buy_qty = (buy_qty // self._lot_size) * self._lot_size
            if buy_qty <= 0:
                continue
            oid = self._next_order_id()
            order = GatewayOrder(
                order_id=oid, symbol=sym, side="BUY",
                qty=buy_qty, order_type="MARKET",
            )
            try:
                self._gw.submit_order(order)
                orders_submitted += 1
            except Exception as e:
                logger.warning("Buy order failed %s: %s", sym, e)

        return orders_submitted

    # -- Phase D: 日终 -------------------------------------------------------

    def day_end(self, trade_date: date) -> None:
        """日终处理。"""
        if isinstance(self._gw, PaperGateway):
            self._gw.day_end(trade_date)

    # -- 状态持久化 -----------------------------------------------------------

    def save_state(self, path: Path | None = None) -> None:
        """序列化状态到 JSON。"""
        path = path or (self._state_dir / "state.json" if self._state_dir else None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)

        positions = []
        for sym, pos in self._position_mgr.positions.items():
            positions.append({
                "ts_code": sym,
                "shares": pos.total_qty,
                "avg_cost": float(pos.avg_cost),
                "today_bought_qty": pos.today_bought_qty,
                "frozen_qty": pos.frozen_qty,
                "t_plus_n": pos.t_plus_n,
            })

        state = {
            "cash": float(self._account.cash),
            "frozen_cash": float(self._account.frozen_cash),
            "initial_cash": float(self._account.initial_cash),
            "positions": positions,
            "nav_history": [
                {"trade_date": r.trade_date, "nav": r.nav,
                 "cash": r.cash, "positions_value": r.positions_value,
                 "n_positions": r.n_positions}
                for r in self._nav_history[-500:]
            ],
            "order_counter": self._order_counter,
        }

        # 原子写入：先写 .tmp，再 rename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

        # R167 A3: 同步写出独立 nav_history.json (governance/scripts 消费方依赖)
        # 消费方期望读 data/paper_state/{profile}/nav_history.json
        try:
            profile_name = path.stem.removesuffix("_state")
            profile_dir = path.parent / profile_name
            profile_dir.mkdir(parents=True, exist_ok=True)
            nav_path = profile_dir / "nav_history.json"
            nav_records = state["nav_history"]
            nav_tmp = nav_path.with_suffix(".tmp")
            nav_tmp.write_text(
                json.dumps(nav_records, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            nav_tmp.replace(nav_path)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to write nav_history.json: %s", exc,
            )

    def load_state(self, path: Path) -> None:
        """从 JSON 恢复状态。"""
        data = json.loads(path.read_text(encoding="utf-8"))

        # 恢复 Account
        self._account.cash = Decimal(str(data["cash"]))
        self._account.frozen_cash = Decimal(str(data.get("frozen_cash", 0)))

        # 恢复 PositionManager
        from aipha.portfolio.position import Position
        self._position_mgr.positions.clear()
        for p in data.get("positions", []):
            pos = Position(
                symbol=p["ts_code"],
                total_qty=p["shares"],
                avg_cost=Decimal(str(p["avg_cost"])),
                today_bought_qty=p.get("today_bought_qty", 0),
                frozen_qty=p.get("frozen_qty", 0),
                t_plus_n=p.get("t_plus_n", self._position_mgr.default_t_plus_n),
            )
            self._position_mgr.positions[pos.symbol] = pos

        # 恢复 NAV 历史
        self._nav_history = [
            NavRecord(**r) for r in data.get("nav_history", [])
        ]
        self._order_counter = data.get("order_counter", 0)

    # -- 内部工具 -------------------------------------------------------------

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"UNI-{self._order_counter:06d}"
