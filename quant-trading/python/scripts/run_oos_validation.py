#!/usr/bin/env python3
"""样本外 (OOS) 验证框架

对所有已注册的可转债策略进行 IS/OOS 分割回测。

用法：
    # 验证所有策略
    python scripts/run_oos_validation.py

    # 验证单个策略
    python scripts/run_oos_validation.py --strategy triple_low

    # 自定义日期与分割比例
    python scripts/run_oos_validation.py --start 2020-01-01 --end 2024-12-31 --split 0.7

输出：
    results/oos_validation.json — 所有策略的 IS/OOS 指标对比
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aipha.utils.paths import project_path, resolve_path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 所有 17 个可转债策略
ALL_STRATEGIES = [
    "triple_low",
    "double_low",
    "low_price",
    "pseudo_ytm",
    "cb_momentum",
    "low_premium",
    "negative_premium",
    "small_size",
    "cb_mean_reversion",
    "multi_factor",
    "new_bond",
    "vol_breakout",
    "new_small",
    "ytm_plus",
    "cb_value_momentum",
    "turnover_surge",
    "forced_redeem_play",
]


def load_strategy_config(strategy_name: str) -> dict | None:
    """加载策略的 YAML 配置"""
    import yaml

    config_path = project_path(f"configs/cb_strategies/{strategy_name}.yml")
    if not config_path.exists():
        logger.warning(f"配置文件不存在: {config_path}")
        return None

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_backtest_for_period(
    strategy_name: str,
    start_date: str,
    end_date: str,
    config_override: dict | None = None,
) -> dict | None:
    """运行单个策略在指定日期范围的回测

    Args:
        strategy_name: 策略名称
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        config_override: 可选配置覆盖

    Returns:
        指标字典，失败返回 None
    """
    try:
        from aipha.backtest.config_runner import (
            BacktestRunConfig,
            load_config,
            parse_config,
            run_from_config,
        )

        config_path = project_path(f"configs/cb_strategies/{strategy_name}.yml")
        if not config_path.exists():
            return None

        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # 覆盖日期范围
        raw["data"]["start_date"] = start_date
        raw["data"]["end_date"] = end_date

        if config_override:
            for k, v in config_override.items():
                raw[k] = v

        # 使用临时配置文件
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tmp:
            yaml.dump(raw, tmp, default_flow_style=False, allow_unicode=True)
            tmp_path = tmp.name

        try:
            result = run_from_config(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # 计算指标
        from scripts.run_cb_strategy import compute_metrics

        return compute_metrics(result.result)

    except Exception as e:
        logger.error(f"{strategy_name} [{start_date} ~ {end_date}] 失败: {e}")
        return None


def split_date_range(
    start: str,
    end: str,
    split_ratio: float = 0.7,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """将日期范围按比例分割为 IS / OOS 两段

    Args:
        start: 开始日期
        end: 结束日期
        split_ratio: IS 占比（默认 70%）

    Returns:
        ((is_start, is_end), (oos_start, oos_end))
    """
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    total_days = (end_d - start_d).days

    is_days = int(total_days * split_ratio)
    split_d = start_d + timedelta(days=is_days)

    return (
        (start, (split_d - timedelta(days=1)).isoformat()),
        (split_d.isoformat(), end),
    )


def compute_degradation(is_metrics: dict, oos_metrics: dict) -> dict:
    """计算 IS→OOS 衰减指标"""
    degradation = {}

    for key in ["annual_return_pct", "sharpe", "calmar"]:
        is_val = is_metrics.get(key, 0)
        oos_val = oos_metrics.get(key, 0)
        if abs(is_val) > 1e-6:
            degradation[f"{key}_degradation_pct"] = round(
                (is_val - oos_val) / abs(is_val) * 100, 1
            )
        else:
            degradation[f"{key}_degradation_pct"] = 0.0

    # 过拟合指标
    is_sharpe = is_metrics.get("sharpe", 0)
    oos_sharpe = oos_metrics.get("sharpe", 0)
    degradation["oos_is_sharpe_ratio"] = (
        round(oos_sharpe / is_sharpe, 3) if abs(is_sharpe) > 0.01 else None
    )

    return degradation


def run_oos_validation(
    strategies: list[str] | None = None,
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    split_ratio: float = 0.7,
    output_path: str = "results/oos_validation.json",
) -> dict:
    """执行 OOS 验证

    Args:
        strategies: 策略列表（None=全部）
        start_date: 回测开始日期
        end_date: 回测结束日期
        split_ratio: IS 占比
        output_path: 输出文件路径

    Returns:
        验证结果字典
    """
    if strategies is None:
        strategies = ALL_STRATEGIES

    (is_start, is_end), (oos_start, oos_end) = split_date_range(
        start_date, end_date, split_ratio
    )

    print(f"OOS 验证框架")
    print(f"  IS  期间: {is_start} ~ {is_end}")
    print(f"  OOS 期间: {oos_start} ~ {oos_end}")
    print(f"  策略数量: {len(strategies)}")
    print(f"{'='*70}")

    results = {}

    for i, strategy_name in enumerate(strategies, 1):
        print(f"\n[{i}/{len(strategies)}] {strategy_name}...")

        # IS 回测
        t0 = time.time()
        is_metrics = run_backtest_for_period(strategy_name, is_start, is_end)
        is_time = time.time() - t0

        if is_metrics is None:
            print(f"  ⚠ IS 回测失败，跳过")
            results[strategy_name] = {"status": "failed", "error": "IS backtest failed"}
            continue

        # OOS 回测
        t0 = time.time()
        oos_metrics = run_backtest_for_period(strategy_name, oos_start, oos_end)
        oos_time = time.time() - t0

        if oos_metrics is None:
            print(f"  ⚠ OOS 回测失败")
            results[strategy_name] = {
                "status": "partial",
                "is_metrics": is_metrics,
                "oos_metrics": None,
            }
            continue

        # 衰减分析
        degradation = compute_degradation(is_metrics, oos_metrics)

        results[strategy_name] = {
            "status": "ok",
            "is_period": f"{is_start}~{is_end}",
            "oos_period": f"{oos_start}~{oos_end}",
            "is_metrics": is_metrics,
            "oos_metrics": oos_metrics,
            "degradation": degradation,
            "is_time_sec": round(is_time, 1),
            "oos_time_sec": round(oos_time, 1),
        }

        # 打印对比
        oos_over_is = degradation.get("oos_is_sharpe_ratio")
        ratio_str = f"{oos_over_is:.2f}" if oos_over_is is not None else "N/A"
        print(
            f"  IS: 年化{is_metrics['annual_return_pct']:+.1f}% "
            f"Sharpe={is_metrics['sharpe']:.3f} "
            f"MDD={is_metrics['max_drawdown_pct']:.1f}%"
        )
        print(
            f"  OOS: 年化{oos_metrics['annual_return_pct']:+.1f}% "
            f"Sharpe={oos_metrics['sharpe']:.3f} "
            f"MDD={oos_metrics['max_drawdown_pct']:.1f}%"
        )
        print(f"  OOS/IS Sharpe 比: {ratio_str}")

    # 保存结果
    output = resolve_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*70}")
    print(f"OOS 验证完成，结果保存至: {output_path}")

    # 汇总
    ok_results = {k: v for k, v in results.items() if v.get("status") == "ok"}
    if ok_results:
        print(f"\n{'策略':<20} {'IS_年化':>8} {'OOS_年化':>8} {'IS_Sharpe':>10} {'OOS_Sharpe':>10} {'OOS/IS':>8}")
        print("-" * 74)
        for name, r in sorted(
            ok_results.items(),
            key=lambda x: x[1]["oos_metrics"]["sharpe"],
            reverse=True,
        ):
            is_m = r["is_metrics"]
            oos_m = r["oos_metrics"]
            ratio = r["degradation"].get("oos_is_sharpe_ratio") or 0
            print(
                f"{name:<20} {is_m['annual_return_pct']:>7.1f}% "
                f"{oos_m['annual_return_pct']:>7.1f}% "
                f"{is_m['sharpe']:>10.3f} {oos_m['sharpe']:>10.3f} "
                f"{ratio:>7.2f}"
            )

    return results


def main():
    parser = argparse.ArgumentParser(description="可转债策略 OOS 验证")
    parser.add_argument("--strategy", type=str, help="验证单个策略")
    parser.add_argument("--start", type=str, default="2022-01-01", help="开始日期")
    parser.add_argument("--end", type=str, default="2024-12-31", help="结束日期")
    parser.add_argument("--split", type=float, default=0.7, help="IS 占比")
    parser.add_argument(
        "--output",
        type=str,
        default="results/oos_validation.json",
        help="输出路径",
    )
    args = parser.parse_args()

    strategies = [args.strategy] if args.strategy else None

    run_oos_validation(
        strategies=strategies,
        start_date=args.start,
        end_date=args.end,
        split_ratio=args.split,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
