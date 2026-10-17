"""check_wf_gate.py — Walk-Forward 验证 CI 闸门（D2）

在 CI 流程或上线前执行，确保足够多的策略完成了 WF 验证且通过硬性门槛。
若未通过则以非零退出码退出，阻断 CI 流水线。

门槛（可通过 CLI 或 configs/quality_gates.yml 覆盖）:
- min_wf_pass:   通过 WF 验证的策略数量下限（默认 5）
- min_oos_ar:    OOS 年化收益均值下限（默认 0.08 = 8%，保守线）
- max_sharpe_decay: IS→OOS 夏普衰减率上限（默认 0.70 = 允许最多衰减 70%）

用法:
    python scripts/check_wf_gate.py                          # 使用默认阈值
    python scripts/check_wf_gate.py --min-wf-pass 3         # 放宽数量要求
    python scripts/check_wf_gate.py --min-oos-ar 0.15       # 收紧 OOS AR 要求
    python scripts/check_wf_gate.py --strategy cb_          # 只检查 CB 策略
    python scripts/check_wf_gate.py --report-only           # 报告但不以非零退出

退出码:
    0  — 全部门槛通过
    1  — 一个或多个硬性门槛未通过
    2  — DB 无法访问或没有 WF 结果
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

# ── 默认阈值（与 copilot-instructions.md 对齐）──────────────────────────────

DEFAULT_MIN_WF_PASS = 5              # WF 通过策略数下限
DEFAULT_MIN_OOS_AR = 0.08            # OOS AR 均值下限（保守：低于生产目标，但排除零）
DEFAULT_MAX_SHARPE_DECAY = 0.70      # 夏普衰减上限（copilot-instructions: ≤50% 为硬标准）
HARD_SHARPE_DECAY_THRESHOLD = 0.50   # 项目硬性门槛


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class WfStrategyStats:
    """单个策略的 WF 聚合统计。"""

    strategy_name: str
    verdict: str            # PASS / FAIL
    n_splits: int
    oos_ar_mean: float      # OOS 年化收益均值
    oos_sharpe_mean: float  # OOS 夏普均值
    sharpe_decay: float     # IS→OOS 夏普衰减率
    oos_positive_ratio: float  # OOS 正收益折比例
    config_path: str = ""
    data_range: str = ""

    @property
    def passes_hard_threshold(self) -> bool:
        return (
            self.verdict == "PASS"
            and self.oos_ar_mean >= DEFAULT_MIN_OOS_AR
            and self.sharpe_decay <= HARD_SHARPE_DECAY_THRESHOLD
        )


@dataclass
class GateCheckResult:
    """CI 闸门检查结果。"""

    # 整体通过/失败
    passed: bool
    exit_code: int  # 0=pass, 1=fail, 2=error

    # 明细
    n_total: int = 0
    n_wf_pass: int = 0           # verdict=PASS 数量
    n_threshold_pass: int = 0    # 满足全部硬性门槛的策略数

    gate_results: list[dict[str, Any]] = field(default_factory=list)
    strategy_stats: list[WfStrategyStats] = field(default_factory=list)

    messages: list[str] = field(default_factory=list)


# ── 数据加载 ──────────────────────────────────────────────────────────────────


def load_wf_stats(strategy_filter: str | None = None) -> list[WfStrategyStats]:
    """从 SQLite 加载 WF 验证结果并聚合为策略统计。

    Args:
        strategy_filter: 策略名前缀/子串过滤（None=全部）

    Returns:
        WfStrategyStats 列表

    Raises:
        RuntimeError: DB 不可用
    """
    try:
        from aipha.backtest.result_store import ResultStore
    except ImportError as exc:
        raise RuntimeError(f"无法导入 ResultStore: {exc}") from exc

    rs = ResultStore()
    try:
        rows = rs.query_wf_results()
    except Exception as exc:
        raise RuntimeError(f"查询 wf_results 失败: {exc}") from exc
    finally:
        rs.close()

    if not rows:
        return []

    result: list[WfStrategyStats] = []
    for row in rows:
        name = row.get("strategy_name", "")
        if strategy_filter and strategy_filter.lower() not in name.lower():
            continue

        splits = _parse_splits(row.get("splits_json"))
        agg = _aggregate_splits(splits)
        n_splits = row.get("n_splits") or len(splits)

        result.append(WfStrategyStats(
            strategy_name=name,
            verdict=row.get("verdict", "FAIL") or "FAIL",
            n_splits=n_splits,
            oos_ar_mean=agg["oos_ar_mean"],
            oos_sharpe_mean=agg["oos_sharpe_mean"],
            sharpe_decay=agg["sharpe_decay"],
            oos_positive_ratio=agg["oos_positive_ratio"],
            config_path=row.get("config_path", "") or "",
            data_range=row.get("data_range", "") or "",
        ))

    logger.info("加载 %d 条 WF 结果（过滤后 %d 条）", len(rows), len(result))
    return result


def _parse_splits(splits_raw: Any) -> list[dict]:
    """将 splits_json 解析为列表（兼容 str/list 两种格式）。"""
    if splits_raw is None:
        return []
    if isinstance(splits_raw, str):
        try:
            return json.loads(splits_raw)
        except json.JSONDecodeError:
            return []
    if isinstance(splits_raw, list):
        return splits_raw
    return []


def _aggregate_splits(splits: list[dict]) -> dict[str, float]:
    """计算各折 OOS/IS 指标的均值。

    Returns:
        {'oos_ar_mean': ..., 'oos_sharpe_mean': ..., 'sharpe_decay': ..., 'oos_positive_ratio': ...}
    """
    if not splits:
        return {"oos_ar_mean": 0.0, "oos_sharpe_mean": 0.0, "sharpe_decay": 0.0, "oos_positive_ratio": 0.0}

    oos_ars, oos_sharpes, decays = [], [], []
    n_positive = 0
    for sp in splits:
        tr = float(sp.get("train_return") or 0.0)
        ts = float(sp.get("test_return") or 0.0)
        tsh = float(sp.get("train_sharpe") or 0.0)
        osh = float(sp.get("test_sharpe") or 0.0)

        oos_ars.append(ts)
        oos_sharpes.append(osh)
        if ts > 0:
            n_positive += 1

        # sharpe decay: (IS - OOS) / |IS|
        sd = sp.get("sharpe_decay")
        if sd is None:
            sd = (tsh - osh) / abs(tsh) if abs(tsh) > 1e-9 else 0.0
        decays.append(float(sd))

    n = len(splits)
    _mean = lambda lst: sum(lst) / len(lst) if lst else 0.0

    return {
        "oos_ar_mean": _mean(oos_ars),
        "oos_sharpe_mean": _mean(oos_sharpes),
        "sharpe_decay": _mean(decays),
        "oos_positive_ratio": n_positive / n,
    }


# ── 门槛检查 ──────────────────────────────────────────────────────────────────


def run_gate_checks(
    stats: list[WfStrategyStats],
    min_wf_pass: int,
    min_oos_ar: float,
    max_sharpe_decay: float,
) -> GateCheckResult:
    """执行所有 CI 门槛检查。

    Args:
        stats: 策略统计列表
        min_wf_pass: WF PASS 策略数下限
        min_oos_ar: OOS AR 均值下限
        max_sharpe_decay: 夏普衰减率上限

    Returns:
        GateCheckResult
    """
    n_total = len(stats)
    n_wf_pass = sum(1 for s in stats if s.verdict == "PASS")
    # 满足全部硬性门槛：verdict=PASS、OOS AR、Sharpe 衰减
    threshold_pass = [
        s for s in stats
        if s.verdict == "PASS"
        and s.oos_ar_mean >= min_oos_ar
        and s.sharpe_decay <= max_sharpe_decay
    ]
    n_threshold_pass = len(threshold_pass)

    gate_results: list[dict[str, Any]] = []
    messages: list[str] = []
    all_passed = True

    # Gate 1: 最少 WF PASS 策略数
    g1_pass = n_wf_pass >= min_wf_pass
    gate_results.append({
        "gate": "min_wf_pass",
        "passed": g1_pass,
        "actual": n_wf_pass,
        "threshold": min_wf_pass,
        "message": f"WF PASS 策略数: {n_wf_pass} / 需要 >= {min_wf_pass}",
    })
    if not g1_pass:
        all_passed = False
        messages.append(f"[FAIL] Gate 1: WF PASS 策略数不足 ({n_wf_pass} < {min_wf_pass})")
    else:
        messages.append(f"[PASS] Gate 1: WF PASS 策略数足够 ({n_wf_pass} >= {min_wf_pass})")

    # Gate 2: WF PASS 策略的 OOS AR 均値
    pass_stats = [s for s in stats if s.verdict == "PASS"]
    oos_ar_vals = [s.oos_ar_mean for s in pass_stats]
    oos_ar_avg = sum(oos_ar_vals) / len(oos_ar_vals) if oos_ar_vals else 0.0
    g2_pass = oos_ar_avg >= min_oos_ar
    gate_results.append({
        "gate": "min_oos_ar",
        "passed": g2_pass,
        "actual": round(oos_ar_avg, 4),
        "threshold": min_oos_ar,
        "message": f"WF PASS 策略 OOS AR 均値: {oos_ar_avg:.1%} / 需要 >= {min_oos_ar:.1%}",
    })
    if not g2_pass:
        all_passed = False
        messages.append(
            f"[FAIL] Gate 2: OOS AR 均値不足 ({oos_ar_avg:.1%} < {min_oos_ar:.1%})"
        )
    else:
        messages.append(
            f"[PASS] Gate 2: OOS AR 均値达标 ({oos_ar_avg:.1%} >= {min_oos_ar:.1%})"
        )

    # Gate 3: Sharpe 衰减率尴尾检查（不领证 FAIL 的策略）
    high_decay = [s for s in pass_stats if s.sharpe_decay > max_sharpe_decay]
    g3_pass = len(high_decay) == 0
    gate_results.append({
        "gate": "max_sharpe_decay",
        "passed": g3_pass,
        "actual": len(high_decay),
        "threshold": max_sharpe_decay,
        "message": (
            f"Sharpe 衰减过大的策略数: {len(high_decay)} (阈値 {max_sharpe_decay:.0%})"
        ),
    })
    if not g3_pass:
        all_passed = False
        names = ", ".join(s.strategy_name for s in high_decay[:5])
        messages.append(
            f"[FAIL] Gate 3: {len(high_decay)} 个策略 Sharpe 衰减 > {max_sharpe_decay:.0%}: {names}"
        )
    else:
        messages.append(
            f"[PASS] Gate 3: 所有 WF PASS 策略 Sharpe 衰减 <= {max_sharpe_decay:.0%}"
        )

    return GateCheckResult(
        passed=all_passed,
        exit_code=0 if all_passed else 1,
        n_total=n_total,
        n_wf_pass=n_wf_pass,
        n_threshold_pass=n_threshold_pass,
        gate_results=gate_results,
        strategy_stats=stats,
        messages=messages,
    )


# ── 报告 ──────────────────────────────────────────────────────────────────────


def print_gate_report(result: GateCheckResult) -> None:
    """打印检查报告到标准输出。"""
    status = "PASS" if result.passed else "FAIL"
    bar = "=" * 60
    print(bar)
    print(f"  WF CI GATE CHECK  [{status}]")
    print(bar)
    print(f"  总策略数: {result.n_total}  WF-PASS: {result.n_wf_pass}  "
          f"硬性门槛通过: {result.n_threshold_pass}")
    print()
    for msg in result.messages:
        prefix = "  [OK] " if "[PASS]" in msg else "  [!!] "
        cleaned = msg.replace("[PASS] ", "").replace("[FAIL] ", "")
        print(prefix + cleaned)
    print()

    # 策略明细
    if result.strategy_stats:
        print(f"  {'Strategy':<32s} {'Verdict':>8s} {'OOS_AR':>8s} {'OOS_Sh':>8s} {'ShDecay':>9s}")
        print("  " + "-" * 68)
        sorted_stats = sorted(
            result.strategy_stats,
            key=lambda s: (s.verdict != "PASS", -s.oos_ar_mean),
        )
        for s in sorted_stats:
            mark = "  " if s.verdict == "PASS" else " !"
            print(
                f"{mark} {s.strategy_name:<32s} {s.verdict:>8s} "
                f"{s.oos_ar_mean:>+7.1%} {s.oos_sharpe_mean:>8.2f} "
                f"{s.sharpe_decay:>8.1%}"
            )
    print(bar)
    print(f"  [{status}] WF CI Gate {'passed' if result.passed else 'FAILED'}")
    print(bar)


def save_gate_report(result: GateCheckResult, output_path: Path) -> None:
    """保存检查报告为 JSON。"""
    from datetime import datetime as _dt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {
        "generated_at": _dt.now().isoformat(timespec="seconds"),
        "passed": result.passed,
        "exit_code": result.exit_code,
        "n_total": result.n_total,
        "n_wf_pass": result.n_wf_pass,
        "n_threshold_pass": result.n_threshold_pass,
        "messages": result.messages,
        "gate_results": result.gate_results,
        "strategies": [
            {
                "strategy_name": s.strategy_name,
                "verdict": s.verdict,
                "n_splits": s.n_splits,
                "oos_ar_mean": round(s.oos_ar_mean, 6),
                "oos_sharpe_mean": round(s.oos_sharpe_mean, 4),
                "sharpe_decay": round(s.sharpe_decay, 4),
                "oos_positive_ratio": round(s.oos_positive_ratio, 4),
                "passes_hard_threshold": s.passes_hard_threshold,
                "config_path": s.config_path,
                "data_range": s.data_range,
            }
            for s in sorted(
                result.strategy_stats,
                key=lambda x: (-int(x.verdict == "PASS"), -x.oos_ar_mean),
            )
        ],
    }
    output_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n闸门报告已保存: {output_path}")


# ── CLI 入口 ──────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="check_wf_gate",
        description="Walk-Forward CI 闸门检查",
    )
    parser.add_argument(
        "--min-wf-pass", type=int, default=DEFAULT_MIN_WF_PASS,
        help=f"WF PASS 策略数下限（默认 {DEFAULT_MIN_WF_PASS}）",
    )
    parser.add_argument(
        "--min-oos-ar", type=float, default=DEFAULT_MIN_OOS_AR,
        help=f"OOS AR 均値下限（默认 {DEFAULT_MIN_OOS_AR:.0%}）",
    )
    parser.add_argument(
        "--max-sharpe-decay", type=float, default=DEFAULT_MAX_SHARPE_DECAY,
        help=f"Sharpe 衰减率上限（默认 {DEFAULT_MAX_SHARPE_DECAY:.0%}）",
    )
    parser.add_argument(
        "--strategy", default=None,
        help="策略名子串过滤（默认全部）",
    )
    parser.add_argument(
        "--output", default=None,
        help="JSON 输出路径（默认不保存）",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="只输出报告，不以非零退出码失败",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="开启 DEBUG 日志",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回退出码。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1. 加载
    try:
        stats = load_wf_stats(strategy_filter=args.strategy)
    except RuntimeError as exc:
        print(f"[ERROR] 无法加载 WF 结果: {exc}")
        return 2

    if not stats:
        print("[ERROR] WF 结果为空（DB 未初始化或尚未运行 WF 验证）")
        return 2

    # 2. 运行门槛
    result = run_gate_checks(
        stats,
        min_wf_pass=args.min_wf_pass,
        min_oos_ar=args.min_oos_ar,
        max_sharpe_decay=args.max_sharpe_decay,
    )

    # 3. 输出
    print_gate_report(result)

    # 4. 保存
    if args.output:
        save_gate_report(result, Path(args.output))

    # 5. 退出码
    if args.report_only:
        return 0
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
