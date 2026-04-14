"""因子 IC / Decay 自动化分析脚本

用法:
    python scripts/factor_ic_decay.py --factors sue roe momentum --output results/factor_analysis.json
    python scripts/factor_ic_decay.py --all --output results/factor_analysis.json

脚本功能:
1. 从回测数据或模拟数据中提取因子值与未来收益
2. 对每个因子运行 IC/IR/Decay/Quantile 分析
3. 输出 JSON 汇总报告 + 文本摘要

依赖:
    - src/aipha/factors/analysis.py (FactorAnalyzer)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# 把 src 加入搜索路径
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from aipha.factors.analysis import FactorAnalyzer, FactorAnalysisReport  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 已注册因子列表（可扩展）
# ---------------------------------------------------------------------------

REGISTERED_FACTORS: list[str] = [
    "sue",
    "roe",
    "eps",
    "dt_netprofit_yoy",
    "pe_ttm",
    "pb",
    "circ_mv",
    "turnover_rate",
    "momentum_20d",
    "volatility_20d",
    "total_mv",
    "volume_ratio",
]


# ---------------------------------------------------------------------------
# 数据加载 / 模拟
# ---------------------------------------------------------------------------

def load_factor_return_data(
    factor_name: str,
    data_dir: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, float]]]:
    """加载因子值与收益数据。

    优先从 data_dir 下的 parquet/csv 文件加载。
    若文件不存在，返回空字典（调用方自行判断）。

    Args:
        factor_name: 因子名
        data_dir: 数据目录
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        (factor_data, return_data) 两个嵌套字典
    """
    if data_dir:
        factor_path = Path(data_dir) / f"factor_{factor_name}.json"
        return_path = Path(data_dir) / "forward_returns.json"
        if factor_path.exists() and return_path.exists():
            factor_data = _load_json_factor_data(factor_path)
            return_data = _load_json_factor_data(return_path)
            return factor_data, return_data

    logger.info(f"因子 {factor_name} 的离线数据未找到，返回空数据")
    return {}, {}


def _load_json_factor_data(path: Path) -> dict[date, dict[str, float]]:
    """从 JSON 文件加载因子数据。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result: dict[date, dict[str, float]] = {}
    for date_str, symbol_values in raw.items():
        d = date.fromisoformat(date_str)
        result[d] = {sym: float(val) for sym, val in symbol_values.items()}
    return result


# ---------------------------------------------------------------------------
# 分析引擎
# ---------------------------------------------------------------------------

def analyze_single_factor(
    factor_name: str,
    factor_data: dict[date, dict[str, float]],
    return_data: dict[date, dict[str, float]],
    holding_periods: list[int] | None = None,
    num_quantiles: int = 5,
) -> FactorAnalysisReport | None:
    """分析单个因子。

    Args:
        factor_name: 因子名
        factor_data: 因子值
        return_data: 收益数据
        holding_periods: 衰减分析持有期
        num_quantiles: 分位数数量

    Returns:
        分析报告或 None（数据不足）
    """
    if not factor_data or not return_data:
        logger.warning(f"因子 {factor_name} 数据不足，跳过")
        return None

    analyzer = FactorAnalyzer(
        factor_data=factor_data,
        return_data=return_data,
        factor_name=factor_name,
    )

    if len(analyzer.dates) < 10:
        logger.warning(f"因子 {factor_name} 对齐后日期数 {len(analyzer.dates)} < 10，跳过")
        return None

    return analyzer.generate_report(
        include_decay=True,
        include_quantile=True,
        holding_periods=holding_periods,
        num_quantiles=num_quantiles,
    )


def analyze_all_factors(
    factors: list[str],
    data_dir: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    holding_periods: list[int] | None = None,
    num_quantiles: int = 5,
) -> dict[str, FactorAnalysisReport]:
    """批量分析多个因子。

    Args:
        factors: 因子名列表
        data_dir: 数据目录
        start_date: 开始日期
        end_date: 结束日期
        holding_periods: 衰减分析持有期
        num_quantiles: 分位数数量

    Returns:
        {因子名: 分析报告}
    """
    results: dict[str, FactorAnalysisReport] = {}

    for factor_name in factors:
        logger.info(f"分析因子: {factor_name}")
        factor_data, return_data = load_factor_return_data(
            factor_name, data_dir, start_date, end_date,
        )

        report = analyze_single_factor(
            factor_name, factor_data, return_data,
            holding_periods, num_quantiles,
        )
        if report:
            results[factor_name] = report

    return results


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def report_to_dict(report: FactorAnalysisReport) -> dict:
    """将报告转换为可序列化的字典。"""
    ic = report.ic_analysis
    result = {
        "factor_name": report.factor_name,
        "analysis_period": [str(report.analysis_period[0]), str(report.analysis_period[1])],
        "sample_size": report.sample_size,
        "ic": {
            "ic_mean": round(ic.ic_mean, 6),
            "ic_std": round(ic.ic_std, 6),
            "ir": round(ic.ir, 6),
            "ic_positive_rate": round(ic.ic_positive_rate, 4),
            "ic_abs_mean": round(ic.ic_abs_mean, 6),
            "t_stat": round(ic.t_stat, 4),
            "p_value": round(ic.p_value, 6),
        },
    }

    if report.decay_analysis:
        result["decay"] = {
            "holding_periods": report.decay_analysis.holding_periods,
            "ic_by_period": {
                str(k): round(v, 6) for k, v in report.decay_analysis.ic_by_period.items()
            },
            "ir_by_period": {
                str(k): round(v, 6) for k, v in report.decay_analysis.ir_by_period.items()
            },
            "optimal_period": report.decay_analysis.optimal_period,
        }

    if report.quantile_analysis:
        qa = report.quantile_analysis
        result["quantile"] = {
            "num_quantiles": qa.num_quantiles,
            "quantile_returns": {
                str(k): round(v, 6) for k, v in qa.quantile_returns.items()
            },
            "long_short_return": round(qa.long_short_return, 6),
            "monotonicity": round(qa.monotonicity, 4),
        }

    return result


def save_json_report(
    reports: dict[str, FactorAnalysisReport],
    output_path: str,
) -> None:
    """保存 JSON 汇总报告。"""
    data = {name: report_to_dict(r) for name, r in reports.items()}
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"报告已保存: {output}")


def print_summary(reports: dict[str, FactorAnalysisReport]) -> str:
    """生成文本摘要并打印。"""
    lines = [
        "=" * 70,
        "因子 IC / Decay 自动化分析汇总",
        "=" * 70,
        "",
        f"{'因子':<20s} {'IC均值':>8s} {'IR':>8s} {'|IC|均值':>8s} {'IC>0率':>8s} {'最优期':>6s}",
        "-" * 70,
    ]

    for name, report in reports.items():
        ic = report.ic_analysis
        opt = report.decay_analysis.optimal_period if report.decay_analysis else "-"
        lines.append(
            f"{name:<20s} {ic.ic_mean:>8.4f} {ic.ir:>8.4f} "
            f"{ic.ic_abs_mean:>8.4f} {ic.ic_positive_rate:>7.1%} {str(opt):>6s}"
        )

    lines.extend(["", "=" * 70])
    summary = "\n".join(lines)
    print(summary)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="因子 IC/Decay 自动化分析")
    parser.add_argument(
        "--factors", nargs="*", default=None,
        help="要分析的因子名列表（默认：全部已注册因子）",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="分析全部已注册因子",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="数据目录（含 factor_*.json 和 forward_returns.json）",
    )
    parser.add_argument(
        "--output", default="results/factor_analysis.json",
        help="输出 JSON 报告路径",
    )
    parser.add_argument(
        "--periods", nargs="*", type=int, default=None,
        help="衰减分析持有期（默认: 1 3 5 10 20）",
    )
    parser.add_argument(
        "--quantiles", type=int, default=5,
        help="分位数数量（默认: 5）",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="详细输出",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    factors = args.factors
    if args.all or not factors:
        factors = REGISTERED_FACTORS

    reports = analyze_all_factors(
        factors=factors,
        data_dir=args.data_dir,
        holding_periods=args.periods,
        num_quantiles=args.quantiles,
    )

    if reports:
        save_json_report(reports, args.output)
        print_summary(reports)
        # 打印每个因子的详细报告
        if args.verbose:
            for report in reports.values():
                print(report.summary())
    else:
        print("未能完成任何因子分析（数据不足或文件不存在）")
        print("请提供 --data-dir 参数指向包含因子数据的目录")
        print(f"已注册因子: {', '.join(REGISTERED_FACTORS)}")


if __name__ == "__main__":
    main()
