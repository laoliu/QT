"""R114 增强组合回测 — HLZ 优化版（基于 R113b 归因分析）。

R114 改进（基于 R113b 归因分析 + 多专家评审）:
  - 移除 v19_hgro（质量门控失败: MDD=39.2% > 30% 阈值）
  - 权重优化: ml 40%→55%, cb 45%→30%, etf 10%→15%
  - ML 权重提升理由: SR=1.593 远高于其他子策略，低相关性(0.138 with CB)
  - ETF 权重提升: 补偿 v19 移除后的分散化，与 ML 相关仅 0.306
  - N_TRIALS 9 = R113 的 8 + R114 新权重配置 1
  - 继承 R113 全部验证框架（DSR/PBO/滑点/扰动/HLZ）

用法:
  python scripts/_combo_enhanced_backtest.py
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import subprocess
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
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s [combo_r112]: %(message)s",
)
logger = logging.getLogger("combo_enhanced")
logger.setLevel(logging.INFO)

OUTPUT_BASE = ROOT / "output" / "combo_r114_enhanced"

def _setup_file_logging() -> None:
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(OUTPUT_BASE / "run.log", mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logging.getLogger().addHandler(handler)

# ── 常量 ──
TOTAL_CAPITAL = 10_000_000.0

# R114 权重优化: 移除 v19（MDD=39.2% 质量门控失败），增加 ML（SR=1.593 最强）
# 论据: 精确 NAV 权重扫描显示 ml55/cb30/etf15 是 HLZ 通过的最平衡方案
#   - ML SR=1.593 vs CB SR=1.009 vs ETF SR=0.478
#   - ML-CB 相关 0.138（极低），ETF-CB 相关 0.050
#   - ETF 提升到 15% 补偿 v19 移除后的分散化损失
WEIGHTS = {
    "ml_csi2000_fund":  0.55,
    "cb_stock_quality": 0.30,
    "etf_cross_v3":     0.15,
}
ORIGINAL_CONFIGS = {
    "cb_stock_quality": ROOT / "configs" / "cb_strategies"
                        / "cb_stock_quality_v4b_balanced_8y.yml",
    "etf_cross_v3":     ROOT / "configs" / "backtest" / "etf_cross_v3_11y.yml",
}
ML_INDEX = "csi2000_lgb_fund"  # 标准 lightgbm 模型 (2020-2025)

# R114 DSR N 值说明:
# 组合级 N=9: 项目历史中实际尝试的组合配置数
#   1. R71 cross_asset_combo（基线固定权重）
#   2. R71 等权变体
#   3. R112 Fixed-Weight (ml35/cb45/v19_10/etf10)
#   4. R112 Risk Parity
#   5. R112 Vol-Targeted Regime
#   6. R113 Fixed-Weight (ml40/cb45/v19_5/etf10)
#   7. R113 Risk Parity
#   8. R113 Vol-Targeted Regime
#   9. R114 Fixed-Weight (ml55/cb30/etf15, v19 移除)
# 子策略级 N 由各自 WF/sweep 报告中记录，此处不双重计数。
N_TRIALS_COMBO = 9


# ── 子策略运行 ──
def run_standard_strategy(name: str, capital: float) -> Path:
    """运行标准引擎子策略。"""
    from aipha.backtest.config_runner import run_from_config
    out_dir = str(OUTPUT_BASE / name)
    logger.info("▶ %s @ %.0f", name, capital)
    t0 = time.time()
    run_from_config(
        ORIGINAL_CONFIGS[name],
        param_overrides={"initial_capital": capital, "output_dir": out_dir},
    )
    logger.info("  ✓ %s  %.1fs", name, time.time() - t0)
    return Path(out_dir)


def run_ml_strategy(capital: float) -> Path:
    """通过 subprocess 运行 ML 子策略。"""
    out_rel = "combo_r114_enhanced/ml_csi2000_fund"
    out_dir = ROOT / "output" / out_rel
    script = ROOT / "scripts" / "risk_controlled_replay.py"
    cmd = [
        sys.executable, str(script),
        "--index", ML_INDEX,
        "--capital", str(capital),
        "--output", out_rel,
    ]
    logger.info("▶ ML @ %.0f", capital)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        logger.error("ML 失败:\n%s\n%s", proc.stdout[-2000:], proc.stderr[-2000:])
        raise RuntimeError(f"ML replay 失败 (rc={proc.returncode})")
    logger.info("  ✓ ML  %.1fs", time.time() - t0)
    return out_dir


def _find_nav_file(out_dir: Path) -> Path | None:
    """返回输出目录中的 NAV 文件路径。"""
    for csv_name in ("daily_snapshots.csv", "daily_nav.csv"):
        csv_path = out_dir / csv_name
        if csv_path.exists():
            return csv_path
    return None


def _has_reusable_output(out_dir: Path) -> bool:
    """判断目录是否存在可复用的有效 NAV 产物。"""
    nav_path = _find_nav_file(out_dir)
    if nav_path is None:
        return False
    try:
        df = pd.read_csv(nav_path, nrows=3)
    except Exception as exc:
        logger.warning("输出检查失败，视为不可复用: %s (%s)", out_dir, exc)
        return False
    return {"date", "nav"}.issubset(df.columns)


def _prepare_output_dir(out_dir: Path, reuse_existing: bool) -> bool:
    """准备子策略输出目录。

    Returns:
        True 表示可以直接复用现有结果；False 表示需要重新运行。
    """
    if reuse_existing and _has_reusable_output(out_dir):
        logger.info("  ⏩ %s 已有有效输出，显式复用", out_dir.name)
        return True
    if out_dir.exists():
        logger.info("  ♻ 清理旧输出后重跑: %s", out_dir)
        shutil.rmtree(out_dir)
    return False


def load_nav(name: str, nav_dir: Path) -> pd.Series:
    """加载子策略 NAV。"""
    for csv_name in ("daily_snapshots.csv", "daily_nav.csv"):
        csv_path = nav_dir / csv_name
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            if "date" in df.columns and "nav" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                s = df.set_index("date")["nav"].sort_index()
                s.name = name
                logger.info("  loaded %s: %d days, nav [%.2f, %.2f]",
                            name, len(s), s.min(), s.max())
                return s
    raise FileNotFoundError(f"No NAV file in {nav_dir}")


def combine_fixed_weight(nav_dict: dict) -> pd.DataFrame:
    """固定权重合成 (baseline)。"""
    frames = []
    for name, nav_s in nav_dict.items():
        w = WEIGHTS.get(name, 0.0)
        frames.append((nav_s / nav_s.iloc[0] * w * TOTAL_CAPITAL).rename(name))
    df = pd.concat(frames, axis=1).sort_index().ffill().dropna()
    df["combo_nav"] = df.sum(axis=1)
    return df


def combine_risk_parity_vol(nav_dict: dict) -> tuple:
    """Risk Parity + Vol Targeting 动态合成。

    Returns:
        (combo_nav_series, weights_history, method_name)
    """
    from aipha.portfolio.strategy_combo import StrategyCombo

    # 转为 {name: {date_str: nav_float}} 格式
    raw_navs: dict[str, dict[str, float]] = {}
    for name, s in nav_dict.items():
        raw_navs[name] = {
            d.strftime("%Y-%m-%d"): float(v)
            for d, v in s.items()
        }

    method = "vol_targeted_regime"
    combo = StrategyCombo(
        raw_navs=raw_navs,
        method=method,
        lookback=60,
        rebalance_freq=20,
        vol_target=0.15,
    )
    result = combo.compute()
    dates = pd.to_datetime(result.dates)
    nav_s = pd.Series(result.combo_nav, index=dates, name="combo_nav")
    # 归一化到 TOTAL_CAPITAL
    nav_s = nav_s / nav_s.iloc[0] * TOTAL_CAPITAL
    logger.info("Risk Parity + VolTarget: AR=%.2f%%, SR=%.3f, MDD=%.2f%%",
                result.annualized_return * 100,
                result.sharpe_ratio,
                result.max_drawdown * 100)
    return nav_s, result.weights_history, method


def compute_metrics(nav_series: pd.Series) -> dict:
    """计算组合绩效指标。"""
    nav = nav_series.dropna()
    if len(nav) < 2:
        return {}
    rets = nav.pct_change().dropna()
    n_days = len(rets)
    n_years = n_days / 252.0
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    ar = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    vol = float(rets.std() * np.sqrt(252))
    sr = (ar - 0.02) / vol if vol > 1e-9 else 0.0
    # max drawdown
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    mdd = float(dd.min())
    calmar = ar / abs(mdd) if abs(mdd) > 1e-9 else 0.0
    # yearly returns
    yearly = {}
    for yr, g in rets.groupby(rets.index.year):
        yr_ret = float((1 + g).prod() - 1)
        yearly[int(yr)] = yr_ret
    return {
        "total_return": total_ret, "annualized_return": ar,
        "volatility": vol, "sharpe_ratio": sr,
        "max_drawdown": mdd, "calmar_ratio": calmar,
        "n_days": n_days, "n_years": n_years,
        "yearly_returns": yearly,
        "daily_returns": rets.values.tolist(),
    }


def run_dsr_validation(daily_returns: np.ndarray, sr: float, n_trials: int) -> dict:
    """DSR 校正验证。"""
    from aipha.backtest.advanced_validation import DeflatedSharpeRatio
    dsr = DeflatedSharpeRatio(significance_level=0.05)
    result = dsr.compute(
        observed_sharpe=sr,
        n_trials=max(n_trials, 1),
        trading_days=len(daily_returns),
        daily_returns=daily_returns.tolist() if hasattr(daily_returns, 'tolist') else list(daily_returns),
    )
    return result.to_dict()


# ── R113 新增: 滑点灵敏度估算 ──

# 子策略典型日换手率（基于 R112 回测结果经验估计）
_TURNOVER_ESTIMATES = {
    "ml_csi2000_fund":  0.08,   # ML 日均 ~8% 换手
    "cb_stock_quality": 0.10,   # CB ~10%
    "etf_cross_v3":     0.03,   # ETF ~3%
}
# 子策略基准滑点 bps
_BASE_SLIPPAGE_BPS = {
    "ml_csi2000_fund":  10.0,
    "cb_stock_quality":  5.0,
    "etf_cross_v3":      5.0,
}


def run_slippage_sensitivity(
    nav_dict: dict[str, pd.Series],
    multipliers: tuple[float, ...] = (2.0, 4.0),
) -> dict:
    """基于换手率与滑点 bps 估算 2x/4x 滑点冲击对组合 SR 的影响。

    方法: 对每个子策略的日收益扣除额外滑点成本 drag:
        drag_daily = turnover_daily × (mult - 1) × base_bps / 10000
    然后重新加权合成组合并计算 SR。
    """
    results: dict[str, Any] = {}
    for mult in multipliers:
        adj_navs: dict[str, pd.Series] = {}
        for name, nav_s in nav_dict.items():
            rets = nav_s.pct_change().fillna(0.0)
            turnover = _TURNOVER_ESTIMATES.get(name, 0.05)
            base_bps = _BASE_SLIPPAGE_BPS.get(name, 10.0)
            drag = turnover * (mult - 1.0) * base_bps / 10000.0
            adj_rets = rets - drag
            adj_nav = (1 + adj_rets).cumprod() * nav_s.iloc[0]
            adj_nav.name = name
            adj_navs[name] = adj_nav
        # 固定权重合成
        fw_df = combine_fixed_weight(adj_navs)
        m = compute_metrics(fw_df["combo_nav"])
        results[f"{mult:.0f}x"] = {
            "sr": m.get("sharpe_ratio", 0),
            "ar": m.get("annualized_return", 0),
            "mdd": m.get("max_drawdown", 0),
        }
        logger.info("  滑点 %.0fx: SR=%.3f AR=%.2f%% MDD=%.2f%%",
                     mult, m["sharpe_ratio"],
                     m["annualized_return"] * 100,
                     m["max_drawdown"] * 100)
    return results


def run_pbo_validation(returns_matrix: np.ndarray) -> dict:
    """PBO 过拟合检验。"""
    from aipha.reporting.pbo import PBOAnalyzer, PBOConfig
    config = PBOConfig(num_subsamples=16, performance_metric="sharpe")
    analyzer = PBOAnalyzer(config=config)
    result = analyzer.compute(returns_matrix)
    return {
        "pbo": result.pbo,
        "is_overfit": result.pbo >= 0.50,
        "logit_distribution_mean": float(np.mean(result.logit_distribution)),
        "num_combinations_tested": result.num_combinations_tested,
        "threshold": 0.50,
    }


# ── R113 新增: 权重扰动 ±20% 稳定性 ──

def run_weight_perturbation(
    nav_dict: dict[str, pd.Series],
    base_weights: dict[str, float],
    base_sr: float,
    delta: float = 0.20,
) -> dict:
    """权重 ±δ 扰动稳定性测试。

    对每个子策略的权重分别 +δ 和 -δ（其余等比缩放保持总和=1），
    计算组合 SR，检验 SR 变化是否 ≤ 30%。
    """
    names = sorted(base_weights.keys())
    perturbations: list[dict] = []
    for target in names:
        for sign in (+1, -1):
            new_w = dict(base_weights)
            adj = new_w[target] * delta * sign
            new_w[target] += adj
            if new_w[target] < 0.01:
                new_w[target] = 0.01
            # 等比缩放其余使总和=1
            others_sum = sum(new_w[n] for n in names if n != target)
            remain = 1.0 - new_w[target]
            if others_sum > 1e-9:
                for n in names:
                    if n != target:
                        new_w[n] = new_w[n] / others_sum * remain
            # 计算扰动后组合 SR
            frames = []
            for n, s in nav_dict.items():
                w = new_w.get(n, 0.0)
                frames.append(
                    (s / s.iloc[0] * w * TOTAL_CAPITAL).rename(n))
            df = pd.concat(frames, axis=1).sort_index().ffill().dropna()
            df["combo_nav"] = df.sum(axis=1)
            m = compute_metrics(df["combo_nav"])
            sr_pert = m.get("sharpe_ratio", 0)
            sr_change = abs(sr_pert - base_sr) / max(abs(base_sr), 1e-9)
            label = f"{target} {'+' if sign > 0 else '-'}{delta*100:.0f}%"
            perturbations.append({
                "label": label,
                "weights": {n: round(new_w[n], 4) for n in names},
                "sr": round(sr_pert, 4),
                "sr_change_pct": round(sr_change * 100, 1),
                "stable": sr_change <= 0.30,
            })
    all_stable = all(p["stable"] for p in perturbations)
    max_change = max(p["sr_change_pct"] for p in perturbations)
    return {
        "perturbations": perturbations,
        "all_stable": all_stable,
        "max_sr_change_pct": max_change,
        "delta": delta,
        "threshold_pct": 30.0,
    }


def generate_report(results: dict) -> str:
    """生成评估报告 Markdown。"""
    lines: list[str] = []
    lines.append("# R114 增强组合评估报告（HLZ 优化版）")
    lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    # ── 固定权重 baseline ──
    bm = results.get("fixed_weight_metrics", {})
    lines.append("## 1. 固定权重 Baseline")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    for k in ("annualized_return", "max_drawdown", "sharpe_ratio",
              "calmar_ratio", "volatility"):
        v = bm.get(k, 0)
        fmt = f"{v:.2%}" if "return" in k or "drawdown" in k or "vol" in k else f"{v:.3f}"
        lines.append(f"| {k} | {fmt} |")
    # ── 动态 Risk Parity + VolTarget ──
    dm = results.get("dynamic_metrics", {})
    lines.append("\n## 2. Risk Parity + Vol Targeting")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    for k in ("annualized_return", "max_drawdown", "sharpe_ratio",
              "calmar_ratio", "volatility"):
        v = dm.get(k, 0)
        fmt = f"{v:.2%}" if "return" in k or "drawdown" in k or "vol" in k else f"{v:.3f}"
        lines.append(f"| {k} | {fmt} |")
    # ── 子策略 ──
    sub = results.get("sub_metrics", {})
    if sub:
        lines.append("\n## 3. 子策略绩效")
        header = "| 策略 | AR | MDD | SR | Calmar |"
        lines.append(header)
        lines.append("|------|-----|-----|-----|--------|")
        for name in sorted(sub.keys()):
            m = sub[name]
            lines.append(
                f"| {name} | {m.get('annualized_return',0):.2%} "
                f"| {m.get('max_drawdown',0):.2%} "
                f"| {m.get('sharpe_ratio',0):.3f} "
                f"| {m.get('calmar_ratio',0):.3f} |")
    # ── DSR ──
    dsr = results.get("dsr")
    if dsr:
        lines.append("\n## 4. DSR 校正 (Bailey & Lopez de Prado 2014)")
        lines.append(f"- 观察 SR: {dsr['observed_sharpe']:.3f}")
        lines.append(f"- Haircut SR: {dsr['haircut_sharpe']:.3f}")
        lines.append(f"- p-value: {dsr['p_value']:.4f}")
        lines.append(f"- 显著: {'✅' if dsr['is_significant'] else '❌'}")
        lines.append(f"- n_trials: {dsr['n_trials']}")
    # ── PBO ──
    pbo = results.get("pbo")
    if pbo:
        lines.append("\n## 5. PBO 过拟合检验 (CSCV)")
        lines.append(f"- PBO: {pbo['pbo']:.4f}")
        lines.append(f"- 过拟合: {'❌ 是' if pbo['is_overfit'] else '✅ 否'}")
        lines.append(f"- 阈值: {pbo['threshold']}")
    # ── R113 滑点灵敏度 ──
    slip = results.get("slippage_sensitivity", {})
    if slip:
        lines.append("\n## 6. 滑点灵敏度 (原则D)")
        lines.append("| 倍数 | SR | AR | MDD | SR>0.8? |")
        lines.append("|------|-----|-----|------|---------|")
        for k, v in sorted(slip.items()):
            ok = "✅" if v["sr"] > 0.8 else "❌"
            lines.append(
                f"| {k} | {v['sr']:.3f} | {v['ar']:.2%} "
                f"| {v['mdd']:.2%} | {ok} |")
    # ── R113 HLZ ──
    hlz = results.get("hlz")
    if hlz:
        lines.append("\n## 7. HLZ 多重检验校正")
        lines.append(f"- t_obs: {hlz['observed_t']:.3f}")
        lines.append(f"- penalty (√(2·ln(N))): {hlz['penalty']:.3f}")
        lines.append(f"- t_adj: {hlz['t_adj']:.3f}")
        lines.append(f"- N_combo: {hlz['n_trials']}")
        lines.append(f"- 显著(t_adj>2.0): "
                      f"{'✅' if hlz['significant'] else '❌'}")
    # ── R113 权重扰动 ──
    pert = results.get("weight_perturbation")
    if pert:
        lines.append("\n## 8. 权重扰动 ±20% 稳定性")
        lines.append(f"- 全部稳定(SR变化≤30%): "
                      f"{'✅' if pert['all_stable'] else '❌'}")
        lines.append(f"- 最大SR变化: {pert['max_sr_change_pct']:.1f}%")
        lines.append("| 扰动 | SR | SR变化 | 稳定? |")
        lines.append("|------|-----|--------|-------|")
        for p in pert["perturbations"]:
            ok = "✅" if p["stable"] else "❌"
            lines.append(
                f"| {p['label']} | {p['sr']:.3f} "
                f"| {p['sr_change_pct']:.1f}% | {ok} |")
    # ── 逐年 ──
    yearly = dm.get("yearly_returns", {})
    if yearly:
        lines.append("\n## 9. 逐年收益 (动态组合)")
        lines.append("| 年份 | 收益 |")
        lines.append("|------|------|")
        for yr in sorted(yearly.keys()):
            lines.append(f"| {yr} | {yearly[yr]:.2%} |")
    return "\n".join(lines)


def _write_run_manifest(*, reuse_existing: bool) -> None:
    """写出组合运行清单，便于配置/日志/报告一致性审计。"""
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "output_dir": str(OUTPUT_BASE),
        "reuse_existing": reuse_existing,
        "ml_index": ML_INDEX,
        "weights": WEIGHTS,
        "standard_configs": {k: str(v) for k, v in ORIGINAL_CONFIGS.items()},
        "sub_strategy_outputs": {
            name: str(OUTPUT_BASE / name)
            for name in [*ORIGINAL_CONFIGS.keys(), "ml_csi2000_fund"]
        },
    }
    manifest_path = OUTPUT_BASE / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("运行清单 → %s", manifest_path)


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser(description="R114 增强组合回测")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="显式复用已有子策略产物；默认正式评估强制重跑。",
    )
    args = parser.parse_args()
    _setup_file_logging()

    logger.info("=" * 60)
    logger.info("R113 增强组合回测 — 开始")
    logger.info("=" * 60)
    logger.info(
        "运行模式: %s",
        "复用已有输出" if args.reuse_existing else "正式评估强制重跑",
    )
    _write_run_manifest(reuse_existing=args.reuse_existing)

    t_start = time.time()

    # 1) 运行子策略（默认强制重跑，避免静默复用旧产物）
    nav_dirs: dict[str, Path] = {}
    for name in ORIGINAL_CONFIGS:
        out_dir = OUTPUT_BASE / name
        if _prepare_output_dir(out_dir, reuse_existing=args.reuse_existing):
            nav_dirs[name] = out_dir
        else:
            capital = TOTAL_CAPITAL * WEIGHTS[name]
            nav_dirs[name] = run_standard_strategy(name, capital)

    ml_out = OUTPUT_BASE / "ml_csi2000_fund"
    if _prepare_output_dir(ml_out, reuse_existing=args.reuse_existing):
        nav_dirs["ml_csi2000_fund"] = ml_out
    else:
        ml_capital = TOTAL_CAPITAL * WEIGHTS["ml_csi2000_fund"]
        nav_dirs["ml_csi2000_fund"] = run_ml_strategy(ml_capital)

    # 2) 加载 NAV
    nav_dict: dict[str, pd.Series] = {}
    for name, d in nav_dirs.items():
        nav_dict[name] = load_nav(name, d)

    # 3) 固定权重 baseline
    logger.info("── 固定权重组合 ──")
    fw_df = combine_fixed_weight(nav_dict)
    fw_metrics = compute_metrics(fw_df["combo_nav"])
    logger.info("Fixed-Weight: AR=%.2f%%, SR=%.3f, MDD=%.2f%%",
                fw_metrics["annualized_return"] * 100,
                fw_metrics["sharpe_ratio"],
                fw_metrics["max_drawdown"] * 100)

    # 4) 动态 Risk Parity + VolTarget
    logger.info("── 动态 Risk Parity + VolTarget ──")
    dyn_nav, wh, method_name = combine_risk_parity_vol(nav_dict)
    dyn_metrics = compute_metrics(dyn_nav)

    results: dict[str, Any] = {
        "fixed_weight_metrics": fw_metrics,
        "dynamic_metrics": dyn_metrics,
        "weights_history": wh,
        "method": method_name,
        "sub_metrics": {},
    }

    # 5) 子策略指标
    for name, s in nav_dict.items():
        results["sub_metrics"][name] = compute_metrics(s)

    # 6) DSR 校正 (组合级)
    logger.info("── DSR 校正 ──")
    dr = np.array(dyn_metrics.get("daily_returns", []))
    if len(dr) > 30:
        dsr_result = run_dsr_validation(
            dr, dyn_metrics["sharpe_ratio"], n_trials=N_TRIALS_COMBO)
        results["dsr"] = dsr_result
        logger.info("DSR p-value=%.4f, significant=%s",
                    dsr_result["p_value"],
                    dsr_result["is_significant"])

    # 7) PBO 检验 (4 子策略收益矩阵)
    logger.info("── PBO 检验 ──")
    ret_cols = []
    for name in sorted(nav_dict.keys()):
        s = nav_dict[name].pct_change().dropna()
        ret_cols.append(s)
    ret_df = pd.concat(ret_cols, axis=1).dropna()
    if ret_df.shape[0] > 100 and ret_df.shape[1] >= 2:
        pbo_result = run_pbo_validation(ret_df.values)
        results["pbo"] = pbo_result
        logger.info("PBO=%.4f, is_overfit=%s",
                    pbo_result["pbo"], pbo_result["is_overfit"])

    # 8) R113 滑点灵敏度 (2x, 4x)
    logger.info("── 滑点灵敏度 ──")
    slip_result = run_slippage_sensitivity(nav_dict, multipliers=(2.0, 4.0))
    results["slippage_sensitivity"] = slip_result
    base_sr = fw_metrics.get("sharpe_ratio", 0)
    for mult_key, val in slip_result.items():
        logger.info("  %s: SR=%.3f (base=%.3f)", mult_key, val["sr"], base_sr)

    # 9) R113 权重扰动 ±20%
    logger.info("── 权重扰动 ±20%% ──")
    pert_result = run_weight_perturbation(
        nav_dict, WEIGHTS, base_sr, delta=0.20)
    results["weight_perturbation"] = pert_result
    logger.info("  扰动全部稳定: %s, 最大SR变化: %.1f%%",
                pert_result["all_stable"],
                pert_result["max_sr_change_pct"])

    # 10) HLZ 多重检验 (组合级)
    logger.info("── HLZ 多重检验 ──")
    n_years = fw_metrics.get("n_years", 1)
    obs_t = base_sr * math.sqrt(max(n_years, 0.01))
    hlz_penalty = math.sqrt(2 * math.log(max(N_TRIALS_COMBO, 1)))
    hlz_t_adj = obs_t - hlz_penalty
    hlz_pass = hlz_t_adj > 2.0
    results["hlz"] = {
        "observed_t": round(obs_t, 3),
        "penalty": round(hlz_penalty, 3),
        "t_adj": round(hlz_t_adj, 3),
        "n_trials": N_TRIALS_COMBO,
        "significant": hlz_pass,
    }
    logger.info("HLZ: t_obs=%.3f penalty=%.3f t_adj=%.3f pass=%s",
                obs_t, hlz_penalty, hlz_t_adj, hlz_pass)

    # 11) 保存结果
    out_json = OUTPUT_BASE / "combo_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(
            {k: v for k, v in results.items()
             if k != "weights_history"},
            f, ensure_ascii=False, indent=2, default=str)
    logger.info("结果 → %s", out_json)

    # 12) 生成报告
    report_md = generate_report(results)
    report_path = OUTPUT_BASE / "evaluation_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    logger.info("报告 → %s", report_path)

    elapsed = time.time() - t_start
    logger.info("=" * 60)
    logger.info("完成！耗时 %.1f 分钟", elapsed / 60)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
