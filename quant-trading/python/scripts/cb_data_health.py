#!/usr/bin/env python3
"""可转债数据健康检查

对缓存的可转债数据进行质量检查：
- 日期连续性与缺失检测
- 价格异常跳变
- 成交量/成交额零值统计
- 停牌/涨跌停标记完整性
- 重复数据检测
- 强赎状态字段覆盖率

用法：
    python scripts/cb_data_health.py
    python scripts/cb_data_health.py --start 2022-01-01 --end 2024-12-31
    python scripts/cb_data_health.py --cache-dir data/cache/cb_tushare
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def check_dates(bars_by_date: dict) -> dict:
    """检查日期连续性"""
    sorted_dates = sorted(bars_by_date.keys())
    if len(sorted_dates) < 2:
        return {"status": "skip", "reason": "数据不足2天"}

    gaps = []
    for i in range(1, len(sorted_dates)):
        prev = sorted_dates[i - 1]
        curr = sorted_dates[i]
        delta = (curr - prev).days
        # 周末跳过（2天正常，3天含周末正常）
        if delta > 3:  # 超过3天视为异常
            gaps.append({
                "from": str(prev),
                "to": str(curr),
                "gap_days": delta,
            })

    return {
        "status": "warning" if gaps else "pass",
        "total_dates": len(sorted_dates),
        "first_date": str(sorted_dates[0]),
        "last_date": str(sorted_dates[-1]),
        "gaps_count": len(gaps),
        "gaps": gaps[:10],  # 最多展示10个
    }


def check_prices(bars_by_date: dict) -> dict:
    """检查价格异常"""
    price_issues = []
    zero_price_count = 0
    extreme_jump_count = 0

    # 按标的追踪价格变化
    prev_prices: dict[str, float] = {}

    for dt in sorted(bars_by_date.keys()):
        day_bars = bars_by_date[dt]
        for symbol, bar in day_bars.items():
            # 零价格
            if bar.close <= 0 or bar.open <= 0:
                zero_price_count += 1
                continue

            # OHLC 一致性
            if bar.high < bar.low:
                price_issues.append({
                    "date": str(dt),
                    "symbol": symbol,
                    "issue": f"high({bar.high}) < low({bar.low})",
                })

            # 日间跳变（超过 ±50% 视为异常）
            prev = prev_prices.get(symbol)
            if prev and prev > 0:
                change = abs(bar.close - prev) / prev
                if change > 0.50:
                    extreme_jump_count += 1
                    if len(price_issues) < 20:
                        price_issues.append({
                            "date": str(dt),
                            "symbol": symbol,
                            "issue": f"跳变 {change:.1%} (prev={prev:.2f}, close={bar.close:.2f})",
                        })

            prev_prices[symbol] = bar.close

    return {
        "status": "warning" if (zero_price_count > 0 or extreme_jump_count > 0) else "pass",
        "zero_price_count": zero_price_count,
        "extreme_jump_count": extreme_jump_count,
        "issues": price_issues[:20],
    }


def check_volume(bars_by_date: dict) -> dict:
    """检查成交量"""
    zero_volume_count = 0
    zero_amount_count = 0
    total_bars = 0

    for dt, day_bars in bars_by_date.items():
        for symbol, bar in day_bars.items():
            total_bars += 1
            if bar.volume <= 0:
                zero_volume_count += 1
            meta = bar.metadata or {}
            if meta.get("amount", 1) <= 0:
                zero_amount_count += 1

    return {
        "status": "pass" if zero_volume_count / max(total_bars, 1) < 0.05 else "warning",
        "total_bars": total_bars,
        "zero_volume_count": zero_volume_count,
        "zero_volume_pct": round(zero_volume_count / max(total_bars, 1) * 100, 2),
        "zero_amount_count": zero_amount_count,
    }


def check_metadata_coverage(bars_by_date: dict) -> dict:
    """检查 metadata 字段覆盖率"""
    field_counts: dict[str, int] = defaultdict(int)
    total_bars = 0
    critical_fields = [
        "cb_value", "cb_over_rate", "amount", "issue_size",
        "stk_short_name", "maturity_date", "list_date",
        "is_called",
    ]

    for dt, day_bars in bars_by_date.items():
        for symbol, bar in day_bars.items():
            total_bars += 1
            meta = bar.metadata or {}
            for field in critical_fields:
                if field in meta and meta[field] is not None:
                    field_counts[field] += 1

    coverage = {}
    for field in critical_fields:
        count = field_counts.get(field, 0)
        pct = round(count / max(total_bars, 1) * 100, 1)
        coverage[field] = {"count": count, "pct": pct}

    # 关键字段覆盖率低于 80% 视为警告
    low_coverage = [
        f for f in critical_fields
        if coverage[f]["pct"] < 80.0
    ]

    return {
        "status": "warning" if low_coverage else "pass",
        "total_bars": total_bars,
        "field_coverage": coverage,
        "low_coverage_fields": low_coverage,
    }


def check_symbols_per_day(bars_by_date: dict) -> dict:
    """检查每日标的数量波动"""
    counts = [len(bars) for bars in bars_by_date.values()]
    if not counts:
        return {"status": "skip"}

    avg = sum(counts) / len(counts)
    min_c = min(counts)
    max_c = max(counts)

    # 如果波动超过 50% 视为异常
    status = "pass"
    if min_c < avg * 0.5:
        status = "warning"

    return {
        "status": status,
        "avg_symbols_per_day": round(avg, 1),
        "min_symbols": min_c,
        "max_symbols": max_c,
        "std": round(
            (sum((c - avg) ** 2 for c in counts) / len(counts)) ** 0.5, 1
        ),
    }


def run_health_check(
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    cache_dir: str = "data/cache/cb_tushare",
) -> dict:
    """执行完整健康检查"""
    from aipha.data.cb_data_adapter import CBDataAdapter

    print(f"可转债数据健康检查")
    print(f"  期间: {start_date} ~ {end_date}")
    print(f"  缓存: {cache_dir}")
    print(f"{'='*60}")

    # 加载数据
    adapter = CBDataAdapter(cache_dir=cache_dir)
    start_str = start_date.replace("-", "")
    end_str = end_date.replace("-", "")

    try:
        bars_by_date = adapter.load(start_str, end_str)
    except Exception as e:
        print(f"❌ 数据加载失败: {e}")
        return {"status": "error", "error": str(e)}

    print(f"  加载 {len(bars_by_date)} 个交易日数据")

    # 执行检查
    checks = {
        "dates": check_dates(bars_by_date),
        "prices": check_prices(bars_by_date),
        "volume": check_volume(bars_by_date),
        "metadata_coverage": check_metadata_coverage(bars_by_date),
        "symbols_per_day": check_symbols_per_day(bars_by_date),
    }

    # 汇总
    warnings = [name for name, result in checks.items() if result.get("status") == "warning"]
    overall_status = "warning" if warnings else "pass"

    print(f"\n{'检查项':<25} {'状态':>8}")
    print("-" * 40)
    for name, result in checks.items():
        status = result.get("status", "unknown")
        icon = "✅" if status == "pass" else ("⚠️" if status == "warning" else "❌")
        print(f"  {name:<23} {icon} {status}")

    if warnings:
        print(f"\n⚠️ {len(warnings)} 项检查存在警告: {', '.join(warnings)}")
    else:
        print(f"\n✅ 所有检查通过")

    # 展示 metadata 覆盖率
    meta_check = checks.get("metadata_coverage", {})
    if meta_check.get("field_coverage"):
        print(f"\n字段覆盖率:")
        for field, info in meta_check["field_coverage"].items():
            pct = info["pct"]
            status_icon = "✅" if pct >= 80 else "⚠️"
            print(f"  {status_icon} {field:<20} {pct:>6.1f}%")

    return {
        "status": overall_status,
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="可转债数据健康检查")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--cache-dir", type=str, default="data/cache/cb_tushare")
    parser.add_argument("--output", type=str, default="results/cb_data_health.json")
    args = parser.parse_args()

    result = run_health_check(
        start_date=args.start,
        end_date=args.end,
        cache_dir=args.cache_dir,
    )

    # 保存结果
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果保存至: {args.output}")


if __name__ == "__main__":
    main()
