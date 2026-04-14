#!/usr/bin/env python3
"""A 股权益策略 Walk-Forward 验证运行器

对 multifactor_enhance / smallcap_rotation / pead_event 执行
滚动窗口样本外验证，检测策略是否存在过拟合。

验证方式：
  - 标准 Walk-Forward（训练 1 年 / 测试 3 月 / 5 折）
  - Purged Walk-Forward（PEAD 策略需要清洗财报季边界）

验收门槛：
  - IS→OOS 衰减 < 50%
  - 测试期胜率 > 50%
  - 测试期平均 Sharpe > 0

用法:
    python scripts/equity_walk_forward.py --strategy multifactor_enhance
    python scripts/equity_walk_forward.py --all
    python scripts/equity_walk_forward.py --strategy pead_event --purged
    python scripts/equity_walk_forward.py --all --synthetic

环境变量:
    TUSHARE_TOKEN: TuShare Pro API Token（真实数据必需）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aipha.backtest.config_runner import run_from_config
from aipha.backtest.validation import (
    PurgedWalkForwardSplitter,
    TimeSeriesSplitter,
    WalkForwardValidator,
    judge_walk_forward,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# 策略 Walk-Forward 配置
# ============================================================================

STRATEGY_WF_CONFIGS: dict[str, dict] = {
    "multifactor_enhance": {
        "config_path": "configs/backtest/multifactor_enhance_5y.yml",
        "train_days": 365,    # 1 年训练
        "test_days": 91,      # 3 个月测试
        "n_splits": 5,
        "gap_days": 0,
        "purge_days": 5,
        "embargo_days": 2,
    },
    "smallcap_rotation": {
        "config_path": "configs/backtest/smallcap_rotation_5y.yml",
        "train_days": 365,
        "test_days": 91,
        "n_splits": 5,
        "gap_days": 0,
        "purge_days": 5,
        "embargo_days": 2,
    },
    "pead_event": {
        "config_path": "configs/backtest/pead_event_5y.yml",
        "train_days": 365,
        "test_days": 91,
        "n_splits": 5,
        "gap_days": 0,
        "purge_days": 10,    # PEAD 需要更大 purge 窗口
        "embargo_days": 5,
    },
    "sector_momentum": {
        "config_path": "configs/backtest/sector_momentum_5y.yml",
        "train_days": 365,
        "test_days": 91,
        "n_splits": 5,
        "gap_days": 0,
        "purge_days": 5,
        "embargo_days": 2,
    },
    "value_quality": {
        "config_path": "configs/backtest/value_quality_5y.yml",
        "train_days": 365,
        "test_days": 91,
        "n_splits": 5,
        "gap_days": 0,
        "purge_days": 5,
        "embargo_days": 2,
    },
    "earnings_momentum": {
        "config_path": "configs/backtest/earnings_momentum_5y.yml",
        "train_days": 365,
        "test_days": 91,
        "n_splits": 5,
        "gap_days": 0,
        "purge_days": 10,   # 财报相关需要更大 purge 窗口
        "embargo_days": 5,
    },
}

# 验收门槛
DECAY_THRESHOLD = 0.50       # IS→OOS 衰减 < 50%
TEST_WIN_RATE_THRESHOLD = 0.50
TEST_SHARPE_THRESHOLD = 0.0


# ============================================================================
# 策略运行适配器
# ============================================================================


def make_strategy_runner(
    config_path: str,
    synthetic: bool = False,
):
    """创建策略运行函数，适配 WalkForwardValidator 接口

    返回一个 callable(start_date, end_date) -> dict[str, float]
    """

    def run_fn(start_dt: date, end_dt: date) -> dict[str, float]:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        # 设置日期范围
        cfg["run"]["start_date"] = start_dt.strftime("%Y-%m-%d")
        cfg["run"]["end_date"] = end_dt.strftime("%Y-%m-%d")

        if synthetic:
            cfg["data"]["provider"] = "synthetic"
            cfg["data"]["source"] = "synthetic"

        # 写临时配置
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, dir=".",
        ) as tmp:
            yaml.dump(cfg, tmp, allow_unicode=True)
            tmp_path = tmp.name

        try:
            result = run_from_config(
                tmp_path, output_dir=tempfile.mkdtemp()
            )

            # 提取验证所需指标
            bt_result = result.result
            initial = bt_result.initial_capital
            final = bt_result.final_nav
            total_return = (final - initial) / initial

            snapshots = bt_result.daily_snapshots
            n_days = len(snapshots)

            # Sharpe
            returns = [s.daily_return for s in snapshots]
            if len(returns) > 1:
                avg_ret = sum(returns) / len(returns)
                std_ret = (
                    sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)  # ddof=1 §13.2
                ) ** 0.5
                sharpe = (
                    (avg_ret * 252 - 0.02) / (std_ret * 252**0.5)
                    if std_ret > 0 else 0
                )
            else:
                sharpe = 0.0

            # Max drawdown
            peak = initial
            max_dd = 0.0
            for snap in snapshots:
                if snap.nav > peak:
                    peak = snap.nav
                dd = (peak - snap.nav) / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd

            return {
                "return": total_return,
                "sharpe": sharpe,
                "max_drawdown": max_dd,
                "n_days": n_days,
                "n_trades": bt_result.total_trades,
            }
        except Exception as e:
            logger.warning(f"WF split 运行失败: {e}")
            return {
                "return": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "error": str(e),
            }
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return run_fn


# ============================================================================
# 主流程
# ============================================================================


def run_walk_forward(
    strategy_name: str,
    use_purged: bool = False,
    synthetic: bool = False,
) -> dict:
    """对单个策略执行 Walk-Forward 验证"""
    wf_cfg = STRATEGY_WF_CONFIGS[strategy_name]
    config_path = wf_cfg["config_path"]

    print(f"\n{'=' * 78}")
    print(f"Walk-Forward 验证: {strategy_name}")
    print(f"{'=' * 78}")
    print(f"模式: {'Purged' if use_purged else '标准'} Walk-Forward")
    print(f"数据: {'合成' if synthetic else '真实(TuShare)'}")
    print(
        f"窗口: 训练={wf_cfg['train_days']}d "
        f"测试={wf_cfg['test_days']}d "
        f"折数={wf_cfg['n_splits']}"
    )

    # 从配置读取日期范围
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    start_str = cfg.get("run", {}).get("start_date", "2020-01-01")
    end_str = cfg.get("run", {}).get("end_date", "2025-09-05")
    start_date = date.fromisoformat(str(start_str)[:10])
    end_date = date.fromisoformat(str(end_str)[:10])

    if synthetic:
        start_date = date(2023, 1, 1)
        end_date = date(2024, 12, 31)

    print(f"期间: {start_date} ~ {end_date}")

    # 创建 splitter
    if use_purged:
        splitter = PurgedWalkForwardSplitter(
            train_period_days=wf_cfg["train_days"],
            test_period_days=wf_cfg["test_days"],
            n_splits=wf_cfg["n_splits"],
            purge_days=wf_cfg["purge_days"],
            embargo_days=wf_cfg["embargo_days"],
        )
    else:
        splitter = TimeSeriesSplitter(
            train_period_days=wf_cfg["train_days"],
            test_period_days=wf_cfg["test_days"],
            n_splits=wf_cfg["n_splits"],
            gap_days=wf_cfg["gap_days"],
        )

    validator = WalkForwardValidator(splitter=splitter)

    # 创建运行器
    run_fn = make_strategy_runner(config_path, synthetic=synthetic)

    # 执行验证
    t0 = time.time()
    report = validator.validate_with_callable(
        run_fn, start_date, end_date,
        strategy_name=strategy_name,
    )
    elapsed = time.time() - t0

    # 打印报告
    print(report.summary())
    print(f"耗时: {elapsed:.1f}s")

    # 判定
    verdict = judge_walk_forward(
        report,
        decay_threshold=DECAY_THRESHOLD,
        win_rate_threshold=TEST_WIN_RATE_THRESHOLD,
        sharpe_threshold=TEST_SHARPE_THRESHOLD,
    )
    print(f"\n判定: [{verdict}]")

    # 序列化
    result = {
        "strategy": strategy_name,
        "mode": "purged" if use_purged else "standard",
        "train_days": wf_cfg["train_days"],
        "test_days": wf_cfg["test_days"],
        "n_splits": len(report.split_results),
        "avg_train_return": round(report.avg_train_return * 100, 2),
        "avg_test_return": round(report.avg_test_return * 100, 2),
        "avg_train_sharpe": round(report.avg_train_sharpe, 3),
        "avg_test_sharpe": round(report.avg_test_sharpe, 3),
        "avg_return_decay": round(report.avg_return_decay * 100, 2),
        "avg_sharpe_decay": round(report.avg_sharpe_decay * 100, 2),
        "test_win_rate": round(report.test_win_rate * 100, 1),
        "verdict": verdict,
        "elapsed_sec": round(elapsed, 1),
        "splits": [
            {
                "split_id": r.split_id,
                "train_period": f"{r.period.train_start}~{r.period.train_end}",
                "test_period": f"{r.period.test_start}~{r.period.test_end}",
                "train_return_pct": round(r.train_return * 100, 2),
                "test_return_pct": round(r.test_return * 100, 2),
                "train_sharpe": round(r.train_sharpe, 3),
                "test_sharpe": round(r.test_sharpe, 3),
                "return_decay_pct": round(r.return_decay * 100, 2),
            }
            for r in report.split_results
        ],
    }

    return result


# judge_walk_forward 已移入 aipha.backtest.validation 模块




def main():
    parser = argparse.ArgumentParser(
        description="A 股权益策略 Walk-Forward 验证",
    )
    parser.add_argument(
        "--strategy", "-s",
        choices=list(STRATEGY_WF_CONFIGS.keys()),
        help="验证指定策略",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="验证所有策略",
    )
    parser.add_argument(
        "--purged",
        action="store_true",
        help="使用 Purged Walk-Forward",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="使用合成数据",
    )
    parser.add_argument(
        "--output", "-o",
        default="results/equity_walk_forward.json",
        help="输出文件路径",
    )

    args = parser.parse_args()

    strategies = (
        list(STRATEGY_WF_CONFIGS.keys()) if args.all
        else [args.strategy] if args.strategy
        else []
    )

    if not strategies:
        parser.print_help()
        sys.exit(1)

    results = {}
    for name in strategies:
        results[name] = run_walk_forward(
            name,
            use_purged=args.purged,
            synthetic=args.synthetic,
        )

    # 总结
    print(f"\n{'=' * 78}")
    print("Walk-Forward 验证总结")
    print(f"{'=' * 78}")
    for name in strategies:
        r = results[name]
        print(
            f"  {name:25s}  [{r['verdict']:6s}]  "
            f"IS Sharpe={r['avg_train_sharpe']:.3f}  "
            f"OOS Sharpe={r['avg_test_sharpe']:.3f}  "
            f"Decay={r['avg_sharpe_decay']:.1f}%  "
            f"WinRate={r['test_win_rate']:.0f}%"
        )

    # 保存
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {output_path}")

    all_pass = all(r["verdict"] == "PASS" for r in results.values())
    return results, all_pass


if __name__ == "__main__":
    results, all_pass = main()
    sys.exit(0 if all_pass else 1)
