"""analyze_yearly_returns.py — 逐年收益拆解与 2020 偏差分析（A4）

读取回测结果（优先 SQLite DB，降级扫描 output/ 目录），
生成策略逐年收益对比表，并量化 2020 年异常贡献对整体指标的影响。

功能:
- 输出每个策略每年的年化收益率矩阵
- 计算"剔除2020后"的重新归一化指标（AR / Sharpe 代理值）
- 标记逐年稳定性（正收益年份比例）
- 支持导出 JSON 报告到 results/

用法:
    python scripts/analyze_yearly_returns.py
    python scripts/analyze_yearly_returns.py --min-years 3
    python scripts/analyze_yearly_returns.py --output results/yearly_report.json
    python scripts/analyze_yearly_returns.py --exclude-year 2020
    python scripts/analyze_yearly_returns.py --strategy cb_comprehensive_opt
    python scripts/analyze_yearly_returns.py --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_PATH = _ROOT / "results" / "yearly_returns_report.json"
OUTPUT_DIR = _ROOT / "output"

# 逐年盈利门槛（hardcoded from copilot-instructions.md）
MIN_POSITIVE_YEARS_RATIO = 0.8   # ≥ 8/10 年盈利（硬性门槛）
ANOMALY_YEAR = 2020              # 可转债市场异常年份

# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class YearlyReturnRow:
    """单个策略的逐年收益统计。"""

    strategy_name: str
    run_id: str
    start_date: str
    end_date: str
    overall_ar: float            # 全期年化收益率
    overall_sharpe: float        # 全期夏普比率
    overall_mdd: float           # 全期最大回撤

    # 逐年收益 {年份: 年化收益率}
    yearly: dict[int, float] = field(default_factory=dict)

    # 衍生指标
    n_positive: int = 0          # 正收益年份数
    n_years: int = 0             # 总年份数
    positive_ratio: float = 0.0  # 正收益年份比例
    anomaly_contribution: float = 0.0  # 异常年份收益占总年份均值的比例

    # 剔除异常年后重算（去掉 ANOMALY_YEAR）
    ex_anomaly_ar_proxy: float = 0.0   # 剔除后年均收益（简单算术均值代理）
    ex_anomaly_positive_ratio: float = 0.0

    @property
    def passes_yearly_stability(self) -> bool:
        """逐年稳定性达标: positive_ratio ≥ 80%。"""
        return self.positive_ratio >= MIN_POSITIVE_YEARS_RATIO


@dataclass
class YearlyReport:
    """完整逐年收益报告。"""

    generated_at: str
    anomaly_year: int             # 被标记为异常的年份
    n_strategies: int

    rows: list[YearlyReturnRow] = field(default_factory=list)
    all_years: list[int] = field(default_factory=list)   # 所有出现的年份（排序）

    # 摘要
    n_stable: int = 0            # 逐年稳定达标策略数
    median_anomaly_contribution: float = 0.0


# ── 数据加载 ──────────────────────────────────────────────────────────────────


def load_yearly_data(
    strategy_filter: str | None = None,
    min_years: int = 2,
) -> list[dict[str, Any]]:
    """从 SQLite DB（优先）读取含 yearly_returns 的回测摘要。

    若 DB 不可用或返回空，降级扫描 output/ 目录的 summary.json。

    Args:
        strategy_filter: 仅加载指定策略（子串匹配）
        min_years: 最少年份数，过滤过短数据

    Returns:
        包含 run_id / strategy_name / yearly_returns / metrics 的字典列表
    """
    records: list[dict[str, Any]] = []
    try:
        records = _load_from_db(strategy_filter)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB 加载失败（%s），降级扫描文件", exc)

    if not records:
        logger.info("DB 为空，扫描 output/ 目录")
        records = _load_from_files(strategy_filter)

    # 过滤：yearly_returns 必须有足够年份
    filtered = [
        r for r in records
        if len(r.get("yearly_returns") or {}) >= min_years
    ]
    logger.info(
        "共加载 %d 条记录（min_years=%d 过滤后 %d 条）",
        len(records), min_years, len(filtered),
    )
    return filtered


def _load_from_db(strategy_filter: str | None) -> list[dict[str, Any]]:
    """从 SQLite backtest_runs 表中加载含 yearly_returns 的记录。"""
    from aipha.backtest.result_store import ResultStore

    rs = ResultStore()
    try:
        runs = rs.query_runs(
            strategy_name=strategy_filter,
            run_type="single",
        )
    finally:
        rs.close()

    result = []
    for run in runs:
        metrics = run.get("metrics_json") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        yearly = metrics.get("yearly_returns") or {}
        if not yearly:
            continue
        # 统一年份为 int
        yearly_int = {int(k): float(v) for k, v in yearly.items() if v is not None}
        result.append({
            "run_id": run.get("run_id", ""),
            "strategy_name": run.get("strategy_name", ""),
            "start_date": run.get("start_date", ""),
            "end_date": run.get("end_date", ""),
            "overall_ar": run.get("annualized_return", 0.0),
            "overall_sharpe": run.get("sharpe_ratio", 0.0),
            "overall_mdd": run.get("max_drawdown", 0.0),
            "yearly_returns": yearly_int,
        })
    logger.info("DB: 找到 %d 条含逐年数据的记录", len(result))
    return result


def _load_from_files(strategy_filter: str | None) -> list[dict[str, Any]]:
    """降级：扫描 output/ 子目录的 summary.json 或 meta.json。"""
    result: list[dict[str, Any]] = []
    if not OUTPUT_DIR.exists():
        logger.warning("output/ 目录不存在: %s", OUTPUT_DIR)
        return result

    for summary_path in OUTPUT_DIR.rglob("summary.json"):
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("跳过 %s: %s", summary_path, exc)
            continue

        # 兼容两种 summary 格式：直接字段 / metrics 嵌套
        metrics = data.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        yearly_raw = metrics.get("yearly_returns") or data.get("yearly_returns") or {}
        if not yearly_raw:
            continue

        strategy_name = (
            data.get("strategy_name")
            or data.get("name")
            or summary_path.parent.name
        )
        if strategy_filter and strategy_filter.lower() not in strategy_name.lower():
            continue

        yearly_int = {int(k): float(v) for k, v in yearly_raw.items() if v is not None}
        result.append({
            "run_id": data.get("run_id", summary_path.parent.name),
            "strategy_name": strategy_name,
            "start_date": data.get("start_date", data.get("period", "")[:10]),
            "end_date": data.get("end_date", data.get("period", "")[-10:]),
            "overall_ar": metrics.get("annualized_return", data.get("annual_return", 0.0)),
            "overall_sharpe": metrics.get("sharpe_ratio", data.get("sharpe", 0.0)),
            "overall_mdd": metrics.get("max_drawdown", data.get("max_drawdown", 0.0)),
            "yearly_returns": yearly_int,
        })

    logger.info("文件扫描: 找到 %d 条含逐年数据的记录", len(result))
    return result


# ── 指标计算 ──────────────────────────────────────────────────────────────────


def build_yearly_row(raw: dict[str, Any], anomaly_year: int) -> YearlyReturnRow | None:
    """从原始摘要字典构建 YearlyReturnRow。

    Args:
        raw: 包含 yearly_returns / strategy_name 的字典
        anomaly_year: 要标记的异常年份

    Returns:
        YearlyReturnRow 或 None（数据不足时）
    """
    yearly = raw.get("yearly_returns", {})
    if not yearly:
        return None

    # 确保年份是 int
    yearly_int: dict[int, float] = {
        int(k): float(v) for k, v in yearly.items() if v is not None
    }
    if len(yearly_int) < 1:
        return None

    n_years = len(yearly_int)
    n_positive = sum(1 for v in yearly_int.values() if v > 0)
    positive_ratio = n_positive / n_years if n_years > 0 else 0.0

    anomaly_contribution = _compute_anomaly_contribution(yearly_int, anomaly_year)
    ex_ar, ex_pos_ratio = _ex_anomaly_ar_proxy(yearly_int, anomaly_year)

    return YearlyReturnRow(
        strategy_name=raw.get("strategy_name", ""),
        run_id=raw.get("run_id", ""),
        start_date=str(raw.get("start_date", "")),
        end_date=str(raw.get("end_date", "")),
        overall_ar=float(raw.get("overall_ar", 0.0) or 0.0),
        overall_sharpe=float(raw.get("overall_sharpe", 0.0) or 0.0),
        overall_mdd=float(raw.get("overall_mdd", 0.0) or 0.0),
        yearly=yearly_int,
        n_positive=n_positive,
        n_years=n_years,
        positive_ratio=positive_ratio,
        anomaly_contribution=anomaly_contribution,
        ex_anomaly_ar_proxy=ex_ar,
        ex_anomaly_positive_ratio=ex_pos_ratio,
    )


def _compute_anomaly_contribution(
    yearly: dict[int, float],
    anomaly_year: int,
) -> float:
    """计算异常年份收益占其余年份均値的倍数（偏差倍数）。

    公式: anomaly_return / mean(all_other_years_return)
    若其余年份均値 ≤ 0，返回 +inf。
    """
    anomaly_val = yearly.get(anomaly_year)
    if anomaly_val is None:
        return 0.0

    other = [v for y, v in yearly.items() if y != anomaly_year]
    if not other:
        return 0.0

    other_mean = sum(other) / len(other)
    if other_mean <= 0:
        return float("inf") if anomaly_val > 0 else 0.0

    return anomaly_val / other_mean


def _ex_anomaly_ar_proxy(
    yearly: dict[int, float],
    anomaly_year: int,
) -> tuple[float, float]:
    """剔除异常年后的年均收益与正收益比例。

    Returns:
        (ex_anomaly_ar_proxy, ex_anomaly_positive_ratio)
    """
    other = {y: v for y, v in yearly.items() if y != anomaly_year}
    if not other:
        return 0.0, 0.0

    ex_ar = sum(other.values()) / len(other)
    ex_pos_ratio = sum(1 for v in other.values() if v > 0) / len(other)
    return ex_ar, ex_pos_ratio


def build_report(
    rows: list[YearlyReturnRow],
    anomaly_year: int,
) -> YearlyReport:
    """整合所有行，构建 YearlyReport。"""
    from datetime import datetime as _dt

    all_years: set[int] = set()
    for row in rows:
        all_years.update(row.yearly.keys())

    n_stable = sum(1 for r in rows if r.passes_yearly_stability)

    # median anomaly contribution
    contributions = [
        r.anomaly_contribution
        for r in rows
        if r.yearly.get(anomaly_year) is not None
        and r.anomaly_contribution != float("inf")
    ]
    median_ac = 0.0
    if contributions:
        sorted_c = sorted(contributions)
        mid = len(sorted_c) // 2
        median_ac = (
            sorted_c[mid]
            if len(sorted_c) % 2 == 1
            else (sorted_c[mid - 1] + sorted_c[mid]) / 2
        )

    return YearlyReport(
        generated_at=_dt.now().isoformat(timespec="seconds"),
        anomaly_year=anomaly_year,
        n_strategies=len(rows),
        rows=rows,
        all_years=sorted(all_years),
        n_stable=n_stable,
        median_anomaly_contribution=median_ac,
    )


# ── 输出渲染 ──────────────────────────────────────────────────────────────────


def print_table(report: YearlyReport, verbose: bool = False) -> None:
    """打印逐年收益表格到标准输出。"""
    years = report.all_years
    if not years:
        print("(无逐年数据)")
        return

    # 标题行
    header_cols = ["Strategy", "Overall_AR", "Sharpe", "MDD"] + [
        f"{y}({'*' if y == report.anomaly_year else ''})" for y in years
    ] + ["Pos%", f"Ex{report.anomaly_year}_AR"]
    sep = "-" * (24 + 10 * (len(header_cols) - 1))

    print(sep)
    header = f"{'Strategy':<28s}" + "".join(
        f"{c:>10s}" for c in header_cols[1:]
    )
    print(header)
    print(sep)

    # 按整体 AR 降序排序
    sorted_rows = sorted(report.rows, key=lambda r: r.overall_ar, reverse=True)

    for row in sorted_rows:
        name = row.strategy_name
        if len(name) > 27:
            name = name[:24] + "..."

        parts = [
            f"{row.overall_ar:>+9.1%}",
            f"{row.overall_sharpe:>9.2f}",
            f"{row.overall_mdd:>9.1%}",
        ]
        for y in years:
            val = row.yearly.get(y)
            if val is None:
                cell = f"{'N/A':>9s}"
            else:
                cell = f"{val:>+9.1%}"
                if y == report.anomaly_year:
                    # ANSI 黄色标记异常年
                    cell = f"\033[93m{cell}\033[0m"
            parts.append(cell)
        parts.append(f"{row.positive_ratio:>9.0%}")
        parts.append(f"{row.ex_anomaly_ar_proxy:>+9.1%}")

        line = f"{name:<28s}" + "".join(parts)
        print(line)

    print(sep)

    if verbose:
        print(f"\n*标记年份 ({report.anomaly_year}) 为异常年份，验证时应谨慎对待")
        print(f"Ex{report.anomaly_year}_AR = 剔除 {report.anomaly_year} 后其余年份算术均値")


def print_summary(report: YearlyReport) -> None:
    """打印摘要统计到标准输出。"""
    first_year = min(report.all_years, default="N/A")
    last_year = max(report.all_years, default="N/A")
    print(f"\n=== 逐年收益分析摘要 [{report.generated_at}] ===")
    print(f"  共加载 {report.n_strategies} 个策略，覆盖年份: {first_year}-{last_year}")
    print(f"  异常年份: {report.anomaly_year}")
    print(
        f"  逐年稳定达标策略数: {report.n_stable}/{report.n_strategies} "
        f"(正收益年份比例≥{MIN_POSITIVE_YEARS_RATIO:.0%})"
    )
    if report.median_anomaly_contribution != float("inf"):
        print(
            f"  {report.anomaly_year} 年收益对比其余年份平均值的中位倍数: "
            f"{report.median_anomaly_contribution:.1f}x"
        )

    # 高异常依赖策略预警
    high_dep = [
        r for r in report.rows
        if r.anomaly_contribution > 2.0
        and r.anomaly_contribution != float("inf")
    ]
    if high_dep:
        print(f"  [!] 高异常高依赖策略 (异常年>2x平均): {len(high_dep)} 个")
        for r in sorted(high_dep, key=lambda x: x.anomaly_contribution, reverse=True)[:5]:
            label = f"{r.anomaly_contribution:.1f}x"
            print(f"    - {r.strategy_name}: {label}")


def save_report(report: YearlyReport, output_path: Path) -> None:
    """将报告序列化为 JSON 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    out: dict[str, Any] = {
        "generated_at": report.generated_at,
        "anomaly_year": report.anomaly_year,
        "n_strategies": report.n_strategies,
        "n_stable": report.n_stable,
        "all_years": report.all_years,
        "median_anomaly_contribution": round(
            report.median_anomaly_contribution, 4
        ) if report.median_anomaly_contribution != float("inf") else None,
        "strategies": [
            {
                "strategy_name": r.strategy_name,
                "run_id": r.run_id,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "overall_ar": round(r.overall_ar, 6),
                "overall_sharpe": round(r.overall_sharpe, 4),
                "overall_mdd": round(r.overall_mdd, 6),
                "n_positive": r.n_positive,
                "n_years": r.n_years,
                "positive_ratio": round(r.positive_ratio, 4),
                "anomaly_contribution": (
                    round(r.anomaly_contribution, 4)
                    if r.anomaly_contribution != float("inf")
                    else None
                ),
                "ex_anomaly_ar_proxy": round(r.ex_anomaly_ar_proxy, 6),
                "ex_anomaly_positive_ratio": round(r.ex_anomaly_positive_ratio, 4),
                "passes_yearly_stability": r.passes_yearly_stability,
                "yearly_returns": {
                    str(k): round(v, 6) for k, v in sorted(r.yearly.items())
                },
            }
            for r in sorted(report.rows, key=lambda x: x.overall_ar, reverse=True)
        ],
    }

    output_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n报告已保存: {output_path}")


# ── CLI 入口 ──────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="analyze_yearly_returns",
        description="逐年收益分析与异常年份 (2020) 偏差评估",
    )
    parser.add_argument(
        "--strategy", default=None,
        help="策略名称和子串过滤（不设则显示全部）",
    )
    parser.add_argument(
        "--min-years", type=int, default=2,
        help="过滤最少年份数（默认 2）",
    )
    parser.add_argument(
        "--exclude-year", type=int, default=ANOMALY_YEAR,
        help=f"标记异常年份（默认 {ANOMALY_YEAR}）",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT_PATH),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="屏蔽文件保存",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="输出详细信息",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Returns:
        0 成功，非 0 失败。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 1. 加载数据
    records = load_yearly_data(
        strategy_filter=args.strategy,
        min_years=args.min_years,
    )
    if not records:
        print("未找到含逐年数据的回测记录（DB 为空且 output/ 无符合条件的 summary.json）")
        return 1

    # 2. 构建每行
    rows: list[YearlyReturnRow] = []
    for raw in records:
        row = build_yearly_row(raw, anomaly_year=args.exclude_year)
        if row is not None:
            rows.append(row)
    if not rows:
        print("所有记录数据不足，无法构建逐年行")
        return 1

    # 3. 构建报告
    report = build_report(rows, anomaly_year=args.exclude_year)

    # 4. 打印
    print_table(report, verbose=args.verbose)
    print_summary(report)

    # 5. 保存
    if not args.no_save:
        save_report(report, Path(args.output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
