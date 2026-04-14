#!/usr/bin/env python
"""A股权益数据层验证脚本

验证 TuShare 数据源连通性、数据完整性、以及策略所需元数据字段覆盖情况。
无需真实 Token 也可运行模拟模式验证本地缓存。

用法:
    # 验证本地缓存（不需要 Token）
    python scripts/equity_data_validation.py --mode cache --cache-dir data/cache/equity_tushare

    # 验证 TuShare API 连通性（需要 Token）
    python scripts/equity_data_validation.py --mode api

    # 完整验证（API + 缓存 + 策略对齐）
    python scripts/equity_data_validation.py --mode full
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# EquityRotationStrategy 子类所需的元数据字段
REQUIRED_METADATA_FIELDS = {
    "core": ["circ_mv", "turnover_rate", "amount", "list_date", "name"],
    "valuation": ["pe_ttm", "pb"],
    "fundamental": ["roe"],
    "classification": ["industry"],
}

# 策略 → 必需元数据映射
STRATEGY_REQUIRED_FIELDS = {
    "multifactor_enhance": ["circ_mv", "turnover_rate", "pe_ttm", "pb", "roe", "name", "list_date"],
    "smallcap_rotation": ["circ_mv", "turnover_rate", "amount", "name", "list_date"],
    "pead_event": ["circ_mv", "turnover_rate", "name", "list_date"],
    "factor_based": ["circ_mv", "turnover_rate", "name", "list_date"],
}


def check_api_connectivity() -> dict[str, Any]:
    """检查 TuShare API 连通性"""
    result = {"check": "api_connectivity", "status": "skip", "details": {}}

    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        result["status"] = "skip"
        result["details"]["reason"] = "TUSHARE_TOKEN 未设置"
        return result

    try:
        from aipha.data.tushare_gateway import get_tushare_gateway

        gw = get_tushare_gateway(token=token)

        # 尝试获取交易日历（最轻量的 API 调用）
        cal = gw.call("trade_cal", exchange="SSE", start_date="20250101", end_date="20250110")
        if cal is not None and len(cal) > 0:
            result["status"] = "pass"
            result["details"]["trade_days_sample"] = len(cal)
        else:
            result["status"] = "warning"
            result["details"]["reason"] = "trade_cal 返回为空"
    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)

    return result


def check_stock_basic_cache(cache_dir: Path) -> dict[str, Any]:
    """检查 stock_basic 缓存完整性"""
    result = {"check": "stock_basic_cache", "status": "skip", "details": {}}

    cache_file = cache_dir / "stock_basic.parquet"
    if not cache_file.exists():
        result["status"] = "warning"
        result["details"]["reason"] = f"缓存文件不存在: {cache_file}"
        return result

    try:
        import pandas as pd
        df = pd.read_parquet(cache_file)

        required_cols = ["ts_code", "name", "industry", "list_date", "list_status"]
        missing_cols = [c for c in required_cols if c not in df.columns]

        n_total = len(df)
        n_listed = len(df[df["list_status"] == "L"]) if "list_status" in df.columns else 0
        n_delisted = len(df[df["list_status"] == "D"]) if "list_status" in df.columns else 0

        result["details"] = {
            "total_stocks": n_total,
            "listed": n_listed,
            "delisted": n_delisted,
            "columns": list(df.columns),
            "missing_required_cols": missing_cols,
            "name_null_rate": float(df["name"].isna().mean()) if "name" in df.columns else None,
            "industry_null_rate": float(df["industry"].isna().mean()) if "industry" in df.columns else None,
        }

        if missing_cols:
            result["status"] = "error"
        elif n_total < 100:
            result["status"] = "warning"
            result["details"]["reason"] = f"股票数过少: {n_total}"
        else:
            result["status"] = "pass"

    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)

    return result


def check_daily_basic_cache(cache_dir: Path) -> dict[str, Any]:
    """检查 daily_basic 月度缓存完整性"""
    result = {"check": "daily_basic_cache", "status": "skip", "details": {}}

    monthly_files = sorted(cache_dir.glob("daily_basic_*.parquet"))
    if not monthly_files:
        result["status"] = "warning"
        result["details"]["reason"] = "无 daily_basic 月度缓存文件"
        return result

    try:
        import pandas as pd

        total_rows = 0
        months_covered = []
        field_coverage = {}

        for f in monthly_files:
            month = f.stem.replace("daily_basic_", "")
            months_covered.append(month)
            df = pd.read_parquet(f)
            total_rows += len(df)

            # 统计字段覆盖率（只取第一个月做样本）
            if not field_coverage:
                for col in ["circ_mv", "pe_ttm", "pb", "turnover_rate", "total_mv"]:
                    if col in df.columns:
                        non_null = df[col].notna().mean()
                        field_coverage[col] = round(float(non_null), 4)
                    else:
                        field_coverage[col] = 0.0

        result["details"] = {
            "monthly_files": len(monthly_files),
            "months": months_covered[:5] + (["..."] if len(months_covered) > 5 else []),
            "total_rows": total_rows,
            "field_coverage": field_coverage,
        }

        # 核心字段覆盖率 < 80% 视为 warning
        low_coverage = {k: v for k, v in field_coverage.items() if v < 0.8}
        if low_coverage:
            result["status"] = "warning"
            result["details"]["low_coverage_fields"] = low_coverage
        else:
            result["status"] = "pass"

    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)

    return result


def check_ohlcv_cache(cache_dir: Path) -> dict[str, Any]:
    """检查日线 OHLCV 缓存（TuShare daily 数据）"""
    result = {"check": "ohlcv_cache", "status": "skip", "details": {}}

    daily_files = sorted(cache_dir.glob("daily_*.parquet"))
    # 排除 daily_basic 文件
    daily_files = [f for f in daily_files if "daily_basic" not in f.name]

    if not daily_files:
        result["status"] = "warning"
        result["details"]["reason"] = "无日线 OHLCV 缓存文件"
        return result

    try:
        import pandas as pd
        total_rows = 0
        sample_cols = None

        for f in daily_files[:5]:  # 只采样前5个文件
            df = pd.read_parquet(f)
            total_rows += len(df)
            if sample_cols is None:
                sample_cols = list(df.columns)

        result["details"] = {
            "daily_files": len(daily_files),
            "sampled_rows": total_rows,
            "columns": sample_cols,
        }
        result["status"] = "pass"

    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)

    return result


def check_strategy_config_alignment() -> dict[str, Any]:
    """检查策略配置文件是否存在且关键字段完整"""
    result = {"check": "strategy_config_alignment", "status": "pass", "details": {}}

    config_dir = project_root / "configs" / "backtest"
    strategies = ["multifactor_enhance", "smallcap_rotation", "pead_event", "factor_based"]
    missing_configs = []
    config_issues = []

    for name in strategies:
        cfg_path = config_dir / f"{name}.yml"
        if not cfg_path.exists():
            missing_configs.append(name)
            continue

        try:
            import yaml
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)

            # 基本结构检查
            issues = []
            if not cfg:
                issues.append("配置文件为空")
            else:
                if "strategy" not in cfg:
                    issues.append("缺少 strategy 节")
                if "data" not in cfg:
                    issues.append("缺少 data 节")

                # 检查 data 节的必要字段
                data_cfg = cfg.get("data", {})
                if not data_cfg.get("source"):
                    issues.append("data.source 未设置")

                # initial_capital 可能在顶层或 backtest 节下
                has_capital = (
                    "initial_capital" in cfg
                    or "initial_capital" in cfg.get("backtest", {})
                    or "capital" in cfg
                )
                if not has_capital:
                    issues.append("initial_capital/capital 未设置")

            if issues:
                config_issues.append({"strategy": name, "issues": issues})

        except Exception as e:
            config_issues.append({"strategy": name, "issues": [f"解析错误: {e}"]})

    result["details"] = {
        "checked_strategies": strategies,
        "missing_configs": missing_configs,
        "config_issues": config_issues,
    }

    if missing_configs:
        result["status"] = "warning"
    if config_issues:
        result["status"] = "error" if any(
            len(c["issues"]) > 1 for c in config_issues
        ) else "warning"

    return result


def check_calendar_data() -> dict[str, Any]:
    """检查交易日历数据可用性"""
    result = {"check": "calendar_data", "status": "skip", "details": {}}

    try:
        from datetime import date as dt_date
        from aipha.data.calendar import CNStockCalendar
        cal = CNStockCalendar()

        # 检查 2023-2024 是否有交易日
        dates_2024 = cal.get_trading_days(dt_date(2024, 1, 1), dt_date(2024, 12, 31))
        dates_2023 = cal.get_trading_days(dt_date(2023, 1, 1), dt_date(2023, 12, 31))

        result["details"] = {
            "trade_days_2024": len(dates_2024) if dates_2024 else 0,
            "trade_days_2023": len(dates_2023) if dates_2023 else 0,
        }

        if len(dates_2024) > 200 and len(dates_2023) > 200:
            result["status"] = "pass"
        elif len(dates_2024) > 0:
            result["status"] = "warning"
        else:
            result["status"] = "error"

    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)

    return result


def run_validation(mode: str = "cache", cache_dir: str = "data/cache/equity_tushare") -> dict:
    """运行数据验证

    Args:
        mode: "cache" | "api" | "full"
        cache_dir: 缓存目录路径
    """
    cache_path = Path(cache_dir)
    results = []

    print("=" * 60)
    print("  A股权益数据层验证")
    print(f"  模式: {mode} | 缓存: {cache_dir}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1) 交易日历（始终检查）
    print("\n[1/6] 检查交易日历...")
    r = check_calendar_data()
    results.append(r)
    _print_result(r)

    # 2) API 连通性
    if mode in ("api", "full"):
        print("\n[2/6] 检查 TuShare API 连通性...")
        r = check_api_connectivity()
        results.append(r)
        _print_result(r)
    else:
        print("\n[2/6] 跳过 API 检查 (cache 模式)")

    # 3) stock_basic 缓存
    if mode in ("cache", "full"):
        print("\n[3/6] 检查 stock_basic 缓存...")
        r = check_stock_basic_cache(cache_path)
        results.append(r)
        _print_result(r)

    # 4) daily_basic 缓存
    if mode in ("cache", "full"):
        print("\n[4/6] 检查 daily_basic 缓存...")
        r = check_daily_basic_cache(cache_path)
        results.append(r)
        _print_result(r)

    # 5) OHLCV 缓存
    if mode in ("cache", "full"):
        print("\n[5/6] 检查 OHLCV 缓存...")
        r = check_ohlcv_cache(cache_path)
        results.append(r)
        _print_result(r)

    # 6) 策略配置对齐
    print("\n[6/6] 检查策略配置对齐...")
    r = check_strategy_config_alignment()
    results.append(r)
    _print_result(r)

    # 汇总
    n_pass = sum(1 for r in results if r["status"] == "pass")
    n_warn = sum(1 for r in results if r["status"] == "warning")
    n_err = sum(1 for r in results if r["status"] == "error")
    n_skip = sum(1 for r in results if r["status"] == "skip")

    print("\n" + "=" * 60)
    verdict = "PASS" if n_err == 0 else "FAIL"
    print(f"  总结: {verdict}")
    print(f"  通过={n_pass}  警告={n_warn}  错误={n_err}  跳过={n_skip}")
    print("=" * 60)

    return {
        "mode": mode,
        "cache_dir": str(cache_dir),
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": {
            "verdict": verdict,
            "pass": n_pass,
            "warning": n_warn,
            "error": n_err,
            "skip": n_skip,
        },
    }


def _print_result(r: dict):
    """格式化打印单项检查结果"""
    status_icon = {"pass": "✅", "warning": "⚠️", "error": "❌", "skip": "⏭️"}
    icon = status_icon.get(r["status"], "?")
    print(f"  {icon} {r['check']}: {r['status'].upper()}")

    details = r.get("details", {})
    for k, v in details.items():
        if k == "error":
            print(f"     错误: {v}")
        elif k == "reason":
            print(f"     原因: {v}")
        elif isinstance(v, dict) and len(str(v)) < 200:
            print(f"     {k}: {v}")
        elif isinstance(v, (int, float, str)) and len(str(v)) < 100:
            print(f"     {k}: {v}")


def main():
    parser = argparse.ArgumentParser(description="A股权益数据层验证")
    parser.add_argument(
        "--mode",
        choices=["cache", "api", "full"],
        default="cache",
        help="验证模式: cache=仅本地缓存, api=TuShare连通性, full=全部",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/cache/equity_tushare",
        help="缓存目录 (default: data/cache/equity_tushare)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出JSON文件路径 (可选)",
    )

    args = parser.parse_args()
    report = run_validation(mode=args.mode, cache_dir=args.cache_dir)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n报告已保存: {args.output}")


if __name__ == "__main__":
    main()
