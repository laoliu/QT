#!/usr/bin/env python3
"""质量门禁 CLI — 对已有回测结果执行质量检查。

用法:
    # 检查单个回测结果
    python scripts/quality_gate.py output/triple_low_8y/summary.json

    # 检查多个结果
    python scripts/quality_gate.py output/*/summary.json

    # 使用自定义门禁配置
    python scripts/quality_gate.py output/triple_low_8y/summary.json \\
        --gates configs/quality_gates.yml

    # 仅在有 error 时输出 (CI 模式)
    python scripts/quality_gate.py output/*/summary.json --ci

    # 检查所有 SQLite 索引中的回测
    python scripts/quality_gate.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def _load_metrics(summary_path: Path) -> dict:
    """从 summary.json 加载指标字典。"""
    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)
    metrics = data.get("metrics", {})
    metrics["strategy_name"] = data.get("strategy_name", summary_path.parent.name)
    return metrics


def _check_file(summary_path: Path, checker, *, verbose: bool = True) -> bool:
    """检查单个 summary.json，返回是否通过。"""
    metrics = _load_metrics(summary_path)
    name = metrics.get("strategy_name", "unknown")

    report = checker.check(metrics, strategy_name=name)

    if verbose or not report.all_passed:
        status = "PASS" if report.all_passed else "FAIL"
        print(f"  [{status}] {name:30s} "
              f"AR={metrics.get('annualized_return', 0)*100:6.2f}% "
              f"MDD={metrics.get('max_drawdown_pct', 0)*100:5.1f}% "
              f"SR={metrics.get('sharpe_ratio', 0):.3f}")

        if not report.all_passed:
            for r in report.results:
                if not r.passed:
                    print(f"        [{r.gate.severity.upper():5s}] "
                          f"{r.gate.metric}: {r.actual_value:.4f} "
                          f"{r.gate.operator} {r.gate.threshold} -- {r.gate.description}")

    return report.all_passed


def _check_all_from_db(checker, *, verbose: bool = True) -> tuple[int, int]:
    """从 SQLite 索引中加载所有回测并检查。"""
    from aipha.backtest.result_store import ResultStore

    db_path = project_root / "output" / "backtest_index.db"
    if not db_path.exists():
        print(f"  SQLite 索引不存在: {db_path}")
        return 0, 0

    store = ResultStore(db_path)
    rows = store.query_runs()  # type: ignore[attr-defined]

    passed = failed = 0
    for row in rows:
        metrics = dict(row)
        name = metrics.get("strategy_name", "?")
        report = checker.check(metrics, strategy_name=name)
        ok = report.all_passed

        if verbose or not ok:
            status = "PASS" if ok else "FAIL"
            ar = metrics.get("annualized_return", 0) or 0
            mdd = metrics.get("max_drawdown_pct", 0) or 0
            sr = metrics.get("sharpe_ratio", 0) or 0
            print(f"  [{status}] {name:30s} AR={ar*100:6.2f}% MDD={mdd*100:5.1f}% SR={sr:.3f}")

        if ok:
            passed += 1
        else:
            failed += 1

    return passed, failed


def main():
    parser = argparse.ArgumentParser(description="质量门禁检查工具")
    parser.add_argument("files", nargs="*", help="summary.json 文件路径")
    parser.add_argument("--gates", default="configs/quality_gates.yml",
                        help="门禁配置 YAML (default: configs/quality_gates.yml)")
    parser.add_argument("--ci", action="store_true",
                        help="CI 模式：仅 FAIL 输出，exit code = 失败数")
    parser.add_argument("--all", action="store_true",
                        help="从 SQLite 索引检查所有回测")
    args = parser.parse_args()

    from aipha.backtest.quality_gates import QualityChecker

    checker = QualityChecker.from_yaml(args.gates)
    verbose = not args.ci

    print("=" * 72)
    print("质量门禁检查")
    print("=" * 72)

    total_pass = total_fail = 0

    if args.all:
        total_pass, total_fail = _check_all_from_db(checker, verbose=verbose)
    elif args.files:
        for fpath in args.files:
            p = Path(fpath)
            if not p.exists():
                print(f"  [SKIP] 文件不存在: {p}")
                continue
            ok = _check_file(p, checker, verbose=verbose)
            if ok:
                total_pass += 1
            else:
                total_fail += 1
    else:
        # 默认: 扫描 output/*/summary.json
        for summary in sorted((project_root / "output").glob("*/summary.json")):
            ok = _check_file(summary, checker, verbose=verbose)
            if ok:
                total_pass += 1
            else:
                total_fail += 1

    print("-" * 72)
    print(f"总计: {total_pass} PASS, {total_fail} FAIL")
    print("=" * 72)

    sys.exit(total_fail if args.ci else 0)


if __name__ == "__main__":
    main()
