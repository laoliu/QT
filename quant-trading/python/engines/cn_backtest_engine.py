"""
Chinese A-share backtesting engine with capital management, position tracking,
dividend processing, and performance reporting.

Migrated and modernized from jtstock:
  - stock_tools/stock_trade.py → TradeExecutor, TradeSystem
  - stock_tools/capital_manager.py → CapitalManager, allocation policies
  - stock_tools/position_manager.py → PositionManager, Position
  - stock_tools/report_manager.py → ReportManager

Architecture:
  TradeSystem orchestrates the full backtest loop:
    for each trade_day:
      → process_dividend()          (cash dividends, bonus shares, rights issue)
      → strategy.select()           (generate buy/sell signals)
      → TradeExecutor.buy/sell()    (execute with limit up/down checks)
      → ReportManager.record()      (track performance metrics)
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from .helpers import calc_yield, date_to_str


# ── Position ─────────────────────────────────────────────────────


@dataclass
class Position:
    """A single stock position."""

    number: int  # shares held
    price: float  # average cost price
    first_datetime: int  # date entered
    last_datetime: int  # date of last add
    cost: float = 0.0
    day_counter: int = 1  # days held

    def __post_init__(self):
        self.cost = self.number * self.price


# ── Position Manager ─────────────────────────────────────────────


class PositionManager:
    """Track and manage stock positions."""

    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._contract_multi: dict[str, int] = {}  # for futures

    def add_position(
        self, stock_id: str, price: float, number: int, dt: int, multi: int = 1
    ) -> None:
        if stock_id in self._positions:
            pos = self._positions[stock_id]
            total = pos.number * pos.price + price * number
            pos.number += number
            pos.price = total / pos.number if pos.number > 0 else price
            pos.last_datetime = dt
        else:
            self._positions[stock_id] = Position(number, price, dt, dt)
        self._contract_multi[stock_id] = multi

    def remove_position(
        self, stock_id: str, price: float, sell_number: int
    ) -> tuple[float, int, float]:
        """Remove shares from position.

        Returns: (total_capital_sold, actual_shares_sold, total_profit)
        """
        pos = self._positions.get(stock_id)
        if not pos:
            return 0.0, 0, 0.0

        actual = min(sell_number, pos.number)
        multi = self._contract_multi.get(stock_id, 1)

        total_capital = price * actual * multi
        total_profit = actual * (price - pos.price) * multi

        pos.number -= actual
        if pos.number <= 0:
            del self._positions[stock_id]
            self._contract_multi.pop(stock_id, None)

        return total_capital, actual, total_profit

    def get_position(self, stock_id: str) -> Optional[Position]:
        return self._positions.get(stock_id)

    def get_all_ids(self) -> list[str]:
        return list(self._positions.keys())

    def get_all_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def is_exist(self, stock_id: str) -> bool:
        return stock_id in self._positions

    def count(self) -> int:
        return len(self._positions)

    def get_position_value(self, stock_id: str, price: float) -> float:
        pos = self._positions.get(stock_id)
        if not pos:
            return 0.0
        multi = self._contract_multi.get(stock_id, 1)
        return pos.number * price * multi

    def calc_profit(self, stock_id: str, price: float) -> float:
        pos = self._positions.get(stock_id)
        if not pos:
            return 0.0
        multi = self._contract_multi.get(stock_id, 1)
        return pos.number * (price - pos.price) * multi

    def increase_day_counter(self) -> None:
        for pos in self._positions.values():
            pos.day_counter += 1

    def get_total_cost(self) -> float:
        total = 0.0
        for sid, pos in self._positions.items():
            multi = self._contract_multi.get(sid, 1)
            total += pos.number * pos.price * multi
        return total


# ── Capital Manager ──────────────────────────────────────────────

# Capital allocation policies
ALLOCATION_POLICIES = {
    "total_allocated": "total",     # divide equally among current open positions
    "demand_allocated": "demand",   # divide by max allowed stocks
    "fixed_allocated": "fixed",     # fixed amount per trade
}


# Time-varying stamp tax schedule for A-shares (卖出印花税)
# 2023-08-28: reduced from 0.1% to 0.05%
_STAMP_TAX_SCHEDULE: list[tuple[int, float]] = [
    (20230828, 0.0005),  # from 2023-08-28: 万5
    (0, 0.001),          # before: 千1
]


def get_stamp_tax_rate(dt: int, time_varying: bool = True) -> float:
    """Get the applicable stamp tax rate for a given date.

    Args:
        dt: Trade date as YYYYMMDD int.
        time_varying: If True, use historical schedule; else use current rate.
    """
    if not time_varying:
        return 0.0005  # current rate
    for cutoff, rate in _STAMP_TAX_SCHEDULE:
        if dt >= cutoff:
            return rate
    return 0.001  # fallback


class CapitalManager:
    """Manage cash, fees, and capital allocation.

    Supports:
      - Time-varying stamp tax (2023-08 reduction)
      - Minimum commission (¥5 per trade)
      - Maximum participation rate (% of daily volume)
      - Transfer fee (过户费)
    """

    def __init__(
        self,
        init_money: float,
        policy: str = "fixed",
        commission: float = 0.0002,
        stamp_tax: float = 0.001,
        simulate_lots: bool = True,
        min_commission: float = 5.0,
        transfer_fee: float = 0.00001,
        time_varying_tax: bool = False,
        max_participation_rate: float = 0.0,
    ):
        self.init_money = init_money
        self.available_money = init_money
        self.policy = policy
        self.commission = commission
        self.stamp_tax = stamp_tax
        self.simulate_lots = simulate_lots  # trade in lots of 100
        self.min_commission = min_commission
        self.transfer_fee = transfer_fee
        self.time_varying_tax = time_varying_tax
        self.max_participation_rate = max_participation_rate

    def calc_per_capital(self, policy_param: dict) -> float:
        """Calculate the per-stock allocation based on policy."""
        money = self.available_money
        if money <= 0:
            return 0.0

        if self.policy == "total":
            count = policy_param.get("current_count", 1)
            return money / max(count, 1)
        elif self.policy == "demand":
            max_stocks = policy_param.get("max_stocks", 20)
            return money / max(max_stocks, 1)
        elif self.policy == "fixed":
            fixed = policy_param.get("fixed_amount", money)
            return min(fixed, money)
        return money

    def buy_one_stock(
        self, price: float, policy_param: dict,
        volume: float = 0.0, dt: int = 0,
    ) -> tuple[int, float]:
        """Calculate shares to buy and deduct from available money.

        Args:
            price: Execution price per share.
            policy_param: Capital allocation parameters.
            volume: Daily volume (shares) for participation rate check.
            dt: Trade date (YYYYMMDD) for time-varying fees.

        Returns: (shares_bought, fee_cost)
        """
        per_capital = self.calc_per_capital(policy_param)
        number = int(per_capital / (1 + self.commission) / price)

        if self.simulate_lots:
            if number < 100:
                return 0, 0.0
            number = (number // 100) * 100

        # Participation rate cap: don't exceed X% of daily volume
        if self.max_participation_rate > 0 and volume > 0:
            max_shares = int(volume * self.max_participation_rate)
            if self.simulate_lots:
                max_shares = (max_shares // 100) * 100
            number = min(number, max_shares)
            if number <= 0:
                return 0, 0.0

        cost = number * price
        comm = max(cost * self.commission, self.min_commission)
        xfer = cost * self.transfer_fee
        fee = comm + xfer

        self.available_money -= cost + fee
        return number, fee

    def sell_one_stock(
        self, total_capital: float, dt: int = 0,
        volume: float = 0.0, sell_shares: int = 0,
    ) -> float:
        """Process a sell transaction. Returns total fee.

        Args:
            total_capital: Total proceeds = shares × price.
            dt: Trade date for time-varying stamp tax.
            volume: Daily volume for participation rate check.
            sell_shares: Number of shares being sold.
        """
        # Time-varying stamp tax
        tax_rate = get_stamp_tax_rate(dt, self.time_varying_tax) if dt else self.stamp_tax
        comm = max(total_capital * self.commission, self.min_commission)
        tax = total_capital * tax_rate
        xfer = total_capital * self.transfer_fee
        fee = comm + tax + xfer
        self.available_money += total_capital - fee
        return fee

    def add_money(self, money: float) -> None:
        self.available_money += money


# ── Report Manager ───────────────────────────────────────────────


class ReportManager:
    """Track performance metrics for a backtest.

    Computes:
      - Daily/monthly/yearly net values
      - Max drawdown and drawdown duration
      - Win rate, profit-loss ratio
      - CAGR, Sharpe ratio
      - Benchmark comparison
    """

    def __init__(self, init_money: float):
        self.init_money = init_money
        self.equity_curve: list[dict] = []
        self.max_net_value: float = 0.0
        self.max_drawdown: float = 0.0
        self.max_drawdown_duration: int = 0

        self.profit_count: int = 0
        self.loss_count: int = 0
        self.total_profit: float = 0.0
        self.total_loss: float = 0.0
        self.total_fees: float = 0.0
        self.total_dividend: float = 0.0

        self._drawdown_start: int = 0
        self._cur_drawdown_days: int = 0
        self._begin_time: Optional[int] = None
        self._end_time: Optional[int] = None
        self._trades: list[dict] = []
        self._monthly_returns: list[dict] = []
        self._yearly_returns: list[dict] = []
        self._last_month_value: float = init_money
        self._last_year_value: float = init_money
        self._last_month: int = 0
        self._last_year: int = 0

    def record_trade(self, dt: int, profit: float, fee: float) -> None:
        if profit > 0:
            self.profit_count += 1
            self.total_profit += profit
        elif profit < 0:
            self.loss_count += 1
            self.total_loss += profit  # negative
        self.total_fees += fee

    def record_dividend(self, dt: int, amount: float) -> None:
        self.total_dividend += amount

    def record_daily(self, dt: int, net_value: float) -> None:
        """Record daily portfolio value and update metrics."""
        if not self._begin_time:
            self._begin_time = dt
        self._end_time = dt

        if net_value > self.max_net_value:
            self.max_net_value = net_value
            self._drawdown_start = dt
            self._cur_drawdown_days = 0

        drawdown = (self.max_net_value - net_value) / self.max_net_value if self.max_net_value > 0 else 0
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

        self._cur_drawdown_days += 1
        if self._cur_drawdown_days > self.max_drawdown_duration:
            self.max_drawdown_duration = self._cur_drawdown_days

        self.equity_curve.append({
            "date": date_to_str(dt),
            "value": round(net_value, 2),
            "drawdown": round(drawdown, 4),
        })

        # Monthly return tracking
        month = dt // 100
        if self._last_month and month != self._last_month:
            monthly_ret = (net_value / self._last_month_value - 1) if self._last_month_value > 0 else 0
            self._monthly_returns.append({
                "month": f"{self._last_month // 100}-{self._last_month % 100:02d}",
                "return": round(monthly_ret, 4),
            })
            self._last_month_value = net_value
        self._last_month = month

        # Yearly return tracking
        year = dt // 10000
        if self._last_year and year != self._last_year:
            yearly_ret = (net_value / self._last_year_value - 1) if self._last_year_value > 0 else 0
            self._yearly_returns.append({
                "year": str(self._last_year),
                "return": round(yearly_ret, 4),
            })
            self._last_year_value = net_value
        self._last_year = year

    def get_summary(self, final_value: float) -> dict:
        """Generate the full backtest summary report."""
        total_trades = self.profit_count + self.loss_count
        win_rate = self.profit_count / total_trades if total_trades > 0 else 0

        avg_profit = self.total_profit / self.profit_count if self.profit_count > 0 else 0
        avg_loss = abs(self.total_loss / self.loss_count) if self.loss_count > 0 else 0
        profit_factor = avg_profit / avg_loss if avg_loss > 0 else 0

        # CAGR
        years = self._calc_years()
        annualized_return = calc_yield(final_value, self.init_money, years) if years > 0 else 0

        # Sharpe ratio (assuming risk-free rate of 3%)
        daily_returns = self._calc_daily_returns()
        sharpe = self._calc_sharpe(daily_returns, risk_free_annual=0.03)

        # Total return
        total_return = (final_value / self.init_money - 1) if self.init_money > 0 else 0

        return {
            "initialCash": self.init_money,
            "finalValue": round(final_value, 2),
            "totalReturn": round(total_return, 4),
            "annualizedReturn": round(annualized_return, 4),
            "maxDrawdown": round(self.max_drawdown, 4),
            "maxDrawdownDuration": self.max_drawdown_duration,
            "sharpeRatio": round(sharpe, 4),
            "winRate": round(win_rate, 4),
            "profitFactor": round(profit_factor, 4),
            "totalTrades": total_trades,
            "profitTrades": self.profit_count,
            "lossTrades": self.loss_count,
            "totalProfit": round(self.total_profit, 2),
            "totalLoss": round(self.total_loss, 2),
            "totalFees": round(self.total_fees, 2),
            "totalDividend": round(self.total_dividend, 2),
            "equityCurve": self.equity_curve,
            "monthlyReturns": self._monthly_returns,
            "yearlyReturns": self._yearly_returns,
        }

    def _calc_years(self) -> float:
        if not self._begin_time or not self._end_time:
            return 0.0
        begin_month = (self._begin_time // 10000) * 12 + (self._begin_time % 10000) // 100
        end_month = (self._end_time // 10000) * 12 + (self._end_time % 10000) // 100
        return max((end_month - begin_month + 1) / 12.0, 0.01)

    def _calc_daily_returns(self) -> list[float]:
        returns = []
        for i in range(1, len(self.equity_curve)):
            prev = self.equity_curve[i - 1]["value"]
            cur = self.equity_curve[i]["value"]
            if prev > 0:
                returns.append(cur / prev - 1)
        return returns

    @staticmethod
    def _calc_sharpe(daily_returns: list[float], risk_free_annual: float = 0.03) -> float:
        if len(daily_returns) < 2:
            return 0.0
        rf_daily = risk_free_annual / 252
        excess = [r - rf_daily for r in daily_returns]
        mean_excess = sum(excess) / len(excess)
        variance = sum((x - mean_excess) ** 2 for x in excess) / len(excess)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return 0.0
        return (mean_excess / std_dev) * math.sqrt(252)


# ── Trade Executor ───────────────────────────────────────────────

MAX_POSITION_NUMBER = 9999999999


class TradeExecutor:
    """Execute buy/sell trades with limit up/down detection and dividend processing.

    Migrated from jtstock/stock_tools/stock_trade.py.
    """

    def __init__(
        self,
        capital_mgr: CapitalManager,
        position_mgr: PositionManager,
        report_mgr: ReportManager,
        max_stocks: int = 20,
    ):
        self.capital_mgr = capital_mgr
        self.position_mgr = position_mgr
        self.report_mgr = report_mgr
        self.max_stocks = max_stocks
        self._trade_count = 0
        self._per_stock_profit: dict[str, float] = {}
        self._last_day: int = 0

    def set_last_day(self, day: int) -> None:
        self._last_day = day

    def buy_one(
        self,
        stock_id: str,
        dt: int,
        policy_param: dict,
        price: float,
        bars: Optional[dict] = None,
    ) -> Optional[dict]:
        """Buy a single stock.

        Args:
          bars: { "open", "high", "low", "close", "prev_close" } for limit checks.
        """
        # Check if max positions reached (new position only)
        if not self.position_mgr.is_exist(stock_id) and self.position_mgr.count() >= self.max_stocks:
            return None
        if price <= 0:
            return None

        # Limit-up check (涨停检测)
        if bars:
            prev_c = bars.get("prev_close", 0)
            c = bars.get("close", price)
            o = bars.get("open", price)
            h = bars.get("high", price)
            low = bars.get("low", price)
            if prev_c and prev_c > 0:
                if c / prev_c >= 1.097 and c == o and c == h and c == low:
                    return None  # 一字涨停，无法买入

        number, fee = self.capital_mgr.buy_one_stock(price, policy_param)
        if number == 0:
            return None

        self.position_mgr.add_position(stock_id, price, number, dt)
        self.report_mgr.record_trade(dt, 0, fee)

        self._per_stock_profit.setdefault(stock_id, 0.0)
        self._per_stock_profit[stock_id] -= fee
        self._trade_count += 1

        return {"id": stock_id, "price": price, "number": number, "datetime": dt}

    def sell_one(
        self,
        stock_id: str,
        dt: int,
        number: int = MAX_POSITION_NUMBER,
        price: Optional[float] = None,
        bars: Optional[dict] = None,
    ) -> Optional[dict]:
        """Sell shares of a single stock."""
        if not self.position_mgr.is_exist(stock_id):
            return None
        if price is None or price <= 0:
            return None

        # Limit-down check (跌停检测)
        if bars and dt != self._last_day:
            prev_c = bars.get("prev_close", 0)
            c = bars.get("close", price)
            o = bars.get("open", price)
            h = bars.get("high", price)
            low = bars.get("low", price)
            if prev_c and prev_c > 0:
                if c / prev_c <= 0.91 and c == o and c == h and c == low:
                    return None  # 一字跌停，无法卖出

        total_cap, actual_number, profit = self.position_mgr.remove_position(stock_id, price, number)
        if actual_number == 0:
            return None

        fee = self.capital_mgr.sell_one_stock(total_cap)
        self.report_mgr.record_trade(dt, profit, fee)

        self._per_stock_profit.setdefault(stock_id, 0.0)
        self._per_stock_profit[stock_id] += profit - fee
        self._trade_count += 1

        return {"id": stock_id, "price": price, "number": actual_number, "datetime": dt}

    def sell_batch(
        self,
        stock_ids: list[str],
        dt: int,
        prices: dict[str, float],
        number: int = MAX_POSITION_NUMBER,
    ) -> list[dict]:
        """Sell multiple stocks."""
        results = []
        for sid in set(stock_ids):
            price = prices.get(sid)
            if price:
                r = self.sell_one(sid, dt, number, price)
                if r:
                    results.append(r)
        return results

    def clear_all(self, dt: int, prices: dict[str, float]) -> None:
        """Liquidate all positions (end of backtest)."""
        for sid in list(self.position_mgr.get_all_ids()):
            price = prices.get(sid)
            if price:
                self.sell_one(sid, dt, MAX_POSITION_NUMBER, price)

    def process_dividends(
        self,
        dt: int,
        dividend_data: dict[str, dict],
    ) -> None:
        """Process cash dividends, bonus shares, and rights issues.

        dividend_data: { stock_id: {
            "type": 1,          # 1=stock dividend, 11=fund split
            "dividend": float,  # cash per 10 shares
            "price": float,     # rights issue price
            "transfer": float,  # bonus + transfer shares per 10
            "qty": float,       # rights issue shares per 10
            "ratio": float,     # split ratio (for funds)
        } }
        """
        for stock_id, div in dividend_data.items():
            pos = self.position_mgr.get_position(stock_id)
            if not pos:
                continue

            div_type = div.get("type", 0)
            if div_type not in (1, 11):
                continue

            dividend = div.get("dividend", 0)
            allot_price = div.get("price", 0)
            transfer = div.get("transfer", 0)
            qty = div.get("qty", 0)
            ratio = div.get("ratio", 1)

            units = pos.number // 10  # per-10-share units
            if units <= 0:
                continue

            # Recalculate average cost post-dividend
            # 除权价 = (收盘价 + 配股比例×配股价 - 每股派现) / (1 + 送股比例 + 配股比例)
            pos.price = (pos.price + qty / 10 * allot_price - dividend * 0.9 / 10) / (
                1 + transfer / 10 + qty / 10
            )

            # Cash dividend (税后)
            if dividend > 0:
                cash = units * dividend * 0.9  # 10% tax
                self.capital_mgr.add_money(cash)
                self.report_mgr.record_dividend(dt, cash)

            # Bonus/transfer shares
            if transfer > 0:
                pos.number += int(units * transfer)

            # Rights issue
            if qty > 0 and allot_price > 0:
                cost = units * qty * allot_price
                if self.capital_mgr.available_money > cost:
                    self.buy_one(stock_id, dt, {"fixed_amount": cost}, allot_price)

            # Fund split
            if ratio not in (0, 1):
                pos.number = int(pos.number * ratio)

    def get_net_value(self, prices: dict[str, float]) -> float:
        """Total portfolio value = cash + positions market value."""
        position_value = sum(
            self.position_mgr.get_position_value(sid, prices.get(sid, 0))
            for sid in self.position_mgr.get_all_ids()
        )
        return self.capital_mgr.available_money + position_value

    def get_per_stock_profit(self) -> dict[str, float]:
        return dict(sorted(self._per_stock_profit.items(), key=lambda x: x[1], reverse=True))


# ── Trade System (Backtest Orchestrator) ─────────────────────────


class TradeSystem:
    """Full backtest orchestrator.

    Runs through each trading day:
      1. Increment day counters
      2. Process dividends
      3. Call strategy to generate buy/sell signals
      4. Execute trades
      5. Record daily metrics
    """

    def __init__(
        self,
        init_money: float = 1000000,
        max_stocks: int = 20,
        policy: str = "fixed",
        commission: float = 0.0003,
        stamp_tax: float = 0.001,
    ):
        self.capital_mgr = CapitalManager(init_money, policy, commission, stamp_tax)
        self.position_mgr = PositionManager()
        self.report_mgr = ReportManager(init_money)
        self.executor = TradeExecutor(self.capital_mgr, self.position_mgr, self.report_mgr, max_stocks)

    def run(
        self,
        trade_days: list[int],
        daily_prices: dict[int, dict[str, dict]],
        strategy_fn,
        dividend_data: Optional[dict[int, dict[str, dict]]] = None,
        benchmark_prices: Optional[dict[int, float]] = None,
    ) -> dict:
        """Run the full backtest.

        Args:
          trade_days: sorted list of trade dates (YYYYMMDD ints).
          daily_prices: { date: { stock_id: { "open", "high", "low", "close", "volume", "prev_close" } } }
          strategy_fn: callable(date, positions, prices) → (buy_ids, sell_ids)
          dividend_data: { date: { stock_id: { dividend info } } }
          benchmark_prices: { date: benchmark_close_price }

        Returns:
          Full backtest result dict (compatible with BacktestResult type).
        """
        if not trade_days:
            return self.report_mgr.get_summary(self.capital_mgr.available_money)

        self.executor.set_last_day(trade_days[-1])

        for dt in trade_days:
            prices = daily_prices.get(dt, {})
            close_prices = {sid: d.get("close", 0) for sid, d in prices.items()}

            # 1. Increment day counters
            self.position_mgr.increase_day_counter()

            # 2. Process dividends
            if dividend_data and dt in dividend_data:
                self.executor.process_dividends(dt, dividend_data[dt])

            # 3. Strategy signals
            positions = self.position_mgr.get_all_positions()
            buy_ids, sell_ids = strategy_fn(dt, positions, prices)

            # 4. Execute sells first
            if sell_ids:
                self.executor.sell_batch(sell_ids, dt, close_prices)

            # 5. Execute buys
            if buy_ids:
                available = self.capital_mgr.available_money
                count = len(buy_ids)
                for sid in buy_ids:
                    price = close_prices.get(sid, 0)
                    if price > 0:
                        self.executor.buy_one(
                            sid, dt,
                            {"fixed_amount": available / max(count, 1)},
                            price,
                            bars=prices.get(sid),
                        )

            # 6. Record daily metrics
            net_value = self.executor.get_net_value(close_prices)
            self.report_mgr.record_daily(dt, net_value)

            # Last day: clear all positions
            if dt == trade_days[-1]:
                self.executor.clear_all(dt, close_prices)

        final_value = self.capital_mgr.available_money
        summary = self.report_mgr.get_summary(final_value)

        # Add benchmark curve if available
        if benchmark_prices:
            first_bp = None
            bench_curve = []
            for dt in trade_days:
                bp = benchmark_prices.get(dt)
                if bp:
                    if first_bp is None:
                        first_bp = bp
                    bench_curve.append({
                        "date": date_to_str(dt),
                        "value": round(self.capital_mgr.init_money * bp / first_bp, 2),
                    })
            summary["benchmarkCurve"] = bench_curve

        # Add per-stock profit breakdown
        summary["perStockProfit"] = self.executor.get_per_stock_profit()

        return summary


# ── CLI Entry Point ──────────────────────────────────────────────

def run_backtest(config: dict) -> dict:
    """Run a backtest from a JSON config (called by TypeScript bridge).

    Config:
    {
      "initialCash": 1000000,
      "maxStocks": 20,
      "policy": "fixed",
      "commission": 0.0003,
      "stampTax": 0.001,
      "tradeDays": [20200101, ...],
      "dailyPrices": { "20200101": { "sh600519": { "open":..., "close":... } } },
      "signals": { "20200101": { "buy": [...], "sell": [...] } },
      "dividends": { ... },  // optional
      "benchmarkPrices": { ... },  // optional
    }
    """
    system = TradeSystem(
        init_money=config.get("initialCash", 1000000),
        max_stocks=config.get("maxStocks", 20),
        policy=config.get("policy", "fixed"),
        commission=config.get("commission", 0.0003),
        stamp_tax=config.get("stampTax", 0.001),
    )

    trade_days = config.get("tradeDays", [])
    daily_prices = {}
    for date_str, prices in config.get("dailyPrices", {}).items():
        daily_prices[int(date_str)] = prices

    signals = {}
    for date_str, sig in config.get("signals", {}).items():
        signals[int(date_str)] = sig

    def strategy_fn(dt, positions, prices):
        sig = signals.get(dt, {})
        return sig.get("buy", []), sig.get("sell", [])

    dividends = {}
    for date_str, div in config.get("dividends", {}).items():
        dividends[int(date_str)] = div

    benchmark = {}
    for date_str, price in config.get("benchmarkPrices", {}).items():
        benchmark[int(date_str)] = price

    return system.run(
        trade_days,
        daily_prices,
        strategy_fn,
        dividends or None,
        benchmark or None,
    )


if __name__ == "__main__":
    config = json.loads(sys.stdin.read())
    result = run_backtest(config)
    print(json.dumps(result))
