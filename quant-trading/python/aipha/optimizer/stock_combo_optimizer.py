#!/usr/bin/env python3
"""
个股多策略组合优化器 — Stock Combo Optimizer

对每只股票找到最佳的2-3策略组合，通过回测优化参数，
输出可执行的交易计划（买卖价格、加减仓规则、止损止盈）。

Actions:
  run           — 执行组合优化
  status        — 查询进度
  query_stock   — 查询某股票的优化结果
  list_runs     — 列出历史运行记录
  delete_run    — 删除某次运行

Input (JSON via stdin / QUANT_CONFIG):
  { "action": "run",
    "symbols": ["600519", "000858"],   // 指定股票（可选）
    "universe": "csi300",              // 或指定范围
    "backtest_years": 5,
    "top_k_strategies": 5,            // 从fitness取每只股票top-K策略
    "max_combo_size": 3,              // 最大组合大小
    "initial_capital": 1000000,
    "combo_methods": ["voting", "weighted", "gated"]
  }
"""

import hashlib
import itertools
import json
import math
import os
import sqlite3
import sys
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

# Proxy bypass
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

import urllib.request
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_AIPHA_ROOT = os.path.dirname(_SCRIPT_DIR)
_PYTHON_ROOT = os.path.dirname(_AIPHA_ROOT)
sys.path.insert(0, _PYTHON_ROOT)

# ── Progress file ────────────────────────────────────────────────

_LOGS_DIR = os.path.join(os.path.dirname(_PYTHON_ROOT), "logs")
_PROGRESS_FILE = os.path.join(_LOGS_DIR, "stock-optimization-progress.json")


def _write_progress(data: dict) -> None:
    os.makedirs(_LOGS_DIR, exist_ok=True)
    try:
        with open(_PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _read_progress() -> dict:
    try:
        with open(_PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle"}


# ── Helpers ──────────────────────────────────────────────────────

def _sf(v, default=0.0):
    try:
        f = float(v) if v is not None else default
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def _get_db_path() -> str:
    data_dir = os.environ.get("QUANT_DATA_DIR", "")
    if not data_dir:
        home = os.path.expanduser("~")
        data_dir = os.path.join(home, ".openclaw", "quant-data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "quant-trading.db")


def _sample_equity_curve(
    equity: list[float], dates: list[date], max_points: int = 250,
) -> list[dict]:
    """Downsample equity curve to max_points for JSON storage.

    Returns: [{date: "YYYY-MM-DD", value: float}, ...]
    """
    if not equity:
        return []
    n = len(equity)
    if n <= max_points:
        step = 1
    else:
        step = max(1, n // max_points)

    sampled = []
    for i in range(0, n, step):
        d_str = str(dates[i]) if i < len(dates) else ""
        sampled.append({"date": d_str, "value": round(equity[i], 2)})

    # Always include last point
    if sampled and n > 0 and (n - 1) % step != 0:
        d_str = str(dates[-1]) if dates else ""
        sampled.append({"date": d_str, "value": round(equity[-1], 2)})

    return sampled


def _combo_id(strategy_ids: list[str], method: str) -> str:
    """Generate deterministic combo ID from sorted strategy IDs + method."""
    key = "|".join(sorted(strategy_ids)) + "|" + method
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _filter_bars_for_symbol(
    bars_by_date: dict[date, dict[str, Any]], symbol: str,
) -> dict[date, dict[str, Any]]:
    """Extract only the target symbol's bars — avoids processing all 765 stocks per backtest."""
    filtered: dict[date, dict[str, Any]] = {}
    for d, syms in bars_by_date.items():
        if symbol in syms:
            filtered[d] = {symbol: syms[symbol]}
    return filtered


def composite_score(metrics: dict) -> float:
    """综合评分 — 与 fitness 一致 + 信号一致性加分。"""
    total_return = _sf(metrics.get("total_return"))
    ann_return = _sf(metrics.get("annualized_return"))
    sharpe = _sf(metrics.get("sharpe_ratio"))
    win_rate = _sf(metrics.get("win_rate"))
    profit_factor = _sf(metrics.get("profit_factor"))
    max_dd = _sf(metrics.get("max_drawdown"))
    total_trades = _sf(metrics.get("total_trades"))
    signal_consistency = _sf(metrics.get("signal_consistency"), 0.5)

    score = (
        total_return * 1.0
        + ann_return * 0.35
        + sharpe * 12
        + win_rate * 0.28
        + min(profit_factor, 4) * 4
        - max_dd * 0.85
        + min(total_trades, 120) * 0.03
        + signal_consistency * 5   # 信号一致性加分
    )
    return round(score, 4)


# ── Parameter grid search ────────────────────────────────────────

def _get_param_grid(strategy_id: str, steps: int = 5) -> list[dict]:
    """Generate parameter grid for a strategy using its declared param_space.

    Args:
        strategy_id: strategy name/id
        steps: number of steps per parameter (5 = coarse, 10 = fine)

    Returns:
        list of param dicts to try. Empty list if no param_space.
    """
    try:
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        cls_ = reg.get(strategy_id)
        if cls_ is None:
            return []
        space = cls_.param_space()
        if space is None:
            return []

        # Adjust step sizes based on requested resolution
        from aipha.strategy.param_space import ParamSpec, ParamType
        adjusted_specs = []
        for spec in space.specs:
            if spec.param_type in (ParamType.INT, ParamType.FLOAT):
                if spec.low is not None and spec.high is not None:
                    new_step = (spec.high - spec.low) / max(steps - 1, 1)
                    if spec.param_type == ParamType.INT:
                        new_step = max(1, int(round(new_step)))
                    adjusted_specs.append(ParamSpec(
                        name=spec.name,
                        param_type=spec.param_type,
                        default=spec.default,
                        low=spec.low,
                        high=spec.high,
                        step=new_step,
                        choices=spec.choices,
                        description=spec.description,
                        is_critical=spec.is_critical,
                    ))
                else:
                    adjusted_specs.append(spec)
            else:
                adjusted_specs.append(spec)

        from aipha.strategy.param_space import ParamSpace, generate_grid
        adjusted_space = ParamSpace(strategy_name=space.strategy_name, specs=adjusted_specs)
        grid = generate_grid(adjusted_space, max_combinations=200)
        return grid
    except Exception:
        return []


def _run_combo_backtest_with_params(
    symbol: str,
    strategy_ids: list[str],
    combo_method: str,
    bars_by_date: dict[date, dict[str, Any]],
    initial_capital: float,
    param_overrides: dict[str, dict] | None = None,
) -> dict | None:
    """Run combined backtest with optional per-strategy param overrides.

    param_overrides: {strategy_id: {param_name: value, ...}}
    bars_by_date should already be filtered to contain only this symbol's data.
    """
    try:
        import aipha.strategy.register_all  # noqa: F401
        from aipha.strategy.registry import StrategyRegistry
        from aipha.backtest.engine import BacktestEngine, BacktestConfig
        from aipha.domain.models import Side

        reg = StrategyRegistry()
        strategy_signals: dict[str, dict[date, str]] = {}
        strategy_trades: dict[str, list] = {}

        for sid in strategy_ids:
            try:
                kwargs = (param_overrides or {}).get(sid, {})
                strategy = reg.create(sid, **kwargs)
                config = BacktestConfig(initial_capital=initial_capital)
                engine = BacktestEngine(config=config, strategy=strategy)
                result = engine.run(bars_by_date)

                signals: dict[date, str] = {}
                trades_for_stock = []
                for t in (result.trades or []):
                    if t.symbol == symbol:
                        signals[t.trade_date] = "buy" if t.side == Side.BUY else "sell"
                        trades_for_stock.append(t)

                strategy_signals[sid] = signals
                strategy_trades[sid] = trades_for_stock
            except Exception as e:
                print(f"  [Combo] 策略 {sid} 回测失败: {e}", file=sys.stderr)
                strategy_signals[sid] = {}
                strategy_trades[sid] = []

        combined_signals = _combine_signals(
            strategy_signals, combo_method, strategy_ids
        )

        if not combined_signals:
            return None

        metrics = _simulate_combined_portfolio(
            symbol, combined_signals, bars_by_date, initial_capital
        )

        if not metrics:
            return None

        consistency = _calc_signal_consistency(strategy_signals, symbol)
        metrics["signal_consistency"] = consistency

        strategy_name_map = {}
        for sid in strategy_ids:
            try:
                s = reg.create(sid)
                strategy_name_map[sid] = getattr(s, "name", sid)
            except Exception:
                strategy_name_map[sid] = sid

        metrics["strategy_ids"] = strategy_ids
        metrics["strategy_names"] = [strategy_name_map.get(s, s) for s in strategy_ids]
        metrics["combo_method"] = combo_method
        if param_overrides:
            metrics["optimized_params"] = param_overrides

        return metrics

    except Exception as e:
        print(f"  [Combo] 组合回测失败 {strategy_ids}: {e}", file=sys.stderr)
        return None


def _grid_search_combo(
    symbol: str,
    strategy_ids: list[str],
    combo_method: str,
    bars_by_date: dict[date, dict[str, Any]],
    initial_capital: float,
) -> dict | None:
    """Two-phase grid search: coarse (5 steps) → refine top 3 (10 steps).

    Phase 1: For each strategy that has param_space, generate a coarse grid.
             Test all param combos and score.
    Phase 2: Take top 3 param combos from Phase 1, refine with finer grid
             around each winning param set.

    Returns best metrics dict or None.
    """
    # Phase 0: default params baseline
    baseline = _run_combo_backtest_with_params(
        symbol, strategy_ids, combo_method, bars_by_date, initial_capital
    )
    if not baseline:
        return None
    baseline_score = composite_score(baseline)

    # Check which strategies have param_space
    strategies_with_params = {}
    for sid in strategy_ids:
        grid = _get_param_grid(sid, steps=5)
        if grid and len(grid) > 1:
            strategies_with_params[sid] = grid

    if not strategies_with_params:
        # No tunable params — just return baseline
        return baseline

    # Phase 1: Coarse search
    # For efficiency, only tune one strategy at a time (not cartesian of all)
    best_params: dict[str, dict] = {}
    best_score = baseline_score
    best_metrics = baseline

    for sid, grid in strategies_with_params.items():
        sid_best_params: dict = {}
        sid_best_score = best_score

        for params in grid[:50]:  # cap at 50 combos per strategy
            overrides = {**best_params, sid: params}
            metrics = _run_combo_backtest_with_params(
                symbol, strategy_ids, combo_method, bars_by_date,
                initial_capital, overrides,
            )
            if not metrics:
                continue
            score = composite_score(metrics)
            if score > sid_best_score:
                sid_best_score = score
                sid_best_params = params
                best_metrics = metrics

        if sid_best_params:
            best_params[sid] = sid_best_params
            best_score = sid_best_score

    # Phase 2: Fine-tune top params (10 steps around best)
    if best_params:
        for sid, coarse_params in list(best_params.items()):
            fine_grid = _get_param_grid(sid, steps=10)
            if not fine_grid or len(fine_grid) <= 1:
                continue

            # Filter fine grid to params near the coarse winner
            nearby = []
            for params in fine_grid:
                # Check if params are within ~30% of coarse winner
                close = True
                for k, v in coarse_params.items():
                    pv = params.get(k, v)
                    if isinstance(v, (int, float)) and v != 0:
                        if abs(pv - v) / abs(v) > 0.35:
                            close = False
                            break
                if close:
                    nearby.append(params)

            for params in nearby[:30]:  # cap refinement
                overrides = {**best_params, sid: params}
                metrics = _run_combo_backtest_with_params(
                    symbol, strategy_ids, combo_method, bars_by_date,
                    initial_capital, overrides,
                )
                if not metrics:
                    continue
                score = composite_score(metrics)
                if score > best_score:
                    best_score = score
                    best_params[sid] = params
                    best_metrics = metrics

    if best_metrics and best_score > baseline_score:
        best_metrics["optimized_params"] = best_params
        return best_metrics
    return baseline


# ── Fetch top strategies from fitness DB ─────────────────────────

def _get_top_strategies_for_stock(
    symbol: str, top_k: int = 5, batch_id: str | None = None,
) -> list[dict]:
    """从 stock_strategy_fitness 表获取该股票的 Top-K 策略。"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not batch_id:
            row = conn.execute(
                "SELECT batch_id FROM stock_strategy_fitness ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return []
            batch_id = row["batch_id"]

        rows = conn.execute(
            """SELECT strategy_id, strategy_name, composite_score,
                      total_return, annualized_return, sharpe_ratio,
                      max_drawdown, win_rate, profit_loss_ratio, total_trades
               FROM stock_strategy_fitness
               WHERE symbol = ? AND batch_id = ?
               ORDER BY composite_score DESC
               LIMIT ?""",
            (symbol, batch_id, top_k),
        ).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Generate strategy combinations ───────────────────────────────

def _generate_combos(
    strategy_ids: list[str],
    max_combo_size: int = 3,
    methods: list[str] | None = None,
) -> list[dict]:
    """Generate all viable (strategy_combo, method) combinations."""
    if methods is None:
        methods = ["voting", "weighted", "gated"]

    combos = []
    # Generate pairs and triples (skip singles — that's fitness already)
    for size in range(2, min(max_combo_size + 1, len(strategy_ids) + 1)):
        for combo in itertools.combinations(strategy_ids, size):
            for method in methods:
                # gated method needs at least 2 strategies
                if method == "gated" and len(combo) < 2:
                    continue
                combos.append({
                    "strategy_ids": list(combo),
                    "method": method,
                    "combo_id": _combo_id(list(combo), method),
                })

    return combos


# ── Combined backtest for a strategy combo ───────────────────────

def _run_combo_backtest(
    symbol: str,
    strategy_ids: list[str],
    combo_method: str,
    bars_by_date: dict[date, dict[str, Any]],
    initial_capital: float,
) -> dict | None:
    """Run combined backtest for a (stock, strategy_combo, method) tuple.

    Combo methods:
      - voting: All strategies run independently; buy only when majority agree
      - weighted: Weighted signal aggregation based on fitness scores
      - gated: First strategy is primary, rest are confirmers
    """
    try:
        import aipha.strategy.register_all  # noqa: F401
        from aipha.strategy.registry import StrategyRegistry
        from aipha.backtest.engine import BacktestEngine, BacktestConfig
        from aipha.domain.models import Side

        reg = StrategyRegistry()

        # Run each strategy independently, collect signals per date
        strategy_signals: dict[str, dict[date, str]] = {}  # {sid: {date: "buy"|"sell"|None}}
        strategy_trades: dict[str, list] = {}

        for sid in strategy_ids:
            try:
                strategy = reg.create(sid)
                config = BacktestConfig(initial_capital=initial_capital)
                engine = BacktestEngine(config=config, strategy=strategy)
                result = engine.run(bars_by_date)

                # Extract signals for this stock
                signals: dict[date, str] = {}
                trades_for_stock = []
                for t in (result.trades or []):
                    if t.symbol == symbol:
                        signals[t.trade_date] = "buy" if t.side == Side.BUY else "sell"
                        trades_for_stock.append(t)

                strategy_signals[sid] = signals
                strategy_trades[sid] = trades_for_stock
            except Exception as e:
                print(f"  [Combo] 策略 {sid} 回测失败: {e}", file=sys.stderr)
                strategy_signals[sid] = {}
                strategy_trades[sid] = []

        # Combine signals based on method
        combined_signals = _combine_signals(
            strategy_signals, combo_method, strategy_ids
        )

        if not combined_signals:
            return None

        # Simulate portfolio with combined signals
        metrics = _simulate_combined_portfolio(
            symbol, combined_signals, bars_by_date, initial_capital
        )

        if not metrics:
            return None

        # Calculate signal consistency
        consistency = _calc_signal_consistency(strategy_signals, symbol)
        metrics["signal_consistency"] = consistency

        # Add strategy info
        strategy_name_map = {}
        for sid in strategy_ids:
            try:
                s = reg.create(sid)
                strategy_name_map[sid] = getattr(s, "name", sid)
            except Exception:
                strategy_name_map[sid] = sid

        metrics["strategy_ids"] = strategy_ids
        metrics["strategy_names"] = [strategy_name_map.get(s, s) for s in strategy_ids]
        metrics["combo_method"] = combo_method

        return metrics

    except Exception as e:
        print(f"  [Combo] 组合回测失败 {strategy_ids}: {e}", file=sys.stderr)
        return None


def _combine_signals(
    strategy_signals: dict[str, dict[date, str]],
    method: str,
    strategy_ids: list[str],
) -> dict[date, str]:
    """Combine signals from multiple strategies using specified method."""
    all_dates = set()
    for sigs in strategy_signals.values():
        all_dates.update(sigs.keys())

    combined: dict[date, str] = {}

    for d in sorted(all_dates):
        buy_count = 0
        sell_count = 0
        total = len(strategy_ids)

        if method == "voting":
            # Majority vote
            for sid in strategy_ids:
                sig = strategy_signals.get(sid, {}).get(d)
                if sig == "buy":
                    buy_count += 1
                elif sig == "sell":
                    sell_count += 1

            threshold = max(2, (total + 1) // 2)  # majority
            if buy_count >= threshold:
                combined[d] = "buy"
            elif sell_count >= threshold:
                combined[d] = "sell"

        elif method == "weighted":
            # Weighted score (equal weights for now, fitness weights later)
            weight_per = 1.0 / total
            buy_score = 0.0
            sell_score = 0.0
            for sid in strategy_ids:
                sig = strategy_signals.get(sid, {}).get(d)
                if sig == "buy":
                    buy_score += weight_per
                elif sig == "sell":
                    sell_score += weight_per

            if buy_score >= 0.6:
                combined[d] = "buy"
            elif sell_score >= 0.6:
                combined[d] = "sell"

        elif method == "gated":
            # First strategy is primary, rest are filters
            primary_sid = strategy_ids[0]
            primary_sig = strategy_signals.get(primary_sid, {}).get(d)
            if not primary_sig:
                continue

            # Check that at least one filter confirms
            confirmed = False
            for sid in strategy_ids[1:]:
                filter_sig = strategy_signals.get(sid, {}).get(d)
                if filter_sig == primary_sig:
                    confirmed = True
                    break

            if confirmed:
                combined[d] = primary_sig

    return combined


def _simulate_combined_portfolio(
    symbol: str,
    combined_signals: dict[date, str],
    bars_by_date: dict[date, dict[str, Any]],
    initial_capital: float,
) -> dict | None:
    """Simulate a portfolio trading one stock based on combined signals."""
    sorted_dates = sorted(bars_by_date.keys())
    if not sorted_dates:
        return None

    cash = initial_capital
    position = 0
    avg_cost = 0.0
    total_trades = 0
    wins = 0
    losses = 0
    total_profit = 0.0
    total_loss = 0.0
    equity_curve: list[float] = []

    # Fee structure
    commission_rate = 8.54e-05
    commission_min = 5.0
    stamp_tax_rate = 0.0005   # sell only
    slippage_bps = 10.0

    # Track buy/sell prices and dates for trading plan extraction
    buy_prices: list[float] = []
    sell_prices: list[float] = []
    buy_dates: list[date] = []
    sell_dates: list[date] = []
    holding_days_list: list[int] = []
    current_hold_start: date | None = None

    for d in sorted_dates:
        if symbol not in bars_by_date.get(d, {}):
            if position > 0:
                equity_curve.append(cash + position * (avg_cost if avg_cost else 0))
            else:
                equity_curve.append(cash)
            continue

        bar = bars_by_date[d][symbol]
        price = bar.close
        signal = combined_signals.get(d)

        if signal == "buy" and position == 0 and cash > 0:
            # Buy — use max 30% of capital per position
            budget = min(cash * 0.3, cash)
            qty = int(budget / (price * (1 + slippage_bps / 10000))) // 100 * 100
            if qty >= 100:
                exec_price = price * (1 + slippage_bps / 10000)
                notional = qty * exec_price
                fee = max(notional * commission_rate, commission_min)
                cost = notional + fee
                if cost <= cash:
                    cash -= cost
                    avg_cost = exec_price
                    position = qty
                    total_trades += 1
                    buy_prices.append(exec_price)
                    buy_dates.append(d)
                    current_hold_start = d

        elif signal == "sell" and position > 0:
            # Sell all
            exec_price = price * (1 - slippage_bps / 10000)
            notional = position * exec_price
            fee = max(notional * commission_rate, commission_min)
            stamp = notional * stamp_tax_rate
            proceeds = notional - fee - stamp
            cash += proceeds
            total_trades += 1
            sell_prices.append(exec_price)
            sell_dates.append(d)

            pnl = (exec_price - avg_cost) * position
            if pnl > 0:
                wins += 1
                total_profit += pnl
            else:
                losses += 1
                total_loss += abs(pnl)

            if current_hold_start:
                holding_days_list.append((d - current_hold_start).days)
                current_hold_start = None

            position = 0
            avg_cost = 0.0

        # Equity
        equity = cash + position * price
        equity_curve.append(equity)

    if len(equity_curve) < 60:
        return None

    # Calculate metrics
    final_equity = equity_curve[-1]
    total_return = (final_equity / initial_capital - 1) * 100

    trading_days = len(equity_curve)
    years = trading_days / 245
    ann_return = 0.0
    if years > 0 and final_equity > 0:
        ann_return = ((final_equity / initial_capital) ** (1 / years) - 1) * 100

    # Max drawdown
    max_dd = 0.0
    peak = equity_curve[0]
    for eq in equity_curve[1:]:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd

    # Sharpe ratio
    sharpe = 0.0
    daily_returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            daily_returns.append(equity_curve[i] / equity_curve[i - 1] - 1)
    if daily_returns:
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in daily_returns) / max(1, len(daily_returns) - 1)) ** 0.5
        if std_ret > 0:
            sharpe = (avg_ret - 0.03 / 252) / std_ret * (252 ** 0.5)

    # Sortino
    sortino = 0.0
    if daily_returns:
        neg = [r for r in daily_returns if r < 0]
        if neg:
            down_std = (sum(r ** 2 for r in neg) / len(neg)) ** 0.5
            if down_std > 0:
                avg_ret = sum(daily_returns) / len(daily_returns)
                sortino = (avg_ret - 0.03 / 252) / down_std * (252 ** 0.5)

    # Win rate & profit factor
    trade_count = wins + losses
    win_rate = (wins / trade_count * 100) if trade_count > 0 else 0
    profit_factor = (total_profit / total_loss) if total_loss > 0 else 0

    # Calmar
    calmar = (ann_return / max_dd) if max_dd > 0 else 0

    # Holding period stats
    avg_hold = sum(holding_days_list) / len(holding_days_list) if holding_days_list else 0
    min_hold = min(holding_days_list) if holding_days_list else 0
    max_hold = max(holding_days_list) if holding_days_list else 0

    return {
        "total_return": round(total_return, 2),
        "annualized_return": round(ann_return, 2),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown": round(max_dd, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4),
        "calmar_ratio": round(calmar, 4),
        "total_trades": total_trades,
        "equity_curve": equity_curve,
        "buy_prices": buy_prices,
        "sell_prices": sell_prices,
        "buy_dates": [str(d) for d in buy_dates],
        "sell_dates": [str(d) for d in sell_dates],
        "holding_days": {
            "avg": round(avg_hold, 1),
            "min": min_hold,
            "max": max_hold,
        },
        "backtest_start": str(sorted_dates[0]),
        "backtest_end": str(sorted_dates[-1]),
    }


def _calc_signal_consistency(
    strategy_signals: dict[str, dict[date, str]],
    symbol: str,
) -> float:
    """Calculate signal consistency — how often strategies agree on direction.

    Returns 0-1, where 1 = perfect agreement on all signal dates.
    """
    all_dates = set()
    for sigs in strategy_signals.values():
        all_dates.update(sigs.keys())

    if not all_dates:
        return 0.0

    agree_count = 0
    signal_dates = 0

    for d in all_dates:
        directions = []
        for sigs in strategy_signals.values():
            sig = sigs.get(d)
            if sig:
                directions.append(sig)

        if len(directions) >= 2:
            signal_dates += 1
            # All agree on same direction
            if len(set(directions)) == 1:
                agree_count += 1

    return round(agree_count / signal_dates, 4) if signal_dates > 0 else 0.5


# ── Trading plan extraction ──────────────────────────────────────

def _extract_trading_plan(
    symbol: str,
    metrics: dict,
    bars_by_date: dict[date, dict[str, Any]],
    strategy_ids: list[str],
    combo_method: str,
) -> dict:
    """Extract actionable trading plan from backtest results.

    Analyzes the backtest trades to determine:
    - Buy zones (price levels favorable for entry)
    - Sell zones (price levels favorable for exit)
    - Stop loss, take profit tiers
    - Position sizing rules
    - Timing hints
    """
    sorted_dates = sorted(bars_by_date.keys())
    if not sorted_dates:
        return {}

    # Get recent price data for reference
    last_prices = []
    for d in sorted_dates[-60:]:
        if symbol in bars_by_date[d]:
            last_prices.append(bars_by_date[d][symbol].close)

    if not last_prices:
        return {}

    current_price = last_prices[-1]
    price_high = max(last_prices)
    price_low = min(last_prices)

    # Calculate technical levels
    ma20 = sum(last_prices[-20:]) / min(20, len(last_prices)) if len(last_prices) >= 5 else current_price
    ma60 = sum(last_prices[-60:]) / min(60, len(last_prices)) if len(last_prices) >= 20 else current_price

    # ATR (14-day)
    atr_values = []
    for i in range(max(1, len(last_prices) - 14), len(last_prices)):
        if i > 0:
            tr = max(
                last_prices[i] - last_prices[i - 1],
                abs(last_prices[i] - last_prices[i - 1]),
            )
            # Simplified — use close-to-close range for daily ATR
            atr_values.append(abs(last_prices[i] - last_prices[i - 1]))
    atr = sum(atr_values) / len(atr_values) if atr_values else current_price * 0.02

    # Bollinger bands (20-period)
    bb_prices = last_prices[-20:] if len(last_prices) >= 20 else last_prices
    bb_mean = sum(bb_prices) / len(bb_prices)
    bb_std = (sum((p - bb_mean) ** 2 for p in bb_prices) / len(bb_prices)) ** 0.5
    bb_upper = bb_mean + 2 * bb_std
    bb_lower = bb_mean - 2 * bb_std

    # Buy zones — derived from backtest buy prices + technical analysis
    buy_prices = metrics.get("buy_prices", [])
    buy_zones = []

    # Zone 1: Near lower Bollinger Band
    buy_zones.append({
        "priceLevel": round(bb_lower, 2),
        "type": "support",
        "strength": 0.8,
        "description": f"布林带下轨支撑 (BB Lower: {bb_lower:.2f})",
    })

    # Zone 2: MA20 pullback
    if ma20 < current_price:
        buy_zones.append({
            "priceLevel": round(ma20, 2),
            "type": "pullback",
            "strength": 0.7,
            "description": f"20日均线回调支撑 (MA20: {ma20:.2f})",
        })

    # Zone 3: Strong support from recent low
    buy_zones.append({
        "priceLevel": round(price_low, 2),
        "type": "support",
        "strength": 0.6,
        "description": f"近60日低点支撑 ({price_low:.2f})",
    })

    # Zone 4: 2× ATR below current price
    atr_support = current_price - 2 * atr
    if atr_support > 0:
        buy_zones.append({
            "priceLevel": round(atr_support, 2),
            "type": "oversold",
            "strength": 0.5,
            "description": f"ATR超跌区 (当前价 - 2×ATR: {atr_support:.2f})",
        })

    # Sell zones
    sell_zones = []

    # Zone 1: Upper Bollinger Band
    sell_zones.append({
        "priceLevel": round(bb_upper, 2),
        "type": "overbought",
        "strength": 0.8,
        "description": f"布林带上轨压力 (BB Upper: {bb_upper:.2f})",
    })

    # Zone 2: Recent high resistance
    sell_zones.append({
        "priceLevel": round(price_high, 2),
        "type": "resistance",
        "strength": 0.7,
        "description": f"近60日高点阻力 ({price_high:.2f})",
    })

    # Zone 3: Target based on backtest average profit
    sell_prices = metrics.get("sell_prices", [])
    if buy_prices and sell_prices:
        avg_profit_pct = sum(
            (s - b) / b for b, s in zip(buy_prices, sell_prices) if b > 0
        ) / max(1, min(len(buy_prices), len(sell_prices)))
        target_price = current_price * (1 + avg_profit_pct)
        if target_price > current_price:
            sell_zones.append({
                "priceLevel": round(target_price, 2),
                "type": "target",
                "strength": 0.6,
                "description": f"回测平均盈利目标 (+{avg_profit_pct*100:.1f}%)",
            })

    # Stop loss
    max_dd = _sf(metrics.get("max_drawdown"), 15)
    stop_pct = min(max_dd * 0.5, 10)  # half of historical max drawdown, capped at 10%
    stop_price = current_price * (1 - stop_pct / 100)

    # Take profit tiers from backtest profit distribution
    tp_tiers = [
        {"targetPct": 5, "sellPortion": 0.3},
        {"targetPct": 10, "sellPortion": 0.3},
        {"targetPct": 20, "sellPortion": 0.4},
    ]

    # Adjust based on backtest performance
    ann_return = _sf(metrics.get("annualized_return"))
    if ann_return > 30:
        tp_tiers = [
            {"targetPct": 8, "sellPortion": 0.25},
            {"targetPct": 15, "sellPortion": 0.35},
            {"targetPct": 30, "sellPortion": 0.4},
        ]
    elif ann_return < 10:
        tp_tiers = [
            {"targetPct": 3, "sellPortion": 0.4},
            {"targetPct": 6, "sellPortion": 0.3},
            {"targetPct": 10, "sellPortion": 0.3},
        ]

    # Position sizing
    sharpe = _sf(metrics.get("sharpe_ratio"))
    position_method = "risk_pct"
    max_pct = 10
    initial_pct = 5
    if sharpe > 1.5:
        max_pct = 15
        initial_pct = 8
    elif sharpe < 0.5:
        max_pct = 8
        initial_pct = 3

    # Holding period
    holding = metrics.get("holding_days", {})

    # Timing hints — analyze day-of-week patterns from backtest trades
    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    buy_day_counts: dict[int, int] = defaultdict(int)  # weekday -> count
    sell_day_counts: dict[int, int] = defaultdict(int)
    buy_day_pnl: dict[int, list[float]] = defaultdict(list)

    buy_dates_raw = metrics.get("buy_dates", [])
    sell_dates_raw = metrics.get("sell_dates", [])
    buy_prices_list = metrics.get("buy_prices", [])
    sell_prices_list = metrics.get("sell_prices", [])

    for ds in buy_dates_raw:
        try:
            dt = datetime.strptime(str(ds)[:10], "%Y-%m-%d")
            buy_day_counts[dt.weekday()] += 1
        except Exception:
            pass

    for ds in sell_dates_raw:
        try:
            dt = datetime.strptime(str(ds)[:10], "%Y-%m-%d")
            sell_day_counts[dt.weekday()] += 1
        except Exception:
            pass

    # PnL by buy day-of-week
    for i, ds in enumerate(buy_dates_raw):
        try:
            dt = datetime.strptime(str(ds)[:10], "%Y-%m-%d")
            if i < len(sell_prices_list) and i < len(buy_prices_list):
                pnl_pct = (sell_prices_list[i] - buy_prices_list[i]) / buy_prices_list[i] if buy_prices_list[i] > 0 else 0
                buy_day_pnl[dt.weekday()].append(pnl_pct)
        except Exception:
            pass

    # Best entry days (sorted by avg PnL)
    best_entry_days = []
    avoid_days = []
    for wd in range(5):  # Mon-Fri only
        pnls = buy_day_pnl.get(wd, [])
        if pnls:
            avg_pnl = sum(pnls) / len(pnls)
            count = buy_day_counts.get(wd, 0)
            if avg_pnl > 0 and count >= 3:
                best_entry_days.append({
                    "day": day_names[wd],
                    "weekday": wd,
                    "avgPnl": round(avg_pnl * 100, 2),
                    "count": count,
                })
            elif avg_pnl < -0.01 and count >= 3:
                avoid_days.append({
                    "day": day_names[wd],
                    "weekday": wd,
                    "avgPnl": round(avg_pnl * 100, 2),
                    "count": count,
                })

    best_entry_days.sort(key=lambda x: x["avgPnl"], reverse=True)
    avoid_days.sort(key=lambda x: x["avgPnl"])

    timing_hints = {
        "bestEntryDays": best_entry_days[:3],
        "avoidDays": avoid_days[:2],
        "buyDayDistribution": {day_names[k]: v for k, v in sorted(buy_day_counts.items()) if k < 5},
        "sellDayDistribution": {day_names[k]: v for k, v in sorted(sell_day_counts.items()) if k < 5},
    }

    # Build combo strategy info
    strategy_names = metrics.get("strategy_names", strategy_ids)
    combo_strategies = []
    if combo_method == "gated":
        combo_strategies.append({
            "strategyId": strategy_ids[0],
            "strategyName": strategy_names[0] if strategy_names else strategy_ids[0],
            "weight": 1.0,
            "role": "primary",
        })
        for i, sid in enumerate(strategy_ids[1:], 1):
            combo_strategies.append({
                "strategyId": sid,
                "strategyName": strategy_names[i] if i < len(strategy_names) else sid,
                "weight": 0.5,
                "role": "filter",
            })
    else:
        weight = 1.0 / len(strategy_ids)
        for i, sid in enumerate(strategy_ids):
            combo_strategies.append({
                "strategyId": sid,
                "strategyName": strategy_names[i] if i < len(strategy_names) else sid,
                "weight": round(weight, 4),
                "role": "equal",
            })

    # Confidence calculation
    confidence = 0.0
    if sharpe > 0:
        confidence += min(sharpe * 0.2, 0.4)  # max 0.4 from sharpe
    win_rate = _sf(metrics.get("win_rate"))
    if win_rate > 45:
        confidence += min((win_rate - 45) / 55 * 0.3, 0.3)  # max 0.3 from win rate
    if _sf(metrics.get("signal_consistency")) > 0.3:
        confidence += min(metrics["signal_consistency"] * 0.3, 0.3)  # max 0.3 from consistency
    confidence = min(round(confidence, 4), 1.0)

    return {
        "symbol": symbol,
        "comboStrategies": combo_strategies,
        "comboMethod": combo_method,
        "buyZones": buy_zones,
        "sellZones": sell_zones,
        "addPositionRules": [
            {
                "condition": f"价格回调至买入价以下{stop_pct/2:.0f}%且趋势未破",
                "pct": 30,
                "trigger": f"price_drop_{stop_pct/2:.0f}pct",
                "description": "趋势内回调加仓",
            },
            {
                "condition": f"价格触及MA20且RSI<40",
                "pct": 20,
                "trigger": "ma20_rsi_low",
                "description": "均线支撑加仓",
            },
        ],
        "reducePositionRules": [
            {
                "condition": "盈利超过第一档止盈",
                "pct": 30,
                "trigger": f"profit_above_{tp_tiers[0]['targetPct']}pct",
                "description": "达第一档止盈减仓",
            },
            {
                "condition": "RSI>70 且接近上轨",
                "pct": 20,
                "trigger": "rsi_overbought",
                "description": "超买减仓",
            },
        ],
        "stopLoss": {
            "price": round(stop_price, 2),
            "type": "atr",
            "atrMultiple": 2.0,
            "pctFromEntry": round(stop_pct, 2),
        },
        "takeProfitTiers": tp_tiers,
        "positionSizing": {
            "method": position_method,
            "maxPct": max_pct,
            "initialPct": initial_pct,
            "maxScaleCount": 3,
        },
        "holdingPeriod": {
            "optimalDays": round(holding.get("avg", 10)),
            "minDays": holding.get("min", 1),
            "maxDays": holding.get("max", 60),
        },
        "timingHints": timing_hints,
        "confidence": confidence,
        "backtestMetrics": {
            "totalReturn": _sf(metrics.get("total_return")),
            "annualizedReturn": ann_return,
            "sharpeRatio": sharpe,
            "maxDrawdown": _sf(metrics.get("max_drawdown")),
            "winRate": win_rate,
            "profitFactor": _sf(metrics.get("profit_factor")),
            "totalTrades": metrics.get("total_trades", 0),
        },
        "equityCurve": _sample_equity_curve(
            metrics.get("equity_curve", []), sorted_dates, max_points=250
        ),
        "updatedAt": datetime.now().isoformat(),
    }


# ── Save results to DB ───────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stock_combo_optimization (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol            TEXT NOT NULL,
          stock_name        TEXT,
          combo_id          TEXT NOT NULL,
          strategy_ids      TEXT NOT NULL,
          strategy_names    TEXT,
          combo_method      TEXT NOT NULL DEFAULT 'voting',
          optimized_params  TEXT,
          composite_score   REAL NOT NULL,
          total_return      REAL,
          annualized_return REAL,
          sharpe_ratio      REAL,
          sortino_ratio     REAL,
          max_drawdown      REAL,
          win_rate          REAL,
          profit_factor     REAL,
          calmar_ratio      REAL,
          total_trades      INTEGER,
          signal_consistency REAL,
          backtest_start    TEXT,
          backtest_end      TEXT,
          trading_plan      TEXT,
          confidence        REAL DEFAULT 0,
          run_id            TEXT NOT NULL,
          created_at        TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE (symbol, combo_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sco_symbol ON stock_combo_optimization(symbol);
        CREATE INDEX IF NOT EXISTS idx_sco_run ON stock_combo_optimization(run_id);
        CREATE INDEX IF NOT EXISTS idx_sco_score ON stock_combo_optimization(composite_score DESC);
        CREATE INDEX IF NOT EXISTS idx_sco_confidence ON stock_combo_optimization(confidence DESC);
    """)


def _save_results(
    results: list[dict],
    run_id: str,
) -> None:
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_table(conn)
        for r in results:
            plan = r.get("trading_plan", {})
            conn.execute(
                """INSERT OR REPLACE INTO stock_combo_optimization
                   (symbol, stock_name, combo_id, strategy_ids, strategy_names,
                    combo_method, optimized_params, composite_score,
                    total_return, annualized_return, sharpe_ratio, sortino_ratio,
                    max_drawdown, win_rate, profit_factor, calmar_ratio,
                    total_trades, signal_consistency,
                    backtest_start, backtest_end, trading_plan, confidence, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r["symbol"], r.get("stock_name"),
                    r["combo_id"],
                    json.dumps(r["strategy_ids"]),
                    json.dumps(r.get("strategy_names", [])),
                    r["combo_method"], json.dumps(r.get("optimized_params", {})),
                    r["composite_score"],
                    r.get("total_return"), r.get("annualized_return"),
                    r.get("sharpe_ratio"), r.get("sortino_ratio"),
                    r.get("max_drawdown"), r.get("win_rate"),
                    r.get("profit_factor"), r.get("calmar_ratio"),
                    r.get("total_trades"), r.get("signal_consistency"),
                    r.get("backtest_start"), r.get("backtest_end"),
                    json.dumps(plan, ensure_ascii=False) if plan else None,
                    r.get("confidence", 0),
                    run_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ── Query helpers ────────────────────────────────────────────────

def _list_stocks(run_id: str = "", limit: int = 200) -> dict:
    """Return all stocks in a run with their best combo summary."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_table(conn)
        if not run_id:
            row = conn.execute(
                "SELECT run_id FROM stock_combo_optimization ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"ok": True, "stocks": [], "message": "暂无优化数据"}
            run_id = row["run_id"]

        rows = conn.execute(
            """SELECT symbol, stock_name,
                      MAX(composite_score) as best_score,
                      MAX(total_return) as best_return,
                      MAX(sharpe_ratio) as best_sharpe,
                      MIN(max_drawdown) as best_drawdown,
                      MAX(win_rate) as best_win_rate,
                      COUNT(*) as combo_count
               FROM stock_combo_optimization
               WHERE run_id = ?
               GROUP BY symbol
               ORDER BY best_score DESC
               LIMIT ?""",
            (run_id, limit),
        ).fetchall()

        return {
            "ok": True,
            "run_id": run_id,
            "stocks": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _query_stock(symbol: str, run_id: str = "") -> dict:
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_table(conn)
        if not run_id:
            row = conn.execute(
                "SELECT run_id FROM stock_combo_optimization ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"ok": True, "results": [], "message": "暂无优化数据"}
            run_id = row["run_id"]

        rows = conn.execute(
            "SELECT * FROM stock_combo_optimization WHERE symbol = ? AND run_id = ? ORDER BY composite_score DESC",
            (symbol, run_id),
        ).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            # Parse JSON fields
            for field in ("strategy_ids", "strategy_names", "optimized_params", "trading_plan"):
                if item.get(field) and isinstance(item[field], str):
                    try:
                        item[field] = json.loads(item[field])
                    except json.JSONDecodeError:
                        pass
            results.append(item)

        return {"ok": True, "run_id": run_id, "symbol": symbol, "results": results}
    finally:
        conn.close()


def _list_runs(limit: int = 20) -> dict:
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT run_id, MIN(created_at) as created_at,
                      COUNT(DISTINCT symbol) as stock_count,
                      COUNT(*) as combo_count,
                      ROUND(AVG(composite_score), 2) as avg_score
               FROM stock_combo_optimization
               GROUP BY run_id
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return {"ok": True, "runs": [dict(r) for r in rows]}
    finally:
        conn.close()


def _delete_run(run_id: str) -> dict:
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_table(conn)
        cur = conn.execute(
            "DELETE FROM stock_combo_optimization WHERE run_id = ?", (run_id,)
        )
        conn.commit()
        return {"ok": True, "deleted": cur.rowcount}
    finally:
        conn.close()


# ── Performance tracking ─────────────────────────────────────────

def _ensure_perf_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS combo_perf_tracking (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol          TEXT NOT NULL,
          run_id          TEXT NOT NULL,
          combo_id        TEXT NOT NULL,
          snapshot_date   TEXT NOT NULL,
          current_price   REAL,
          expected_return REAL,
          actual_return   REAL,
          hit_buy_zone    INTEGER DEFAULT 0,
          hit_sell_zone   INTEGER DEFAULT 0,
          hit_stop_loss   INTEGER DEFAULT 0,
          hit_take_profit INTEGER DEFAULT 0,
          confidence_adj  REAL DEFAULT 0,
          notes           TEXT,
          created_at      TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE (symbol, run_id, snapshot_date)
        );
        CREATE INDEX IF NOT EXISTS idx_cpt_symbol ON combo_perf_tracking(symbol);
        CREATE INDEX IF NOT EXISTS idx_cpt_run ON combo_perf_tracking(run_id);
    """)


def _track_performance(symbols: list[str] | None = None) -> dict:
    """Snapshot current prices vs plan predictions for tracked stocks.

    Compares: current price vs buy/sell zones, stop loss, take profit levels.
    Calculates actual return if entry price is known.
    """
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_table(conn)
        _ensure_perf_table(conn)

        # Get latest run_id
        row = conn.execute(
            "SELECT run_id FROM stock_combo_optimization ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"ok": True, "tracked": 0, "message": "无优化数据"}
        run_id = row["run_id"]

        # Get optimized stocks
        where_clause = f"AND symbol IN ({','.join('?' * len(symbols))})" if symbols else ""
        params = [run_id] + (symbols or [])
        rows = conn.execute(
            f"""SELECT symbol, combo_id, trading_plan, composite_score
                FROM stock_combo_optimization
                WHERE run_id = ? {where_clause}
                ORDER BY composite_score DESC""",
            params,
        ).fetchall()

        if not rows:
            return {"ok": True, "tracked": 0, "message": "无需追踪的股票"}

        # Get current prices
        tracked_symbols = list(set(r["symbol"] for r in rows))
        try:
            from data_providers.akshare_provider import get_klines_batch
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
            klines = get_klines_batch(tracked_symbols, start_date, end_date, market="cn_stock", kline_days=0)
        except Exception:
            klines = {}

        current_prices: dict[str, float] = {}
        for sym, bars in klines.items():
            if bars:
                last = bars[-1]
                current_prices[sym] = float(last.get("close", 0))

        snapshot_date = datetime.now().strftime("%Y-%m-%d")
        results = []
        tracked = 0

        for r in rows:
            sym = r["symbol"]
            price = current_prices.get(sym)
            if not price or price <= 0:
                continue

            plan_raw = r["trading_plan"]
            try:
                plan = json.loads(plan_raw) if isinstance(plan_raw, str) else (plan_raw or {})
            except Exception:
                plan = {}

            # Check zone hits
            buy_zones = plan.get("buyZones", [])
            sell_zones = plan.get("sellZones", [])
            stop_loss = plan.get("stopLoss", {})

            hit_buy = 0
            hit_sell = 0
            hit_sl = 0
            hit_tp = 0

            for z in buy_zones:
                if price <= z.get("priceLevel", 0):
                    hit_buy = 1
                    break

            for z in sell_zones:
                if price >= z.get("priceLevel", 0):
                    hit_sell = 1
                    break

            if stop_loss and price <= stop_loss.get("price", 0):
                hit_sl = 1

            tp_tiers = plan.get("takeProfitTiers", [])
            metrics = plan.get("backtestMetrics", {})
            # Expected vs actual: if we can estimate entry from buy zones
            entry_est = buy_zones[0]["priceLevel"] if buy_zones else price
            expected_ret = _sf(metrics.get("annualizedReturn"))
            actual_ret = ((price - entry_est) / entry_est * 100) if entry_est > 0 else 0

            # Adjust confidence based on reality match
            conf_adj = 0.0
            if actual_ret > 0 and expected_ret > 0:
                conf_adj = 0.05  # prediction directionally correct
            elif actual_ret < -10:
                conf_adj = -0.1  # significant deviation

            conn.execute(
                """INSERT OR REPLACE INTO combo_perf_tracking
                   (symbol, run_id, combo_id, snapshot_date, current_price,
                    expected_return, actual_return,
                    hit_buy_zone, hit_sell_zone, hit_stop_loss, hit_take_profit,
                    confidence_adj)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sym, run_id, r["combo_id"], snapshot_date, price,
                 expected_ret, round(actual_ret, 2),
                 hit_buy, hit_sell, hit_sl, hit_tp, conf_adj),
            )
            tracked += 1
            results.append({
                "symbol": sym,
                "price": price,
                "expected_return": expected_ret,
                "actual_return": round(actual_ret, 2),
                "hit_buy_zone": bool(hit_buy),
                "hit_sell_zone": bool(hit_sell),
                "hit_stop_loss": bool(hit_sl),
                "confidence_adj": conf_adj,
            })

        conn.commit()
        return {"ok": True, "tracked": tracked, "snapshot_date": snapshot_date, "results": results}
    finally:
        conn.close()


def _get_perf_history(symbol: str = "", limit: int = 30) -> dict:
    """Get performance tracking history for a symbol or all."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_perf_table(conn)
        if symbol:
            rows = conn.execute(
                "SELECT * FROM combo_perf_tracking WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT symbol, snapshot_date, current_price, expected_return, actual_return,
                          hit_buy_zone, hit_sell_zone, hit_stop_loss, confidence_adj
                   FROM combo_perf_tracking ORDER BY snapshot_date DESC, symbol LIMIT ?""",
                (limit,),
            ).fetchall()
        return {"ok": True, "history": [dict(r) for r in rows]}
    finally:
        conn.close()


# ── Main: run optimization ───────────────────────────────────────

def run_stock_combo_optimization(
    symbols: list[str] | None = None,
    universe: str = "csi300",
    backtest_years: int = 5,
    top_k_strategies: int = 5,
    max_combo_size: int = 3,
    initial_capital: float = 1_000_000,
    combo_methods: list[str] | None = None,
    fitness_batch_id: str = "",
) -> dict:
    """主入口：对每只股票执行多策略组合优化。"""
    run_id = f"combo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    t0 = time.time()

    if combo_methods is None:
        combo_methods = ["voting", "weighted", "gated"]

    _write_progress({
        "run_id": run_id,
        "status": "starting",
        "phase": "初始化...",
        "completed": 0,
        "total": 0,
        "elapsed_seconds": 0,
    })

    # Step 1: Determine target stocks
    if not symbols:
        # Load from fitness DB — stocks that have fitness data
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        try:
            if not fitness_batch_id:
                row = conn.execute(
                    "SELECT batch_id FROM stock_strategy_fitness ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    fitness_batch_id = row[0]

            if fitness_batch_id:
                rows = conn.execute(
                    "SELECT DISTINCT symbol FROM stock_strategy_fitness WHERE batch_id = ?",
                    (fitness_batch_id,),
                ).fetchall()
                symbols = [r[0] for r in rows]
            else:
                symbols = []
        finally:
            conn.close()

    if not symbols:
        return {"ok": False, "error": "无可用股票。请先运行策略适配分析 (Fitness Analysis)。"}

    print(f"[Step 1] 目标股票: {len(symbols)} 只", file=sys.stderr)

    # Step 2: Load kline data
    print(f"[Step 2] 加载K线数据...", file=sys.stderr)
    _write_progress({
        "run_id": run_id,
        "status": "loading_klines",
        "phase": f"加载K线数据 (0/{len(symbols)})...",
        "completed": 0,
        "total": len(symbols),
        "elapsed_seconds": int(time.time() - t0),
    })

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=backtest_years * 365)).strftime("%Y-%m-%d")

    try:
        from data_providers.akshare_provider import get_klines_batch
    except ImportError:
        return {"ok": False, "error": "无法导入数据提供器"}

    all_klines: dict[str, list[dict]] = {}
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        batch_klines = get_klines_batch(batch, start_date, end_date, market="cn_stock", kline_days=0)
        all_klines.update(batch_klines)
        loaded = min(i + batch_size, len(symbols))
        # Count how many in this batch missed cache (needed remote fetch)
        missed = sum(1 for s in batch if s not in all_klines or not all_klines.get(s))
        print(f"  K线加载: {loaded}/{len(symbols)} (本批缓存未命中: {missed})", file=sys.stderr)
        _write_progress({
            "run_id": run_id,
            "status": "loading_klines",
            "phase": f"加载K线数据 ({loaded}/{len(symbols)})",
            "completed": loaded,
            "total": len(symbols),
            "elapsed_seconds": int(time.time() - t0),
        })
        # Rate limit between batches to avoid API throttling
        if missed > 10 and i + batch_size < len(symbols):
            time.sleep(2)

    valid_symbols = [s for s in symbols if s in all_klines and len(all_klines[s]) >= 60]
    print(f"[Step 2] 有效股票: {len(valid_symbols)}/{len(symbols)}", file=sys.stderr)

    if not valid_symbols:
        return {"ok": False, "error": "所有股票K线数据不足"}

    # Step 3: Load fundamentals and build bars_by_date
    print("[Step 3] 构建回测数据...", file=sys.stderr)
    _write_progress({
        "run_id": run_id,
        "status": "building_bars",
        "phase": "构建回测数据...",
        "completed": 0,
        "total": len(valid_symbols),
        "elapsed_seconds": int(time.time() - t0),
    })

    # Reuse fitness script's loader
    sys.path.insert(0, os.path.join(os.path.dirname(_PYTHON_ROOT), "python"))
    from aipha.domain.models import Bar

    # Build bars_by_date
    bars_by_date: dict[date, dict[str, Any]] = {}
    for symbol_code, klines in all_klines.items():
        if symbol_code not in valid_symbols:
            continue
        for bar_dict in klines:
            ts = bar_dict.get("timestamp", 0)
            if isinstance(ts, (int, float)) and ts > 1e12:
                dt_obj = datetime.fromtimestamp(ts / 1000)
            elif isinstance(ts, (int, float)):
                dt_obj = datetime.fromtimestamp(ts)
            elif isinstance(ts, str):
                dt_obj = datetime.strptime(ts[:10], "%Y-%m-%d")
            else:
                continue
            d = dt_obj.date()
            bar = Bar(
                symbol=symbol_code,
                start_ts=dt_obj,
                end_ts=dt_obj.replace(hour=15, minute=0, second=0),
                open=_sf(bar_dict.get("open")),
                high=_sf(bar_dict.get("high")),
                low=_sf(bar_dict.get("low")),
                close=_sf(bar_dict.get("close")),
                volume=_sf(bar_dict.get("volume")),
            )
            if d not in bars_by_date:
                bars_by_date[d] = {}
            bars_by_date[d][symbol_code] = bar

    print(f"[Step 3] bars_by_date: {len(bars_by_date)} 交易日", file=sys.stderr)

    # Step 4: Per-stock combo optimization (PARALLEL)
    print(f"[Step 4] 开始逐股优化 (并行)...", file=sys.stderr)

    # Pre-filter bars per symbol to avoid passing all 765 stocks to each backtest
    print(f"  预构建单股 bars...", file=sys.stderr)
    stock_bars: dict[str, dict[date, dict[str, Any]]] = {}
    for sym in valid_symbols:
        stock_bars[sym] = _filter_bars_for_symbol(bars_by_date, sym)

    # Free the big shared dict to save memory
    del bars_by_date

    def _optimize_one_stock(symbol_code: str) -> dict | None:
        """Optimize combos for a single stock. Runs in thread pool."""
        sym_bars = stock_bars[symbol_code]
        if not sym_bars:
            return None

        top_strategies = _get_top_strategies_for_stock(
            symbol_code, top_k=top_k_strategies, batch_id=fitness_batch_id
        )
        if len(top_strategies) < 2:
            return None

        strategy_ids = [s["strategy_id"] for s in top_strategies]
        combos = _generate_combos(strategy_ids, max_combo_size, combo_methods)
        if not combos:
            return None

        best_combo = None
        best_score = -999

        for combo in combos:
            metrics = _grid_search_combo(
                symbol_code,
                combo["strategy_ids"],
                combo["method"],
                sym_bars,
                initial_capital,
            )
            if not metrics:
                continue

            score = composite_score(metrics)
            sharpe = _sf(metrics.get("sharpe_ratio"))
            win_rate = _sf(metrics.get("win_rate"))
            if sharpe < 0.3 and win_rate < 40:
                continue

            if score > best_score:
                best_score = score
                best_combo = {
                    **combo,
                    "metrics": metrics,
                    "composite_score": score,
                }

        if not best_combo:
            return None

        plan = _extract_trading_plan(
            symbol_code,
            best_combo["metrics"],
            sym_bars,
            best_combo["strategy_ids"],
            best_combo["method"],
        )

        stock_name = None
        try:
            db_path = _get_db_path()
            cn = sqlite3.connect(db_path)
            row = cn.execute(
                "SELECT stock_name FROM stock_strategy_fitness WHERE symbol = ? AND stock_name IS NOT NULL LIMIT 1",
                (symbol_code,),
            ).fetchone()
            if row:
                stock_name = row[0]
            cn.close()
        except Exception:
            pass

        m = best_combo["metrics"]
        return {
            "symbol": symbol_code,
            "stock_name": stock_name,
            "combo_id": best_combo["combo_id"],
            "strategy_ids": best_combo["strategy_ids"],
            "strategy_names": m.get("strategy_names", []),
            "combo_method": best_combo["method"],
            "optimized_params": m.get("optimized_params", {}),
            "composite_score": best_combo["composite_score"],
            "total_return": m.get("total_return"),
            "annualized_return": m.get("annualized_return"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "sortino_ratio": m.get("sortino_ratio"),
            "max_drawdown": m.get("max_drawdown"),
            "win_rate": m.get("win_rate"),
            "profit_factor": m.get("profit_factor"),
            "calmar_ratio": m.get("calmar_ratio"),
            "total_trades": m.get("total_trades"),
            "signal_consistency": m.get("signal_consistency"),
            "backtest_start": m.get("backtest_start"),
            "backtest_end": m.get("backtest_end"),
            "trading_plan": plan,
            "confidence": plan.get("confidence", 0),
        }

    # Run with thread pool (threads share memory; GIL released during I/O + numpy)
    import concurrent.futures

    all_results: list[dict] = []
    completed = 0
    max_workers = min(os.cpu_count() or 4, 8)
    print(f"  并行线程数: {max_workers}", file=sys.stderr)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sym = {
            executor.submit(_optimize_one_stock, sym): sym
            for sym in valid_symbols
        }

        for future in concurrent.futures.as_completed(future_to_sym):
            symbol_code = future_to_sym[future]
            completed += 1

            _write_progress({
                "run_id": run_id,
                "status": "running",
                "phase": f"优化完成 {completed}/{len(valid_symbols)}",
                "completed": completed,
                "total": len(valid_symbols),
                "current_symbol": symbol_code,
                "result_count": len(all_results),
                "elapsed_seconds": int(time.time() - t0),
            })

            try:
                result = future.result()
                if result:
                    all_results.append(result)
                    print(f"  [OK] {symbol_code}: score={result['composite_score']:.2f}, "
                          f"method={result['combo_method']}",
                          file=sys.stderr)
            except Exception as e:
                print(f"  [ERR] {symbol_code}: {e}", file=sys.stderr)

    # Step 5: Save to DB
    print(f"[Step 5] 保存结果 ({len(all_results)} 条)...", file=sys.stderr)
    _write_progress({
        "run_id": run_id,
        "status": "saving",
        "phase": "保存结果...",
        "completed": len(valid_symbols),
        "total": len(valid_symbols),
        "elapsed_seconds": int(time.time() - t0),
    })

    if all_results:
        _save_results(all_results, run_id)

    elapsed = int(time.time() - t0)
    _write_progress({
        "run_id": run_id,
        "status": "done",
        "phase": "完成",
        "completed": len(valid_symbols),
        "total": len(valid_symbols),
        "result_count": len(all_results),
        "elapsed_seconds": elapsed,
    })

    # Prepare response (without equity curves to keep JSON small)
    response_results = []
    for r in all_results:
        resp = {k: v for k, v in r.items() if k != "equity_curve"}
        response_results.append(resp)

    return {
        "ok": True,
        "run_id": run_id,
        "total_stocks": len(valid_symbols),
        "optimized_count": len(all_results),
        "elapsed_seconds": elapsed,
        "results": response_results,
    }


# ── Main entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    action = config.get("action", "run")

    try:
        if action == "run":
            result = run_stock_combo_optimization(
                symbols=config.get("symbols"),
                universe=config.get("universe", "csi300"),
                backtest_years=config.get("backtest_years", 5),
                top_k_strategies=config.get("top_k_strategies", 5),
                max_combo_size=config.get("max_combo_size", 3),
                initial_capital=config.get("initial_capital", 1_000_000),
                combo_methods=config.get("combo_methods"),
                fitness_batch_id=config.get("fitness_batch_id", ""),
            )
        elif action == "status":
            result = _read_progress()
        elif action == "query_stock":
            symbol = config.get("symbol", "")
            if not symbol:
                result = {"error": "需要 symbol 参数"}
            else:
                result = _query_stock(symbol, config.get("run_id", ""))
        elif action == "list_stocks":
            result = _list_stocks(
                config.get("run_id", ""),
                config.get("limit", 200),
            )
        elif action == "list_runs":
            result = _list_runs(config.get("limit", 20))
        elif action == "delete_run":
            rid = config.get("run_id", "")
            if not rid:
                result = {"error": "需要 run_id 参数"}
            else:
                result = _delete_run(rid)
        elif action == "track_performance":
            syms = config.get("symbols")
            result = _track_performance(syms if isinstance(syms, list) else None)
        elif action == "perf_history":
            result = _get_perf_history(
                config.get("symbol", ""),
                config.get("limit", 30),
            )
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))
