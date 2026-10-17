"""压力测试批量运行脚本（H3）

为所有 WF-PASS 策略在极端市场情景下运行聚焦回测，
生成 results/stress_tests/batch_report.json 供平台展示。

场景：
  2018_trade_war   — 2018-01-29 → 2018-12-28 （中美贸易战, 沪深300 -33%）
  2020_covid       — 2020-01-20 → 2020-03-23 （新冠疫情恐慌, 沪深300 -16%）
  2022_drawdown    — 2022-03-01 → 2022-04-27 （上海封控极端回撤, 沪深300 -20%）
  2024_dma_crisis  — 2024-01-15 → 2024-02-08 （DMA微盘股踩踏, CSI2000 -25%）
  2015_crash       — 2015-06-15 → 2015-09-30 （A股股灾, 沪深300 -45%，仅A股策略）

注意: 2015 情景超出大多数 CB 策略数据起始时间，仅适用于 A 股策略。

使用方法::

    python scripts/run_stress_tests.py              # 运行全部
    python scripts/run_stress_tests.py --dry-run    # 仅打印计划
    python scripts/run_stress_tests.py --resume     # 跳过已有结果
    python scripts/run_stress_tests.py --scenarios 2018_trade_war 2022_drawdown
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

# ── 路径设置 ────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.chdir(_REPO_ROOT)

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("stress_tests")

# ── 输出目录 ─────────────────────────────────────────────────────────────────
RESULTS_DIR = _REPO_ROOT / "results" / "stress_tests"
BATCH_REPORT_PATH = RESULTS_DIR / "batch_report.json"

# ── 压力情景定义（与 var.py 中保持一致）────────────────────────────────────
STRESS_SCENARIOS: list[dict] = [
    {
        "name": "2018_trade_war",
        "description": "2018年中美贸易战A股下跌",
        "start_date": date(2018, 1, 29),
        "end_date": date(2018, 12, 28),
        "benchmark_drawdown": 0.33,
    },
    {
        "name": "2020_covid",
        "description": "2020年初新冠疫情恐慌",
        "start_date": date(2020, 1, 20),
        "end_date": date(2020, 3, 23),
        "benchmark_drawdown": 0.16,
    },
    {
        "name": "2022_drawdown",
        "description": "2022年4月上海封控极端回撤",
        "start_date": date(2022, 3, 1),
        "end_date": date(2022, 4, 27),
        "benchmark_drawdown": 0.20,
    },
    {
        "name": "2024_dma_crisis",
        "description": "2024年2月DMA微盘股踩踏(CSI2000 -25%)",
        "start_date": date(2024, 1, 15),
        "end_date": date(2024, 2, 8),
        "benchmark_drawdown": 0.25,
    },
    {
        "name": "2015_crash",
        "description": "2015年股灾(A股策略专用,沪深300 -45%)",
        "start_date": date(2015, 6, 15),
        "end_date": date(2015, 9, 30),
        "benchmark_drawdown": 0.45,
    },
]

# ── 20 个 WF-PASS 策略配置（策略名 → 配置文件路径）──────────────────────────
WF_PASS_CONFIGS: dict[str, str] = {
    "low_price":             "configs/cb_strategies/low_price.yml",
    "multi_factor":          "configs/cb_strategies/multi_factor.yml",
    "small_size":            "configs/cb_strategies/small_size_v2_7y.yml",
    "triple_low":            "configs/cb_strategies/triple_low.yml",
    "double_low_percentile": "configs/cb_strategies/double_low_pct_50_50.yml",
    "double_low_rank":       "configs/cb_strategies/double_low_rank_50_50.yml",
    "double_low_zscore":     "configs/cb_strategies/double_low_zscore_50_50.yml",
    "pseudo_ytm":            "configs/cb_strategies/pseudo_ytm.yml",
    "small_size_enhanced":   "configs/cb_strategies/small_size_enhanced.yml",
    "new_bond":              "configs/cb_strategies/new_bond.yml",
    "new_small":             "configs/cb_strategies/new_small.yml",
    "double_low_plus":       "configs/cb_strategies/double_low_plus_v5_7y.yml",
    "cb_comprehensive":      "configs/cb_strategies/cb_comprehensive_v2_7y.yml",
    "cb_ultra_noage":        "configs/cb_strategies/cb_ultra_noage.yml",
    "cb_reversal_small":     "configs/cb_strategies/cb_reversal_small.yml",
    "ytm_plus":              "configs/cb_strategies/ytm_plus.yml",
    "cb_regime_rotation":    "configs/cb_strategies/cb_regime_rotation.yml",
    "cb_multi_alpha":        "configs/cb_strategies/cb_multi_alpha_10y.yml",
    "cb_ultra":              "configs/cb_strategies/cb_ultra.yml",
    "negative_premium":      "configs/cb_strategies/negative_premium.yml",
}


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _result_path(strategy_name: str, scenario_name: str) -> Path:
    """返回单个情景结果文件路径。"""
    return RESULTS_DIR / strategy_name / f"{scenario_name}.json"


def _has_result(strategy_name: str, scenario_name: str) -> bool:
    """检查是否已有结果（用于 --resume）。"""
    return _result_path(strategy_name, scenario_name).exists()


def _check_date_coverage(config_path: str, scenario: dict) -> bool:
    """检查策略数据范围是否覆盖情景时间段（最小覆盖：情景区间开始前 30 天需有数据）。"""
    try:
        from aipha.backtest.config_runner import load_config
        cfg = load_config(config_path)
        # 需要数据起始在情景开始前（留 warmup buffer）
        return cfg.data.start_date <= scenario["start_date"]
    except Exception:
        return True  # 无法检查时默认允许运行


def _run_one_scenario(
    strategy_name: str,
    config_path: str,
    scenario: dict,
    dry_run: bool = False,
) -> dict:
    """运行单个策略-情景组合，返回结果摘要 dict。"""
    result_path = _result_path(strategy_name, scenario["name"])
    result_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "strategy": strategy_name,
        "scenario": scenario["name"],
        "scenario_description": scenario["description"],
        "start_date": scenario["start_date"].isoformat(),
        "end_date": scenario["end_date"].isoformat(),
        "benchmark_drawdown": scenario.get("benchmark_drawdown"),
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
        "metrics": {},
        "error": None,
    }

    if dry_run:
        summary["status"] = "dry_run"
        return summary

    if not _check_date_coverage(config_path, scenario):
        summary["status"] = "skipped"
        summary["error"] = "策略数据起始时间晚于情景开始时间"
        return summary

    try:
        from aipha.backtest.config_runner import run_from_config

        out_dir = RESULTS_DIR / strategy_name / scenario["name"]
        run_result = run_from_config(
            config_path,
            output_dir=str(out_dir),
            param_overrides={
                "data.start_date": scenario["start_date"].isoformat(),
                "data.end_date":   scenario["end_date"].isoformat(),
            },
        )
        m = run_result.result.metrics if run_result.result and run_result.result.metrics else {}
        summary["metrics"] = {
            "annualized_return": m.get("annualized_return", m.get("total_return")),
            "max_drawdown":      m.get("max_drawdown"),
            "sharpe_ratio":      m.get("sharpe_ratio"),
            "calmar_ratio":      m.get("calmar_ratio"),
            "total_trades":      m.get("total_trades"),
        }
        summary["status"] = "success"
        logger.info(
            f"  [{strategy_name}/{scenario['name']}] "
            f"AR={summary['metrics'].get('annualized_return', 'N/A'):.2%}  "
            f"MDD={summary['metrics'].get('max_drawdown', 'N/A'):.2%}"
        )
    except Exception as exc:
        summary["status"] = "error"
        summary["error"] = str(exc)
        logger.error(f"  [{strategy_name}/{scenario['name']}] ERROR: {exc}")

    json.dump(summary, open(result_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    return summary


def run_batch(
    strategies: list[str] | None = None,
    scenarios: list[str] | None = None,
    resume: bool = False,
    dry_run: bool = False,
) -> list[dict]:
    """批量运行所有策略 × 情景组合。

    Args:
        strategies: 限定策略名称列表（None = 全部 20 个 WF-PASS）
        scenarios:  限定情景名称列表（None = 全部 3 个）
        resume:     跳过已有结果的组合
        dry_run:    仅打印计划，不实际运行

    Returns:
        所有运行结果的 dict 列表
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 确定目标策略
    if strategies:
        missing = [s for s in strategies if s not in WF_PASS_CONFIGS]
        if missing:
            logger.warning(f"未知策略名称（已忽略）: {missing}")
        target_configs = {s: WF_PASS_CONFIGS[s] for s in strategies if s in WF_PASS_CONFIGS}
    else:
        target_configs = dict(WF_PASS_CONFIGS)

    # 确定目标情景
    if scenarios:
        target_scenarios = [s for s in STRESS_SCENARIOS if s["name"] in scenarios]
    else:
        target_scenarios = STRESS_SCENARIOS

    total = len(target_configs) * len(target_scenarios)
    print(f"\n压力测试计划: {len(target_configs)} 策略 x {len(target_scenarios)} 情景 = {total} 个组合")
    if dry_run:
        print("(DRY-RUN 模式, 不执行回测)\n")

    all_results: list[dict] = []
    done = 0

    for strategy_name, config_path in target_configs.items():
        for scenario in target_scenarios:
            done += 1
            key = f"{strategy_name}/{scenario['name']}"
            if resume and _has_result(strategy_name, scenario["name"]) and not dry_run:
                print(f"  [{done:3d}/{total}] SKIP   {key}")
                # 加载已有结果
                result_file = _result_path(strategy_name, scenario["name"])
                all_results.append(json.load(open(result_file, encoding="utf-8")))
                continue

            print(f"  [{done:3d}/{total}] RUN    {key}  ({scenario['start_date']} ~ {scenario['end_date']})")
            t0 = time.monotonic()
            r = _run_one_scenario(strategy_name, config_path, scenario, dry_run=dry_run)
            elapsed = time.monotonic() - t0
            status_icon = {"success": "OK", "skipped": "SKIP", "error": "FAIL", "dry_run": "DRY"}.get(r["status"], "?")
            print(f"             {status_icon} ({elapsed:.1f}s)")
            all_results.append(r)

    return all_results


def save_batch_report(results: list[dict]) -> None:
    """将批量结果汇总保存到 batch_report.json。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(),
        "scenarios": [s["name"] for s in STRESS_SCENARIOS],
        "strategies": list(WF_PASS_CONFIGS.keys()),
        "total_runs": len(results),
        "success_count": sum(1 for r in results if r["status"] == "success"),
        "skip_count": sum(1 for r in results if r["status"] == "skipped"),
        "error_count": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
    json.dump(report, open(BATCH_REPORT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print(f"\n批量报告已保存: {BATCH_REPORT_PATH}")
    print(f"  成功: {report['success_count']}, 跳过: {report['skip_count']}, 失败: {report['error_count']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="压力测试批量运行工具")
    parser.add_argument("--strategies", nargs="+", metavar="NAME", help="限定策略名称")
    parser.add_argument("--scenarios",  nargs="+", metavar="NAME", help="限定情景名称 (2018_trade_war, 2020_covid, 2022_drawdown)")
    parser.add_argument("--resume",  action="store_true", help="跳过已有结果")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划")
    args = parser.parse_args()

    results = run_batch(
        strategies=args.strategies,
        scenarios=args.scenarios,
        resume=args.resume,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        save_batch_report(results)
