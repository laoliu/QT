#!/usr/bin/env python
"""A 股权益策略回测运行器

统一入口，支持按策略名运行 A 股权益策略回测。
使用 config_runner.run_from_config() 全链路，自动处理
数据加载（含 equity 元数据）、引擎组装、报告生成。

用法:
    # 运行单个策略
    python scripts/run_equity_backtest.py --strategy multifactor_enhance
    python scripts/run_equity_backtest.py --strategy smallcap_rotation
    python scripts/run_equity_backtest.py --strategy pead_event

    # 运行所有 A 股策略
    python scripts/run_equity_backtest.py --all

    # 指定自定义配置
    python scripts/run_equity_backtest.py --config configs/backtest/multifactor_enhance.yml

    # 使用合成数据测试管道（无需 TuShare Token）
    python scripts/run_equity_backtest.py --strategy multifactor_enhance --synthetic

    # 指定输出目录
    python scripts/run_equity_backtest.py --strategy multifactor_enhance -o output/test_run

环境变量:
    TUSHARE_TOKEN: TuShare Pro API Token（真实数据必需）
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logger = logging.getLogger(__name__)

# A 股权益策略 → 默认回测配置映射
EQUITY_STRATEGY_CONFIGS: dict[str, str] = {
    "multifactor_enhance": "configs/backtest/multifactor_enhance.yml",
    "smallcap_rotation": "configs/backtest/smallcap_rotation.yml",
    "pead_event": "configs/backtest/pead_event.yml",
    "momentum": "configs/backtest/momentum_hs300.yml",
    "dual_ma": "configs/backtest/dual_ma.yml",
    "mean_reversion": "configs/backtest/mean_reversion.yml",
    "multifactor_hs300": "configs/backtest/multifactor_hs300.yml",
    "factor_based": "configs/backtest/factor_based.yml",
    "etf_rotation": "configs/backtest/etf_rotation_10y.yml",
}

# 生产级策略（优先级排序）
PRODUCTION_STRATEGIES = [
    "multifactor_enhance",
    "smallcap_rotation",
    "pead_event",
]


def setup_logging(verbose: bool = False) -> None:
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_single_strategy(
    strategy_name: str,
    config_path: str | None = None,
    output_dir: str | None = None,
    synthetic: bool = False,
) -> dict:
    """运行单个策略的回测

    Args:
        strategy_name: 策略名称
        config_path: 自定义配置路径（None 使用默认）
        output_dir: 输出目录
        synthetic: 是否使用合成数据

    Returns:
        结果摘要字典
    """
    from aipha.backtest.config_runner import run_from_config, load_config

    # 确定配置文件路径
    if config_path is None:
        config_path = EQUITY_STRATEGY_CONFIGS.get(strategy_name)
        if config_path is None:
            raise ValueError(
                f"未知策略: {strategy_name}。"
                f"可选: {list(EQUITY_STRATEGY_CONFIGS.keys())}"
            )

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_file}")

    # 如果使用合成数据，临时修改配置
    if synthetic:
        import yaml
        import tempfile

        with open(config_file) as f:
            cfg = yaml.safe_load(f)

        cfg["data"]["provider"] = "synthetic"
        cfg["data"]["source"] = "synthetic"
        # 合成数据时缩短日期范围
        cfg["run"]["start_date"] = "2023-01-01"
        cfg["run"]["end_date"] = "2023-06-30"

        # 写入临时文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, dir="."
        ) as tmp:
            yaml.dump(cfg, tmp, allow_unicode=True)
            config_file = Path(tmp.name)

    # 确定输出目录
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = f"output/equity_{strategy_name}/{timestamp}"

    logger.info(f"{'=' * 60}")
    logger.info(f"策略: {strategy_name}")
    logger.info(f"配置: {config_file}")
    logger.info(f"输出: {output_dir}")
    logger.info(f"数据: {'合成' if synthetic else '真实(TuShare)'}")
    logger.info(f"{'=' * 60}")

    start_time = time.time()

    try:
        result = run_from_config(str(config_file), output_dir=output_dir)
        elapsed = time.time() - start_time

        # 提取关键指标
        summary = {
            "strategy": strategy_name,
            "status": "SUCCESS",
            "elapsed_sec": round(elapsed, 1),
            "final_nav": result.result.final_nav,
            "total_trades": result.result.total_trades,
            "total_fees": result.result.total_fees.total if result.result.total_fees else 0,
        }

        if result.report is not None:
            perf = result.report.performance
            dd = result.report.drawdown
            summary.update({
                "total_return": round(perf.total_return * 100, 2),
                "annualized_return": round(perf.annualized_return * 100, 2),
                "volatility": round(perf.volatility * 100, 2),
                "sharpe": round(perf.sharpe_ratio, 3),
                "sortino": round(getattr(perf, "sortino_ratio", 0) or 0, 3),
                "calmar": round(getattr(perf, "calmar_ratio", 0) or 0, 3),
                "max_drawdown": round(dd.max_drawdown_pct * 100, 2),
                "turnover": round(
                    getattr(result.report.trading, "turnover_rate", 0) or 0, 3
                ),
                "win_rate": round(
                    getattr(result.report.trading, "win_rate", 0) or 0, 3
                ),
            })

        if result.html_report_path:
            summary["html_report"] = str(result.html_report_path)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"回测结果: {strategy_name}")
        logger.info(f"  耗时: {elapsed:.1f}s")
        logger.info(f"  最终净值: {summary['final_nav']:,.2f}")
        logger.info(f"  总交易: {summary['total_trades']} 笔")
        if "annualized_return" in summary:
            logger.info(f"  年化收益: {summary['annualized_return']:.2f}%")
            logger.info(f"  最大回撤: {summary['max_drawdown']:.2f}%")
            logger.info(f"  Sharpe: {summary['sharpe']:.3f}")
            logger.info(f"  Calmar: {summary['calmar']:.3f}")
        logger.info(f"{'=' * 60}\n")

        return summary

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"策略 {strategy_name} 回测失败: {e}", exc_info=True)
        return {
            "strategy": strategy_name,
            "status": "FAILED",
            "error": str(e),
            "elapsed_sec": round(elapsed, 1),
        }
    finally:
        # 清理临时文件
        if synthetic and config_file.name.endswith(".yml") and config_file != Path(config_path):
            try:
                config_file.unlink()
            except OSError:
                pass


def run_all_strategies(
    output_base: str = "output/equity_batch",
    synthetic: bool = False,
    production_only: bool = True,
) -> list[dict]:
    """运行所有 A 股策略回测

    Args:
        output_base: 输出基础目录
        synthetic: 是否使用合成数据
        production_only: 仅运行生产级策略

    Returns:
        所有策略结果摘要列表
    """
    import json

    strategies = PRODUCTION_STRATEGIES if production_only else list(EQUITY_STRATEGY_CONFIGS.keys())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    results = []
    for strategy_name in strategies:
        output_dir = f"{output_base}/{timestamp}/{strategy_name}"
        summary = run_single_strategy(
            strategy_name, output_dir=output_dir, synthetic=synthetic
        )
        results.append(summary)

    # 打印汇总
    logger.info(f"\n{'=' * 80}")
    logger.info("A 股权益策略回测汇总")
    logger.info(f"{'=' * 80}")
    header = f"{'策略':<25} {'状态':<10} {'年化收益':>10} {'最大回撤':>10} {'Sharpe':>8} {'Calmar':>8}"
    logger.info(header)
    logger.info("-" * 80)
    for r in results:
        if r["status"] == "SUCCESS" and "annualized_return" in r:
            logger.info(
                f"{r['strategy']:<25} {'✓':^10} "
                f"{r['annualized_return']:>9.2f}% "
                f"{r['max_drawdown']:>9.2f}% "
                f"{r['sharpe']:>8.3f} "
                f"{r['calmar']:>8.3f}"
            )
        else:
            logger.info(f"{r['strategy']:<25} {'✗':^10} {r.get('error', 'N/A')}")
    logger.info(f"{'=' * 80}\n")

    # 保存汇总 JSON
    summary_dir = Path(f"{output_base}/{timestamp}")
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / "batch_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"汇总保存至: {summary_path}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="A 股权益策略回测运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python scripts/run_equity_backtest.py --strategy multifactor_enhance
    python scripts/run_equity_backtest.py --all --synthetic
    python scripts/run_equity_backtest.py --config configs/backtest/smallcap_rotation.yml
        """,
    )
    parser.add_argument(
        "--strategy", "-s",
        choices=list(EQUITY_STRATEGY_CONFIGS.keys()),
        help="运行指定策略",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="运行所有生产级 A 股策略",
    )
    parser.add_argument(
        "--all-strategies",
        action="store_true",
        help="运行全部 A 股策略（含 Demo）",
    )
    parser.add_argument(
        "--config", "-c",
        help="指定自定义配置文件路径",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出目录",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="使用合成数据（无需 TuShare Token）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.all or args.all_strategies:
        run_all_strategies(
            output_base=args.output or "output/equity_batch",
            synthetic=args.synthetic,
            production_only=not args.all_strategies,
        )
    elif args.strategy or args.config:
        strategy_name = args.strategy or Path(args.config).stem
        run_single_strategy(
            strategy_name=strategy_name,
            config_path=args.config,
            output_dir=args.output,
            synthetic=args.synthetic,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
