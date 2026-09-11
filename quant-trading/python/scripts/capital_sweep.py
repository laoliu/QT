#!/usr/bin/env python
"""多资金量回测扫描工具。

对同一策略配置，在不同初始资金量下运行回测并对比关键指标，
用于分析策略的资金容量和规模效应。

使用方法:
    python scripts/capital_sweep.py configs/backtest/multifactor_enhance_5y.yml
    python scripts/capital_sweep.py configs/cb_strategies/triple_low.yml --capitals 100000 500000 1000000
    python scripts/capital_sweep.py configs/backtest/smallcap_rotation_5y.yml --output results/capital_sweep/

依赖: 本项目已有的 config_runner + config_parser
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from aipha.backtest.config_parser import load_config, config_to_dict
from aipha.backtest.config_runner import run_from_config

logger = logging.getLogger(__name__)

# 默认 5 档资金量（单位：元）
DEFAULT_CAPITALS = [
    100_000,       # 10万 — 散户入门
    500_000,       # 50万 — 散户进阶
    1_000_000,     # 100万 — 标准
    5_000_000,     # 500万 — 大户
    10_000_000,    # 1000万 — 机构起步
]


def _format_capital(capital: float) -> str:
    """将资金量格式化为易读字符串（如 '100K', '1M', '10M'）。"""
    if capital >= 1_000_000:
        return f"{capital / 1_000_000:.0f}M"
    elif capital >= 1_000:
        return f"{capital / 1_000:.0f}K"
    return f"{capital:.0f}"


def run_capital_sweep(
    config_path: str | Path,
    capitals: list[float] | None = None,
    output_dir: str | Path | None = None,
) -> list[dict]:
    """对一个策略配置运行多资金量回测。

    Args:
        config_path: 基准配置文件路径
        capitals: 资金量列表（元），默认使用 DEFAULT_CAPITALS
        output_dir: 输出目录

    Returns:
        每个资金量的回测结果摘要列表
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    capitals = capitals or DEFAULT_CAPITALS
    output_dir = Path(output_dir) if output_dir else Path("results/capital_sweep")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载基准配置
    base_config = load_config(config_path)
    strategy_name = base_config.strategy.name
    logger.info(
        f"资金量扫描: 策略={strategy_name}, 配置={config_path.name}, "
        f"资金档位={[_format_capital(c) for c in capitals]}"
    )

    results: list[dict] = []

    for capital in capitals:
        cap_label = _format_capital(capital)
        logger.info(f"\n{'='*60}")
        logger.info(f"运行: {strategy_name} @ 初始资金 {cap_label} ({capital:,.0f} 元)")
        logger.info(f"{'='*60}")

        # 创建临时配置文件（修改 initial_capital）
        config_copy = copy.deepcopy(base_config)
        config_copy.initial_capital = capital

        # 如果有 output_dir 字段，也相应修改
        sub_output = output_dir / f"{strategy_name}_{cap_label}"
        config_copy.output_dir = str(sub_output)

        # 序列化到临时 YAML
        config_dict = config_to_dict(config_copy)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tmp:
            import yaml
            yaml.dump(config_dict, tmp, allow_unicode=True, default_flow_style=False)
            tmp_path = Path(tmp.name)

        try:
            run_result = run_from_config(tmp_path, output_dir=sub_output)

            # 提取关键指标
            report = run_result.report
            result_data = run_result.result

            summary = {
                "strategy": strategy_name,
                "initial_capital": capital,
                "capital_label": cap_label,
                "final_nav": result_data.final_nav,
                "total_return_pct": (result_data.final_nav / capital - 1) * 100,
                "total_trades": result_data.total_trades,
            }

            if report is not None:
                summary.update({
                    "annual_return_pct": report.performance.annualized_return * 100,
                    "max_drawdown_pct": report.drawdown.max_drawdown_pct * 100,
                    "sharpe": report.performance.sharpe_ratio,
                    "calmar": report.performance.calmar_ratio,
                    "win_rate_pct": getattr(report.performance, "win_rate", 0) * 100,
                })
                # 成本归因
                if hasattr(report, "cost_analysis") and report.cost_analysis is not None:
                    summary["total_cost"] = getattr(
                        report.cost_analysis, "total_cost", 0
                    )
                    summary["cost_drag_pct"] = getattr(
                        report.cost_analysis, "cost_drag_pct", 0
                    ) * 100

            results.append(summary)
            logger.info(
                f"✓ {cap_label}: 年化={summary.get('annual_return_pct', 'N/A'):.2f}%, "
                f"回撤={summary.get('max_drawdown_pct', 'N/A'):.2f}%, "
                f"Sharpe={summary.get('sharpe', 'N/A'):.2f}, "
                f"交易={summary['total_trades']}"
            )

        except Exception as e:
            logger.error(f"✗ {cap_label} 回测失败: {e}")
            results.append({
                "strategy": strategy_name,
                "initial_capital": capital,
                "capital_label": cap_label,
                "error": str(e),
            })
        finally:
            tmp_path.unlink(missing_ok=True)

    # 保存汇总结果
    _save_results(results, strategy_name, output_dir)
    return results


def _save_results(
    results: list[dict],
    strategy_name: str,
    output_dir: Path,
) -> None:
    """保存扫描结果为 CSV + JSON。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"capital_sweep_{strategy_name}_{timestamp}"

    # CSV
    csv_path = output_dir / f"{base_name}.csv"
    if results:
        fieldnames = list(results[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        logger.info(f"CSV 结果已保存: {csv_path}")

    # JSON
    json_path = output_dir / f"{base_name}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "strategy": strategy_name,
                "timestamp": timestamp,
                "capitals": [r.get("initial_capital") for r in results],
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    logger.info(f"JSON 结果已保存: {json_path}")

    # 打印对比表格
    _print_comparison_table(results)


def _print_comparison_table(results: list[dict]) -> None:
    """打印资金量对比表格到控制台。"""
    print("\n" + "=" * 90)
    print("资金量扫描结果对比")
    print("=" * 90)
    header = (
        f"{'资金量':>10} {'年化收益%':>10} {'最大回撤%':>10} "
        f"{'Sharpe':>8} {'Calmar':>8} {'交易次数':>8} {'成本拖累%':>10}"
    )
    print(header)
    print("-" * 90)

    for r in results:
        if "error" in r:
            print(f"{r['capital_label']:>10} {'ERROR':>10} {r['error']}")
            continue
        print(
            f"{r['capital_label']:>10} "
            f"{r.get('annual_return_pct', 0):>10.2f} "
            f"{r.get('max_drawdown_pct', 0):>10.2f} "
            f"{r.get('sharpe', 0):>8.2f} "
            f"{r.get('calmar', 0):>8.2f} "
            f"{r.get('total_trades', 0):>8} "
            f"{r.get('cost_drag_pct', 0):>10.2f}"
        )
    print("=" * 90)


def main():
    parser = argparse.ArgumentParser(
        description="多资金量回测扫描工具 — 分析策略资金容量与规模效应"
    )
    parser.add_argument(
        "config",
        help="基准配置文件路径 (YAML)",
    )
    parser.add_argument(
        "--capitals",
        nargs="+",
        type=float,
        default=None,
        help="资金量列表（元），默认: 100000 500000 1000000 5000000 10000000",
    )
    parser.add_argument(
        "--output",
        default="results/capital_sweep",
        help="输出目录 (默认: results/capital_sweep/)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="详细日志输出",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    results = run_capital_sweep(
        config_path=args.config,
        capitals=args.capitals,
        output_dir=args.output,
    )

    # 返回码：全部成功=0，有失败=1
    has_errors = any("error" in r for r in results)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
