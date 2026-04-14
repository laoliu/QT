"""Aipha 回测引擎 — QT Python Bridge 入口。

调用方式: QUANT_CONFIG='{...}' python -m engines.aipha_backtest_engine
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_dir)
if _parent not in sys.path:
    sys.path.insert(0, _parent)


def run(config: dict) -> dict:
    action = config.get("action", "run")

    if action == "run":
        return _run_backtest(config)
    elif action == "run_from_yaml":
        return _run_from_yaml(config)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


def _run_backtest(config: dict) -> dict:
    from aipha.backtest.engine import BacktestConfig, BacktestEngine
    from aipha.execution.fill_models import create_fill_model
    from aipha.execution.slippage import create_slippage_model
    from aipha.execution.fees import FeeModel
    from aipha.data.qt_adapter import create_provider
    from aipha.domain.models import Bar, Side
    from aipha.strategy.base import Strategy, StrategyContext, OrderIntent, IntentType

    # 解析参数
    strategy_name = config.get("strategyId", "buy_and_hold")
    symbols = config.get("symbols", [])
    start = date.fromisoformat(config["startDate"])
    end = date.fromisoformat(config["endDate"])
    initial_capital = config.get("initialCash", 1_000_000)
    source = config.get("dataSource", "akshare")

    # 创建数据提供者
    provider = create_provider(source)

    # 加载数据
    bars_by_date: dict[date, dict[str, Bar]] = {}
    for symbol in symbols:
        try:
            bars = provider.load_bars(symbol=symbol, start=start, end=end, frequency="1d")
            for bar in bars:
                d = bar.start_ts.date() if isinstance(bar.start_ts, datetime) else bar.start_ts
                if d not in bars_by_date:
                    bars_by_date[d] = {}
                bars_by_date[d][symbol] = bar
        except Exception as e:
            return {"ok": False, "error": f"加载 {symbol} 数据失败: {e}"}

    if not bars_by_date:
        return {"ok": False, "error": "没有获取到任何数据"}

    # 创建策略
    from aipha.strategy.registry import _registry
    strategy_cls = _registry.get(strategy_name)
    if strategy_cls:
        strategy = _registry.create(strategy_name, **config.get("parameters", {}))
    else:
        # 使用内置简单策略
        strategy = _create_simple_strategy(strategy_name, config.get("parameters", {}))

    # 创建引擎组件
    bt_config = BacktestConfig(
        initial_capital=initial_capital,
        commission_rate=config.get("commission", 8.54e-05),
        slippage_bps=config.get("slippageBps", 10.0),
    )
    fill_model = create_fill_model(config.get("fillModel", "next_bar_open"))
    slippage = create_slippage_model(config.get("slippageModel", "fixed"), bps=bt_config.slippage_bps)
    fee_model = FeeModel(commission_rate=bt_config.commission_rate)

    # 运行回测
    engine = BacktestEngine(
        config=bt_config,
        strategy=strategy,
        fill_model=fill_model,
        slippage_model=slippage,
        fee_model=fee_model,
    )
    result = engine.run(bars_by_date)

    # 构建返回
    snapshots = [
        {
            "date": s.trade_date.isoformat(),
            "nav": round(s.nav, 2),
            "cash": round(s.cash, 2),
            "dailyReturn": round(s.daily_return, 6),
            "cumulativeReturn": round(s.cumulative_return, 6),
            "positionCount": len(s.positions),
        }
        for s in result.daily_snapshots
    ]

    trades = [
        {
            "date": t.trade_date.isoformat(),
            "symbol": t.symbol,
            "side": t.side.value,
            "qty": t.qty,
            "price": round(t.price, 4),
            "fees": round(t.fees.total, 4),
        }
        for t in result.trades
    ]

    return {
        "ok": True,
        "data": {
            "strategyId": strategy_name,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "initialCapital": initial_capital,
            "finalValue": round(result.final_nav, 2),
            "totalReturn": round((result.final_nav / initial_capital - 1) * 100, 2),
            "totalTrades": result.total_trades,
            "totalFees": round(result.total_fees.total, 2),
            "sharpeRatio": _compute_sharpe(result.daily_snapshots),
            "maxDrawdown": _compute_mdd(result.daily_snapshots),
            "dailySnapshots": snapshots,
            "trades": trades,
        },
    }


def _run_from_yaml(config: dict) -> dict:
    yaml_path = config.get("yamlPath")
    if not yaml_path:
        return {"ok": False, "error": "需要 yamlPath 参数"}

    from aipha.config.config_parser import load_config, validate_config
    run_config = load_config(yaml_path)
    errors = validate_config(run_config)
    if errors:
        return {"ok": False, "error": f"配置验证失败: {'; '.join(errors)}"}

    # 转换为标准 backtest config 并运行
    bt_config = {
        "action": "run",
        "strategyId": run_config.strategy.name,
        "symbols": run_config.data.symbols or [],
        "startDate": run_config.data.start_date.isoformat(),
        "endDate": run_config.data.end_date.isoformat(),
        "initialCash": run_config.initial_capital,
        "dataSource": run_config.data.source,
        "commission": run_config.cost.commission_rate,
        "slippageBps": run_config.cost.slippage_bps,
        "fillModel": run_config.fill_model.name,
        "parameters": run_config.strategy.params,
    }
    return _run_backtest(bt_config)


def _create_simple_strategy(name: str, params: dict):
    """内置简单策略"""
    from aipha.strategy.base import Strategy, OrderIntent, StrategyContext, IntentType
    from aipha.domain.models import Bar, Side

    class BuyAndHoldStrategy(Strategy):
        name = "buy_and_hold"

        def __init__(self, **kwargs):
            self._bought = False

        def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
            if self._bought:
                return []
            self._bought = True
            intents = []
            n = len(bars)
            if n == 0:
                return []
            per_stock_cash = context.cash * 0.95 / n
            for symbol, bar in bars.items():
                if bar.close > 0:
                    qty = int(per_stock_cash / bar.close / 100) * 100
                    if qty > 0:
                        intents.append(OrderIntent(
                            symbol=symbol, side=Side.BUY, qty=qty,
                            intent_type=IntentType.ORDER, reason="初始建仓",
                        ))
            return intents

    return BuyAndHoldStrategy(**params)


def _compute_sharpe(snapshots) -> float:
    if len(snapshots) < 2:
        return 0.0
    returns = [s.daily_return for s in snapshots if s.daily_return != 0]
    if not returns:
        return 0.0
    import statistics
    mean = statistics.mean(returns)
    std = statistics.stdev(returns) if len(returns) > 1 else 1e-10
    return round(mean / std * (252 ** 0.5), 4) if std > 0 else 0.0


def _compute_mdd(snapshots) -> float:
    if not snapshots:
        return 0.0
    navs = [s.nav for s in snapshots]
    peak = navs[0]
    max_dd = 0.0
    for nav in navs:
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return round(max_dd * 100, 2)


if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG", "{}")
        config = json.loads(raw)
        result = run(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()}, ensure_ascii=False))
