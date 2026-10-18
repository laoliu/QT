"""Deterministic daily portfolio replay for monthly A-share ML targets.

Signals are formed after close and executed at the next session's open. The
engine is deliberately small and fixture-friendly; it owns the portfolio
accounting used by the canonical ML research path.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from .contracts import TargetPosition, price_limit_flags, validate_market_frame
from .corporate_actions import CorporateActionLedger, raw_accounting_bars
from aipha.utils.metrics import compute_annualized_volatility, compute_sharpe_ratio


def run_portfolio_backtest(
    market: pd.DataFrame,
    predictions: pd.DataFrame,
    targets: list[TargetPosition],
    config: Any,
) -> dict[str, Any]:
    if getattr(config, "max_style_exposure", ()):
        from .style import validate_target_styles
        from aipha.universe.historical import filter_historical_universe

        validate_target_styles(targets, filter_historical_universe(market, min_listing_days=config.min_listing_days),
                               config.max_style_exposure)
    # The caller retains authenticated provenance. Per-row accounting needs
    # values and embedded events, not repeated deep copies of the audit attrs.
    bars = market.copy(deep=False)
    bars.attrs = {}
    bars = validate_market_frame(bars)
    bars["_open_limit_up"], bars["_open_limit_down"] = price_limit_flags(bars)
    ledger = CorporateActionLedger(bars)
    bars = raw_accounting_bars(bars)
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars = bars.sort_values(["trade_date", "symbol"])
    date_list = sorted(bars["trade_date"].unique())
    by_date = {d: group.set_index("symbol") for d, group in bars.groupby("trade_date", sort=True)}
    prediction_dates = pd.DatetimeIndex([])
    if not predictions.empty:
        if "trade_date" not in predictions:
            raise ValueError("OOS predictions require trade_date for the evaluation window")
        prediction_dates = pd.DatetimeIndex(pd.to_datetime(predictions["trade_date"], errors="raise")).normalize()
        if prediction_dates.hasnans or not prediction_dates.isin(date_list).all():
            raise ValueError("OOS prediction dates must be present in the market calendar")
    targets_by_date: dict[pd.Timestamp, dict[str, tuple[float, str]]] = {}
    for day, group in pd.DataFrame([asdict(t) for t in targets]).groupby("rebalance_date") if targets else []:
        targets_by_date[pd.Timestamp(day)] = {
            str(row.symbol): (float(row.target_weight), str(row.signal_id))
            for row in group.itertuples(index=False)
        }
    if len(prediction_dates) and not set(targets_by_date).issubset(set(prediction_dates)):
        raise ValueError("target signal dates must belong to the OOS prediction window")

    cash = float(config.initial_capital)
    holdings: dict[str, int] = {}
    buy_dates: dict[str, pd.Timestamp] = {}
    last_prices: dict[str, float] = {}
    previous_nav = cash
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    unexecutable = 0
    execution_attempts = 0
    unexecutable_reasons: dict[str, int] = {}
    fee_total = 0.0
    commission_total = 0.0
    transfer_total = 0.0
    stamp_total = 0.0
    slippage_total = 0.0

    def skip_execution(reason: str) -> None:
        nonlocal unexecutable
        unexecutable += 1
        unexecutable_reasons[reason] = unexecutable_reasons.get(reason, 0) + 1

    # A monthly signal on close D is queued and evaluated on the next available
    # market session, preserving the signal/execution information boundary.
    execution_targets: dict[pd.Timestamp, tuple[dict[str, tuple[float, str]], pd.Timestamp]] = {}
    for i, signal_date in enumerate(date_list[:-1]):
        signal_key = pd.Timestamp(signal_date).normalize()
        if signal_key not in targets_by_date:
            continue
        execution_targets[pd.Timestamp(date_list[i + 1]).normalize()] = (
            targets_by_date[signal_key], signal_key,
        )

    for raw_day in date_list:
        day = pd.Timestamp(raw_day).normalize()
        frame = by_date[raw_day]
        ledger.check_factor_changes(day.date(), frame, holdings)
        cash += ledger.before_open(day.date(), holdings, last_prices, buy_dates)
        if day in execution_targets:
            desired_records, signal_day = execution_targets[day]
            desired = {symbol: weight for symbol, (weight, _) in desired_records.items()}
            signal_ids = {symbol: signal_id for symbol, (_, signal_id) in desired_records.items()}
            current_run_id = next((t.run_id for t in targets if t.rebalance_date == signal_day.date()), "")
            current_model_id = next((t.model_id for t in targets if t.rebalance_date == signal_day.date()), "")
            open_prices = frame["open"].to_dict()
            # Raw currency prices determine actual shares and board lots.
            # Receivables count toward equity, but cannot finance purchases
            # until the dividend/share settlement date has arrived.
            opening_marks = {
                **last_prices,
                **frame.loc[~frame.get("is_suspended", pd.Series(False, index=frame.index)).astype(bool), "open"].to_dict(),
            }
            opening_values = {s: q * opening_marks.get(s, 0.0) for s, q in holdings.items()}
            cash_receivable, share_receivable = ledger.receivables(opening_marks)
            nav_for_sizing = cash + sum(opening_values.values()) + cash_receivable + share_receivable
            desired_shares: dict[str, int] = {}
            for symbol, weight in desired.items():
                price = float(open_prices.get(symbol, 0) or 0)
                if price <= 0:
                    unexecutable += 1
                    continue
                budget = min(float(weight), config.max_position_weight) * nav_for_sizing
                desired_shares[symbol] = int(budget / price / 100) * 100
            union = sorted(set(holdings) | set(desired_shares))
            deltas = {s: desired_shares.get(s, 0) - holdings.get(s, 0) for s in union}
            gross_value = sum(abs(q * float(open_prices.get(s, last_prices.get(s, 0)) or 0)) for s, q in deltas.items())
            turnover_cap = config.max_daily_turnover * max(nav_for_sizing, 0.0)
            scale = min(1.0, turnover_cap / gross_value) if gross_value > 0 else 1.0
            # Sell before buys, allowing proceeds to finance purchases.
            for side in (-1, 1):
                for symbol in sorted(union):
                    delta = deltas[symbol]
                    if delta == 0 or (1 if delta > 0 else -1) != side:
                        continue
                    execution_attempts += 1
                    row = frame.loc[symbol] if symbol in frame.index else None
                    if row is None or bool(row.get("is_suspended", False)):
                        skip_execution("missing_or_suspended")
                        continue
                    if side > 0 and bool(row["_open_limit_up"]):
                        skip_execution("limit_up_buy_block")
                        continue
                    if side < 0 and bool(row["_open_limit_down"]):
                        skip_execution("limit_down_sell_block")
                        continue
                    if side < 0 and buy_dates.get(symbol, day) >= day:
                        skip_execution("t_plus_one")
                        continue
                    requested = int(abs(delta) * scale)
                    qty = (requested // 100) * 100
                    if side < 0:
                        qty = min(qty, holdings.get(symbol, 0))
                        qty = (qty // 100) * 100
                        # Bonus shares can leave an odd-lot remainder. A full
                        # liquidation sells that remainder in the same order.
                        if requested >= holdings.get(symbol, 0):
                            qty = holdings.get(symbol, 0)
                    if qty <= 0:
                        skip_execution("turnover_or_lot_constraint")
                        continue
                    base_price = float(row["open"])
                    slip = config.slippage_bps / 10000
                    fill_price = base_price * (1 + slip if side > 0 else 1 - slip)
                    slippage_cost = abs(fill_price - base_price) * qty
                    notional = qty * fill_price
                    commission = max(config.commission_min, notional * config.commission_rate)
                    transfer = notional * config.transfer_fee_rate
                    stamp = notional * config.stamp_tax_rate if side < 0 else 0.0
                    fees = commission + transfer + stamp
                    if side > 0:
                        # Both the fixed minimum and proportional commission
                        # inequalities must hold before rounding to board lots.
                        max_notional = max(0.0, min(
                            (cash - config.commission_min) / (1 + config.transfer_fee_rate),
                            cash / (1 + config.commission_rate + config.transfer_fee_rate),
                        ))
                        affordable = int(np.nextafter(max_notional / fill_price / 100, np.inf)) * 100
                        qty = min(qty, affordable)
                        qty = (qty // 100) * 100
                        # Recheck the actual fee expression after floating-point
                        # division; never allow an upward rounding to overspend.
                        while qty > 0:
                            notional = qty * fill_price
                            commission = max(config.commission_min, notional * config.commission_rate)
                            transfer = notional * config.transfer_fee_rate
                            fees = commission + transfer
                            if notional + fees <= cash:
                                break
                            qty -= 100
                        if qty <= 0:
                            skip_execution("insufficient_cash")
                            continue
                        slippage_cost = abs(fill_price - base_price) * qty
                        stamp = 0.0
                        cash -= notional + fees
                        holdings[symbol] = holdings.get(symbol, 0) + qty
                        buy_dates[symbol] = day
                    else:
                        cash += notional - fees
                        holdings[symbol] -= qty
                        if holdings[symbol] <= 0:
                            holdings.pop(symbol, None)
                            buy_dates.pop(symbol, None)
                    fee_total += fees
                    commission_total += commission
                    transfer_total += transfer
                    stamp_total += stamp
                    slippage_total += slippage_cost
                    signal_id = signal_ids.get(symbol) or f"{current_run_id or current_model_id or 'research'}:{signal_day.date().isoformat()}:{symbol}:exit"
                    trades.append({
                        "trade_date": day.date().isoformat(), "signal_date": signal_day.date().isoformat(),
                        "symbol": symbol, "signal_id": signal_id, "run_id": current_run_id,
                        "model_id": current_model_id,
                        "side": "buy" if side > 0 else "sell", "qty": qty,
                        "price": fill_price, "base_price": base_price, "notional": notional,
                        "commission": commission, "transfer_fee": transfer, "stamp_tax": stamp,
                        "slippage_cost": slippage_cost, "slippage_bps": config.slippage_bps,
                    })
        close_values = 0.0
        marked_symbols = set(holdings) | {a.symbol for a, _, shares in ledger.pending.values() if shares}
        for symbol in sorted(marked_symbols):
            if symbol in frame.index and not bool(frame.loc[symbol].get("is_suspended", False)):
                last_prices[symbol] = float(frame.loc[symbol, "close"])
            close_values += holdings.get(symbol, 0) * last_prices.get(symbol, 0.0)
        cash_receivable, share_receivable = ledger.receivables(last_prices)
        nav = cash + close_values + cash_receivable + share_receivable
        ret = nav / previous_nav - 1.0 if previous_nav > 0 else 0.0
        daily.append({"trade_date": day.date().isoformat(), "nav": nav, "cash": cash,
                      "position_value": close_values, "daily_return": ret,
                      "dividend_receivable": cash_receivable, "share_receivable_value": share_receivable})
        ledger.after_close(day.date(), holdings)
        previous_nav = nav

    if len(prediction_dates):
        start = prediction_dates.min().date().isoformat()
        daily = [row for row in daily if row["trade_date"] >= start]
    # The first row is the initial equity at signal-date close, before any
    # next-open order. It anchors NAV and is not a return observation.
    returns = pd.Series([x["daily_return"] for x in daily[1:]], dtype=float)
    navs = pd.Series([x["nav"] for x in daily], dtype=float)
    annual_return = float((navs.iloc[-1] / config.initial_capital) ** (252 / len(returns)) - 1) if len(returns) else 0.0
    volatility = compute_annualized_volatility(returns.to_numpy(), trading_days=252)
    sharpe = compute_sharpe_ratio(
        returns.to_numpy(), risk_free_rate=0.0, trading_days=252,
    )
    drawdown = navs / navs.cummax() - 1 if len(navs) else pd.Series(dtype=float)
    traded = sum(float(t["notional"]) for t in trades)
    return {
        "daily_returns": daily,
        "trades": trades,
        "n_unexecutable": unexecutable,
        "corporate_actions": ledger.log,
        "final_positions": dict(holdings),
        "evaluation_window": {
            "start": daily[0]["trade_date"] if daily else None,
            "end": daily[-1]["trade_date"] if daily else None,
            "signal_oos_start": prediction_dates.min().date().isoformat() if len(prediction_dates) else None,
            "signal_oos_end": prediction_dates.max().date().isoformat() if len(prediction_dates) else None,
            "return_observations": len(returns),
            "valuation_tail_sessions": sum(bool(pd.Timestamp(row["trade_date"]) > prediction_dates.max()) for row in daily) if len(prediction_dates) else 0,
            "initial_row_is_nav_anchor": True,
            "annualization_sessions": 252,
        },
        "accounting": {
            "price_basis": "raw", "dividend_tax_policy": "gross_before_holding_period_tax",
            "cash_dividends_paid": ledger.cash_distributed,
            "bonus_shares_delivered": ledger.shares_distributed,
            "fractional_share_policy": "reject_without_explicit_allocation",
            "unexplained_factor_change_policy": "reject_while_held",
        },
        "metrics": {
            "portfolio_total_return": float(navs.iloc[-1] / config.initial_capital - 1) if len(navs) else 0.0,
            "portfolio_annualized_return": annual_return,
            "portfolio_annualized_volatility": volatility,
            "portfolio_sharpe": sharpe,
            "portfolio_max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
            "portfolio_costs": float(fee_total),
            "portfolio_total_costs": float(fee_total + slippage_total),
            "cost_attribution": {
                "commission": float(commission_total),
                "transfer_fee": float(transfer_total),
                "stamp_tax": float(stamp_total),
                "slippage": float(slippage_total),
                "total": float(fee_total + slippage_total),
            },
            "portfolio_turnover": float(traded / config.initial_capital),
            "execution_attempts": int(execution_attempts),
            "unexecutable_rate": float(unexecutable / execution_attempts) if execution_attempts else 0.0,
            "unexecutable_reasons": dict(unexecutable_reasons),
        },
    }
