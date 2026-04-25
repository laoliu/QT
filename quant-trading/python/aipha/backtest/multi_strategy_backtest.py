"""多策略组合回测"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ALLOCATION_MODE_MAP = {
    "equal": "等权",
    "risk_parity": "风险平价",
    "inverse_vol": "波动率倒数",
    "max_sharpe": "最大夏普",
    "min_variance": "最小方差",
    "target_vol": "目标波动率",
    "max_diversification": "最大分散化",
    "custom": "自定义权重",
}


@dataclass
class SubStrategyResult:
    name: str
    weight: float
    nav_series: pd.Series
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    total_trades: int = 0
    total_fees: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": round(self.weight, 4),
            "annual_return": round(self.annual_return, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "sharpe": round(self.sharpe, 4),
            "total_trades": self.total_trades,
            "total_fees": round(self.total_fees, 2),
        }


@dataclass
class MultiStrategyResult:
    sub_results: list[SubStrategyResult]
    combined_nav: pd.Series
    combined_annual_return: float = 0.0
    combined_max_drawdown: float = 0.0
    combined_sharpe: float = 0.0
    correlation_matrix: pd.DataFrame | None = None
    allocation_mode: str = "equal"
    rebalance_freq: str = "monthly"

    def to_dict(self) -> dict:
        d = {
            "allocation_mode": self.allocation_mode,
            "rebalance_freq": self.rebalance_freq,
            "combined_annual_return": round(self.combined_annual_return, 6),
            "combined_max_drawdown": round(self.combined_max_drawdown, 6),
            "combined_sharpe": round(self.combined_sharpe, 4),
            "sub_results": [s.to_dict() for s in self.sub_results],
        }
        if self.correlation_matrix is not None:
            d["correlation_matrix"] = self.correlation_matrix.round(4).to_dict()
        return d


def _combine_nav_series(
    nav_list: list[pd.Series],
    weights: list[float],
    rebalance_freq: str = "monthly",
) -> pd.Series:
    """合并多条净值曲线为组合净值"""
    if not nav_list:
        return pd.Series(dtype=float)
    # 对齐日期
    aligned = pd.DataFrame({f"s{i}": s for i, s in enumerate(nav_list)}).ffill()
    if aligned.empty:
        return pd.Series(dtype=float)

    ret_df = aligned.pct_change().fillna(0)
    w = np.array(weights) / np.sum(weights)

    combo_ret = ret_df.values @ w
    combo_nav = pd.Series((1 + combo_ret).cumprod(), index=aligned.index)
    return combo_nav


def _compute_portfolio_metrics(nav: pd.Series) -> dict:
    if nav.empty or len(nav) < 2:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = (nav.index[-1] - nav.index[0]).days or 1
    ann_ret = (1 + total_ret) ** (365 / days) - 1

    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    max_dd = dd.min()

    daily_ret = nav.pct_change().dropna()
    if daily_ret.std() > 0:
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
    else:
        sharpe = 0.0

    return {"annual_return": ann_ret, "max_drawdown": abs(max_dd), "sharpe": sharpe}


def _compute_correlation(nav_list: list[pd.Series], names: list[str]) -> pd.DataFrame:
    if len(nav_list) < 2:
        return pd.DataFrame()
    aligned = pd.DataFrame({n: s for n, s in zip(names, nav_list)}).ffill()
    return aligned.pct_change().dropna().corr()


def parse_multi_strategy_config(config: dict) -> tuple[list[dict], list[float], str, str]:
    """解析多策略配置

    Returns: (strategy_configs, weights, allocation_mode, rebalance_freq)
    """
    strategies = config.get("strategies", [])
    mode = config.get("allocation_mode", "equal")
    freq = config.get("rebalance_freq", "monthly")

    cfgs = []
    weights = []
    for s in strategies:
        cfgs.append(s.get("config", {}))
        weights.append(float(s.get("weight", 1.0)))

    if mode == "equal":
        weights = [1.0 / len(cfgs)] * len(cfgs) if cfgs else []

    return cfgs, weights, mode, freq


def build_sub_strategy_config(base_config: dict, override: dict) -> dict:
    merged = {**base_config}
    merged.update(override)
    return merged


def run_multi_strategy_backtest(
    sub_results: list[SubStrategyResult],
    allocation_mode: str = "equal",
    rebalance_freq: str = "monthly",
) -> MultiStrategyResult:
    """汇总多个子策略回测结果为组合

    Parameters
    ----------
    sub_results : 各子策略独立回测结果
    allocation_mode : 权重分配模式
    rebalance_freq : 再平衡频率
    """
    names = [s.name for s in sub_results]
    navs = [s.nav_series for s in sub_results]
    weights = [s.weight for s in sub_results]

    combined = _combine_nav_series(navs, weights, rebalance_freq)
    metrics = _compute_portfolio_metrics(combined)
    corr = _compute_correlation(navs, names)

    return MultiStrategyResult(
        sub_results=sub_results,
        combined_nav=combined,
        combined_annual_return=metrics["annual_return"],
        combined_max_drawdown=metrics["max_drawdown"],
        combined_sharpe=metrics["sharpe"],
        correlation_matrix=corr if not corr.empty else None,
        allocation_mode=allocation_mode,
        rebalance_freq=rebalance_freq,
    )


# ── Top-10 策略组合回测 ──────────────────────────────────────────


def _get_db_path() -> str:
    data_dir = os.environ.get("QUANT_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".openclaw", "quant-data"
    )
    return os.path.join(data_dir, "quant-trading.db")


def _get_combo_progress_path() -> str:
    """进度文件路径 — 与 fitness-progress.json 同目录。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_root = os.path.dirname(os.path.dirname(script_dir))  # aipha/backtest -> aipha -> python
    logs_dir = os.path.join(os.path.dirname(python_root), "logs")
    return os.path.join(logs_dir, "combo-backtest-progress.json")


def _write_combo_progress(data: dict) -> None:
    p = _get_combo_progress_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _read_combo_progress() -> dict:
    try:
        with open(_get_combo_progress_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle"}


def _load_global_top_strategies(batch_id: str = "", top_n: int = 10) -> list[dict]:
    """从 DB 加载 global_strategy_ranking 的 top N 策略。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not batch_id:
            row = conn.execute(
                "SELECT batch_id FROM global_strategy_ranking ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            batch_id = row["batch_id"] if row else ""
        if not batch_id:
            return []
        rows = conn.execute(
            "SELECT * FROM global_strategy_ranking WHERE batch_id = ? ORDER BY rank ASC LIMIT ?",
            (batch_id, top_n),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_strategy_symbols(batch_id: str, strategy_ids: list[str], top_per_strategy: int = 10) -> list[str]:
    """从 stock_strategy_fitness 获取各策略的最优股票（每策略取 top N），合并去重。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in strategy_ids)
        rows = conn.execute(
            f"""
            SELECT DISTINCT symbol FROM (
                SELECT symbol,
                       ROW_NUMBER() OVER (PARTITION BY strategy_id ORDER BY composite_score DESC) AS rn
                FROM stock_strategy_fitness
                WHERE batch_id = ? AND strategy_id IN ({placeholders})
            ) WHERE rn <= ?
            """,
            [batch_id] + strategy_ids + [top_per_strategy],
        ).fetchall()
        symbols = [r[0] for r in rows]
        # 如果 stock_strategy_fitness 无数据，回退到全局前 N 只股票
        if not symbols:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_strategy_fitness WHERE batch_id = ? LIMIT ?",
                (batch_id, top_per_strategy * len(strategy_ids)),
            ).fetchall()
            symbols = [r[0] for r in rows]
        logger.info("_load_strategy_symbols: %d strategies × top %d → %d unique symbols",
                     len(strategy_ids), top_per_strategy, len(symbols))
        return symbols
    finally:
        conn.close()


def _load_strategy_symbols_map(batch_id: str, strategy_ids: list[str], top_per_strategy: int = 10) -> dict[str, list[str]]:
    """获取每个策略的最优股票列表（返回 {strategy_id: [symbols]}）。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in strategy_ids)
        rows = conn.execute(
            f"""
            SELECT strategy_id, symbol FROM (
                SELECT strategy_id, symbol,
                       ROW_NUMBER() OVER (PARTITION BY strategy_id ORDER BY composite_score DESC) AS rn
                FROM stock_strategy_fitness
                WHERE batch_id = ? AND strategy_id IN ({placeholders})
            ) WHERE rn <= ?
            ORDER BY strategy_id, rn
            """,
            [batch_id] + strategy_ids + [top_per_strategy],
        ).fetchall()
        result: dict[str, list[str]] = {}
        for sid, symbol in rows:
            result.setdefault(sid, []).append(symbol)
        logger.info("_load_strategy_symbols_map: %s",
                     {k: len(v) for k, v in result.items()})
        return result
    finally:
        conn.close()


def _allocate_weights(
    nav_list: list[pd.Series],
    mode: str = "equal",
    target_vol: float = 0.10,
    custom_weights: list[float] | None = None,
) -> list[float]:
    """根据分配模式计算各策略权重。"""
    n = len(nav_list)
    if n == 0:
        return []

    if mode == "custom" and custom_weights and len(custom_weights) == n:
        total = sum(custom_weights)
        return [w / total for w in custom_weights] if total > 0 else [1.0 / n] * n

    if mode == "equal" or n == 1:
        return [1.0 / n] * n

    # 计算日收益率矩阵
    ret_df = pd.DataFrame({f"s{i}": s.pct_change().dropna() for i, s in enumerate(nav_list)})
    if ret_df.empty or len(ret_df) < 5:
        return [1.0 / n] * n

    cov = ret_df.cov().values * 252
    vols = np.sqrt(np.diag(cov))
    means = ret_df.mean().values * 252

    if mode == "inverse_vol":
        inv = 1.0 / np.maximum(vols, 1e-8)
        return (inv / inv.sum()).tolist()

    if mode == "risk_parity":
        # 简化风险平价: 权重 ∝ 1/vol
        inv = 1.0 / np.maximum(vols, 1e-8)
        return (inv / inv.sum()).tolist()

    if mode == "min_variance":
        try:
            inv_cov = np.linalg.inv(cov + np.eye(n) * 1e-6)
            w = inv_cov @ np.ones(n)
            w = np.maximum(w, 0)
            return (w / w.sum()).tolist() if w.sum() > 0 else [1.0 / n] * n
        except np.linalg.LinAlgError:
            return [1.0 / n] * n

    if mode == "max_sharpe":
        try:
            inv_cov = np.linalg.inv(cov + np.eye(n) * 1e-6)
            excess = means - 0.03  # rf = 3%
            w = inv_cov @ excess
            w = np.maximum(w, 0)
            return (w / w.sum()).tolist() if w.sum() > 0 else [1.0 / n] * n
        except np.linalg.LinAlgError:
            return [1.0 / n] * n

    if mode == "target_vol":
        port_vol = np.sqrt(np.ones(n).T @ cov @ np.ones(n)) / n
        scale = target_vol / max(port_vol, 1e-8)
        scale = min(scale, 2.0)
        w = np.ones(n) / n * scale
        return (w / w.sum()).tolist()

    if mode == "max_diversification":
        inv = 1.0 / np.maximum(vols, 1e-8)
        w = inv / inv.sum()
        return w.tolist()

    return [1.0 / n] * n


def _compute_win_rate(nav: pd.Series) -> float:
    """计算交易日胜率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    daily_ret = nav.pct_change().dropna()
    if len(daily_ret) == 0:
        return 0.0
    return float((daily_ret > 0).sum() / len(daily_ret))


def _compute_calmar(nav: pd.Series) -> float:
    """计算 Calmar 比率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = (nav.index[-1] - nav.index[0]).days or 1
    ann_ret = (1 + total_ret) ** (365 / days) - 1
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    max_dd = abs(dd.min())
    return ann_ret / max_dd if max_dd > 1e-8 else 0.0


def _compute_monthly_returns(nav: pd.Series) -> list[dict]:
    """计算月度收益。"""
    if nav.empty or len(nav) < 2:
        return []
    monthly = nav.resample("ME").last().dropna()
    ret = monthly.pct_change().dropna()
    return [
        {"month": d.strftime("%Y-%m"), "return": round(float(r) * 100, 2)}
        for d, r in ret.items()
    ]


def _compute_drawdown_curve(nav: pd.Series) -> list[dict]:
    """计算回撤曲线。"""
    if nav.empty or len(nav) < 2:
        return []
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    # 采样 (最多500点)
    step = max(1, len(dd) // 500)
    return [
        {"date": d.strftime("%Y-%m-%d"), "drawdown": round(float(v) * 100, 2)}
        for d, v in dd.iloc[::step].items()
    ]


def _compute_contributions(
    sub_results: list[SubStrategyResult],
    combined_nav: pd.Series,
) -> list[dict]:
    """计算各策略对组合收益的贡献度。"""
    total_combined_return = float(combined_nav.iloc[-1] / combined_nav.iloc[0] - 1) if len(combined_nav) >= 2 else 0.0
    contributions = []
    for s in sub_results:
        if s.nav_series.empty or len(s.nav_series) < 2:
            contributions.append({"strategy_id": s.name, "contribution_pct": 0.0})
            continue
        sub_ret = float(s.nav_series.iloc[-1] / s.nav_series.iloc[0] - 1)
        contrib = sub_ret * s.weight
        pct = (contrib / total_combined_return * 100) if abs(total_combined_return) > 1e-8 else 0.0
        contributions.append({
            "strategy_id": s.name,
            "contribution_pct": round(pct, 2),
            "weighted_return": round(contrib * 100, 4),
        })
    return contributions


def run_top10_portfolio_backtest(
    batch_id: str = "",
    top_n: int = 10,
    allocation_mode: str = "equal",
    rebalance_freq: str = "monthly",
    period_days: int = 252,
    initial_capital: float = 1_000_000,
    custom_weights: list[float] | None = None,
    strategy_ids: list[str] | None = None,
) -> dict:
    """从 fitness top-N 策略出发，跑 1 年综合组合回测。

    Parameters
    ----------
    strategy_ids : 如果指定，则只跑这些策略（忽略 top_n 排名）

    Returns a JSON-serializable dict with combined equity curve, per-strategy
    sub-results, correlation matrix, contributions, etc.
    """
    logger.info(
        "run_top10_portfolio_backtest: batch_id=%s top_n=%d mode=%s freq=%s days=%d capital=%.0f strategy_ids=%s",
        batch_id, top_n, allocation_mode, rebalance_freq, period_days, initial_capital, strategy_ids,
    )

    _write_combo_progress({"status": "running", "phase": "加载策略排名...", "completed": 0, "total": top_n})

    # 1) 加载策略: 如果指定了 strategy_ids 则按指定的加载，否则按排名取 top_n
    if strategy_ids:
        all_strats = _load_global_top_strategies(batch_id, 100)
        strategies = [s for s in all_strats if s["strategy_id"] in strategy_ids]
        # 保持 strategy_ids 的顺序
        id_order = {sid: i for i, sid in enumerate(strategy_ids)}
        strategies.sort(key=lambda s: id_order.get(s["strategy_id"], 999))
        if not strategies:
            strategies = _load_global_top_strategies(batch_id, top_n)
    else:
        strategies = _load_global_top_strategies(batch_id, top_n)
    if not strategies:
        _write_combo_progress({"status": "error", "phase": "无策略数据"})
        return {"error": "没有找到全局策略排名数据，请先运行 fitness 分析"}

    # 2) 准备回测日期范围
    end_date = date.today()
    start_date = end_date - timedelta(days=int(period_days * 365 / 252))

    # 2.5) 收集各策略的关联股票
    strategy_ids = [s["strategy_id"] for s in strategies]
    batch = strategies[0].get("batch_id", batch_id) or batch_id
    all_symbols = _load_strategy_symbols(batch, strategy_ids)
    if not all_symbols:
        _write_combo_progress({"status": "error", "phase": "无法获取策略关联的股票"})
        return {"error": "未找到策略关联的股票数据，请确认 fitness 分析结果包含 stock_strategy_fitness 记录"}

    # 3) 分批加载 K线数据 (每批50只，带进度)
    _write_combo_progress({"status": "running", "phase": f"加载K线数据 ({len(all_symbols)} 只股票)...", "completed": 0, "total": len(strategies)})
    try:
        from aipha.backtest.data_loader import load_bars_by_date
    except ImportError:
        _write_combo_progress({"status": "error", "phase": "导入失败"})
        return {"error": "无法导入 aipha.backtest.data_loader，请检查 Python 环境"}

    BATCH_SIZE = 50
    bars_by_date: dict = {}
    total_symbols = len(all_symbols)
    for batch_start in range(0, total_symbols, BATCH_SIZE):
        batch_symbols = all_symbols[batch_start:batch_start + BATCH_SIZE]
        loaded = min(batch_start + len(batch_symbols), total_symbols)
        _write_combo_progress({
            "status": "running",
            "phase": f"加载K线数据 ({loaded}/{total_symbols} 只股票)...",
            "completed": 0,
            "total": len(strategies),
        })
        logger.info("加载 K线 batch %d-%d / %d", batch_start + 1, loaded, total_symbols)
        chunk = load_bars_by_date(symbols=batch_symbols, start=start_date, end=end_date)
        for d, sym_bars in chunk.items():
            if d not in bars_by_date:
                bars_by_date[d] = {}
            bars_by_date[d].update(sym_bars)

    if not bars_by_date:
        _write_combo_progress({"status": "error", "phase": "无K线数据"})
        return {"error": "无法加载 K线数据"}

    logger.info("K线加载完成: %d 交易日, %d 只股票", len(bars_by_date), total_symbols)

    # 4) 对每个策略，基于其选出的股票构建等权组合 NAV
    #    (fitness策略可能来自TS引擎或Python引擎，无需重新运行策略，
    #     直接用策略选股结果 + 实际价格数据计算收益)
    sub_results: list[SubStrategyResult] = []
    errors: list[str] = []
    sorted_dates = sorted(bars_by_date.keys())
    t0 = datetime.now()

    # 获取每个策略的股票列表 (从 stock_strategy_fitness)
    strategy_symbols_map = _load_strategy_symbols_map(batch, strategy_ids)

    for strat in strategies:
        sid = strat["strategy_id"]
        idx = len(sub_results) + len(errors) + 1
        elapsed = (datetime.now() - t0).total_seconds()
        _write_combo_progress({
            "status": "running",
            "phase": f"计算策略净值: {idx}/{len(strategies)} — {sid}",
            "completed": idx - 1,
            "total": len(strategies),
            "elapsed_seconds": round(elapsed),
            "allocation_mode": allocation_mode,
        })
        logger.info("计算策略净值 [%d/%d]: %s", idx, len(strategies), sid)

        try:
            # 获取该策略的股票池
            strat_syms = strategy_symbols_map.get(sid, [])
            if not strat_syms:
                errors.append(f"{sid}: 无关联股票")
                continue

            # 构建等权组合的每日 NAV: 每只股票按收盘价涨跌幅等权加总
            nav_values = []
            nav_dates = []
            prev_prices: dict[str, float] = {}

            for d in sorted_dates:
                day_bars = bars_by_date.get(d, {})
                # 找到有数据的股票
                available = [s for s in strat_syms if s in day_bars]
                if not available:
                    continue

                if not nav_values:
                    # 首日：初始化
                    nav_values.append(initial_capital)
                    nav_dates.append(d)
                    prev_prices = {s: day_bars[s].close for s in available if day_bars[s].close > 0}
                else:
                    # 计算等权日收益率
                    daily_returns = []
                    for s in available:
                        if s in prev_prices and prev_prices[s] > 0 and day_bars[s].close > 0:
                            ret = (day_bars[s].close - prev_prices[s]) / prev_prices[s]
                            daily_returns.append(ret)

                    if daily_returns:
                        avg_ret = sum(daily_returns) / len(daily_returns)
                        nav_values.append(nav_values[-1] * (1 + avg_ret))
                    else:
                        nav_values.append(nav_values[-1])
                    nav_dates.append(d)

                    # 更新价格
                    for s in available:
                        if day_bars[s].close > 0:
                            prev_prices[s] = day_bars[s].close

            if len(nav_values) < 2:
                errors.append(f"{sid}: 净值数据不足 ({len(nav_values)} 天)")
                continue

            nav_series = pd.Series(nav_values, index=pd.DatetimeIndex(nav_dates))
            metrics = _compute_portfolio_metrics(nav_series)
            sub_results.append(SubStrategyResult(
                name=sid,
                weight=1.0,
                nav_series=nav_series,
                annual_return=metrics["annual_return"],
                max_drawdown=metrics["max_drawdown"],
                sharpe=metrics["sharpe"],
                total_trades=0,
                total_fees=0.0,
            ))
            logger.info("策略 %s: %d 只股票, 年化=%.2f%%, 夏普=%.2f",
                        sid, len(strat_syms), metrics["annual_return"] * 100, metrics["sharpe"])
        except Exception as e:
            errors.append(f"{sid}: {e}")
            logger.warning("策略 %s 计算失败: %s", sid, e, exc_info=True)

    if not sub_results:
        _write_combo_progress({"status": "error", "phase": "所有策略回测均失败", "errors": errors[:5]})
        return {"error": "所有策略回测均失败", "details": errors}

    _write_combo_progress({
        "status": "running",
        "phase": "计算权重和合成组合净值...",
        "completed": len(sub_results),
        "total": len(strategies),
    })

    # 5) 计算权重分配
    navs = [s.nav_series for s in sub_results]
    weights = _allocate_weights(navs, allocation_mode, custom_weights=custom_weights)
    for s, w in zip(sub_results, weights):
        s.weight = w

    # 6) 合成组合净值
    combo_result = run_multi_strategy_backtest(sub_results, allocation_mode, rebalance_freq)

    # 7) 附加指标
    win_rate = _compute_win_rate(combo_result.combined_nav)
    calmar = _compute_calmar(combo_result.combined_nav)
    monthly_returns = _compute_monthly_returns(combo_result.combined_nav)
    drawdown_curve = _compute_drawdown_curve(combo_result.combined_nav)
    contributions = _compute_contributions(sub_results, combo_result.combined_nav)

    # 8) 采样 equity curve (最多 500 点)
    nav = combo_result.combined_nav
    step = max(1, len(nav) // 500)
    equity_curve = [
        {"date": d.strftime("%Y-%m-%d"), "value": round(float(v) * initial_capital, 2)}
        for d, v in nav.iloc[::step].items()
    ]

    # 子策略 equity curves (用于前端绘图)
    sub_equity_curves = {}
    for s in sub_results:
        sn = s.nav_series
        ss = max(1, len(sn) // 500)
        sub_equity_curves[s.name] = [
            {"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
            for d, v in sn.iloc[::ss].items()
        ]

    result_dict = {
        "ok": True,
        "allocation_mode": allocation_mode,
        "rebalance_freq": rebalance_freq,
        "period_days": period_days,
        "initial_capital": initial_capital,
        "top_n": len(sub_results),
        "strategies_used": [s.name for s in sub_results],
        "combined_metrics": {
            "total_return": round(float(nav.iloc[-1] / nav.iloc[0] - 1) * 100, 2) if len(nav) >= 2 else 0,
            "annualized_return": round(combo_result.combined_annual_return * 100, 2),
            "sharpe_ratio": round(combo_result.combined_sharpe, 4),
            "max_drawdown": round(combo_result.combined_max_drawdown * 100, 2),
            "win_rate": round(win_rate * 100, 2),
            "calmar_ratio": round(calmar, 4),
        },
        "equity_curve": equity_curve,
        "sub_results": [
            {
                **s.to_dict(),
                "annual_return": round(s.annual_return * 100, 2),
                "max_drawdown": round(s.max_drawdown * 100, 2),
            }
            for s in sub_results
        ],
        "sub_equity_curves": sub_equity_curves,
        "correlation_matrix": combo_result.correlation_matrix.round(4).to_dict()
        if combo_result.correlation_matrix is not None
        else None,
        "monthly_returns": monthly_returns,
        "drawdown_curve": drawdown_curve,
        "contributions": contributions,
        "errors": errors if errors else None,
    }

    _write_combo_progress({
        "status": "done",
        "phase": "回测完成",
        "completed": len(sub_results),
        "total": len(strategies),
        "elapsed_seconds": round((datetime.now() - t0).total_seconds()),
        "allocation_mode": allocation_mode,
    })

    return result_dict


# ── Pool × Strategy 5-year backtest ──────────────────────────────


def _get_pool_backtest_progress_path() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_root = os.path.dirname(os.path.dirname(script_dir))
    logs_dir = os.path.join(os.path.dirname(python_root), "logs")
    return os.path.join(logs_dir, "pool-backtest-progress.json")


def _write_pool_progress(data: dict) -> None:
    p = _get_pool_backtest_progress_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _read_pool_progress() -> dict:
    try:
        with open(_get_pool_backtest_progress_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle"}


def _save_pool_backtest_result(result: dict) -> None:
    """保存池回测结果到 DB。"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pool_strategy_backtest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                pool_key TEXT NOT NULL,
                pool_label TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                symbol_count INTEGER,
                symbols TEXT,
                period_years INTEGER,
                total_return_pct REAL,
                annualized_return_pct REAL,
                max_drawdown_pct REAL,
                sharpe REAL,
                win_rate_pct REAL,
                profit_factor REAL,
                calmar REAL,
                cum_multiple REAL,
                initial_capital REAL,
                total_days INTEGER,
                nav_start REAL,
                nav_end REAL,
                equity_curve TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
        run_id = result.get("run_id", "")
        for r in result.get("results", []):
            conn.execute("""
                INSERT INTO pool_strategy_backtest
                (run_id, pool_key, pool_label, strategy_type, symbol_count, symbols,
                 period_years, total_return_pct, annualized_return_pct, max_drawdown_pct,
                 sharpe, win_rate_pct, profit_factor, calmar, cum_multiple,
                 initial_capital, total_days, nav_start, nav_end, equity_curve)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run_id, r["pool_key"], r["pool_label"], r["strategy_type"],
                r.get("symbol_count", 0), json.dumps(r.get("symbols", []), ensure_ascii=False),
                r.get("period_years", 5),
                r.get("total_return_pct", 0), r.get("annualized_return_pct", 0),
                r.get("max_drawdown_pct", 0), r.get("sharpe", 0),
                r.get("win_rate_pct", 0), r.get("profit_factor", 0),
                r.get("calmar", 0), r.get("cum_multiple", 0),
                r.get("initial_capital", 1_000_000), r.get("total_days", 0),
                r.get("nav_start", 0), r.get("nav_end", 0),
                json.dumps(r.get("equity_curve", []), ensure_ascii=False),
            ))
        conn.commit()
    finally:
        conn.close()


def _load_pool_backtest_history(limit: int = 20) -> list[dict]:
    """加载历史池回测记录。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 先检查表是否存在
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pool_strategy_backtest'"
        ).fetchone()
        if not tbl:
            return []
        rows = conn.execute(
            "SELECT * FROM pool_strategy_backtest ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _delete_pool_backtest_run(run_id: str) -> bool:
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM pool_strategy_backtest WHERE run_id = ?", (run_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def _get_pool_backtest_combo(run_id: str, pool_key: str, strategy_type: str) -> dict | None:
    """查询指定池回测组合的详情（含 symbols 列表）。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM pool_strategy_backtest WHERE run_id = ? AND pool_key = ? AND strategy_type = ? LIMIT 1",
            (run_id, pool_key, strategy_type),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # symbols 存储为 JSON 字符串，解析为 list
        sym_raw = d.get("symbols", "[]")
        try:
            d["symbols_list"] = json.loads(sym_raw) if isinstance(sym_raw, str) else sym_raw
        except Exception:
            d["symbols_list"] = []
        return d
    finally:
        conn.close()


def run_pool_strategy_backtest(
    pool_configs: list[dict],
    period_years: int = 5,
    rebalance_days: int = 5,
    initial_capital: float = 1_000_000,
) -> dict:
    """对多个 (股票池 × 策略) 组合做 N 年回测。

    pool_configs: [{"pool_key": "...", "pool_label": "...", "strategy_type": "mixed"|"momentum",
                    "symbols": ["600519", ...]}]
    """
    from aipha.backtest.data_loader import load_bars_by_date

    total = len(pool_configs)
    if total == 0:
        return {"ok": False, "error": "无回测任务"}

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    end_date = date.today()
    start_date = end_date - timedelta(days=period_years * 365)

    _write_pool_progress({
        "status": "running", "phase": "收集股票列表...",
        "completed": 0, "total": total, "run_id": run_id,
    })

    # 收集所有需要的股票
    all_symbols = list(set(s for cfg in pool_configs for s in cfg.get("symbols", [])))
    if not all_symbols:
        _write_pool_progress({"status": "error", "phase": "无股票数据"})
        return {"ok": False, "error": "无股票数据"}

    _write_pool_progress({
        "status": "running",
        "phase": f"加载K线数据 ({len(all_symbols)} 只股票)...",
        "completed": 0, "total": total, "run_id": run_id,
    })

    # 加载K线
    bars_by_date = load_bars_by_date(symbols=all_symbols, start=start_date, end=end_date)
    if not bars_by_date:
        _write_pool_progress({"status": "error", "phase": "无K线数据"})
        return {"ok": False, "error": "无K线数据"}

    sorted_dates = sorted(bars_by_date.keys())
    logger.info("K线加载完成: %d 交易日, %d 只股票", len(sorted_dates), len(all_symbols))

    # 为每个组合运行回测
    results = []
    t0 = datetime.now()

    for idx, cfg in enumerate(pool_configs):
        pool_key = cfg["pool_key"]
        pool_label = cfg["pool_label"]
        strategy_type = cfg["strategy_type"]
        symbols = cfg.get("symbols", [])
        label = f"{pool_label} × {'混合策略' if strategy_type == 'mixed' else '趋势动量'}"

        elapsed = (datetime.now() - t0).total_seconds()
        _write_pool_progress({
            "status": "running",
            "phase": f"回测 {idx + 1}/{total}: {label}",
            "completed": idx, "total": total,
            "run_id": run_id, "elapsed_seconds": round(elapsed),
        })
        logger.info("回测 [%d/%d]: %s (%d 只股票)", idx + 1, total, label, len(symbols))

        try:
            # 构建该池的收盘价 DataFrame
            close_data: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
            for d in sorted_dates:
                day_bars = bars_by_date.get(d, {})
                for s in symbols:
                    if s in day_bars and day_bars[s].close > 0:
                        close_data[s].append((d, day_bars[s].close))

            # 转为 DataFrame
            close_dict = {}
            for s, pairs in close_data.items():
                if pairs:
                    idx_dates = [p[0] for p in pairs]
                    vals = [p[1] for p in pairs]
                    close_dict[s] = pd.Series(vals, index=pd.DatetimeIndex(idx_dates))

            if not close_dict:
                results.append({
                    "pool_key": pool_key, "pool_label": pool_label,
                    "strategy_type": strategy_type, "error": "无K线数据",
                })
                continue

            close_df = pd.DataFrame(close_dict).sort_index().ffill()

            if strategy_type == "momentum":
                nav = _run_momentum_backtest(close_df, rebalance_days, initial_capital)
            else:
                nav = _run_mixed_backtest(close_df, rebalance_days, initial_capital)

            if nav is None or len(nav) < 2:
                results.append({
                    "pool_key": pool_key, "pool_label": pool_label,
                    "strategy_type": strategy_type, "error": "净值数据不足",
                })
                continue

            metrics = _compute_portfolio_metrics(nav)
            win_rate = _compute_win_rate(nav)
            calmar = _compute_calmar(nav)

            # 利润因子
            daily_ret = nav.pct_change().dropna()
            gains = daily_ret[daily_ret > 0].sum()
            losses = abs(daily_ret[daily_ret < 0].sum())
            profit_factor = float(gains / losses) if losses > 0 else 0.0

            # 采样 equity curve
            step = max(1, len(nav) // 300)
            equity_curve = [
                {"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
                for d, v in nav.iloc[::step].items()
            ]

            r = {
                "pool_key": pool_key,
                "pool_label": pool_label,
                "strategy_type": strategy_type,
                "symbol_count": len(symbols),
                "symbols": symbols,
                "period_years": period_years,
                "total_return_pct": round(float(nav.iloc[-1] / nav.iloc[0] - 1) * 100, 2),
                "annualized_return_pct": round(metrics["annual_return"] * 100, 2),
                "max_drawdown_pct": round(metrics["max_drawdown"] * 100, 2),
                "sharpe": round(metrics["sharpe"], 2),
                "win_rate_pct": round(win_rate * 100, 1),
                "profit_factor": round(profit_factor, 2),
                "calmar": round(calmar, 2),
                "cum_multiple": round(float(nav.iloc[-1] / nav.iloc[0]), 2),
                "initial_capital": initial_capital,
                "total_days": (nav.index[-1] - nav.index[0]).days,
                "nav_start": round(float(nav.iloc[0]), 2),
                "nav_end": round(float(nav.iloc[-1]), 2),
                "equity_curve": equity_curve,
            }
            results.append(r)
            logger.info("  → %s: 总收益 %+.2f%%, 年化 %+.2f%%, 夏普 %.2f",
                        label, r["total_return_pct"], r["annualized_return_pct"], r["sharpe"])

        except Exception as e:
            logger.warning("回测 %s 失败: %s", label, e, exc_info=True)
            results.append({
                "pool_key": pool_key, "pool_label": pool_label,
                "strategy_type": strategy_type, "error": str(e),
            })

    elapsed = round((datetime.now() - t0).total_seconds())
    ok_results = [r for r in results if "error" not in r]

    # 按综合评分排序 (年化收益×0.3 + 夏普×20 + (100-回撤)×0.2 + 胜率×0.1 + 利润因子×5)
    for r in ok_results:
        r["composite_score"] = round(
            r["annualized_return_pct"] * 0.3
            + r["sharpe"] * 20
            + (100 - r["max_drawdown_pct"]) * 0.2
            + r["win_rate_pct"] * 0.1
            + r["profit_factor"] * 5,
            2,
        )
    ok_results.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    for i, r in enumerate(ok_results):
        r["rank"] = i + 1

    result_dict = {
        "ok": True,
        "run_id": run_id,
        "total_tasks": total,
        "completed": len(ok_results),
        "failed": len(results) - len(ok_results),
        "elapsed_seconds": elapsed,
        "results": ok_results,
        "errors": [r for r in results if "error" in r],
    }

    # 保存到 DB
    try:
        _save_pool_backtest_result(result_dict)
    except Exception as e:
        logger.warning("保存回测结果失败: %s", e)

    _write_pool_progress({
        "status": "done", "phase": "回测完成",
        "completed": total, "total": total,
        "run_id": run_id, "elapsed_seconds": elapsed,
    })

    return result_dict


def _run_mixed_backtest(
    close_df: pd.DataFrame,
    rebalance_days: int = 5,
    initial_capital: float = 1_000_000,
) -> pd.Series | None:
    """混合策略: MA20>MA60多头 + 20日动量选股，等权持仓。"""
    if close_df.empty or len(close_df) < 61:
        return None

    ma20 = close_df.rolling(20).mean()
    ma60 = close_df.rolling(60).mean()
    returns_20d = close_df.pct_change(20)

    nav_values = [initial_capital]
    nav_dates = [close_df.index[60]]
    holdings: list[str] = []
    hold_count = 0

    for i in range(60, len(close_df)):
        dt = close_df.index[i]

        if hold_count % rebalance_days == 0 or not holdings:
            scores = {}
            for code in close_df.columns:
                try:
                    if pd.isna(close_df[code].iloc[i]) or pd.isna(ma20[code].iloc[i]):
                        continue
                    price = close_df[code].iloc[i]
                    m20 = ma20[code].iloc[i]
                    m60 = ma60[code].iloc[i]
                    mom = returns_20d[code].iloc[i] if not pd.isna(returns_20d[code].iloc[i]) else 0
                    if price > m20 > m60 and mom > 0:
                        scores[code] = mom
                except (KeyError, IndexError):
                    continue

            top_n = max(3, len(scores) // 2)
            sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
            new_holdings = [code for code, _ in sorted_stocks]
            if new_holdings:
                holdings = new_holdings

        hold_count += 1

        if holdings and i > 60:
            daily_returns = []
            for code in holdings:
                try:
                    prev = close_df[code].iloc[i - 1]
                    curr = close_df[code].iloc[i]
                    if not pd.isna(prev) and not pd.isna(curr) and prev > 0:
                        daily_returns.append((curr - prev) / prev)
                except (KeyError, IndexError):
                    continue
            if daily_returns:
                avg_ret = sum(daily_returns) / len(daily_returns)
                nav_values.append(nav_values[-1] * (1 + avg_ret))
            else:
                nav_values.append(nav_values[-1])
            nav_dates.append(dt)

    if len(nav_values) < 2:
        return None
    return pd.Series(nav_values, index=pd.DatetimeIndex(nav_dates[:len(nav_values)]))


def _run_momentum_backtest(
    close_df: pd.DataFrame,
    rebalance_days: int = 5,
    initial_capital: float = 1_000_000,
) -> pd.Series | None:
    """趋势动量策略: 20日+5日动量选股，等权持仓。"""
    if close_df.empty or len(close_df) < 21:
        return None

    returns_20d = close_df.pct_change(20)
    returns_5d = close_df.pct_change(5)

    nav_values = [initial_capital]
    nav_dates = [close_df.index[20]]
    holdings: list[str] = []
    hold_count = 0

    for i in range(20, len(close_df)):
        dt = close_df.index[i]

        if hold_count % rebalance_days == 0 or not holdings:
            scores = {}
            for code in close_df.columns:
                try:
                    mom20 = returns_20d[code].iloc[i]
                    mom5 = returns_5d[code].iloc[i]
                    if pd.isna(mom20) or pd.isna(mom5):
                        continue
                    if mom20 > 0.02 and mom5 > 0:
                        scores[code] = mom20 * 0.7 + mom5 * 0.3
                except (KeyError, IndexError):
                    continue

            top_n = max(3, min(len(scores), len(close_df.columns) // 2))
            sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
            new_holdings = [code for code, _ in sorted_stocks]
            if new_holdings:
                holdings = new_holdings

        hold_count += 1

        if holdings and i > 20:
            daily_returns = []
            for code in holdings:
                try:
                    prev = close_df[code].iloc[i - 1]
                    curr = close_df[code].iloc[i]
                    if not pd.isna(prev) and not pd.isna(curr) and prev > 0:
                        daily_returns.append((curr - prev) / prev)
                except (KeyError, IndexError):
                    continue
            if daily_returns:
                avg_ret = sum(daily_returns) / len(daily_returns)
                nav_values.append(nav_values[-1] * (1 + avg_ret))
            else:
                nav_values.append(nav_values[-1])
            nav_dates.append(dt)

    if len(nav_values) < 2:
        return None
    return pd.Series(nav_values, index=pd.DatetimeIndex(nav_dates[:len(nav_values)]))


# ── CLI entry point for standalone testing ────────────────────────

def _cli_main():
    """供 strategy_fitness.py 或直接命令行调用的入口。"""
    raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(raw)
    action = config.get("action", "run_top10")
    try:
        if action == "run_top10":
            result = run_top10_portfolio_backtest(
                batch_id=config.get("batch_id", ""),
                top_n=config.get("top_n", 10),
                allocation_mode=config.get("allocation_mode", "equal"),
                rebalance_freq=config.get("rebalance_freq", "monthly"),
                period_days=config.get("period_days", 252),
                initial_capital=config.get("initial_capital", 1_000_000),
                custom_weights=config.get("custom_weights"),
                strategy_ids=config.get("strategy_ids"),
            )
        elif action == "compare_allocations":
            modes = config.get("modes", ["equal", "risk_parity", "max_sharpe", "min_variance"])
            results = {}
            for mode in modes:
                r = run_top10_portfolio_backtest(
                    batch_id=config.get("batch_id", ""),
                    top_n=config.get("top_n", 10),
                    allocation_mode=mode,
                    rebalance_freq=config.get("rebalance_freq", "monthly"),
                    period_days=config.get("period_days", 252),
                    initial_capital=config.get("initial_capital", 1_000_000),
                )
                results[mode] = r
            result = {"ok": True, "comparisons": results}
        elif action == "read_progress":
            result = _read_combo_progress()
        elif action == "run_pool_backtest":
            result = run_pool_strategy_backtest(
                pool_configs=config.get("pool_configs", []),
                period_years=config.get("period_years", 5),
                rebalance_days=config.get("rebalance_days", 5),
                initial_capital=config.get("initial_capital", 1_000_000),
            )
        elif action == "pool_backtest_progress":
            result = _read_pool_progress()
        elif action == "pool_backtest_history":
            result = {"ok": True, "records": _load_pool_backtest_history(config.get("limit", 50))}
        elif action == "pool_backtest_delete":
            deleted = _delete_pool_backtest_run(config.get("run_id", ""))
            result = {"ok": deleted}
        elif action == "pool_backtest_get_combo":
            combo = _get_pool_backtest_combo(
                config.get("run_id", ""),
                config.get("pool_key", ""),
                config.get("strategy_type", ""),
            )
            if combo:
                result = {"ok": True, "combo": combo}
            else:
                result = {"ok": False, "error": "未找到该回测记录"}
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _cli_main()
