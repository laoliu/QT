#!/usr/bin/env python
"""批量资金量扫描 — 对所有 WF-PASS 策略运行 4 档资金回测。

capitals: 100万 / 300万 / 500万 / 1000万

用法:
    python scripts/run_capital_sweep_batch.py             # 运行全部策略
    python scripts/run_capital_sweep_batch.py --dry-run   # 仅打印计划
    python scripts/run_capital_sweep_batch.py --strategies small_size triple_low
    python scripts/run_capital_sweep_batch.py --resume    # 跳过已有结果的策略
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# ── 项目根目录 ─────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.capital_sweep import run_capital_sweep  # noqa: E402

logger = logging.getLogger(__name__)

# ── 资金档位（元）─────────────────────────────────────────────────────────────
CAPITALS = [
    1_000_000,   # 100万
    3_000_000,   # 300万
    5_000_000,   # 500万
    10_000_000,  # 1000万
]

# ── WF-PASS 策略 → 最优配置文件映射 ───────────────────────────────────────────
# 来源: wf_results 表, verdict=PASS, 每策略取最高 score 对应 config_path
WF_PASS_CONFIGS: dict[str, str] = {
    "low_price":           "configs/cb_strategies/low_price.yml",
    "multi_factor":        "configs/cb_strategies/multi_factor.yml",
    "small_size":          "configs/cb_strategies/small_size_v2_7y.yml",
    "triple_low":          "configs/cb_strategies/triple_low.yml",
    "double_low_percentile": "configs/cb_strategies/double_low_pct_50_50.yml",
    "double_low_rank":     "configs/cb_strategies/double_low_rank_50_50.yml",
    "double_low_zscore":   "configs/cb_strategies/double_low_zscore_50_50.yml",
    "pseudo_ytm":          "configs/cb_strategies/pseudo_ytm.yml",
    "small_size_enhanced": "configs/cb_strategies/small_size_enhanced.yml",
    "new_bond":            "configs/cb_strategies/new_bond.yml",
    "new_small":           "configs/cb_strategies/new_small.yml",
    "double_low_plus":     "configs/cb_strategies/double_low_plus_v5_7y.yml",
    "cb_comprehensive":    "configs/cb_strategies/cb_comprehensive_v2_7y.yml",
    "cb_ultra_noage":      "configs/cb_strategies/cb_ultra_noage.yml",
    "cb_reversal_small":   "configs/cb_strategies/cb_reversal_small.yml",
    "ytm_plus":            "configs/cb_strategies/ytm_plus.yml",
    "cb_regime_rotation":  "configs/cb_strategies/cb_regime_rotation.yml",
    "cb_multi_alpha":      "configs/cb_strategies/cb_multi_alpha_10y.yml",
    "cb_ultra":            "configs/cb_strategies/cb_ultra.yml",
    "negative_premium":    "configs/cb_strategies/negative_premium.yml",
}

# ── 输出目录 ───────────────────────────────────────────────────────────────────
SWEEP_OUTPUT_DIR = _ROOT / "results" / "capital_sweep"
BATCH_SUMMARY_PATH = SWEEP_OUTPUT_DIR / "batch_summary.json"


def _has_result(strategy_name: str) -> bool:
    """检查该策略是否已有扫描结果（用于 --resume 模式）。"""
    pattern = f"capital_sweep_{strategy_name}_*.json"
    return any(SWEEP_OUTPUT_DIR.glob(pattern))


def _run_one(
    strategy_name: str,
    config_path: Path,
    capitals: list[float],
) -> dict:
    """运行单策略资金量扫描，返回汇总 dict。"""
    logger.info(f"[{strategy_name}] 开始资金量扫描: {config_path.name}")
    try:
        results = run_capital_sweep(
            config_path=config_path,
            capitals=capitals,
            output_dir=SWEEP_OUTPUT_DIR,
        )
        return {
            "strategy": strategy_name,
            "config": str(config_path),
            "timestamp": datetime.now().isoformat(),
            "results": results,
            "error": None,
        }
    except Exception as e:
        logger.error(f"[{strategy_name}] 扫描失败: {e}", exc_info=True)
        return {
            "strategy": strategy_name,
            "config": str(config_path),
            "timestamp": datetime.now().isoformat(),
            "results": [],
            "error": str(e),
        }


def run_batch(
    strategies: list[str] | None = None,
    capitals: list[float] | None = None,
    resume: bool = False,
    dry_run: bool = False,
) -> dict[str, list[dict]]:
    """批量运行所有 WF-PASS 策略的资金量扫描。

    Args:
        strategies: 要运行的策略列表，None 表示全部
        capitals: 资金档位列表，None 表示使用默认 CAPITALS
        resume: 是否跳过已有结果的策略
        dry_run: 仅打印计划，不实际运行

    Returns:
        {strategy_name: [result_dict, ...]}
    """
    used_caps = capitals or CAPITALS
    target_configs: dict[str, str]

    if strategies:
        missing = [s for s in strategies if s not in WF_PASS_CONFIGS]
        if missing:
            logger.warning(f"以下策略不在 WF-PASS 列表中，跳过: {missing}")
        target_configs = {
            s: WF_PASS_CONFIGS[s] for s in strategies if s in WF_PASS_CONFIGS
        }
    else:
        target_configs = dict(WF_PASS_CONFIGS)

    logger.info(
        f"批量资金量扫描: {len(target_configs)} 个策略, "
        f"资金档位={[c // 10000 for c in used_caps]}万元, "
        f"resume={resume}, dry_run={dry_run}"
    )

    if dry_run:
        for name, cfg in target_configs.items():
            skip = "(已有结果，将跳过)" if resume and _has_result(name) else ""
            print(f"  {name:35s}  <- {cfg}  {skip}")
        return {}

    SWEEP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    batch_results: dict[str, list[dict]] = {}
    failed: list[str] = []

    for idx, (name, cfg_rel) in enumerate(target_configs.items(), start=1):
        if resume and _has_result(name):
            logger.info(f"[{idx}/{len(target_configs)}] {name}: 跳过（已有结果）")
            continue

        logger.info(f"[{idx}/{len(target_configs)}] {name}")
        config_path = _ROOT / cfg_rel.replace("\\", "/")
        if not config_path.exists():
            logger.error(f"配置文件不存在: {config_path}")
            failed.append(name)
            continue

        entry = _run_one(name, config_path, used_caps)
        batch_results[name] = entry["results"]
        if entry["error"]:
            failed.append(name)

    logger.info(
        f"批量扫描完成: 成功={len(batch_results) - len(failed)}, 失败={len(failed)}"
    )
    if failed:
        logger.warning(f"失败策略: {failed}")

    if batch_results:
        save_batch_summary(batch_results)

    return batch_results


def save_batch_summary(batch_results: dict[str, list[dict]]) -> Path:
    """将批量结果保存为 batch_summary.json。"""
    SWEEP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "timestamp": datetime.now().isoformat(),
        "capitals": CAPITALS,
        "strategies": batch_results,
    }
    BATCH_SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"批量结果已保存: {BATCH_SUMMARY_PATH}")
    return BATCH_SUMMARY_PATH


def main() -> None:
    """CLI 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="批量资金量扫描: 对所有 WF-PASS CB 策略运行 4 档资金回测"
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        metavar="NAME",
        help="指定策略子集，不填则运行全部",
    )
    parser.add_argument(
        "--capitals",
        nargs="+",
        type=float,
        metavar="YUAN",
        help="资金档位（元），默认 100万/300万/500万/1000万",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="跳过已有扫描结果的策略",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计划，不实际运行回测",
    )
    args = parser.parse_args()

    run_batch(
        strategies=args.strategies,
        capitals=args.capitals,
        resume=args.resume,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
