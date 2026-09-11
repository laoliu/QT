"""CB策略滑点敏感性分析脚本 (A3).

将 slippage_bps 从 5bps 提升到 15-25bps，评估 top3 CB 策略的冲击成本影响。

用法：
    python scripts/cb_slippage_sensitivity.py \\
        --configs configs/cb_strategies/cb_comprehensive_opt_7y.yml \\
                  configs/cb_strategies/cb_multi_alpha_10y.yml \\
                  configs/cb_strategies/cb_small_size_gated.yml \\
        --slippage 5 10 15 20 25 \\
        --output output/cb_slippage_sensitivity.json
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 确保项目根目录在 sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aipha.backtest.config_runner import run_from_config  # noqa: E402
from aipha.backtest.result_store import ResultStore  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_SLIPPAGE_BPS: list[int] = [5, 10, 15, 20, 25]
DEFAULT_STRATEGIES: list[str] = [
    "configs/cb_strategies/cb_comprehensive_opt_7y.yml",
    "configs/cb_strategies/cb_multi_alpha_10y.yml",
    "configs/cb_strategies/cb_small_size_gated.yml",
]
SLIPPAGE_YAML_KEY = "slippage_bps"  # 在 cost_model / slippage 节点下

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SlippageRunResult:
    """单次滑点水平下的回测结果摘要."""

    strategy_name: str
    config_path: str
    slippage_bps: int
    annual_return: float | None
    max_drawdown: float | None
    sharpe: float | None
    calmar: float | None
    run_id: str | None
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        """无错误且有收益数据."""
        return self.error is None and self.annual_return is not None


@dataclass
class StrategySensitivityReport:
    """单策略在多滑点水平下的汇总报告."""

    strategy_name: str
    config_path: str
    baseline_bps: int
    baseline_ar: float | None
    runs: list[SlippageRunResult] = field(default_factory=list)

    def ar_decay_pct(self, bps: int) -> float | None:
        """相对基准的年化收益衰减幅度(pp)，负数表示下降."""
        base = self.baseline_ar
        if base is None:
            return None
        run = next((r for r in self.runs if r.slippage_bps == bps and r.is_ok), None)
        if run is None or run.annual_return is None:
            return None
        return run.annual_return - base


@dataclass
class SensitivityReport:
    """全部策略 × 全部滑点水平的汇总报告."""

    strategies: list[StrategySensitivityReport] = field(default_factory=list)
    baseline_bps: int = DEFAULT_SLIPPAGE_BPS[0]
    slippage_levels: list[int] = field(default_factory=lambda: list(DEFAULT_SLIPPAGE_BPS))

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_bps": self.baseline_bps,
            "slippage_levels": self.slippage_levels,
            "strategies": [asdict(s) for s in self.strategies],
        }


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------


def _patch_slippage_bps(config: dict[str, Any], bps: int) -> dict[str, Any]:
    """将配置中的 slippage_bps 替换为指定值，返回深复制后的配置."""
    cfg = copy.deepcopy(config)

    # 支持两种 YAML 结构:
    # 1. cost_model.slippage.bps （cb_multi_alpha 等新格式）
    # 2. slippage_model.bps 或根级 slippage_bps （旧格式）
    patched = False

    for top_key in ("cost_model", "execution"):
        if top_key in cfg:
            slippage_node = cfg[top_key].get("slippage", {})
            if isinstance(slippage_node, dict):
                slippage_node["bps"] = bps
                cfg[top_key]["slippage"] = slippage_node
                patched = True
                break

    if not patched and "slippage_model" in cfg:
        cfg["slippage_model"]["bps"] = bps
        patched = True

    # 支持 cost.slippage_bps（CB策略常见格式）
    if not patched and "cost" in cfg and isinstance(cfg["cost"], dict):
        cfg["cost"][SLIPPAGE_YAML_KEY] = float(bps)
        # 修复：如果使用非 fixed 滑点模型，还需修改 slippage_params
        cost_node = cfg["cost"]
        model = cost_node.get("slippage_model", "fixed").lower().strip()
        if model not in ("fixed", "fixed_bps") and "slippage_params" in cost_node:
            params = cost_node["slippage_params"]
            if isinstance(params, dict):
                if "base_bps" in params:
                    params["base_bps"] = float(bps)
                if "fallback_bps" in params:
                    params["fallback_bps"] = float(bps)
        patched = True

    if not patched and "slippage" in cfg:
        if isinstance(cfg["slippage"], dict):
            cfg["slippage"]["bps"] = bps
            patched = True

    if not patched:
        # 降级：直接在根节点写 slippage_bps（兼容简单格式）
        cfg[SLIPPAGE_YAML_KEY] = bps
        logger.debug("降级：根节点写入 slippage_bps=%d", bps)

    return cfg


def _run_single(
    config_path: str,
    bps: int,
    tmp_yml: Path,
) -> SlippageRunResult:
    """加载配置 → 修改滑点 → 写临时 YAML → 调用 run_from_config，返回结果."""
    base_cfg_path = Path(config_path)
    strategy_name = base_cfg_path.stem

    try:
        raw = yaml.safe_load(base_cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return SlippageRunResult(
            strategy_name=strategy_name,
            config_path=config_path,
            slippage_bps=bps,
            annual_return=None,
            max_drawdown=None,
            sharpe=None,
            calmar=None,
            run_id=None,
            error=f"读取配置失败: {exc}",
        )

    patched = _patch_slippage_bps(raw, bps)

    # 在策略名后缀中注入滑点水平，使输出目录不冲突
    run_tag = f"slip{bps}bps"
    if "run" not in patched:
        patched["run"] = {}
    patched["run"]["run_tag"] = run_tag

    tmp_yml.write_text(yaml.dump(patched, allow_unicode=True), encoding="utf-8")
    logger.info("[A3] 运行 %s  slippage=%d bps", strategy_name, bps)

    try:
        run_result = run_from_config(str(tmp_yml))
    except Exception as exc:
        logger.exception("回测失败: %s  bps=%d  error=%s", strategy_name, bps, exc)
        return SlippageRunResult(
            strategy_name=strategy_name,
            config_path=config_path,
            slippage_bps=bps,
            annual_return=None,
            max_drawdown=None,
            sharpe=None,
            calmar=None,
            run_id=None,
            error=str(exc),
        )

    # 提取指标
    perf = run_result.report.performance if run_result.report else None
    dd = run_result.report.drawdown if run_result.report else None
    run_id = run_result.output_dir.name if run_result.output_dir else None

    return SlippageRunResult(
        strategy_name=strategy_name,
        config_path=config_path,
        slippage_bps=bps,
        annual_return=perf.annualized_return if perf else None,
        max_drawdown=dd.max_drawdown_pct if dd else None,
        sharpe=perf.sharpe_ratio if perf else None,
        calmar=perf.calmar_ratio if perf else None,
        run_id=run_id,
    )


def run_sensitivity(
    config_paths: list[str],
    slippage_levels: list[int],
    output_path: str | None = None,
    dry_run: bool = False,
) -> SensitivityReport:
    """对所有策略×所有滑点水平执行回测，汇总返回 SensitivityReport."""
    slippage_levels = sorted(set(slippage_levels))
    baseline_bps = slippage_levels[0]
    total_jobs = len(config_paths) * len(slippage_levels)
    logger.info(
        "CB 滑点敏感性分析：%d 策略 × %d 水平 = %d 任务",
        len(config_paths), len(slippage_levels), total_jobs,
    )

    # 临时 YAML 存放目录
    tmp_dir = Path("output") / "cb_slippage_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    report = SensitivityReport(
        baseline_bps=baseline_bps,
        slippage_levels=slippage_levels,
    )

    for cfg_path in config_paths:
        strategy_name = Path(cfg_path).stem
        strat_results: list[SlippageRunResult] = []

        for bps in slippage_levels:
            if dry_run:
                logger.info("  [dry-run] %s  slippage=%d bps", strategy_name, bps)
                strat_results.append(
                    SlippageRunResult(
                        strategy_name=strategy_name,
                        config_path=cfg_path,
                        slippage_bps=bps,
                        annual_return=None,
                        max_drawdown=None,
                        sharpe=None,
                        calmar=None,
                        run_id=None,
                        error="dry_run",
                    )
                )
                continue

            tmp_yml = tmp_dir / f"{strategy_name}_slip{bps}bps.yml"
            result = _run_single(cfg_path, bps, tmp_yml)
            strat_results.append(result)

        # 基准结果
        baseline_run = next(
            (r for r in strat_results if r.slippage_bps == baseline_bps and r.is_ok), None
        )
        strat_report = StrategySensitivityReport(
            strategy_name=strategy_name,
            config_path=cfg_path,
            baseline_bps=baseline_bps,
            baseline_ar=baseline_run.annual_return if baseline_run else None,
            runs=strat_results,
        )
        report.strategies.append(strat_report)

    return report


def print_report(report: SensitivityReport) -> None:
    """按策略打印滑点敏感性表格."""
    hdr = f"{'策略':<35} {'滑点(bps)':>10} {'年化':>10} {'MDD':>8} {'Sharpe':>8} {'Calmar':>8} {'变化(pp)':>10}"
    sep = "-" * len(hdr)
    print("\n=== CB 策略滑点敏感性分析 ===")
    for strat in report.strategies:
        print(sep)
        print(f"  {strat.strategy_name}")
        print(hdr)
        for run in strat.runs:
            decay = strat.ar_decay_pct(run.slippage_bps)
            ar_str = f"{run.annual_return:.1%}" if run.annual_return is not None else "  N/A"
            mdd_str = f"{run.max_drawdown:.1%}" if run.max_drawdown is not None else "  N/A"
            sharpe_str = f"{run.sharpe:.2f}" if run.sharpe is not None else "  N/A"
            calmar_str = f"{run.calmar:.2f}" if run.calmar is not None else "  N/A"
            decay_str = f"{decay:+.1%}" if decay is not None else "  N/A"
            if run.error and run.error != "dry_run":
                decay_str = f"ERR: {run.error[:20]}"
            # 基准行用 * 标记
            tag = "*" if run.slippage_bps == report.baseline_bps else " "
            print(
                f"{tag} {strat.strategy_name:<33} {run.slippage_bps:>10} "
                f"{ar_str:>10} {mdd_str:>8} {sharpe_str:>8} {calmar_str:>8} {decay_str:>10}"
            )
    print(sep)
    print(f"\n* = 基准滑点 ({report.baseline_bps} bps)")


def save_report(report: SensitivityReport, path: str) -> None:
    """将报告保存为 JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="CB 策略滑点敏感性分析 (A3)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--configs",
        nargs="+",
        default=DEFAULT_STRATEGIES,
        help="CB 策略配置文件路径列表",
    )
    p.add_argument(
        "--slippage",
        nargs="+",
        type=int,
        default=DEFAULT_SLIPPAGE_BPS,
        help="待测滑点水平（bps）列表",
    )
    p.add_argument(
        "--output",
        default="output/cb_slippage_sensitivity.json",
        help="结果 JSON 输出路径",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要运行的任务，不实际执行回测",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="输出调试日志",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    report = run_sensitivity(
        config_paths=args.configs,
        slippage_levels=args.slippage,
        output_path=args.output,
        dry_run=args.dry_run,
    )
    print_report(report)
    if not args.dry_run:
        save_report(report, args.output)
        logger.info("结果已保存至 %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
