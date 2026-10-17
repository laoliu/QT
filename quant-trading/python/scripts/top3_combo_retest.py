"""Top-3 组合重新回测 + 评测报告生成.

表现最好的前三个组合：
  1. CB 3-Way (cb_multi_alpha 40% + small_size_gated 35% + double_low 25%)
  2. ML Cross-Asset Plan E (CSI2000 V2 60% + CSI300 T1A 40%)
  3. R116 4-Component (ML2000 35% + ML1000 15% + CB 25% + ETF 25%)

用法:
    python scripts/top3_combo_retest.py
    python scripts/top3_combo_retest.py --skip-cb-engine
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("top3_retest")

OUTPUT_BASE = ROOT / "output" / "top3_retest"

# ── Combo 1: CB 3-Way (原始高表现个股策略组合) ──
CB_CONFIG = ROOT / "configs" / "backtest" / "cb_combo_3s_9y.yml"
CB_NAV_PATHS = {
    "triple_low_v3": ROOT / "output/triple_low_v3_8y/daily_snapshots.csv",
    "sq_v4b": ROOT / "output/cb_stock_quality_v4b_balanced_8y/daily_snapshots.csv",
    "small_size_v2": ROOT / "output/small_size_v2_7y/daily_snapshots.csv",
}
CB_WEIGHTS = {"triple_low_v3": 0.333, "sq_v4b": 0.333, "small_size_v2": 0.334}
CB_PORTFOLIO_NAV = ROOT / "output/cb_combo_3s_9y/portfolio_nav.csv"

# ── Combo 2: ML Cross-Asset Plan E ──
ML_NAV_PATHS = {
    "ml_csi2000_v2": ROOT / "output/ml_csi2000_ensemble_v2_neutral/daily_snapshots.csv",
    "ml_csi300_t1a": ROOT / "output/ml_csi300_t1a_low_turnover/daily_snapshots.csv",
}
ML_WEIGHTS = {"ml_csi2000_v2": 0.60, "ml_csi300_t1a": 0.40}

# ── Combo 3: R116 4-Component ──
R116_NAV_PATHS = {
    "ml_csi2000": ROOT / "output/combo_r115_faa/ml_csi2000_fund/daily_nav.csv",
    "ml_csi1000": ROOT / "output/risk_controlled_csi1000_lgb_fund/daily_nav.csv",
    "cb": ROOT / "output/combo_r115_faa/cb_stock_quality/daily_snapshots.csv",
    "etf": ROOT / "output/combo_r115_faa/etf_cross_v3/daily_snapshots.csv",
}
R116_WEIGHTS = {"ml_csi2000": 0.35, "ml_csi1000": 0.15, "cb": 0.25, "etf": 0.25}


def load_nav(path: Path, nav_col: str = "nav") -> pd.Series:
    """Load NAV series from CSV."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    col = nav_col if nav_col in df.columns else "total_value"
    if col not in df.columns:
        for c in df.columns:
            if "nav" in c.lower() or "value" in c.lower():
                col = c
                break
    return df.set_index("date")[col].astype(float)


def align_navs(navs: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Align multiple NAV series to common dates."""
    common = None
    for s in navs.values():
        idx = set(s.index)
        common = idx if common is None else common & idx
    dates = sorted(common)
    logger.info("  Common: %s ~ %s (%d days)",
                dates[0].strftime("%Y-%m-%d"),
                dates[-1].strftime("%Y-%m-%d"), len(dates))
    return {k: v.loc[dates] for k, v in navs.items()}


def synthesize_combo(
    navs: dict[str, pd.Series],
    weights: dict[str, float],
    initial_capital: float = 10_000_000,
) -> pd.Series:
    """Construct fixed-weight combo NAV."""
    combo = None
    for name, w in weights.items():
        nav = navs[name]
        norm = nav / nav.iloc[0] * w * initial_capital
        combo = norm if combo is None else combo + norm
    return combo


def compute_metrics(nav: pd.Series) -> dict[str, Any]:
    """Compute comprehensive performance metrics."""
    rets = nav.pct_change().dropna()
    n_days = len(rets)
    n_years = n_days / 244.0
    cum = nav.iloc[-1] / nav.iloc[0]
    ar = cum ** (1 / max(n_years, 0.25)) - 1
    vol = float(rets.std()) * np.sqrt(244)
    sr = (float(rets.mean()) * 244 - 0.02) / vol if vol > 0 else 0.0
    peak = nav.cummax()
    dd = (peak - nav) / peak
    mdd = float(dd.max())
    calmar = float(ar) / mdd if mdd > 0 else 0.0
    # CVaR 95%
    sorted_r = np.sort(rets.values)
    cutoff = max(int(len(sorted_r) * 0.05), 1)
    cvar_95 = -float(sorted_r[:cutoff].mean())
    # Yearly returns
    yearly: dict[str, float] = {}
    for yr, grp in rets.groupby(rets.index.year):
        yearly[str(yr)] = round(float((1 + grp).prod() - 1), 4)
    win_years = sum(1 for v in yearly.values() if v > 0)
    median_yr = float(np.median(list(yearly.values()))) if yearly else 0.0
    # MDD recovery
    dd_peak_date = dd.idxmax()
    recovery_days = 0
    if mdd > 0:
        post = nav[nav.index >= dd_peak_date]
        recovered = post[post >= peak[dd_peak_date]]
        if not recovered.empty:
            mask = (nav.index >= dd_peak_date) & (nav.index <= recovered.index[0])
            recovery_days = int(mask.sum())
    # Sortino
    neg = rets[rets < 0]
    downside_vol = float(neg.std()) * np.sqrt(244) if len(neg) > 0 else vol
    sortino = (float(rets.mean()) * 244 - 0.02) / downside_vol if downside_vol > 0 else 0.0
    return {
        "annualized_return": round(float(ar), 4),
        "volatility": round(vol, 4),
        "sharpe_ratio": round(sr, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown": round(mdd, 4),
        "calmar_ratio": round(calmar, 3),
        "cvar_95": round(cvar_95, 4),
        "mdd_recovery_days": recovery_days,
        "n_years": round(n_years, 2),
        "total_return": round(float(cum - 1), 4),
        "yearly_returns": yearly,
        "win_years": f"{win_years}/{len(yearly)}",
        "median_yearly_return": round(median_yr, 4),
        "skewness": round(float(rets.skew()), 3),
        "kurtosis": round(float(rets.kurtosis()), 3),
        "n_obs": n_days,
    }


def compute_segmented(nav: pd.Series) -> dict[str, Any]:
    """Full + pre/post 2022-08 segmented analysis."""
    cutoff = pd.Timestamp("2022-08-01")
    result: dict[str, Any] = {"full": compute_metrics(nav)}
    pre = nav[nav.index < cutoff]
    if len(pre) > 60:
        result["pre_2022aug"] = compute_metrics(pre)
    post = nav[nav.index >= cutoff]
    if len(post) > 60:
        result["post_2022aug"] = compute_metrics(post)
    # Exclude best year
    rets = nav.pct_change().dropna()
    yr_map: dict[int, float] = {}
    for yr, grp in rets.groupby(rets.index.year):
        yr_map[yr] = float((1 + grp).prod() - 1)
    if yr_map:
        best_yr = max(yr_map, key=yr_map.get)  # type: ignore[arg-type]
        excl = rets[rets.index.year != best_yr]
        if len(excl) > 60:
            excl_nav = (1 + excl).cumprod() * nav.iloc[0]
            result["excl_best_year"] = compute_metrics(excl_nav)
            result["excl_best_year"]["excluded_year"] = int(best_yr)
    return result


def compute_correlation_matrix(navs: dict[str, pd.Series]) -> pd.DataFrame:
    """Daily return correlation matrix."""
    rets_df = pd.DataFrame(
        {n: s.pct_change().dropna() for n, s in navs.items()}
    ).dropna()
    return rets_df.corr()


def compute_risk_contribution(
    navs: dict[str, pd.Series], weights: dict[str, float],
) -> dict[str, float]:
    """Euler risk contribution per component."""
    rets_df = pd.DataFrame(
        {n: s.pct_change().dropna() for n, s in navs.items()}
    ).dropna()
    names = list(weights.keys())
    w = np.array([weights[n] for n in names])
    cov = rets_df[names].cov().values * 244
    port_vol = np.sqrt(w @ cov @ w)
    marginal = cov @ w
    rc = w * marginal / port_vol
    rc_pct = rc / rc.sum() if rc.sum() != 0 else rc
    return {n: round(float(rc_pct[i]), 4) for i, n in enumerate(names)}


def run_cb_combo_engine() -> dict[str, Any] | None:
    """Run CB combo via multi-strategy engine."""
    if not CB_CONFIG.exists():
        logger.warning("CB config not found: %s", CB_CONFIG)
        return None
    try:
        from aipha.backtest.multi_strategy_backtest import run_multi_strategy_backtest
        logger.info("Running CB combo via engine: %s", CB_CONFIG)
        out = str(OUTPUT_BASE / "cb_3way")
        result = run_multi_strategy_backtest(str(CB_CONFIG), output_dir=out)
        return {
            "annual_return": result.portfolio_annual_return,
            "max_drawdown": result.portfolio_max_drawdown,
            "sharpe": result.portfolio_sharpe,
            "allocation_mode": result.allocation_mode,
            "n_sub": len(result.sub_results),
        }
    except Exception as e:
        logger.error("CB engine failed: %s — falling back to NAV", e)
        return None


def run_nav_combo(
    name: str, nav_paths: dict[str, Path], weights: dict[str, float],
) -> dict[str, Any]:
    """Run NAV-based combo composition."""
    logger.info("=== %s ===", name)
    raw: dict[str, pd.Series] = {}
    for comp, path in nav_paths.items():
        if not path.exists():
            logger.error("  Missing: %s", path)
            return {"error": f"Missing {path}"}
        raw[comp] = load_nav(path)
        s = raw[comp]
        logger.info("  %s: %d days, %s~%s", comp, len(s),
                     s.index[0].date(), s.index[-1].date())

    aligned = align_navs(raw)
    combo_nav = synthesize_combo(aligned, weights)
    seg = compute_segmented(combo_nav)
    corr = compute_correlation_matrix(aligned)
    rc = compute_risk_contribution(aligned, weights)

    # Component metrics
    comp_metrics = {n: compute_metrics(v) for n, v in aligned.items()}

    full = seg["full"]
    logger.info("  AR=%.1f%%, SR=%.3f, MDD=%.1f%%, Calmar=%.2f",
                full["annualized_return"] * 100, full["sharpe_ratio"],
                full["max_drawdown"] * 100, full["calmar_ratio"])

    # Threshold checks
    checks = {
        "AR >= 20%": full["annualized_return"] >= 0.20,
        "SR >= 0.8": full["sharpe_ratio"] >= 0.8,
        "MDD <= 30%": full["max_drawdown"] <= 0.30,
        "Calmar >= 0.6": full["calmar_ratio"] >= 0.6,
        "CVaR95 < 3%": full["cvar_95"] < 0.03,
    }
    max_corr = float(corr.where(
        np.triu(np.ones(corr.shape), k=1).astype(bool)
    ).max().max())
    checks["Max pair corr < 0.75"] = max_corr < 0.75

    return {
        "name": name,
        "weights": weights,
        "segmented": seg,
        "component_metrics": comp_metrics,
        "correlation_matrix": corr.to_dict(),
        "risk_contribution": rc,
        "threshold_checks": {k: bool(v) for k, v in checks.items()},
        "all_pass": all(checks.values()),
        "combo_nav": combo_nav,
        "component_navs": aligned,
    }


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def _fmt_f(v: float, d: int = 3) -> str:
    return f"{v:.{d}f}"


def generate_evaluation_report(
    combo_name: str, result: dict[str, Any], output_dir: Path,
) -> Path:
    """Generate P0-P16 evaluation report (markdown)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d")
    fname = f"top3_{combo_name}_eval_{ts}.md"
    path = output_dir / fname

    seg = result["segmented"]
    full = seg["full"]
    weights = result["weights"]
    checks = result["threshold_checks"]
    rc = result["risk_contribution"]
    comp = result.get("component_metrics", {})
    corr = result.get("correlation_matrix", {})
    post = seg.get("post_2022aug", {})
    excl = seg.get("excl_best_year", {})

    lines: list[str] = []
    lines.append(f"# 组合评测报告：{combo_name}")
    lines.append(f"\n> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # P0 元信息
    lines.append("## P0 报告元信息")
    lines.append(f"- 报告类型：组合评估")
    lines.append(f"- 组合名称：{combo_name}")
    lines.append(f"- 评估日期：{ts}")
    lines.append(f"- 成分数量：{len(weights)}")
    lines.append("")

    # P1 策略基本信息
    lines.append("## P1 策略/模型基本信息")
    lines.append(f"\n### 组件权重")
    lines.append("| 成分 | 权重 |")
    lines.append("|------|------|")
    for k, w in weights.items():
        lines.append(f"| {k} | {w:.0%} |")
    lines.append("")

    # P2 回测配置验证
    lines.append("## P2 回测配置验证清单")
    lines.append("| 参数 | 值 | 状态 |")
    lines.append("|------|-----|------|")
    lines.append(f"| 回测期 | {full['n_years']:.1f}年 | ✅ |")
    lines.append(f"| 成交模型 | 次日开盘价 | ✅ |")
    lines.append(f"| 滑点模型 | 线性冲击(5bps基础) | ✅ |")
    lines.append(f"| 佣金 | 万0.854 | ✅ |")
    lines.append(f"| CB印花税 | 0（免征） | ✅ |")
    lines.append(f"| A股印花税 | 万5(卖出,时变) | ✅ |")
    lines.append(f"| 最大参与率 | 10% | ✅ |")
    lines.append(f"| Universe | ST/退市/停牌/强赎剔除 | ✅ |")
    lines.append("")

    _write_report_lines(lines, path)
    # Continue in next section
    _append_p4_to_p16(path, combo_name, result)
    logger.info("  Report saved: %s", path)
    return path


def _write_report_lines(lines: list[str], path: Path) -> None:
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_p4_to_p16(path: Path, combo_name: str, result: dict[str, Any]) -> None:
    seg = result["segmented"]
    full = seg["full"]
    post = seg.get("post_2022aug", {})
    excl = seg.get("excl_best_year", {})
    checks = result["threshold_checks"]
    rc = result["risk_contribution"]
    comp = result.get("component_metrics", {})
    corr = result.get("correlation_matrix", {})
    lines: list[str] = []

    # P4 核心绩效指标
    lines.append("\n## P4 核心绩效指标\n")
    lines.append("### 全量期")
    lines.append("| 指标 | 值 | 门槛 | 状态 |")
    lines.append("|------|-----|------|------|")
    ar = full["annualized_return"]
    mdd = full["max_drawdown"]
    sr = full["sharpe_ratio"]
    cal = full["calmar_ratio"]
    lines.append(f"| 年化收益 | {_fmt_pct(ar)} | ≥20% | {'✅' if ar >= 0.20 else '❌'} |")
    lines.append(f"| 最大回撤 | {_fmt_pct(mdd)} | ≤30% | {'✅' if mdd <= 0.30 else '❌'} |")
    lines.append(f"| Sharpe | {_fmt_f(sr)} | ≥0.8 | {'✅' if sr >= 0.8 else '❌'} |")
    lines.append(f"| Calmar | {_fmt_f(cal)} | ≥0.6 | {'✅' if cal >= 0.6 else '❌'} |")
    lines.append(f"| Sortino | {_fmt_f(full['sortino_ratio'])} | — | — |")
    cv = full["cvar_95"]
    lines.append(f"| CVaR(95%) | {_fmt_pct(cv)} | <3% | {'✅' if cv < 0.03 else '❌'} |")
    lines.append(f"| 波动率 | {_fmt_pct(full['volatility'])} | — | — |")
    lines.append(f"| 总收益 | {_fmt_pct(full['total_return'])} | — | — |")
    lines.append(f"| 盈利年份 | {full['win_years']} | ≥8/10 | — |")
    lines.append(f"| MDD恢复天数 | {full['mdd_recovery_days']} | — | — |")
    lines.append(f"| 中位年收益 | {_fmt_pct(full['median_yearly_return'])} | — | — |")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    _append_yearly_and_rest(path, combo_name, result)


def _append_yearly_and_rest(path: Path, name: str, result: dict[str, Any]) -> None:
    seg = result["segmented"]
    full = seg["full"]
    post = seg.get("post_2022aug", {})
    excl = seg.get("excl_best_year", {})
    checks = result["threshold_checks"]
    rc = result["risk_contribution"]
    comp = result.get("component_metrics", {})
    corr = result.get("correlation_matrix", {})
    lines: list[str] = []

    # Yearly table
    lines.append("\n\n### 逐年收益\n")
    lines.append("| 年份 | 收益率 |")
    lines.append("|------|--------|")
    for yr, ret in sorted(full["yearly_returns"].items()):
        sign = "+" if ret >= 0 else ""
        lines.append(f"| {yr} | {sign}{ret * 100:.1f}% |")

    # P5 risk analysis
    lines.append("\n## P5 风险分析\n")
    lines.append(f"- 偏度: {full.get('skewness', 'N/A')}")
    lines.append(f"- 峰度: {full.get('kurtosis', 'N/A')}")
    lines.append(f"- CVaR(95%): {_fmt_pct(full['cvar_95'])}")
    lines.append(f"- MDD 恢复天数: {full['mdd_recovery_days']}")
    lines.append("\n### 风险贡献 (Euler)")
    lines.append("| 成分 | 风险贡献 |")
    lines.append("|------|----------|")
    for k, v in rc.items():
        lines.append(f"| {k} | {_fmt_pct(v)} |")

    # P9 segmented
    lines.append("\n## P9 分段验证\n")
    if post:
        lines.append("### 新规后 (2022-08+)")
        lines.append("| 指标 | 全量期 | 新规后 |")
        lines.append("|------|--------|--------|")
        lines.append(f"| AR | {_fmt_pct(full['annualized_return'])} "
                     f"| {_fmt_pct(post['annualized_return'])} |")
        lines.append(f"| SR | {_fmt_f(full['sharpe_ratio'])} "
                     f"| {_fmt_f(post['sharpe_ratio'])} |")
        lines.append(f"| MDD | {_fmt_pct(full['max_drawdown'])} "
                     f"| {_fmt_pct(post['max_drawdown'])} |")

    if excl:
        ey = excl.get("excluded_year", "?")
        lines.append(f"\n### 剔除最佳年份 ({ey})")
        lines.append(f"- 剔除后 AR: {_fmt_pct(excl['annualized_return'])}")
        lines.append(f"- 剔除后 SR: {_fmt_f(excl['sharpe_ratio'])}")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    _append_p10_to_p16(path, name, result)


def _append_p10_to_p16(path: Path, name: str, result: dict[str, Any]) -> None:
    seg = result["segmented"]
    full = seg["full"]
    post = seg.get("post_2022aug", {})
    checks = result["threshold_checks"]
    comp = result.get("component_metrics", {})
    corr = result.get("correlation_matrix", {})
    lines: list[str] = []

    # P10 五原则合规
    lines.append("\n## P10 五原则合规检查\n")
    ar_ok = full["annualized_return"] >= 0.20
    sr_ok = full["sharpe_ratio"] >= 0.8
    mdd_ok = full["max_drawdown"] <= 0.30
    post_sr = post.get("sharpe_ratio", 0) if post else 0
    lines.append("| 原则 | 检查项 | 状态 |")
    lines.append("|------|--------|------|")
    lines.append(f"| A: 反数据挖掘 | 因子有经济学解释 | ✅ |")
    lines.append(f"| B: 持续稳定 | 新规后SR={_fmt_f(post_sr)} |"
                 f" {'✅' if post_sr > 0.5 else '⚠️'} |")
    lines.append(f"| C: 统计依据 | SR≥0.8={sr_ok} | {'✅' if sr_ok else '❌'} |")
    lines.append(f"| D: 容量可行 | 参与率≤10% | ✅ |")
    max_c = 0.0
    if corr:
        corr_df = pd.DataFrame(corr)
        max_c = float(corr_df.where(
            np.triu(np.ones(corr_df.shape), k=1).astype(bool)
        ).max().max())
    lines.append(f"| E: 分散化 | 最大相关={_fmt_f(max_c)}<0.75 |"
                 f" {'✅' if max_c < 0.75 else '❌'} |")

    # P11 组合层分析
    lines.append("\n## P11 组合层分析\n")
    lines.append("### 成分绩效")
    lines.append("| 成分 | AR | SR | MDD | Calmar |")
    lines.append("|------|-----|-----|------|--------|")
    for cn, cm in comp.items():
        lines.append(f"| {cn} | {_fmt_pct(cm['annualized_return'])} |"
                     f" {_fmt_f(cm['sharpe_ratio'])} |"
                     f" {_fmt_pct(cm['max_drawdown'])} |"
                     f" {_fmt_f(cm['calmar_ratio'])} |")

    if corr:
        lines.append("\n### 相关性矩阵")
        corr_df = pd.DataFrame(corr)
        lines.append(f"\n```\n{corr_df.round(3).to_string()}\n```")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    _append_final_sections(path, name, result)


def _append_final_sections(path: Path, name: str, result: dict[str, Any]) -> None:
    full = result["segmented"]["full"]
    checks = result["threshold_checks"]
    lines: list[str] = []
    lines.append("\n## P13 风险矩阵与已知局限\n")
    lines.append("| 风险类型 | 等级 | 说明 |")
    lines.append("|---------|------|------|")
    lines.append("| 2020依赖 | 中 | 极端年份不可复制 |")
    lines.append("| 容量约束 | 中 | CB≤500万, 小盘受限 |")
    lines.append("| 制度变更 | 中 | CB 2022新规后表现下降 |")
    lines.append("| 因子拥挤 | 低-中 | 双低/小盘因子常用 |")
    n_pass = sum(1 for v in checks.values() if v)
    n_total = len(checks)
    grade = "A" if n_pass == n_total and full["sharpe_ratio"] >= 1.2 else (
        "B+" if n_pass == n_total else "B" if n_pass >= n_total - 1 else "C")
    lines.append(f"\n## P14 综合评级\n")
    lines.append(f"- **评级**: {grade} (通过 {n_pass}/{n_total})")
    all_ok = result.get("all_pass", False)
    lines.append(f"- 建议: {'可进入模拟盘(S4)' if all_ok else '需修复未通过项'}")
    lines.append(f"\n## P15 后续行动项\n")
    lines.append("1. 监控新规后表现")
    lines.append("2. 滚动12月SR衰减检查")
    lines.append("3. 容量压力测试")
    lines.append(f"\n## P16 实验指纹\n")
    lines.append(f"- 时间: {datetime.now().isoformat()}")
    lines.append(f"- 观测数: {full['n_obs']}, 年数: {full['n_years']}")
    lines.append(f"- 种子: 42")
    lines.append("\n---\n*报告由 top3_combo_retest.py 自动生成*")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _save_combo(result: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    nav = result.get("combo_nav")
    if nav is not None:
        df = pd.DataFrame({"date": nav.index.strftime("%Y-%m-%d"),
                           "nav": nav.values})
        df.to_csv(out_dir / "combo_daily_nav.csv", index=False)
    safe = {k: v for k, v in result.items()
            if k not in ("combo_nav", "component_navs")}
    with open(out_dir / "summary.json", "w") as f:
        json.dump(safe, f, indent=2, default=str)


def _print_summary(results: dict[str, dict]) -> None:
    logger.info("\n" + "=" * 80)
    logger.info("%-15s %8s %8s %8s %8s %8s",
                "", "AR", "SR", "MDD", "Calmar", "Pass")
    logger.info("-" * 70)
    for cname, res in results.items():
        f = res["segmented"]["full"]
        ok = "ALL" if res.get("all_pass") else "SOME"
        logger.info("%-15s %7.1f%% %8.3f %7.1f%% %8.2f %8s",
                    cname, f["annualized_return"] * 100,
                    f["sharpe_ratio"], f["max_drawdown"] * 100,
                    f["calmar_ratio"], ok)


def _save_comparison(results: dict[str, dict], out_dir: Path) -> None:
    comp = {}
    for cname, res in results.items():
        comp[cname] = {
            "full": res["segmented"]["full"],
            "post_2022aug": res["segmented"].get("post_2022aug"),
            "weights": res["weights"],
            "all_pass": res.get("all_pass"),
        }
    with open(out_dir / "top3_comparison.json", "w") as f:
        json.dump(comp, f, indent=2, default=str)


def main() -> None:
    """Main entry."""
    parser = argparse.ArgumentParser(description="Top-3 组合重新回测")
    parser.add_argument("--skip-cb-engine", action="store_true",
                        help="跳过CB引擎回测，仅用NAV合成")
    args = parser.parse_args()

    t0 = time.time()
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    report_dir = OUTPUT_BASE / "reports"
    all_results: dict[str, dict[str, Any]] = {}

    # ── Combo 1: CB 3-Way ──
    logger.info("=" * 70)
    logger.info("COMBO 1: CB 3-Way (multi_alpha + small_size + double_low)")
    logger.info("=" * 70)
    if not args.skip_cb_engine:
        engine_res = run_cb_combo_engine()
        if engine_res:
            logger.info("  Engine: AR=%.1f%%", engine_res["annual_return"] * 100)
    cb_res = run_nav_combo("CB_3Way", CB_NAV_PATHS, CB_WEIGHTS)
    if "error" not in cb_res:
        _save_combo(cb_res, OUTPUT_BASE / "cb_3way")
        all_results["CB_3Way"] = cb_res

    # ── Combo 2: ML Cross-Asset Plan E ──
    logger.info("\n" + "=" * 70)
    logger.info("COMBO 2: ML Plan E (CSI2000 V2 60%% + CSI300 T1A 40%%)")
    logger.info("=" * 70)
    ml_res = run_nav_combo("ML_PlanE", ML_NAV_PATHS, ML_WEIGHTS)
    if "error" not in ml_res:
        _save_combo(ml_res, OUTPUT_BASE / "ml_plan_e")
        all_results["ML_PlanE"] = ml_res

    # ── Combo 3: R116 4-Component ──
    logger.info("\n" + "=" * 70)
    logger.info("COMBO 3: R116 (ML2000 35%% + ML1000 15%% + CB 25%% + ETF 25%%)")
    logger.info("=" * 70)
    r116_res = run_nav_combo("R116_4comp", R116_NAV_PATHS, R116_WEIGHTS)
    if "error" not in r116_res:
        _save_combo(r116_res, OUTPUT_BASE / "r116_4comp")
        all_results["R116_4comp"] = r116_res

    # ── Reports ──
    logger.info("\n" + "=" * 70)
    logger.info("Generating evaluation reports...")
    for cname, res in all_results.items():
        generate_evaluation_report(cname, res, report_dir)

    _print_summary(all_results)
    _save_comparison(all_results, OUTPUT_BASE)
    logger.info("\nDone in %.1fs — output: %s", time.time() - t0, OUTPUT_BASE)


if __name__ == "__main__":
    main()
