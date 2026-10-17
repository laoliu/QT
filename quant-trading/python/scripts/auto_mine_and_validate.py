"""自动因子挖掘与验证管道 — 系统化模板搜索 + IC Trap Guard 5 阶段验证.

基于经济学先验模板，在算子×字段×窗口空间中系统搜索因子候选，
然后通过月频截面 IC 评估 + IC Trap Guard 5 阶段管道筛选有效因子。

用法:
    python scripts/auto_mine_and_validate.py
    python scripts/auto_mine_and_validate.py --universe csi500 --top 20
    python scripts/auto_mine_and_validate.py --universe csi2000 --fwd 10

输出:
    output/auto_mining/mining_report.json
    output/auto_mining/passed_factors.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("auto_mine")

# ── 验证门槛 ──
IC_THRESHOLD = 0.02       # D7: |IC| >= 2%
ICIR_THRESHOLD = 0.30     # D7: |ICIR| >= 0.30
HLZ_T_THRESHOLD = 2.0     # HLZ 校正后 t_adj > 2.0
SEG_CUTOFF = pd.Timestamp("2022-08-01")

# ── 数据字段映射 ──
PRICE_FIELDS = ["close", "open", "high", "low"]
VOLUME_FIELDS = ["volume", "amount"]
BASIC_FIELDS = ["turnover_rate", "pe_ttm", "pb", "total_mv"]


@dataclass
class FactorTemplate:
    """因子模板定义."""
    category: str
    name_prefix: str
    expression: str
    rationale: str


@dataclass
class MiningCandidate:
    """待验证的因子候选."""
    name: str
    expression: str
    category: str
    rationale: str


@dataclass
class ICStats:
    """IC 统计结果."""
    ic_mean: float = 0.0
    ic_std: float = 0.0
    icir: float = 0.0
    ic_pos_rate: float = 0.0
    n_months: int = 0
    t_stat: float = 0.0
    p_value: float = 1.0


@dataclass
class FactorResult:
    """单因子验证结果."""
    name: str
    expression: str
    category: str
    rationale: str
    passed: bool = False
    failure_reason: str = ""
    ic_full: ICStats = field(default_factory=ICStats)
    ic_post2022: ICStats = field(default_factory=ICStats)
    guard_verdict: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="自动因子挖掘与验证管道")
    p.add_argument("--universe", default="csi2000",
                   choices=["csi300", "csi500", "csi800", "csi1000",
                            "csi2000", "micro", "all"])
    p.add_argument("--start", default="2015-01-05")
    p.add_argument("--end", default="2026-02-28")
    p.add_argument("--fwd", type=int, default=5, help="前向收益天数")
    p.add_argument("--top", type=int, default=30,
                   help="报告排名前 N 因子")
    p.add_argument("--output", default="output/auto_mining")
    return p.parse_args()


def build_templates() -> list[FactorTemplate]:
    """构建基于经济学先验的因子模板库.

    9 大类别，每类有理论依据，避免数据挖掘偏差（原则 A）。
    """
    tpl: list[FactorTemplate] = []

    # ── 1. 短期反转 (Jegadeesh 1990, DeBondt & Thaler 1985) ──
    for w in [5, 10, 20]:
        tpl.append(FactorTemplate(
            "reversal", f"rev_{w}d",
            f"cs_rank(-($close / ts_delay($close, {w}) - 1))",
            f"过去{w}日收益反转: 过度反应修正",
        ))

    # ── 2. 中期动量 (Jegadeesh & Titman 1993) ──
    for w in [60, 120]:
        tpl.append(FactorTemplate(
            "momentum", f"mom_{w}d",
            f"cs_rank($close / ts_delay($close, {w}) - 1)",
            f"过去{w}日价格动量: 信息扩散效应",
        ))
    # 衰减加权动量
    for w in [20, 60]:
        tpl.append(FactorTemplate(
            "momentum", f"wgt_mom_{w}d",
            f"cs_rank(ts_decay_linear($close / ts_delay($close, 1) - 1, {w}))",
            f"过去{w}日衰减加权收益: 近期信息更重要",
        ))

    # ── 3. 波动率 (Ang et al 2006 低波动异象) ──
    for w in [20, 60]:
        tpl.append(FactorTemplate(
            "volatility", f"ivol_{w}d",
            f"cs_rank(-ts_std($close / ts_delay($close, 1) - 1, {w}))",
            f"过去{w}日特质波动率(取负): 低波动溢价",
        ))
    for w in [20, 60]:
        tpl.append(FactorTemplate(
            "volatility", f"range_{w}d",
            (f"cs_rank(-((ts_max($high, {w}) - ts_min($low, {w}))"
             f" / (ts_mean($close, {w}) + 1e-8)))"),
            f"过去{w}日价格振幅(取负): 低波动溢价",
        ))

    # ── 4. 流动性 (Amihud 2002, Pástor & Stambaugh 2003) ──
    for w in [5, 20, 60]:
        tpl.append(FactorTemplate(
            "liquidity", f"turnover_{w}d",
            f"cs_rank(-ts_mean($turnover_rate, {w}))",
            f"过去{w}日平均换手率(取负): 流动性溢价",
        ))
    for w in [10, 20]:
        tpl.append(FactorTemplate(
            "liquidity", f"amihud_{w}d",
            (f"cs_rank(ts_mean(safe_div(ts_abs("
             f"$close / ts_delay($close, 1) - 1), $amount), {w}))"),
            f"Amihud {w}日非流动性: 流动性补偿",
        ))

    # ── 5. 量价背离 (Gervais et al 2001) ──
    for w in [10, 20, 60]:
        tpl.append(FactorTemplate(
            "vol_price", f"pv_corr_{w}d",
            f"cs_rank(-ts_corr($close, $volume, {w}))",
            f"过去{w}日价量相关(取负): 量价背离信号",
        ))
    tpl.append(FactorTemplate(
        "vol_price", "vol_surge",
        "cs_rank(ts_delta($volume, 5) / (ts_mean($volume, 20) + 1e-8))",
        "5日成交量变化/20日均量: 放量信号",
    ))

    # ── 6. 技术形态 (Lo et al 2000) ──
    for w in [20, 60, 120]:
        tpl.append(FactorTemplate(
            "technical", f"williams_{w}d",
            (f"cs_rank(($close - ts_min($low, {w}))"
             f" / (ts_max($high, {w}) - ts_min($low, {w}) + 1e-8))"),
            f"Williams %R {w}日: 超买超卖",
        ))
    for w in [10, 20, 60]:
        tpl.append(FactorTemplate(
            "technical", f"slope_{w}d",
            f"cs_rank(ts_slope($close, {w}))",
            f"过去{w}日价格线性趋势强度",
        ))
    for w in [60, 120]:
        tpl.append(FactorTemplate(
            "technical", f"ts_pctrank_{w}d",
            f"cs_rank(ts_rank($close, {w}))",
            f"过去{w}日时序百分位排名: 相对位置",
        ))

    # ── 7. 价值 (Fama & French 1993) ──
    tpl.append(FactorTemplate(
        "value", "ep",
        "cs_rank(safe_div(1.0, ts_abs($pe_ttm) + 1e-8))",
        "EP = 1/|PE|: 盈利收益率",
    ))
    tpl.append(FactorTemplate(
        "value", "bp",
        "cs_rank(safe_div(1.0, ts_abs($pb) + 1e-8))",
        "BP = 1/|PB|: 账面市值比",
    ))

    # ── 8. 规模 (Banz 1981, Fama & French 1993) ──
    tpl.append(FactorTemplate(
        "size", "small_cap",
        "cs_rank(-$total_mv)",
        "市值取负: 小盘股溢价",
    ))

    # ── 9. 复合/高阶 ──
    tpl.append(FactorTemplate(
        "composite", "intraday_ret",
        "cs_rank(ts_mean($close / ($open + 1e-8) - 1, 5))",
        "5日平均日内收益: 盘中信息含量",
    ))
    tpl.append(FactorTemplate(
        "composite", "vol_adj_ret_20d",
        ("cs_rank(safe_div(ts_mean($close / ts_delay($close, 1) - 1, 20)"
         ", ts_std($close / ts_delay($close, 1) - 1, 20)))"),
        "20日风险调整收益: 信噪比",
    ))

    logger.info("模板库: %d 个模板, %d 个类别",
                len(tpl), len(set(t.category for t in tpl)))
    return tpl


def generate_candidates(templates: list[FactorTemplate]) -> list[MiningCandidate]:
    """从模板生成因子候选列表."""
    candidates: list[MiningCandidate] = []
    for tpl in templates:
        candidates.append(MiningCandidate(
            name=tpl.name_prefix,
            expression=tpl.expression,
            category=tpl.category,
            rationale=tpl.rationale,
        ))
    logger.info("生成 %d 个因子候选", len(candidates))
    return candidates


def load_and_prepare(
    universe: str, start: str, end: str, fwd: int,
) -> pd.DataFrame:
    """加载市场数据并计算前向收益."""
    from train_ml_factor import load_daily_data, filter_universe

    logger.info("加载数据: %s ~ %s, Universe=%s", start, end, universe)
    df = load_daily_data(start, end)
    df = filter_universe(df, universe)
    n_syms = df["symbol"].nunique()
    logger.info("数据就绪: %d 行, %d 股, %d 交易日",
                len(df), n_syms, df["datetime"].nunique())

    # 前向收益: shift(-fwd) / current - 1
    fwd_col = f"fwd_{fwd}d"
    df[fwd_col] = df.groupby("symbol")["close"].transform(
        lambda s: s.shift(-fwd) / s - 1.0
    )
    n_valid = df[fwd_col].notna().sum()
    logger.info("前向收益 %s: %d 有效行 (%.1f%%)",
                fwd_col, n_valid, n_valid / len(df) * 100)
    return df


def compute_monthly_ic(
    df: pd.DataFrame, fcol: str, fwd_col: str,
) -> list[dict]:
    """计算月频截面 Rank IC (Spearman)."""
    records: list[dict] = []
    sub = df[[fcol, fwd_col, "datetime"]].dropna()
    if sub.empty:
        return records
    sub = sub.copy()
    sub["ym"] = sub["datetime"].dt.to_period("M")
    for ym, grp in sub.groupby("ym"):
        if len(grp) < 30:
            continue
        fv = grp[fcol].values
        rv = grp[fwd_col].values
        if np.std(fv) < 1e-12 or np.std(rv) < 1e-12:
            continue
        ic, pval = sp_stats.spearmanr(fv, rv)
        if not np.isfinite(ic):
            continue
        records.append({
            "period": str(ym),
            "date": grp["datetime"].max().strftime("%Y-%m-%d"),
            "ic": float(ic),
            "pval": float(pval) if np.isfinite(pval) else 1.0,
            "n": len(grp),
        })
    return records


def calc_ic_stats(records: list[dict]) -> ICStats:
    """从月度 IC 记录计算汇总统计."""
    if not records:
        return ICStats()
    ics = [r["ic"] for r in records]
    n = len(ics)
    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics, ddof=1)) if n > 1 else 0.0
    icir = mean_ic / std_ic if std_ic > 1e-12 else 0.0
    pos_rate = sum(1 for x in ics if x > 0) / n
    t_val = mean_ic / (std_ic / np.sqrt(n)) if std_ic > 0 else 0.0
    p_val = float(
        2 * (1 - sp_stats.t.cdf(abs(t_val), df=max(n - 1, 1)))
    )
    return ICStats(
        ic_mean=round(mean_ic, 5),
        ic_std=round(std_ic, 5),
        icir=round(icir, 3),
        ic_pos_rate=round(pos_rate, 3),
        n_months=n,
        t_stat=round(t_val, 3),
        p_value=round(p_val, 4),
    )


def evaluate_factor(
    candidate: MiningCandidate, df: pd.DataFrame, fwd_col: str,
    parser: Any, guard: Any,
) -> FactorResult:
    """评估单个因子候选: 解析→计算→IC→Guard."""
    result = FactorResult(
        name=candidate.name, expression=candidate.expression,
        category=candidate.category, rationale=candidate.rationale,
    )
    # 1) 解析
    try:
        parsed = parser.parse(candidate.expression, candidate.name)
        if not parsed.is_valid:
            result.failure_reason = f"parse: {parsed.error_message}"
            return result
    except Exception as e:
        result.failure_reason = f"parse: {e}"
        return result

    # 2) 计算因子值
    try:
        fvals = parser.execute(parsed, df)
        valid_rate = fvals.notna().mean()
        if valid_rate < 0.05:
            result.failure_reason = f"因子值覆盖率过低: {valid_rate:.1%}"
            return result
    except Exception as e:
        result.failure_reason = f"compute: {e}"
        return result

    # 3) 写入 DataFrame 并计算月频 IC
    fcol = f"_f_{candidate.name}"
    df[fcol] = fvals

    all_ic = compute_monthly_ic(df, fcol, fwd_col)
    cutoff_str = SEG_CUTOFF.strftime("%Y-%m")
    post_ic = [r for r in all_ic if r["period"] >= cutoff_str]

    result.ic_full = calc_ic_stats(all_ic)
    result.ic_post2022 = calc_ic_stats(post_ic)

    # 清理临时列
    df.drop(columns=[fcol], inplace=True, errors="ignore")

    # 4) IC Trap Guard AST 去重检查
    try:
        from aipha.factors.ic_trap_guard import GuardCheckResult
        guard_res: GuardCheckResult = guard.check_factor(
            factor_name=candidate.name,
            factor_values=fvals.dropna(),
            forward_returns=df.loc[fvals.dropna().index, fwd_col],
            expression=candidate.expression,
        )
        result.guard_verdict = guard_res.verdict.value
    except Exception as e:
        result.guard_verdict = f"error: {e}"

    # 5) 综合判定: 全量期+新规后 均须达标
    full_ok = (abs(result.ic_full.ic_mean) >= IC_THRESHOLD
               and abs(result.ic_full.icir) >= ICIR_THRESHOLD)
    post_ok = (result.ic_post2022.n_months >= 6
               and abs(result.ic_post2022.ic_mean) >= IC_THRESHOLD * 0.8)
    if full_ok and post_ok:
        result.passed = True
    elif not full_ok:
        result.failure_reason = (
            f"全量期IC不达标: IC={result.ic_full.ic_mean:.4f}"
            f" ICIR={result.ic_full.icir:.3f}")
    else:
        result.failure_reason = (
            f"新规后IC衰减: IC={result.ic_post2022.ic_mean:.4f}")

    return result


def run_pipeline(args: argparse.Namespace) -> list[FactorResult]:
    """端到端管道: 模板生成→数据加载→逐因子验证."""
    from aipha.factors.factor_expression_parser import FactorExpressionParser
    from aipha.factors.ic_trap_guard import ICTrapGuard, GuardConfig

    t0 = time.time()

    # 1) 生成候选
    templates = build_templates()
    candidates = generate_candidates(templates)

    # 2) 加载数据
    df = load_and_prepare(args.universe, args.start, args.end, args.fwd)
    fwd_col = f"fwd_{args.fwd}d"

    # 3) 初始化解析器和 Guard
    parser = FactorExpressionParser()
    guard_cfg = GuardConfig(
        ic_threshold=IC_THRESHOLD,
        icir_threshold=ICIR_THRESHOLD,
        hlz_t_threshold=HLZ_T_THRESHOLD,
        total_trials=len(candidates),
    )
    guard = ICTrapGuard(config=guard_cfg)

    # 4) 逐因子评估
    results: list[FactorResult] = []
    for i, cand in enumerate(candidates, 1):
        logger.info("[%d/%d] 评估 %s (%s)...",
                    i, len(candidates), cand.name, cand.category)
        try:
            res = evaluate_factor(cand, df, fwd_col, parser, guard)
            results.append(res)
            status = "✓ PASS" if res.passed else f"✗ {res.failure_reason[:50]}"
            ic_str = f"IC={res.ic_full.ic_mean:+.4f} ICIR={res.ic_full.icir:+.3f}"
            logger.info("  %s | %s", ic_str, status)
        except Exception as e:
            logger.error("  异常: %s", traceback.format_exc()[-200:])
            results.append(FactorResult(
                name=cand.name, expression=cand.expression,
                category=cand.category, rationale=cand.rationale,
                failure_reason=f"exception: {e}",
            ))

    elapsed = time.time() - t0
    n_passed = sum(1 for r in results if r.passed)
    logger.info("=" * 60)
    logger.info("挖掘完成: %d 候选, %d 通过 (%.1f%%), 耗时 %.0fs",
                len(results), n_passed,
                n_passed / max(len(results), 1) * 100, elapsed)
    return results


def report_results(results: list[FactorResult], output_dir: Path) -> None:
    """输出验证报告到控制台和 JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 按 |ICIR| 降序排列
    ranked = sorted(results, key=lambda r: abs(r.ic_full.icir), reverse=True)

    # 控制台汇总
    logger.info("\n" + "=" * 90)
    logger.info("自动因子挖掘结果汇总")
    logger.info("=" * 90)
    header = (f"{'#':>3} {'Name':<20} {'Cat':<11} {'IC':>8} {'ICIR':>7}"
              f" {'IC+%':>6} {'PostIC':>8} {'Pass':>5}")
    logger.info(header)
    logger.info("-" * 90)
    for i, r in enumerate(ranked[:40], 1):
        p = "YES" if r.passed else "NO"
        logger.info(
            f"{i:>3} {r.name:<20} {r.category:<11}"
            f" {r.ic_full.ic_mean:>+8.4f} {r.ic_full.icir:>+7.3f}"
            f" {r.ic_full.ic_pos_rate*100:>5.1f}%"
            f" {r.ic_post2022.ic_mean:>+8.4f} {p:>5}"
        )

    # 通过的因子
    passed = [r for r in ranked if r.passed]
    logger.info("\n通过验证的因子 (%d 个):", len(passed))
    for r in passed:
        logger.info("  %-20s IC=%+.4f ICIR=%+.3f  PostIC=%+.4f  %s",
                    r.name, r.ic_full.ic_mean, r.ic_full.icir,
                    r.ic_post2022.ic_mean, r.category)

    # JSON 完整报告
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_candidates": len(results),
        "total_passed": len(passed),
        "pass_rate": round(len(passed) / max(len(results), 1), 3),
        "thresholds": {
            "ic": IC_THRESHOLD, "icir": ICIR_THRESHOLD,
            "hlz_t": HLZ_T_THRESHOLD,
        },
        "results": [],
    }
    for r in ranked:
        report["results"].append({
            "name": r.name, "expression": r.expression,
            "category": r.category, "rationale": r.rationale,
            "passed": r.passed, "failure_reason": r.failure_reason,
            "ic_full": vars(r.ic_full),
            "ic_post2022": vars(r.ic_post2022),
            "guard_verdict": r.guard_verdict,
        })
    rpt_path = output_dir / "mining_report.json"
    rpt_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    logger.info("报告已保存: %s", rpt_path)

    # 通过因子单独导出
    if passed:
        pf = [{"name": r.name, "expression": r.expression,
               "category": r.category, "ic": r.ic_full.ic_mean,
               "icir": r.ic_full.icir,
               "post_ic": r.ic_post2022.ic_mean}
              for r in passed]
        pf_path = output_dir / "passed_factors.json"
        pf_path.write_text(json.dumps(pf, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        logger.info("通过因子已导出: %s", pf_path)


def main() -> None:
    args = parse_args()
    logger.info("自动因子挖掘与验证管道启动")
    logger.info("  Universe: %s | 前向收益: %dd | 日期: %s ~ %s",
                args.universe, args.fwd, args.start, args.end)

    results = run_pipeline(args)
    output_dir = Path(args.output)
    report_results(results, output_dir)

    n_passed = sum(1 for r in results if r.passed)
    logger.info("\n管道完成: %d/%d 通过验证", n_passed, len(results))


if __name__ == "__main__":
    main()
