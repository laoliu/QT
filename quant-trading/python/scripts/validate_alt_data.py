"""替代数据完整性验证脚本。

检查项:
  1. 文件数量 vs 预期交易日数
  2. 损坏文件检测（尝试读取所有 parquet）
  3. 列完整性（必需字段是否存在）
  4. 数据质量（空值率、价格合理性）
  5. 各数据类型覆盖率汇总

用法:
    python scripts/validate_alt_data.py
    python scripts/validate_alt_data.py --type moneyflow
    python scripts/validate_alt_data.py --sample 50  # 只抽样 50 天做质量检查
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "cache" / "alternative"
START_DATE = date(2015, 1, 5)
END_DATE = date(2026, 3, 12)
CHIPS_START = date(2018, 1, 2)  # TuShare cyq_perf 从 2018 起有数据

# 各类型必需列
REQUIRED_COLS: dict[str, list[str]] = {
    "moneyflow": ["ts_code", "trade_date", "buy_sm_vol", "sell_sm_vol",
                  "buy_md_vol", "sell_md_vol", "buy_lg_vol", "sell_lg_vol",
                  "buy_elg_vol", "sell_elg_vol"],
    "margin": ["ts_code", "trade_date", "rzye", "rzmre", "rqye", "rqyl", "rqmcl"],
    "chips": ["ts_code", "trade_date", "his_low", "his_high",
              "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct",
              "cost_95pct", "weight_avg", "winner_rate"],
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--type", choices=["moneyflow", "margin", "chips", "all"],
                   default="all")
    p.add_argument("--sample", type=int, default=0,
                   help="只抽样 N 天做质量检查 (0=全量)")
    p.add_argument("--output", default="output/alt_data_validation.json")
    return p.parse_args()


def get_trading_days(start: date, end: date) -> list[date]:
    from aipha.data.calendar import CNStockCalendar
    cal = CNStockCalendar()
    return cal.get_trading_days(start, end)


def check_file_coverage(
    expected_dates: list[date],
    glob_pattern: str,
    date_extractor: callable,
) -> dict[str, Any]:
    """检查文件覆盖率：expected_dates vs 实际文件。"""
    existing = {date_extractor(p) for p in CACHE_DIR.glob(glob_pattern)}
    expected_set = {d.strftime("%Y%m%d") for d in expected_dates}
    missing = sorted(expected_set - existing)
    extra = sorted(existing - expected_set)
    return {
        "expected": len(expected_dates),
        "existing": len(existing),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "coverage_pct": round(len(existing & expected_set) / len(expected_dates) * 100, 1),
        "missing_sample": missing[:10],
        "missing_last": missing[-3:] if missing else [],
    }


def check_corrupted_files(
    glob_pattern: str, sample: int = 0,
) -> dict[str, Any]:
    """尝试读取所有（或抽样）parquet 文件，检测损坏文件。"""
    files = sorted(CACHE_DIR.glob(glob_pattern))
    if sample > 0 and len(files) > sample:
        step = max(1, len(files) // sample)
        files = files[::step][:sample]
    corrupted: list[str] = []
    empty: list[str] = []
    total_rows = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if len(df) == 0:
                empty.append(f.name)
            else:
                total_rows += len(df)
        except Exception as e:
            corrupted.append(f"{f.name}: {e}")
    return {
        "checked": len(files),
        "corrupted": len(corrupted),
        "empty": len(empty),
        "total_rows_sampled": total_rows,
        "corrupted_files": corrupted[:5],
        "empty_files": empty[:5],
    }


def check_column_integrity(
    glob_pattern: str, required_cols: list[str], sample: int = 10,
) -> dict[str, Any]:
    """检查必需列是否存在（抽样验证）。"""
    files = sorted(CACHE_DIR.glob(glob_pattern))
    if not files:
        return {"status": "no_files"}
    step = max(1, len(files) // sample)
    sampled = files[::step][:sample]
    missing_cols_count = 0
    col_issues: list[str] = []
    actual_cols: list[str] = []
    for f in sampled:
        try:
            df = pd.read_parquet(f, columns=None)
            actual_cols = list(df.columns)
            missing = [c for c in required_cols if c not in df.columns]
            if missing:
                missing_cols_count += 1
                col_issues.append(f"{f.name}: missing {missing}")
        except Exception:
            pass
    return {
        "sampled": len(sampled),
        "files_with_missing_cols": missing_cols_count,
        "issues": col_issues[:3],
        "actual_columns": actual_cols[:15],
    }


def check_data_quality(
    glob_pattern: str, key_cols: list[str], sample: int = 20,
) -> dict[str, Any]:
    """抽样检查数值合理性（空值率、极端值）。"""
    files = sorted(CACHE_DIR.glob(glob_pattern))
    if not files:
        return {"status": "no_files"}
    step = max(1, len(files) // sample)
    sampled = files[::step][:sample]
    dfs: list[pd.DataFrame] = []
    for f in sampled:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            pass
    if not dfs:
        return {"status": "all_failed"}
    combined = pd.concat(dfs, ignore_index=True)
    result: dict[str, Any] = {
        "sampled_rows": len(combined),
        "sampled_days": len(sampled),
        "unique_stocks": combined.get("ts_code", combined.get("symbol", pd.Series())).nunique() if "ts_code" in combined.columns else 0,
    }
    null_rates: dict[str, float] = {}
    for col in key_cols:
        if col in combined.columns:
            null_rates[col] = round(combined[col].isna().mean(), 4)
    result["null_rates"] = null_rates
    result["avg_null_rate"] = round(float(np.mean(list(null_rates.values()))), 4) if null_rates else 0.0
    return result


def _print_section(title: str, data: dict) -> None:
    print(f"\n  [{title}]")
    for k, v in data.items():
        if isinstance(v, list):
            print(f"    {k}: {v}")
        else:
            print(f"    {k}: {v}")


def main() -> None:
    args = parse_args()
    sample = args.sample

    print("=== 替代数据完整性验证 ===")
    all_days = get_trading_days(START_DATE, END_DATE)
    chips_days = get_trading_days(CHIPS_START, END_DATE)
    print(f"  全量交易日: {len(all_days)} 天 ({START_DATE} ~ {END_DATE})")
    print(f"  chips起始: {len(chips_days)} 天 ({CHIPS_START} ~ {END_DATE})")

    report: dict[str, Any] = {}
    types_to_check = (
        [args.type] if args.type != "all" else ["moneyflow", "margin", "chips"]
    )

    for dtype in types_to_check:
        print(f"\n{'=' * 50}")
        print(f"  数据类型: {dtype.upper()}")
        if dtype == "moneyflow":
            pattern = "moneyflow_*.parquet"
            extractor = lambda p: p.stem.replace("moneyflow_", "")
            expected = all_days
        elif dtype == "margin":
            pattern = "margin_*.parquet"
            extractor = lambda p: p.stem.replace("margin_", "")
            expected = all_days
        else:  # chips
            pattern = "chips_daily_*.parquet"
            extractor = lambda p: p.stem.replace("chips_daily_", "")
            expected = chips_days

        r: dict[str, Any] = {"type": dtype}

        # 1. 文件覆盖率
        cov = check_file_coverage(expected, pattern, extractor)
        r["coverage"] = cov
        _print_section("覆盖率", cov)

        # 2. 损坏文件检测
        n_sample = sample if sample > 0 else min(200, cov["existing"])
        corrupt = check_corrupted_files(pattern, n_sample)
        r["corruption"] = corrupt
        _print_section("损坏检测", corrupt)

        # 3. 列完整性
        req_cols = REQUIRED_COLS.get(dtype, [])
        col_check = check_column_integrity(pattern, req_cols, sample=10)
        r["columns"] = col_check
        print(f"  [列完整性] files_with_missing_cols={col_check.get('files_with_missing_cols',0)}")

        # 4. 数据质量
        q = check_data_quality(pattern, req_cols[:6], sample=min(20, n_sample))
        r["quality"] = q
        print(f"  [数据质量] stocks={q.get('unique_stocks',0)}  "
              f"rows={q.get('sampled_rows',0)}  "
              f"avg_null={q.get('avg_null_rate',0):.2%}")

        report[dtype] = r

    # 输出 JSON
    out_path = ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {out_path}")

    # 总览
    print("\n" + "=" * 50)
    print("总览:")
    for dtype, r in report.items():
        cov = r.get("coverage", {})
        corrupt = r.get("corruption", {})
        q = r.get("quality", {})
        status = "OK" if cov.get("missing_count", 1) == 0 and corrupt.get("corrupted", 1) == 0 else "WARN"
        print(f"  {status} {dtype:12s}  "
              f"覆盖={cov.get('coverage_pct', 0):.1f}%  "
              f"缺失={cov.get('missing_count', '?')}  "
              f"损坏={corrupt.get('corrupted', '?')}  "
              f"空值={q.get('avg_null_rate', 0):.1%}")


if __name__ == "__main__":
    main()
