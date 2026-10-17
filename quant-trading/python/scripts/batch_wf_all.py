"""批量 Walk-Forward 验证：为所有 SR≥0.8 但无 WF 的策略执行验证

用法:
    python scripts/batch_wf_all.py                     # 默认全部 17 策略
    python scripts/batch_wf_all.py --only triple_low   # 仅指定策略
    python scripts/batch_wf_all.py --train-days 504 --test-days 252 --purged
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aipha.utils.paths import project_path, resolve_path

logger = logging.getLogger(__name__)

# ── 策略名 → 配置文件路径 ──────────────────────────────────────
STRATEGY_CONFIGS: dict[str, str] = {
    "triple_low": "configs/cb_strategies/triple_low.yml",
    "multi_factor": "configs/cb_strategies/multi_factor.yml",
    "double_low_percentile": "configs/cb_strategies/double_low_pct_50_50.yml",
    "new_small": "configs/cb_strategies/new_small.yml",
    "double_low_zscore": "configs/cb_strategies/double_low_zscore_50_50.yml",
    "low_price": "configs/cb_strategies/low_price.yml",
    "double_low_rank": "configs/cb_strategies/double_low_rank_50_50.yml",
    "small_size_enhanced": "configs/cb_strategies/small_size_enhanced.yml",
    "cb_ultra_noage": "configs/cb_strategies/cb_ultra_noage.yml",
    "cb_reversal_small": "configs/cb_strategies/cb_reversal_small.yml",
    "pseudo_ytm": "configs/cb_strategies/pseudo_ytm.yml",
    "new_bond": "configs/cb_strategies/new_bond.yml",
    "forced_redeem_play": "configs/cb_strategies/forced_redeem_play.yml",
    "ytm_plus": "configs/cb_strategies/ytm_plus.yml",
    "cb_regime_rotation": "configs/cb_strategies/cb_regime_rotation.yml",
    "cb_ultra": "configs/cb_strategies/cb_ultra.yml",
    "negative_premium": "configs/cb_strategies/negative_premium.yml",
    # ── A1: top5 CB 策略（R12 评审要求强制 WF 验证）─────────────────────────
    "cb_comprehensive_opt": "configs/cb_strategies/cb_comprehensive_opt_7y.yml",
    "double_low_plus_v4": "configs/cb_strategies/double_low_plus_v4_7y.yml",
    "double_low_plus_opt": "configs/cb_strategies/double_low_plus_opt_7y.yml",
    "double_low_plus_v3": "configs/cb_strategies/double_low_plus_v3_7y.yml",
    "small_size_10y": "configs/cb_strategies/small_size_10y.yml",
}

# WF 默认使用最大可用 CB 数据范围
DEFAULT_START_DATE = "2017-01-01"
DEFAULT_END_DATE = "2025-12-31"

# 默认 WF 参数
DEFAULT_TRAIN_DAYS = 504   # 2年
DEFAULT_TEST_DAYS = 252    # 1年


def _make_wf_config(
    src_path: str,
    start_date: str,
    end_date: str,
) -> str:
    """加载原始配置，覆盖日期范围，写入临时文件并返回路径。

    Args:
        src_path: 原始策略配置 YAML 路径
        start_date: WF 用的起始日期 (YYYY-MM-DD)
        end_date: WF 用的结束日期 (YYYY-MM-DD)

    Returns:
        临时配置文件路径（调用方负责清理）
    """
    import yaml

    with open(src_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    # 覆盖日期范围
    if "data" not in cfg:
        cfg["data"] = {}
    cfg["data"]["start_date"] = start_date
    cfg["data"]["end_date"] = end_date

    # 写入临时文件
    fd, tmp_path = tempfile.mkstemp(suffix=".yml", prefix="wf_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True)

    return tmp_path


def run_batch(
    strategies: list[str] | None = None,
    *,
    train_days: int = DEFAULT_TRAIN_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
    purged: bool = True,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
) -> list[dict[str, Any]]:
    """批量执行 WF 验证并返回结果列表。

    Args:
        strategies: 要验证的策略名列表（None=全部 17 个）
        train_days: 训练期天数
        test_days: 测试期天数
        purged: 是否 Purged WF
        start_date: 数据起始日期（覆盖配置）
        end_date: 数据结束日期（覆盖配置）

    Returns:
        每项为 {strategy_name, verdict, score, ...} 的列表
    """
    from wf_config_runner import run_walk_forward_from_config

    targets = strategies or list(STRATEGY_CONFIGS.keys())
    total = len(targets)
    results: list[dict[str, Any]] = []

    output_dir = project_path("output", "wf_batch")
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, name in enumerate(targets, 1):
        cfg_path = STRATEGY_CONFIGS.get(name)
        if not cfg_path:
            logger.error(f"[{idx}/{total}] 策略 {name} 无配置映射，跳过")
            results.append({
                "strategy_name": name,
                "verdict": "SKIP",
                "score": 0.0,
                "error": "no config mapping",
            })
            continue

        cfg_path = str(resolve_path(cfg_path))
        if not os.path.exists(cfg_path):
            logger.error(f"[{idx}/{total}] 配置文件不存在: {cfg_path}")
            results.append({
                "strategy_name": name,
                "verdict": "SKIP",
                "score": 0.0,
                "error": f"config not found: {cfg_path}",
            })
            continue

        # 生成临时配置（扩展日期范围）
        tmp_cfg = _make_wf_config(cfg_path, start_date, end_date)
        out_json = output_dir / f"{name}_wf.json"

        logger.info(
            f"\n{'='*60}\n"
            f"[{idx}/{total}] WF 验证: {name}\n"
            f"  配置: {cfg_path}\n"
            f"  日期: {start_date} ~ {end_date}\n"
            f"  参数: train={train_days}d test={test_days}d "
            f"purged={purged}\n"
            f"{'='*60}"
        )

        t0 = time.time()
        try:
            result = run_walk_forward_from_config(
                config_path=tmp_cfg,
                train_days=train_days,
                test_days=test_days,
                purged=purged,
            )

            elapsed = time.time() - t0
            result["elapsed_sec"] = round(elapsed, 1)

            # 保存 JSON 结果
            report_dict = result["report"].to_dict()
            out_data = {
                **report_dict,
                "verdict": result["verdict"],
                "score": result["score"],
                "config_path": cfg_path,
                "data_range": result["data_range"],
                "n_trading_days": result["n_trading_days"],
                "elapsed_sec": result["elapsed_sec"],
                "params": {
                    "train_days": result["train_days"],
                    "test_days": result["test_days"],
                    "n_splits": result["n_splits"],
                    "purged": result["purged"],
                },
            }
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(out_data, f, indent=2, ensure_ascii=False)

            # 写入 SQLite 索引
            try:
                from aipha.backtest.result_store import (
                    ResultStore,
                    wf_json_to_wf_dict,
                )
                wf_dict = wf_json_to_wf_dict(out_json)
                store = ResultStore()
                store.upsert_wf_result(wf_dict)
                store.close()
            except Exception as e:
                logger.debug(f"SQLite 索引写入失败: {e}")

            results.append({
                "strategy_name": name,
                "verdict": result["verdict"],
                "score": result["score"],
                "elapsed_sec": result["elapsed_sec"],
                "n_splits": result["n_splits"],
            })

            logger.info(
                f"[{idx}/{total}] {name}: "
                f"{result['verdict']} (score={result['score']}/10) "
                f"[{elapsed:.0f}s]"
            )

        except Exception as e:
            elapsed = time.time() - t0
            logger.error(f"[{idx}/{total}] {name} 失败: {e}")
            results.append({
                "strategy_name": name,
                "verdict": "ERROR",
                "score": 0.0,
                "error": str(e),
                "elapsed_sec": round(elapsed, 1),
            })

        finally:
            # 清理临时文件
            try:
                os.unlink(tmp_cfg)
            except OSError:
                pass

    # 保存批次汇总
    summary_path = output_dir / "batch_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "total": total,
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    logger.info(f"批次汇总已保存: {summary_path}")

    return results


def _print_summary(results: list[dict[str, Any]]) -> None:
    """打印汇总表格。"""
    print(f"\n{'='*72}")
    print(f"{'策略':25s} {'判定':8s} {'评分':6s} {'分折':5s} {'耗时':8s}")
    print(f"{'-'*72}")

    pass_count = 0
    fail_count = 0
    error_count = 0
    skip_count = 0

    for r in results:
        name = r.get("strategy_name", "?")
        verdict = r.get("verdict", "?")
        score = r.get("score", 0.0)
        splits = r.get("n_splits", "-")
        elapsed = r.get("elapsed_sec", 0.0)
        err = r.get("error", "")

        elapsed_str = f"{elapsed:.0f}s" if elapsed else "-"
        score_str = f"{score:.1f}/10" if isinstance(score, (int, float)) else str(score)
        splits_str = str(splits) if splits else "-"

        suffix = f"  ({err})" if err else ""
        print(f"{name:25s} {verdict:8s} {score_str:6s} {splits_str:5s} {elapsed_str:8s}{suffix}")

        if verdict == "PASS":
            pass_count += 1
        elif verdict in ("FAIL", "MARGINAL"):
            fail_count += 1
        elif verdict == "ERROR":
            error_count += 1
        else:
            skip_count += 1

    print(f"{'-'*72}")
    print(
        f"合计: {len(results)} 策略 | "
        f"PASS={pass_count} FAIL={fail_count} ERROR={error_count} SKIP={skip_count}"
    )
    print(f"{'='*72}")


def main() -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="批量 Walk-Forward 验证：为所有缺少 WF 的 CB 策略执行验证"
    )
    parser.add_argument(
        "--only", nargs="+", default=None,
        help="仅验证指定策略（空格分隔）",
    )
    parser.add_argument(
        "--train-days", type=int, default=DEFAULT_TRAIN_DAYS,
        help=f"训练期天数 (默认 {DEFAULT_TRAIN_DAYS})",
    )
    parser.add_argument(
        "--test-days", type=int, default=DEFAULT_TEST_DAYS,
        help=f"测试期天数 (默认 {DEFAULT_TEST_DAYS})",
    )
    parser.add_argument(
        "--no-purge", action="store_true",
        help="不使用 Purged WF",
    )
    parser.add_argument(
        "--start-date", type=str, default=DEFAULT_START_DATE,
        help=f"数据起始日期 (默认 {DEFAULT_START_DATE})",
    )
    parser.add_argument(
        "--end-date", type=str, default=DEFAULT_END_DATE,
        help=f"数据结束日期 (默认 {DEFAULT_END_DATE})",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_only",
        help="仅列出待验证策略，不执行",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    if args.list_only:
        print("待验证策略:")
        for name, cfg in STRATEGY_CONFIGS.items():
            if args.only and name not in args.only:
                continue
            print(f"  {name:25s} -> {cfg}")
        return 0

    results = run_batch(
        strategies=args.only,
        train_days=args.train_days,
        test_days=args.test_days,
        purged=not args.no_purge,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    _print_summary(results)

    pass_count = sum(1 for r in results if r.get("verdict") == "PASS")
    return 0 if pass_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
