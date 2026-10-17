#!/usr/bin/env python
"""可转债（CB）策略基准跑分工具。

目的：
- 批量运行 configs/cb_strategies/*.yml
- 汇总年化收益、回撤、Sharpe、交易次数、费用拖累等指标
- 输出 CSV/JSON，按年化或 Sharpe 排名，方便筛选“冲 30% 年化”的候选策略

用法示例:
    python scripts/cb_benchmark.py
    python scripts/cb_benchmark.py --start 2022-01-01 --end 2024-12-31
    python scripts/cb_benchmark.py --pattern "configs/cb_strategies/*.yml" --max-configs 10

注意：历史回测结果不代表未来表现；本工具只做可复现实验对比。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

# 允许从仓库根目录直接运行脚本
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BenchmarkResult:
    config_path: str
    strategy: str
    start_date: str
    end_date: str
    initial_capital: float
    final_nav: float
    total_return_pct: float
    annual_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe: float | None
    calmar: float | None
    total_trades: int
    total_fees: float
    cost_drag_pct: float | None
    error: str | None = None


def discover_configs(pattern: str) -> list[Path]:
    paths = sorted(Path().glob(pattern))
    return [p for p in paths if p.is_file()]


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _extract_metrics(run_result) -> dict[str, Any]:
    """从 BacktestRunResult 提取可排序指标。"""
    result = run_result.result
    report = getattr(run_result, "report", None)

    metrics: dict[str, Any] = {
        "initial_capital": float(result.initial_capital),
        "final_nav": float(result.final_nav),
        "total_return_pct": (float(result.final_nav) / float(result.initial_capital) - 1.0) * 100.0,
        "total_trades": int(result.total_trades),
        "total_fees": float(result.total_fees.total),
    }

    if report is None:
        metrics.update(
            {
                "annual_return_pct": None,
                "max_drawdown_pct": None,
                "sharpe": None,
                "calmar": None,
                "cost_drag_pct": None,
            }
        )
        return metrics

    metrics.update(
        {
            "annual_return_pct": _safe_float(report.performance.annualized_return) * 100
            if _safe_float(report.performance.annualized_return) is not None
            else None,
            "max_drawdown_pct": _safe_float(report.drawdown.max_drawdown_pct) * 100
            if _safe_float(report.drawdown.max_drawdown_pct) is not None
            else None,
            "sharpe": _safe_float(report.performance.sharpe_ratio),
            "calmar": _safe_float(report.performance.calmar_ratio),
        }
    )

    cost_drag_pct = None
    if getattr(report, "cost_analysis", None) is not None:
        cost_drag_pct = _safe_float(getattr(report.cost_analysis, "cost_drag_pct", None))
        if cost_drag_pct is not None:
            cost_drag_pct *= 100

    metrics["cost_drag_pct"] = cost_drag_pct
    return metrics


def run_benchmark(
    config_paths: list[Path],
    *,
    output_dir: Path,
    start_override: date | None = None,
    end_override: date | None = None,
    max_configs: int | None = None,
    sort_by: str = "annual_return",
    sleep_s: float = 0.0,
) -> list[BenchmarkResult]:
    """批量运行回测并汇总指标。"""
    # lazy import: 避免在 import 本脚本时就触发大量依赖加载
    from aipha.backtest.config_runner import run_from_config

    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[BenchmarkResult] = []

    if max_configs is not None:
        config_paths = config_paths[: max_configs]

    for i, config_path in enumerate(config_paths, start=1):
        logger.info(f"[{i}/{len(config_paths)}] {config_path}")

        # 如需覆盖日期范围，写临时 YAML（避免污染原配置）
        effective_config_path = config_path
        tmp_path: Path | None = None
        if start_override or end_override:
            import yaml

            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            cfg.setdefault("data", {})
            if start_override:
                cfg["data"]["start_date"] = start_override.isoformat()
            if end_override:
                cfg["data"]["end_date"] = end_override.isoformat()

            tmp_path = output_dir / f"._tmp_{config_path.stem}_{int(time.time()*1000)}.yml"
            tmp_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
            effective_config_path = tmp_path

        try:
            # 每个配置一个独立输出目录，避免互相覆盖
            run_output = output_dir / config_path.stem
            run_result = run_from_config(effective_config_path, output_dir=run_output)

            m = _extract_metrics(run_result)
            br = BenchmarkResult(
                config_path=str(config_path).replace("\\", "/"),
                strategy=str(run_result.result.strategy_name),
                start_date=str(run_result.result.start_date),
                end_date=str(run_result.result.end_date),
                initial_capital=m["initial_capital"],
                final_nav=m["final_nav"],
                total_return_pct=m["total_return_pct"],
                annual_return_pct=m.get("annual_return_pct"),
                max_drawdown_pct=m.get("max_drawdown_pct"),
                sharpe=m.get("sharpe"),
                calmar=m.get("calmar"),
                total_trades=m["total_trades"],
                total_fees=m["total_fees"],
                cost_drag_pct=m.get("cost_drag_pct"),
                error=None,
            )
            results.append(br)

        except Exception as e:
            results.append(
                BenchmarkResult(
                    config_path=str(config_path).replace("\\", "/"),
                    strategy="",
                    start_date=str(start_override) if start_override else "",
                    end_date=str(end_override) if end_override else "",
                    initial_capital=0.0,
                    final_nav=0.0,
                    total_return_pct=0.0,
                    annual_return_pct=None,
                    max_drawdown_pct=None,
                    sharpe=None,
                    calmar=None,
                    total_trades=0,
                    total_fees=0.0,
                    cost_drag_pct=None,
                    error=str(e),
                )
            )
            logger.error(f"回测失败: {config_path}: {e}")
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        if sleep_s > 0:
            time.sleep(sleep_s)

    _save_results(results, output_dir)
    _print_top(results, sort_by=sort_by)
    return results


def _save_results(results: list[BenchmarkResult], output_dir: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"cb_benchmark_{ts}.csv"
    json_path = output_dir / f"cb_benchmark_{ts}.json"

    rows = [asdict(r) for r in results]

    # CSV
    fieldnames = list(rows[0].keys()) if rows else []
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": ts,
                "n": len(results),
                "results": rows,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(f"结果已保存: {csv_path}")
    logger.info(f"结果已保存: {json_path}")


def _sort_key(sort_by: str):
    if sort_by == "sharpe":
        return lambda r: (r.error is None, r.sharpe if r.sharpe is not None else -1e9)
    # 默认按年化
    return lambda r: (r.error is None, r.annual_return_pct if r.annual_return_pct is not None else -1e9)


def _print_top(results: list[BenchmarkResult], *, sort_by: str, top_n: int = 10) -> None:
    ok = [r for r in results if r.error is None]
    ok_sorted = sorted(ok, key=_sort_key(sort_by), reverse=True)

    print("\n" + "=" * 110)
    print(f"CB 策略基准跑分 Top{min(top_n, len(ok_sorted))} (sort_by={sort_by})")
    print("=" * 110)
    print(
        f"{'策略':22s} {'年化%':>8s} {'回撤%':>8s} {'Sharpe':>8s} {'Calmar':>8s} {'收益%':>8s} {'交易':>6s} {'费用':>10s}  配置"
    )
    print("-" * 110)

    for r in ok_sorted[:top_n]:
        ar = r.annual_return_pct if r.annual_return_pct is not None else float("nan")
        dd = r.max_drawdown_pct if r.max_drawdown_pct is not None else float("nan")
        sr = r.sharpe if r.sharpe is not None else float("nan")
        cal = r.calmar if r.calmar is not None else float("nan")
        print(
            f"{r.strategy[:22]:22s} {ar:8.2f} {dd:8.2f} {sr:8.3f} {cal:8.3f} {r.total_return_pct:8.2f} {r.total_trades:6d} {r.total_fees:10.2f}  {Path(r.config_path).name}"
        )

    print("=" * 110)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="可转债策略基准跑分工具")
    parser.add_argument(
        "--pattern",
        default="configs/cb_strategies/*.yml",
        help="配置文件 glob (默认: configs/cb_strategies/*.yml)",
    )
    parser.add_argument(
        "--output",
        default="results/cb_benchmark",
        help="输出目录 (默认: results/cb_benchmark)",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="覆盖开始日期 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="覆盖结束日期 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--max-configs",
        type=int,
        default=None,
        help="最多运行多少个配置（用于快速试跑）",
    )
    parser.add_argument(
        "--sort-by",
        choices=["annual_return", "sharpe"],
        default="annual_return",
        help="排序维度",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="每次回测之间 sleep 秒数（默认 0）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    configs = discover_configs(args.pattern)
    if not configs:
        print(f"未找到配置: pattern={args.pattern}")
        return 2

    start_override = _parse_iso_date(args.start)
    end_override = _parse_iso_date(args.end)

    run_benchmark(
        configs,
        output_dir=Path(args.output),
        start_override=start_override,
        end_override=end_override,
        max_configs=args.max_configs,
        sort_by=args.sort_by,
        sleep_s=args.sleep,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
