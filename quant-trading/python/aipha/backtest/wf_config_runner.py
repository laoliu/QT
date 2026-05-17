"""Walk-Forward validation config runner — YAML-driven WF entry point.

Reads a backtest YAML config, runs Walk-Forward validation using
``WalkForwardSplit`` from ``aipha.validation.walk_forward``, and persists
results to ``result_store``.

Usage:
    from aipha.backtest.wf_config_runner import run_wf_from_config
    report = run_wf_from_config('configs/cb_strategies/double_low_8y.yml')
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> "datetime.datetime":
    """Parse date string in either YYYYMMDD or YYYY-MM-DD format."""
    import datetime
    s = s.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def run_wf_from_config(
    yaml_path: str | Path,
    n_splits: int = 5,
    mode: str = "expanding",
    purge_gap: int = 5,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run Walk-Forward validation driven by a YAML backtest config.

    Steps:
        1. Parse YAML config to get strategy/data parameters
        2. Generate WF fold splits
        3. Run backtest on each fold (train → test)
        4. Aggregate results and compute WF metrics
        5. Save to output + result_store upsert
    """
    yaml_path = Path(yaml_path)
    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    strategy_name = cfg.get("strategy", {}).get("name", yaml_path.stem)

    from aipha.validation.walk_forward import (
        WalkForwardSplit,
        WalkForwardReport,
        FoldResult,
        CombinatorialPBO,
    )
    from aipha.backtest.config_runner import run_from_config

    # Parse date range
    data_cfg = cfg.get("data", {})
    start_date = data_cfg.get("start_date", "20180102")
    end_date = data_cfg.get("end_date", "20260331")

    # Create splits
    splitter = WalkForwardSplit(
        n_splits=n_splits,
        mode=mode,
        purge_gap=purge_gap,
    )

    # Estimate total trading days
    from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
    import datetime
    d0 = _parse_date(start_date)
    d1 = _parse_date(end_date)
    years = (d1 - d0).days / 365.25
    total_size = int(years * TRADING_DAYS_PER_YEAR)
    folds = splitter.split(total_size)

    logger.info("WF validation: %d folds, mode=%s, total_size=%d",
                len(folds), mode, total_size)

    fold_results: list[FoldResult] = []
    perf_matrix: list[list[float]] = []

    for fold in folds:
        logger.info("  Fold %d: train=%d-%d test=%d-%d",
                     fold.fold_id, fold.train_start, fold.train_end,
                     fold.test_start, fold.test_end)

        # Convert indices to dates (approximate)
        train_start = _index_to_date(fold.train_start, d0)
        train_end = _index_to_date(fold.train_end, d0)
        test_start = _index_to_date(fold.test_start, d0)
        test_end = _index_to_date(fold.test_end, d0)

        # Run IS backtest
        is_result = _run_fold(
            yaml_path, train_start, train_end,
            f"wf_is_fold{fold.fold_id}"
        )
        # Run OOS backtest
        oos_result = _run_fold(
            yaml_path, test_start, test_end,
            f"wf_oos_fold{fold.fold_id}"
        )

        is_sr = _extract_sharpe(is_result)
        oos_sr = _extract_sharpe(oos_result)

        fr = FoldResult(
            fold_id=fold.fold_id,
            train_metric=is_sr,
            test_metric=oos_sr,
            overfit_ratio=1 - (oos_sr / is_sr) if is_sr > 0.01 else 0.0,
        )
        fold_results.append(fr)
        perf_matrix.append([is_sr, oos_sr])

    # Aggregate
    avg_train = sum(f.train_metric for f in fold_results) / max(len(fold_results), 1)
    avg_test = sum(f.test_metric for f in fold_results) / max(len(fold_results), 1)
    degradation = 1 - (avg_test / avg_train) if avg_train > 0.01 else 0.0

    # PBO
    pbo = CombinatorialPBO()
    try:
        import numpy as np
        pbo_value = pbo.estimate(np.array(perf_matrix))
    except Exception:
        pbo_value = None

    avg_overfit = sum(f.overfit_ratio for f in fold_results) / max(len(fold_results), 1)

    report = WalkForwardReport(
        n_folds=n_splits,
        fold_results=fold_results,
        avg_train_metric=avg_train,
        avg_test_metric=avg_test,
        avg_overfit_ratio=avg_overfit,
        degradation=degradation,
        pbo_estimate=pbo_value if pbo_value is not None else 0.0,
        is_overfit=(pbo_value or 0) > 0.5 or degradation > 0.5,
    )

    # Save
    out = Path(output_dir) if output_dir else Path("output") / f"wf_{strategy_name}"
    out.mkdir(parents=True, exist_ok=True)
    report_dict = asdict(report)
    (out / "wf_report.json").write_text(
        json.dumps(report_dict, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    # Upsert to result store
    _upsert_wf(strategy_name, report_dict, out)

    return report_dict


def _index_to_date(idx: int, base: Any) -> str:
    """Convert fold index to approximate date string."""
    import datetime
    from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
    days = int(idx * 365.25 / TRADING_DAYS_PER_YEAR)
    d = base + datetime.timedelta(days=days)
    return d.strftime("%Y%m%d")


def _run_fold(yaml_path: Path, start: str, end: str, tag: str) -> dict:
    """Run backtest for a single fold period."""
    from aipha.backtest.config_runner import run_from_config
    try:
        result = run_from_config(
            str(yaml_path),
            param_overrides={
                "data.start_date": start,
                "data.end_date": end,
            },
            output_dir=f"output/_wf_tmp/{tag}",
        )
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
    except Exception as e:
        logger.warning("Fold %s failed: %s", tag, e)
    return {}


def _extract_sharpe(result: dict) -> float:
    """Extract Sharpe ratio from backtest result dict."""
    m = result.get("metrics", result)
    return float(m.get("sharpe_ratio", 0))


def _upsert_wf(name: str, report: dict, out: Path) -> None:
    """Persist WF result to result store."""
    try:
        from aipha.backtest.result_store import ResultStore
        store = ResultStore()
        store.upsert_wf_result(
            strategy_name=name,
            n_splits=report.get("n_splits", 5),
            avg_is_sharpe=report.get("avg_train_metric", 0),
            avg_oos_sharpe=report.get("avg_test_metric", 0),
            degradation=report.get("degradation", 0),
            pbo=report.get("pbo_estimate"),
            output_dir=str(out),
        )
    except Exception as e:
        logger.warning("Failed to upsert WF result: %s", e)
