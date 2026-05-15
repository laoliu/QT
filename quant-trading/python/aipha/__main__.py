#!/usr/bin/env python
"""Aipha-Q 统一命令行入口

用法:
    python -m aipha backtest --config configs/backtest/examples/momentum_synthetic.yml
    python -m aipha backtest --config config.yml --html --output output/my_run
    python -m aipha paper --config configs/paper/cb_small_size.yml
    python -m aipha live --config configs/live/easytrader.yml --confirm
    python -m aipha live --config configs/live/easytrader.yml --dry-run
    python -m aipha check-data --symbols 000001.SZ,600000.SH
    python -m aipha version

子命令:
    backtest    运行回测
    paper       启动 Paper Trading（模拟盘）
    live        启动实盘交易（需要显式确认）
    check-data  数据健康检查
    version     显示版本信息
"""

import argparse
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# 加载环境变量（.env 文件）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未安装时静默忽略


def setup_logging(verbose: bool = False):
    """设置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_version(args):
    """显示版本信息"""
    from aipha import __version__
    print(f"Aipha-Q v{__version__}")
    print(f"Python {sys.version}")


def cmd_paper(args):
    """启动 Paper Trading（模拟盘）"""
    logger = logging.getLogger(__name__)

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    if getattr(args, "replay", False):
        # 历史回放模式：用缓存数据代替实时行情
        from aipha.runner.paper_replay import run_paper_replay

        try:
            result = run_paper_replay(
                config_path,
                days=getattr(args, "replay_days", 60),
            )
            if result["success"]:
                logger.info("Paper Replay 完成")
            else:
                logger.error(f"Paper Replay 失败: {result.get('error')}")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Paper Replay 失败: {e}", exc_info=True)
            sys.exit(1)
    else:
        from aipha.runner.paper_engine import run_paper_trading

        try:
            run_paper_trading(config_path)
        except KeyboardInterrupt:
            logger.info("Paper Trading 已被用户中断")
        except Exception as e:
            logger.error(f"Paper Trading 失败: {e}", exc_info=True)
            sys.exit(1)


def cmd_live(args):
    """启动实盘交易

    安全检查:
    1. 必须显式确认（--confirm 标志）
    2. 默认 dry-run 模式
    3. 凭证从环境变量或加密文件加载
    """
    logger = logging.getLogger(__name__)

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    # 安全闸门：必须显式确认
    if not args.confirm:
        print("\n" + "=" * 60)
        print("⚠️  实盘交易涉及真实资金风险！")
        print("=" * 60)
        print()
        print("在启用实盘之前，请确保：")
        print("  1. 已在仿真模式 (paper) 充分验证策略")
        print("  2. 已理解并设置合理的风控参数")
        print("  3. 已保护好账户凭证（环境变量或加密文件）")
        print("  4. 已仔细检查交易配置（标的、资金、费率等）")
        print()
        print("使用 --confirm 标志启动实盘交易：")
        print(f"  python -m aipha live --config {args.config} --confirm")
        if not args.dry_run:
            print()
            print("建议先使用 --dry-run 模式验证：")
            print(f"  python -m aipha live --config {args.config} --dry-run")
        print()
        sys.exit(0)

    # 加载配置
    import yaml

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        sys.exit(1)

    # 加载凭证
    from aipha.execution.credentials import CredentialsLoader, mask_dict

    loader = CredentialsLoader()
    broker_name = config.get("broker", {}).get("name", "easytrader")
    env_prefix = config.get("broker", {}).get("env_prefix", f"BROKER_{broker_name.upper()}")

    fallback_file = config.get("broker", {}).get("credentials_file")
    fallback_path = Path(fallback_file) if fallback_file else None

    creds = loader.load(
        broker_name=broker_name,
        env_prefix=env_prefix,
        fallback_file=fallback_path,
    )

    if creds is None:
        logger.error(
            f"无法加载券商凭证。请设置环境变量 {env_prefix}_USER / {env_prefix}_PASSWORD，"
            f"或提供加密凭证文件。"
        )
        sys.exit(1)

    # 打印启动摘要（脱敏）
    print("\n" + "=" * 50)
    print("实盘交易启动" if not args.dry_run else "实盘交易 DRY-RUN")
    print("=" * 50)
    print(f"  券商: {broker_name}")
    print(f"  凭证来源: {creds.source}")
    print(f"  凭证摘要: {creds.masked_summary()}")
    print(f"  配置: {config_path}")
    print(f"  Dry-Run: {args.dry_run}")
    print()

    # 创建和启动网关
    try:
        from aipha.execution.easytrader_gateway import (
            EasytraderClientType,
            EasytraderConfig,
            EasytraderGateway,
        )
        from aipha.execution.broker_gateway import BrokerType

        broker_config = config.get("broker", {})
        et_config = EasytraderConfig(
            broker_type=BrokerType.EASYTRADER,
            name=broker_name,
            client_type=EasytraderClientType(
                broker_config.get("client_type", "universal")
            ),
            client_path=broker_config.get("client_path", ""),
            account_id=creds.user,
            dry_run=args.dry_run,
            poll_interval=broker_config.get("poll_interval", 1.0),
            auto_reconnect=broker_config.get("auto_reconnect", True),
            lot_size=broker_config.get("lot_size", 100),
        )

        gateway = EasytraderGateway(et_config)
        gateway.connect()

        login_ok = gateway.login(creds.to_login_dict())
        if not login_ok:
            logger.error("登录失败，退出")
            sys.exit(1)

        logger.info("实盘网关已连接并登录")

        # 加载策略
        strategy_config = config.get("strategy", {})
        strategy_name = strategy_config.get("name", "")

        if not strategy_name:
            logger.error("配置中缺少 strategy.name")
            gateway.disconnect()
            sys.exit(1)

        from aipha.strategy.registry import create_strategy_from_config

        strategy = create_strategy_from_config(strategy_config)

        # 创建运行器
        from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode

        runner_config = RunnerConfig(
            mode=RunnerMode.LIVE,
            initial_capital=Decimal(str(config.get("initial_capital", 1_000_000))),
            lot_size=et_config.lot_size,
        )

        runner = LiveRunner(runner_config, strategy, gateway)
        runner.start()

        logger.info("实盘运行器已启动，Ctrl+C 停止")

        # 主循环（等待中断）
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("收到停止信号...")

        runner.stop()
        gateway.disconnect()
        logger.info("实盘交易已安全停止")

    except ImportError as e:
        logger.error(f"缺少依赖: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"实盘交易失败: {e}", exc_info=True)
        sys.exit(1)


def cmd_backtest(args):
    """运行回测"""
    from aipha.backtest.config_runner import (
        BacktestRunResult,
        load_config,
        run_from_config,
        save_run_result,
        validate_config,
    )
    from aipha.backtest.html_report import generate_html_report
    from aipha.backtest.report import generate_report

    logger = logging.getLogger(__name__)

    # 加载配置（干运行模式下只验证不执行）
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    if args.dry_run:
        try:
            config = load_config(config_path)
            errors = validate_config(config)
            if errors:
                logger.error("配置验证失败:")
                for error in errors:
                    logger.error(f"  - {error}")
                sys.exit(1)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            sys.exit(1)

        print("\n配置摘要:")
        print(f"  名称: {config.name}")
        print(f"  策略: {config.strategy.name}")
        print(f"  成交模型: {config.fill_model.name}")
        print(f"  日期: {config.data.start_date} ~ {config.data.end_date}")
        print(f"  数据源: {config.data.source}")
        print(f"  初始资金: {config.initial_capital:,.0f}")
        return

    # 运行回测（统一入口，配置驱动）
    output_dir = args.output if args.output else None
    try:
        run_result = run_from_config(config_path, output_dir=output_dir)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"回测执行失败: {e}", exc_info=True)
        sys.exit(1)

    result = run_result.result

    # 如未指定 output_dir，加时间戳
    if not output_dir:
        run_result = BacktestRunResult(
            config=run_result.config,
            result=result,
            fingerprint=run_result.fingerprint,
            output_dir=Path(run_result.config.output_dir)
            / datetime.now().strftime("%Y%m%d_%H%M%S"),
        )

    # 保存结果
    logger.info("保存结果...")
    paths = save_run_result(run_result)

    # 生成HTML报告
    if args.html:
        logger.info("生成HTML报告...")
        report = generate_report(result)
        html_path = run_result.output_dir / "report.html"
        generate_html_report(report, html_path)
        paths["report_html"] = html_path
        logger.info(f"HTML报告已生成: {html_path}")

    # 打印结果摘要
    print("\n" + "=" * 50)
    print("回测结果")
    print("=" * 50)
    print(f"  策略: {result.strategy_name}")
    print(f"  成交模型: {run_result.config.fill_model.name}")
    print(f"  初始资金: {result.initial_capital:,.2f}")
    print(f"  最终净值: {result.final_nav:,.2f}")
    print(f"  收益率: {(result.final_nav / result.initial_capital - 1) * 100:.2f}%")
    print(f"  总交易次数: {result.total_trades}")
    print(f"  总费用: {result.total_fees.total:,.2f}")

    print("\n输出文件:")
    for name, path in paths.items():
        print(f"  {name}: {path}")

    logger.info("回测完成")


def cmd_check_data(args):
    """数据健康检查

    从 TuShare / AKShare 缓存或在线 API 加载日频行情，
    并运行数据健康检查流水线（缺失率、跳变、停牌标记、重复、乱序）。

    用法示例:
        python -m aipha check-data --symbols 000001.SZ,600000.SH --source tushare
        python -m aipha check-data --parquet data/cache/tushare --start 2024-01-01 --end 2024-12-31
    """
    import os
    from collections import defaultdict
    from datetime import date

    logger = logging.getLogger(__name__)

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else ["000001.SZ"]
    logger.info(f"检查标的: {symbols}")

    # --- 日期范围 ---
    start_date = date.fromisoformat(args.start) if args.start else date(2024, 1, 1)
    end_date = date.fromisoformat(args.end) if args.end else date.today()

    # --- 加载数据 ---
    data: dict[str, "pd.DataFrame"] = {}

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas 未安装")
        sys.exit(1)

    source = args.source or "tushare"

    if args.parquet:
        # 直接从 Parquet 缓存目录加载
        from pathlib import Path as _P

        cache_dir = _P(args.parquet)
        if not cache_dir.exists():
            logger.error(f"缓存目录不存在: {cache_dir}")
            sys.exit(1)

        for pf in sorted(cache_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(pf)
                # 尝试匹配 symbols
                if "ts_code" in df.columns:
                    for sym in symbols:
                        sub = df[df["ts_code"] == sym]
                        if not sub.empty:
                            data[sym] = sub
                else:
                    # 文件名推测 symbol
                    stem = pf.stem  # 例如 000001.SZ_daily_qfq_20240101_20241231
                    for sym in symbols:
                        if sym.replace(".", "_") in stem or sym in stem:
                            data[sym] = df
            except Exception as e:
                logger.warning(f"读取 {pf} 失败: {e}")

    elif source == "tushare":
        try:
            from aipha.data.tushare_provider import TuShareProBarProvider

            token = os.environ.get("TUSHARE_TOKEN")
            if not token:
                logger.error("未配置 TUSHARE_TOKEN 环境变量")
                sys.exit(1)

            provider = TuShareProBarProvider(
                token=token,
                adjust="qfq",
            )

            for sym in symbols:
                logger.info(f"加载 {sym} ...")
                bars = provider.load_bars(
                    symbol=sym, start=start_date, end=end_date, frequency="daily",
                )
                if bars:
                    rows = [
                        {
                            "trade_date": b.start_ts.strftime("%Y%m%d"),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "vol": b.volume,
                        }
                        for b in bars
                    ]
                    data[sym] = pd.DataFrame(rows)
                    logger.info(f"  {sym}: {len(rows)} 条")
                else:
                    logger.warning(f"  {sym}: 无数据")
        except ImportError:
            logger.error("tushare 未安装，请 pip install tushare")
            sys.exit(1)

    elif source == "akshare":
        try:
            from aipha.data.akshare_provider import AKShareBarProvider

            provider = AKShareBarProvider(
                adjust="qfq",
            )

            for sym in symbols:
                logger.info(f"加载 {sym} ...")
                bars = provider.load_bars(
                    symbol=sym, start=start_date, end=end_date, frequency="daily",
                )
                if bars:
                    rows = [
                        {
                            "trade_date": b.start_ts.strftime("%Y%m%d"),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "vol": b.volume,
                        }
                        for b in bars
                    ]
                    data[sym] = pd.DataFrame(rows)
                    logger.info(f"  {sym}: {len(rows)} 条")
                else:
                    logger.warning(f"  {sym}: 无数据")
        except ImportError:
            logger.error("akshare 未安装，请 pip install akshare")
            sys.exit(1)
    else:
        logger.error(f"不支持的数据源: {source}")
        sys.exit(1)

    if not data:
        logger.error("未加载到任何数据，无法进行检查")
        sys.exit(1)

    # --- 运行健康检查 ---
    try:
        from aipha.data.health_check import DataHealthChecker, format_report_text

        checker = DataHealthChecker()
        checker.add_default_checks(
            missing_threshold=args.missing_threshold,
            jump_threshold=args.jump_threshold,
        )
        report = checker.run(
            data,
            symbols=list(data.keys()),
            date_range=(start_date.isoformat(), end_date.isoformat()),
        )

        # 格式化输出
        print(format_report_text(report))

        # 退出码：有 ERROR 级别则返回 1
        if report.summary.get("error_count", 0) > 0:
            sys.exit(1)

    except ImportError as e:
        logger.error(f"数据检查模块不可用: {e}")
        sys.exit(1)


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        prog="aipha",
        description="Aipha-Q 量化交易系统命令行工具",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细信息",
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # backtest 子命令
    backtest_parser = subparsers.add_parser("backtest", help="运行回测")
    backtest_parser.add_argument(
        "-c", "--config",
        required=True,
        help="配置文件路径 (YAML/JSON)",
    )
    backtest_parser.add_argument(
        "-o", "--output",
        help="输出目录（覆盖配置）",
    )
    backtest_parser.add_argument(
        "--html",
        action="store_true",
        help="生成HTML报告",
    )
    backtest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅验证配置，不运行",
    )
    backtest_parser.set_defaults(func=cmd_backtest)

    # paper 子命令
    paper_parser = subparsers.add_parser("paper", help="启动 Paper Trading（模拟盘）")
    paper_parser.add_argument(
        "-c", "--config",
        required=True,
        help="Paper Trading 配置文件路径 (YAML)",
    )
    paper_parser.add_argument(
        "--replay",
        action="store_true",
        help="使用历史数据回放模式（不需要实时行情/交易时段）",
    )
    paper_parser.add_argument(
        "--replay-days",
        type=int,
        default=60,
        help="回放的最近交易日数（默认 60）",
    )
    paper_parser.set_defaults(func=cmd_paper)

    # live 子命令
    live_parser = subparsers.add_parser("live", help="启动实盘交易（需要显式确认）")
    live_parser.add_argument(
        "-c", "--config",
        required=True,
        help="实盘交易配置文件路径 (YAML)",
    )
    live_parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认启动实盘交易（必须显式传入）",
    )
    live_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Dry-Run 模式：连接网关但不实际下单",
    )
    live_parser.set_defaults(func=cmd_live)

    # check-data 子命令
    check_parser = subparsers.add_parser("check-data", help="数据健康检查")
    check_parser.add_argument(
        "--symbols",
        help="标的列表（逗号分隔，如 000001.SZ,600000.SH）",
    )
    check_parser.add_argument(
        "--source",
        default="tushare",
        choices=["tushare", "akshare"],
        help="在线数据源（默认 tushare）",
    )
    check_parser.add_argument(
        "--parquet",
        help="从本地 Parquet 缓存目录加载（优先于 --source）",
    )
    check_parser.add_argument(
        "--start",
        help="开始日期（YYYY-MM-DD，默认 2024-01-01）",
    )
    check_parser.add_argument(
        "--end",
        help="结束日期（YYYY-MM-DD，默认今天）",
    )
    check_parser.add_argument(
        "--missing-threshold",
        type=float,
        default=0.1,
        dest="missing_threshold",
        help="缺失率告警阈值（默认 0.1 即 10%%）",
    )
    check_parser.add_argument(
        "--jump-threshold",
        type=float,
        default=0.30,
        dest="jump_threshold",
        help="价格跳变告警阈值（默认 0.30 即 30%%）",
    )
    check_parser.set_defaults(func=cmd_check_data)

    # version 子命令
    version_parser = subparsers.add_parser("version", help="显示版本")
    version_parser.set_defaults(func=cmd_version)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
