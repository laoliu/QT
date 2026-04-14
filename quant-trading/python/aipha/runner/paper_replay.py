"""模拟盘历史回放 — 用历史数据驱动 LiveRunner 纸上交易"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _load_paper_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def _load_replay_data(config: dict, days: int) -> dict[date, dict[str, Any]]:
    """加载 N 天历史数据用于回放

    Returns: {date -> {symbol -> bar_data}}
    """
    from aipha.data.provider_base import DataProviderBase

    symbols = config.get("symbols", [])
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    result: dict[date, dict[str, Any]] = {}
    logger.info("加载回放数据: %s ~ %s, %d symbols", start_date, end_date, len(symbols))
    # 子类需实现具体的数据加载
    return result


def _build_live_runner(config: dict) -> tuple[Any, Any]:
    """创建 LiveRunner + Strategy 实例"""
    from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode

    runner_cfg = RunnerConfig(
        strategy_name=config.get("strategy_name", "unknown"),
        symbols=config.get("symbols", []),
        initial_capital=config.get("initial_capital", 1_000_000),
        mode=RunnerMode.PAPER,
    )
    runner = LiveRunner(runner_cfg)

    # 策略创建
    strategy = None
    strategy_name = config.get("strategy_name", "")
    if strategy_name:
        try:
            from aipha.strategy.registry import StrategyRegistry
            strategy = StrategyRegistry.create(strategy_name, config.get("strategy_params", {}))
        except Exception as e:
            logger.warning("策略创建失败 %s: %s", strategy_name, e)

    return runner, strategy


def run_paper_replay(
    config_path: str | Path,
    *,
    days: int = 60,
) -> dict[str, Any]:
    """历史数据回放模拟盘

    Parameters
    ----------
    config_path : YAML 配置路径
    days : 回放天数

    Returns
    -------
    dict with replay results (nav_series, trades, metrics)
    """
    path = Path(config_path)
    config = _load_paper_config(path)
    daily_data = _load_replay_data(config, days)
    runner, strategy = _build_live_runner(config)

    results: list[dict] = []
    nav_series: list[float] = [config.get("initial_capital", 1_000_000)]

    for dt in sorted(daily_data.keys()):
        bars = daily_data[dt]
        # 每日驱动策略
        if strategy and hasattr(strategy, "on_bar"):
            for symbol, bar in bars.items():
                strategy.on_bar(bar)

        # 记录每日净值
        if runner and hasattr(runner, "portfolio_value"):
            nav_series.append(runner.portfolio_value)
        results.append({"date": str(dt), "n_bars": len(bars)})

    logger.info("回放完成: %d 交易日", len(daily_data))
    return {
        "days_replayed": len(daily_data),
        "nav_series": nav_series,
        "daily_results": results,
    }
