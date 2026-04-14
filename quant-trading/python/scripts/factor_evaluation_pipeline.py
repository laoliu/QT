"""自动化因子评估管道 — 从挖掘 KB 到可用因子清单的全流程.

流程:
  1. 读取 mutation_kb.json 中 passed 因子
  2. 计算因子值 (FactorExpressionParser)
  3. IC 相关矩阵 → 层次聚类 → 去冗余
  4. 深度评估: IC衰减 / 分位收益 / 换手率
  5. Walk-Forward 交叉验证
  6. 输出: 独立因子清单 + 评估报告 JSON

用法:
  python scripts/factor_evaluation_pipeline.py --kb output/auto_mining_v6/mutation_kb.json
  python scripts/factor_evaluation_pipeline.py --kb output/auto_mining_v6/mutation_kb.json --skip-wf
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from scipy.cluster.hierarchy import fcluster, linkage

# ── 项目路径 ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("factor_eval")

# ── 评估常量 ──
REDUNDANCY_THRESHOLD = 0.70   # IC 相关性去冗余阈值
IC_DECAY_PERIODS = [1, 3, 5, 10, 20]  # IC 衰减持有期 (天)
N_QUANTILES = 5               # 分位收益分组数
WF_N_FOLDS = 5                # Walk-Forward 折数
WF_OOS_RATIO = 0.20           # OOS 占比
TURNOVER_QUANTILE = 0.20      # 换手率分析 top 20%
POST_SEG_DATE = "2022-08"     # 新规后分段


def load_kb_passed(kb_path: str) -> list[dict]:
    """从 mutation_kb.json 加载已通过因子."""
    kb_file = Path(kb_path)
    if not kb_file.exists():
        raise FileNotFoundError(f"KB 不存在: {kb_file}")
    with open(kb_file, "r", encoding="utf-8") as f:
        entries = json.load(f)
    passed = [e for e in entries if e.get("passed")]
    passed.sort(key=lambda e: abs(e.get("icir", 0)), reverse=True)
    logger.info("KB 加载: %d 总计, %d passed", len(entries), len(passed))
    return passed


def _count_total_kb_entries(kb_path: str) -> int:
    """获取 KB 中全部因子数 (含 passed + failed), 用于 DSR N."""
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        return len(entries)
    except Exception:
        return 0


def compute_factor_values(
    df: pd.DataFrame, factors: list[dict], parser: Any,
) -> dict[str, pd.Series]:
    """批量计算因子值. Returns {name: Series}."""
    results: dict[str, pd.Series] = {}
    for entry in factors:
        name = entry["name"]
        expr = entry["expression"]
        try:
            parsed = parser.parse(expr, name)
            vals = parser.execute(parsed, df)
            if vals is not None and not vals.isna().all():
                results[name] = vals
            else:
                logger.warning("因子 %s 全 NaN, 跳过", name)
        except Exception as e:
            logger.warning("因子 %s 计算失败: %s", name, e)
    logger.info("因子计算完成: %d/%d 成功", len(results), len(factors))
    return results


def series_to_panel(
    df: pd.DataFrame, factor_series: pd.Series, name: str,
) -> dict[date, dict[str, float]]:
    """DataFrame Series → FactorPanel {date: {symbol: value}}."""
    panel: dict[date, dict[str, float]] = {}
    tmp = pd.DataFrame({
        "datetime": df["datetime"],
        "symbol": df["symbol"],
        "val": factor_series,
    }).dropna(subset=["val"])
    for dt, grp in tmp.groupby("datetime"):
        d = dt.date() if hasattr(dt, "date") else dt
        panel[d] = dict(zip(grp["symbol"], grp["val"]))
    return panel


def compute_ic_matrix(
    df: pd.DataFrame, factor_values: dict[str, pd.Series],
    fwd_col: str,
) -> tuple[np.ndarray, list[str]]:
    """计算因子间月度 IC 相关矩阵.

    对每因子计算月频截面 Spearman IC 序列, 再求 IC 序列间的 Pearson 相关.
    """
    names = sorted(factor_values.keys())
    n = len(names)
    # 计算每个因子的月频 IC 序列
    ic_series: dict[str, pd.Series] = {}
    tmp = df[["datetime", fwd_col]].copy()
    tmp["ym"] = tmp["datetime"].dt.to_period("M")
    for name in names:
        fvals = factor_values[name]
        sub = pd.DataFrame({
            "f": fvals, "r": df[fwd_col], "ym": tmp["ym"],
        }).dropna()
        monthly_ic = {}
        for ym, grp in sub.groupby("ym"):
            if len(grp) < 30:
                continue
            if grp["f"].std() < 1e-12:
                continue
            ic, _ = sp_stats.spearmanr(grp["f"], grp["r"])
            if np.isfinite(ic):
                monthly_ic[str(ym)] = ic
        ic_series[name] = pd.Series(monthly_ic)
    # 构建 IC 相关矩阵
    corr = np.eye(n)
    common_months = set.intersection(
        *[set(s.index) for s in ic_series.values()]
    ) if ic_series else set()
    if len(common_months) < 6:
        logger.warning("共同月份不足 (%d), IC矩阵可能不准",
                       len(common_months))
    for i in range(n):
        for j in range(i + 1, n):
            si = ic_series[names[i]]
            sj = ic_series[names[j]]
            common = si.index.intersection(sj.index)
            if len(common) < 6:
                corr[i, j] = corr[j, i] = 0.0
                continue
            r, _ = sp_stats.pearsonr(
                si.loc[common].values, sj.loc[common].values)
            corr[i, j] = corr[j, i] = r if np.isfinite(r) else 0.0
    logger.info("IC 相关矩阵: %dx%d, 共同月份=%d",
                n, n, len(common_months))
    return corr, names


def cluster_factors(
    ic_corr: np.ndarray, names: list[str],
    threshold: float = REDUNDANCY_THRESHOLD,
) -> dict[int, list[str]]:
    """层次聚类去冗余. Returns {cluster_id: [factor_names]}."""
    n = len(names)
    if n <= 1:
        return {0: names[:]}
    # 距离 = 1 - |corr|
    dist = 1.0 - np.abs(ic_corr)
    np.fill_diagonal(dist, 0.0)
    # condensed distance for linkage
    from scipy.spatial.distance import squareform
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="average")
    # 切割阈值 = 1 - threshold (e.g. 0.70 → dist=0.30)
    labels = fcluster(Z, t=1.0 - threshold, criterion="distance")
    clusters: dict[int, list[str]] = {}
    for name, label in zip(names, labels):
        clusters.setdefault(int(label), []).append(name)
    logger.info("聚类完成: %d 因子 → %d 簇 (阈值=%.2f)",
                n, len(clusters), threshold)
    return clusters


def select_representatives(
    clusters: dict[int, list[str]],
    factor_stats: dict[str, dict],
) -> list[str]:
    """每簇选 |ICIR| 最高的因子作为独立代表."""
    reps: list[str] = []
    for cid, members in sorted(clusters.items()):
        best = max(members,
                   key=lambda n: abs(factor_stats.get(n, {}).get("icir", 0)))
        reps.append(best)
        if len(members) > 1:
            logger.info("簇 %d: %d 因子, 代表=%s (ICIR=%.3f), 淘汰=%s",
                        cid, len(members), best,
                        abs(factor_stats.get(best, {}).get("icir", 0)),
                        [m for m in members if m != best])
    logger.info("去冗余: %d 簇 → %d 独立因子", len(clusters), len(reps))
    return reps


def evaluate_ic_decay(
    df: pd.DataFrame, factor_series: pd.Series,
    name: str, periods: list[int] = IC_DECAY_PERIODS,
) -> dict[int, float]:
    """IC 衰减分析: 不同持有期的 IC.

    最佳持有期对应最高 |IC|, 短窗口因子衰减快意味高换手.
    """
    result: dict[int, float] = {}
    for p in periods:
        fwd = df.groupby("symbol")["close"].transform(
            lambda s: s.shift(-p) / s - 1.0
        )
        sub = pd.DataFrame({"f": factor_series, "r": fwd}).dropna()
        if len(sub) < 100:
            result[p] = 0.0
            continue
        ic, _ = sp_stats.spearmanr(sub["f"], sub["r"])
        result[p] = round(float(ic), 5) if np.isfinite(ic) else 0.0
    return result


def evaluate_quantile_returns(
    df: pd.DataFrame, factor_series: pd.Series,
    fwd_col: str, n_q: int = N_QUANTILES,
) -> dict:
    """分位收益分析.

    按因子值分 n_q 组, 计算每组平均前向收益; 单调性越好=因子越有效.
    """
    sub = pd.DataFrame({
        "f": factor_series, "r": df[fwd_col], "dt": df["datetime"],
    }).dropna()
    if len(sub) < 200:
        return {"quantile_returns": {}, "long_short": 0.0,
                "monotonicity": 0.0}
    # 按月截面分组
    sub["ym"] = sub["dt"].dt.to_period("M")
    q_rets: dict[int, list[float]] = {q: [] for q in range(1, n_q + 1)}
    for _, grp in sub.groupby("ym"):
        if len(grp) < n_q * 5:
            continue
        try:
            grp = grp.copy()
            grp["q"] = pd.qcut(grp["f"], n_q, labels=False,
                               duplicates="drop") + 1
        except ValueError:
            continue
        for q in range(1, n_q + 1):
            qg = grp[grp["q"] == q]
            if len(qg) > 0:
                q_rets[q].append(float(qg["r"].mean()))
    avg_rets = {q: round(float(np.mean(v)), 5) if v else 0.0
                for q, v in q_rets.items()}
    top = avg_rets.get(n_q, 0.0)
    bot = avg_rets.get(1, 0.0)
    ls = round(top - bot, 5)
    # 单调性: Spearman of quantile_rank vs avg_return
    qs = list(avg_rets.keys())
    rs = [avg_rets[q] for q in qs]
    mono = 0.0
    if len(qs) >= 3:
        mono_r, _ = sp_stats.spearmanr(qs, rs)
        mono = round(float(mono_r), 3) if np.isfinite(mono_r) else 0.0
    return {"quantile_returns": avg_rets, "long_short": ls,
            "monotonicity": mono}


def evaluate_turnover(
    df: pd.DataFrame, factor_series: pd.Series,
    name: str, quantile: float = TURNOVER_QUANTILE,
) -> dict:
    """因子换手率分析.

    衡量 top 分位持仓每月的变动比例, 高换手 = 高交易成本.
    """
    sub = pd.DataFrame({
        "f": factor_series, "sym": df["symbol"], "dt": df["datetime"],
    }).dropna()
    if len(sub) < 200:
        return {"avg_turnover": 1.0, "monthly_turnovers": []}
    sub["ym"] = sub["dt"].dt.to_period("M")
    monthly_tops: dict[str, set[str]] = {}
    for ym, grp in sub.groupby("ym"):
        if len(grp) < 20:
            continue
        thresh = grp["f"].quantile(1.0 - quantile)
        top_syms = set(grp[grp["f"] >= thresh]["sym"])
        if top_syms:
            monthly_tops[str(ym)] = top_syms
    # 计算月间换手率
    months = sorted(monthly_tops.keys())
    turnovers: list[float] = []
    for i in range(1, len(months)):
        prev = monthly_tops[months[i - 1]]
        curr = monthly_tops[months[i]]
        union = prev | curr
        if not union:
            continue
        changed = len(prev.symmetric_difference(curr))
        to = changed / len(union)
        turnovers.append(round(to, 4))
    avg_to = round(float(np.mean(turnovers)), 4) if turnovers else 1.0
    return {"avg_turnover": avg_to, "monthly_turnovers": turnovers}


def walk_forward_validate(
    df: pd.DataFrame, factor_series: pd.Series,
    fwd_col: str, n_folds: int = WF_N_FOLDS,
    embargo_days: int | None = None,
) -> dict:
    """Purged Walk-Forward 交叉验证.

    将时间轴等分 n_folds 折, 累积训练窗口+OOS 测试, 报告每折 OOS IC/ICIR.

    Args:
        embargo_days: IS/OOS 之间的 embargo 交易日数。
            默认 None → 自动从 fwd_col 提取 (如 fwd_10d → 10)。
            防止 IS 尾部前向收益标签与 OOS 头部重叠。
    """
    sub = pd.DataFrame({
        "f": factor_series, "r": df[fwd_col], "dt": df["datetime"],
    }).dropna().sort_values("dt")
    # 自动推断 embargo 天数
    if embargo_days is None:
        import re as _re
        m = _re.search(r"(\d+)", fwd_col)
        embargo_days = int(m.group(1)) if m else 10
    if len(sub) < 1000:
        return {"n_folds": 0, "fold_results": [],
                "oos_ic_mean": 0.0, "oos_icir": 0.0,
                "embargo_days": embargo_days}
    dates = sub["dt"].unique()
    n_dates = len(dates)
    fold_size = n_dates // (n_folds + 1)  # 先留 1 share 给初始 IS
    fold_results: list[dict] = []
    for fold in range(n_folds):
        # IS: dates[0 : (fold+1)*fold_size]
        is_end_idx = (fold + 1) * fold_size
        # Embargo: 跳过 IS 末尾 embargo_days 个交易日
        oos_start_idx = is_end_idx + embargo_days
        oos_end_idx = min((fold + 2) * fold_size, n_dates)
        if oos_end_idx <= oos_start_idx:
            break
        oos_start = dates[oos_start_idx]
        oos_end = dates[oos_end_idx - 1]
        oos = sub[(sub["dt"] >= oos_start) & (sub["dt"] <= oos_end)]
        if len(oos) < 100:
            continue
        # OOS 月频 IC
        oos_copy = oos.copy()
        oos_copy["ym"] = oos_copy["dt"].dt.to_period("M")
        ics: list[float] = []
        for _, grp in oos_copy.groupby("ym"):
            if len(grp) < 30 or grp["f"].std() < 1e-12:
                continue
            ic, _ = sp_stats.spearmanr(grp["f"], grp["r"])
            if np.isfinite(ic):
                ics.append(float(ic))
        if not ics:
            continue
        mu = float(np.mean(ics))
        sd = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
        icir = mu / sd if sd > 1e-12 else 0.0
        fold_results.append({
            "fold": fold + 1,
            "oos_start": str(oos_start.date()),
            "oos_end": str(oos_end.date()),
            "oos_ic": round(mu, 5),
            "oos_icir": round(icir, 3),
            "n_months": len(ics),
        })
    # 汇总
    if fold_results:
        all_ics = [f["oos_ic"] for f in fold_results]
        all_icirs = [f["oos_icir"] for f in fold_results]
        oos_ic_mean = round(float(np.mean(all_ics)), 5)
        oos_icir_mean = round(float(np.mean(all_icirs)), 3)
    else:
        oos_ic_mean = 0.0
        oos_icir_mean = 0.0
    return {
        "n_folds": len(fold_results),
        "fold_results": fold_results,
        "oos_ic_mean": oos_ic_mean,
        "oos_icir": oos_icir_mean,
        "embargo_days": embargo_days,
    }


def _compute_monthly_ics(
    df: pd.DataFrame, factor_series: pd.Series, fwd_col: str,
) -> list[float]:
    """计算月频 Spearman IC (供 DSR 使用)."""
    sub = pd.DataFrame({
        "f": factor_series, "r": df[fwd_col], "dt": df["datetime"],
    }).dropna()
    if len(sub) < 100:
        return []
    sub["ym"] = sub["dt"].dt.to_period("M")
    ics: list[float] = []
    for _, grp in sub.groupby("ym"):
        if len(grp) < 30 or grp["f"].std() < 1e-12:
            continue
        ic, _ = sp_stats.spearmanr(grp["f"], grp["r"])
        if np.isfinite(ic):
            ics.append(float(ic))
    return ics


def evaluate_single_factor(
    df: pd.DataFrame, factor_series: pd.Series,
    name: str, fwd_col: str, skip_wf: bool = False,
    n_total_trials: int = 0,
    all_factor_values: dict[str, pd.Series] | None = None,
) -> dict:
    """单因子综合评估 (IC衰减 + 分位收益 + 换手率 + WF + DSR + PBO)."""
    result: dict[str, Any] = {"name": name}
    # IC 衰减
    result["ic_decay"] = evaluate_ic_decay(df, factor_series, name)
    # 分位收益
    result["quantile"] = evaluate_quantile_returns(
        df, factor_series, fwd_col)
    # 换手率
    result["turnover"] = evaluate_turnover(df, factor_series, name)
    # Walk-Forward
    if not skip_wf:
        result["walk_forward"] = walk_forward_validate(
            df, factor_series, fwd_col)
    # DSR 校正 (Bailey & López de Prado 2014)
    if n_total_trials >= 2:
        try:
            from aipha.factors.deflated_sharpe import compute_dsr_for_factor
            ic_list = result["ic_decay"].get("_raw_ic_monthly", [])
            if not ic_list:
                # 回退: 用 ic_decay period=1 的 IC 近似
                ic_list = _compute_monthly_ics(
                    df, factor_series, fwd_col)
            result["dsr"] = compute_dsr_for_factor(
                ic_list, n_total_trials)
        except Exception as e:
            logger.warning("DSR 计算失败 (%s): %s", name, e)
            result["dsr"] = {"dsr": 0, "p_value": 1.0,
                             "is_significant": False}
    # PBO 过拟合概率 (Bailey & López de Prado 2017 CSCV)
    if all_factor_values and len(all_factor_values) >= 2:
        try:
            from aipha.reporting.pbo import PBOAnalyzer, PBOConfig
            # 构建月频 IC 绩效矩阵 (T, N)
            all_names = sorted(all_factor_values.keys())
            monthly_ic_dict: dict[str, dict[str, float]] = {}
            tmp_ym = df[["datetime", fwd_col]].copy()
            tmp_ym["ym"] = tmp_ym["datetime"].dt.to_period("M")
            for fn in all_names:
                fv = all_factor_values[fn]
                sub = pd.DataFrame({
                    "f": fv, "r": df[fwd_col], "ym": tmp_ym["ym"],
                }).dropna()
                mic: dict[str, float] = {}
                for ym, grp in sub.groupby("ym"):
                    if len(grp) < 30 or grp["f"].std() < 1e-12:
                        continue
                    ic, _ = sp_stats.spearmanr(grp["f"], grp["r"])
                    if np.isfinite(ic):
                        mic[str(ym)] = float(ic)
                monthly_ic_dict[fn] = mic
            # 对齐共同月份
            common_m = set.intersection(
                *[set(v.keys()) for v in monthly_ic_dict.values()]
            ) if monthly_ic_dict else set()
            if len(common_m) >= 12 and len(all_names) >= 2:
                sorted_m = sorted(common_m)
                perf_mat = np.array([
                    [monthly_ic_dict[fn][m] for m in sorted_m]
                    for fn in all_names
                ]).T  # (T, N)
                n_sub = min(16, max(4, len(sorted_m) // 4))
                n_sub = n_sub if n_sub % 2 == 0 else n_sub - 1
                pbo_cfg = PBOConfig(num_subsamples=max(4, n_sub))
                pbo_result = PBOAnalyzer(pbo_cfg).compute(perf_mat)
                result["pbo"] = {
                    "pbo": round(pbo_result.pbo, 4),
                    "is_overfit": pbo_result.is_overfit,
                    "n_tested": pbo_result.num_combinations_tested,
                    "sd_ratio": round(pbo_result.stochastic_dominance_ratio, 4)
                    if pbo_result.stochastic_dominance_ratio is not None
                    else None,
                }
            else:
                result["pbo"] = {"pbo": None, "is_overfit": None,
                                 "reason": "共同月份不足或因子数不足"}
        except Exception as e:
            logger.warning("PBO 计算失败 (%s): %s", name, e)
            result["pbo"] = {"pbo": None, "error": str(e)}
    # 综合评分
    ic10 = abs(result["ic_decay"].get(10, 0))
    mono = abs(result["quantile"].get("monotonicity", 0))
    to = result["turnover"].get("avg_turnover", 1.0)
    wf_icir = abs(result.get("walk_forward", {}).get("oos_icir", 0))
    # 加权综合分 (0-1 范围)
    score = (0.30 * min(ic10 / 0.05, 1.0)
             + 0.25 * min(mono, 1.0)
             + 0.20 * max(1.0 - to, 0.0)
             + 0.25 * min(wf_icir / 1.0, 1.0))
    result["composite_score"] = round(score, 4)
    return result


def _build_report(
    passed: list[dict], factor_values: dict[str, pd.Series],
    ic_corr: np.ndarray, names: list[str],
    clusters: dict[int, list[str]], reps: list[str],
    evaluations: list[dict], args: argparse.Namespace,
    elapsed: float,
) -> dict:
    """构建最终 JSON 报告."""
    return {
        "pipeline_version": "1.0",
        "run_time_s": round(elapsed, 1),
        "config": {
            "kb_path": str(args.kb),
            "universe": args.universe,
            "start": args.start, "end": args.end,
            "fwd_days": args.fwd,
            "redundancy_threshold": REDUNDANCY_THRESHOLD,
            "wf_folds": WF_N_FOLDS,
        },
        "n_passed": len(passed),
        "n_computed": len(factor_values),
        "n_clusters": len(clusters),
        "n_independent": len(reps),
        "cluster_detail": {
            str(k): v for k, v in clusters.items()},
        "independent_factors": reps,
        "evaluations": evaluations,
    }


def _print_summary(evaluations: list[dict]) -> None:
    """打印控制台摘要."""
    logger.info("=" * 60)
    logger.info("因子评估摘要 (按综合评分排序)")
    logger.info("=" * 60)
    for i, ev in enumerate(evaluations):
        name = ev["name"]
        score = ev.get("composite_score", 0)
        icir = ev.get("kb_icir", 0)
        to = ev.get("turnover", {}).get("avg_turnover", 0)
        mono = ev.get("quantile", {}).get("monotonicity", 0)
        wf = ev.get("walk_forward", {}).get("oos_icir", 0)
        pbo_val = ev.get("pbo", {}).get("pbo")
        pbo_str = f"PBO={pbo_val:.2f}" if pbo_val is not None else "PBO=N/A"
        logger.info(
            " %2d. %-40s  score=%.3f  ICIR=%.3f  "
            "mono=%.2f  TO=%.2f  WF_ICIR=%.3f  %s",
            i+1, name[:40], score, icir, mono, to, wf, pbo_str)
    logger.info("=" * 60)


def _persist_evaluation_to_db(
    store: Any, report: dict, evaluations: list[dict],
    clusters: dict[int, list[str]], reps: list[str],
) -> None:
    """将评估结果写入 MiningStore (如 store 非 None)."""
    if store is None:
        return
    try:
        cfg = report.get("config", {})
        eval_id = (
            f"eval_{cfg.get('kb_path', 'unknown')}"
            f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        store.upsert_eval_run({
            "eval_id": eval_id,
            "kb_path": cfg.get("kb_path", ""),
            "universe": cfg.get("universe", ""),
            "start_date": cfg.get("start", ""),
            "end_date": cfg.get("end", ""),
            "fwd_days": cfg.get("fwd_days", 10),
            "n_passed": report.get("n_passed", 0),
            "n_computed": report.get("n_computed", 0),
            "n_clusters": report.get("n_clusters", 0),
            "n_independent": report.get("n_independent", 0),
            "run_time_s": report.get("run_time_s", 0),
            "config_json": cfg,
        })
        rep_set = set(reps)
        name_to_cluster: dict[str, int] = {}
        for cid, members in clusters.items():
            for m in members:
                name_to_cluster[m] = int(cid)
        for ev in evaluations:
            name = ev.get("name", "")
            store.upsert_eval_factor({
                "eval_id": eval_id,
                "factor_name": name,
                "expression": ev.get("expression", ""),
                "kb_icir": ev.get("kb_icir", 0),
                "kb_ic_mean": ev.get("kb_ic_mean", 0),
                "composite_score": ev.get("composite_score", 0),
                "monotonicity": ev.get("quantile", {}).get(
                    "monotonicity", 0),
                "avg_turnover": ev.get("turnover", {}).get(
                    "avg_turnover", 0),
                "wf_oos_icir": ev.get("walk_forward", {}).get(
                    "oos_icir", 0),
                "wf_n_folds": ev.get("walk_forward", {}).get(
                    "n_folds", 0),
                "ic_decay_json": ev.get("ic_decay"),
                "quantile_json": ev.get("quantile"),
                "cluster_id": name_to_cluster.get(name, -1),
                "is_representative": name in rep_set,
            })
        logger.info("DB 写入: eval_id=%s, %d 因子",
                     eval_id, len(evaluations))
    except Exception as exc:
        logger.warning("评估结果 DB 写入失败: %s", exc)


def run_evaluation(
    kb_path: str, *,
    df: Optional[pd.DataFrame] = None,
    universe: str = "csi2000",
    start: str = "2015-01-05",
    end: str = "2026-02-27",
    fwd: int = 10,
    output_dir: Optional[str] = None,
    skip_wf: bool = False,
    top_n: int = 0,
    store: Any = None,
) -> dict:
    """可编程调用入口 — 供守护进程/其他脚本直接调用.

    Args:
        kb_path: mutation_kb.json 路径
        df: 预加载的市场数据 (可选, 避免重复加载)
        skip_wf: 跳过 Walk-Forward 验证
        top_n: 仅评估 top N 因子 (0=全部)
    Returns:
        评估报告 dict
    """
    ns = argparse.Namespace(
        kb=kb_path, universe=universe, start=start, end=end,
        fwd=fwd, output=output_dir, skip_wf=skip_wf, top_n=top_n,
    )
    return run_pipeline(ns, preloaded_df=df, store=store)


def run_pipeline(args: argparse.Namespace,
                 preloaded_df: Optional[pd.DataFrame] = None,
                 store: Any = None) -> dict:
    """主评估管道."""
    t0 = time.time()
    kb_path = Path(args.kb)
    out_dir = Path(args.output) if args.output else kb_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 加载 KB ──
    passed = load_kb_passed(str(kb_path))
    if not passed:
        logger.error("无 passed 因子"); return {}
    # 获取总试验数 (含 passed + failed) 用于 DSR 校正
    n_total_trials = _count_total_kb_entries(str(kb_path))
    if args.top_n > 0:
        passed = passed[:args.top_n]
        logger.info("仅评估 top %d 因子", args.top_n)
    factor_stats = {e["name"]: e for e in passed}

    # ── 2. 加载市场数据 ──
    # ── P2-3: 解析多周期 ──
    fwd_periods = [int(x) for x in str(args.fwd).split(",")]
    primary_fwd = fwd_periods[0]
    fwd_col = f"fwd_{primary_fwd}d"

    if preloaded_df is not None:
        df = preloaded_df
        for p in fwd_periods:
            col = f"fwd_{p}d"
            if col not in df.columns:
                df[col] = df.groupby("symbol")["close"].transform(
                    lambda s, _p=p: s.shift(-_p) / s - 1.0)
        logger.info("使用预加载数据: %d行, %d股",
                     len(df), df["symbol"].nunique())
    else:
        from train_ml_factor import load_daily_data, filter_universe
        logger.info("加载市场数据...")
        df = load_daily_data(args.start, args.end)
        df = filter_universe(df, args.universe)
        for p in fwd_periods:
            col = f"fwd_{p}d"
            df[col] = df.groupby("symbol")["close"].transform(
                lambda s, _p=p: s.shift(-_p) / s - 1.0)
        logger.info("数据: %d行, %d股", len(df), df["symbol"].nunique())

    # ── 3. 计算因子值 ──
    from aipha.factors.factor_expression_parser import (
        FactorExpressionParser,
    )
    parser = FactorExpressionParser()
    factor_values = compute_factor_values(df, passed, parser)
    if not factor_values:
        logger.error("无因子计算成功"); return {}

    # ── 4. IC 相关矩阵 + 聚类去冗余 ──
    ic_corr, names = compute_ic_matrix(df, factor_values, fwd_col)
    clusters = cluster_factors(ic_corr, names,
                               threshold=REDUNDANCY_THRESHOLD)
    reps = select_representatives(clusters, factor_stats)
    logger.info("独立因子: %s", reps)

    # ── 5. 深度评估每个独立因子 ──
    extra_fwd_cols = [f"fwd_{p}d" for p in fwd_periods[1:]]
    evaluations: list[dict] = []
    for i, name in enumerate(reps):
        logger.info("[%d/%d] 评估: %s", i+1, len(reps), name)
        ev = evaluate_single_factor(
            df, factor_values[name], name, fwd_col,
            skip_wf=args.skip_wf,
            n_total_trials=n_total_trials,
            all_factor_values=factor_values)
        ev["expression"] = factor_stats[name].get("expression", "")
        ev["kb_icir"] = factor_stats[name].get("icir", 0)
        ev["kb_ic_mean"] = factor_stats[name].get("ic_mean", 0)
        # P2-3: 多周期 IC 交叉验证
        if extra_fwd_cols:
            mp: dict[str, dict] = {}
            for efcol in extra_fwd_cols:
                ic_recs = _compute_monthly_ics(
                    df, factor_values[name], efcol)
                if ic_recs:
                    n_m = len(ic_recs)
                    mu = float(np.mean(ic_recs))
                    sd = float(np.std(ic_recs, ddof=1)) if n_m > 1 else 0
                    icir = mu / sd if sd > 1e-12 else 0
                    mp[efcol] = {"ic_mean": round(mu, 5),
                                 "icir": round(icir, 3), "n": n_m}
            ev["multi_period"] = mp
        evaluations.append(ev)
    evaluations.sort(key=lambda e: e.get("composite_score", 0),
                     reverse=True)

    # ── 6. 汇总报告 ──
    report = _build_report(
        passed, factor_values, ic_corr, names,
        clusters, reps, evaluations, args, time.time() - t0)
    # 保存
    report_path = out_dir / "evaluation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info("报告已保存: %s", report_path)
    # 保存独立因子清单
    indep_path = out_dir / "independent_factors.json"
    with open(indep_path, "w", encoding="utf-8") as f:
        json.dump(evaluations, f, ensure_ascii=False, indent=2,
                  default=str)
    logger.info("独立因子详情: %s", indep_path)
    # ── 7. 持久化到 DB (如有 store) ──
    _persist_evaluation_to_db(store, report, evaluations, clusters, reps)
    _print_summary(evaluations)
    return report


def parse_args() -> argparse.Namespace:
    """CLI 参数解析."""
    p = argparse.ArgumentParser(description="因子自动评估管道")
    p.add_argument("--kb", default="output/auto_mining_v6/mutation_kb.json",
                   help="mutation_kb.json 路径")
    p.add_argument("--universe", default="csi2000")
    p.add_argument("--start", default="2015-01-05")
    p.add_argument("--end", default="2026-02-27")
    p.add_argument("--fwd", type=str, default="10",
                   help="前向收益天数, 逗号分隔多周期 (如 5,10,20)")
    p.add_argument("--output", default=None,
                   help="输出目录 (默认: KB 同级)")
    p.add_argument("--skip-wf", action="store_true",
                   help="跳过 Walk-Forward 验证 (加速)")
    p.add_argument("--top-n", type=int, default=0,
                   help="仅评估 ICIR 最高的 N 个因子 (0=全部)")
    return p.parse_args()


if __name__ == "__main__":
    result = run_pipeline(parse_args())
    if result:
        n_ind = result.get("n_independent", 0)
        n_total = result.get("n_passed", 0)
        logger.info("评估完成: %d/%d 独立因子", n_ind, n_total)
    else:
        logger.error("评估失败")
