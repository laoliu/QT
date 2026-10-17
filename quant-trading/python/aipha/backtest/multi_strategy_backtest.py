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
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.utils.paths import PROJECT_ROOT, resolve_path

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
    # YAML 链路传 {date: nav} dict；Top-N 组合链路传 pd.Series。
    # 两条链路的下游函数各自处理自己的口径，这里不做转换。
    nav_series: dict[date, float] | Any = field(default_factory=dict)
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
        combined_nav: 组合加权净值（YAML 链路为 {date: nav}，Top-N 链路为 pd.Series）
        combined_annual_return: 组合年化收益率
        combined_max_drawdown: 组合最大回撤
        combined_sharpe: 组合夏普比率
        allocation_mode: 资金配置模式
        initial_capital: 总初始资金
        correlation_matrix: 子策略收益相关性矩阵（可选）
        risk_control_config: 组合级风控配置（从 YAML risk_control 解析）
        adaptive_result: AdaptiveWeightAllocator 结果（非 fixed_weight 模式时填充）

    指标字段名以 ``combined_*`` 为准；``portfolio_*`` 保留为只读别名，
    给 ``_save_summary`` 等既有读方用（它输出的 JSON 键仍叫 "portfolio"）。
    """

    sub_results: list[SubStrategyResult] = field(default_factory=list)
    combined_nav: dict[date, float] | Any = field(default_factory=dict)
    combined_annual_return: float = 0.0
    combined_max_drawdown: float = 0.0
    combined_sharpe: float = 0.0
    allocation_mode: str = "equal"
    initial_capital: float = 0.0
    correlation_matrix: dict[str, dict[str, float]] | Any | None = None
    risk_control_config: dict[str, Any] | None = None
    adaptive_result: Any | None = None  # AdaptivePortfolioResult when available

    # ---- 只读别名（向后兼容既有读方）----
    @property
    def portfolio_annual_return(self) -> float:
        return self.combined_annual_return

    @property
    def portfolio_max_drawdown(self) -> float:
        return self.combined_max_drawdown

    @property
    def portfolio_sharpe(self) -> float:
        return self.combined_sharpe

    def to_dict(self) -> dict[str, Any]:
        """输出可 JSON 序列化的摘要（给前端 / 接口用）。

        故意**不含** ``combined_nav`` 与 ``nav_series``：净值序列可能有
        几千个点，且 dict/Series 两种口径都出现过，塞进同一个 JSON 字段
        只会让前端两头猜。曲线走独立接口取。
        """
        return {
            "allocation_mode": self.allocation_mode,
            "initial_capital": self.initial_capital,
            "combined_annual_return": self.combined_annual_return,
            "combined_max_drawdown": self.combined_max_drawdown,
            "combined_sharpe": self.combined_sharpe,
            "sub_results": [
                {
                    "name": sr.name,
                    "weight": sr.weight,
                    "annual_return": sr.annual_return,
                    "max_drawdown": sr.max_drawdown,
                    "sharpe": sr.sharpe,
                    "total_trades": sr.total_trades,
                    "total_fees": sr.total_fees,
                }
                for sr in self.sub_results
            ],
        }


# ============================================================================
# 配置解析
# ============================================================================


def parse_multi_strategy_yaml(
    path: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """解析多策略组合 YAML 配置（``portfolio.strategies`` 形态）

    这是 ``run_multi_strategy_backtest`` 走的那条链路，对应
    ``configs/backtest/multi_strategy_portfolio.yml`` 的 YAML 契约。
    原名叫 ``parse_multi_strategy_config``，让位给下方 Top-N 组合链路的
    同名函数（那条接收的是前端传来的 config **dict**，返回四元组）。
    两者是不同的输入契约，不是同一个函数的两种写法 —— 这条按路径读
    YAML 并要求 ``portfolio.strategies``，改名以免调用方混用。

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


def build_sub_config_with_capital(
    shared: dict[str, Any],
    sub: dict[str, Any],
    initial_capital: float,
) -> dict[str, Any]:
    """将共享配置 + 子策略定义合成为单策略 YAML dict（含资金分配）

    原名 ``build_sub_strategy_config``；该名字现由下方通用的两参数
    深合并版本使用（Top-N 链路只做 base/override 合并，不碰资金）。
    本函数额外做了「按权重切分总资金」和输出子目录标注，是 YAML
    链路专用的。

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
    shared, strategies, risk_control = parse_multi_strategy_yaml(config_path)
    total_capital = shared.get("initial_capital", 1_000_000.0)

    # 从原始 YAML 重新读取 portfolio 节获取 allocation_mode
    with open(config_path, "r", encoding="utf-8") as f:
        raw_yaml = yaml.safe_load(f)
    portfolio_cfg = raw_yaml.get("portfolio", {})
    allocation_mode = portfolio_cfg.get("allocation_mode", "fixed_weight")

    effective_output = (
        resolve_path(output_dir)
        if output_dir
        else resolve_path(
            shared.get("output_dir"), "output", "multi_strategy"
        )
    )

    # 2. 逐策略运行
    sub_results: list[SubStrategyResult] = []

    for sub in strategies:
        name = sub["name"]
        weight = sub.get("weight", 0)
        logger.info(f"  运行子策略 [{name}] (weight={weight:.2%})")

        # 构建单策略配置 dict
        sub_cfg_dict = build_sub_config_with_capital(shared, sub, total_capital)

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
                combined_nav = _combine_nav_dicts(sub_results, total_capital)
        except Exception as e:
            logger.error(
                f"自适应配权失败: {e}，回退到 fixed_weight 模式"
            )
            combined_nav = _combine_nav_dicts(sub_results, total_capital)
    else:
        # fixed_weight 模式：静态权重合成
        if allocation_mode != "fixed_weight":
            logger.warning(
                f"allocation_mode='{allocation_mode}' 不支持或子策略不足，"
                f"回退到 fixed_weight"
            )
        combined_nav = _combine_nav_dicts(sub_results, total_capital)

    # 4. 计算组合指标
    portfolio_metrics = _metrics_from_nav_dict(combined_nav, total_capital)

    # 5. 计算相关性矩阵
    correlation = _correlation_from_sub_results(sub_results)

    result = MultiStrategyResult(
        sub_results=sub_results,
        combined_nav=combined_nav,
        combined_annual_return=portfolio_metrics.get("annual_return", 0.0),
        combined_max_drawdown=portfolio_metrics.get("max_drawdown", 0.0),
        combined_sharpe=portfolio_metrics.get("sharpe", 0.0),
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


def _combine_nav_dicts(
    sub_results: list[SubStrategyResult],
    total_capital: float,
) -> dict[date, float]:
    """按权重合成组合 NAV（dict 口径，YAML 链路专用）

    原名 ``_combine_nav_series``；该名字现由下方 pandas 口径的版本使用
    （输入 list[Series] + 权重，输出归一化 Series）。本函数输入的是
    ``SubStrategyResult`` 列表、输出 {date: 绝对金额}。

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


def _metrics_from_nav_dict(
    combined_nav: dict[date, float],
    initial_capital: float,
) -> dict[str, float]:
    """计算组合级指标（dict 口径，YAML 链路专用）

    原名 ``_compute_portfolio_metrics``，让位给下方 pandas 口径的同名
    函数。两者的年化/回撤/Sharpe 口径保持一致（自然日 CAGR + sqrt(244)）。
    """
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


def _correlation_from_sub_results(
    sub_results: list[SubStrategyResult],
) -> dict[str, dict[str, float]] | None:
    """计算子策略日收益率相关性矩阵（dict 口径，YAML 链路专用）

    原名 ``_compute_correlation``，让位给下方返回 ``pd.DataFrame`` 的
    同名函数。本函数返回嵌套 dict（可直接进 JSON 摘要），
    共同交易日不足 10 天时返回 None。
    """
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


# ============================================================================
# Top-N 组合回测（前端「Top-N Portfolio Combo Backtest」链路）
# ============================================================================
#
# 这一段服务的是另一条链路：前端
# ``POST /quant/admin/api/strategy-fitness/portfolio-backtest``
# （src/http/routes/strategy-fitness.ts）把 config 以 **dict** 传进来，
# 权重由算法算而不是写在 YAML 里，NAV 一律用 ``pd.Series``。
# 与上方 YAML 链路共用数据结构，但输入/输出口径不同，函数各自独立。


# 配权模式 → 中文标签。键必须与前端下发的 ``allocation_mode`` 取值一致。
ALLOCATION_MODE_MAP: dict[str, str] = {
    "equal": "等权",
    "risk_parity": "风险平价",
    "inverse_vol": "逆波动率",
    "max_sharpe": "最大夏普",
    "min_variance": "最小方差",
    "target_vol": "目标波动率",
    "max_diversification": "最大分散化",
    "custom": "自定义权重",
}

# 协方差类算法所需的最少日收益观测数。不足则退回等权 —— 用 3 个点估出来的
# 协方差矩阵去做风险平价，结果完全由噪声决定，比等权更危险（它看起来像
# 是「算过的」）。
_MIN_COV_OBS = 10

# 回撤曲线返回给前端的最大点数（再多前端图也画不出差别，白传）
_MAX_DD_POINTS = 500


def parse_multi_strategy_config(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[float], str, str]:
    """解析前端传来的组合配置 dict。

    与 :func:`parse_multi_strategy_yaml` 的区别：那条按路径读 YAML、要求
    ``portfolio.strategies``；这条吃的是顶层 ``strategies`` 的 dict。

    Args:
        config: {"allocation_mode":..., "rebalance_freq":..., "strategies":[
                 {"name":..., "weight":..., "config":{...}}, ...]}

    Returns:
        (子策略参数 dict 列表, 权重列表, allocation_mode, rebalance_freq)
    """
    mode = str(config.get("allocation_mode") or "equal")
    freq = str(config.get("rebalance_freq") or "monthly")

    strategies = config.get("strategies") or []
    if not strategies:
        return [], [], mode, freq

    n = len(strategies)
    cfgs = [dict(s.get("config") or {}) for s in strategies]

    if mode == "equal":
        # equal 模式下配置里写的 weight 一律忽略，避免「模式说等权、
        # 权重却是 0.8/0.2」这种自相矛盾的配置静默生效。
        weights = [1.0 / n] * n
    else:
        weights = [float(s.get("weight", 0.0) or 0.0) for s in strategies]
        total = sum(weights)
        if total <= 0:
            logger.warning("权重之和 %.4f <= 0，退回等权", total)
            weights = [1.0 / n] * n
        elif abs(total - 1.0) > 1e-9:
            logger.warning("权重之和 %.4f != 1.0，按比例归一化", total)
            weights = [w / total for w in weights]

    return cfgs, weights, mode, freq


def build_sub_strategy_config(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """把 base 配置与子策略 override 合并（override 优先）。

    递归合并嵌套 dict，其余类型整体覆盖。不修改任何入参（深拷贝）。
    """
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = build_sub_strategy_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _as_float_series(nav: Any) -> pd.Series:
    """把入参统一成 float 型 pd.Series（丢掉 NaN）。"""
    s = nav if isinstance(nav, pd.Series) else pd.Series(nav)
    return s.astype(float).dropna()


def _aligned_returns(
    navs: list[Any],
    labels: list[str],
) -> dict[str, list[float]]:
    """对齐到共同日期后计算各策略日收益率。

    只保留所有策略都有数据的日期（内连接）—— 缺失日 ffill 出来的 0 收益
    会把波动率压低，从而让缺数据的策略在配权时显得「更稳」。
    """
    frame = pd.concat([_as_float_series(s) for s in navs], axis=1, keys=labels)
    rets = frame.pct_change().dropna()
    return {lab: [float(x) for x in rets[lab].tolist()] for lab in labels}


def _max_sharpe_weights(
    labels: list[str],
    returns_dict: dict[str, list[float]],
) -> dict[str, float]:
    """最大夏普（对角近似）：权重 ∝ max(夏普, 0)。

    完整解是 ``w ∝ Σ⁻¹μ``，需要矩阵求逆且会给出负权重（做空）。这里按
    对角近似（忽略相关性）并把负夏普截到 0，保证 long-only。
    全部策略夏普 ≤ 0 时退回等权：此时「最大夏普」没有有意义的解，
    押注最不亏的那个不是配权而是赌方向。
    """
    from aipha.portfolio.cross_asset_allocator import compute_volatility

    scores: dict[str, float] = {}
    for lab in labels:
        rets = returns_dict.get(lab, [])
        if len(rets) < 2:
            scores[lab] = 0.0
            continue
        vol = compute_volatility(rets)
        mean = sum(rets) / len(rets)
        scores[lab] = max(mean / vol, 0.0) if vol > 1e-12 else 0.0

    total = sum(scores.values())
    if total <= 0:
        logger.warning("所有策略夏普 <= 0，max_sharpe 退回等权")
        return {lab: 1.0 / len(labels) for lab in labels}
    return {lab: v / total for lab, v in scores.items()}


def _max_diversification_weights(
    labels: list[str],
    cov: dict[str, dict[str, float]],
) -> dict[str, float]:
    """最大分散化（近似）：权重 ∝ 1 / (σ_i × 平均相关性_i)。

    严格的 MDP 解是 ``w ∝ Σ⁻¹σ``（同样需要求逆且允许负权重）。这里用
    「波动率 × 与其他策略的平均相关性」作为惩罚项：又贵（高波动）又
    不带来分散化（高相关）的策略权重最低。平均相关性截断到 [0.05, 1]
    以免负相关策略拿到无穷大权重。
    """
    from aipha.portfolio.cross_asset_allocator import compute_correlation_matrix

    corr = compute_correlation_matrix(cov)
    n = len(labels)
    raw: dict[str, float] = {}
    for lab in labels:
        sigma = math.sqrt(max(cov[lab][lab], 1e-12))
        others = [corr[lab][o] for o in labels if o != lab]
        avg_corr = sum(others) / len(others) if others else 1.0
        avg_corr = min(max(avg_corr, 0.05), 1.0)
        raw[lab] = 1.0 / (sigma * avg_corr)

    total = sum(raw.values())
    if total <= 0:
        return {lab: 1.0 / n for lab in labels}
    return {lab: v / total for lab, v in raw.items()}


def _allocate_weights(
    navs: list[Any],
    mode: str = "equal",
    custom_weights: list[float] | None = None,
) -> list[float]:
    """按 ``mode`` 给各策略 NAV 序列配权。

    返回的权重**恒为非负且和为 1**（全额投资、不做空、不加杠杆）。
    目标波动率所需的杠杆缩放不在这里 —— 见
    ``aipha/portfolio/vol_targeting.py``，那是叠加在权重之上的一层。

    equal / inverse_vol / risk_parity / min_variance 直接复用
    ``aipha.portfolio.cross_asset_allocator`` 里的实现（同一套算法只保留
    一份，避免两处实现慢慢漂移）。

    Args:
        navs: 各策略 NAV 序列（pd.Series）
        mode: :data:`ALLOCATION_MODE_MAP` 中的键；未知值退回等权
        custom_weights: 仅 ``mode="custom"`` 时使用，长度须与 navs 一致

    Returns:
        与 navs 等长的权重列表
    """
    n = len(navs)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    equal = [1.0 / n] * n

    if mode == "custom":
        if custom_weights and len(custom_weights) == n:
            total = float(sum(custom_weights))
            if total > 0:
                return [float(w) / total for w in custom_weights]
            logger.warning("custom_weights 之和 <= 0，退回等权")
        else:
            logger.warning(
                "custom_weights 长度 %s != 策略数 %s，退回等权",
                len(custom_weights) if custom_weights else 0, n,
            )
        return equal

    if mode == "equal":
        return equal

    if mode not in ALLOCATION_MODE_MAP:
        logger.warning("未知配权模式 '%s'，退回等权", mode)
        return equal

    from aipha.portfolio.cross_asset_allocator import (
        _inverse_volatility,
        _min_variance,
        _risk_parity,
        compute_covariance_matrix,
    )

    labels = [f"s{i}" for i in range(n)]
    returns_dict = _aligned_returns(navs, labels)
    n_obs = min((len(v) for v in returns_dict.values()), default=0)
    if n_obs < _MIN_COV_OBS:
        logger.warning(
            "共同交易日仅 %s 天（< %s），'%s' 退回等权",
            n_obs, _MIN_COV_OBS, mode,
        )
        return equal

    if mode in ("inverse_vol", "target_vol"):
        # target_vol 在权重层面与 inverse_vol 一致：把组合波动压到目标值
        # 靠的是整体杠杆（vol_targeting overlay），而权重必须和为 1。
        weights = _inverse_volatility(labels, returns_dict)
    else:
        cov = compute_covariance_matrix(returns_dict)
        if mode == "risk_parity":
            weights = _risk_parity(labels, cov)
        elif mode == "min_variance":
            weights = _min_variance(labels, cov)
        elif mode == "max_sharpe":
            weights = _max_sharpe_weights(labels, returns_dict)
        elif mode == "max_diversification":
            weights = _max_diversification_weights(labels, cov)
        else:  # pragma: no cover - ALLOCATION_MODE_MAP 已覆盖全部分支
            logger.warning("配权模式 '%s' 尚未实现，退回等权", mode)
            return equal

    # 兜底：截负 + 归一化。上面每个算法都该满足，但配权结果直接决定下多少
    # 单，不能依赖「应该」。
    vals = [max(float(weights.get(lab, 0.0)), 0.0) for lab in labels]
    total = sum(vals)
    if total <= 0:
        logger.warning("'%s' 配权结果全为 0，退回等权", mode)
        return equal
    return [v / total for v in vals]


def _combine_nav_series(
    navs: list[Any],
    weights: list[float],
) -> pd.Series:
    """按权重合成组合净值曲线（归一化口径）。

    每条 NAV 先除以自身首值（各策略起点不同、初始资金也不同，不归一化
    直接相加等于按绝对资金量隐式再配一次权），再加权求和。返回的曲线
    从 1.0 附近起步，乘上总资金即为组合 NAV。

    日期取并集并前向填充；某策略尚未开始的日期按 1.0（持平）计入。
    """
    if not navs:
        return pd.Series(dtype=float)

    n = len(navs)
    if weights and len(weights) == n:
        total = float(sum(weights))
        w = [float(x) / total for x in weights] if total > 0 else [1.0 / n] * n
    else:
        logger.warning("权重数 %s != 策略数 %s，按等权合成", len(weights or []), n)
        w = [1.0 / n] * n

    normalized: list[pd.Series] = []
    for nav in navs:
        s = _as_float_series(nav)
        if s.empty:
            normalized.append(s)
            continue
        base = float(s.iloc[0])
        normalized.append(s / base if base else s * 0.0 + 1.0)

    frame = pd.concat(normalized, axis=1).sort_index().ffill().fillna(1.0)
    combined = frame.iloc[:, 0] * w[0]
    for i in range(1, n):
        combined = combined + frame.iloc[:, i] * w[i]
    return combined


def _compute_portfolio_metrics(nav: Any) -> dict[str, float]:
    """从组合净值曲线算年化收益 / 最大回撤 / 夏普。

    口径与 :func:`_metrics_from_nav_dict` 一致：年化按自然日 CAGR，
    夏普按 sqrt(244)（§13.8）。
    """
    zero = {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}
    if nav is None or len(nav) == 0:
        return dict(zero)

    s = _as_float_series(nav)
    if len(s) < 2:
        return dict(zero)

    rets = s.pct_change().dropna()
    if rets.empty:
        return dict(zero)

    first = float(s.iloc[0])
    total_return = float(s.iloc[-1]) / first - 1.0 if first else 0.0

    # 年化：自然日 CAGR（索引不是日期时退回累计收益，不硬造年化）
    annual_return = total_return
    try:
        n_days = (pd.Timestamp(s.index[-1]) - pd.Timestamp(s.index[0])).days
    except (TypeError, ValueError):
        n_days = 0
    if n_days > 0:
        growth = 1.0 + total_return
        if growth <= 0:
            annual_return = -1.0
        else:
            try:
                annual_return = growth ** (365.0 / n_days) - 1.0
            except OverflowError:
                annual_return = total_return

    running_max = s.cummax()
    valid = running_max > 0
    max_dd = 0.0
    if bool(valid.any()):
        dd = (running_max[valid] - s[valid]) / running_max[valid]
        dd_max = float(dd.max())
        max_dd = 0.0 if math.isnan(dd_max) else max(dd_max, 0.0)

    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    sharpe = 0.0
    if std > 1e-12 and not math.isnan(std):
        sharpe = float(rets.mean()) / std * math.sqrt(TRADING_DAYS_PER_YEAR)

    return {
        "annual_return": annual_return,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
    }


def _compute_correlation(
    navs: list[Any],
    names: list[str],
) -> pd.DataFrame:
    """子策略日收益率相关性矩阵（DataFrame 口径）。

    少于 2 个策略时返回空 DataFrame（相关性无从谈起）。
    """
    if len(navs) < 2:
        return pd.DataFrame()

    cols = (
        list(names)
        if names and len(names) == len(navs)
        else [f"s{i}" for i in range(len(navs))]
    )
    frame = pd.concat([_as_float_series(s) for s in navs], axis=1, keys=cols)
    return frame.pct_change().dropna().corr()


def _compute_win_rate(nav: Any) -> float:
    """日胜率 = 上涨交易日 / 有效交易日。

    平盘（收益恰为 0）计入分母不计入分子 —— 停牌或空仓的日子不算赢。
    """
    if nav is None or len(nav) == 0:
        return 0.0
    s = _as_float_series(nav)
    if len(s) < 2:
        return 0.0
    rets = s.pct_change().dropna()
    if rets.empty:
        return 0.0
    return float((rets > 0).sum()) / float(len(rets))


def _compute_calmar(nav: Any) -> float:
    """Calmar = 年化收益 / 最大回撤。

    最大回撤为 0 时返回 0.0 而不是 inf：曲线没回撤过通常意味着样本太短
    或根本没交易，报一个无穷大的 Calmar 只会污染排序。
    """
    if nav is None or len(nav) == 0:
        return 0.0
    metrics = _compute_portfolio_metrics(nav)
    mdd = metrics["max_drawdown"]
    if mdd <= 1e-12:
        return 0.0
    return metrics["annual_return"] / mdd


def _compute_monthly_returns(nav: Any) -> list[dict[str, Any]]:
    """按月收益率明细：[{"month": "YYYY-MM", "return": 0.0123}, ...]

    首月以序列首值为基准，其后各月以上月末净值为基准。
    """
    if nav is None or len(nav) == 0:
        return []
    s = _as_float_series(nav)
    if s.empty:
        return []

    try:
        periods = pd.to_datetime(pd.Series(s.index)).dt.to_period("M")
    except (TypeError, ValueError):
        logger.warning("NAV 索引不是日期类型，无法计算月度收益")
        return []

    monthly = s.groupby(periods.to_numpy()).last()
    out: list[dict[str, Any]] = []
    prev = float(s.iloc[0])
    for period, value in monthly.items():
        current = float(value)
        ret = current / prev - 1.0 if prev else 0.0
        out.append({"month": str(period), "return": round(ret, 6)})
        prev = current
    return out


def _compute_drawdown_curve(nav: Any) -> list[dict[str, Any]]:
    """回撤曲线：[{"date": "YYYY-MM-DD", "drawdown": -0.0123}, ...]

    ``drawdown`` 为**非正数**（0 表示处于新高）。超过
    :data:`_MAX_DD_POINTS` 个点时等间隔抽稀。
    """
    if nav is None or len(nav) == 0:
        return []
    s = _as_float_series(nav)
    if s.empty:
        return []

    running_max = s.cummax()
    dd = (s / running_max.where(running_max > 0) - 1.0).fillna(0.0)

    step = max(1, math.ceil(len(dd) / _MAX_DD_POINTS))
    dd = dd.iloc[::step]

    out: list[dict[str, Any]] = []
    for idx, value in dd.items():
        try:
            label = pd.Timestamp(idx).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            label = str(idx)
        out.append({"date": label, "drawdown": round(float(value), 6)})
    return out


def _compute_contributions(
    sub_results: list[SubStrategyResult],
    combined_nav: Any,
) -> list[dict[str, Any]]:
    """各子策略对组合收益的贡献。

    贡献 = 权重 × 该策略区间收益率。``contribution_pct`` 以贡献绝对值
    之和为分母 —— 用组合总收益做分母时，组合收益接近 0 会让百分比炸到
    几百甚至变号，那个数字没法读。
    """
    if not sub_results:
        return []

    rows: list[dict[str, Any]] = []
    for sr in sub_results:
        s = (
            _as_float_series(sr.nav_series)
            if sr.nav_series is not None
            else pd.Series(dtype=float)
        )
        if len(s) >= 2 and float(s.iloc[0]):
            total_return = float(s.iloc[-1]) / float(s.iloc[0]) - 1.0
        else:
            total_return = 0.0
        rows.append({
            "strategy_id": sr.name,
            "name": sr.name,
            "weight": round(float(sr.weight), 6),
            "total_return": round(total_return, 6),
            "contribution": round(float(sr.weight) * total_return, 6),
        })

    denom = sum(abs(r["contribution"]) for r in rows)
    for r in rows:
        r["contribution_pct"] = (
            round(r["contribution"] / denom * 100.0, 4) if denom > 1e-12 else 0.0
        )
    return rows


# ---------------------------------------------------------------------------
# 组合回测进度（前端轮询）
# ---------------------------------------------------------------------------


def _get_combo_progress_path() -> str:
    """组合回测进度文件路径。

    必须与前端读的位置一致：``strategy-fitness.ts`` 取的是
    ``path.join(path.dirname(getPythonDir()), "logs")``，即 python/ 的
    **上一级** logs/（quant-trading/logs/），不是 python/logs/。
    写错一层的话前端永远读到 ``{"status":"idle"}`` —— 进度条不动，
    而回测其实在跑。
    """
    return str(PROJECT_ROOT.parent / "logs" / "combo-backtest-progress.json")


def _write_combo_progress(payload: dict[str, Any]) -> None:
    """写进度文件（原子替换，失败只告警不打断回测）。"""
    path = Path(_get_combo_progress_path())
    data = dict(payload)
    data.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再 replace：前端随时可能在读，不能让它读到半个 JSON
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        logger.warning("写入组合回测进度失败 (%s): %s", path, exc)


def _read_combo_progress() -> dict[str, Any]:
    """读进度文件；不存在视为 idle，损坏时报 error 而不是装作 idle。"""
    path = Path(_get_combo_progress_path())
    if not path.exists():
        return {"status": "idle"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("组合回测进度文件无法解析 (%s): %s", path, exc)
        return {"status": "error", "error": str(exc)}
    return data if isinstance(data, dict) else {"status": "error", "error": "格式错误"}
