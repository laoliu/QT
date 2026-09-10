"""Walk-Forward 配置驱动验证运行器

基于现有回测 YAML 配置，自动执行 Walk-Forward 样本外验证。

用法:
    python scripts/wf_config_runner.py configs/backtest/multifactor_enhance_5y.yml

    # 自定义分折参数
    python scripts/wf_config_runner.py configs/backtest/pead_event_5y.yml \\
        --train-days 252 --test-days 63 --n-splits 5 --purged

工作原理:
    1. 加载 YAML 配置获取策略 + 引擎组件
    2. 加载全量数据（使用缓存）
    3. 为每个 fold 筛选日期范围的数据子集
    4. 使用真实 BacktestEngine + 完整组件链 运行回测
    5. 提取指标 (return, sharpe, max_drawdown)
    6. 输出 ValidationReport + judge + score
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

# 确保 src 在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aipha.backtest.config_runner import (  # noqa: E402
    _create_engine_components,
    _create_strategy,
    _load_data,
    _build_universe_policy,
    _build_risk_manager,
    _build_stop_loss_manager,
    _build_market_regime_detector,
    _build_weight_optimizer,
    load_config,
    to_backtest_config,
    validate_config,
)
from aipha.backtest.engine import BacktestEngine  # noqa: E402
from aipha.backtest.report import (  # noqa: E402
    compute_drawdown_analysis,
    compute_performance_metrics,
)
from aipha.backtest.validation import (  # noqa: E402
    WalkForwardThresholds,
    WalkForwardValidator,
    PurgedWalkForwardSplitter,
    TimeSeriesSplitter,
    compute_wf_score,
    judge_walk_forward,
    auto_n_splits,
)
from aipha.backtest.wf_statistical_validation import (  # noqa: E402
    summarize_wf_statistical_validation,
)
from aipha.backtest.quality_gates import (  # noqa: E402
    check_overfitting_gates,
    format_quality_report,
)

logger = logging.getLogger(__name__)


def _extract_daily_returns(backtest_result: Any) -> list[float]:
    """从 BacktestResult 提取日收益序列。"""
    snapshots = getattr(backtest_result, "daily_snapshots", None) or []
    returns: list[float] = []
    for snap in snapshots:
        daily_return = getattr(snap, "daily_return", None)
        if daily_return is None:
            continue
        try:
            returns.append(float(daily_return))
        except (TypeError, ValueError):
            continue
    return returns


def run_walk_forward_from_config(
    config_path: str | Path,
    *,
    train_days: int = 252,
    test_days: int = 63,
    n_splits: int | None = None,
    purged: bool = False,
    purge_days: int = 5,
    embargo_days: int = 2,
    thresholds: WalkForwardThresholds | None = None,
) -> dict[str, Any]:
    """从 YAML 配置执行 Walk-Forward 验证

    Args:
        config_path: YAML 回测配置文件路径
        train_days: IS 训练期天数 (默认 252，即 1 年)
        test_days: OOS 测试期天数 (默认 63，即 1 季度)
        n_splits: 分折数 (None=自动计算)
        purged: 是否使用 Purged WF (含 purge+embargo 抗信息泄露)
        purge_days: Purged WF 训练集末尾清洗天数
        embargo_days: Purged WF 测试集后禁用天数
        thresholds: 验收门槛配置

    Returns:
        dict 含 report (ValidationReport), verdict, score, splits_detail
    """
    config_path = Path(config_path)
    logger.info(f"Walk-Forward 验证: {config_path}")

    # 1. 加载配置
    config = load_config(config_path)
    errors = validate_config(config)
    if errors:
        raise ValueError(f"配置验证失败:\n  " + "\n  ".join(errors))

    # 2. 构建引擎配置（一次性）
    engine_config = to_backtest_config(config)

    # 3. 加载全量数据
    logger.info("加载全量数据...")
    all_bars = _load_data(config)

    if not all_bars:
        raise ValueError("数据加载失败：无可用数据")

    sorted_dates = sorted(all_bars.keys())
    data_start = sorted_dates[0]
    data_end = sorted_dates[-1]
    total_data_days = (data_end - data_start).days
    n_trading_days = len(sorted_dates)

    logger.info(
        f"数据范围: {data_start} ~ {data_end} "
        f"({n_trading_days} 交易日, {total_data_days} 日历天)"
    )

    # 4. 自动计算分折数（使用交易日数，非日历天数）
    if n_splits is None:
        n_splits = auto_n_splits(n_trading_days, train_days, test_days)
        logger.info(f"自动计算分折数: {n_splits}")

    # 5. 创建分割器和验证器
    if purged:
        splitter = PurgedWalkForwardSplitter(
            train_period_days=train_days,
            test_period_days=test_days,
            n_splits=n_splits,
            purge_days=purge_days,
            embargo_days=embargo_days,
        )
    else:
        splitter = TimeSeriesSplitter(
            train_period_days=train_days,
            test_period_days=test_days,
            n_splits=n_splits,
        )

    validator = WalkForwardValidator(splitter=splitter)
    fold_oos_daily_returns: list[list[float]] = []
    split_periods = splitter.split(data_start, data_end)
    test_period_keys = {
        (period.test_start, period.test_end)
        for period in split_periods
    }

    # 6. 构建 run_fn: 每个 fold 创建新的策略实例 + 引擎
    strategy_name = config.strategy.name

    def _run_fold(start: date, end: date) -> dict[str, float]:
        """单个 fold 的回测执行"""
        fold_bars = {
            d: bars for d, bars in all_bars.items()
            if start <= d <= end
        }

        if len(fold_bars) < 10:
            logger.warning(
                f"Fold {start}~{end}: 仅 {len(fold_bars)} 个交易日，跳过"
            )
            return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}

        # 每个 fold 创建全新策略 + 引擎（避免状态泄露）
        strategy = _create_strategy(config)
        fill_model, slippage, fee_model, rules = _create_engine_components(config)
        universe_policy = _build_universe_policy(config)
        risk_manager = _build_risk_manager(config)
        stop_loss = _build_stop_loss_manager(config)
        regime = _build_market_regime_detector(config)
        optimizer = _build_weight_optimizer(config)

        engine = BacktestEngine(
            config=engine_config,
            strategy=strategy,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
            universe_policy=universe_policy,
            risk_manager=risk_manager,
            stop_loss_manager=stop_loss,
            market_regime_detector=regime,
            weight_optimizer=optimizer,
            benchmark_symbol=config.risk.benchmark_symbol,
        )

        try:
            result = engine.run(fold_bars)
        except Exception as e:
            logger.error(f"Fold {start}~{end} 回测失败: {e}")
            return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}

        if (start, end) in test_period_keys:
            fold_oos_daily_returns.append(_extract_daily_returns(result))

        perf = compute_performance_metrics(result)
        dd = compute_drawdown_analysis(result)

        metrics = {
            "return": perf.total_return,
            "sharpe": perf.sharpe_ratio,
            "max_drawdown": dd.max_drawdown_pct,
        }
        logger.info(
            f"  Fold {start}~{end}: return={metrics['return']:.2%}, "
            f"sharpe={metrics['sharpe']:.2f}, mdd={metrics['max_drawdown']:.2%}"
        )
        return metrics

    # 7. 执行 Walk-Forward 验证
    logger.info(
        f"开始 {'Purged ' if purged else ''}Walk-Forward 验证: "
        f"train={train_days}d, test={test_days}d, n_splits={n_splits}"
    )

    report = validator.validate_with_callable(
        run_fn=_run_fold,
        start_date=data_start,
        end_date=data_end,
        strategy_name=strategy_name,
    )

    # 8. 判定与评分
    effective_thresholds = thresholds or WalkForwardThresholds()
    verdict = judge_walk_forward(report, thresholds=effective_thresholds)
    score = compute_wf_score(report)
    statistical_validation = summarize_wf_statistical_validation(
        fold_oos_daily_returns,
        n_trials=max(n_splits, 1),
    )

    # 8b. 把 DSR / PBO 变成判定，而不只是一条日志
    overfitting_report = check_overfitting_gates(
        statistical_validation, strategy_name=strategy_name,
    )

    # 9. 输出结果
    logger.info(report.summary())
    logger.info(f"判定: {verdict} | 评分: {score}/10")
    if statistical_validation.get("pbo") is not None:
        logger.info(
            "WF统计验证: DSR=%s HLZ=%s PBO=%s",
            statistical_validation.get("dsr"),
            statistical_validation.get("hlz_adjusted_t"),
            statistical_validation.get("pbo"),
        )
    if not overfitting_report.all_passed:
        logger.error("过拟合门禁未通过:\n%s", format_quality_report(overfitting_report))

    return {
        "report": report,
        "verdict": verdict,
        "score": score,
        "statistical_validation": statistical_validation,
        "overfitting_gate": {
            "all_passed": overfitting_report.all_passed,
            "errors": [gr.message for gr in overfitting_report.errors],
            "warnings": [gr.message for gr in overfitting_report.warnings],
            "skipped": [gr.gate.metric for gr in overfitting_report.skipped],
        },
        "config_path": str(config_path),
        "strategy_name": strategy_name,
        "data_range": f"{data_start} ~ {data_end}",
        "n_trading_days": n_trading_days,
        "train_days": train_days,
        "test_days": test_days,
        "n_splits": n_splits,
        "purged": purged,
    }


def run_batch_walk_forward(
    config_paths: list[str | Path],
    *,
    train_days: int = 252,
    test_days: int = 63,
    n_splits: int | None = None,
    purged: bool = False,
    purge_days: int = 5,
    embargo_days: int = 2,
    thresholds: WalkForwardThresholds | None = None,
    stop_on_error: bool = False,
) -> list[dict[str, Any]]:
    """批量执行多个配置的 Walk-Forward 验证。

    Args:
        config_paths: 配置文件路径列表
        train_days, test_days, n_splits, purged, purge_days, embargo_days:
            与 run_walk_forward_from_config 相同
        thresholds: 验收门槛配置
        stop_on_error: 遇到错误是否停止（默认 False，跳过失败的配置继续）

    Returns:
        列表，每项为 run_walk_forward_from_config 的结果字典，
        若失败则该项多一个 "error" 字段
    """
    results: list[dict[str, Any]] = []
    for i, cfg_path in enumerate(config_paths):
        logger.info(f"[Batch {i + 1}/{len(config_paths)}] {cfg_path}")
        try:
            result = run_walk_forward_from_config(
                config_path=cfg_path,
                train_days=train_days,
                test_days=test_days,
                n_splits=n_splits,
                purged=purged,
                purge_days=purge_days,
                embargo_days=embargo_days,
                thresholds=thresholds,
            )
            results.append(result)
        except Exception as e:
            logger.error(f"[Batch {i + 1}] {cfg_path} 失败: {e}")
            if stop_on_error:
                raise
            results.append({
                "config_path": str(cfg_path),
                "error": str(e),
                "verdict": "ERROR",
                "score": 0.0,
                "strategy_name": str(cfg_path),
            })
    return results


def main() -> int:
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="Walk-Forward 样本外验证运行器"
    )
    parser.add_argument(
        "config", type=str,
        help="YAML 回测配置文件路径",
    )
    parser.add_argument(
        "--train-days", type=int, default=252,
        help="训练期天数 (默认 252)",
    )
    parser.add_argument(
        "--test-days", type=int, default=63,
        help="测试期天数 (默认 63)",
    )
    parser.add_argument(
        "--n-splits", type=int, default=None,
        help="分折数 (默认自动计算)",
    )
    parser.add_argument(
        "--purged", action="store_true",
        help="使用 Purged WF (含 purge+embargo)",
    )
    parser.add_argument(
        "--purge-days", type=int, default=5,
        help="Purged WF: 训练集末尾清洗天数 (默认 5)",
    )
    parser.add_argument(
        "--embargo-days", type=int, default=2,
        help="Purged WF: 测试集后禁用天数 (默认 2)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="结果输出 JSON 路径",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    result = run_walk_forward_from_config(
        config_path=args.config,
        train_days=args.train_days,
        test_days=args.test_days,
        n_splits=args.n_splits,
        purged=args.purged,
        purge_days=args.purge_days,
        embargo_days=args.embargo_days,
    )

    # 输出 JSON
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_dict = result["report"].to_dict()
        output_data = {
            **report_dict,
            "verdict": result["verdict"],
            "score": result["score"],
            "config_path": result["config_path"],
            "data_range": result["data_range"],
            "n_trading_days": result["n_trading_days"],
            "statistical_validation": result["statistical_validation"],
            "params": {
                "train_days": result["train_days"],
                "test_days": result["test_days"],
                "n_splits": result["n_splits"],
                "purged": result["purged"],
            },
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        logger.info(f"结果已保存: {output_path}")

        # 写入 SQLite 索引
        try:
            from aipha.backtest.result_store import ResultStore, wf_json_to_wf_dict

            wf_dict = wf_json_to_wf_dict(output_path)
            store = ResultStore()
            store.upsert_wf_result(wf_dict)
            store.close()
            logger.info("WF 结果已索引到 SQLite")
        except Exception as _e:
            logger.debug("WF SQLite 索引写入失败（不影响结果）: %s", _e)

    print(f"\n{'='*60}")
    print(f"策略: {result['strategy_name']}")
    print(f"数据: {result['data_range']} ({result['n_trading_days']} 交易日)")
    print(f"模式: {'Purged ' if result['purged'] else ''}WF "
          f"(train={result['train_days']}d, test={result['test_days']}d, "
          f"splits={result['n_splits']})")
    print(f"判定: {result['verdict']}")
    print(f"评分: {result['score']}/10")
    print(f"{'='*60}")

    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
