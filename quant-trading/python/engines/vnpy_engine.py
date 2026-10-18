#!/usr/bin/env python3
"""
VN.py engine bridge — runs vnpy CTA strategies from Node.js.

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout with backtest results.

Supports 6 CTA strategies:
  - vnpy-double-ma, vnpy-atr-rsi, vnpy-boll-channel
  - vnpy-turtle, vnpy-king-keltner, vnpy-multi-timeframe

Usage:
  echo '{"strategyId": "vnpy-turtle", ...}' | python -m engines.vnpy_engine --json

Requirements:
  pip install vnpy vnpy_ctastrategy akshare pandas
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── Strategy Definitions ──────────────────────────────────────────

def _build_strategies():
    """Return vnpy CTA strategy classes."""
    from vnpy_ctastrategy import CtaTemplate

    class _BaseBridge(CtaTemplate):
        """Base with required abstract methods filled in."""
        def on_init(self):
            self.write_log(f"{self.__class__.__name__} on_init")
        def on_start(self):
            self.write_log(f"{self.__class__.__name__} on_start")
        def on_stop(self):
            pass
        def on_tick(self, tick):
            pass

    class DoubleMaStrategy(_BaseBridge):
        author = "vnpy-bridge"
        fast_window = 10
        slow_window = 30
        fast_ma0 = 0.0
        fast_ma1 = 0.0
        slow_ma0 = 0.0
        slow_ma1 = 0.0
        parameters = ["fast_window", "slow_window"]
        variables = ["fast_ma0", "fast_ma1", "slow_ma0", "slow_ma1"]

        def on_bar(self, bar):
            am = self.am
            if not am.inited:
                return
            self.fast_ma0 = am.sma(self.fast_window)
            self.slow_ma0 = am.sma(self.slow_window)
            if self.fast_ma1 and self.slow_ma1:
                if self.fast_ma0 > self.slow_ma0 and self.fast_ma1 <= self.slow_ma1:
                    if self.pos <= 0:
                        self.buy(bar.close_price, 1)
                elif self.fast_ma0 < self.slow_ma0 and self.fast_ma1 >= self.slow_ma1:
                    if self.pos > 0:
                        self.sell(bar.close_price, 1)
            self.fast_ma1 = self.fast_ma0
            self.slow_ma1 = self.slow_ma0

    class AtrRsiStrategy(_BaseBridge):
        author = "vnpy-bridge"
        atr_period = 22
        rsi_period = 5
        rsi_entry = 16
        atr_value = 0.0
        rsi_value = 0.0
        parameters = ["atr_period", "rsi_period", "rsi_entry"]
        variables = ["atr_value", "rsi_value"]

        def on_bar(self, bar):
            am = self.am
            if not am.inited:
                return
            self.atr_value = am.atr(self.atr_period)
            self.rsi_value = am.rsi(self.rsi_period)
            if self.rsi_value < self.rsi_entry:
                if self.pos <= 0:
                    self.buy(bar.close_price, 1)
            elif self.rsi_value > (100 - self.rsi_entry):
                if self.pos > 0:
                    self.sell(bar.close_price, 1)

    class BollChannelStrategy(_BaseBridge):
        author = "vnpy-bridge"
        boll_window = 18
        boll_dev = 3.4
        sl_multiplier = 5.2
        boll_up = 0.0
        boll_down = 0.0
        parameters = ["boll_window", "boll_dev", "sl_multiplier"]
        variables = ["boll_up", "boll_down"]

        def on_bar(self, bar):
            am = self.am
            if not am.inited:
                return
            self.boll_up, self.boll_down = am.boll(self.boll_window, self.boll_dev)
            if bar.close_price > self.boll_up:
                if self.pos <= 0:
                    self.buy(bar.close_price, 1)
            elif bar.close_price < self.boll_down:
                if self.pos > 0:
                    self.sell(bar.close_price, 1)

    class TurtleStrategy(_BaseBridge):
        author = "vnpy-bridge"
        entry_window = 20
        exit_window = 10
        entry_up = 0.0
        exit_down = 0.0
        parameters = ["entry_window", "exit_window"]
        variables = ["entry_up", "exit_down"]

        def on_bar(self, bar):
            am = self.am
            if not am.inited:
                return
            self.entry_up = am.high[-self.entry_window:].max() if len(am.high) >= self.entry_window else bar.high_price
            self.exit_down = am.low[-self.exit_window:].min() if len(am.low) >= self.exit_window else bar.low_price
            if bar.close_price > self.entry_up:
                if self.pos <= 0:
                    self.buy(bar.close_price, 1)
            elif bar.close_price < self.exit_down:
                if self.pos > 0:
                    self.sell(bar.close_price, 1)

    class KingKeltnerStrategy(_BaseBridge):
        author = "vnpy-bridge"
        kk_period = 40
        kk_dev = 2.0
        kk_up = 0.0
        kk_down = 0.0
        parameters = ["kk_period", "kk_dev"]
        variables = ["kk_up", "kk_down"]

        def on_bar(self, bar):
            am = self.am
            if not am.inited:
                return
            ema = am.ema(self.kk_period)
            atr = am.atr(self.kk_period)
            self.kk_up = ema + self.kk_dev * atr
            self.kk_down = ema - self.kk_dev * atr
            if bar.close_price > self.kk_up:
                if self.pos <= 0:
                    self.buy(bar.close_price, 1)
            elif bar.close_price < self.kk_down:
                if self.pos > 0:
                    self.sell(bar.close_price, 1)

    class MultiTimeframeStrategy(_BaseBridge):
        author = "vnpy-bridge"
        fast_period = 5
        slow_period = 20
        weekly_period = 10
        fast_ma = 0.0
        slow_ma = 0.0
        parameters = ["fast_period", "slow_period", "weekly_period"]
        variables = ["fast_ma", "slow_ma"]

        def on_bar(self, bar):
            am = self.am
            if not am.inited:
                return
            self.fast_ma = am.sma(self.fast_period)
            self.slow_ma = am.sma(self.slow_period)
            trend_ma = am.sma(self.weekly_period * 5)
            if self.fast_ma > self.slow_ma and bar.close_price > trend_ma:
                if self.pos <= 0:
                    self.buy(bar.close_price, 1)
            elif self.fast_ma < self.slow_ma or bar.close_price < trend_ma:
                if self.pos > 0:
                    self.sell(bar.close_price, 1)

    return {
        "vnpy-double-ma": DoubleMaStrategy,
        "vnpy-atr-rsi": AtrRsiStrategy,
        "vnpy-boll-channel": BollChannelStrategy,
        "vnpy-turtle": TurtleStrategy,
        "vnpy-king-keltner": KingKeltnerStrategy,
        "vnpy-multi-timeframe": MultiTimeframeStrategy,
    }


# ── Data Fetching ─────────────────────────────────────────────────

def _fetch_data(symbol: str, start_date: str, end_date: str, market: str | None = None):
    """Fetch OHLCV data, convert to vnpy BarData list."""
    try:
        from data_providers.provider_factory import fetch_data_for_backtest
        df = fetch_data_for_backtest(symbol, start_date, end_date, market)
    except ImportError:
        df = _fetch_data_akshare(symbol, start_date, end_date)

    if df is None or df.empty:
        return []

    return df


def _fetch_data_akshare(symbol: str, start_date: str, end_date: str):
    """Fallback: fetch A-share data via akshare."""
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]]
        return df
    except Exception as e:
        print(f"[vnpy_engine] akshare fetch failed for {symbol}: {e}", file=sys.stderr)
        return None


# ── Simple Backtest Loop ──────────────────────────────────────────

def run_backtest(config: dict):
    """
    Lightweight CTA backtest using vnpy strategy classes.

    vnpy's built-in BacktestingEngine needs a database / VN Trader gateway,
    so we run a simplified bar-by-bar loop that instantiates the CtaTemplate
    subclass and drives it with market data.
    """
    strategy_id = config.get("strategyId", "vnpy-turtle")
    from strategies.lifecycle import legacy_engine_error
    error = legacy_engine_error(strategy_id)
    if error:
        return {"ok": False, "error": error}
    symbols = config.get("symbols", ["000300"])
    if isinstance(symbols, str):
        symbols = [symbols]
    start_date = config.get("startDate", "2024-01-01")
    end_date = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    initial_cash = config.get("initialCash", 100000)
    commission = config.get("commission", 0.0003)
    parameters = config.get("parameters", {})

    strategies = _build_strategies()
    strategy_cls = strategies.get(strategy_id)
    if strategy_cls is None:
        return {"ok": False, "error": f"Unknown vnpy strategy: {strategy_id}"}

    symbol = symbols[0]
    df = _fetch_data(symbol, start_date, end_date)
    if df is None or (isinstance(df, list) and len(df) == 0) or (hasattr(df, "empty") and df.empty):
        return {"ok": False, "error": f"No data for {symbol}"}

    # Run simplified bar-by-bar backtest
    cash = float(initial_cash)
    pos = 0
    trades = []
    equity_curve = []
    signals = []

    # Use ArrayManager manually
    from vnpy_ctastrategy import ArrayManager

    am = ArrayManager(size=max(200, int(parameters.get("entry_window", 20)) * 3 + 50))

    # Create a minimal strategy instance
    class _FakeEngine:
        """Minimal engine mock for CtaTemplate init."""
        def subscribe(self, *a, **kw): pass
        def send_order(self, *a, **kw): return []
        def cancel_order(self, *a, **kw): pass
        def write_log(self, msg, *a): print(f"[vnpy] {msg}", file=sys.stderr)
        def put_event(self, *a): pass

    from vnpy.trader.object import BarData, Exchange
    from datetime import timezone

    # Instantiate strategy
    strat = strategy_cls(_FakeEngine(), strategy_id, symbol, {})
    for k, v in parameters.items():
        if hasattr(strat, k):
            setattr(strat, k, v)
    strat.am = am
    strat.pos = 0
    strat.inited = True
    strat.trading = True

    # Intercept buy/sell
    entry_price = 0.0

    def mock_buy(price, volume, stop=False, lock=False, net=False):
        nonlocal cash, pos, entry_price
        cost = price * volume * (1 + commission)
        if cash >= cost:
            cash -= cost
            pos += int(volume)
            entry_price = price
            signals.append({
                "date": current_date,
                "type": "BUY",
                "price": round(price, 2),
                "quantity": int(volume),
            })
            trades.append(("BUY", current_date, price))

    def mock_sell(price, volume, stop=False, lock=False, net=False):
        nonlocal cash, pos
        if pos > 0:
            sell_vol = min(int(volume), pos)
            cash += price * sell_vol * (1 - commission)
            pos -= sell_vol
            signals.append({
                "date": current_date,
                "type": "SELL",
                "price": round(price, 2),
                "quantity": sell_vol,
            })
            trades.append(("SELL", current_date, price))

    strat.buy = mock_buy
    strat.sell = mock_sell

    current_date = ""

    for idx, row in df.iterrows():
        dt = idx if not isinstance(idx, str) else datetime.fromisoformat(str(idx))
        current_date = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]

        bar = BarData(
            symbol=symbol,
            exchange=Exchange.SSE,
            datetime=dt.replace(tzinfo=timezone.utc) if hasattr(dt, "replace") else dt,
            interval="d",
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            volume=float(row["volume"]),
            gateway_name="vnpy_bridge",
        )

        am.update_bar(bar)
        strat.on_bar(bar)

        # Track equity
        market_value = cash + pos * float(row["close"])
        equity_curve.append({"date": current_date, "value": round(market_value, 2)})

    # Final stats
    final_price = float(df.iloc[-1]["close"]) if len(df) > 0 else 0
    final_value = cash + pos * final_price
    total_return = (final_value - initial_cash) / initial_cash * 100

    # Max drawdown
    peak = initial_cash
    max_dd = 0.0
    for pt in equity_curve:
        v = pt["value"]
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Win rate
    buy_price = None
    wins = 0
    total_round_trips = 0
    for action, dt, price in trades:
        if action == "BUY":
            buy_price = price
        elif action == "SELL" and buy_price is not None:
            total_round_trips += 1
            if price > buy_price:
                wins += 1
            buy_price = None

    win_rate = (wins / total_round_trips * 100) if total_round_trips > 0 else 0

    # Annualized return
    days = len(df)
    ann_return = ((final_value / initial_cash) ** (252 / max(days, 1)) - 1) * 100 if days > 0 else 0

    # Sharpe (simplified)
    if len(equity_curve) > 1:
        import math
        daily_returns = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]["value"]
            cur = equity_curve[i]["value"]
            daily_returns.append((cur - prev) / prev if prev > 0 else 0)
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe = 0

    return {
        "strategyId": strategy_id,
        "symbols": symbols,
        "startDate": start_date,
        "endDate": end_date,
        "initialCash": initial_cash,
        "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2),
        "annualizedReturn": round(ann_return, 2),
        "maxDrawdown": round(max_dd, 2),
        "sharpeRatio": round(sharpe, 3),
        "winRate": round(win_rate, 1),
        "totalTrades": len(trades),
        "signals": signals[-200:],
        "equityCurve": equity_curve,
    }


# ── Main ──────────────────────────────────────────────────────────

def main():
    try:
        config = get_input()
        result = run_backtest(config)
    except Exception as e:
        result = {"ok": False, "error": str(e)}
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
