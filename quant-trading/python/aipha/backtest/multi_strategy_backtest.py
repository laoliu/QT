"""多策略组合回测协调器

读取多策略配置 YAML（含 ``portfolio.strategies`` 数组），
将每个子策略映射为独立的 ``BacktestRunConfig``，
分别调用 ``run_from_config`` 执行回测，
最终按权重合成组合 NAV 并输出汇总报告。

支持三种资金配置模式（``allocation_mode``）：
- ``fixed_weight``：静态权重（按 YAML 配置中的 weight 字段合成 NAV）
- ``risk_parity``：滚动风险平价（通过 ``AdaptiveWeightAllocator`` 实现）
- ``adaptive``：综合自适应（通过 ``AdaptiveWeightAllocator`` 的 ``composite_adaptive`` 方法）

组合级风控配置（``risk_control``）会被解析并记录到摘要中，
供下游审计与报告消费。

使用方式::

    from aipha.backtest.multi_strategy_backtest import run_multi_strategy_backtest
    result = run_multi_strategy_backtest("configs/backtest/multi_strategy_portfolio.yml")

设计原则：
- 子策略完全复用 ``config_runner.run_from_config`` 链路
- 组合层只做资金权重分配 + NAV 合成 + 指标汇总
- 不引入新的回测引擎或执行路径
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)

# 资金配置模式 → AdaptiveWeightAllocator 方法映射
_ALLOCATION_MODE_MAP: dict[str, str | None] = {
    "fixed_weight": None,  # 不使用 AdaptiveWeightAllocator
    "risk_parity": "rolling_risk_parity",
    "adaptive": "composite_adaptive",
    "inverse_volatility": "inverse_volatility",
    "momentum_weight": "momentum",
    "max_sharpe": "rolling_max_sharpe",
    "regime_adaptive": "regime_adaptive",
    "drawdown_penalty": "drawdown_penalty",
}


# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class SubStrategyResult:
    """单个子策略的回测结果摘要

    Attributes:
        name: 策略名称
        weight: 资金权重
        nav_series: 归一化日净值序列 {date: nav}
        annual_return: 年化收益率
        max_drawdown: 最大回撤
        sharpe: 夏普比率
        total_trades: 总交易次数
        total_fees: 总费用
    """

    name: str
    weight: float
    nav_series: dict[date, float] = field(default_factory=dict)
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    total_trades: int = 0
    total_fees: float = 0.0


@dataclass
class MultiStrategyResult:
    """多策略组合回测结果

    Attributes:
        sub_results: 各子策略结果列表
        combined_nav: 组合加权净值 {date: nav}
        portfolio_annual_return: 组合年化收益率
        portfolio_max_drawdown: 组合最大回撤
        portfolio_sharpe: 组合夏普比率
        allocation_mode: 资金配置模式
        initial_capital: 总初始资金
        correlation_matrix: 子策略收益相关性矩阵（可选）
        risk_control_config: 组合级风控配置（从 YAML risk_control 解析）
        adaptive_result: AdaptiveWeightAllocator 结果（非 fixed_weight 模式时填充）
    """

    sub_results: list[SubStrategyResult] = field(default_factory=list)
    combined_nav: dict[date, float] = field(default_factory=dict)
    portfolio_annual_return: float = 0.0
    portfolio_max_drawdown: float = 0.0
    portfolio_sharpe: float = 0.0
    allocation_mode: str = "fixed_weight"
    initial_capital: float = 0.0
    correlation_matrix: dict[str, dict[str, float]] | None = None
    risk_control_config: dict[str, Any] | None = None
    adaptive_result: Any | None = None  # AdaptivePortfolioResult when available


# ============================================================================
# 配置解析
# ============================================================================


def parse_multi_strategy_config(
    path: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """解析多策略组合 YAML 配置

    Args:
        path: 配置文件路径

    Returns:
        (共享配置字典, 子策略配置列表, risk_control 配置或 None)

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 缺少 portfolio.strategies 字段
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    portfolio = raw.get("portfolio")
    if not portfolio or "strategies" not in portfolio:
        raise ValueError(
            f"多策略配置必须包含 portfolio.strategies 字段: {path}"
        )

    strategies = portfolio["strategies"]
    if not strategies or not isinstance(strategies, list):
        raise ValueError("portfolio.strategies 必须是非空列表")

    # 验证权重之和
    total_weight = sum(s.get("weight", 0) for s in strategies)
    if abs(total_weight - 1.0) > 0.01:
        logger.warning(
            f"子策略权重之和 {total_weight:.4f} != 1.0，将按比例归一化"
        )

    # 提取 risk_control 配置（E-2 修复：显式解析并传递给下游）
    risk_control = raw.get("risk_control")
    if risk_control:
        logger.info(f"已加载组合级风控配置: {list(risk_control.keys())}")
    else:
        logger.info("未配置组合级风控（risk_control）")

    # 共享配置（去掉 portfolio 和 risk_control 部分）
    shared = {k: v for k, v in raw.items() if k not in ("portfolio", "risk_control")}

    return shared, strategies, risk_control


def build_sub_strategy_config(
    shared: dict[str, Any],
    sub: dict[str, Any],
    initial_capital: float,
) -> dict[str, Any]:
    """将共享配置 + 子策略定义合成为单策略 YAML dict

    Args:
        shared: 共享配置部分（data, cost, fill_model 等）
        sub: 单个子策略定义（name, weight, config）
        initial_capital: 总初始资金

    Returns:
        可被 ``config_runner.parse_config()`` 解析的配置字典
    """
    cfg = copy.deepcopy(shared)

    name = sub["name"]
    weight = sub.get("weight", 0)
    params = sub.get("config", {})

    # 资金分配
    allocated_capital = initial_capital * weight
    cfg["initial_capital"] = allocated_capital

    # 策略配置
    cfg["strategy"] = {
        "name": name,
        "params": params,
    }

    # 名称标注
    cfg["name"] = cfg.get("name", "multi") + f"_sub_{name}"

    # 输出子目录
    base_output = cfg.get("output_dir", "output/multi_strategy")
    cfg["output_dir"] = str(Path(base_output) / name)

    return cfg


# ============================================================================
# 回测执行
# ============================================================================


def run_multi_strategy_backtest(
    config_path: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> MultiStrategyResult:
    """执行多策略组合回测

    流程：
    1. 解析多策略 YAML 配置
    2. 为每个子策略生成独立配置并运行回测
    3. 收集子策略 NAV 序列
    4. 按权重合成组合 NAV
    5. 计算组合指标

    Args:
        config_path: 多策略 YAML 配置文件路径
        output_dir: 输出目录（可选，覆盖配置中的 output_dir）

    Returns:
        MultiStrategyResult
    """
    # 延迟导入避免循环依赖
    from aipha.backtest.config_runner import (
        load_config,
        parse_config,
        run_from_config,
    )

    config_path = Path(config_path)
    logger.info(f"开始多策略组合回测: {config_path}")

    # 1. 解析配置（含 risk_control）
    shared, strategies, risk_control = parse_multi_strategy_config(config_path)
    total_capital = shared.get("initial_capital", 1_000_000.0)

    # 从原始 YAML 重新读取 portfolio 节获取 allocation_mode
    with open(config_path, "r", encoding="utf-8") as f:
        raw_yaml = yaml.safe_load(f)
    portfolio_cfg = raw_yaml.get("portfolio", {})
    allocation_mode = portfolio_cfg.get("allocation_mode", "fixed_weight")

    effective_output = Path(output_dir) if output_dir else Path(
        shared.get("output_dir", "output/multi_strategy")
    )

    # 2. 逐策略运行
    sub_results: list[SubStrategyResult] = []

    for sub in strategies:
        name = sub["name"]
        weight = sub.get("weight", 0)
        logger.info(f"  运行子策略 [{name}] (weight={weight:.2%})")

        # 构建单策略配置 dict
        sub_cfg_dict = build_sub_strategy_config(shared, sub, total_capital)

        # 写入临时 YAML
        sub_yml = effective_output / name / "config.yml"
        sub_yml.parent.mkdir(parents=True, exist_ok=True)
        with open(sub_yml, "w", encoding="utf-8") as f:
            yaml.dump(sub_cfg_dict, f, allow_unicode=True, default_flow_style=False)

        # 通过 config_runner 运行
        try:
            run_result = run_from_config(str(sub_yml))
            bt_result = run_result.result

            # 提取 NAV 序列
            nav_dict: dict[date, float] = {}
            for snap in bt_result.daily_snapshots:
                nav_dict[snap.trade_date] = snap.nav

            sr = SubStrategyResult(
                name=name,
                weight=weight,
                nav_series=nav_dict,
                annual_return=getattr(
                    run_result.report.performance, "annualized_return", 0.0
                ) if run_result.report else 0.0,
                max_drawdown=getattr(
                    run_result.report.drawdown, "max_drawdown_pct", 0.0
                ) if run_result.report else 0.0,
                sharpe=getattr(
                    run_result.report.performance, "sharpe_ratio", 0.0
                ) if run_result.report else 0.0,
                total_trades=bt_result.total_trades,
                total_fees=float(bt_result.total_fees.total),
            )
            sub_results.append(sr)
            logger.info(
                f"    [{name}] 完成: NAV={bt_result.final_nav:.2f}, "
                f"trades={bt_result.total_trades}"
            )
        except Exception as e:
            logger.error(f"    [{name}] 执行失败: {e}")
            sub_results.append(SubStrategyResult(name=name, weight=weight))

    # 3. 合成组合 NAV（根据 allocation_mode 分发）
    adaptive_result = None

    adaptive_method = _ALLOCATION_MODE_MAP.get(allocation_mode)
    if adaptive_method is not None and len(sub_results) >= 2:
        # 使用 AdaptiveWeightAllocator 动态配权
        logger.info(
            f"使用自适应配权模式: {allocation_mode} → {adaptive_method}"
        )
        try:
            from aipha.strategy.adaptive_weight import AdaptiveWeightAllocator

            # 读取自适应权重参数（优先从 portfolio 配置，回退到默认值）
            aw_params = portfolio_cfg.get("adaptive_weight_params", {})
            allocator = AdaptiveWeightAllocator(
                method=adaptive_method,
                lookback=aw_params.get("lookback", 60),
                rebalance_freq=aw_params.get(
                    "rebalance_freq",
                    portfolio_cfg.get("rebalance_days", 5),
                ),
                min_weight=aw_params.get("min_weight", 0.05),
                max_weight=aw_params.get("max_weight", 0.80),
            )

            # 构建每个子策略的 NAV 字典（归一化到各自初始资金）
            strategy_navs: dict[str, dict[date, float]] = {}
            for sr in sub_results:
                if sr.nav_series and len(sr.nav_series) >= 2:
                    first_nav = next(iter(
                        v for _, v in sorted(sr.nav_series.items())
                    ))
                    if first_nav > 0:
                        strategy_navs[sr.name] = {
                            d: v / first_nav
                            for d, v in sr.nav_series.items()
                        }

            if len(strategy_navs) >= 2:
                adaptive_result = allocator.compute_portfolio(strategy_navs)

                # 将自适应组合 NAV 缩放到总资金
                combined_nav = {
                    d: v * total_capital
                    for d, v in adaptive_result.portfolio_nav.items()
                }
                logger.info(
                    f"自适应配权完成: 年化={adaptive_result.annualized_return:.2%}, "
                    f"Sharpe={adaptive_result.sharpe:.2f}, "
                    f"MDD={adaptive_result.max_drawdown:.2%}"
                )
            else:
                logger.warning(
                    f"有效子策略不足 2 个（{len(strategy_navs)}），"
                    f"回退到 fixed_weight 模式"
                )
                combined_nav = _combine_nav_series(sub_results, total_capital)
        except Exception as e:
            logger.error(
                f"自适应配权失败: {e}，回退到 fixed_weight 模式"
            )
            combined_nav = _combine_nav_series(sub_results, total_capital)
    else:
        # fixed_weight 模式：静态权重合成
        if allocation_mode != "fixed_weight":
            logger.warning(
                f"allocation_mode='{allocation_mode}' 不支持或子策略不足，"
                f"回退到 fixed_weight"
            )
        combined_nav = _combine_nav_series(sub_results, total_capital)

    # 4. 计算组合指标
    portfolio_metrics = _compute_portfolio_metrics(combined_nav, total_capital)

    # 5. 计算相关性矩阵
    correlation = _compute_correlation(sub_results)

    result = MultiStrategyResult(
        sub_results=sub_results,
        combined_nav=combined_nav,
        portfolio_annual_return=portfolio_metrics.get("annual_return", 0.0),
        portfolio_max_drawdown=portfolio_metrics.get("max_drawdown", 0.0),
        portfolio_sharpe=portfolio_metrics.get("sharpe", 0.0),
        allocation_mode=allocation_mode,
        initial_capital=total_capital,
        correlation_matrix=correlation,
        risk_control_config=risk_control,
        adaptive_result=adaptive_result,
    )

    # 6. 记录风控配置摘要
    if risk_control:
        logger.info(f"组合级风控配置已加载: {list(risk_control.keys())}")
        if risk_control.get("drawdown_limit", {}).get("max_drawdown_pct"):
            logger.info(
                f"  回撤限制: {risk_control['drawdown_limit']['max_drawdown_pct']:.0%}"
            )
        if risk_control.get("concentration", {}).get("max_single_pct"):
            logger.info(
                f"  单票集中度: {risk_control['concentration']['max_single_pct']:.0%}"
            )

    # 6. 保存组合报告摘要
    _save_summary(result, effective_output)

    logger.info(
        f"多策略组合回测完成: "
        f"年化={result.portfolio_annual_return:.2%}, "
        f"回撤={result.portfolio_max_drawdown:.2%}, "
        f"Sharpe={result.portfolio_sharpe:.2f}"
    )

    return result


# ============================================================================
# 内部辅助
# ============================================================================


def _combine_nav_series(
    sub_results: list[SubStrategyResult],
    total_capital: float,
) -> dict[date, float]:
    """按权重合成组合 NAV

    每个子策略的 NAV 贡献 = (子策略 NAV / 子策略初始资金) * 权重 * 总资金

    Args:
        sub_results: 子策略结果列表
        total_capital: 总初始资金

    Returns:
        日期 → 组合净值
    """
    # 收集所有日期
    all_dates: set[date] = set()
    for sr in sub_results:
        all_dates.update(sr.nav_series.keys())

    if not all_dates:
        return {}

    sorted_dates = sorted(all_dates)
    combined: dict[date, float] = {}

    for d in sorted_dates:
        nav_sum = 0.0
        for sr in sub_results:
            if not sr.nav_series:
                # 子策略执行失败，贡献=初始权重份额不变
                nav_sum += sr.weight * total_capital
                continue

            if d in sr.nav_series:
                sub_nav = sr.nav_series[d]
            else:
                # 日期缺失：用最近已知值
                known = [dd for dd in sr.nav_series if dd <= d]
                sub_nav = sr.nav_series[max(known)] if known else sr.weight * total_capital

            nav_sum += sub_nav

        combined[d] = nav_sum

    return combined


def _compute_portfolio_metrics(
    combined_nav: dict[date, float],
    initial_capital: float,
) -> dict[str, float]:
    """计算组合级指标"""
    if not combined_nav or len(combined_nav) < 2:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    sorted_dates = sorted(combined_nav.keys())
    navs = [combined_nav[d] for d in sorted_dates]

    # 日收益率
    daily_returns = []
    for i in range(1, len(navs)):
        if navs[i - 1] > 0:
            daily_returns.append(navs[i] / navs[i - 1] - 1.0)

    if not daily_returns:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    # 年化收益
    total_return = navs[-1] / navs[0] - 1.0
    n_days = (sorted_dates[-1] - sorted_dates[0]).days
    annual_return = (1.0 + total_return) ** (365.0 / max(n_days, 1)) - 1.0 if n_days > 0 else 0.0

    # 最大回撤
    peak = navs[0]
    max_dd = 0.0
    for v in navs:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    # Sharpe
    import statistics

    mean_ret = statistics.mean(daily_returns)
    std_ret = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 1e-10
    sharpe = (mean_ret / std_ret) * (TRADING_DAYS_PER_YEAR ** 0.5) if std_ret > 1e-10 else 0.0

    return {
        "annual_return": annual_return,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
    }


def _compute_correlation(
    sub_results: list[SubStrategyResult],
) -> dict[str, dict[str, float]] | None:
    """计算子策略日收益率相关性矩阵"""
    active = [sr for sr in sub_results if len(sr.nav_series) >= 2]
    if len(active) < 2:
        return None

    # 收集共同日期
    common_dates = set(active[0].nav_series.keys())
    for sr in active[1:]:
        common_dates &= set(sr.nav_series.keys())

    if len(common_dates) < 10:
        return None

    sorted_dates = sorted(common_dates)

    # 计算日收益率
    returns: dict[str, list[float]] = {}
    for sr in active:
        rets = []
        prev = None
        for d in sorted_dates:
            nav = sr.nav_series[d]
            if prev is not None and prev > 0:
                rets.append(nav / prev - 1.0)
            prev = nav
        returns[sr.name] = rets

    # 相关性矩阵
    import statistics

    names = [sr.name for sr in active]
    matrix: dict[str, dict[str, float]] = {}

    for n1 in names:
        matrix[n1] = {}
        for n2 in names:
            if n1 == n2:
                matrix[n1][n2] = 1.0
            else:
                r1, r2 = returns[n1], returns[n2]
                min_len = min(len(r1), len(r2))
                if min_len < 2:
                    matrix[n1][n2] = 0.0
                    continue
                r1, r2 = r1[:min_len], r2[:min_len]
                m1, m2 = statistics.mean(r1), statistics.mean(r2)
                cov = sum((a - m1) * (b - m2) for a, b in zip(r1, r2)) / (min_len - 1)
                s1 = statistics.stdev(r1)
                s2 = statistics.stdev(r2)
                if s1 > 1e-10 and s2 > 1e-10:
                    matrix[n1][n2] = cov / (s1 * s2)
                else:
                    matrix[n1][n2] = 0.0

    return matrix


def _save_summary(
    result: MultiStrategyResult,
    output_dir: Path,
) -> None:
    """保存组合回测摘要到 YAML"""
    import json

    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "allocation_mode": result.allocation_mode,
        "initial_capital": result.initial_capital,
        "portfolio": {
            "annual_return": round(result.portfolio_annual_return, 6),
            "max_drawdown": round(result.portfolio_max_drawdown, 6),
            "sharpe": round(result.portfolio_sharpe, 4),
        },
        "sub_strategies": [
            {
                "name": sr.name,
                "weight": sr.weight,
                "annual_return": round(sr.annual_return, 6),
                "max_drawdown": round(sr.max_drawdown, 6),
                "sharpe": round(sr.sharpe, 4),
                "total_trades": sr.total_trades,
                "total_fees": round(sr.total_fees, 2),
            }
            for sr in result.sub_results
        ],
    }
    if result.correlation_matrix:
        summary["correlation"] = {
            k: {kk: round(vv, 4) for kk, vv in v.items()}
            for k, v in result.correlation_matrix.items()
        }

    # 风控配置记录（E-2 修复：确保下游可审计风控配置）
    if result.risk_control_config:
        summary["risk_control"] = result.risk_control_config

    # 自适应权重摘要
    if result.adaptive_result is not None:
        ar = result.adaptive_result
        summary["adaptive_weight"] = {
            "method": ar.method,
            "annualized_return": round(ar.annualized_return, 6),
            "sharpe": round(ar.sharpe, 4),
            "max_drawdown": round(ar.max_drawdown, 6),
        }
        if ar.yearly_avg_weights:
            summary["adaptive_weight"]["yearly_avg_weights"] = {
                str(year): {name: round(w, 4) for name, w in weights.items()}
                for year, weights in ar.yearly_avg_weights.items()
            }

    path = output_dir / "multi_strategy_summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info(f"多策略组合摘要已保存: {path}")

    # 保存组合 NAV
    if result.combined_nav:
        nav_path = output_dir / "portfolio_nav.csv"
        with open(nav_path, "w", encoding="utf-8") as f:
            f.write("date,nav\n")
            for d in sorted(result.combined_nav.keys()):
                f.write(f"{d},{result.combined_nav[d]:.4f}\n")
        logger.info(f"组合 NAV 已保存: {nav_path}")
