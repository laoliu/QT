"""运行多策略组合回测脚本.

用法：
    python scripts/run_multi_strategy.py configs/backtest/cb_combo_3s_8y.yml
    python scripts/run_multi_strategy.py configs/backtest/multi_strategy_portfolio.yml \\
        --output output/my_combo
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aipha.backtest.multi_strategy_backtest import run_multi_strategy_backtest  # noqa: E402

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="执行多策略组合回测",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("config", help="多策略组合 YAML 配置文件路径")
    p.add_argument("--output", default=None, help="输出目录（覆盖配置中的 output_dir）")
    p.add_argument("--verbose", action="store_true", help="输出调试日志")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = args.config
    if not Path(config_path).exists():
        logger.error("配置文件不存在: %s", config_path)
        return 2

    logger.info("启动多策略组合回测: %s", config_path)
    result = run_multi_strategy_backtest(config_path, output_dir=args.output)

    perf = result.portfolio_annual_return
    print("\n=== 组合回测结果 ===")
    print(f"  年化收益:    {result.portfolio_annual_return:.2%}")
    print(f"  最大回撤:    {result.portfolio_max_drawdown:.2%}")
    print(f"  Sharpe 比率: {result.portfolio_sharpe:.3f}")
    print(f"  组合分配模式: {result.allocation_mode}")
    if result.adaptive_result is not None:
        weights = getattr(result.adaptive_result, "weights", None)
        if weights:
            print("  自适应权重:")
            for name, w in weights.items():
                print(f"    {name}: {w:.1%}")
    print(f"  子策略数量:  {len(result.sub_results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
