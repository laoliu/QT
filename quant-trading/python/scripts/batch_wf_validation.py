"""批量 Walk-Forward 验证：V3.1 全部生产策略

对 Tiered Combo V3.1 的 4 个组件分别执行 5-fold WF 验证，
结果自动写入 backtest_index.db (wf_results 表)。

用法:
    python scripts/batch_wf_validation.py
    python scripts/batch_wf_validation.py --strategies csi2000 csi300
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

# V3.1 生产策略 WF 配置
PRODUCTION_STRATEGIES: dict[str, dict[str, Any]] = {
    "csi2000": {
        "config": "configs/backtest/ml_csi2000_lt20b.yml",
        "name": "ml_csi2000_lt20b",
        "train_days": 504,
        "test_days": 252,
        "n_splits": 5,
        "purged": True,
        "description": "CSI2000 <20B 6-model ensemble",
    },
    "csi300": {
        "config": "configs/backtest/ml_csi300_ge200b_t1a.yml",
        "name": "ml_csi300_ge200b_t1a",
        "train_days": 504,
        "test_days": 252,
        "n_splits": 5,
        "purged": True,
        "description": "CSI300 >=200B zero-decay",
    },
}


def run_single_wf(key: str, spec: dict[str, Any]) -> dict[str, Any]:
    """运行单个策略的 WF 验证"""
    from scripts.wf_config_runner import run_walk_forward_from_config
    from aipha.backtest.validation import WalkForwardThresholds

    config_path = Path(spec["config"])
    if not config_path.exists():
        logger.error("配置文件不存在: %s", config_path)
        return {"key": key, "status": "SKIP", "reason": "config_not_found"}

    thresholds = WalkForwardThresholds(
        decay_threshold=0.50,
        win_rate_threshold=0.50,
        sharpe_threshold=0.0,
    )

    logger.info("=" * 60)
    logger.info("开始 WF 验证: %s (%s)", key, spec["description"])
    logger.info("配置: %s", spec["config"])
    logger.info("参数: train=%d, test=%d, splits=%d, purged=%s",
                spec["train_days"], spec["test_days"],
                spec["n_splits"], spec["purged"])
    logger.info("=" * 60)

    try:
        result = run_walk_forward_from_config(
            config_path=str(config_path),
            train_days=spec["train_days"],
            test_days=spec["test_days"],
            n_splits=spec["n_splits"],
            purged=spec["purged"],
            thresholds=thresholds,
        )
        result["key"] = key
        result["strategy_name"] = spec["name"]
        result["status"] = "OK"
        return result
    except Exception as exc:
        logger.exception("WF 验证失败: %s", key)
        return {"key": key, "status": "ERROR", "reason": str(exc)}


def save_wf_to_db(result: dict[str, Any]) -> None:
    """将 WF 结果写入 SQLite"""
    from aipha.backtest.result_store import ResultStore

    if result.get("status") != "OK":
        logger.warning("跳过非成功结果: %s", result.get("key"))
        return

    store = ResultStore()
    wf_id = f"wf_{result['strategy_name']}_{datetime.now():%Y%m%d_%H%M%S}"

    report = result.get("report")
    wf_data = {
        "wf_id": wf_id,
        "strategy_name": result["strategy_name"],
        "verdict": result.get("verdict", "UNKNOWN"),
        "score": result.get("score", 0.0),
        "config_path": PRODUCTION_STRATEGIES[result["key"]]["config"],
        "train_days": PRODUCTION_STRATEGIES[result["key"]]["train_days"],
        "test_days": PRODUCTION_STRATEGIES[result["key"]]["test_days"],
        "n_splits": PRODUCTION_STRATEGIES[result["key"]]["n_splits"],
        "purged": 1 if PRODUCTION_STRATEGIES[result["key"]]["purged"] else 0,
    }

    if report and hasattr(report, "to_dict"):
        wf_data["splits_json"] = json.dumps(report.to_dict(), default=str)

    store.upsert_wf_result(wf_data)
    logger.info("WF 结果已写入 DB: %s (verdict=%s, score=%.2f)",
                wf_id, wf_data["verdict"], wf_data["score"])


def print_summary(results: list[dict[str, Any]]) -> None:
    """打印汇总表"""
    print("\n" + "=" * 70)
    print("Walk-Forward 验证汇总")
    print("=" * 70)
    print(f"{'策略':<20} {'状态':<8} {'判定':<8} {'评分':<8} {'说明'}")
    print("-" * 70)

    for r in results:
        key = r.get("key", "?")
        status = r.get("status", "?")
        verdict = r.get("verdict", "-")
        score = r.get("score", 0.0)
        reason = r.get("reason", "")

        if status == "OK":
            desc = f"score={score:.2f}"
        else:
            desc = reason[:40]

        print(f"{key:<20} {status:<8} {verdict:<8} {score:<8.2f} {desc}")

    print("=" * 70)

    pass_count = sum(1 for r in results
                     if r.get("verdict") == "PASS")
    total = len(results)
    print(f"\n通过: {pass_count}/{total}")

    if pass_count < total:
        print("⚠️  存在未通过的策略，请检查 WF 报告")


def main() -> None:
    """主入口"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="批量 Walk-Forward 验证 V3.1 生产策略"
    )
    parser.add_argument(
        "--strategies", nargs="*", default=None,
        choices=list(PRODUCTION_STRATEGIES.keys()),
        help="指定要验证的策略（默认全部）",
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="不写入 SQLite（仅打印结果）",
    )
    args = parser.parse_args()

    keys = args.strategies or list(PRODUCTION_STRATEGIES.keys())
    results: list[dict[str, Any]] = []

    for key in keys:
        spec = PRODUCTION_STRATEGIES[key]
        result = run_single_wf(key, spec)
        results.append(result)

        if not args.no_db and result.get("status") == "OK":
            save_wf_to_db(result)

    print_summary(results)


if __name__ == "__main__":
    main()
