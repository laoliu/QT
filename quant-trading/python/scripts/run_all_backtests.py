#!/usr/bin/env python3
"""全策略多资金量批量回测 — 一键运行所有候选策略

运行顺序：
1. PEAD Event 20y (A股)
2. Multifactor Enhance 20y (A股)
3. Dividend Defensive 20y (A股)
4. CB small_size 7y (可转债)

每个策略 × 6 资金量 = 24 次回测

用法:
    python scripts/run_all_backtests.py
    python scripts/run_all_backtests.py --strategies pead_event,multifactor_enhance
    python scripts/run_all_backtests.py --capitals 1000000,5000000
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aipha.utils.paths import project_path, resolve_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 策略配置表 ──
STRATEGY_CONFIGS = {
    "pead_event": "configs/backtest/pead_event_20y.yml",
    "multifactor_enhance": "configs/backtest/multifactor_enhance_20y.yml",
    "dividend_defensive": "configs/backtest/dividend_defensive_20y.yml",
    "cb_small_size": "configs/cb_strategies/small_size_7y.yml",
}

DEFAULT_CAPITALS = [
    500_000,     # 50 万
    1_000_000,   # 100 万
    1_500_000,   # 150 万
    2_000_000,   # 200 万
    3_000_000,   # 300 万
    5_000_000,   # 500 万
]


def main():
    parser = argparse.ArgumentParser(description="全策略多资金量批量回测")
    parser.add_argument(
        "--strategies", type=str, default=None,
        help="逗号分隔的策略名（默认运行全部）",
    )
    parser.add_argument(
        "--capitals", type=str, default=None,
        help="逗号分隔的资金量列表，单位元",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="输出根目录",
    )
    args = parser.parse_args()

    # 解析策略
    if args.strategies:
        strat_names = [s.strip() for s in args.strategies.split(",")]
    else:
        strat_names = list(STRATEGY_CONFIGS.keys())

    # 解析资金量
    if args.capitals:
        capitals = [float(c.strip()) for c in args.capitals.split(",")]
    else:
        capitals = DEFAULT_CAPITALS

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_output = resolve_path(args.output_dir) if args.output_dir else project_path(f"output/batch_{ts}")
    base_output.mkdir(parents=True, exist_ok=True)

    total_runs = len(strat_names) * len(capitals)
    logger.info(f"批量回测开始: {len(strat_names)} 策略 × {len(capitals)} 资金量 = {total_runs} 次运行")
    logger.info(f"策略: {strat_names}")
    logger.info(f"资金量: {[f'{int(c/10000)}万' for c in capitals]}")
    logger.info(f"输出目录: {base_output}")

    all_results: list[dict] = []
    t0_total = time.time()
    run_idx = 0

    for strat_name in strat_names:
        config_path = STRATEGY_CONFIGS.get(strat_name)
        if not config_path:
            logger.error(f"未知策略: {strat_name}, 跳过")
            continue

        if not Path(config_path).exists():
            logger.error(f"配置文件不存在: {config_path}, 跳过")
            continue

        logger.info(f"\n{'#'*60}")
        logger.info(f"# 策略: {strat_name}")
        logger.info(f"# 配置: {config_path}")
        logger.info(f"{'#'*60}")

        # 调用 multi_capital_backtest 的核心逻辑
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            orig_yaml = yaml.safe_load(f)

        # 导入 multi_capital runner
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        from multi_capital_backtest import run_single

        for capital in capitals:
            run_idx += 1
            cap_str = f"{int(capital / 10000)}w"
            logger.info(
                f"\n[{run_idx}/{total_runs}] {strat_name} @ {cap_str}"
            )

            strat_output = base_output / strat_name
            result = run_single(orig_yaml, capital, strat_output, strat_name)
            all_results.append(result)

            # 每次运行后输出进度
            elapsed = time.time() - t0_total
            remaining_runs = total_runs - run_idx
            avg_per_run = elapsed / run_idx
            eta = remaining_runs * avg_per_run
            logger.info(
                f"  进度: {run_idx}/{total_runs}, "
                f"已用 {elapsed/60:.1f}min, "
                f"剩余 ~{eta/60:.0f}min"
            )

    total_time = time.time() - t0_total

    # 汇总 CSV
    csv_path = base_output / "all_strategies_multi_capital.csv"
    if all_results:
        fieldnames = []
        for r in all_results:
            for k in r:
                if k not in fieldnames:
                    fieldnames.append(k)

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        logger.info(f"\n汇总已保存: {csv_path}")

    # 打印汇总
    logger.info(f"\n{'='*100}")
    logger.info(f"  全策略多资金量回测汇总 ({total_time/60:.1f} 分钟)")
    logger.info(f"{'='*100}")
    header = (
        f"{'策略':>24s}  {'资金':>8s}  {'年化%':>8s}  {'回撤%':>8s}  "
        f"{'Sharpe':>7s}  {'Calmar':>7s}  {'交易':>6s}  {'费用':>10s}"
    )
    logger.info(header)
    logger.info("-" * 100)

    for r in all_results:
        if "error" in r:
            logger.info(
                f"{r['strategy']:>24s}  {r['capital_label']:>8s}  "
                f"ERROR: {r['error'][:40]}"
            )
            continue
        logger.info(
            f"{r['strategy']:>24s}  {r['capital_label']:>8s}  "
            f"{r.get('annual_return_pct', 'N/A'):>8}  "
            f"{r.get('max_drawdown_pct', 'N/A'):>8}  "
            f"{r.get('sharpe', 'N/A'):>7}  "
            f"{r.get('calmar', 'N/A'):>7}  "
            f"{r.get('total_trades', 'N/A'):>6}  "
            f"{r.get('total_fees', 'N/A'):>10}"
        )

    logger.info(f"{'='*100}")

    errors = [r for r in all_results if "error" in r]
    if errors:
        logger.warning(f"\n{len(errors)}/{total_runs} 次运行失败")
        sys.exit(1)
    else:
        logger.info(f"\n全部 {total_runs} 次运行成功！")


if __name__ == "__main__":
    main()
