"""analyze_strategy_decay.py — IS→OOS AR 衰减分析与策略组合优化（H7）

读取 WF 验证结果，计算每个策略的样本内/样本外 AR 衰减特征，
并分析不同策略组合在理论投资组合层面的收益。

用法:
    python scripts/analyze_strategy_decay.py              # 全量分析
    python scripts/analyze_strategy_decay.py --top 10     # 只看 Top-10 策略
    python scripts/analyze_strategy_decay.py --output results/decay_report.json
    python scripts/analyze_strategy_decay.py --verbose    # 输出详细分解
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_PATH = _ROOT / "results" / "decay_report.json"

# 硬性门槛（与 copilot-instructions.md 对齐）
HARD_AR_THRESHOLD = 0.20          # OOS AR ≥ 20%
HARD_SHARPE_THRESHOLD = 0.8       # Sharpe ≥ 0.8
MAX_SHARPE_DECAY = 0.50           # IS→OOS 夏普衰减 ≤ 50%

# 组合分析配置
PORTFOLIO_SIZES = [3, 5, 8, 10, 15, 20]   # 等权组合测试的策略数


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class StrategyDecayStats:
    """单个策略的 IS/OOS 衰减统计。"""

    strategy_name: str
    verdict: str                  # PASS / FAIL
    score: float

    # IS 指标（各折均值）
    is_ar_mean: float = 0.0       # 样本内年化收益均值
    is_sharpe_mean: float = 0.0

    # OOS 指标（各折均值）
    oos_ar_mean: float = 0.0      # 样本外年化收益均值
    oos_sharpe_mean: float = 0.0

    # 衰减指标（各折加权均值）
    ar_decay: float = 0.0         # (IS_AR - OOS_AR) / IS_AR，越低越好
    sharpe_decay: float = 0.0

    # 稳健性
    oos_positive_ratio: float = 0.0  # 样本外正收益折数比例
    n_splits: int = 0

    # 辅助字段
    config_path: str = ""
    data_range: str = ""

    @property
    def passes_hard_threshold(self) -> bool:
        """是否满足全部硬性门槛。"""
        return (
            self.oos_ar_mean >= HARD_AR_THRESHOLD
            and self.oos_sharpe_mean >= HARD_SHARPE_THRESHOLD
            and self.sharpe_decay <= MAX_SHARPE_DECAY
        )


@dataclass
class PortfolioResult:
    """等权组合的理论指标。"""

    size: int
    strategies: list[str]
    equal_weight_oos_ar: float    # 等权 OOS AR 均值（理论上界）
    min_oos_ar: float             # 最弱策略的 OOS AR（下界）
    avg_sharpe_decay: float
    all_pass_threshold: bool      # 所有成员均满足硬性门槛


@dataclass
class DecayReport:
    """完整分析报告。"""

    generated_at: str
    n_strategies_analyzed: int
    n_wf_pass: int
    n_threshold_pass: int

    strategies: list[StrategyDecayStats] = field(default_factory=list)
    best_combinations: list[PortfolioResult] = field(default_factory=list)

    # 汇总指标
    median_ar_decay: float = 0.0
    median_sharpe_decay: float = 0.0
    top5_oos_ar_mean: float = 0.0  # Top5 OOS AR 均值（组合可达上界参考）


# ── 数据加载（Step 2 填充）───────────────────────────────────────────────────

def load_wf_results() -> list[dict[str, Any]]:
    """从 SQLite 索引加载 WF 验证结果。"""
    from aipha.backtest.result_store import ResultStore

    rs = ResultStore()
    rows = rs.query_wf_results()
    logger.info("从 DB 加载 %d 条 WF 结果", len(rows))
    return rows


def _parse_splits(splits_raw: Any) -> list[dict]:
    """将 splits_json 解析为列表（兼容 str 和 list 两种存储格式）。"""
    if splits_raw is None:
        return []
    if isinstance(splits_raw, str):
        try:
            return json.loads(splits_raw)
        except json.JSONDecodeError:
            logger.warning("splits_json 解析失败，返回空列表")
            return []
    if isinstance(splits_raw, list):
        return splits_raw
    return []


# ── 衰减计算（Step 3 填充）───────────────────────────────────────────────────

def compute_decay_stats(wf_row: dict[str, Any]) -> StrategyDecayStats:
    """从一行 WF 结果计算衰减统计。"""
    splits = _parse_splits(wf_row.get("splits_json"))

    is_ars, oos_ars = [], []
    is_sharpes, oos_sharpes = [], []
    ar_decays, sharpe_decays = [], []
    oos_positive = 0

    for sp in splits:
        tr = sp.get("train_return") or 0.0
        ts = sp.get("test_return") or 0.0
        tsh = sp.get("train_sharpe") or 0.0
        osh = sp.get("test_sharpe") or 0.0

        is_ars.append(tr)
        oos_ars.append(ts)
        is_sharpes.append(tsh)
        oos_sharpes.append(osh)

        # return_decay: 预存字段优先，否则自行计算
        rd = sp.get("return_decay")
        if rd is None:
            rd = ((tr - ts) / abs(tr)) if abs(tr) > 1e-9 else 0.0
        ar_decays.append(rd)

        sd = sp.get("sharpe_decay")
        if sd is None:
            sd = ((tsh - osh) / abs(tsh)) if abs(tsh) > 1e-9 else 0.0
        sharpe_decays.append(sd)

        if ts > 0:
            oos_positive += 1

    n = len(splits)
    _mean = lambda lst: sum(lst) / len(lst) if lst else 0.0

    return StrategyDecayStats(
        strategy_name=wf_row.get("strategy_name", ""),
        verdict=wf_row.get("verdict", ""),
        score=float(wf_row.get("score") or 0.0),
        is_ar_mean=_mean(is_ars),
        is_sharpe_mean=_mean(is_sharpes),
        oos_ar_mean=_mean(oos_ars),
        oos_sharpe_mean=_mean(oos_sharpes),
        ar_decay=_mean(ar_decays),
        sharpe_decay=_mean(sharpe_decays),
        oos_positive_ratio=(oos_positive / n) if n else 0.0,
        n_splits=n,
        config_path=wf_row.get("config_path", ""),
        data_range=wf_row.get("data_range", ""),
    )


def rank_strategies(stats: list[StrategyDecayStats]) -> list[StrategyDecayStats]:
    """按综合质量排序：OOS AR 优先，衰减率次之。

    排序键（降序）: (oos_ar_mean × oos_positive_ratio) − 0.3 × sharpe_decay
    仅对 verdict=PASS 的策略排序，FAIL 追加在末尾。
    """
    pass_stats = [s for s in stats if s.verdict == "PASS"]
    fail_stats = [s for s in stats if s.verdict != "PASS"]

    def _score(s: StrategyDecayStats) -> float:
        quality = s.oos_ar_mean * s.oos_positive_ratio
        penalty = 0.3 * max(s.sharpe_decay, 0.0)
        return quality - penalty

    pass_stats.sort(key=_score, reverse=True)
    fail_stats.sort(key=lambda s: s.oos_ar_mean, reverse=True)
    return pass_stats + fail_stats


# ── 组合分析（Step 4 填充）───────────────────────────────────────────────────

def analyze_portfolio_combinations(
    stats: list[StrategyDecayStats],
    sizes: list[int] | None = None,
) -> list[PortfolioResult]:
    """对 Top-N 组合做等权 OOS AR 预测分析。

    对每个请求的 size N，从 PASS 策略中取 Top-N（按 rank_strategies 顺序），
    计算等权组合的理论 OOS AR。同时记录所有成员是否满足硬性门槛。
    """
    sizes = sizes or PORTFOLIO_SIZES
    pass_ranked = [s for s in stats if s.verdict == "PASS"]

    results: list[PortfolioResult] = []
    for size in sizes:
        if size > len(pass_ranked):
            logger.debug("组合 size=%d 超过可用策略数 %d，跳过", size, len(pass_ranked))
            continue

        selected = pass_ranked[:size]
        oos_ars = [s.oos_ar_mean for s in selected]
        avg_oos_ar = sum(oos_ars) / size
        min_oos_ar = min(oos_ars)
        avg_decay = sum(s.sharpe_decay for s in selected) / size
        all_pass = all(s.passes_hard_threshold for s in selected)

        results.append(PortfolioResult(
            size=size,
            strategies=[s.strategy_name for s in selected],
            equal_weight_oos_ar=avg_oos_ar,
            min_oos_ar=min_oos_ar,
            avg_sharpe_decay=avg_decay,
            all_pass_threshold=all_pass,
        ))

    return results


# ── 报告生成（Step 5 填充）───────────────────────────────────────────────────

def build_report(
    all_stats: list[StrategyDecayStats],
    combos: list[PortfolioResult],
) -> DecayReport:
    """汇总指标，构建最终报告对象。"""
    from datetime import datetime

    pass_stats = [s for s in all_stats if s.verdict == "PASS"]
    threshold_pass = [s for s in all_stats if s.passes_hard_threshold]

    # 中位数衰减
    def _median(lst: list[float]) -> float:
        if not lst:
            return 0.0
        s = sorted(lst)
        n = len(s)
        return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)

    ar_decays = [s.ar_decay for s in pass_stats]
    sh_decays = [s.sharpe_decay for s in pass_stats]
    top5_oos = sorted([s.oos_ar_mean for s in pass_stats], reverse=True)[:5]

    return DecayReport(
        generated_at=datetime.now().isoformat(),
        n_strategies_analyzed=len(all_stats),
        n_wf_pass=len(pass_stats),
        n_threshold_pass=len(threshold_pass),
        strategies=all_stats,
        best_combinations=combos,
        median_ar_decay=_median(ar_decays),
        median_sharpe_decay=_median(sh_decays),
        top5_oos_ar_mean=sum(top5_oos) / len(top5_oos) if top5_oos else 0.0,
    )


def save_report(report: DecayReport, output_path: Path) -> None:
    """将报告序列化为 JSON 并写入文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _to_dict(obj: Any) -> Any:
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _to_dict(v) for k, v in obj.__dict__.items()}
        if isinstance(obj, list):
            return [_to_dict(i) for i in obj]
        return obj

    data = _to_dict(report)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("报告已写入 %s", output_path)


def print_summary(report: DecayReport, verbose: bool = False) -> None:
    """向终端打印可读摘要。"""
    print("\n" + "=" * 70)
    print(f"  IS→OOS 衰减分析报告  ({report.generated_at[:19]})")
    print("=" * 70)
    print(f"  分析策略数:    {report.n_strategies_analyzed}")
    print(f"  WF PASS:       {report.n_wf_pass}")
    print(f"  满足硬性门槛:  {report.n_threshold_pass}"
          f"  (OOS AR≥20%, Sharpe≥0.8, 夏普衰减≤50%)")
    print(f"  中位 AR 衰减:  {report.median_ar_decay*100:+.1f}%")
    print(f"  中位夏普衰减:  {report.median_sharpe_decay*100:+.1f}%")
    print(f"  Top-5 OOS AR:  {report.top5_oos_ar_mean*100:.1f}% (等权理论均值)")
    print()

    # 策略排名表
    print(f"  {'策略':<28} {'OOS AR':>8} {'Sharpe↓decay':>13} {'正收益折':>8} {'门槛':>5}")
    print("  " + "-" * 65)
    for s in report.strategies[:20]:
        flag = "OK" if s.passes_hard_threshold else "  "
        verdict_mark = "P" if s.verdict == "PASS" else "F"
        print(
            f"  [{verdict_mark}] {s.strategy_name:<25} "
            f"{s.oos_ar_mean*100:>7.1f}%  "
            f"{s.sharpe_decay*100:>+10.1f}%  "
            f"{s.oos_positive_ratio*100:>6.0f}%  {flag}"
        )

    if verbose and report.best_combinations:
        print()
        print(f"  {'组合规模':>6} | {'均OOS AR':>9} | {'最弱AR':>8} | {'夏普衰减':>9} | 全门槛")
        print("  " + "-" * 55)
        for c in report.best_combinations:
            all_flag = "OK" if c.all_pass_threshold else "  "
            print(
                f"  Top-{c.size:<3d} | "
                f"{c.equal_weight_oos_ar*100:>8.1f}% | "
                f"{c.min_oos_ar*100:>7.1f}% | "
                f"{c.avg_sharpe_decay*100:>+8.1f}% | {all_flag}"
            )

    print("=" * 70 + "\n")


# ── 主入口（Step 5） ──────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回退出码（0=成功）。"""
    parser = argparse.ArgumentParser(description="IS→OOS AR 衰减分析与策略组合优化")
    parser.add_argument("--top", type=int, default=None,
                        help="只输出前 N 个策略的详情（默认全量）")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT_PATH),
                        help="报告输出路径（JSON）")
    parser.add_argument("--verbose", action="store_true",
                        help="输出组合分析详情")
    parser.add_argument("--no-save", action="store_true",
                        help="不保存 JSON 报告")
    args = parser.parse_args(argv)

    # 加载并计算
    rows = load_wf_results()
    if not rows:
        logger.error("未找到任何 WF 结果，请先运行 Walk-Forward 验证")
        return 1

    all_stats = [compute_decay_stats(r) for r in rows]
    ranked = rank_strategies(all_stats)

    if args.top:
        display_stats = ranked[:args.top]
    else:
        display_stats = ranked

    combos = analyze_portfolio_combinations(ranked)
    report = build_report(display_stats if args.top else ranked, combos)
    # 保证 report.strategies 与 ranked 顺序一致
    report.strategies = ranked

    print_summary(report, verbose=args.verbose)

    if not args.no_save:
        save_report(report, Path(args.output))

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
