"""Walk-Forward Validation for A-Share Equity Strategies.

Uses config_runner pipeline to run full backtests for each IS/OOS window.
Generates WalkForwardValidator-compatible reports with PASS/FAIL judgment.

Usage:
    python scripts/wf_equity.py
"""
import json
import logging
import os
import sys
import tempfile
import time
from copy import deepcopy
from datetime import date
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from aipha.backtest.config_runner import run_from_config
from aipha.backtest.validation import (
    PurgedWalkForwardSplitter,
    SplitResult,
    ValidationReport,
    judge_walk_forward,
    compute_wf_score,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("wf_equity")
logger.setLevel(logging.INFO)

OUTPUT_BASE = Path("output/wf_equity")


# ============================================================================
# Strategy configs for WF: each has a YAML base + WF window parameters
# ============================================================================

STRATEGIES = [
    {
        "name": "balanced_mf_hs300",
        "config_path": "configs/backtest/balanced_mf_hs300_v5.yml",
        # IS=3Y(756 tdays), OOS=1Y(252 tdays), 6 splits, purge=10d, embargo=5d
        "train_days": 756,
        "test_days": 252,
        "n_splits": 6,
        "purge_days": 10,
        "embargo_days": 5,
    },
    {
        "name": "balanced_mf_csi800",
        "config_path": "configs/backtest/balanced_mf_csi800_9y.yml",
        "train_days": 756,
        "test_days": 252,
        "n_splits": 4,
        "purge_days": 10,
        "embargo_days": 5,
    },
    {
        "name": "balanced_mf_hs300_12y",
        "config_path": "configs/backtest/balanced_mf_hs300_12y.yml",
        "train_days": 756,
        "test_days": 252,
        "n_splits": 6,
        "purge_days": 10,
        "embargo_days": 5,
    },
]


def _modify_dates(base_cfg: dict, start_str: str, end_str: str) -> dict:
    """Return a deep-copy of cfg with start/end dates overridden."""
    cfg = deepcopy(base_cfg)
    for section in ("run", "data"):
        if section in cfg:
            cfg[section]["start_date"] = start_str
            cfg[section]["end_date"] = end_str
    return cfg


def _run_period(
    base_cfg: dict,
    start_date: date,
    end_date: date,
    out_dir: Path,
) -> dict[str, float]:
    """Run a single backtest period via config_runner and return metrics."""
    start_s = start_date.strftime("%Y-%m-%d")
    end_s = end_date.strftime("%Y-%m-%d")
    cfg = _modify_dates(base_cfg, start_s, end_s)

    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.yml', delete=False, encoding='utf-8',
        dir=str(OUTPUT_BASE),
    )
    yaml.dump(cfg, tmp, allow_unicode=True)
    tmp.close()
    try:
        res = run_from_config(tmp.name, output_dir=str(out_dir))
        if res.report is not None:
            p = res.report.performance
            d = res.report.drawdown
            return {
                "return": p.annualized_return,
                "sharpe": p.sharpe_ratio,
                "max_drawdown": d.max_drawdown_pct,
                "total_return": p.total_return,
                "calmar": p.calmar_ratio,
            }
        return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    except Exception as e:
        logger.error(f"Failed {start_s}~{end_s}: {e}")
        return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    finally:
        os.unlink(tmp.name)


def validate_one(strat: dict) -> dict:
    """Run purged WF for one strategy, return summary dict."""
    name = strat["name"]
    cfg_path = strat["config_path"]
    logger.info(f"\n{'='*70}\nWF Equity: {name}  ({cfg_path})\n{'='*70}")

    with open(cfg_path, 'r', encoding='utf-8') as f:
        base_cfg = yaml.safe_load(f)

    # Determine overall date range
    start_s = str(
        base_cfg.get("run", {}).get("start_date")
        or base_cfg.get("data", {}).get("start_date", "2014-01-02")
    )
    end_s = str(
        base_cfg.get("run", {}).get("end_date")
        or base_cfg.get("data", {}).get("end_date", "2024-12-31")
    )
    overall_start = date.fromisoformat(start_s)
    overall_end = date.fromisoformat(end_s)

    splitter = PurgedWalkForwardSplitter(
        train_period_days=strat["train_days"],
        test_period_days=strat["test_days"],
        n_splits=strat["n_splits"],
        purge_days=strat.get("purge_days", 10),
        embargo_days=strat.get("embargo_days", 5),
    )
    splits = splitter.split(overall_start, overall_end)
    logger.info(f"Splits: {len(splits)}")
    for i, sp in enumerate(splits):
        logger.info(f"  {i+1}: IS {sp.train_start}~{sp.train_end}  OOS {sp.test_start}~{sp.test_end}")

    strategy_out = OUTPUT_BASE / name
    sr_list: list[SplitResult] = []
    detailed: list[dict] = []

    for i, period in enumerate(splits):
        logger.info(f"\n--- Split {i+1}/{len(splits)} ---")
        is_dir = strategy_out / f"split{i+1}_IS"
        oos_dir = strategy_out / f"split{i+1}_OOS"

        t0 = time.time()
        is_m = _run_period(base_cfg, period.train_start, period.train_end, is_dir)
        logger.info(f"  IS: ret={is_m['return']:.2%}, sharpe={is_m['sharpe']:.2f} ({time.time()-t0:.0f}s)")

        t0 = time.time()
        oos_m = _run_period(base_cfg, period.test_start, period.test_end, oos_dir)
        logger.info(f"  OOS: ret={oos_m['return']:.2%}, sharpe={oos_m['sharpe']:.2f} ({time.time()-t0:.0f}s)")

        # Decay
        is_sharpe = is_m["sharpe"]
        oos_sharpe = oos_m["sharpe"]
        sharpe_decay = (is_sharpe - oos_sharpe) / abs(is_sharpe) if is_sharpe != 0 else 0.0
        is_ret = is_m["return"]
        oos_ret = oos_m["return"]
        ret_decay = (is_ret - oos_ret) / abs(is_ret) if is_ret != 0 else 0.0

        sr_list.append(SplitResult(
            split_id=i + 1,
            period=period,
            train_return=is_ret,
            train_sharpe=is_sharpe,
            train_max_drawdown=is_m["max_drawdown"],
            test_return=oos_ret,
            test_sharpe=oos_sharpe,
            test_max_drawdown=oos_m["max_drawdown"],
            return_decay=ret_decay,
            sharpe_decay=sharpe_decay,
        ))
        detailed.append({
            "split": i + 1,
            "is": f"{period.train_start}~{period.train_end}",
            "oos": f"{period.test_start}~{period.test_end}",
            "is_ret": is_ret, "is_sharpe": is_sharpe, "is_mdd": is_m["max_drawdown"],
            "oos_ret": oos_ret, "oos_sharpe": oos_sharpe, "oos_mdd": oos_m["max_drawdown"],
            "sharpe_decay": sharpe_decay, "ret_decay": ret_decay,
        })

    # Build report
    if sr_list:
        test_rets = [r.test_return for r in sr_list]
        report = ValidationReport(
            split_results=sr_list,
            strategy_name=name,
            avg_train_return=float(np.mean([r.train_return for r in sr_list])),
            avg_test_return=float(np.mean(test_rets)),
            avg_train_sharpe=float(np.mean([r.train_sharpe for r in sr_list])),
            avg_test_sharpe=float(np.mean([r.test_sharpe for r in sr_list])),
            avg_return_decay=float(np.mean([r.return_decay for r in sr_list])),
            avg_sharpe_decay=float(np.mean([r.sharpe_decay for r in sr_list])),
            test_win_rate=sum(1 for r in test_rets if r > 0) / len(test_rets),
            decay_consistency=float(np.std([r.return_decay for r in sr_list])),
        )
    else:
        report = ValidationReport(
            split_results=[], strategy_name=name,
            avg_train_return=0, avg_test_return=0,
            avg_train_sharpe=0, avg_test_sharpe=0,
            avg_return_decay=0, avg_sharpe_decay=0,
            test_win_rate=0, decay_consistency=0,
        )

    verdict = judge_walk_forward(report)
    score = compute_wf_score(report)
    logger.info(f"\n{report.summary()}\nVerdict: {verdict}  Score: {score}/10")

    return {
        "name": name,
        "verdict": verdict,
        "score": score,
        "report": report.to_dict(),
        "detailed": detailed,
    }


def main():
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

    all_results = {}
    for strat in STRATEGIES:
        t0 = time.time()
        res = validate_one(strat)
        res["elapsed_s"] = round(time.time() - t0)
        all_results[strat["name"]] = res

    # Save
    out_path = OUTPUT_BASE / "wf_equity_results.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)

    # Summary
    print("\n" + "=" * 100)
    print("A-SHARE EQUITY WALK-FORWARD VALIDATION SUMMARY")
    print("=" * 100)
    hdr = f"{'Strategy':<28} {'Verdict':>8} {'Score':>6} {'IS_Sharpe':>10} {'OOS_Sharpe':>11} {'Decay':>7} {'WinRate':>8} {'Splits':>6}"
    print(hdr)
    print("-" * 100)
    for name, r in all_results.items():
        rpt = r["report"]
        print(
            f"{name:<28} {r['verdict']:>8} {r['score']:>5.1f} "
            f"{rpt['avg_train_sharpe']:>9.2f} {rpt['avg_test_sharpe']:>10.2f} "
            f"{rpt['avg_sharpe_decay']:>6.1%} {rpt['test_win_rate']:>7.0%} "
            f"{rpt['n_splits']:>6}"
        )
    print("=" * 100)
    print(f"Results: {out_path}")


if __name__ == "__main__":
    main()
