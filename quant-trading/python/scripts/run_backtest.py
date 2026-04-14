#!/usr/bin/env python
"""配置驱动的回测命令行工具

用法:
    python scripts/run_backtest.py --config configs/backtest/examples/momentum_daily.yml
    python scripts/run_backtest.py --config config.yml --output output/my_run
    python scripts/run_backtest.py --config config.yml --dry-run  # 仅验证配置

选项:
    --config, -c     配置文件路径（YAML/JSON）
    --output, -o     输出目录（覆盖配置中的设置）
    --dry-run        仅验证配置，不运行回测
    --verbose, -v    显示详细信息
"""

import argparse
import logging
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aipha.backtest.config_runner import (
    load_config,
    run_from_config,
    validate_config,
)
from aipha.backtest.html_report import generate_html_report
from aipha.backtest.report import generate_report


def setup_logging(verbose: bool = False):
    """设置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="配置驱动的回测工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="配置文件路径（YAML/JSON）",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出目录（覆盖配置中的设置）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅验证配置，不运行回测",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细信息",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="生成HTML可视化报告",
    )

    args = parser.parse_args()

    # 设置日志
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # 加载配置
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    logger.info(f"加载配置: {config_path}")
    try:
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        sys.exit(1)

    # 验证配置
    errors = validate_config(config)
    if errors:
        logger.error("配置验证失败:")
        for error in errors:
            logger.error(f"  - {error}")
        sys.exit(1)

    logger.info(f"配置验证通过: {config.name}")

    # 干运行模式
    if args.dry_run:
        logger.info("干运行模式：配置有效，不执行回测")
        print("\n配置摘要:")
        print(f"  名称: {config.name}")
        print(f"  策略: {config.strategy.name}")
        print(f"  日期: {config.data.start_date} ~ {config.data.end_date}")
        print(f"  数据源: {config.data.source}")
        print(f"  初始资金: {config.initial_capital:,.0f}")
        print(f"  随机种子: {config.random_seed}")
        sys.exit(0)

    # 运行回测 — 委托给 config_runner.run_from_config（§4.5.1 唯一入口）
    try:
        result = run_from_config(
            config_path=str(config_path),
            output_dir=args.output,
        )
    except Exception as e:
        logger.error(f"回测失败: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # run_from_config 已自动保存四件套 + DB 索引
    paths = {"output_dir": result.output_dir}

    # 生成HTML报告（如果启用）
    if args.html:
        logger.info("生成HTML报告...")
        report = generate_report(result.result)
        html_path = result.output_dir / "report.html"
        generate_html_report(report, html_path)
        paths["report_html"] = html_path
        logger.info(f"HTML报告已生成: {html_path}")

    print("\n回测结果:")
    print(f"  策略: {result.result.strategy_name}")
    print(f"  初始资金: {result.result.initial_capital:,.2f}")
    print(f"  最终净值: {result.result.final_nav:,.2f}")
    print(f"  总交易次数: {result.result.total_trades}")
    print(f"  总费用: {result.result.total_fees.total:,.2f}")

    print("\n输出文件:")
    for name, path in paths.items():
        print(f"  {name}: {path}")

    logger.info("回测完成")


if __name__ == "__main__":
    main()
