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
) -> dict:
    """从 fitness top-N 策略出发，跑 1 年综合组合回测。

    Returns a JSON-serializable dict with combined equity curve, per-strategy
    sub-results, correlation matrix, contributions, etc.
    """
    logger.info(
        "run_top10_portfolio_backtest: batch_id=%s top_n=%d mode=%s freq=%s days=%d capital=%.0f",
        batch_id, top_n, allocation_mode, rebalance_freq, period_days, initial_capital,
    )

    _write_combo_progress({"status": "running", "phase": "加载策略排名...", "completed": 0, "total": top_n})

    # 1) 加载 top-N 策略
    strategies = _load_global_top_strategies(batch_id, top_n)
    if not strategies:
        _write_combo_progress({"status": "error", "phase": "无策略数据"})
        return {"error": "没有找到全局策略排名数据，请先运行 fitness 分析"}

    # 2) 准备回测日期范围
    end_date = date.today()
    start_date = end_date - timedelta(days=int(period_days * 365 / 252))

    # 3) 加载 K线数据
    _write_combo_progress({"status": "running", "phase": "加载K线数据...", "completed": 0, "total": len(strategies)})
    try:
        from aipha.backtest.data_loader import load_bars_by_date
    except ImportError:
        _write_combo_progress({"status": "error", "phase": "导入失败"})
        return {"error": "无法导入 aipha.backtest.data_loader，请检查 Python 环境"}

    logger.info("加载 K线数据: %s ~ %s", start_date, end_date)
    bars_by_date = load_bars_by_date(start_date=start_date, end_date=end_date)
    if not bars_by_date:
        _write_combo_progress({"status": "error", "phase": "无K线数据"})
        return {"error": "无法加载 K线数据"}

    # 4) 对每个策略运行回测，收集 NAV 曲线
    sub_results: list[SubStrategyResult] = []
    errors: list[str] = []

    try:
        import aipha.strategy.register_all  # noqa: F401
        from aipha.strategy.registry import StrategyRegistry
        from aipha.backtest.engine import BacktestEngine, BacktestConfig
    except ImportError as e:
        _write_combo_progress({"status": "error", "phase": str(e)})
        return {"error": f"策略引擎导入失败: {e}"}

    reg = StrategyRegistry()
    t0 = datetime.now()

    for strat in strategies:
        sid = strat["strategy_id"]
        idx = len(sub_results) + len(errors) + 1
        elapsed = (datetime.now() - t0).total_seconds()
        _write_combo_progress({
            "status": "running",
            "phase": f"策略回测: {idx}/{len(strategies)} — {sid}",
            "completed": idx - 1,
            "total": len(strategies),
            "elapsed_seconds": round(elapsed),
            "allocation_mode": allocation_mode,
        })
        logger.info("回测策略 [%d/%d]: %s", idx, len(strategies), sid)
        try:
            strategy = reg.create(sid)
            bt_config = BacktestConfig(initial_capital=initial_capital)
            engine = BacktestEngine(config=bt_config, strategy=strategy)
            result = engine.run(bars_by_date)

            # 构建 NAV Series
            sorted_dates = sorted(bars_by_date.keys())
            nav_values = []
            nav_dates = []
            for snap in result.daily_snapshots:
                nav_values.append(snap.nav)
                nav_dates.append(snap.date)

            if not nav_values:
                # Fallback: 用 equity 手动算
                equity = initial_capital
                for d in sorted_dates:
                    nav_values.append(equity)
                    nav_dates.append(d)
                if not nav_values:
                    errors.append(f"{sid}: 无净值数据")
                    continue

            nav_series = pd.Series(nav_values, index=pd.DatetimeIndex(nav_dates))

            # 计算子策略指标
            metrics = _compute_portfolio_metrics(nav_series)
            sub_results.append(SubStrategyResult(
                name=sid,
                weight=1.0,  # 临时，后面重新分配
                nav_series=nav_series,
                annual_return=metrics["annual_return"],
                max_drawdown=metrics["max_drawdown"],
                sharpe=metrics["sharpe"],
                total_trades=len(result.trades),
                total_fees=sum(t.fees.total for t in result.trades) if result.trades else 0.0,
            ))
        except Exception as e:
            errors.append(f"{sid}: {e}")
            logger.warning("策略 %s 回测失败: %s", sid, e, exc_info=True)

    if not sub_results:
        _write_combo_progress({"status": "error", "phase": "所有策略回测均失败"})
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
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _cli_main()
